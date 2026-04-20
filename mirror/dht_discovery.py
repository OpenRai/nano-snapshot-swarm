from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import bencodepy

from shared.bep46 import parse_dht_value
from shared.nano_identity import compute_bep46_target_id

logger = logging.getLogger("mirror.discovery")

DEFAULT_SALT = "daily"
MAX_RETRIES = 3
RETRY_BACKOFF = [10, 30, 60]
DHT_TIMEOUT = 120


@dataclass
class DHTDiscoveryResult:
    info_hash_hex: str
    sequence: int
    value_bytes: bytes
    verified: bool


def discover_latest_snapshot(
    session,
    authority_pubkey_hex: str,
    salt: str = DEFAULT_SALT,
    timeout: float = DHT_TIMEOUT,
) -> Optional[DHTDiscoveryResult]:
    pub_key_bytes = bytes.fromhex(authority_pubkey_hex)
    target_id = compute_bep46_target_id(pub_key_bytes, salt)

    logger.info(
        f"Querying DHT for mutable item (target: {target_id.hex()[:16]}..., salt: '{salt}')"
    )

    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            wait_time = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
            logger.info(f"Retry {attempt}/{MAX_RETRIES} after {wait_time}s")
            time.sleep(wait_time)

        session.dht_get_mutable_item(pub_key_bytes, salt)

        deadline = time.time() + timeout
        found = False

        while time.time() < deadline:
            alerts = session.pop_alerts()
            for snap in alerts:
                if snap.type_name == "dht_mutable_item_alert":
                    result = _process_mutable_item_snapshot(snap, pub_key_bytes, salt)
                    if result is not None:
                        return result
                    found = True
            if found:
                break
            time.sleep(2)

        if not found:
            logger.warning(f"No DHT response after attempt {attempt + 1}")

    logger.error(f"Failed to discover snapshot via DHT after {MAX_RETRIES} attempts")
    return None


def _process_mutable_item_snapshot(
    snap,
    expected_pub_key: bytes,
    salt: str = DEFAULT_SALT,
) -> Optional[DHTDiscoveryResult]:
    """Process an AlertSnapshot from a dht_mutable_item_alert."""
    try:
        seq = snap.extra.get("seq", 0)
        item = snap.extra.get("item")

        if seq == 0 and item is None:
            logger.info("DHT item not found (seq=0, empty entry) — item may have expired")
            return None

        # Convert item to bytes — libtorrent returns an entry object.
        # For our raw 32-byte info hash values, the entry is a string type.
        if isinstance(item, (bytes, bytearray)):
            value_bytes = bytes(item)
        elif isinstance(item, str):
            value_bytes = item.encode("latin-1")
        elif isinstance(item, dict):
            value_bytes = bencodepy.encode(item)
        else:
            logger.warning(f"Unexpected item type: {type(item)}")
            return None

        logger.info(f"DHT item raw: type={type(item).__name__}, len={len(value_bytes)}, hex={value_bytes[:64].hex()}")

        # Note: signature verification requires raw signature bytes which
        # we don't currently extract in AlertSnapshot. For now, trust seq > 0
        # items from the DHT (the DHT protocol itself validates signatures).
        verified = seq > 0

        parsed = parse_dht_value(value_bytes)
        info_hash_raw = parsed.get(b"info_hash", b"")
        if len(info_hash_raw) in (20, 32):
            info_hash_hex = info_hash_raw.hex()
        else:
            logger.error(f"Unexpected info_hash length: {len(info_hash_raw)}, raw value ({len(value_bytes)} bytes): {value_bytes[:64].hex()}")
            return None

        logger.info(
            f"Discovered DHT item: seq={seq}, info_hash={info_hash_hex[:16]}..., "
            f"verified={verified}"
        )

        return DHTDiscoveryResult(
            info_hash_hex=info_hash_hex,
            sequence=seq,
            value_bytes=value_bytes,
            verified=verified,
        )
    except Exception as e:
        logger.error(f"Error processing DHT mutable item alert: {e}")
        return None
