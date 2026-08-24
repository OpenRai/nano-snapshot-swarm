#!/usr/bin/env bash
# Refuse a snapshot download that cannot finish without exhausting its filesystem.
set -euo pipefail

log() {
    echo "[$(date -Iseconds)] $*"
}

usage() {
    echo "Usage: $0 --output-dir DIR --incoming-bytes N --partial-bytes N --retention-count N" >&2
    exit 2
}

require_non_negative_integer() {
    local name="$1"
    local value="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        log "ERROR: ${name} must be a non-negative integer; got ${value@Q}"
        exit 2
    fi
}

output_dir=""
incoming_bytes=""
partial_bytes=""
retention_count=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output-dir) output_dir="${2:-}"; shift 2 ;;
        --incoming-bytes) incoming_bytes="${2:-}"; shift 2 ;;
        --partial-bytes) partial_bytes="${2:-}"; shift 2 ;;
        --retention-count) retention_count="${2:-}"; shift 2 ;;
        *) usage ;;
    esac
done

[ -n "$output_dir" ] || usage
require_non_negative_integer incoming_bytes "$incoming_bytes"
require_non_negative_integer partial_bytes "$partial_bytes"
require_non_negative_integer retention_count "$retention_count"

safety_bytes="${SNAPSHOT_DISK_SAFETY_BYTES:-5368709120}"
require_non_negative_integer SNAPSHOT_DISK_SAFETY_BYTES "$safety_bytes"

if [ "$partial_bytes" -gt "$incoming_bytes" ]; then
    partial_bytes="$incoming_bytes"
fi
remaining_bytes=$((incoming_bytes - partial_bytes))
available_kib="$(df -Pk "$output_dir" | awk 'NR == 2 { print $4 }')"
if ! [[ "$available_kib" =~ ^[0-9]+$ ]]; then
    log "ERROR: unable to determine available disk space for ${output_dir}"
    exit 1
fi
available_bytes=$((available_kib * 1024))
required_bytes=$((remaining_bytes + safety_bytes))

# retain_current_snapshot() creates a hard link in OUTPUT_DIR, so retention
# keeps a previous torrent available without allocating another archive copy.
log "Disk budget: incoming=${incoming_bytes} partial=${partial_bytes} remaining=${remaining_bytes} retention=${retention_count} hardlink_reserve=0 safety=${safety_bytes} required=${required_bytes} available=${available_bytes}"
if [ "$available_bytes" -lt "$required_bytes" ]; then
    log "ERROR: insufficient disk budget: requires ${required_bytes} bytes free for the incoming snapshot and safety reserve, but only ${available_bytes} bytes are available in ${output_dir}"
    exit 1
fi
