from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("libtorrent")

from producer.retention import SNAPSHOT_NAME, retain_current_snapshot  # noqa: E402
from producer.seeder import (  # noqa: E402
    _load_current_torrent,
    _load_original_filename,
    _record_verified_publication,
    _should_log_seeding_status,
)


class _FakeSession:
    def __init__(self, canonical_info_hash: str, active: set[str] | None = None) -> None:
        self.canonical_info_hash = canonical_info_hash
        self.active = set(active or ())
        self.added: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.snapshot_meta: dict[str, str] | None = None

    def has_torrent(self, info_hash: str) -> bool:
        return info_hash in self.active

    def add_torrent(self, *, info_hash: str, save_path: str, torrent_file: str) -> None:
        del save_path
        loaded_info_hash = Path(torrent_file).parent.name
        if len(loaded_info_hash) != 64:
            loaded_info_hash = self.canonical_info_hash
        self.active.add(loaded_info_hash)
        self.added.append((loaded_info_hash, torrent_file))

    def remove_torrent(self, info_hash: str) -> None:
        self.active.discard(info_hash)
        self.removed.append(info_hash)

    def get_handle(self, info_hash: str) -> object | None:
        return object() if info_hash in self.active else None

    def torrent_metadata(self, info_hash: str) -> object | None:
        if info_hash not in self.active or self.snapshot_meta is None:
            return None
        return SimpleNamespace(snapshot_meta=self.snapshot_meta)


def test_seeder_records_dashboard_state_only_after_verified_publication(tmp_path: Path) -> None:
    state_path = tmp_path / "publisher_state.json"
    state_path.write_text(
        json.dumps(
            {
                "last_seq": 12,
                "last_info_hash": "ab" * 32,
                "last_dht_seq": 40,
                "last_dht_info_hash": "ab" * 32,
            }
        )
    )

    _record_verified_publication("cd" * 32, 41, str(state_path))

    state = json.loads(state_path.read_text())
    assert state == {
        "last_seq": 13,
        "last_info_hash": "cd" * 32,
        "last_dht_seq": 41,
        "last_dht_info_hash": "cd" * 32,
    }


def test_seeder_reports_original_filename_from_torrent_metadata(tmp_path: Path) -> None:
    info_hash = "ab" * 32
    (tmp_path / "snapshot-meta.json").write_text(
        json.dumps({"original_filename": "fallback.7z"})
    )
    session = _FakeSession(info_hash, {info_hash})
    session.snapshot_meta = {"original_filename": "from-torrent.7z"}

    assert _load_original_filename(session, str(tmp_path), info_hash) == "from-torrent.7z"


def test_seeder_restart_does_not_increment_dashboard_sequence(tmp_path: Path) -> None:
    state_path = tmp_path / "publisher_state.json"
    _record_verified_publication("ab" * 32, 40, str(state_path))
    _record_verified_publication("ab" * 32, 41, str(state_path))

    state = json.loads(state_path.read_text())
    assert state["last_seq"] == 1
    assert state["last_dht_seq"] == 41


def test_seeding_heartbeat_logs_every_five_minutes() -> None:
    assert _should_log_seeding_status(300, 0)
    assert not _should_log_seeding_status(299, 0)


@pytest.mark.parametrize("previous_already_active", [False, True])
def test_seeder_reload_keeps_retained_previous_torrent_active(
    tmp_path: Path, previous_already_active: bool
) -> None:
    previous_info_hash = "ab" * 32
    current_info_hash = "cd" * 32
    snapshot_path = tmp_path / SNAPSHOT_NAME
    torrent_path = tmp_path / f"{SNAPSHOT_NAME}.torrent"

    snapshot_path.write_bytes(b"previous snapshot")
    torrent_path.write_bytes(b"previous torrent")
    retain_current_snapshot(tmp_path, previous_info_hash, retention=1)

    snapshot_path.write_bytes(b"current snapshot")
    torrent_path.write_bytes(b"current torrent")
    (tmp_path / "snapshot-meta.json").write_text(
        json.dumps({"torrent_info_hash": current_info_hash})
    )

    active = {previous_info_hash} if previous_already_active else set()
    session = _FakeSession(current_info_hash, active)

    loaded_info_hash, _handle, _size = _load_current_torrent(
        session, str(tmp_path), previous_info_hash
    )

    assert loaded_info_hash == current_info_hash
    assert previous_info_hash in session.active
    assert previous_info_hash not in session.removed
    assert current_info_hash in session.active


def test_seeder_reload_removes_previous_torrent_without_retention(tmp_path: Path) -> None:
    previous_info_hash = "ab" * 32
    current_info_hash = "cd" * 32
    (tmp_path / SNAPSHOT_NAME).write_bytes(b"current snapshot")
    (tmp_path / f"{SNAPSHOT_NAME}.torrent").write_bytes(b"current torrent")
    (tmp_path / "snapshot-meta.json").write_text(
        json.dumps({"torrent_info_hash": current_info_hash})
    )
    session = _FakeSession(current_info_hash, {previous_info_hash})

    _load_current_torrent(session, str(tmp_path), previous_info_hash)

    assert session.removed == [previous_info_hash]
    assert previous_info_hash not in session.active
