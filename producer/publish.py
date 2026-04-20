from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import libtorrent as lt

from shared.bep46 import build_dht_value
from shared.nano_identity import compute_bep46_target_id, derive_nano_address

STATE_FILE = "publisher_state.json"
DEFAULT_SALT = "daily"
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
    salt: str = DEFAULT_SALT,
) -> dict:
    state = load_state(state_path)
    seq = state.get("last_seq", 0) + 1

    pub_key_bytes, nano_address = derive_nano_address(private_key_hex)
    target_id = compute_bep46_target_id(pub_key_bytes, salt)

    print(f"Publisher identity: {nano_address}")
    print(f"DHT target ID (SHA-1): {target_id.hex()}")
    print(f"Publishing seq={seq}, info_hash={info_hash_hex}, salt='{salt}'")

    value_bytes = build_dht_value(info_hash_hex, piece_size)

    print(f"Value size: {len(value_bytes)} bytes")

    if dry_run:
        print("DRY RUN — not publishing to DHT")
        return {
            "seq": seq,
            "info_hash_hex": info_hash_hex,
            "nano_address": nano_address,
            "dry_run": True,
        }

    # Build 64-byte ed25519 secret key (seed + pubkey) for libtorrent
    import nacl.signing

    sk = nacl.signing.SigningKey(bytes.fromhex(private_key_hex))
    secret_key_64 = bytes(sk._signing_key)  # 64 bytes: seed || pubkey

    settings = {
        "enable_dht": True,
        "listen_interfaces": "0.0.0.0:6883",
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

    # dht_put_mutable_item handles seq and signing internally.
    # IMPORTANT: pass bytes, not str. Python str→C++ std::string uses UTF-8,
    # which corrupts binary data (bytes >0x7F become multi-byte sequences).
    ses.dht_put_mutable_item(
        secret_key_64,
        pub_key_bytes if isinstance(pub_key_bytes, bytes) else pub_key_bytes.encode("latin-1"),
        value_bytes if isinstance(value_bytes, bytes) else value_bytes.encode("latin-1"),
        salt.encode("utf-8") if isinstance(salt, str) else salt,
    )

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
        "info_hash_hex": info_hash_hex,
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
