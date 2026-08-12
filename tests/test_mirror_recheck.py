from __future__ import annotations

import sys
from types import SimpleNamespace

from producer.retention import SNAPSHOT_NAME


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def pause_torrent(self, info_hash: str) -> None:
        self.calls.append(("pause", info_hash))

    def remove_torrent(self, info_hash: str) -> None:
        self.calls.append(("remove", info_hash))

    def ensure_torrent(self, **kwargs) -> None:
        self.calls.append(("ensure", kwargs))

    def force_recheck(self, info_hash: str) -> None:
        self.calls.append(("recheck", info_hash))

    def resume_torrent(self, info_hash: str) -> None:
        self.calls.append(("resume", info_hash))

    def torrent_metadata(self, info_hash: str):
        return None


def test_replacement_waits_for_metadata_recheck_before_resuming(tmp_path) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.libtorrent_session import TorrentMetadataSnapshot
    from mirror.reconcile import DesiredSnapshot, ReconcileDecision
    from mirror.watcher import MirrorWatcher

    watcher = MirrorWatcher(authority_pubkey_hex="ab" * 32, data_dir=str(tmp_path))
    session = FakeSession()
    watcher.session = session
    watcher._active_info_hash = "cd" * 32
    target = DesiredSnapshot(seq=2, info_hash="ef" * 32)

    watcher._apply_reconcile_decision(ReconcileDecision(action="replace", target=target))

    assert session.calls == [
        ("pause", "cd" * 32),
        ("remove", "cd" * 32),
        ("ensure", {"info_hash": "ef" * 32, "save_path": str(tmp_path), "paused": True}),
    ]

    watcher._apply_metadata(TorrentMetadataSnapshot(name=SNAPSHOT_NAME))
    watcher._begin_recheck_after_metadata(target.info_hash)
    watcher._resume_after_recheck(target.info_hash, "checking_files")
    watcher._resume_after_recheck(target.info_hash, "downloading")

    assert session.calls[-2:] == [("recheck", "ef" * 32), ("resume", "ef" * 32)]
