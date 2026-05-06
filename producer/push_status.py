from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from nacl.signing import SigningKey


def _parse_private_key(hex_key: str) -> bytes:
    key_bytes = bytes.fromhex(hex_key)
    if len(key_bytes) == 64:
        return key_bytes[:32]
    if len(key_bytes) == 32:
        return key_bytes
    raise ValueError(f"Private key must be 32 or 64 bytes, got {len(key_bytes)}")


def sign_push(private_key_hex: str, sequence: int, info_hash: str, timestamp: str) -> str:
    seed = _parse_private_key(private_key_hex)
    signing_key = SigningKey(seed)
    message = f"{sequence}:{info_hash}:{timestamp}".encode("ascii")
    signed = signing_key.sign(message)
    return signed.signature.hex()


def get_archive_listing(snapshot_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["7z", "l", snapshot_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        lines = result.stdout.splitlines()
        # Find the separator line (starts with --)
        for i, line in enumerate(lines):
            if line.startswith("--"):
                return "\n".join(lines[i:])
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def push_status(
    status_api_url: str,
    private_key_hex: str,
    sequence: int,
    info_hash: str,
    torrent_name: str,
    web_seed_url: str,
    piece_size: int,
    snapshot_file: str,
    torrent_file: str,
) -> dict:
    torrent_bytes = Path(torrent_file).read_bytes()
    torrent_b64 = base64.b64encode(torrent_bytes).decode("ascii")

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    signature = sign_push(private_key_hex, sequence, info_hash, timestamp)

    # Construct full web seed URL (follow BEP 19 logic: append if ends in slash)
    full_web_seed = web_seed_url
    if full_web_seed.endswith("/"):
        full_web_seed += torrent_name

    payload = {
        "sequence": sequence,
        "info_hash": info_hash,
        "torrent_name": torrent_name,
        "web_seed_url": full_web_seed,
        "piece_size": piece_size,
        "snapshot_size_bytes": Path(snapshot_file).stat().st_size,
        "timestamp": timestamp,
        "torrent_file_b64": torrent_b64,
        "signature": signature,
    }

    listing = get_archive_listing(snapshot_file)
    if listing:
        payload["archive_listing"] = listing

    req = urllib.request.Request(
        f"{status_api_url.rstrip('/')}/api/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Push snapshot status to the status API")
    parser.add_argument("--status-api-url", required=True)
    parser.add_argument("--private-key", default=None)
    parser.add_argument("--state-file", default="publisher_state.json")
    parser.add_argument("--meta-file", default="snapshot-meta.json")
    parser.add_argument("--torrent-file", required=True)
    parser.add_argument("--snapshot-file", required=True)
    parser.add_argument(
        "--web-seed-url",
        default="https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest",
    )
    parser.add_argument("--torrent-name", default="nano-ledger-snapshot.7z")
    parser.add_argument("--piece-size", type=int, default=32 * 1024 * 1024)
    args = parser.parse_args()

    private_key = args.private_key or os.environ.get("DHT_PRIVATE_KEY")
    if not private_key:
        print("ERROR: DHT_PRIVATE_KEY not set (env or --private-key)", file=sys.stderr)
        return 1

    # Read publisher state for sequence and info_hash
    state_path = Path(args.state_file)
    if state_path.exists():
        state = json.loads(state_path.read_text())
        sequence = state.get("last_seq", 0)
        info_hash = state.get("last_info_hash", "")
    else:
        print(f"ERROR: State file not found: {args.state_file}", file=sys.stderr)
        return 1

    if not info_hash:
        print("ERROR: No info hash in state file", file=sys.stderr)
        return 1

    try:
        result = push_status(
            status_api_url=args.status_api_url,
            private_key_hex=private_key,
            sequence=sequence,
            info_hash=info_hash,
            torrent_name=args.torrent_name,
            web_seed_url=args.web_seed_url,
            piece_size=args.piece_size,
            snapshot_file=args.snapshot_file,
            torrent_file=args.torrent_file,
        )
        print(json.dumps(result, indent=2))
        return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
