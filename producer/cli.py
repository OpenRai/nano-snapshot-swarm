from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))  # noqa: E402

from producer.publish import DEFAULT_SALT, publish_to_dht  # noqa: E402
from producer.torrent_create import create_torrent  # noqa: E402


def cmd_publish(args: argparse.Namespace) -> None:
    private_key = args.private_key or os.environ.get("DHT_PRIVATE_KEY")
    if not private_key:
        print("ERROR: DHT_PRIVATE_KEY not set (env or --private-key)", file=sys.stderr)
        sys.exit(1)

    web_seed_url = args.web_seed_url or os.environ.get("WEB_SEED_URL", "")

    # Resolve snapshot file path
    snapshot_file = args.snapshot_file
    if not snapshot_file:
        output_dir = args.output_dir or os.environ.get("OUTPUT_DIR", ".")
        candidate = os.path.join(output_dir, "nano-ledger-snapshot.7z")
        if os.path.exists(candidate):
            snapshot_file = candidate
        else:
            print(
                f"ERROR: No --snapshot-file given and {candidate} not found",
                file=sys.stderr,
            )
            sys.exit(1)

    if not os.path.exists(snapshot_file):
        print(f"ERROR: Snapshot file not found: {snapshot_file}", file=sys.stderr)
        sys.exit(1)

    print(f"Creating torrent for: {snapshot_file}")

    # Build torrent comment with snapshot metadata
    comment_data = {
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if args.source_url:
        comment_data["source_url"] = args.source_url
    if args.original_filename:
        comment_data["original_filename"] = args.original_filename
    comment = json.dumps(comment_data, separators=(",", ":"))

    torrent_path, info_hash = create_torrent(
        filepath=snapshot_file,
        web_seed_url=web_seed_url or None,
        piece_size=args.piece_size,
        output_path=snapshot_file + ".torrent",
        comment=comment,
    )
    print(f"Torrent created: {torrent_path}")
    print(f"Info-hash (v2): {info_hash}")

    result = publish_to_dht(
        private_key_hex=private_key,
        info_hash_hex=info_hash,
        piece_size=args.piece_size,
        state_path=args.state_file,
        dry_run=args.dry_run,
        salt=args.salt,
    )
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nano P2P Snapshot Producer — create torrent and publish to DHT"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pub_parser = subparsers.add_parser("publish", help="Create torrent and publish to DHT")
    pub_parser.add_argument(
        "--snapshot-file",
        default=None,
        help="Path to snapshot file (e.g. nano-ledger-snapshot.7z). "
        "Falls back to OUTPUT_DIR/nano-ledger-snapshot.7z",
    )
    pub_parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for auto-detecting snapshot file (overrides OUTPUT_DIR env)",
    )
    pub_parser.add_argument(
        "--private-key",
        default=None,
        help="Ed25519 private key hex (overrides DHT_PRIVATE_KEY env)",
    )
    pub_parser.add_argument(
        "--web-seed-url",
        default=None,
        help="Web seed URL (overrides WEB_SEED_URL env)",
    )
    pub_parser.add_argument(
        "--piece-size",
        type=int,
        default=32 * 1024 * 1024,
        help="Torrent piece size in bytes (default: 32 MiB)",
    )
    pub_parser.add_argument(
        "--source-url",
        default=None,
        help="Resolved source URL of the snapshot (embedded in torrent comment)",
    )
    pub_parser.add_argument(
        "--original-filename",
        default=None,
        help="Original snapshot filename (embedded in torrent comment)",
    )
    pub_parser.add_argument(
        "--state-file",
        default="publisher_state.json",
        help="Path to publisher state file",
    )
    pub_parser.add_argument("--dry-run", action="store_true", help="Don't publish to DHT")
    pub_parser.add_argument(
        "--salt",
        default=os.environ.get("DHT_SALT", DEFAULT_SALT),
        help=f"DHT salt (env DHT_SALT, default: {DEFAULT_SALT})",
    )
    pub_parser.set_defaults(func=cmd_publish)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
