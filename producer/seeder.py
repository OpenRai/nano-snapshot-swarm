#!/usr/bin/env python3
"""Seed the latest nano-ledger-snapshot.7z torrent via libtorrent.

Intended to run as a long-lived systemd service. On restart (e.g. after
a new snapshot is published), it picks up the latest .torrent file and
begins seeding immediately.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

# Allow imports from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mirror.libtorrent_session import LibtorrentSession  # noqa: E402

logger = logging.getLogger("producer.seeder")

SNAPSHOT_NAME = "nano-ledger-snapshot.7z"


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    data_dir = os.environ.get("OUTPUT_DIR", os.path.expanduser("~/nano-snapshots"))
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

    # Periodic status logging
    while running:
        try:
            status = handle.status()
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
