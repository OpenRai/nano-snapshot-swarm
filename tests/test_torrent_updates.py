from __future__ import annotations

import sys
from types import SimpleNamespace

from producer.retention import SNAPSHOT_NAME, retain_current_snapshot, retained_torrent_pairs
from producer.torrent_create import create_torrent


def test_create_torrent_returns_hybrid_hashes(monkeypatch, tmp_path) -> None:
    calls = []

    class FakeCreateTorrent:
        def add_tracker(self, tracker):
            calls.append(("tracker", tracker))

        def set_comment(self, comment):
            calls.append(("comment", comment))

        def generate(self):
            return {b"info": {}}

    fake_libtorrent = SimpleNamespace(
        file_storage=lambda: object(),
        add_files=lambda storage, path: calls.append(("add_files", path)),
        create_torrent=lambda storage, piece_size: FakeCreateTorrent(),
        set_piece_hashes=lambda torrent, directory: calls.append(("hashes", directory)),
        bencode=lambda entry: b"fake-torrent",
        torrent_info=lambda data: SimpleNamespace(
            info_hashes=lambda: SimpleNamespace(v1="11" * 20, v2="22" * 32)
        ),
    )
    monkeypatch.setitem(sys.modules, "libtorrent", fake_libtorrent)
    archive = tmp_path / SNAPSHOT_NAME
    archive.write_bytes(b"snapshot")

    _, hashes = create_torrent(str(archive), snapshot_meta='{"original_filename":"upstream.7z"}')

    assert hashes.v1 == "11" * 20
    assert hashes.v2 == "22" * 32
    assert ("hashes", str(tmp_path)) in calls


def test_retention_keeps_only_requested_prior_pairs(tmp_path) -> None:
    archive = tmp_path / SNAPSHOT_NAME
    torrent = tmp_path / f"{SNAPSHOT_NAME}.torrent"
    archive.write_bytes(b"first")
    torrent.write_bytes(b"first-torrent")
    retain_current_snapshot(tmp_path, "aa" * 32, retention=1)

    archive.write_bytes(b"second")
    torrent.write_bytes(b"second-torrent")
    retain_current_snapshot(tmp_path, "bb" * 32, retention=1)

    pairs = retained_torrent_pairs(tmp_path)
    assert [torrent.parent.name for _, torrent in pairs] == ["bb" * 32]
    assert pairs[0][0].read_bytes() == b"second"
    assert pairs[0][1].read_bytes() == b"second-torrent"

    retain_current_snapshot(tmp_path, "cc" * 32, retention=0)
    assert retained_torrent_pairs(tmp_path) == []
