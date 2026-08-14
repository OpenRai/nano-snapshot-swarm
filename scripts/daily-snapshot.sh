#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/scripts/runtime-config.sh"
source "$REPO_DIR/scripts/aria2-rpc.sh"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/nano-snapshots}"
UPSTREAM_SNAPSHOT_INDEX_URL="${UPSTREAM_SNAPSHOT_INDEX_URL:-https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest}"
TORRENT_FORMAT_VERSION=3
SNAPSHOT_RETENTION_COUNT="${SNAPSHOT_RETENTION_COUNT:-0}"
ARIA2_RPC_PORT="${ARIA2_RPC_PORT:-6800}"
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
if ! [[ "$ARIA2_RPC_PORT" =~ ^[0-9]+$ ]] || \
   [ "$ARIA2_RPC_PORT" -lt 1024 ] || [ "$ARIA2_RPC_PORT" -gt 65535 ]; then
    log "ERROR: ARIA2_RPC_PORT must be an integer from 1024 through 65535"
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
    # JSON-RPC reports completed bytes accurately even when split downloads write the
    # final range before earlier ranges and the partial file already has its final size.
    if ! command -v jq >/dev/null 2>&1; then
        log "ERROR: jq is required to monitor aria2c download progress"
        exit 1
    fi
    ARIA2_RPC_SECRET=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
    if [ -z "$ARIA2_RPC_SECRET" ]; then
        log "ERROR: Could not generate an aria2c RPC secret"
        exit 1
    fi
    ARIA2_GID="${ARIA2_RPC_SECRET:0:16}"
    ARIA2_RPC_URL="http://127.0.0.1:${ARIA2_RPC_PORT}/jsonrpc"
    ARIA2_STATUS_REQUEST=$(jq -cn \
        --arg token "token:${ARIA2_RPC_SECRET}" \
        --arg gid "$ARIA2_GID" \
        '{
            jsonrpc: "2.0",
            id: "progress",
            method: "aria2.tellStatus",
            params: [$token, $gid, [
                "status",
                "totalLength",
                "completedLength",
                "downloadSpeed",
                "errorCode",
                "errorMessage"
            ]]
        }')
    ARIA2_SHUTDOWN_REQUEST=$(jq -cn \
        --arg token "token:${ARIA2_RPC_SECRET}" \
        '{jsonrpc: "2.0", id: "shutdown", method: "aria2.shutdown", params: [$token]}')

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
        --enable-rpc=true \
        --rpc-listen-all=false \
        --rpc-listen-port="$ARIA2_RPC_PORT" \
        --rpc-secret="$ARIA2_RPC_SECRET" \
        --gid="$ARIA2_GID" \
        --dir="$WORK_DIR" \
        --out="$(basename "$PARTIAL_FILE")" \
        "$LATEST_URL" &
    ARIA_PID=$!

    # Wait briefly for the loopback RPC listener and command-line download to register.
    ARIA2_RPC_READY=false
    for _ in $(seq 1 20); do
        if ! kill -0 "$ARIA_PID" 2>/dev/null; then
            break
        fi
        if query_aria2_status; then
            ARIA2_RPC_READY=true
            break
        fi
        sleep 0.5
    done
    if [ "$ARIA2_RPC_READY" != true ]; then
        log "ERROR: aria2c RPC status did not become available on 127.0.0.1:${ARIA2_RPC_PORT}"
        kill "$ARIA_PID" 2>/dev/null || true
        wait "$ARIA_PID" 2>/dev/null || true
        exit 1
    fi

    # Progress poller: preserve the existing 20-second log cadence, but use
    # aria2's completedLength rather than the partial file's logical size.
    POLL_INTERVAL=20
    RPC_FAILURES=0
    RPC_FAILURE_LIMIT=3
    ARIA_TERMINAL_STATUS=""
    ARIA_MONITOR_FAILED=false
    PREV_COMPLETED=$ARIA2_COMPLETED_LENGTH
    PREV_TIME=$(date +%s)
    while kill -0 "$ARIA_PID" 2>/dev/null; do
        sleep "$POLL_INTERVAL"
        # aria2c may have exited during sleep. Do not emit one stale final update.
        if ! kill -0 "$ARIA_PID" 2>/dev/null; then
            break
        fi

        if ! query_aria2_status; then
            RPC_FAILURES=$((RPC_FAILURES + 1))
            log "WARNING: aria2c progress unavailable (${RPC_FAILURES}/${RPC_FAILURE_LIMIT}); download process is still running"
            if [ "$RPC_FAILURES" -ge "$RPC_FAILURE_LIMIT" ]; then
                log "ERROR: aria2c progress unavailable for three consecutive checks; stopping so the download can resume safely"
                ARIA_MONITOR_FAILED=true
                kill "$ARIA_PID" 2>/dev/null || true
                break
            fi
            continue
        fi
        RPC_FAILURES=0

        NOW_TIME=$(date +%s)
        ELAPSED=$((NOW_TIME - PREV_TIME))
        if [ "$ELAPSED" -gt 0 ]; then
            SPEED_BPS=$(( (ARIA2_COMPLETED_LENGTH - PREV_COMPLETED) / ELAPSED ))
        else
            SPEED_BPS=$ARIA2_DOWNLOAD_SPEED
        fi
        if [ "$SPEED_BPS" -lt 0 ]; then
            SPEED_BPS=0
        fi
        SPEED_HUMAN=$(numfmt --to=iec-i --suffix=B "$SPEED_BPS" 2>/dev/null || echo "${SPEED_BPS}B")
        SIZE_HUMAN=$(numfmt --to=iec-i --suffix=B "$ARIA2_COMPLETED_LENGTH" 2>/dev/null || echo "${ARIA2_COMPLETED_LENGTH}B")
        TOTAL_HUMAN=$(numfmt --to=iec-i --suffix=B "$ARIA2_TOTAL_LENGTH" 2>/dev/null || echo "${ARIA2_TOTAL_LENGTH}B")

        case "$ARIA2_STATUS" in
            complete)
                log "Progress: ${SIZE_HUMAN} / ${TOTAL_HUMAN} (100%) complete"
                ARIA_TERMINAL_STATUS=complete
                if ! shutdown_aria2; then
                    log "ERROR: aria2c completed but its RPC server did not shut down cleanly"
                    ARIA_MONITOR_FAILED=true
                    kill "$ARIA_PID" 2>/dev/null || true
                fi
                break
                ;;
            error|removed)
                log "ERROR: aria2c reported ${ARIA2_STATUS} (code=${ARIA2_ERROR_CODE:-unknown}): ${ARIA2_ERROR_MESSAGE:-no error message}"
                ARIA_TERMINAL_STATUS="$ARIA2_STATUS"
                if ! shutdown_aria2; then
                    kill "$ARIA_PID" 2>/dev/null || true
                fi
                break
                ;;
        esac

        if [ "$ARIA2_TOTAL_LENGTH" -gt 0 ] && [ "$ARIA2_COMPLETED_LENGTH" -gt 0 ]; then
            PCT=$(( ARIA2_COMPLETED_LENGTH * 100 / ARIA2_TOTAL_LENGTH ))
            REMAINING=$((ARIA2_TOTAL_LENGTH - ARIA2_COMPLETED_LENGTH))
            if [ "$SPEED_BPS" -gt 0 ]; then
                ETA_SECS=$((REMAINING / SPEED_BPS))
                ETA_MIN=$((ETA_SECS / 60))
                ETA_SEC=$((ETA_SECS % 60))
                log "Progress: ${SIZE_HUMAN} / ${TOTAL_HUMAN} (${PCT}%) ${SPEED_HUMAN}/s ETA ${ETA_MIN}m${ETA_SEC}s"
            else
                log "Progress: ${SIZE_HUMAN} / ${TOTAL_HUMAN} (${PCT}%) stalled"
            fi
        else
            log "Progress: ${SIZE_HUMAN} ${SPEED_HUMAN}/s"
        fi
        PREV_COMPLETED=$ARIA2_COMPLETED_LENGTH
        PREV_TIME=$NOW_TIME
    done

    # Collect aria2c exit code
    ARIA_EXIT=0
    wait "$ARIA_PID" || ARIA_EXIT=$?
    if [ "$ARIA_MONITOR_FAILED" = true ] || \
       [ "$ARIA_TERMINAL_STATUS" = error ] || \
       [ "$ARIA_TERMINAL_STATUS" = removed ]; then
        ARIA_EXIT=1
    fi

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
TORRENT_HASH=$(echo "$PUBLISH_OUTPUT" | sed -n 's/^info_hash=//p' || true)
TORRENT_HASH_V1=$(echo "$PUBLISH_OUTPUT" | sed -n 's/^info_hash_v1=//p' || true)
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
