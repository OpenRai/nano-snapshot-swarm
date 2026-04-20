from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import bencodepy
import libtorrent as lt

from shared.bep46 import parse_dht_value, verify_mutable_item
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
            for alert in alerts:
                if not hasattr(lt, "dht_mutable_item_alert"):
                    continue
                if isinstance(alert, lt.dht_mutable_item_alert):
                    result = _process_mutable_item_alert(alert, pub_key_bytes, salt)
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


def _process_mutable_item_alert(
    alert,
    expected_pub_key: bytes,
    salt: str = DEFAULT_SALT,
) -> Optional[DHTDiscoveryResult]:
    try:
        # libtorrent 2.x: alert.item returns a libtorrent entry object
        # Use lt.bencode() to serialize it back to bytes
        try:
            value_data = alert.item
        except Exception:
            value_data = None
        
        if value_data is not None:
            if isinstance(value_data, dict):
                value_bytes = bencodepy.encode(value_data)
            elif isinstance(value_data, (bytes, bytearray)):
                value_bytes = bytes(value_data)
            elif isinstance(value_data, str):
                value_bytes = value_data.encode("latin-1")
            else:
                # libtorrent entry object — bencode it
                try:
                    bencoded = lt.bencode(value_data)
                    value_bytes = bencoded if isinstance(bencoded, bytes) else bencoded.encode("latin-1")
                except Exception as e:
                    logger.warning(f"Failed to bencode entry: {e}, type: {type(value_data)}")
                    return None
        else:
            # Fallback: try to extract from alert message
            logger.warning("Could not access alert.item directly")
            return None

        seq = alert.seq if hasattr(alert, "seq") else 0
        signature = alert.signature if hasattr(alert, "signature") else b""
        alert_salt = alert.salt if hasattr(alert, "salt") else None

        verified = False
        if signature and seq > 0:
            verified = verify_mutable_item(
                expected_pub_key, value_bytes, seq, signature, salt=alert_salt or salt
            )
            if not verified:
                logger.error("DHT item signature verification FAILED — rejecting")
                return None

        parsed = parse_dht_value(value_bytes)
        info_hash_raw = parsed.get(b"info_hash", b"")
        if len(info_hash_raw) == 32:
            info_hash_hex = info_hash_raw.hex()
        elif len(info_hash_raw) == 20:
            info_hash_hex = info_hash_raw.hex()
        else:
            logger.error(f"Unexpected info_hash length: {len(info_hash_raw)}")
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
