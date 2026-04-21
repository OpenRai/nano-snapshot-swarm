from __future__ import annotations

import json

from mirror.state import MirrorState


def test_mirror_state_persists_phase_and_error(tmp_path) -> None:
    state_path = tmp_path / "mirror_state.json"
    state = MirrorState(str(state_path))

    state.update(7, "ab" * 32, "nano-validation-snapshot.7z")
    state.set_phase("checking", "")

    loaded = json.loads(state_path.read_text())
    assert loaded["last_seq"] == 7
    assert loaded["last_info_hash"] == "ab" * 32
    assert loaded["current_torrent_name"] == "nano-validation-snapshot.7z"
    assert loaded["phase"] == "checking"
    assert loaded["last_error"] == ""


def test_mirror_state_loads_phase_and_error(tmp_path) -> None:
    state_path = tmp_path / "mirror_state.json"
    state_path.write_text(
        json.dumps(
            {
                "last_seq": 9,
                "last_info_hash": "cd" * 32,
                "current_torrent_name": "nano-ledger-snapshot.7z",
                "phase": "error",
                "last_error": "download stalled",
            }
        )
    )

    state = MirrorState(str(state_path))
    assert state.last_seq == 9
    assert state.last_info_hash == "cd" * 32
    assert state.current_torrent_name == "nano-ledger-snapshot.7z"
    assert state.phase == "error"
    assert state.last_error == "download stalled"


def test_mirror_state_update_preserves_torrent_name_when_omitted(tmp_path) -> None:
    state_path = tmp_path / "mirror_state.json"
    state = MirrorState(str(state_path))

    state.update(4, "ab" * 32, "nano-ledger-snapshot.7z")
    state.update(5, "cd" * 32)

    loaded = json.loads(state_path.read_text())
    assert loaded["last_seq"] == 5
    assert loaded["last_info_hash"] == "cd" * 32
    assert loaded["current_torrent_name"] == "nano-ledger-snapshot.7z"
