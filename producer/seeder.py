#!/usr/bin/env python3
"""Seed the latest nano-ledger-snapshot.7z torrent via libtorrent.

Intended to run as a long-lived systemd service. On reload (e.g. after
a new snapshot is published), it picks up the latest .torrent file and
begins seeding immediately.

If DHT_PRIVATE_KEY is set, the seeder also periodically publishes the
snapshot's info hash to the DHT via BEP 46, keeping the mutable item
alive without needing a separate short-lived publisher process.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# Allow imports from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mirror.libtorrent_session import LibtorrentSession  # noqa: E402
from producer.dht_put import publish_with_highest_sequence  # noqa: E402
from producer.publish import _wait_for_verified_snapshot  # noqa: E402
from producer.retention import retained_torrent_pairs  # noqa: E402
from shared.bep46 import build_dht_value  # noqa: E402
from shared.metrics import SnapshotMetrics  # noqa: E402
from shared.nano_identity import compute_bep46_target_id  # noqa: E402

logger = logging.getLogger("producer.seeder")

SNAPSHOT_NAME = "nano-ledger-snapshot.7z"
DHT_REPUBLISH_INTERVAL = 1800  # 30 minutes
DHT_FAILURE_RETRY_INTERVAL = 300  # 5 minutes
SEEDING_HEARTBEAT_INTERVAL = 300  # 5 minutes


def _should_log_seeding_status(now: float, last_logged_at: float) -> bool:
    """Keep normal seeding logs useful without starving the stats file."""
    return now - last_logged_at >= SEEDING_HEARTBEAT_INTERVAL


def _record_verified_publication(
    info_hash_hex: str,
    dht_sequence: int,
    state_path: str | None = None,
) -> None:
    """Persist dashboard state only after the live seeder verified DHT state."""
    path = Path(
        state_path
        or os.environ.get("PUBLISHER_STATE_FILE", str(PROJECT_ROOT / "publisher_state.json"))
    )
    try:
        state = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read publisher state {path}: {exc}") from exc

    if state.get("last_info_hash") != info_hash_hex:
        state["last_seq"] = int(state.get("last_seq", 0)) + 1
        state["last_info_hash"] = info_hash_hex
    state["last_dht_seq"] = dht_sequence
    state["last_dht_info_hash"] = info_hash_hex

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(temporary, path)
    logger.info(
        "Publisher state updated after authoritative DHT verification: "
        "local snapshot revision=%s, DHT mutable-item sequence=%s, "
        "torrent v2 info hash=%s...",
        state.get("last_seq", 0),
        dht_sequence,
        info_hash_hex[:16],
    )


def _load_dht_keys() -> tuple[bytes, bytes] | None:
    """Load DHT private key from env, return (privkey_64, pubkey_32) or None.

    libtorrent's ed25519 expects the 64-byte *expanded* private key
    (SHA-512 of the seed with clamping), NOT the NaCl format (seed || pubkey).
    """
    private_key_hex = os.environ.get("DHT_PRIVATE_KEY")
    if not private_key_hex:
        return None
    try:
        import hashlib

        import nacl.signing

        seed = bytes.fromhex(private_key_hex)
        # Derive public key via NaCl
        sk = nacl.signing.SigningKey(seed)
        pubkey = bytes(sk.verify_key)

        # Build libtorrent-format expanded private key:
        # SHA-512(seed), then clamp first 32 bytes
        expanded = bytearray(hashlib.sha512(seed).digest())
        expanded[0] &= 248
        expanded[31] &= 63
        expanded[31] |= 64

        return bytes(expanded), pubkey
    except Exception as e:
        logger.warning(f"Failed to load DHT_PRIVATE_KEY: {e}")
        return None


def _load_info_hash(data_dir: str) -> str | None:
    """Read torrent info hash from snapshot-meta.json."""
    meta_path = Path(data_dir) / "snapshot-meta.json"
    if not meta_path.exists():
        return None
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        return meta.get("torrent_info_hash")
    except Exception as e:
        logger.warning(f"Failed to read snapshot-meta.json: {e}")
        return None


def _dht_publish(
    session: LibtorrentSession,
    privkey_64: bytes,
    pubkey_32: bytes,
    info_hash_hex: str,
    salt: str,
) -> dict[str, int | None]:
    """Publish and read back the exact info hash on the live DHT session."""
    value_bytes = build_dht_value(info_hash_hex)
    target = compute_bep46_target_id(pubkey_32, salt)
    last_error = "unknown publication error"
    for attempt in range(1, 4):
        helper_path = os.environ.get("DHT_PUT_HELPER")
        if helper_path:
            helper_result = publish_with_highest_sequence(info_hash_hex, salt)
            dht_sequence = helper_result["sequence"]
            acknowledgements = helper_result["direct_acknowledgements"]
            logger.info(
                "Native DHT mutable-item put completed: sequence=%s, "
                "observed sequence=%s, direct acknowledgements=%s",
                dht_sequence,
                helper_result["observed_sequence"],
                acknowledgements,
            )
        else:
            logger.warning(
                "DHT_PUT_HELPER is not configured; using libtorrent Python convenience "
                "publisher without explicit highest-sequence control"
            )
            session.publish_dht_mutable_item(privkey_64, pubkey_32, value_bytes, salt)
            put_alert = session.wait_for_dht_put(timeout=60, salt=salt)
            dht_sequence = None
            acknowledgements = None
            if put_alert is not None:
                dht_sequence = int(put_alert.extra.get("seq", 0))
                acknowledgements = put_alert.extra.get("num_success")
                logger.info(
                    "DHT mutable-item put completed: sequence=%s, "
                    "direct acknowledgements=%s",
                    dht_sequence,
                    acknowledgements,
                )
            else:
                last_error = "no dht_put_alert"
                logger.warning("DHT mutable-item put produced no completion alert")

        verified = _wait_for_verified_snapshot(
            session,
            pubkey_32,
            info_hash_hex,
            salt,
            timeout=120,
        )
        if verified is not None:
            verified_sequence, _ = verified
            if dht_sequence is not None and verified_sequence < dht_sequence:
                last_error = (
                    f"authoritative read-back sequence {verified_sequence} is below "
                    f"put sequence {dht_sequence}"
                )
                logger.warning("Ignoring stale DHT mutable-item read-back: %s", last_error)
                verified = None
            else:
                logger.info(
                    "DHT mutable item verified: sequence=%s, "
                    "torrent v2 info hash=%s...",
                    verified_sequence,
                    info_hash_hex[:16],
                )
                return {
                    "sequence": verified_sequence,
                    "direct_acknowledgements": acknowledgements,
                }
        if verified is None and not last_error.startswith("authoritative read-back sequence"):
            last_error = "read-back did not contain the exact signed snapshot"
        if dht_sequence is not None:
            logger.warning(
                "DHT put completed but read-back did not verify; "
                "continuing without republishing to preserve sequence monotonicity"
            )
            break
        if attempt < 3:
            logger.warning(
                "DHT mutable-item read-back did not verify; retrying attempt %s/3",
                attempt + 1,
            )
            time.sleep(5)

    raise RuntimeError(
        f"DHT publication was not verified for torrent v2 info hash "
        f"{info_hash_hex[:16]}... (target ID (SHA-1)={target.hex()[:16]}..., {last_error})"
    )


def _load_current_torrent(
    session: LibtorrentSession,
    data_dir: str,
    current_info_hash: str | None,
) -> tuple[str, object, int]:
    """Load the canonical torrent while moving the previous one to retention."""
    data_path = Path(data_dir)
    torrent_path = data_path / f"{SNAPSHOT_NAME}.torrent"
    snapshot_path = data_path / SNAPSHOT_NAME
    info_hash = _load_info_hash(data_dir)
    if not info_hash:
        raise RuntimeError("snapshot-meta.json has no torrent v2 info hash")
    if not snapshot_path.exists() or not torrent_path.exists():
        raise RuntimeError("canonical snapshot or torrent is missing")

    if current_info_hash and current_info_hash != info_hash:
        previous_retained = False
        for archive_path, retained_torrent in retained_torrent_pairs(data_dir):
            if retained_torrent.parent.name == current_info_hash:
                previous_retained = True
                if not session.has_torrent(current_info_hash):
                    session.add_torrent(
                        info_hash="",
                        save_path=str(archive_path.parent),
                        torrent_file=str(retained_torrent),
                    )
                    logger.info(
                        "Previous torrent reloaded for continued seeding: "
                        "v2 info hash=%s...",
                        current_info_hash[:16],
                    )
                else:
                    logger.info(
                        "Previous torrent remains active for continued seeding: "
                        "v2 info hash=%s...",
                        current_info_hash[:16],
                    )
                break
        if not previous_retained:
            session.remove_torrent(current_info_hash)

    if not session.has_torrent(info_hash):
        session.add_torrent(
            info_hash="",
            save_path=data_dir,
            torrent_file=str(torrent_path),
        )
        logger.info("Canonical torrent loaded for seeding: v2 info hash=%s...", info_hash[:16])
    handle = session.get_handle(info_hash)
    if handle is None:
        raise RuntimeError(f"canonical torrent handle unavailable: {info_hash[:16]}...")
    return info_hash, handle, snapshot_path.stat().st_size


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    data_dir = os.environ.get("OUTPUT_DIR", os.path.expanduser("~/nano-snapshots"))
    salt = os.environ.get("DHT_SALT", "daily")
    snapshot_path = Path(data_dir) / SNAPSHOT_NAME
    torrent_path = Path(data_dir) / f"{SNAPSHOT_NAME}.torrent"

    # Load and identify the DHT signer before checking the current artifact, so
    # an operator can recover the mirror-facing public key from any startup log.
    dht_keys = _load_dht_keys()
    if dht_keys:
        logger.info("DHT publishing enabled (DHT_PRIVATE_KEY set)")
        logger.info(
            "Verification using PRODUCER_SIGNING_PUBKEY: %s",
            dht_keys[1].hex(),
        )
    else:
        logger.info("DHT publishing disabled (no DHT_PRIVATE_KEY)")

    if not snapshot_path.exists():
        logger.error(f"Snapshot file not found: {snapshot_path}")
        sys.exit(1)
    if not torrent_path.exists():
        logger.error(f"Torrent file not found: {torrent_path}")
        sys.exit(1)

    snapshot_size = snapshot_path.stat().st_size
    logger.info(f"Seeding: {snapshot_path} ({snapshot_size / (1024**3):.1f} GiB)")
    logger.info(f"Torrent: {torrent_path}")

    # Graceful shutdown on SIGTERM/SIGINT; SIGHUP reloads the canonical torrent
    # without destroying the DHT session or the retained swarms. Install this
    # before session and metrics startup so systemd reload is safe throughout
    # the entire process startup.
    running = True
    reload_requested = threading.Event()

    def on_signal(signum, _frame):
        nonlocal running
        if signum == signal.SIGHUP:
            logger.info("Received SIGHUP; scheduling canonical torrent reload")
            reload_requested.set()
        else:
            logger.info(f"Received signal {signum}, shutting down...")
            running = False

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGHUP, on_signal)

    session = LibtorrentSession(
        data_dir=data_dir,
        listen_port=6881,
        load_dht_state=False,
    )
    session.start()
    metrics = SnapshotMetrics("producer")
    metrics.start_http_server(int(os.environ.get("METRICS_PORT", "9108")))

    # Wait for DHT bootstrap alert before the first publication.
    bootstrapped = session.wait_for_dht_bootstrap(timeout=120)
    if not bootstrapped:
        logger.warning("DHT bootstrap did not complete — publication will retry")

    current_info_hash, handle, snapshot_size = _load_current_torrent(
        session, data_dir, None
    )
    logger.info("Canonical torrent added, seeding...")
    for archive_path, retained_torrent in retained_torrent_pairs(data_dir):
        retained_info_hash = retained_torrent.parent.name
        if not session.has_torrent(retained_info_hash):
            session.add_torrent(
                info_hash="",
                save_path=str(archive_path.parent),
                torrent_file=str(retained_torrent),
            )
            logger.info(
                "Retained torrent added for seeding: v2 info hash=%s...",
                retained_info_hash[:16],
            )

    # Stats file path
    stats_path = Path(data_dir) / "seeder-stats.json"
    started_at = time.time()
    last_dht_publish = 0.0
    last_dht_sequence: int | None = None
    last_dht_acknowledgements: int | None = None
    last_dht_error: str | None = None
    last_dht_attempt = 0
    last_dht_attempt_at = 0.0
    last_seeding_log_at = 0.0
    dht_verified = False

    def publish_current() -> None:
        nonlocal last_dht_publish, last_dht_sequence
        nonlocal last_dht_acknowledgements, last_dht_error
        nonlocal last_dht_attempt, last_dht_attempt_at, dht_verified
        if not dht_keys or not session._session:
            dht_verified = False
            last_dht_error = "DHT_PRIVATE_KEY is not configured"
            return
        dht_nodes = session.dht_node_count()
        logger.info("DHT has %s nodes, publishing current torrent...", dht_nodes)
        privkey_64, pubkey_32 = dht_keys
        last_dht_attempt += 1
        last_dht_attempt_at = time.time()
        try:
            result = _dht_publish(
                session,
                privkey_64,
                pubkey_32,
                current_info_hash,
                salt,
            )
            last_dht_publish = time.time()
            last_dht_sequence = int(result["sequence"])
            last_dht_acknowledgements = result["direct_acknowledgements"]
            _record_verified_publication(current_info_hash, last_dht_sequence)
            last_dht_error = None
            dht_verified = True
        except Exception as exc:
            last_dht_error = str(exc)
            dht_verified = False
            raise

    try:
        publish_current()
    except Exception as exc:
        dht_verified = False
        logger.error("Initial DHT publication is not verified: %s", exc)

    # A reload received during startup is satisfied by loading and publishing
    # the current canonical torrent above; avoid a redundant immediate put.
    if reload_requested.is_set():
        reload_requested.clear()
        logger.info("Startup completed a pending canonical torrent reload")

    # Periodic status logging + stats file + DHT publishing
    while running:
        now = time.time()

        if reload_requested.is_set():
            reload_requested.clear()
            try:
                new_info_hash, new_handle, new_snapshot_size = _load_current_torrent(
                    session, data_dir, current_info_hash
                )
                current_info_hash = new_info_hash
                handle = new_handle
                snapshot_size = new_snapshot_size
                dht_verified = False
                logger.info(
                    "Canonical torrent reload complete: v2 info hash=%s...",
                    current_info_hash[:16],
                )
                try:
                    publish_current()
                except Exception as exc:
                    logger.error("Reloaded torrent DHT publication is not verified: %s", exc)
            except Exception as exc:
                logger.error("Canonical torrent reload failed: %s", exc)

        # DHT publishing (every 30 min)
        if dht_keys and (
            now - last_dht_attempt_at
        ) >= (DHT_REPUBLISH_INTERVAL if dht_verified else DHT_FAILURE_RETRY_INTERVAL):
            try:
                publish_current()
            except Exception as exc:
                dht_verified = False
                logger.error("DHT publication is not verified: %s", exc)
            # Save DHT state periodically for faster re-bootstrap
            session.save_dht_state()

        try:
            status = handle.status()
            if last_dht_sequence is not None:
                metrics.observe_generation(
                    info_hash=current_info_hash,
                    sequence=last_dht_sequence,
                    size_bytes=snapshot_size,
                )
            else:
                metrics.snapshot_size.labels(service="producer").set(snapshot_size)
            metrics.observe_transfer(
                total_upload=status.total_upload,
                total_download=status.total_download,
                peers=status.num_peers,
                seeds=getattr(status, "num_seeds", 0),
                connections=getattr(status, "num_connections", status.num_peers),
            )
            metrics.dht_nodes.set(session.dht_node_count())
            metrics.observe_state(
                "seeding" if status.is_seeding else str(status.state),
                ready=bool(status.is_seeding and dht_verified),
            )
            stats = {
                "state": "seeding" if status.is_seeding else str(status.state),
                "progress_pct": round(status.progress * 100, 1),
                "peers": status.num_peers,
                "upload_rate_kbps": round(status.upload_rate / 1024, 1),
                "download_rate_kbps": round(status.download_rate / 1024, 1),
                "total_upload_mib": round(status.total_upload / (1024**2), 1),
                "total_download_mib": round(status.total_download / (1024**2), 1),
                "snapshot_size_gib": round(snapshot_size / (1024**3), 2),
                "torrent_name": status.name,
                "uptime_seconds": int(now - started_at),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "dht_publishing": dht_keys is not None,
                "torrent_info_hash": current_info_hash,
                "dht_verified": dht_verified,
                "dht_sequence": last_dht_sequence,
                "dht_direct_acknowledgements": last_dht_acknowledgements,
                "dht_publish_attempt": last_dht_attempt,
                "dht_last_error": last_dht_error,
                "seeder_ready": bool(status.is_seeding and dht_verified),
                "retained_torrent_count": len(retained_torrent_pairs(data_dir)),
                "last_dht_publish": time.strftime(
                    "%Y-%m-%dT%H:%M:%S%z", time.localtime(last_dht_publish)
                )
                if last_dht_publish > 0
                else None,
            }
            # Atomic write
            tmp = stats_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(stats, indent=2) + "\n")
            tmp.rename(stats_path)

            if _should_log_seeding_status(now, last_seeding_log_at):
                logger.info(
                    f"Seeding | Peers: {status.num_peers} | "
                    f"UL: {status.upload_rate / 1024:.1f} KB/s | "
                    f"Total UL: {status.total_upload / (1024**2):.1f} MiB"
                )
                last_seeding_log_at = now
        except Exception as e:
            logger.error(f"Status error: {e}")
        for _ in range(5):
            if not running:
                break
            time.sleep(1)

    session.stop()
    logger.info("Seeder stopped.")


if __name__ == "__main__":
    main()
