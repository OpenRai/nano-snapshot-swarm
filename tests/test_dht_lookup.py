from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

sys.modules.setdefault("libtorrent", SimpleNamespace())

from mirror.libtorrent_session import AlertSnapshot, LibtorrentSession  # noqa: E402


def _candidate(sequence: int, *, authoritative: bool = False) -> AlertSnapshot:
    return AlertSnapshot(
        type_name="dht_mutable_item_alert",
        category=0,
        message="test DHT item",
        extra={"salt": "daily", "seq": sequence, "authoritative": authoritative},
    )


def test_lookup_collects_candidates_until_authoritative_completion() -> None:
    session = object.__new__(LibtorrentSession)
    session._alerts = []
    session._alert_lock = threading.Lock()
    session._alert_event = threading.Event()

    def dht_get_mutable_item(_public_key: bytes, _salt: str) -> None:
        with session._alert_lock:
            session._alerts.extend([_candidate(1355), _candidate(183, authoritative=True)])
        session._alert_event.set()

    session.dht_get_mutable_item = dht_get_mutable_item

    candidates = session.lookup_dht_mutable_item(b"key", "daily", timeout=0.1)

    assert [candidate.extra["seq"] for candidate in candidates or []] == [1355, 183]
