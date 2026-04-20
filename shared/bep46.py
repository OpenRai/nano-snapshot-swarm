from __future__ import annotations

import hashlib

try:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import SigningKey
except ImportError:
    SigningKey = None
    BadSignatureError = Exception


def _bencode_value(value: bytes) -> bytes:
    return str(len(value)).encode("ascii") + b":" + value


def build_signature_buffer(seq: int, value: bytes, salt: str = "") -> bytes:
    parts = []
    if salt:
        parts.append(_bencode_value(b"salt"))
        parts.append(_bencode_value(salt.encode("utf-8")))
    parts.append(b"3:seqi" + str(seq).encode("ascii") + b"e")
    parts.append(b"1:v" + _bencode_value(value))
    # Remove the length prefix from the value part — bencode format is key+length+value
    # The value portion in the signature buffer uses the raw bencoded form
    # Correcting: "3:seqi<seq>e1:v<len>:<value>"
    result = b""
    if salt:
        result += b"4:salt" + _bencode_value(salt.encode("utf-8"))
    result += b"3:seqi" + str(seq).encode("ascii") + b"e"
    result += b"1:v" + str(len(value)).encode("ascii") + b":" + value
    return result


def _parse_private_key(hex_key: str) -> bytes:
    key_bytes = bytes.fromhex(hex_key)
    if len(key_bytes) == 64:
        return key_bytes[:32]
    if len(key_bytes) == 32:
        return key_bytes
    raise ValueError(f"Private key must be 32 or 64 bytes, got {len(key_bytes)}")


def sign_mutable_item(
    private_key_hex: str,
    value: bytes,
    seq: int,
    salt: str = "daily",
) -> tuple[bytes, bytes]:
    if SigningKey is None:
        raise ImportError("pynacl is required: pip install pynacl")
    seed = _parse_private_key(private_key_hex)
    signing_key = SigningKey(seed)
    message = build_signature_buffer(seq, value, salt)
    signature = signing_key.sign(message)
    # signing_key.sign returns SignedMessage: signature (64 bytes) + message
    return signature.signature, bytes(signing_key.verify_key)


def verify_mutable_item(
    pub_key_bytes: bytes,
    value: bytes,
    seq: int,
    signature: bytes,
    salt: str = "daily",
) -> bool:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    verify_key = VerifyKey(pub_key_bytes)
    message = build_signature_buffer(seq, value, salt)
    try:
        verify_key.verify(message, signature)
        return True
    except (BadSignatureError, Exception):
        return False


def build_dht_value(info_hash_hex: str, piece_size: int = 0) -> bytes:
    """Build the DHT mutable item value: raw 32-byte info hash.

    libtorrent's dht_put_mutable_item() treats the data arg as a raw string
    and bencodes it internally. Passing already-bencoded data causes double
    bencoding and signature verification failures. So we pass just the raw
    info hash bytes — minimal, unambiguous, and correctly signed.

    The piece_size parameter is accepted for API compatibility but ignored;
    it is not included in the DHT value (mirror gets it from the .torrent).
    """
    info_hash = bytes.fromhex(info_hash_hex)
    if len(info_hash) not in (20, 32):
        raise ValueError(f"info_hash must be 20 or 32 bytes, got {len(info_hash)}")
    return info_hash


def parse_dht_value(raw_value: bytes) -> dict:
    """Parse DHT mutable item value back to a dict with info_hash key.

    Accepts raw 20/32-byte info hash (new format) or falls back to
    bencoded dict decoding (legacy format).
    """
    if len(raw_value) in (20, 32):
        return {b"info_hash": raw_value}
    # Legacy: try bencoded dict
    try:
        import bencodepy
        return bencodepy.decode(raw_value)
    except Exception:
        raise ValueError(f"Cannot parse DHT value: {len(raw_value)} bytes")


def verify_bep46_test_vectors() -> bool:
    # Test vector 1: mutable item without salt
    pub_key_hex = "77ff84905a91936367c01360803104f92432fcd904a43511876df5cdf3e7e548"
    value = b"Hello World!"
    seq = 1

    sig_buffer = build_signature_buffer(seq, value, salt="")
    expected_buffer = b"3:seqi1e1:v12:Hello World!"
    if sig_buffer != expected_buffer:
        return False

    # Test vector 2: mutable item with salt "foobar"
    sig_buffer_salt = build_signature_buffer(seq, value, salt="foobar")
    expected_buffer_salt = b"4:salt6:foobar3:seqi1e1:v12:Hello World!"
    if sig_buffer_salt != expected_buffer_salt:
        return False

    # Verify target ID computation
    pub_key_bytes = bytes.fromhex(pub_key_hex)
    target_no_salt = hashlib.sha1(pub_key_bytes).digest()
    expected_target = bytes.fromhex("4a533d47ec9c7d95b1ad75f576cffc641853b750")
    if target_no_salt != expected_target:
        return False

    target_with_salt = hashlib.sha1(pub_key_bytes + b"foobar").digest()
    expected_target_salt = bytes.fromhex("411eba73b6f087ca51a3795d9c8c938d365e32c1")
    if target_with_salt != expected_target_salt:
        return False

    # Verify the BEP 46 test vector 2 signature verifies against the known public key
    # Note: the test vector private key uses a different Ed25519 key format than pynacl,
    # so we only verify the signature, not re-sign with the test vector key.
    signature_hex = (
        "6834284b6b24c3204eb2fea824d82f88883a3d95e8b4a21b8c0ded553d17d17d"
        "df9a8a7104b1258f30bed3787e6cb896fca78c58f8e03b5f18f14951a87d9a08"
    )
    expected_sig = bytes.fromhex(signature_hex)

    if not verify_mutable_item(pub_key_bytes, value, seq, expected_sig, salt="foobar"):
        return False

    # Verify that our own sign/verify roundtrip works correctly
    our_sig, our_pub = sign_mutable_item(
        "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d",
        value,
        seq,
        salt="foobar",
    )
    if not verify_mutable_item(our_pub, value, seq, our_sig, salt="foobar"):
        return False

    return True
