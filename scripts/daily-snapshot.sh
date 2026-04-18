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
RUNNING_PID=$(pgrep -f "curl.*nano-snapshot" 2>/dev/null | head -1 || true)
if [ -n "$RUNNING_PID" ]; then
    RUNTIME_SECS=$(ps -o etimes= -p "$RUNNING_PID" 2>/dev/null | tr -d " " || echo "0")
    RUNTIME_HOURS=$((RUNTIME_SECS / 3600))
    if [ "$RUNTIME_HOURS" -lt "$MAX_RUNTIME_HOURS" ]; then
        log "A curl instance is already running (PID $RUNNING_PID, ${RUNTIME_HOURS}h < ${MAX_RUNTIME_HOURS}h) — exiting"
        exit 0
    else
        log "Stale curl instance running for ${RUNTIME_HOURS}h — killing PID $RUNNING_PID"
        kill "$RUNNING_PID" 2>/dev/null || true
        sleep 2
    fi
fi

# Clean up any stale curl processes
for PID in $(pgrep -f "curl.*nano-snapshot" 2>/dev/null || true); do
    log "Killing orphaned curl PID $PID"
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

# Use .partial file for download, then atomically rename on success
PARTIAL_FILE="${TARGET_FILE}.partial"

# Check for existing partial download
if [ -f "$PARTIAL_FILE" ] && [ -s "$PARTIAL_FILE" ]; then
    CURRENT_SIZE=$(stat -c%s "$PARTIAL_FILE")
    log "Found partial download: $PARTIAL_FILE (${CURRENT_SIZE} bytes so far)"
else
    log "Starting new download: $LATEST_URL (expected ~60GB)"
fi

# --- Step 3: Download with curl (resumable) ---
log "Starting download: $LATEST_URL (expected ~60GB)"

# If partial file exists, use curl with Range header for proper resume
if [ -f "$PARTIAL_FILE" ] && [ -s "$PARTIAL_FILE" ]; then
    CURRENT_SIZE=$(stat -c%s "$PARTIAL_FILE")
    log "Resuming from byte $CURRENT_SIZE using curl"
    curl -# -A "$AGENT" -H "Range: bytes=$CURRENT_SIZE-" -o "$PARTIAL_FILE" "$LATEST_URL" &
    CURL_PID=$!
else
    log "Starting fresh download"
    curl -# -A "$AGENT" -o "$PARTIAL_FILE" "$LATEST_URL" &
    CURL_PID=$!
fi

# Background progress logger - polls file size every 20 seconds
(
    LAST_SIZE=$(stat -c%s "$PARTIAL_FILE" 2>/dev/null || echo 0)
    START_TIME=$(date +%s)
    while kill -0 $CURL_PID 2>/dev/null; do
        sleep 20
        CURRENT_SIZE=$(stat -c%s "$PARTIAL_FILE" 2>/dev/null || echo 0)
        ELAPSED=$(($(date +%s) - START_TIME))
        log "Progress: $(numfmt --to=iec-i --suffix=B $CURRENT_SIZE) downloaded, ${ELAPSED}s elapsed"
    done
) &
LOGGER_PID=$!

# Wait for curl to complete
wait $CURL_PID

# Kill the logger
kill $LOGGER_PID 2>/dev/null || true

# Verify download completed
if [ ! -f "$PARTIAL_FILE" ] || [ $(stat -c%s "$PARTIAL_FILE") -lt 1000000 ]; then
    log "ERROR: Download failed or file too small"
    exit 1
fi

# Atomically rename .partial to final filename
log "Renaming to final filename"
mv "$PARTIAL_FILE" "$TARGET_FILE"

ORIG_SIZE=$(stat -c%s "$TARGET_FILE")
log "Downloaded ${FILENAME} (${ORIG_SIZE} bytes)"

# --- Step 4: Extract ---
# Use single-threaded extraction to minimize memory usage on limited systems
log "Extracting ${FILENAME}"
7z x -mmt=1 -y -o"$WORK_DIR" "$TARGET_FILE" > /dev/null

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

# Remove any stale compacted files first
rm -f "${COMPACTED_DIR}/data.ldb"

if ! mdb_copy "$EXTRACTED_FILE" "$COMPACTED_DIR" 2>&1; then
    log "ERROR: mdb_copy failed - $EXTRACTED_FILE may be corrupted"
    exit 1
fi

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
