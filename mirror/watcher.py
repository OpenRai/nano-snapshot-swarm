from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from mirror.dht_discovery import DEFAULT_SALT, DHTDiscoveryResult, discover_latest_snapshot
from mirror.libtorrent_session import LibtorrentSession
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


class MirrorState:
    def __init__(self, path: str):
        self.path = path
        self.last_seq: int = 0
        self.last_info_hash: str = ""
        self.current_torrent_name: str = ""
        self._load()

    def _load(self) -> None:
        p = Path(self.path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.last_seq = data.get("last_seq", 0)
                self.last_info_hash = data.get("last_info_hash", "")
                self.current_torrent_name = data.get("current_torrent_name", "")
                logger.info(
                    f"Loaded state: seq={self.last_seq}, hash={self.last_info_hash[:16]}..."
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Corrupted state file, resetting: {e}")
                self._save()
        else:
            self._save()

    def _save(self) -> None:
        data = {
            "last_seq": self.last_seq,
            "last_info_hash": self.last_info_hash,
            "current_torrent_name": self.current_torrent_name,
        }
        Path(self.path).write_text(json.dumps(data, indent=2))

    def update(self, seq: int, info_hash: str, torrent_name: str = "") -> None:
        self.last_seq = seq
        self.last_info_hash = info_hash
        self.current_torrent_name = torrent_name
        self._save()
        logger.info(f"State updated: seq={seq}, hash={info_hash[:16]}...")


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
    ):
        self.authority_pubkey_hex = authority_pubkey_hex
        self.data_dir = data_dir
        self.poll_interval = poll_interval
        self.web_seed_url = web_seed_url
        self.salt = salt
        self.download_timeout = download_timeout
        self.extract = extract
        self.seed_peers = seed_peers or []

        self.pub_key_bytes = bytes.fromhex(self.authority_pubkey_hex)
        self.nano_address = public_key_to_nano_address(self.pub_key_bytes)

        self.state = MirrorState(os.path.join(data_dir, STATE_FILENAME))
        self.session: Optional[LibtorrentSession] = None
        self._current_info_hash: Optional[str] = None
        self._running = False

    def start(self, *, once: bool = False) -> None:
        logger.info("=" * 60)
        logger.info("Nano P2P Mirror Service Starting")
        logger.info(f"Authority Nano address: {self.nano_address}")
        logger.info(f"Authority public key: {self.authority_pubkey_hex[:16]}...")
        logger.info(f"Data directory: {self.data_dir}")
        logger.info(f"Web seed URL: {self.web_seed_url}")
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

        self._running = True

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        if once:
            logger.info("Waiting 15s for DHT to bootstrap (leech mode)...")
            time.sleep(15)
            try:
                self._run_once()
            except Exception:
                logger.exception("Fatal error in leech mode")
            finally:
                self.stop()
        else:
            logger.info("Waiting 30s for DHT to bootstrap (swarm mode)...")
            time.sleep(30)
            self._resume_existing_torrent()
            try:
                self._run_loop()
            except Exception:
                logger.exception("Fatal error in main loop")
            finally:
                self.stop()

    def stop(self) -> None:
        logger.info("Shutting down mirror service...")
        self._running = False
        if self.session:
            self.session.stop()
        logger.info("Mirror service stopped.")

    def _handle_signal(self, signum: int, frame) -> None:
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self._running = False

    def _resume_existing_torrent(self) -> None:
        """Re-add the last-known torrent on restart so we keep seeding."""
        if not self.state.last_info_hash:
            return
        logger.info(
            f"Resuming torrent from previous session: "
            f"{self.state.last_info_hash[:16]}..."
        )
        try:
            handle = self.session.add_torrent(
                info_hash=self.state.last_info_hash,
                save_path=self.data_dir,
                web_seeds=[self.web_seed_url],
            )
            self._connect_seed_peers(handle)
            self._current_info_hash = self.state.last_info_hash
            logger.info("Force rechecking existing data...")
            self.session.force_recheck(self.state.last_info_hash)
        except Exception:
            logger.exception("Failed to resume existing torrent")

    def _run_once(self) -> None:
        logger.info("=== Leecher: starting single discovery cycle ===")
        try:
            result = discover_latest_snapshot(
                session=self.session,
                authority_pubkey_hex=self.authority_pubkey_hex,
                salt=self.salt,
            )
        except Exception:
            logger.exception("DHT discovery failed")
            sys.exit(1)

        if result is None:
            logger.error("No snapshot discovered from DHT in leech mode")
            sys.exit(1)

        logger.info(
            f"Leecher: discovered seq={result.sequence}, info_hash={result.info_hash_hex[:16]}..."
        )

        status = self._download_and_wait(result)
        if status == DownloadStatus.SEEDING:
            torrent_name = self.state.current_torrent_name or "unknown"
            logger.info(f"Leecher: download complete, seeding. File: {torrent_name}")

            if self.extract:
                # Stop libtorrent BEFORE extraction to free memory — 7z
                # decompression can be memory-hungry on large archives.
                logger.info("Stopping libtorrent session before extraction...")
                self.stop()

                archive_path = Path(self.data_dir) / torrent_name
                self._extract_and_cleanup(archive_path)

            sys.exit(0)
        elif status == DownloadStatus.TIMEOUT:
            logger.error(f"Leecher: download timed out after {self.download_timeout}s")
            sys.exit(1)
        else:
            logger.error("Leecher: download failed")
            sys.exit(1)

    def _extract_and_cleanup(self, archive_path: Path) -> None:
        """Extract .7z archive in-place, then delete the archive."""
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

        # Verify data.ldb exists
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
        while self._running:
            try:
                result = discover_latest_snapshot(
                    session=self.session,
                    authority_pubkey_hex=self.authority_pubkey_hex,
                    salt=self.salt,
                )

                if result is not None:
                    self._handle_discovery(result)
                else:
                    logger.info("No snapshot discovered from DHT; will retry next cycle")

            except Exception:
                logger.exception("Error during discovery cycle")

            logger.info(f"Next discovery cycle in {self.poll_interval}s")
            for _ in range(self.poll_interval):
                if not self._running:
                    break
                time.sleep(1)

    def _handle_discovery(self, result: DHTDiscoveryResult) -> None:
        if result.sequence <= self.state.last_seq:
            logger.info(
                f"Discovery seq {result.sequence} <= stored "
                f"seq {self.state.last_seq}; no update needed"
            )
            return

        logger.info(
            f"New snapshot detected! seq={result.sequence}, "
            f"info_hash={result.info_hash_hex[:16]}... "
            f"(was seq={self.state.last_seq})"
        )

        if self._current_info_hash and self._current_info_hash != result.info_hash_hex:
            logger.info("Pausing current torrent...")
            self.session.pause_torrent(self._current_info_hash)
            time.sleep(2)

        try:
            handle = self.session.add_torrent(
                info_hash=result.info_hash_hex,
                save_path=self.data_dir,
                web_seeds=[self.web_seed_url],
            )
            self._connect_seed_peers(handle)

            if self.state.last_info_hash:
                logger.info("Performing force recheck on existing data...")
                self.session.force_recheck(result.info_hash_hex)

            self._current_info_hash = result.info_hash_hex
            self.state.update(result.sequence, result.info_hash_hex)

            t_info = handle.torrent_file()
            t_name = t_info.name() if t_info else "unknown"
            self.state.current_torrent_name = t_name
            self._log_torrent_metadata(t_info)
            self.state._save()
            logger.info(f"Now tracking torrent: {t_name}")

            self._monitor_download(handle, result.info_hash_hex)

        except Exception:
            logger.exception(f"Failed to add torrent for info_hash {result.info_hash_hex[:16]}...")

    def _download_and_wait(self, result: DHTDiscoveryResult) -> DownloadStatus:
        try:
            handle = self.session.add_torrent(
                info_hash=result.info_hash_hex,
                save_path=self.data_dir,
                web_seeds=[self.web_seed_url],
            )
            self._connect_seed_peers(handle)

            self._current_info_hash = result.info_hash_hex
            self.state.update(result.sequence, result.info_hash_hex)

            t_info = handle.torrent_file()
            t_name = t_info.name() if t_info else "unknown"
            self.state.current_torrent_name = t_name
            self._log_torrent_metadata(t_info)
            self.state._save()
            logger.info(f"Leecher: added torrent '{t_name}'")

        except Exception:
            logger.exception("Leecher: failed to add torrent")
            return DownloadStatus.ERROR

        return self._monitor_download(handle, result.info_hash_hex)

    def _connect_seed_peers(self, handle) -> None:
        """Connect to explicit seed peers (e.g. seeder on the same host)."""
        import socket

        for host, port in self.seed_peers:
            try:
                # Resolve hostname to IP — libtorrent needs a raw IP address
                ip = socket.gethostbyname(host)
                handle.connect_peer((ip, port))
                logger.info(f"Connecting to seed peer {host}:{port} ({ip})")
            except Exception as e:
                logger.warning(f"Failed to connect to seed peer {host}:{port}: {e}")

    def _log_torrent_metadata(self, t_info) -> None:
        """Log snapshot metadata from the torrent info dict, if available."""
        if not t_info:
            return
        try:
            # x-snapshot is injected into the info dict by the producer,
            # so it survives BEP 9 magnet metadata exchange.
            raw = (
                t_info.info_section()
                if hasattr(t_info, "info_section")
                else t_info.metadata()
            )
            if not raw:
                return
            import bencodepy
            info_dict = bencodepy.decode(raw)
            x_snapshot = info_dict.get(b"x-snapshot")
            if not x_snapshot:
                return
            meta = json.loads(x_snapshot)
            parts = []
            if "original_filename" in meta:
                parts.append(f"file={meta['original_filename']}")
            if "source_url" in meta:
                parts.append(f"url={meta['source_url']}")
            if parts:
                logger.info(f"Snapshot metadata: {', '.join(parts)}")
        except Exception:
            logger.debug("Could not parse torrent metadata", exc_info=True)

    def _monitor_download(
        self,
        handle,
        info_hash: str,
    ) -> DownloadStatus:
        last_progress_log = 0.0
        last_state: str = ""
        no_peer_seconds = 0
        start_time = time.time()
        metadata_logged = False

        while self._running:
            if self.download_timeout > 0:
                elapsed = time.time() - start_time
                if elapsed >= self.download_timeout:
                    logger.error(f"Download timeout reached ({self.download_timeout}s)")
                    return DownloadStatus.TIMEOUT

            try:
                # Log metadata once it resolves (magnet links start without it)
                if not metadata_logged:
                    t_info = handle.torrent_file()
                    if t_info:
                        metadata_logged = True
                        t_name = t_info.name()
                        if self.state.current_torrent_name == "unknown":
                            self.state.current_torrent_name = t_name
                            self.state._save()
                            logger.info(f"Torrent metadata resolved: {t_name}")
                        self._log_torrent_metadata(t_info)

                status = handle.status()
                progress = status.progress
                state = str(status.state)
                num_peers = status.num_peers

                # Reset progress tracking on state transitions (e.g.
                # checking_files → downloading resets progress to 0)
                if state != last_state:
                    if last_state:
                        logger.info(f"State transition: {last_state} → {state}")
                    last_state = state
                    last_progress_log = 0.0

                if progress - last_progress_log >= 0.05 or progress == 1.0:
                    dl_rate = status.download_rate
                    ul_rate = status.upload_rate
                    logger.info(
                        f"Download: {progress * 100:.1f}% | State: {state} | "
                        f"DL: {dl_rate / 1000:.1f} KB/s | UL: {ul_rate / 1000:.1f} KB/s | "
                        f"Peers: {num_peers}"
                    )
                    last_progress_log = progress

                if status.is_seeding:
                    logger.info(f"Snapshot seeding complete: {info_hash[:16]}...")
                    return DownloadStatus.SEEDING

                # Stall detection: warn every 60s when no peers are connected
                if num_peers == 0:
                    no_peer_seconds += 5
                    if no_peer_seconds >= 60:
                        logger.warning(
                            f"No peers, {progress * 100:.1f}% progress for 60s "
                            f"— download may be stalled"
                        )
                        no_peer_seconds = 0
                else:
                    no_peer_seconds = 0

            except Exception as e:
                logger.error(f"Error monitoring download: {e}")
                return DownloadStatus.ERROR

            time.sleep(5)

        return DownloadStatus.ERROR


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
        help="Download timeout in seconds (0=infinite, default: 0; auto-set to 3600 in leech mode)",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Extract .7z after download and delete archive (only in --once mode)",
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

    # Parse seed peers from CLI and env
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
    )
    watcher.start(once=args.once)


if __name__ == "__main__":
    main()
