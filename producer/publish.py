from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import libtorrent as lt

from shared.bep46 import build_dht_value, sign_mutable_item
from shared.nano_identity import compute_bep46_target_id, derive_nano_address

STATE_FILE = "publisher_state.json"
DHT_SALT = "daily"
DHT_PUBLISH_TIMEOUT = 120


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


def publish_to_dht(
    private_key_hex: str,
    info_hash_hex: str,
    piece_size: int = 32 * 1024 * 1024,
    state_path: str = STATE_FILE,
    dry_run: bool = False,
) -> dict:
    state = load_state(state_path)
    seq = state.get("last_seq", 0) + 1

    pub_key_bytes, nano_address = derive_nano_address(private_key_hex)
    target_id = compute_bep46_target_id(pub_key_bytes, DHT_SALT)

    print(f"Publisher identity: {nano_address}")
    print(f"DHT target ID (SHA-1): {target_id.hex()}")
    print(f"Publishing seq={seq}, info_hash={info_hash_hex}")

    value_bytes = build_dht_value(info_hash_hex, piece_size)
    signature, derived_pub = sign_mutable_item(private_key_hex, value_bytes, seq, salt=DHT_SALT)

    if derived_pub != pub_key_bytes:
        raise PublishError("Derived public key mismatch")

    print(f"Signature: {signature.hex()}")
    print(f"Value size: {len(value_bytes)} bytes")

    if dry_run:
        print("DRY RUN — not publishing to DHT")
        return {
            "seq": seq,
            "info_hash": info_hash_hex,
            "signature": signature.hex(),
            "nano_address": nano_address,
            "dry_run": True,
        }

    settings = {
        "enable_dht": True,
        "listen_interfaces": "0.0.0.0:6881",
        "alert_mask": lt.alert.category_t.all_categories,
    }
    ses = lt.session(settings)

    bootstrap_nodes = [
        ("router.bittorrent.com", 6881),
        ("router.utorrent.com", 6881),
        ("dht.transmissionbt.com", 6881),
    ]
    for host, port in bootstrap_nodes:
        ses.add_dht_node((host, port))

    print("Waiting for DHT to bootstrap...")
    time.sleep(30)

    pubkey_list = [b for b in pub_key_bytes]

    def put_callback(_entry, sign, _new_seq, _new_salt):
        sign[:] = signature

    ses.dht_put_item(pubkey_list, put_callback, DHT_SALT.encode("utf-8"))

    print("Waiting for DHT put confirmation...")
    deadline = time.time() + DHT_PUBLISH_TIMEOUT
    confirmed = False

    while time.time() < deadline:
        alerts = ses.pop_alerts()
        for alert in alerts:
            if isinstance(alert, lt.dht_put_alert):
                print(f"DHT put confirmed: {alert}")
                confirmed = True
                break
        if confirmed:
            break
        time.sleep(1)

    if not confirmed:
        print("WARNING: DHT put not confirmed within timeout (may still have propagated)")

    state["last_seq"] = seq
    state["last_info_hash"] = info_hash_hex
    save_state(state, state_path)

    print(f"Published seq={seq} to DHT")
    return {
        "seq": seq,
        "info_hash": info_hash_hex,
        "signature": signature.hex(),
        "nano_address": nano_address,
        "confirmed": confirmed,
    }


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
        )
        print(json.dumps(result, indent=2))
    except PublishError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
