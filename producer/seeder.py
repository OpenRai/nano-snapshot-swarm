#!/usr/bin/env python3
"""Seed the latest nano-ledger-snapshot.7z torrent via libtorrent.

Intended to run as a long-lived systemd service. On restart (e.g. after
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
import time
from pathlib import Path

# Allow imports from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import libtorrent as lt  # noqa: E402

from mirror.libtorrent_session import LibtorrentSession  # noqa: E402
from shared.bep46 import build_dht_value  # noqa: E402
from shared.nano_identity import compute_bep46_target_id  # noqa: E402

logger = logging.getLogger("producer.seeder")

SNAPSHOT_NAME = "nano-ledger-snapshot.7z"
DHT_REPUBLISH_INTERVAL = 1800  # 30 minutes


def _load_dht_keys() -> tuple[bytes, bytes] | None:
    """Load DHT private key from env, return (privkey_64, pubkey_32) or None."""
    private_key_hex = os.environ.get("DHT_PRIVATE_KEY")
    if not private_key_hex:
        return None
    try:
        import nacl.signing

        sk = nacl.signing.SigningKey(bytes.fromhex(private_key_hex))
        return bytes(sk._signing_key), bytes(sk.verify_key)
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
    lt_session: lt.session,
    privkey_64: bytes,
    pubkey_32: bytes,
    info_hash_hex: str,
    salt: str,
) -> None:
    """Publish info hash to DHT via BEP 46 mutable item."""
    value_bytes = build_dht_value(info_hash_hex)
    salt_bytes = salt.encode("utf-8")
    lt_session.dht_put_mutable_item(privkey_64, pubkey_32, value_bytes, salt_bytes)
    target = compute_bep46_target_id(pubkey_32, salt)
    logger.info(
        f"DHT publish: info_hash={info_hash_hex[:16]}... "
        f"target={target.hex()[:16]}... salt='{salt}'"
    )


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

    if not snapshot_path.exists():
        logger.error(f"Snapshot file not found: {snapshot_path}")
        sys.exit(1)
    if not torrent_path.exists():
        logger.error(f"Torrent file not found: {torrent_path}")
        sys.exit(1)

    snapshot_size = snapshot_path.stat().st_size
    logger.info(f"Seeding: {snapshot_path} ({snapshot_size / (1024**3):.1f} GiB)")
    logger.info(f"Torrent: {torrent_path}")

    # Load DHT publishing keys (optional)
    dht_keys = _load_dht_keys()
    if dht_keys:
        logger.info("DHT publishing enabled (DHT_PRIVATE_KEY set)")
    else:
        logger.info("DHT publishing disabled (no DHT_PRIVATE_KEY)")

    session = LibtorrentSession(
        data_dir=data_dir,
        listen_port=6881,
    )
    session.start()

    # Wait for DHT bootstrap
    logger.info("Waiting 15s for DHT bootstrap...")
    time.sleep(15)

    handle = session.add_torrent(
        info_hash="",  # unused when torrent_file is provided
        save_path=data_dir,
        torrent_file=str(torrent_path),
    )
    logger.info("Torrent added, seeding...")

    # Graceful shutdown on SIGTERM/SIGINT
    running = True

    def on_signal(signum, _frame):
        nonlocal running
        logger.info(f"Received signal {signum}, shutting down...")
        running = False

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    # Stats file path
    stats_path = Path(data_dir) / "seeder-stats.json"
    started_at = time.time()
    last_dht_publish = 0.0

    # Periodic status logging + stats file + DHT publishing
    while running:
        now = time.time()

        # DHT publishing (every 30 min)
        if dht_keys and (now - last_dht_publish) >= DHT_REPUBLISH_INTERVAL:
            info_hash_hex = _load_info_hash(data_dir)
            if info_hash_hex and session._session:
                try:
                    privkey_64, pubkey_32 = dht_keys
                    _dht_publish(session._session, privkey_64, pubkey_32, info_hash_hex, salt)
                    last_dht_publish = now

                    # Check for put alert
                    time.sleep(5)
                    for alert in session.pop_alerts():
                        if isinstance(alert, lt.dht_put_alert):
                            num = alert.num_success if hasattr(alert, "num_success") else "?"
                            logger.info(f"DHT put result: success={num}")
                except Exception as e:
                    logger.error(f"DHT publish error: {e}")

        try:
            status = handle.status()
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

            logger.info(
                f"Seeding | Peers: {status.num_peers} | "
                f"UL: {status.upload_rate / 1024:.1f} KB/s | "
                f"Total UL: {status.total_upload / (1024**2):.1f} MiB"
            )
        except Exception as e:
            logger.error(f"Status error: {e}")
        for _ in range(60):
            if not running:
                break
            time.sleep(1)

    session.stop()
    logger.info("Seeder stopped.")


if __name__ == "__main__":
    main()
