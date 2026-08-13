from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

from mirror.libtorrent_session import AlertSnapshot, LibtorrentSession
from shared.bep46 import build_dht_value, parse_dht_value, verify_mutable_item
from shared.nano_identity import compute_bep46_target_id, derive_nano_address

STATE_FILE = "publisher_state.json"
DEFAULT_SALT = "daily"
DHT_PUBLISH_TIMEOUT = 120
DHT_BOOTSTRAP_TIMEOUT = 120
DHT_PUBLISH_ATTEMPTS = 3
DHT_RETRY_DELAY = 5
DHT_VERIFY_TIMEOUT = 120

logger = logging.getLogger("producer.publish")


def load_state(state_path: str = STATE_FILE) -> dict:
    p = Path(state_path)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {"last_seq": 0, "last_info_hash": ""}


def save_state(state: dict, state_path: str = STATE_FILE) -> None:
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


class PublishError(Exception):
    pass


def _expanded_libtorrent_private_key(private_key_hex: str) -> bytes:
    """Return libtorrent's 64-byte expanded Ed25519 secret key."""
    seed = bytes.fromhex(private_key_hex)
    if len(seed) == 64:
        seed = seed[:32]
    if len(seed) != 32:
        raise ValueError("DHT private key must be 32 or 64 bytes")
    expanded = bytearray(hashlib.sha512(seed).digest())
    expanded[0] &= 248
    expanded[31] &= 63
    expanded[31] |= 64
    # libtorrent's secret key is the expanded 64-byte Ed25519 hash.
    return bytes(expanded)


def _raw_dht_value(item: object) -> bytes | None:
    if isinstance(item, (bytes, bytearray)):
        return bytes(item)
    if isinstance(item, str):
        return item.encode("latin-1")
    if isinstance(item, dict):
        value = item.get("value") or item.get(b"value")
        return _raw_dht_value(value)
    return None


def _verified_snapshot_from_alert(
    alert: AlertSnapshot,
    expected_public_key: bytes,
    expected_info_hash: str,
    salt: str,
) -> tuple[int, str] | None:
    returned_key_hex = alert.extra.get("key")
    signature_hex = alert.extra.get("signature")
    sequence = alert.extra.get("seq", 0)
    value = _raw_dht_value(alert.extra.get("item"))
    if not isinstance(returned_key_hex, str) or not isinstance(signature_hex, str):
        return None
    if value is None:
        return None
    try:
        returned_key = bytes.fromhex(returned_key_hex)
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return None
    if returned_key != expected_public_key:
        return None
    if not verify_mutable_item(returned_key, value, int(sequence), signature, salt):
        return None
    try:
        info_hash = parse_dht_value(value)[b"info_hash"].hex()
    except (KeyError, TypeError, ValueError):
        return None
    if info_hash != expected_info_hash:
        return None
    return int(sequence), info_hash


def _wait_for_verified_snapshot(
    session: LibtorrentSession,
    public_key: bytes,
    expected_info_hash: str,
    salt: str,
    timeout: float,
) -> tuple[int, str] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session.clear_alerts("dht_mutable_item_alert")
        session.dht_get_mutable_item(public_key, salt)
        remaining = max(1.0, deadline - time.time())
        alert = session.wait_for_dht_mutable_item(
            salt=salt,
            timeout=min(15.0, remaining),
            authoritative_only=True,
        )
        if alert is None:
            continue
        if alert.extra.get("authoritative") is not True:
            logger.debug("Ignoring non-authoritative DHT mutable-item read-back")
            continue
        returned_key = alert.extra.get("key")
        returned_value = _raw_dht_value(alert.extra.get("item"))
        returned_hash = "unparseable"
        if returned_value is not None:
            try:
                returned_hash = parse_dht_value(returned_value)[b"info_hash"].hex()[:16] + "..."
            except (KeyError, TypeError, ValueError):
                pass
        logger.info(
            "DHT mutable-item read-back candidate: authoritative=true, sequence=%s, "
            "public key match=%s, info hash=%s",
            alert.extra.get("seq", 0),
            returned_key == public_key.hex(),
            returned_hash,
        )
        verified = _verified_snapshot_from_alert(
            alert,
            public_key,
            expected_info_hash,
            salt,
        )
        if verified is not None:
            return verified
        time.sleep(1)
    return None


def publish_to_dht(
    private_key_hex: str,
    info_hash_hex: str,
    piece_size: int = 32 * 1024 * 1024,
    state_path: str = STATE_FILE,
    dry_run: bool = False,
    salt: str = DEFAULT_SALT,
) -> dict:
    state = load_state(state_path)
    seq = state.get("last_seq", 0) + 1

    pub_key_bytes, _ = derive_nano_address(private_key_hex)
    target_id = compute_bep46_target_id(pub_key_bytes, salt)

    print(f"Producer signing public key (PRODUCER_SIGNING_PUBKEY): {pub_key_bytes.hex()}")
    print(f"DHT mutable-item target ID (SHA-1): {target_id.hex()[:16]}...")
    print(
        "Publishing snapshot: "
        f"publisher status sequence={seq}, torrent v2 info hash={info_hash_hex[:16]}..., "
        f"salt='{salt}'"
    )

    value_bytes = build_dht_value(info_hash_hex, piece_size)

    print(f"Value size: {len(value_bytes)} bytes")

    if dry_run:
        print("DRY RUN — not publishing to DHT")
        return {
            "seq": seq,
            "info_hash_hex": info_hash_hex,
            "dry_run": True,
        }

    secret_key_64 = _expanded_libtorrent_private_key(private_key_hex)
    dht_session = LibtorrentSession(
        data_dir=str(Path(state_path).resolve().parent),
        listen_port=6883,
        load_dht_state=False,
    )
    dht_session.start()
    try:
        print("Waiting for DHT to bootstrap...")
        if not dht_session.wait_for_dht_bootstrap(timeout=DHT_BOOTSTRAP_TIMEOUT):
            raise PublishError("DHT bootstrap did not complete")

        print("Publishing and verifying DHT mutable item...")
        dht_sequence: int | None = None
        acknowledgements: int | None = None
        for attempt in range(1, DHT_PUBLISH_ATTEMPTS + 1):
            dht_session.publish_dht_mutable_item(
                secret_key_64,
                pub_key_bytes,
                value_bytes,
                salt,
            )
            put_alert = dht_session.wait_for_dht_put(
                timeout=DHT_PUBLISH_TIMEOUT,
                salt=salt,
            )
            if put_alert is not None:
                dht_sequence = int(put_alert.extra.get("seq", 0))
                acknowledgements = put_alert.extra.get("num_success")
                print(
                    "DHT mutable-item put completed: "
                    f"sequence={dht_sequence}, direct acknowledgements={acknowledgements}"
                )
            else:
                print(f"DHT mutable-item put produced no completion alert (attempt {attempt})")

            verified = _wait_for_verified_snapshot(
                dht_session,
                pub_key_bytes,
                info_hash_hex,
                salt,
                timeout=DHT_VERIFY_TIMEOUT,
            )
            if verified is not None:
                verified_sequence, verified_hash = verified
                put_sequence = int(put_alert.extra.get("seq", 0)) if put_alert else 0
                if put_sequence and verified_sequence < put_sequence:
                    print(
                        "DHT read-back returned a stale sequence; "
                        "not accepting publication"
                    )
                else:
                    dht_sequence = verified_sequence
                    state["last_seq"] = seq
                    state["last_info_hash"] = info_hash_hex
                    state["last_dht_seq"] = dht_sequence
                    state["last_dht_info_hash"] = verified_hash
                    save_state(state, state_path)
                    print(
                        "DHT mutable item verified: "
                        f"sequence={dht_sequence}, torrent v2 info hash={verified_hash[:16]}..."
                    )
                    return {
                        "seq": seq,
                        "dht_seq": dht_sequence,
                        "info_hash_hex": info_hash_hex,
                        "confirmed": True,
                        "direct_acknowledgements": acknowledgements,
                    }
            if put_alert is not None:
                print(
                    "DHT put completed but read-back did not verify; "
                    "not republishing to preserve sequence monotonicity"
                )
                break
            if attempt < DHT_PUBLISH_ATTEMPTS:
                print(f"DHT read-back did not verify; retrying in {DHT_RETRY_DELAY}s")
                time.sleep(DHT_RETRY_DELAY)
        raise PublishError(
            "DHT publication was not verified by reading back the exact signed snapshot"
        )
    finally:
        dht_session.stop()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Publish snapshot info-hash to DHT via BEP 46")
    parser.add_argument("info_hash", help="BitTorrent v2 info-hash (hex)")
    parser.add_argument(
        "--private-key",
        default=None,
        help="Ed25519 private key (hex). Defaults to DHT_PRIVATE_KEY env.",
    )
    parser.add_argument(
        "--piece-size",
        type=int,
        default=32 * 1024 * 1024,
        help="Piece size in bytes (default: 32 MiB)",
    )
    parser.add_argument("--state-file", default=STATE_FILE, help="Path to state file")
    parser.add_argument(
        "--dry-run", action="store_true", help="Create payload but don't publish to DHT"
    )
    parser.add_argument(
        "--salt",
        default=os.environ.get("DHT_SALT", DEFAULT_SALT),
        help=f"DHT salt (env DHT_SALT, default: {DEFAULT_SALT})",
    )
    args = parser.parse_args()

    private_key = args.private_key or os.environ.get("DHT_PRIVATE_KEY")
    if not private_key:
        print("ERROR: DHT_PRIVATE_KEY not set (env or --private-key)", file=sys.stderr)
        sys.exit(1)

    try:
        result = publish_to_dht(
            private_key_hex=private_key,
            info_hash_hex=args.info_hash,
            piece_size=args.piece_size,
            state_path=args.state_file,
            dry_run=args.dry_run,
            salt=args.salt,
        )
        print(json.dumps(result, indent=2))
    except PublishError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
