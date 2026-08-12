from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

SNAPSHOT_NAME = "nano-ledger-snapshot.7z"
RETENTION_DIRNAME = "retained"


def retain_current_snapshot(data_dir: str | Path, info_hash: str, retention: int) -> None:
    """Retain the current archive/torrent pair before it is replaced.

    Each retained torrent needs its archive under the canonical filename, so a
    pair gets its own directory.  A temporary directory is renamed into place
    only after both files are present; pruning likewise first renames a pair
    out of the visible retention directory.
    """
    if retention < 0:
        raise ValueError("retention must be non-negative")

    root = Path(data_dir)
    retained = root / RETENTION_DIRNAME
    snapshot = root / SNAPSHOT_NAME
    torrent = root / f"{SNAPSHOT_NAME}.torrent"

    if retention and info_hash and snapshot.exists() and torrent.exists():
        retained.mkdir(parents=True, exist_ok=True)
        destination = retained / info_hash
        if not destination.exists():
            temp_dir = Path(tempfile.mkdtemp(prefix=f".{info_hash}.", dir=retained))
            try:
                os.link(snapshot.resolve(), temp_dir / SNAPSHOT_NAME)
                shutil.copy2(torrent, temp_dir / torrent.name)
                os.replace(temp_dir, destination)
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise

    if not retained.exists():
        return

    pairs = sorted(
        (path for path in retained.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for pair in pairs[retention:]:
        removed = retained / f".{pair.name}.removing"
        os.replace(pair, removed)
        shutil.rmtree(removed)


def retained_torrent_pairs(data_dir: str | Path) -> list[tuple[Path, Path]]:
    """Return valid retained archive/torrent pairs newest first."""
    retained = Path(data_dir) / RETENTION_DIRNAME
    if not retained.exists():
        return []
    pairs = []
    for directory in retained.iterdir():
        snapshot = directory / SNAPSHOT_NAME
        torrent = directory / f"{SNAPSHOT_NAME}.torrent"
        if (
            directory.is_dir()
            and not directory.name.startswith(".")
            and snapshot.exists()
            and torrent.exists()
        ):
            pairs.append((snapshot, torrent))
    return sorted(pairs, key=lambda pair: pair[1].stat().st_mtime, reverse=True)
