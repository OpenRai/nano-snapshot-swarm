from types import SimpleNamespace

from mirror.dht_discovery import _process_mutable_item_snapshot
from shared.bep46 import sign_mutable_item

PRIVATE_KEY_HEX = "01" * 32
INFO_HASH = bytes.fromhex("ab" * 32)


def signed_snapshot(
    *,
    value: bytes = INFO_HASH,
    key_hex: str | None = None,
    signature_hex: str | None = None,
):
    signature, public_key = sign_mutable_item(PRIVATE_KEY_HEX, value, seq=1, salt="daily")
    return SimpleNamespace(
        extra={
            "seq": 1,
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
