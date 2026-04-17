from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))  # noqa: E402

from producer.publish import publish_to_dht  # noqa: E402
from producer.torrent_create import create_torrent  # noqa: E402


def cmd_snapshot(args: argparse.Namespace) -> None:
    env = os.environ.copy()
    if args.ledger_path:
        env["NANO_LEDGER_PATH"] = args.ledger_path
    if args.output_dir:
        env["OUTPUT_DIR"] = args.output_dir

    snapshot_script = SCRIPT_DIR / "snapshot.sh"
    result = subprocess.run(
        ["bash", str(snapshot_script)],
        env=env,
    )
    if result.returncode != 0:
        print(f"ERROR: snapshot.sh exited with code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def cmd_publish(args: argparse.Namespace) -> None:
    private_key = args.private_key or os.environ.get("DHT_PRIVATE_KEY")
    if not private_key:
        print("ERROR: DHT_PRIVATE_KEY not set (env or --private-key)", file=sys.stderr)
        sys.exit(1)

    web_seed_url = args.web_seed_url or os.environ.get("WEB_SEED_URL", "")
    output_dir = args.output_dir or os.environ.get("OUTPUT_DIR", ".")
    snapshot_file = os.path.join(output_dir, "nano-daily.ldb.zst")

    if not os.path.exists(snapshot_file):
        print(f"ERROR: Snapshot file not found: {snapshot_file}", file=sys.stderr)
        sys.exit(1)

    print(f"Creating torrent for: {snapshot_file}")
    torrent_path, info_hash = create_torrent(
        filepath=snapshot_file,
        web_seed_url=web_seed_url or None,
        piece_size=args.piece_size,
        output_path=snapshot_file + ".torrent",
    )
    print(f"Torrent created: {torrent_path}")
    print(f"Info-hash (v2): {info_hash}")

    result = publish_to_dht(
        private_key_hex=private_key,
        info_hash_hex=info_hash,
        piece_size=args.piece_size,
        state_path=args.state_file,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


def cmd_full(args: argparse.Namespace) -> None:
    cmd_snapshot(args)
    cmd_publish(args)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nano P2P Snapshot Producer — extract, compress, and publish ledger snapshots"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common_args = argparse.ArgumentParser(add_help=False)
    common_args.add_argument(
        "--ledger-path", default=None, help="Path to data.ldb (overrides NANO_LEDGER_PATH env)"
    )
    common_args.add_argument(
        "--output-dir", default=None, help="Output directory (overrides OUTPUT_DIR env)"
    )
    common_args.add_argument(
        "--private-key",
        default=None,
        help="Ed25519 private key hex (overrides DHT_PRIVATE_KEY env)",
    )
    common_args.add_argument(
        "--web-seed-url", default=None, help="Web seed URL (overrides WEB_SEED_URL env)"
    )
    common_args.add_argument(
        "--piece-size",
        type=int,
        default=32 * 1024 * 1024,
        help="Torrent piece size in bytes (default: 32 MiB)",
    )
    common_args.add_argument(
        "--state-file", default="publisher_state.json", help="Path to publisher state file"
    )
    common_args.add_argument("--dry-run", action="store_true", help="Don't publish to DHT")

    snap_parser = subparsers.add_parser(
        "snapshot", parents=[common_args], help="Extract and compress ledger"
    )
    snap_parser.set_defaults(func=cmd_snapshot)

    pub_parser = subparsers.add_parser(
        "publish", parents=[common_args], help="Create torrent and publish to DHT"
    )
    pub_parser.set_defaults(func=cmd_publish)

    full_parser = subparsers.add_parser(
        "full", parents=[common_args], help="Run full pipeline: snapshot then publish"
    )
    full_parser.set_defaults(func=cmd_full)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
