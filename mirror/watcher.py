from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from mirror.config import WEB_SEED_MODE_FALLBACK, WEB_SEED_MODES, resolve_web_seeds
from mirror.dht_discovery import DEFAULT_SALT, DHTDiscoveryResult, discover_latest_snapshot
from mirror.libtorrent_session import (
    LibtorrentSession,
    TorrentMetadataSnapshot,
    TorrentStatusSnapshot,
)
from mirror.reconcile import DesiredSnapshot, ReconcileDecision, reconcile_snapshot
from mirror.state import MirrorState
from shared.nano_identity import public_key_to_nano_address

logger = logging.getLogger("mirror.watcher")

DEFAULT_DATA_DIR = os.environ.get("DATA_DIR", "/data")
DEFAULT_POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "600"))
DEFAULT_DOWNLOAD_TIMEOUT = 0
STATE_FILENAME = "mirror_state.json"

WEB_SEED_URL = "https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest"


class DownloadStatus(Enum):
    SEEDING = "seeding"
    TIMEOUT = "timeout"
    ERROR = "error"


class MirrorWatcher:
    def __init__(
        self,
        authority_pubkey_hex: str,
        data_dir: str = DEFAULT_DATA_DIR,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        web_seed_url: str = WEB_SEED_URL,
        salt: str = DEFAULT_SALT,
        download_timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
        extract: bool = False,
        seed_peers: Optional[list[tuple[str, int]]] = None,
        web_seed_mode: str = WEB_SEED_MODE_FALLBACK,
    ):
        self.authority_pubkey_hex = authority_pubkey_hex
        self.data_dir = data_dir
        self.poll_interval = poll_interval
        self.web_seed_url = web_seed_url
        self.salt = salt
        self.download_timeout = download_timeout
        self.extract = extract
        self.seed_peers = seed_peers or []
        self.web_seed_mode = web_seed_mode

        self.pub_key_bytes = bytes.fromhex(self.authority_pubkey_hex)
        self.nano_address = public_key_to_nano_address(self.pub_key_bytes)

        self.state = MirrorState(os.path.join(data_dir, STATE_FILENAME))
        self.session: Optional[LibtorrentSession] = None
        self._desired_snapshot = self._load_desired_snapshot()
        self._active_info_hash: Optional[str] = None
        self._active_started_at: Optional[float] = None
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._discovery_thread: Optional[threading.Thread] = None
        self._stop_reason: Optional[DownloadStatus] = None
        self._state_lock = threading.Lock()
        self._reconcile_event = threading.Event()

    def start(self, *, once: bool = False) -> None:
        logger.info("=" * 60)
        logger.info("Nano P2P Mirror Service Starting")
        logger.info(f"Authority Nano address: {self.nano_address}")
        logger.info(f"Authority public key: {self.authority_pubkey_hex[:16]}...")
        logger.info(f"Data directory: {self.data_dir}")
        logger.info(f"Web seed URL: {self.web_seed_url}")
        logger.info(f"Web seed mode: {self.web_seed_mode}")
        logger.info(f"DHT salt: '{self.salt}'")
        if once:
            logger.info("Mode: LEECH (download-once, exit when done)")
            if self.download_timeout > 0:
                logger.info(f"Download timeout: {self.download_timeout}s")
            else:
                logger.info("Download timeout: infinite")
        else:
            logger.info(f"Mode: SWARM (continuous polling every {self.poll_interval}s)")
        if self.seed_peers:
            logger.info(f"Seed peers: {', '.join(f'{h}:{p}' for h, p in self.seed_peers)}")
        logger.info("=" * 60)

        self.session = LibtorrentSession(
            data_dir=self.data_dir,
            listen_port=6881,
        )
        self.session.start()
        self.state.set_phase("bootstrapping_dht")

        self._running = True
        self._stop_reason = None

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        if once:
            logger.info("Waiting 30s for DHT to bootstrap (leech mode)...")
            time.sleep(30)
            self.state.set_phase("discovering")
            try:
                self._run_once()
            except Exception:
                self.state.set_phase("error", "Fatal error in leech mode")
                logger.exception("Fatal error in leech mode")
            finally:
                self.stop()
        else:
            logger.info("Waiting 30s for DHT to bootstrap (swarm mode)...")
            time.sleep(30)
            try:
                self._run_loop()
            except Exception:
                self.state.set_phase("error", "Fatal error in main loop")
                logger.exception("Fatal error in main loop")
            finally:
                self.stop()

    def stop(self) -> None:
        logger.info("Shutting down mirror service...")
        self._running = False
        self._reconcile_event.set()
        if self._discovery_thread and self._discovery_thread.is_alive():
            self._discovery_thread.join(timeout=5)
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        self.state.set_phase("stopped")
        if self.session:
            self.session.stop()
        logger.info("Mirror service stopped.")

    def _handle_signal(self, signum: int, frame) -> None:
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self._running = False
        self._reconcile_event.set()

    def _load_desired_snapshot(self) -> Optional[DesiredSnapshot]:
        if not self.state.last_info_hash:
            return None
        return DesiredSnapshot(seq=self.state.last_seq, info_hash=self.state.last_info_hash)

    def _run_once(self) -> None:
        logger.info("=== Leecher: starting single discovery cycle ===")
        try:
            self.state.set_phase("discovering")
            result = discover_latest_snapshot(
                session=self.session,
                authority_pubkey_hex=self.authority_pubkey_hex,
                salt=self.salt,
            )
        except Exception:
            logger.exception("DHT discovery failed")
            sys.exit(1)

        if result is None:
            self.state.set_phase("error", "No snapshot discovered from DHT in leech mode")
            logger.error("No snapshot discovered from DHT in leech mode")
            sys.exit(1)

        logger.info(
            f"Leecher: discovered seq={result.sequence}, info_hash={result.info_hash_hex[:16]}..."
        )
        self._set_desired_snapshot(result)
        self._ensure_monitor_thread()
        self._reconcile_to_desired(resumed_info_hash=self.state.last_info_hash)

        status = self._wait_for_terminal_download_status()
        if status == DownloadStatus.SEEDING:
            torrent_name = self.state.current_torrent_name or "unknown"
            logger.info(f"Leecher: download complete, seeding. File: {torrent_name}")

            if self.extract:
                logger.info("Stopping libtorrent session before extraction...")
                self.stop()

                archive_path = Path(self.data_dir) / torrent_name
                self._extract_and_cleanup(archive_path)

            sys.exit(0)
        if status == DownloadStatus.TIMEOUT:
            logger.error(f"Leecher: download timed out after {self.download_timeout}s")
            sys.exit(1)

        logger.error("Leecher: download failed")
        sys.exit(1)

    def _extract_and_cleanup(self, archive_path: Path) -> None:
        if not archive_path.exists():
            logger.error(f"Archive not found for extraction: {archive_path}")
            sys.exit(1)

        archive_size = archive_path.stat().st_size
        logger.info(
            f"Extracting {archive_path.name} "
            f"({archive_size / (1024**3):.1f} GiB)..."
        )

        try:
            subprocess.run(
                ["7z", "x", "-mmt=3", "-y", f"-o{archive_path.parent}", str(archive_path)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.error("7z command not found — install p7zip-full to use --extract")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            logger.error(f"7z extraction failed with exit code {e.returncode}")
            sys.exit(1)

        extracted = archive_path.parent / "data.ldb"
        if not extracted.exists():
            logger.error("Extraction produced no data.ldb — archive may be corrupt")
            sys.exit(1)

        extracted_size = extracted.stat().st_size
        logger.info(
            f"Extracted data.ldb ({extracted_size / (1024**3):.1f} GiB) — "
            f"removing archive {archive_path.name}"
        )
        archive_path.unlink()
        logger.info("Extraction complete, archive deleted.")

    def _run_loop(self) -> None:
        self._ensure_monitor_thread()
        self._reconcile_to_desired(resumed_info_hash=self.state.last_info_hash)

        self._discovery_thread = threading.Thread(target=self._discovery_loop, daemon=True)
        self._discovery_thread.start()

        if self._active_info_hash is None:
            self.state.set_phase("idle")

        while self._running:
            self._reconcile_event.wait(timeout=1.0)
            self._reconcile_event.clear()

    def _ensure_monitor_thread(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor_active_torrent_loop,
            daemon=True,
        )
        self._monitor_thread.start()

    def _discovery_loop(self) -> None:
        while self._running:
            try:
                self.state.set_phase("discovering")
                result = discover_latest_snapshot(
                    session=self.session,
                    authority_pubkey_hex=self.authority_pubkey_hex,
                    salt=self.salt,
                )

                if result is not None:
                    self._set_desired_snapshot(result)
                else:
                    logger.info("No snapshot discovered from DHT; will retry next cycle")
            except Exception:
                self.state.set_phase("error", "Error during discovery cycle")
                logger.exception("Error during discovery cycle")

            if not self._running:
                break

            logger.info(f"Next discovery cycle in {self.poll_interval}s")
            for _ in range(self.poll_interval):
                if not self._running:
                    break
                time.sleep(1)

    def _set_desired_snapshot(self, result: DHTDiscoveryResult) -> None:
        with self._state_lock:
            if result.sequence < self.state.last_seq:
                return

            if (
                result.info_hash_hex == self.state.last_info_hash
                and result.sequence <= self.state.last_seq
            ):
                logger.info(
                    f"Discovery seq {result.sequence} <= stored seq "
                    f"{self.state.last_seq}; no update needed"
                )
                return

            if result.sequence > self.state.last_seq:
                logger.info(
                    f"New snapshot detected! seq={result.sequence}, "
                    f"info_hash={result.info_hash_hex[:16]}... (was seq={self.state.last_seq})"
                )

            self._desired_snapshot = DesiredSnapshot(
                seq=result.sequence,
                info_hash=result.info_hash_hex,
            )
            self.state.update(result.sequence, result.info_hash_hex)

        self._reconcile_to_desired()

    def _reconcile_to_desired(self, *, resumed_info_hash: Optional[str] = None) -> None:
        with self._state_lock:
            decision = reconcile_snapshot(
                desired=self._desired_snapshot,
                active_info_hash=self._active_info_hash,
                resumed_info_hash=resumed_info_hash,
            )

        self._apply_reconcile_decision(decision)
        self._reconcile_event.set()

    def _apply_reconcile_decision(self, decision: ReconcileDecision) -> None:
        target = decision.target
        if target is None:
            return

        try:
            if decision.action == "noop":
                return

            if decision.action == "replace" and self._active_info_hash:
                logger.info("Pausing current torrent...")
                self.session.pause_torrent(self._active_info_hash)
                time.sleep(2)
                self.session.remove_torrent(self._active_info_hash)
                self._active_info_hash = None

            if decision.action in {"activate", "replace"}:
                if decision.should_recheck:
                    self.state.set_phase("resuming")
                    logger.info(
                        f"Resuming torrent from previous session: {target.info_hash[:16]}..."
                    )
                else:
                    self.state.set_phase("metadata")

                self.session.ensure_torrent(
                    info_hash=target.info_hash,
                    save_path=self.data_dir,
                    web_seeds=resolve_web_seeds(self.web_seed_url, self.web_seed_mode),
                )
                self._connect_seed_peers(target.info_hash)
                self._active_info_hash = target.info_hash
                self._active_started_at = time.time()

                if decision.should_recheck:
                    logger.info("Force rechecking existing data...")
                    self.session.force_recheck(target.info_hash)

                metadata = self.session.torrent_metadata(target.info_hash)
                if metadata is not None:
                    self._apply_metadata(metadata)
                    logger.info(f"Now tracking torrent: {metadata.name}")
        except Exception:
            self.state.set_phase(
                "error",
                f"Failed to activate torrent for {target.info_hash[:16]}...",
            )
            logger.exception(
                f"Failed to activate torrent for info_hash {target.info_hash[:16]}..."
            )
            self._stop_reason = DownloadStatus.ERROR
            self._running = False

    def _connect_seed_peers(self, info_hash: str) -> None:
        for host, port in self.seed_peers:
            try:
                self.session.connect_peer(info_hash, host, port)
            except Exception as e:
                logger.warning(f"Failed to connect to seed peer {host}:{port}: {e}")

    def _apply_metadata(self, metadata: TorrentMetadataSnapshot) -> None:
        if self.state.current_torrent_name != metadata.name:
            self.state.current_torrent_name = metadata.name
            self.state._save()
            logger.info(f"Torrent metadata resolved: {metadata.name}")

        meta = metadata.snapshot_meta
        if not meta:
            return

        parts = []
        if "original_filename" in meta:
            parts.append(f"file={meta['original_filename']}")
        if "source_url" in meta:
            parts.append(f"url={meta['source_url']}")
        if parts:
            logger.info(f"Snapshot metadata: {', '.join(parts)}")

    def _monitor_active_torrent_loop(self) -> None:
        tracked_hash: Optional[str] = None
        last_progress_log = 0.0
        last_state = ""
        no_peer_seconds = 0
        metadata_logged = False

        while self._running:
            info_hash = self._active_info_hash
            if not info_hash:
                tracked_hash = None
                last_progress_log = 0.0
                last_state = ""
                no_peer_seconds = 0
                metadata_logged = False
                time.sleep(1)
                continue

            if info_hash != tracked_hash:
                tracked_hash = info_hash
                last_progress_log = 0.0
                last_state = ""
                no_peer_seconds = 0
                metadata_logged = False

            if self.download_timeout > 0 and self._active_started_at is not None:
                elapsed = time.time() - self._active_started_at
                if elapsed >= self.download_timeout:
                    logger.error(f"Download timeout reached ({self.download_timeout}s)")
                    self._stop_reason = DownloadStatus.TIMEOUT
                    self._running = False
                    break

            try:
                if not metadata_logged:
                    metadata = self.session.torrent_metadata(info_hash)
                    if metadata:
                        metadata_logged = True
                        self._apply_metadata(metadata)

                status = self.session.torrent_status(info_hash)
                if status is None:
                    time.sleep(1)
                    continue

                self._update_transfer_state(
                    status,
                    info_hash,
                    last_state,
                    last_progress_log,
                    no_peer_seconds,
                )

                if status.state != last_state:
                    last_state = status.state
                    last_progress_log = 0.0

                if status.progress - last_progress_log >= 0.02 or status.progress == 1.0:
                    last_progress_log = status.progress

                if status.num_peers == 0:
                    no_peer_seconds += 5
                    if no_peer_seconds >= 60:
                        logger.warning(
                            f"No peers, {status.progress * 100:.1f}% progress for 60s "
                            f"— download may be stalled"
                        )
                        no_peer_seconds = 0
                else:
                    no_peer_seconds = 0

                if status.is_seeding and self.download_timeout > 0:
                    self._stop_reason = DownloadStatus.SEEDING
                    self._running = False
                    break
            except Exception as e:
                self.state.set_phase("error", str(e))
                logger.error(f"Error monitoring download: {e}")
                self._stop_reason = DownloadStatus.ERROR
                self._running = False
                break

            time.sleep(5)

    def _update_transfer_state(
        self,
        status: TorrentStatusSnapshot,
        info_hash: str,
        last_state: str,
        last_progress_log: float,
        no_peer_seconds: int,
    ) -> None:
        if status.state != last_state:
            if last_state:
                logger.info(f"State transition: {last_state} → {status.state}")
            phase = "checking" if status.state == "checking_files" else status.state
            self.state.set_phase(phase)

        if status.progress - last_progress_log >= 0.02 or status.progress == 1.0:
            logger.info(
                f"Download: {status.progress * 100:.1f}% | State: {status.state} | "
                f"DL: {status.download_rate / 1000:.1f} KB/s | "
                f"UL: {status.upload_rate / 1000:.1f} KB/s | "
                f"Peers: {status.num_peers}"
            )

        if status.is_seeding:
            self.state.set_phase("seeding")
            logger.info(f"Snapshot seeding complete: {info_hash[:16]}...")

        if status.num_peers == 0 and no_peer_seconds >= 60:
            logger.warning(
                f"No peers, {status.progress * 100:.1f}% progress for 60s "
                f"— download may be stalled"
            )

    def _wait_for_terminal_download_status(self) -> DownloadStatus:
        while self._running:
            if self._stop_reason is not None:
                return self._stop_reason
            time.sleep(1)
        return self._stop_reason or DownloadStatus.ERROR


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Nano P2P Mirror Service")
    parser.add_argument(
        "--authority-pubkey",
        default=os.environ.get("AUTHORITY_PUBKEY", ""),
        help="Authority Ed25519 public key (hex). Required.",
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Directory for ledger data and state (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"DHT poll interval in seconds (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "--web-seed-url",
        default=WEB_SEED_URL,
        help="Web seed URL for fallback downloads",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--salt",
        default=os.environ.get("DHT_SALT", DEFAULT_SALT),
        help=f"DHT salt (env DHT_SALT, default: {DEFAULT_SALT})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Leech mode: download once then exit (do not run as daemon)",
    )
    parser.add_argument(
        "--download-timeout",
        type=int,
        default=DEFAULT_DOWNLOAD_TIMEOUT,
        help=(
            "Download timeout in seconds (0=infinite, default: 0; "
            "auto-set to 3600 in --once mode)"
        ),
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Extract .7z after download and delete archive (only in --once mode)",
    )
    parser.add_argument(
        "--web-seed-mode",
        default=os.environ.get("WEB_SEED_MODE", WEB_SEED_MODE_FALLBACK),
        choices=sorted(WEB_SEED_MODES),
        help="Web seed policy: fallback or off",
    )
    parser.add_argument(
        "--seed-peer",
        action="append",
        default=[],
        dest="seed_peers",
        metavar="HOST:PORT",
        help="Explicit peer to connect to (can be repeated). Env: SEED_PEERS (comma-separated)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    pubkey = args.authority_pubkey.replace(":", "").strip()
    if not pubkey:
        print("ERROR: AUTHORITY_PUBKEY is required (env or --authority-pubkey)", file=sys.stderr)
        sys.exit(1)

    try:
        pubkey_bytes = bytes.fromhex(pubkey)
        if len(pubkey_bytes) != 32:
            raise ValueError
    except ValueError:
        print(
            "ERROR: AUTHORITY_PUBKEY must be a 32-byte hex string (64 hex characters)",
            file=sys.stderr,
        )
        sys.exit(1)

    download_timeout = args.download_timeout
    if args.once and download_timeout == 0:
        download_timeout = 3600

    extract = args.extract
    if extract and not args.once:
        logger.warning("--extract is only supported in --once mode; ignoring")
        extract = False

    seed_peers: list[tuple[str, int]] = []
    env_peers = os.environ.get("SEED_PEERS", "")
    all_peer_strs = args.seed_peers + [p.strip() for p in env_peers.split(",") if p.strip()]
    for peer_str in all_peer_strs:
        try:
            host, port_str = peer_str.rsplit(":", 1)
            seed_peers.append((host, int(port_str)))
        except (ValueError, IndexError):
            print(
                f"WARNING: Invalid seed peer '{peer_str}', expected HOST:PORT",
                file=sys.stderr,
            )

    watcher = MirrorWatcher(
        authority_pubkey_hex=pubkey,
        data_dir=args.data_dir,
        poll_interval=args.poll_interval,
        web_seed_url=args.web_seed_url,
        salt=args.salt,
        download_timeout=download_timeout,
        extract=extract,
        seed_peers=seed_peers,
        web_seed_mode=args.web_seed_mode,
    )
    watcher.start(once=args.once)


if __name__ == "__main__":
    main()
