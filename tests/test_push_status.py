from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, "status-api")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from producer.push_status import canonical_push_payload, sign_push


@pytest.fixture
def key_pair():
    """Return a deterministic Ed25519 key pair for testing."""
    from nacl.signing import SigningKey

    seed = bytes.fromhex("e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d")
    signing_key = SigningKey(seed)
    verify_key = signing_key.verify_key
    return signing_key, verify_key


class TestSignPush:
    def test_sign_and_verify_roundtrip(self, key_pair):
        signing_key, verify_key = key_pair
        private_key_hex = signing_key._signing_key.hex()
        payload = {
            "sequence": 42,
            "info_hash": "ab" * 32,
            "info_hash_v1": "cd" * 20,
            "torrent_name": "nano-ledger-snapshot.7z",
            "piece_size": 33554432,
            "snapshot_size_bytes": 64320000000,
            "timestamp": "2026-04-23T00:00:00Z",
            "torrent_file_b64": "ZmFrZQ==",
            "archive_listing": "listing",
        }

        signature_hex = sign_push(private_key_hex, payload)
        signature = bytes.fromhex(signature_hex)

        verify_key.verify(canonical_push_payload(payload), signature)

        from app.main import canonical_push_payload as api_canonical_push_payload

        assert api_canonical_push_payload(payload) == canonical_push_payload(payload)

    def test_sign_rejects_wrong_key(self, key_pair):
        signing_key, _ = key_pair
        private_key_hex = signing_key._signing_key.hex()

        payload = {
            "sequence": 42,
            "info_hash": "ab" * 32,
            "torrent_name": "nano-ledger-snapshot.7z",
            "piece_size": 1,
            "snapshot_size_bytes": 1,
            "timestamp": "2026-04-23T00:00:00Z",
            "torrent_file_b64": "ZmFrZQ==",
        }
        signature_hex = sign_push(private_key_hex, payload)
        signature = bytes.fromhex(signature_hex)

        wrong_key = bytes.fromhex(
            "cdbc9284015e84c225f0e67b891606505a60cf1218b127ac1c1edb6444567e6b"
        )
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey

        verify_key = VerifyKey(wrong_key)
        with pytest.raises(BadSignatureError):
            verify_key.verify(canonical_push_payload(payload), signature)

    def test_different_sequence_produces_different_signature(self, key_pair):
        signing_key, _ = key_pair
        private_key_hex = signing_key._signing_key.hex()

        first = {"sequence": 1, "info_hash": "ab" * 32, "timestamp": "2026-04-23T00:00:00Z"}
        second = {**first, "sequence": 2}
        sig1 = sign_push(private_key_hex, first)
        sig2 = sign_push(private_key_hex, second)
        assert sig1 != sig2

    def test_different_info_hash_produces_different_signature(self, key_pair):
        signing_key, _ = key_pair
        private_key_hex = signing_key._signing_key.hex()

        first = {"sequence": 1, "info_hash": "ab" * 32, "timestamp": "2026-04-23T00:00:00Z"}
        second = {**first, "info_hash": "cd" * 32}
        sig1 = sign_push(private_key_hex, first)
        sig2 = sign_push(private_key_hex, second)
        assert sig1 != sig2
