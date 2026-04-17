#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="${OUTPUT_DIR:-/opt/nano-snapshots}"
WEB_SEED_URL="${WEB_SEED_URL:-https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest}"
AGENT="nano-bootstrap-swarm/1.0"
MAX_RUNTIME_HOURS=12

LOG_FILE="${LOG_FILE:-${OUTPUT_DIR}/nano-snapshot.log}"

log() {
    echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE"
}

WORK_DIR="${OUTPUT_DIR}/tmp"
mkdir -p "$WORK_DIR"

# --- Guard: prevent concurrent runs and stale downloads ---
RUNNING_PID=$(pgrep -f "rclone copyurl" 2>/dev/null | head -1 || true)
if [ -n "$RUNNING_PID" ]; then
    RUNTIME_SECS=$(ps -o etimes= -p "$RUNNING_PID" 2>/dev/null | tr -d " " || echo "0")
    RUNTIME_HOURS=$((RUNTIME_SECS / 3600))
    if [ "$RUNTIME_HOURS" -lt "$MAX_RUNTIME_HOURS" ]; then
        log "An rclone instance is already running (PID $RUNNING_PID, ${RUNTIME_HOURS}h < ${MAX_RUNTIME_HOURS}h) — exiting"
        exit 0
    else
        log "Stale rclone instance running for ${RUNTIME_HOURS}h — killing PID $RUNNING_PID"
        kill "$RUNNING_PID" 2>/dev/null || true
        sleep 2
    fi
fi

# Clean up any stale rclone processes
for PID in $(pgrep -f "rclone" 2>/dev/null || true); do
    log "Killing orphaned rclone PID $PID"
    kill "$PID" 2>/dev/null || true
done
sleep 1

# --- Step 1: Resolve latest snapshot URL from S3 listing ---
log "Resolving latest snapshot URL"

RAW_URL=$(curl -sSL -A "$AGENT" "$WEB_SEED_URL" | tr -d '"\r')
if [ -z "$RAW_URL" ]; then
    log "ERROR: Could not resolve latest snapshot URL from $WEB_SEED_URL"
    exit 1
fi

# S3 listing returns a full URL — extract just the filename
FILENAME=$(basename "$RAW_URL")
LATEST_URL="$RAW_URL"

# If the listing returned a relative path, construct full URL
if [[ "$LATEST_URL" != http* ]]; then
    LATEST_URL="https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/${FILENAME}"
fi

log "Resolved: ${FILENAME}"
TARGET_FILE="${WORK_DIR}/${FILENAME}"

# --- Step 2: Decide whether to resume or start fresh ---
# Always clean up any stale files to ensure a fresh start
for STALE_FILE in "$WORK_DIR"/*.7z; do
    [ -f "$STALE_FILE" ] || continue
    if [ "$(basename "$STALE_FILE")" != "$FILENAME" ]; then
        log "Removing stale file from different snapshot: $STALE_FILE"
        rm -f "$STALE_FILE"
    fi
done

# Clean up previous extraction/compaction artifacts
rm -rf "${WORK_DIR:?}/compacted"
rm -f "${WORK_DIR}/data.ldb"

# Check for existing download
if [ -f "$TARGET_FILE" ] && [ -s "$TARGET_FILE" ]; then
    log "Found existing download: $TARGET_FILE"
else
    log "Starting new download: $LATEST_URL"
fi

# --- Step 3: Download with rclone (resumable) ---
log "Downloading with rclone"
# Use rclone with periodic logging (every 20s) instead of continuous progress
# Redirect stderr to stdout to capture in log
rclone copyurl --stats 20s --stats-one-line --no-check-certificate --s3-acl public-read "$LATEST_URL" "$TARGET_FILE" 2>&1 | tee -a "$LOG_FILE"

if [ ! -f "$TARGET_FILE" ]; then
    log "ERROR: Download failed — file not found"
    exit 1
fi

ORIG_SIZE=$(stat -c%s "$TARGET_FILE")
log "Downloaded ${FILENAME} (${ORIG_SIZE} bytes)"

# --- Step 4: Extract ---
log "Extracting ${FILENAME}"
7z x -y -o"$WORK_DIR" "$TARGET_FILE" > /dev/null

EXTRACTED_FILE="${WORK_DIR}/data.ldb"
if [ ! -f "$EXTRACTED_FILE" ]; then
    log "ERROR: Extraction failed — data.ldb not found"
    exit 1
fi

EXTRACTED_SIZE=$(stat -c%s "$EXTRACTED_FILE")
log "Extracted data.ldb (${EXTRACTED_SIZE} bytes)"

# --- Step 5: Compact with mdb_copy ---
log "Compacting with mdb_copy"
COMPACTED_DIR="${WORK_DIR}/compacted"
mkdir -p "$COMPACTED_DIR"
mdb_copy "$EXTRACTED_FILE" "$COMPACTED_DIR"

COMPACTED_FILE="${COMPACTED_DIR}/data.ldb"
if [ ! -f "$COMPACTED_FILE" ]; then
    log "ERROR: mdb_copy failed — compacted file not found"
    exit 1
fi

COMPACTED_SIZE=$(stat -c%s "$COMPACTED_FILE")
SAVINGS=$(( (EXTRACTED_SIZE - COMPACTED_SIZE) * 100 / EXTRACTED_SIZE ))
log "Compacted: ${COMPACTED_SIZE} bytes (saved ${SAVINGS}%)"

# --- Step 6: Compress with zstd --rsyncable ---
COMPRESSED_OUTPUT="${OUTPUT_DIR}/nano-daily.ldb.zst"
log "Compressing with zstd -3 --rsyncable"
zstd -3 --rsyncable -f "$COMPACTED_FILE" -o "$COMPRESSED_OUTPUT"

COMP_SIZE=$(stat -c%s "$COMPRESSED_OUTPUT")
SHA256=$(sha256sum "$COMPRESSED_OUTPUT" | cut -d' ' -f1)
log "Compressed to ${COMPRESSED_OUTPUT} (${COMP_SIZE} bytes, sha256=${SHA256})"

# --- Step 7: Create torrent and publish ---
log "Creating torrent and publishing to DHT"

cd /opt/nano-bootstrap-swarm
source .venv/bin/activate
if [ -z "$DHT_PRIVATE_KEY" ] && [ -f /home/openrai/.env ]; then
    source /home/openrai/.env
fi

python -m producer.cli publish \
    --private-key "$DHT_PRIVATE_KEY" \
    --output-dir "$OUTPUT_DIR" \
    --web-seed-url "$WEB_SEED_URL"

log "=== Daily snapshot pipeline complete ==="
