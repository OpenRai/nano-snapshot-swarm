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


def _mutable_alert(
    info_hash: str,
    sequence: int,
    *,
    authoritative: bool = True,
) -> AlertSnapshot:
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
            "authoritative": authoritative,
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

    def wait_for_dht_mutable_item(
        self,
        *,
        salt: str,
        timeout: float,
        authoritative_only: bool,
    ) -> AlertSnapshot:
        assert authoritative_only is True
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
    def wait_for_dht_mutable_item(
        self,
        *,
        salt: str,
        timeout: float,
        authoritative_only: bool,
    ) -> AlertSnapshot:
        assert authoritative_only is True
        return _mutable_alert("cd" * 32, 55)


class StaleThenAuthoritativeSession(FakeDHTSession):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.reads = 0

    def wait_for_dht_mutable_item(
        self,
        *,
        salt: str,
        timeout: float,
        authoritative_only: bool,
    ) -> AlertSnapshot:
        assert authoritative_only is True
        self.reads += 1
        if self.reads == 1:
            return _mutable_alert("ab" * 32, 133, authoritative=False)
        return _mutable_alert(self.info_hash, 1305, authoritative=True)


def test_publication_ignores_non_authoritative_signed_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(publish_module, "LibtorrentSession", StaleThenAuthoritativeSession)
    state_path = tmp_path / "publisher-state.json"

    result = publish_module.publish_to_dht(
        private_key_hex=PRIVATE_KEY,
        info_hash_hex="ab" * 32,
        state_path=str(state_path),
        salt="daily",
    )

    assert result["dht_seq"] == 1305


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


def test_completed_put_is_not_republished_when_readback_is_delayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedReadbackSession(FakeDHTSession):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.publish_count = 0

        def publish_dht_mutable_item(
            self, private_key: bytes, public_key: bytes, value: bytes, salt: str
        ) -> None:
            self.publish_count += 1
            super().publish_dht_mutable_item(private_key, public_key, value, salt)

    session = DelayedReadbackSession()
    from producer import seeder as seeder_module

    monkeypatch.setattr(seeder_module, "_wait_for_verified_snapshot", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="not verified"):
        seeder_module._dht_publish(
            session,
            bytes(64),
            _public_key(),
            "ab" * 32,
            "daily",
        )

    assert session.publish_count == 1
