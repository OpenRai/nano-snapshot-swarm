from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.bep46 import (
    build_signature_buffer,
    sign_mutable_item,
    verify_bep46_test_vectors,
    verify_mutable_item,
)
from shared.nano_identity import (
    compute_bep46_target_id,
    derive_nano_address,
    nano_address_to_public_key,
    public_key_to_nano_address,
    verify_identity,
)

# BEP 46 test vectors
KNOWN_PUBKEY_HEX = "77ff84905a91936367c01360803104f92432fcd904a43511876df5cdf3e7e548"
# 64-byte private key from spec (seed || public_key_or_other)
# pynacl takes 32-byte seed
KNOWN_SEED_HEX = "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d"
KNOWN_PRIVKEY_64_HEX = (
    "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d"
    "b7e7a9fea2c0eb269d61e3b38e450a22e754941ac78479d6c54e1faf6037881d"
)


class TestNanoIdentity:
    def test_public_key_to_address_roundtrip(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        address = public_key_to_nano_address(pub_bytes)
        recovered = nano_address_to_public_key(address)
        assert recovered == pub_bytes

    def test_address_starts_with_nano_prefix(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        address = public_key_to_nano_address(pub_bytes)
        assert address.startswith("nano_")

    def test_address_length(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        address = public_key_to_nano_address(pub_bytes)
        assert len(address) == 5 + 60  # prefix + 60 chars

    def test_invalid_address_raises(self):
        with pytest.raises(ValueError):
            nano_address_to_public_key("invalid_address")

    def test_verify_identity_match(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        address = public_key_to_nano_address(pub_bytes)
        assert verify_identity(pub_bytes, address) is True

    def test_verify_identity_mismatch(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        wrong_address = public_key_to_nano_address(bytes(32))
        assert verify_identity(pub_bytes, wrong_address) is False

    def test_derive_nano_address_from_seed(self):
        pub_key, address = derive_nano_address(KNOWN_SEED_HEX)
        assert verify_identity(pub_key, address)

    def test_derive_nano_address_from_64byte_key(self):
        pub_key, address = derive_nano_address(KNOWN_PRIVKEY_64_HEX)
        assert verify_identity(pub_key, address)

    def test_bep46_target_id_no_salt(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        target_id = compute_bep46_target_id(pub_bytes, salt="")
        expected = hashlib.sha1(pub_bytes).digest()
        assert target_id == expected

    def test_bep46_target_id_with_salt(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        target_id = compute_bep46_target_id(pub_bytes, salt="daily")
        expected = hashlib.sha1(pub_bytes + b"daily").digest()
        assert target_id == expected

    def test_bep46_test_vector_1_target(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        target = hashlib.sha1(pub_bytes).digest()
        expected_target = bytes.fromhex("4a533d47ec9c7d95b1ad75f576cffc641853b750")
        assert target == expected_target

    def test_bep46_test_vector_2_target(self):
        pub_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        target = hashlib.sha1(pub_bytes + b"foobar").digest()
        expected_target = bytes.fromhex("411eba73b6f087ca51a3795d9c8c938d365e32c1")
        assert target == expected_target


class TestBEP46:
    def test_signature_buffer_no_salt(self):
        buffer = build_signature_buffer(seq=1, value=b"Hello World!", salt="")
        assert buffer == b"3:seqi1e1:v12:Hello World!"

    def test_signature_buffer_with_salt(self):
        buffer = build_signature_buffer(seq=1, value=b"Hello World!", salt="foobar")
        assert buffer == b"4:salt6:foobar3:seqi1e1:v12:Hello World!"

    def test_sign_and_verify_no_salt(self):
        value = b"Hello World!"
        signature, pub_key = sign_mutable_item(KNOWN_SEED_HEX, value, seq=1, salt="")
        assert verify_mutable_item(pub_key, value, 1, signature, salt="")

    def test_sign_and_verify_with_salt(self):
        value = b"Hello World!"
        signature, pub_key = sign_mutable_item(KNOWN_SEED_HEX, value, seq=1, salt="foobar")
        assert verify_mutable_item(pub_key, value, 1, signature, salt="foobar")

    def test_sign_with_64byte_key(self):
        value = b"Hello World!"
        signature, pub_key = sign_mutable_item(KNOWN_PRIVKEY_64_HEX, value, seq=1, salt="")
        assert verify_mutable_item(pub_key, value, 1, signature, salt="")

    def test_verify_rejects_wrong_key(self):
        value = b"Hello World!"
        signature, _ = sign_mutable_item(KNOWN_SEED_HEX, value, seq=1, salt="")
        wrong_pub = bytes(32)
        assert verify_mutable_item(wrong_pub, value, 1, signature, salt="") is False

    def test_verify_rejects_wrong_seq(self):
        value = b"Hello World!"
        signature, pub_key = sign_mutable_item(KNOWN_SEED_HEX, value, seq=1, salt="")
        assert verify_mutable_item(pub_key, value, 2, signature, salt="") is False

    def test_verify_rejects_wrong_value(self):
        signature, pub_key = sign_mutable_item(KNOWN_SEED_HEX, b"Hello World!", seq=1, salt="")
        assert verify_mutable_item(pub_key, b"Different data", 1, signature, salt="") is False

    def test_bep46_test_vectors_pass(self):
        assert verify_bep46_test_vectors() is True

    def test_bep46_test_vector_2_signature(self):
        signature_hex = (
            "6834284b6b24c3204eb2fea824d82f88883a3d95e8b4a21b8c0ded553d17d17d"
            "df9a8a7104b1258f30bed3787e6cb896fca78c58f8e03b5f18f14951a87d9a08"
        )
        expected_sig = bytes.fromhex(signature_hex)
        pub_key_bytes = bytes.fromhex(KNOWN_PUBKEY_HEX)
        value = b"Hello World!"
        assert verify_mutable_item(pub_key_bytes, value, 1, expected_sig, salt="foobar")


class TestBuildDHTValue:
    def test_build_and_parse_roundtrip(self):
        from shared.bep46 import build_dht_value, parse_dht_value

        info_hash_hex = "ab" * 32
        value_bytes = build_dht_value(info_hash_hex)
        parsed = parse_dht_value(value_bytes)
        assert parsed[b"info_hash"] == bytes.fromhex(info_hash_hex)

    def test_value_is_raw_info_hash(self):
        from shared.bep46 import build_dht_value

        info_hash_hex = "ab" * 32
        value_bytes = build_dht_value(info_hash_hex)
        assert len(value_bytes) == 32
        assert value_bytes == bytes.fromhex(info_hash_hex)

    def test_value_size_under_limit(self):
        from shared.bep46 import build_dht_value

        info_hash_hex = "ab" * 32
        value_bytes = build_dht_value(info_hash_hex)
        assert len(value_bytes) < 1000

    def test_sign_verify_with_raw_info_hash(self):
        """Verify that signing raw info hash bytes works end-to-end."""
        from shared.bep46 import build_dht_value

        info_hash_hex = "ab" * 32
        value_bytes = build_dht_value(info_hash_hex)
        signature, pub_key = sign_mutable_item(KNOWN_SEED_HEX, value_bytes, seq=1, salt="daily")
        assert verify_mutable_item(pub_key, value_bytes, 1, signature, salt="daily")

    def test_parse_legacy_bencoded_value(self):
        """parse_dht_value should still handle legacy bencoded dict format."""
        import bencodepy

        from shared.bep46 import parse_dht_value

        legacy = bencodepy.encode({b"info_hash": b"\xab" * 32, b"v": 2})
        parsed = parse_dht_value(legacy)
        assert parsed[b"info_hash"] == b"\xab" * 32
