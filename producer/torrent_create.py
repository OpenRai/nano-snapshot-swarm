from __future__ import annotations

import os
import sys


def _v2_flags(lt):
    """Build v2-only + merkle flags, compatible with libtorrent 2.0.x and 2.1+."""
    flags = lt.create_torrent_flags_t.v2_only
    # 2.1+ renamed merkle → merkle_tree
    if hasattr(lt.create_torrent_flags_t, "merkle_tree"):
        flags |= lt.create_torrent_flags_t.merkle_tree
    elif hasattr(lt.create_torrent_flags_t, "merkle"):
        flags |= lt.create_torrent_flags_t.merkle
    return flags


def create_torrent(
    filepath: str,
    web_seed_url: str | None = None,
    piece_size: int = 32 * 1024 * 1024,
    output_path: str | None = None,
    comment: str | None = None,
    snapshot_meta: str | None = None,
) -> tuple[str, str]:
    """Create a v2 torrent for a single file.

    Args:
        snapshot_meta: JSON string embedded as 'x-snapshot' in the info dict.
            Survives magnet link metadata exchange (BEP 9). Only include
            stable fields (source_url, original_filename) — not timestamps,
            since changes affect the info hash.
        comment: Stored in the outer torrent dict. NOT available via magnet
            links — only when loading from a .torrent file.
    """
    import libtorrent as lt

    if output_path is None:
        output_path = filepath + ".torrent"

    fs = lt.file_storage()
    filename = os.path.basename(filepath)
    lt.add_files(fs, filepath)

    ct = lt.create_torrent(fs, piece_size=piece_size, flags=_v2_flags(lt))

    if web_seed_url:
        seed_url = web_seed_url
        if seed_url.endswith("/"):
            seed_url += filename

        if hasattr(ct, "set_web_seeds"):
            ct.set_web_seeds([seed_url])
        else:
            ct.add_url_seed(seed_url)

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

    ct = lt.create_torrent(fs, piece_size=piece_size, flags=_v2_flags(lt))

    if web_seed_url:
        for fname in filenames:
            seed_url = f"{web_seed_url.rstrip('/')}/{fname}"
            if hasattr(ct, "set_web_seeds"):
                ct.set_web_seeds([seed_url])
            else:
                ct.add_url_seed(seed_url)

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
