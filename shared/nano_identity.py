from __future__ import annotations

import hashlib
from typing import Tuple

try:
    from nacl.signing import SigningKey
except ImportError:
    SigningKey = None

NANO_PREFIX = "nano_"
NANO_ALPHABET = "13456789abcdefghijkmnopqrstuwxyz"
NANO_ALPHABET_MAP = {c: i for i, c in enumerate(NANO_ALPHABET)}


def _blake2b_5(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=5).digest()


def _nano_base32_encode(payload: bytes) -> str:
    data_reversed = payload[::-1]
    bits = 0
    acc = 0
    out = []
    for byte in data_reversed:
        acc |= byte << bits
        bits += 8
        while bits >= 5:
            out.append(NANO_ALPHABET[acc & 0x1F])
            acc >>= 5
            bits -= 5
    if bits > 0:
        out.append(NANO_ALPHABET[acc & 0x1F])
    return "".join(reversed(out))


def _nano_base32_decode(s: str) -> bytes:
    acc = 0
    bits = 0
    out = []
    for i in range(len(s) - 1, -1, -1):
        acc |= NANO_ALPHABET_MAP[s[i]] << bits
        bits += 5
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    return bytes(reversed(out))


def public_key_to_nano_address(pub_key_bytes: bytes) -> str:
    if len(pub_key_bytes) != 32:
        raise ValueError(f"Public key must be 32 bytes, got {len(pub_key_bytes)}")
    checksum = _blake2b_5(pub_key_bytes)
    encoded = _nano_base32_encode(pub_key_bytes + checksum[::-1])
    return NANO_PREFIX + encoded[:60]


def nano_address_to_public_key(address: str) -> bytes:
    if not address.startswith(NANO_PREFIX):
        raise ValueError(f"Address must start with '{NANO_PREFIX}'")
    encoded = address[len(NANO_PREFIX) :]
    if len(encoded) != 60:
        raise ValueError(f"Address body must be 60 chars, got {len(encoded)}")
    decoded = _nano_base32_decode(encoded)
    pub_key = decoded[:32]
    checksum = decoded[32:]
    expected_checksum = _blake2b_5(pub_key)
    if checksum[::-1] != expected_checksum:
        raise ValueError("Checksum mismatch: invalid Nano address")
    return pub_key


def _parse_private_key(hex_key: str) -> bytes:
    key_bytes = bytes.fromhex(hex_key)
    if len(key_bytes) == 64:
        return key_bytes[:32]
    if len(key_bytes) == 32:
        return key_bytes
    raise ValueError(f"Private key must be 32 or 64 bytes, got {len(key_bytes)}")


def derive_nano_address(private_key_hex: str) -> Tuple[bytes, str]:
    if SigningKey is None:
        raise ImportError("pynacl is required: pip install pynacl")
    seed = _parse_private_key(private_key_hex)
    signing_key = SigningKey(seed)
    pub_key_bytes = bytes(signing_key.verify_key)
    address = public_key_to_nano_address(pub_key_bytes)
    return pub_key_bytes, address


def verify_identity(pub_key_bytes: bytes, expected_nano_address: str) -> bool:
    try:
        derived_pub = nano_address_to_public_key(expected_nano_address)
        return pub_key_bytes == derived_pub
    except ValueError:
        return False


def compute_bep46_target_id(pub_key_bytes: bytes, salt: str = "daily") -> bytes:
    sha1 = hashlib.sha1()
    sha1.update(pub_key_bytes)
    if salt:
        sha1.update(salt.encode("utf-8"))
    return sha1.digest()
