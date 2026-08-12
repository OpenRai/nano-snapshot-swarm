from types import SimpleNamespace

from mirror.dht_discovery import _process_mutable_item_snapshot, discover_latest_snapshot
from shared.bep46 import sign_mutable_item

PRIVATE_KEY_HEX = "01" * 32
INFO_HASH = bytes.fromhex("ab" * 32)


def signed_snapshot(
    *,
    value: bytes = INFO_HASH,
    sequence: int = 1,
    key_hex: str | None = None,
    signature_hex: str | None = None,
):
    signature, public_key = sign_mutable_item(
        PRIVATE_KEY_HEX, value, seq=sequence, salt="daily"
    )
    return SimpleNamespace(
        extra={
            "seq": sequence,
            "item": value,
            "key": key_hex or public_key.hex(),
            "signature": signature_hex or signature.hex(),
        }
    ), public_key


def test_accepts_mutable_item_signed_by_configured_authority():
    snapshot, public_key = signed_snapshot()

    result = _process_mutable_item_snapshot(snapshot, public_key)

    assert result is not None
    assert result.verified is True
    assert result.info_hash_hex == INFO_HASH.hex()


def test_rejects_mutable_item_with_missing_signature():
    snapshot, public_key = signed_snapshot()
    del snapshot.extra["signature"]

    assert _process_mutable_item_snapshot(snapshot, public_key) is None


def test_rejects_mutable_item_with_different_public_key():
    snapshot, public_key = signed_snapshot(key_hex=("02" * 32))

    assert _process_mutable_item_snapshot(snapshot, public_key) is None


def test_rejects_mutable_item_with_invalid_signature():
    snapshot, public_key = signed_snapshot(signature_hex="00" * 64)

    assert _process_mutable_item_snapshot(snapshot, public_key) is None


def test_discovery_retries_stale_verified_item_until_minimum_sequence(monkeypatch):
    stale, public_key = signed_snapshot(sequence=7)
    current, _ = signed_snapshot(value=bytes.fromhex("cd" * 32), sequence=9)

    class FakeSession:
        def __init__(self) -> None:
            self.snapshots = [stale, current]
            self.get_calls = 0

        def dht_get_mutable_item(self, pubkey: bytes, salt: str) -> None:
            self.get_calls += 1

        def wait_for_dht_mutable_item(self, *, salt: str, timeout: float):
            return self.snapshots.pop(0)

    session = FakeSession()
    monkeypatch.setattr("mirror.dht_discovery.time.sleep", lambda _seconds: None)

    result = discover_latest_snapshot(
        session,
        public_key.hex(),
        min_sequence=8,
    )

    assert result is not None
    assert result.sequence == 9
    assert result.info_hash_hex == "cd" * 32
    assert session.get_calls == 2
