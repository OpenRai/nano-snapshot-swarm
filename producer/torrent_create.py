from __future__ import annotations

import os
import sys


def create_torrent(
    filepath: str,
    web_seed_url: str | None = None,
    piece_size: int = 32 * 1024 * 1024,
    output_path: str | None = None,
) -> tuple[str, str]:
    import libtorrent as lt

    if output_path is None:
        output_path = filepath + ".torrent"

    fs = lt.file_storage()
    filename = os.path.basename(filepath)
    lt.add_files(fs, filepath)

    create_flags = lt.create_torrent_flags_t.v2_only | lt.create_torrent_flags_t.merkle_tree
    ct = lt.create_torrent(fs, piece_size=piece_size, flags=create_flags)

    if web_seed_url:
        url_list = [f"{web_seed_url.rstrip('/')}/{filename}"]
        ct.set_web_seeds(url_list)

    lt.set_piece_hashes(ct, os.path.dirname(filepath) or ".")

    torrent_data = lt.bencode(ct.generate())
    with open(output_path, "wb") as f:
        f.write(torrent_data)

    info = lt.torrent_info(torrent_data)
    info_hash_v2 = (
        str(info.info_hashes().v2) if hasattr(info, "info_hashes") else str(info.info_hash())
    )

    return output_path, info_hash_v2


def create_torrent_from_directory(
    directory: str,
    filenames: list[str],
    web_seed_url: str | None = None,
    piece_size: int = 32 * 1024 * 1024,
    output_path: str | None = None,
) -> tuple[str, str]:
    import libtorrent as lt

    if output_path is None:
        output_path = os.path.join(directory, "nano-daily.torrent")

    fs = lt.file_storage()
    for fname in filenames:
        full_path = os.path.join(directory, fname)
        file_size = os.path.getsize(full_path)
        fs.add_file(fname, file_size)

    create_flags = lt.create_torrent_flags_t.v2_only | lt.create_torrent_flags_t.merkle_tree
    ct = lt.create_torrent(fs, piece_size=piece_size, flags=create_flags)

    if web_seed_url:
        url_list = []
        for fname in filenames:
            url_list.append(f"{web_seed_url.rstrip('/')}/{fname}")
        ct.set_web_seeds(url_list)

    lt.set_piece_hashes(ct, directory)

    torrent_data = lt.bencode(ct.generate())
    with open(output_path, "wb") as f:
        f.write(torrent_data)

    info = lt.torrent_info(torrent_data)
    info_hash_v2 = (
        str(info.info_hashes().v2) if hasattr(info, "info_hashes") else str(info.info_hash())
    )

    return output_path, info_hash_v2


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: torrent_create.py <filepath> [web_seed_url] [output_path]", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    web_seed_url = sys.argv[2] if len(sys.argv) > 2 else None
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    torrent_path, info_hash = create_torrent(filepath, web_seed_url, output_path=output_path)
    print(f"torrent={torrent_path}")
    print(f"info_hash_v2={info_hash}")


if __name__ == "__main__":
    main()
