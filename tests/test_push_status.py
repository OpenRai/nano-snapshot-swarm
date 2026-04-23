from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from producer.push_status import sign_push


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

        signature_hex = sign_push(private_key_hex, 42, "ab" * 32, "2026-04-23T00:00:00Z")
        signature = bytes.fromhex(signature_hex)

        message = b"42:" + b"ab" * 32 + b":2026-04-23T00:00:00Z"
        verify_key.verify(message, signature)

    def test_sign_rejects_wrong_key(self, key_pair):
        signing_key, _ = key_pair
        private_key_hex = signing_key._signing_key.hex()

        signature_hex = sign_push(private_key_hex, 42, "ab" * 32, "2026-04-23T00:00:00Z")
        signature = bytes.fromhex(signature_hex)

        wrong_key = bytes.fromhex(
            "cdbc9284015e84c225f0e67b891606505a60cf1218b127ac1c1edb6444567e6b"
        )
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey

        verify_key = VerifyKey(wrong_key)
        message = b"42:" + b"ab" * 32 + b":2026-04-23T00:00:00Z"
        with pytest.raises(BadSignatureError):
            verify_key.verify(message, signature)

    def test_different_sequence_produces_different_signature(self, key_pair):
        signing_key, _ = key_pair
        private_key_hex = signing_key._signing_key.hex()

        sig1 = sign_push(private_key_hex, 1, "ab" * 32, "2026-04-23T00:00:00Z")
        sig2 = sign_push(private_key_hex, 2, "ab" * 32, "2026-04-23T00:00:00Z")
        assert sig1 != sig2

    def test_different_info_hash_produces_different_signature(self, key_pair):
        signing_key, _ = key_pair
        private_key_hex = signing_key._signing_key.hex()

        sig1 = sign_push(private_key_hex, 1, "ab" * 32, "2026-04-23T00:00:00Z")
        sig2 = sign_push(private_key_hex, 1, "cd" * 32, "2026-04-23T00:00:00Z")
        assert sig1 != sig2
