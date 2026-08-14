#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/scripts/runtime-config.sh"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/nano-snapshots}"
UPSTREAM_SNAPSHOT_INDEX_URL="${UPSTREAM_SNAPSHOT_INDEX_URL:-https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest}"
TORRENT_FORMAT_VERSION=3
SNAPSHOT_RETENTION_COUNT="${SNAPSHOT_RETENTION_COUNT:-0}"
if ! USE_PLACEHOLDER_SNAPSHOT="$(parse_boolean_env USE_PLACEHOLDER_SNAPSHOT false)"; then
    exit 1
fi
AGENT="nano-snapshot-swarm/1.0"

log() {
    echo "[$(date -Iseconds)] $*"
}

WORK_DIR="${OUTPUT_DIR}/tmp"
mkdir -p "$WORK_DIR"

if ! [[ "$SNAPSHOT_RETENTION_COUNT" =~ ^[0-9]+$ ]]; then
    log "ERROR: SNAPSHOT_RETENTION_COUNT must be a non-negative integer"
    exit 1
fi

# --- Lockfile: prevent concurrent script instances (Bug 7 fix) ---
LOCKFILE="${OUTPUT_DIR}/.snapshot.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    log "Another instance is already running — exiting"
    exit 0
fi

wait_for_authoritative_seeder() {
    local expected_hash="$1"
    if ! systemctl --user is-enabled nano-seed.service &>/dev/null; then
        log "ERROR: nano-seed.service must be enabled for authoritative publication"
        return 1
    fi

    if systemctl --user is-active nano-seed.service &>/dev/null; then
        log "Requesting nano-seed.service reload to load the updated torrent"
        systemctl --user reload nano-seed.service
    else
        log "Starting nano-seed.service to seed updated snapshot"
        systemctl --user start nano-seed.service
    fi

    local stats_file="${OUTPUT_DIR}/seeder-stats.json"
    local state_file="${REPO_DIR}/publisher_state.json"
    # A cold seeder may use its full 120s bootstrap wait, 60s DHT put wait,
    # and 120s signed read-back wait. Leave a small margin for reload handling.
    for _ in $(seq 1 360); do
        if [ -f "$stats_file" ] && python3 - "$stats_file" "$state_file" "$expected_hash" <<'PY'
import json
import sys

try:
    stats = json.loads(open(sys.argv[1]).read())
    state = json.loads(open(sys.argv[2]).read())
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

expected_hash = sys.argv[3]
if (
    stats.get("torrent_info_hash") == expected_hash
    and stats.get("dht_verified") is True
    and stats.get("dht_sequence") == state.get("last_dht_seq")
    and state.get("last_dht_info_hash") == expected_hash
    and state.get("last_info_hash") == expected_hash
):
    raise SystemExit(0)
raise SystemExit(1)
PY
        then
            log "Seeder verified authoritative DHT publication: ${expected_hash:0:16}..."
            return 0
        fi
        sleep 1
    done
    log "ERROR: Seeder did not authoritatively verify the published torrent within 360s"
    return 1
}

# --- Step 1: Resolve or create the latest snapshot ---
if [[ "$USE_PLACEHOLDER_SNAPSHOT" == true ]]; then
    log "USE_PLACEHOLDER_SNAPSHOT=true — creating a local 128 MiB placeholder"
    PLACEHOLDER_FILE=$(bash "$REPO_DIR/scripts/create-placeholder-snapshot.sh" "$WORK_DIR")
    FILENAME=$(basename "$PLACEHOLDER_FILE")
    TARGET_FILE="$PLACEHOLDER_FILE"
    log "Created placeholder: ${FILENAME}"
else
    log "Resolving latest snapshot URL"

    # Bug 2+3 fix: use -f to fail on HTTP errors, capture exit code explicitly
    RAW_URL=""
    if ! RAW_URL=$(curl -sSfL -A "$AGENT" "$UPSTREAM_SNAPSHOT_INDEX_URL" | tr -d '"\r\n '); then
        log "ERROR: Could not fetch latest snapshot URL from $UPSTREAM_SNAPSHOT_INDEX_URL (curl exit $?)"
        exit 1
    fi
    if [ -z "$RAW_URL" ]; then
        log "ERROR: Empty response from $UPSTREAM_SNAPSHOT_INDEX_URL"
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
fi

# Stable name for torrenting (changing filenames = new info hash = no delta reuse)
STABLE_NAME="nano-ledger-snapshot.7z"
STABLE_FILE="${OUTPUT_DIR}/${STABLE_NAME}"
META_FILE="${OUTPUT_DIR}/snapshot-meta.json"
CURRENT_STABLE_TARGET=$(readlink -f "$STABLE_FILE" 2>/dev/null || true)

# --- Step 2: Clean up stale files ---
# Bug 4 fix: also clean stale .partial and .aria2 files from old snapshots
for STALE_FILE in "$WORK_DIR"/*.7z "$WORK_DIR"/*.7z.partial "$WORK_DIR"/*.7z.aria2; do
    [ -f "$STALE_FILE" ] || continue
    STALE_BASE=$(basename "$STALE_FILE")
    # Keep files matching the current snapshot
    if [ "$STALE_FILE" != "$CURRENT_STABLE_TARGET" ] && \
       [ "$STALE_BASE" != "$FILENAME" ] && \
       [ "$STALE_BASE" != "${FILENAME}.partial" ] && \
       [ "$STALE_BASE" != "${FILENAME}.aria2" ]; then
        log "Removing stale file from different snapshot: $STALE_FILE"
        rm -f "$STALE_FILE"
    fi
done

# Use .partial file for download, then atomically rename on success
PARTIAL_FILE="${TARGET_FILE}.partial"

# --- Early exit: an unchanged canonical torrent needs no seeder interruption ---
# The long-lived seeder republishes the mutable item every 30 minutes, and the
# separate hourly status-push timer keeps the dashboard current.
if [ -f "$META_FILE" ] && [ -f "$STABLE_FILE" ] && [ -s "$STABLE_FILE" ]; then
    readarray -t PREVIOUS_METADATA < <(python3 - "$META_FILE" <<'PY'
import json
import sys

try:
    metadata = json.load(open(sys.argv[1]))
except (OSError, json.JSONDecodeError):
    metadata = {}
for key in ("original_filename", "torrent_info_hash", "torrent_format_version"):
    print(metadata.get(key, ""))
PY
    )
    PREV_FILENAME="${PREVIOUS_METADATA[0]:-}"
    PREV_TORRENT="${PREVIOUS_METADATA[1]:-}"
    PREV_TORRENT_FORMAT_VERSION="${PREVIOUS_METADATA[2]:-}"
    if [ "$PREV_FILENAME" = "$FILENAME" ] && [ -n "$PREV_TORRENT" ] && [ "$PREV_TORRENT_FORMAT_VERSION" = "$TORRENT_FORMAT_VERSION" ]; then
        log "Snapshot unchanged (${FILENAME}, torrent ${PREV_TORRENT}) — no seeder reload or DHT publication requested"
        log "=== Daily snapshot pipeline complete (unchanged snapshot) ==="
        exit 0
    fi
    # Metadata matches but publish didn't complete — ensure TARGET_FILE exists so we skip download
    if [ "$PREV_FILENAME" = "$FILENAME" ] && [ ! -f "$TARGET_FILE" ]; then
        log "Previous download exists as ${STABLE_FILE} — linking back to tmp/"
        ln -f "$STABLE_FILE" "$TARGET_FILE" 2>/dev/null || ln -sf "$STABLE_FILE" "$TARGET_FILE"
    fi
fi

# If the final .7z already exists in tmp/, skip download entirely
if [ -f "$TARGET_FILE" ] && [ -s "$TARGET_FILE" ]; then
    log "Final file already exists: $TARGET_FILE ($(stat -c%s "$TARGET_FILE") bytes) — skipping download"
else
    # --- Step 3: Download with aria2c (resumable, multi-connection) ---
    if [ -f "$PARTIAL_FILE" ] && [ -s "$PARTIAL_FILE" ]; then
        CURRENT_SIZE=$(stat -c%s "$PARTIAL_FILE")
        log "Resuming download: $PARTIAL_FILE ($(numfmt --to=iec-i --suffix=B "$CURRENT_SIZE") so far)"
    else
        log "Starting new download: $LATEST_URL"
    fi

    # Get expected size from server for post-download validation
    EXPECTED_SIZE=$(curl -sSfLI -A "$AGENT" "$LATEST_URL" | grep -i '^content-length:' | tr -d '[:space:]' | cut -d: -f2) || true
    if [ -n "$EXPECTED_SIZE" ]; then
        log "Expected size: $(numfmt --to=iec-i --suffix=B "$EXPECTED_SIZE")"
    fi

    # aria2c handles resume via its .aria2 control file — far more reliable than
    # curl -C - which is a dumb byte-offset append with no corruption detection.
    # --file-allocation=none avoids pre-allocating 60GB (important on low-RAM systems).
    # --quiet suppresses ALL stdout (progress bar + summaries) to avoid flooding journald.
    # We run aria2c in the background and poll the file size every 20s for clean progress logs.
    log "Downloading with aria2c (4 connections, auto-resume)"
    aria2c \
        --user-agent="$AGENT" \
        --max-connection-per-server=4 \
        --split=4 \
        --min-split-size=50M \
        --continue=true \
        --auto-file-renaming=false \
        --allow-overwrite=false \
        --max-tries=10 \
        --retry-wait=30 \
        --timeout=300 \
        --connect-timeout=30 \
        --lowest-speed-limit=100K \
        --file-allocation=none \
        --quiet=true \
        --dir="$WORK_DIR" \
        --out="$(basename "$PARTIAL_FILE")" \
        "$LATEST_URL" &
    ARIA_PID=$!

    # Progress poller: log size/speed/ETA every 20 seconds while aria2c runs
    POLL_INTERVAL=20
    PREV_SIZE=$(stat -c%s "$PARTIAL_FILE" 2>/dev/null || echo 0)
    PREV_TIME=$(date +%s)
    while kill -0 "$ARIA_PID" 2>/dev/null; do
        sleep "$POLL_INTERVAL"
        NOW_SIZE=$(stat -c%s "$PARTIAL_FILE" 2>/dev/null || echo 0)
        NOW_TIME=$(date +%s)
        ELAPSED=$((NOW_TIME - PREV_TIME))
        if [ "$ELAPSED" -gt 0 ]; then
            SPEED_BPS=$(( (NOW_SIZE - PREV_SIZE) / ELAPSED ))
        else
            SPEED_BPS=0
        fi
        SPEED_HUMAN=$(numfmt --to=iec-i --suffix=B "$SPEED_BPS" 2>/dev/null || echo "${SPEED_BPS}B")
        SIZE_HUMAN=$(numfmt --to=iec-i --suffix=B "$NOW_SIZE" 2>/dev/null || echo "${NOW_SIZE}B")
        if [ -n "${EXPECTED_SIZE:-}" ] && [ "$EXPECTED_SIZE" -gt 0 ] 2>/dev/null && [ "$NOW_SIZE" -gt 0 ]; then
            PCT=$(( NOW_SIZE * 100 / EXPECTED_SIZE ))
            REMAINING=$((EXPECTED_SIZE - NOW_SIZE))
            if [ "$SPEED_BPS" -gt 0 ]; then
                ETA_SECS=$((REMAINING / SPEED_BPS))
                ETA_MIN=$((ETA_SECS / 60))
                ETA_SEC=$((ETA_SECS % 60))
                log "Progress: ${SIZE_HUMAN} / $(numfmt --to=iec-i --suffix=B "$EXPECTED_SIZE") (${PCT}%) ${SPEED_HUMAN}/s ETA ${ETA_MIN}m${ETA_SEC}s"
            else
                log "Progress: ${SIZE_HUMAN} / $(numfmt --to=iec-i --suffix=B "$EXPECTED_SIZE") (${PCT}%) stalled"
            fi
        else
            log "Progress: ${SIZE_HUMAN} ${SPEED_HUMAN}/s"
        fi
        PREV_SIZE=$NOW_SIZE
        PREV_TIME=$NOW_TIME
    done

    # Collect aria2c exit code
    ARIA_EXIT=0
    wait "$ARIA_PID" || ARIA_EXIT=$?

    if [ "$ARIA_EXIT" -ne 0 ]; then
        PARTIAL_SIZE=$(stat -c%s "$PARTIAL_FILE" 2>/dev/null || echo 0)
        log "ERROR: aria2c exited with code $ARIA_EXIT (downloaded $(numfmt --to=iec-i --suffix=B "$PARTIAL_SIZE") so far, will resume next run)"
        exit 1
    fi

    # --- Post-download validation ---
    DOWNLOADED_SIZE=$(stat -c%s "$PARTIAL_FILE" 2>/dev/null || echo 0)

    # Validate against expected Content-Length if available
    if [ -n "${EXPECTED_SIZE:-}" ] && [ "$EXPECTED_SIZE" -gt 0 ] 2>/dev/null; then
        if [ "$DOWNLOADED_SIZE" -ne "$EXPECTED_SIZE" ]; then
            log "ERROR: Size mismatch: downloaded $(numfmt --to=iec-i --suffix=B "$DOWNLOADED_SIZE") but expected $(numfmt --to=iec-i --suffix=B "$EXPECTED_SIZE") — keeping partial for resume"
            exit 1
        fi
        log "Size verified: $(numfmt --to=iec-i --suffix=B "$DOWNLOADED_SIZE") matches Content-Length"
    elif [ "$DOWNLOADED_SIZE" -lt 1000000000 ]; then
        log "ERROR: Download too small ($(numfmt --to=iec-i --suffix=B "$DOWNLOADED_SIZE")) — expected ~60GB"
        exit 1
    fi

    # Verify 7z magic bytes (37 7a bc af 27 1c) — Bug 1 fix
    MAGIC=$(hexdump -n 6 -e '6/1 "%02x"' "$PARTIAL_FILE" 2>/dev/null || true)
    if [ "$MAGIC" != "377abcaf271c" ]; then
        log "ERROR: Not a valid 7z archive (magic: $MAGIC) — removing corrupt file"
        rm -f "$PARTIAL_FILE" "${PARTIAL_FILE}.aria2"
        exit 1
    fi

    # Atomically rename .partial to final filename
    log "Renaming to final filename"
    rm -f "${PARTIAL_FILE}.aria2"
    mv "$PARTIAL_FILE" "$TARGET_FILE"

    ORIG_SIZE=$(stat -c%s "$TARGET_FILE")
    log "Downloaded ${FILENAME} ($(numfmt --to=iec-i --suffix=B "$ORIG_SIZE"))"
fi

# --- Step 4: Symlink to stable name, compute provenance ---

# Preserve the current canonical pair before repointing it to the new archive.
if [ -f "$META_FILE" ] && [ -f "$STABLE_FILE" ] && [ -f "${STABLE_FILE}.torrent" ]; then
    PREVIOUS_TORRENT=$(python3 - "$META_FILE" <<'PY'
import json
import sys

try:
    print(json.load(open(sys.argv[1])).get("torrent_info_hash", ""))
except (OSError, json.JSONDecodeError):
    print("")
PY
    )
    if [ -n "$PREVIOUS_TORRENT" ]; then
        cd "$REPO_DIR"
        source .venv/bin/activate
        python - "$OUTPUT_DIR" "$PREVIOUS_TORRENT" "$SNAPSHOT_RETENTION_COUNT" <<'PY'
import sys

from producer.retention import retain_current_snapshot

retain_current_snapshot(sys.argv[1], sys.argv[2], int(sys.argv[3]))
PY
    fi
fi

# New snapshot — compute SHA-256 for provenance record
log "Computing SHA-256 of ${FILENAME}"
SHA256=$(sha256sum "$TARGET_FILE" | cut -d' ' -f1)
FILE_SIZE=$(stat -c%s "$TARGET_FILE")
log "SHA-256: ${SHA256}"

# Symlink the timestamped file to the stable torrent name.
# The original stays in tmp/ so future runs can detect it by filename.
# The torrent and seeder use the stable symlink path.
log "Symlinking ${FILENAME} → ${STABLE_NAME}"
ln -sf "$TARGET_FILE" "$STABLE_FILE"

# Write provenance metadata (written BEFORE publish; updated with torrent hash after).
python3 - "$META_FILE" "$FILENAME" "$SHA256" "$FILE_SIZE" "$TORRENT_FORMAT_VERSION" <<'PY'
import datetime
import json
import sys

metadata = {
    "original_filename": sys.argv[2],
    "sha256": sys.argv[3],
    "size_bytes": int(sys.argv[4]),
    "torrent_format_version": int(sys.argv[5]),
    "downloaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
with open(sys.argv[1], "w") as metadata_file:
    json.dump(metadata, metadata_file, indent=2)
PY
log "Wrote ${META_FILE}"

# --- Step 5: Create the torrent; the long-lived seeder owns DHT publication ---
log "Creating torrent; deferring DHT publication to the long-lived seeder"

cd "$REPO_DIR"
source .venv/bin/activate
if [ -z "${DHT_PRIVATE_KEY:-}" ] && [ -f "$HOME/.env" ]; then
    source "$HOME/.env"
fi

PUBLISH_OUTPUT=$(python -m producer.cli publish \
    --private-key "$DHT_PRIVATE_KEY" \
    --snapshot-file "$STABLE_FILE" \
    --original-filename "$FILENAME" \
    --defer-dht-publish)

echo "$PUBLISH_OUTPUT"

# Extract info hash and update metadata so future runs skip
TORRENT_HASH=$(echo "$PUBLISH_OUTPUT" | grep -oP '(?<=Info-hash \(v2\): ).*' || true)
TORRENT_HASH_V1=$(echo "$PUBLISH_OUTPUT" | grep -oP '(?<=Info-hash \(v1\): ).*' || true)
if [ -n "$TORRENT_HASH" ]; then
    python3 - "$META_FILE" "$TORRENT_HASH" "$TORRENT_HASH_V1" <<'PY'
import json
import sys

with open(sys.argv[1]) as metadata_file:
    metadata = json.load(metadata_file)
metadata["torrent_info_hash"] = sys.argv[2]
metadata["torrent_info_hash_v1"] = sys.argv[3]
with open(sys.argv[1], "w") as metadata_file:
    json.dump(metadata, metadata_file, indent=2)
PY
    log "Updated metadata with torrent hash: $TORRENT_HASH"
fi

# --- Step 6: Reload the long-lived seeder and publish the new torrent ---
# A reload keeps its DHT session and existing retained swarms alive. The seeder
# writes publisher_state.json only after authoritative verification.
wait_for_authoritative_seeder "$TORRENT_HASH"

# --- Step 7: Push status to API ---
if [ -n "${STATUS_API_URL:-}" ]; then
    log "Pushing status to ${STATUS_API_URL}"
    "${REPO_DIR}/scripts/push-snapshot-status.sh" || log "WARNING: Status push failed (non-fatal)"
fi

log "=== Daily snapshot pipeline complete ==="
