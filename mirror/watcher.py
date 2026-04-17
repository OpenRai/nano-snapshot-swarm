from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from mirror.dht_discovery import DHTDiscoveryResult, discover_latest_snapshot
from mirror.libtorrent_session import LibtorrentSession
from shared.nano_identity import public_key_to_nano_address

logger = logging.getLogger("mirror.watcher")

DEFAULT_DATA_DIR = os.environ.get("DATA_DIR", "/data")
DEFAULT_POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "600"))
STATE_FILENAME = "mirror_state.json"

WEB_SEED_URL = "https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest"


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
    ):
        self.authority_pubkey_hex = authority_pubkey_hex
        self.data_dir = data_dir
        self.poll_interval = poll_interval
        self.web_seed_url = web_seed_url

        self.pub_key_bytes = bytes.fromhex(self.authority_pubkey_hex)
        self.nano_address = public_key_to_nano_address(self.pub_key_bytes)

        self.state = MirrorState(os.path.join(data_dir, STATE_FILENAME))
        self.session: Optional[LibtorrentSession] = None
        self._current_info_hash: Optional[str] = None
        self._running = False

    def start(self) -> None:
        logger.info("=" * 60)
        logger.info("Nano P2P Mirror Service Starting")
        logger.info(f"Authority Nano address: {self.nano_address}")
        logger.info(f"Authority public key: {self.authority_pubkey_hex[:16]}...")
        logger.info(f"Data directory: {self.data_dir}")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Web seed URL: {self.web_seed_url}")
        logger.info("=" * 60)

        self.session = LibtorrentSession(
            data_dir=self.data_dir,
            listen_port=6881,
        )
        self.session.start()

        self._running = True

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info("Waiting 30s for DHT to bootstrap...")
        time.sleep(30)

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

    def _run_loop(self) -> None:
        while self._running:
            try:
                result = discover_latest_snapshot(
                    session=self.session,
                    authority_pubkey_hex=self.authority_pubkey_hex,
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

            if self.state.last_info_hash:
                logger.info("Performing force recheck on existing data...")
                self.session.force_recheck(result.info_hash_hex)

            self._current_info_hash = result.info_hash_hex
            self.state.update(result.sequence, result.info_hash_hex)

            t_info = handle.get_torrent_info()
            t_name = t_info.name() if t_info else "unknown"
            logger.info(f"Now tracking torrent: {t_name}")

            self._monitor_download(handle, result.info_hash_hex)

        except Exception:
            logger.exception(f"Failed to add torrent for info_hash {result.info_hash_hex[:16]}...")

    def _monitor_download(self, handle, info_hash: str) -> None:
        last_progress_log = 0.0
        while self._running:
            try:
                status = handle.status()
                progress = status.progress
                state = str(status.state)

                if progress - last_progress_log >= 0.05 or progress == 1.0:
                    dl_rate = status.download_rate
                    ul_rate = status.upload_rate
                    num_peers = status.num_peers
                    logger.info(
                        f"Download: {progress * 100:.1f}% | State: {state} | "
                        f"DL: {dl_rate / 1000:.1f} KB/s | UL: {ul_rate / 1000:.1f} KB/s | "
                        f"Peers: {num_peers}"
                    )
                    last_progress_log = progress

                if status.is_seeding:
                    logger.info(f"Snapshot seeding complete: {info_hash[:16]}...")
                    break

            except Exception as e:
                logger.error(f"Error monitoring download: {e}")
                break

            time.sleep(5)


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

    watcher = MirrorWatcher(
        authority_pubkey_hex=pubkey,
        data_dir=args.data_dir,
        poll_interval=args.poll_interval,
        web_seed_url=args.web_seed_url,
    )
    watcher.start()


if __name__ == "__main__":
    main()
