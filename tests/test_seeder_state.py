from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("libtorrent")

from producer.seeder import _record_verified_publication  # noqa: E402


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


def test_seeder_restart_does_not_increment_dashboard_sequence(tmp_path: Path) -> None:
    state_path = tmp_path / "publisher_state.json"
    _record_verified_publication("ab" * 32, 40, str(state_path))
    _record_verified_publication("ab" * 32, 41, str(state_path))

    state = json.loads(state_path.read_text())
    assert state["last_seq"] == 1
    assert state["last_dht_seq"] == 41
