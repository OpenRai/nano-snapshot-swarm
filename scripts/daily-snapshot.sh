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
RUNNING_PID=$(pgrep -f "rclone copyurl.*daily-snapshot" 2>/dev/null | head -1 || true)
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

for PID in $(pgrep -f "rclone.*$WORK_DIR" 2>/dev/null || true); do
    log "Killing orphaned rclone PID $PID"
    kill "$PID" 2>/dev/null || true
done
sleep 1

# --- Step 1: Resolve latest snapshot URL via HTTP Location header ---
log "Resolving latest snapshot URL"

LATEST_URL=$(curl -sSL -A "$AGENT" -I "$WEB_SEED_URL" 2>/dev/null | grep -i "^Location:" | tr -d "" | cut -d" " -f2 | tr -d '"')
if [ -z "$LATEST_URL" ]; then
    log "ERROR: Could not resolve latest snapshot URL from $WEB_SEED_URL"
    exit 1
fi

FILENAME=$(basename "$LATEST_URL")
TARGET_FILE="${WORK_DIR}/${FILENAME}"

# --- Step 2: Decide whether to resume or start fresh ---
if [ -f "$TARGET_FILE" ] && [ -s "$TARGET_FILE" ]; then
    EXISTING_FILE_IN_DIR=$(ls "$WORK_DIR"/*.7z 2>/dev/null | head -1 || true)
    if [ -n "$EXISTING_FILE_IN_DIR" ] && [ "$(basename "$EXISTING_FILE_IN_DIR")" = "$FILENAME" ]; then
        log "Found existing partial download: $EXISTING_FILE_IN_DIR"
        log "Attempting to resume: $LATEST_URL -> $TARGET_FILE"
    else
        log "Filename mismatch or stale file in $WORK_DIR — removing all tmp files"
        rm -rf "${WORK_DIR:?}"/*
        mkdir -p "$WORK_DIR"
        log "Starting fresh download: $LATEST_URL"
    fi
else
    if [ -d "$WORK_DIR" ] && [ "$(ls -A "$WORK_DIR" 2>/dev/null)" ]; then
        log "No matching file found in $WORK_DIR — clearing tmp"
        rm -rf "${WORK_DIR:?}"/*
        mkdir -p "$WORK_DIR"
    fi
    log "Starting new download: $LATEST_URL"
fi

# --- Step 3: Download with rclone (resumable) ---
rclone copyurl --progress "$LATEST_URL" "$TARGET_FILE"

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
SAVINGS=$(($(expr $EXTRACTED_SIZE - $COMPACTED_SIZE) \* 100 / $EXTRACTED_SIZE))
log "Compacted: ${COMPACTED_SIZE} bytes (saved ${SAVINGS}%)"

# --- Step 6: Compress with zstd --rsyncable ---
COMPRESSED_OUTPUT="${OUTPUT_DIR}/nano-daily.ldb.zst"
log "Compressing with zstd -3 --rsyncable"
zstd -3 --rsyncable -f "$COMPACTED_FILE" -o "$COMPRESSED_OUTPUT"

COMP_SIZE=$(stat -c%s "$COMPRESSED_OUTPUT")
SHA256=$(sha256sum "$COMPRESSED_OUTPUT" | cut -d" " -f1)
log "Compressed to ${COMPRESSED_OUTPUT} (${COMP_SIZE} bytes, sha256=${SHA256})"

# --- Step 7: Create torrent and publish ---
log "Creating torrent and publishing to DHT"

cd /opt/nano-bootstrap-swarm
source .venv/bin/activate
source /home/openrai/.env

python -m producer.cli publish     --private-key "$DHT_PRIVATE_KEY"     --output-dir "$OUTPUT_DIR"     --web-seed-url "$WEB_SEED_URL"

log "=== Daily snapshot pipeline complete ==="
