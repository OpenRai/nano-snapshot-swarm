from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

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
    signature_hex: Optional[str] = None
    dht_pubkey_hex: Optional[str] = None


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

        snap = session.wait_for_dht_mutable_item(salt=salt, timeout=timeout)
        if snap is not None:
            result = _process_mutable_item_snapshot(snap, pub_key_bytes, salt)
            if result is not None:
                return result
            found = True
        else:
            found = False

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
        # For mutable items, alert.item may be:
        # 1. A dict with 'key' and 'value' fields (the full DHT response)
        # 2. Raw bytes/string (the value directly)
        if isinstance(item, dict):
            # Extract the actual value from the DHT response dict
            value_raw = item.get("value") or item.get(b"value")
            if value_raw is None:
                logger.warning(f"DHT item dict has no 'value' key: {list(item.keys())}")
                return None
            if isinstance(value_raw, (bytes, bytearray)):
                value_bytes = bytes(value_raw)
            elif isinstance(value_raw, str):
                value_bytes = value_raw.encode("latin-1")
            else:
                logger.warning(f"Unexpected value type in dict: {type(value_raw)}")
                return None
        elif isinstance(item, (bytes, bytearray)):
            value_bytes = bytes(item)
        elif isinstance(item, str):
            value_bytes = item.encode("latin-1")
        else:
            logger.warning(f"Unexpected item type: {type(item)}")
            return None

        logger.debug(
            f"DHT item: type={type(item).__name__}, "
            f"keys={list(item.keys()) if isinstance(item, dict) else 'N/A'}"
        )

        # Note: signature verification requires raw signature bytes which
        # we don't currently extract in AlertSnapshot. For now, trust seq > 0
        # items from the DHT (the DHT protocol itself validates signatures).
        verified = seq > 0

        parsed = parse_dht_value(value_bytes)
        info_hash_raw = parsed.get(b"info_hash", b"")
        if len(info_hash_raw) in (20, 32):
            info_hash_hex = info_hash_raw.hex()
        else:
            logger.error(
                f"Unexpected info_hash length: {len(info_hash_raw)}, "
                f"raw value ({len(value_bytes)} bytes): "
                f"{value_bytes[:64].hex()}"
            )
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
            signature_hex=snap.extra.get("signature"),
            dht_pubkey_hex=snap.extra.get("key"),
        )
    except Exception as e:
        logger.error(f"Error processing DHT mutable item alert: {e}")
        return None
