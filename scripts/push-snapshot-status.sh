#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/nano-snapshots}"
STATUS_API_URL="${STATUS_API_URL:-}"  # Must be set, e.g. https://nano-snapshots.openrai.org

if [ -z "$STATUS_API_URL" ]; then
    echo "STATUS_API_URL not set, skipping push"
    exit 0
fi

META_FILE="${OUTPUT_DIR}/snapshot-meta.json"
STATE_FILE="${REPO_DIR}/publisher_state.json"
TORRENT_FILE="${OUTPUT_DIR}/nano-ledger-snapshot.7z.torrent"
SNAPSHOT_FILE="${OUTPUT_DIR}/nano-ledger-snapshot.7z"

# Read sequence and info_hash from state
cd "$REPO_DIR"
source .venv/bin/activate
if [ -z "${DHT_PRIVATE_KEY:-}" ] && [ -f "$HOME/.env" ]; then
    source "$HOME/.env"
fi

RESOLVED_WEB_SEED=$(python3 -c "import json; print(json.load(open('$META_FILE')).get('source_url', '${WEB_SEED_URL:-https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest/}'))" 2>/dev/null || echo "${WEB_SEED_URL:-https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest/}")

python -m producer.push_status \
    --status-api-url "$STATUS_API_URL" \
    --private-key "$DHT_PRIVATE_KEY" \
    --state-file "$STATE_FILE" \
    --meta-file "$META_FILE" \
    --torrent-file "$TORRENT_FILE" \
    --snapshot-file "$SNAPSHOT_FILE" \
    --web-seed-url "$RESOLVED_WEB_SEED"
