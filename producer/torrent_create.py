from __future__ import annotations

import os
import sys
from dataclasses import dataclass

PUBLIC_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
)


@dataclass(frozen=True)
class TorrentHashes:
    v1: str
    v2: str


def create_torrent(
    filepath: str,
    piece_size: int = 32 * 1024 * 1024,
    output_path: str | None = None,
    comment: str | None = None,
    snapshot_meta: str | None = None,
) -> tuple[str, TorrentHashes]:
    """Create a hybrid v1+v2 torrent for a single file.

    Args:
        snapshot_meta: JSON string embedded as 'x-snapshot' in the info dict.
            Survives magnet link metadata exchange (BEP 9). Only include
            stable fields such as original_filename — not timestamps,
            since changes affect the info hash.
        comment: Stored in the outer torrent dict. NOT available via magnet
            links — only when loading from a .torrent file.
    """
    import libtorrent as lt

    if output_path is None:
        output_path = filepath + ".torrent"

    fs = lt.file_storage()
    lt.add_files(fs, filepath)

    ct = lt.create_torrent(fs, piece_size=piece_size)

    for tracker in PUBLIC_TRACKERS:
        ct.add_tracker(tracker)

    if comment:
        ct.set_comment(comment)

    lt.set_piece_hashes(ct, os.path.dirname(filepath) or ".")

    entry = ct.generate()

    # Inject snapshot metadata into the info dict so it survives
    # magnet link metadata exchange (BEP 9). The outer comment field
    # is NOT transferred via magnet — only the info dict is.
    if snapshot_meta:
        entry[b"info"][b"x-snapshot"] = snapshot_meta.encode("utf-8")

    torrent_data = lt.bencode(entry)
    with open(output_path, "wb") as f:
        f.write(torrent_data)

    info = lt.torrent_info(torrent_data)
    hashes = info.info_hashes()
    info_hashes = TorrentHashes(v1=str(hashes.v1), v2=str(hashes.v2))

    return output_path, info_hashes


def create_torrent_from_directory(
    directory: str,
    filenames: list[str],
    piece_size: int = 32 * 1024 * 1024,
    output_path: str | None = None,
) -> tuple[str, TorrentHashes]:
    import libtorrent as lt

    if output_path is None:
        output_path = os.path.join(directory, "nano-daily.torrent")

    fs = lt.file_storage()
    for fname in filenames:
        full_path = os.path.join(directory, fname)
        file_size = os.path.getsize(full_path)
        fs.add_file(fname, file_size)

    ct = lt.create_torrent(fs, piece_size=piece_size)

    for tracker in PUBLIC_TRACKERS:
        ct.add_tracker(tracker)

    lt.set_piece_hashes(ct, directory)

    torrent_data = lt.bencode(ct.generate())
    with open(output_path, "wb") as f:
        f.write(torrent_data)

    info = lt.torrent_info(torrent_data)
    hashes = info.info_hashes()
    info_hashes = TorrentHashes(v1=str(hashes.v1), v2=str(hashes.v2))

    return output_path, info_hashes


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: torrent_create.py <filepath> [output_path]", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    torrent_path, hashes = create_torrent(filepath, output_path=output_path)
    print(f"torrent={torrent_path}")
    print(f"info_hash_v1={hashes.v1}")
    print(f"info_hash_v2={hashes.v2}")


if __name__ == "__main__":
    main()
