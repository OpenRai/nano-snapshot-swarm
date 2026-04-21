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
from producer.validation_fixture import (  # noqa: E402
    DEFAULT_VALIDATION_ARCHIVE_NAME,
    DEFAULT_VALIDATION_SALT,
    create_validation_fixture,
    parse_size_bytes,
)


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

    # Build snapshot metadata for the info dict (survives magnet exchange)
    # Only include stable fields — timestamps go in the outer comment
    snapshot_meta = {}
    if args.source_url:
        snapshot_meta["source_url"] = args.source_url
    if args.original_filename:
        snapshot_meta["original_filename"] = args.original_filename
    snapshot_meta_json = json.dumps(snapshot_meta, separators=(",", ":")) if snapshot_meta else None

    # Outer comment with timestamp (only available from .torrent file, not magnet)
    comment = json.dumps(
        {"created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
        separators=(",", ":"),
    )

    torrent_path, info_hash = create_torrent(
        filepath=snapshot_file,
        web_seed_url=web_seed_url or None,
        piece_size=args.piece_size,
        output_path=snapshot_file + ".torrent",
        comment=comment,
        snapshot_meta=snapshot_meta_json,
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


def cmd_validation_fixture_create(args: argparse.Namespace) -> None:
    output_dir = args.output_dir or os.environ.get("OUTPUT_DIR", ".")
    result = create_validation_fixture(
        output_dir=output_dir,
        archive_name=args.archive_name,
        size_bytes=parse_size_bytes(args.size),
        force=args.force,
        keep_source=args.keep_source,
    )
    print(json.dumps(result, indent=2))


def cmd_validation_fixture_publish(args: argparse.Namespace) -> None:
    output_dir = args.output_dir or os.environ.get("OUTPUT_DIR", ".")
    archive_path = os.path.join(output_dir, args.archive_name)
    publish_args = argparse.Namespace(
        private_key=args.private_key,
        web_seed_url=args.web_seed_url,
        snapshot_file=archive_path,
        output_dir=output_dir,
        source_url=args.source_url,
        original_filename=args.archive_name,
        piece_size=args.piece_size,
        state_file=args.state_file,
        dry_run=args.dry_run,
        salt=args.salt,
    )
    cmd_publish(publish_args)


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

    validation_parser = subparsers.add_parser(
        "validation-fixture",
        help="Create or publish a small synthetic validation artifact",
    )
    validation_subparsers = validation_parser.add_subparsers(
        dest="validation_command",
        required=True,
    )

    create_parser = validation_subparsers.add_parser(
        "create",
        help="Create a random validation artifact and 7z archive",
    )
    create_parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for validation fixture files (overrides OUTPUT_DIR env)",
    )
    create_parser.add_argument(
        "--archive-name",
        default=DEFAULT_VALIDATION_ARCHIVE_NAME,
        help="Validation archive filename",
    )
    create_parser.add_argument(
        "--size",
        default="1g",
        help="Uncompressed random source size, e.g. 512m or 1g",
    )
    create_parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    create_parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Keep the uncompressed random source file after 7z creation",
    )
    create_parser.set_defaults(func=cmd_validation_fixture_create)

    publish_validation_parser = validation_subparsers.add_parser(
        "publish",
        help="Publish the validation archive to DHT",
    )
    publish_validation_parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory containing the validation archive (overrides OUTPUT_DIR env)",
    )
    publish_validation_parser.add_argument(
        "--archive-name",
        default=DEFAULT_VALIDATION_ARCHIVE_NAME,
        help="Validation archive filename",
    )
    publish_validation_parser.add_argument(
        "--private-key",
        default=None,
        help="Ed25519 private key hex (overrides DHT_PRIVATE_KEY env)",
    )
    publish_validation_parser.add_argument(
        "--web-seed-url",
        default=None,
        help="Optional validation web seed URL (overrides WEB_SEED_URL env)",
    )
    publish_validation_parser.add_argument(
        "--source-url",
        default=None,
        help="Resolved source URL to record in torrent metadata",
    )
    publish_validation_parser.add_argument(
        "--piece-size",
        type=int,
        default=32 * 1024 * 1024,
        help="Torrent piece size in bytes (default: 32 MiB)",
    )
    publish_validation_parser.add_argument(
        "--state-file",
        default="publisher_state.validation.json",
        help="Path to validation publisher state file",
    )
    publish_validation_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create torrent and payload but don't publish to DHT",
    )
    publish_validation_parser.add_argument(
        "--salt",
        default=os.environ.get("VALIDATION_DHT_SALT", DEFAULT_VALIDATION_SALT),
        help=f"Validation DHT salt (env VALIDATION_DHT_SALT, default: {DEFAULT_VALIDATION_SALT})",
    )
    publish_validation_parser.set_defaults(func=cmd_validation_fixture_publish)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
