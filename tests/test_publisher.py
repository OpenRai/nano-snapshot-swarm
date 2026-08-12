from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# The publisher tests replace the network session, so the optional native
# libtorrent module only needs to exist during module import.
sys.modules.setdefault("libtorrent", SimpleNamespace())

from mirror.libtorrent_session import AlertSnapshot  # noqa: E402
from producer import publish as publish_module  # noqa: E402
from shared.bep46 import build_dht_value, sign_mutable_item  # noqa: E402

PRIVATE_KEY = "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d"


def _public_key() -> bytes:
    import nacl.signing

    return bytes(nacl.signing.SigningKey(bytes.fromhex(PRIVATE_KEY)).verify_key)


def _mutable_alert(info_hash: str, sequence: int) -> AlertSnapshot:
    public_key = _public_key()
    value = build_dht_value(info_hash)
    signature, _ = sign_mutable_item(PRIVATE_KEY, value, sequence, "daily")
    return AlertSnapshot(
        type_name="dht_mutable_item_alert",
        category=0,
        message="verified test item",
        extra={
            "key": public_key.hex(),
            "signature": signature.hex(),
            "seq": sequence,
            "salt": "daily",
            "item": value,
        },
    )


class FakeDHTSession:
    def __init__(self, **_: object) -> None:
        self.info_hash = ""
        self.stopped = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True

    def wait_for_dht_bootstrap(self, timeout: float) -> bool:
        return True

    def publish_dht_mutable_item(
        self, _private_key: bytes, _public_key: bytes, value: bytes, _salt: str
    ) -> None:
        self.info_hash = value.hex()

    def wait_for_dht_put(self, timeout: float, *, salt: str) -> AlertSnapshot:
        return AlertSnapshot(
            type_name="dht_put_alert",
            category=0,
            message="put completed",
            extra={"salt": salt, "seq": 55, "num_success": 0},
        )

    def clear_alerts(self, _type_name: str) -> None:
        pass

    def dht_get_mutable_item(self, _public_key: bytes, _salt: str) -> None:
        pass

    def wait_for_dht_mutable_item(self, *, salt: str, timeout: float) -> AlertSnapshot:
        return _mutable_alert(self.info_hash, 55)


def test_publication_requires_verified_readback_and_persists_dht_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(publish_module, "LibtorrentSession", FakeDHTSession)
    state_path = tmp_path / "publisher-state.json"

    result = publish_module.publish_to_dht(
        private_key_hex=PRIVATE_KEY,
        info_hash_hex="ab" * 32,
        state_path=str(state_path),
        salt="daily",
    )

    assert result["confirmed"] is True
    assert result["dht_seq"] == 55
    saved = json.loads(state_path.read_text())
    assert saved["last_dht_seq"] == 55
    assert saved["last_dht_info_hash"] == "ab" * 32


class NeverVerifiesSession(FakeDHTSession):
    def wait_for_dht_mutable_item(self, *, salt: str, timeout: float) -> AlertSnapshot:
        return _mutable_alert("cd" * 32, 55)


def test_publication_does_not_persist_state_without_verified_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(publish_module, "LibtorrentSession", NeverVerifiesSession)
    monkeypatch.setattr(
        publish_module,
        "_wait_for_verified_snapshot",
        lambda *args, **kwargs: None,
    )
    state_path = tmp_path / "publisher-state.json"
    state_path.write_text(json.dumps({"last_seq": 7, "last_info_hash": "ef" * 32}))

    with pytest.raises(publish_module.PublishError):
        publish_module.publish_to_dht(
            private_key_hex=PRIVATE_KEY,
            info_hash_hex="ab" * 32,
            state_path=str(state_path),
            salt="daily",
        )

    assert json.loads(state_path.read_text()) == {
        "last_seq": 7,
        "last_info_hash": "ef" * 32,
    }
