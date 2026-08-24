#!/usr/bin/env bash
# Detect a healthy-but-stalled Alloy remote-write queue and make one bounded
# recovery attempt.  This is intentionally separate from the producer: a
# collector failure must never stop DHT publication or seeding.
set -euo pipefail

log() {
    echo "[$(date -Iseconds)] $*"
}

require_positive_integer() {
    local name="$1"
    local value="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]] || [ "$value" -eq 0 ]; then
        log "ERROR: ${name} must be a positive integer; got ${value@Q}"
        exit 2
    fi
}

MIN_FREE_BYTES="${OBSERVABILITY_MIN_FREE_BYTES:-5368709120}"
STALE_SECONDS="${OBSERVABILITY_REMOTE_WRITE_STALE_SECONDS:-600}"
RESTART_COOLDOWN_SECONDS="${OBSERVABILITY_RESTART_COOLDOWN_SECONDS:-3600}"
ALLOY_METRICS_URL="${OBSERVABILITY_ALLOY_METRICS_URL:-http://127.0.0.1:12345/metrics}"
PRODUCER_METRICS_URL="${OBSERVABILITY_PRODUCER_METRICS_URL:-http://127.0.0.1:9108/metrics}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/nano-observability-watchdog"
LAST_RESTART_FILE="${STATE_DIR}/last-restart"

require_positive_integer OBSERVABILITY_MIN_FREE_BYTES "$MIN_FREE_BYTES"
require_positive_integer OBSERVABILITY_REMOTE_WRITE_STALE_SECONDS "$STALE_SECONDS"
require_positive_integer OBSERVABILITY_RESTART_COOLDOWN_SECONDS "$RESTART_COOLDOWN_SECONDS"

available_kib="$(df -Pk / | awk 'NR == 2 { print $4 }')"
if ! [[ "$available_kib" =~ ^[0-9]+$ ]]; then
    log "ERROR: unable to determine available disk space"
    exit 1
fi
available_bytes=$((available_kib * 1024))
if [ "$available_bytes" -lt "$MIN_FREE_BYTES" ]; then
    log "ERROR: only ${available_bytes} bytes free on /; need at least ${MIN_FREE_BYTES}. Reclaim disk space before restarting Alloy."
    exit 1
fi

producer_metrics="$(curl --fail --silent --show-error "$PRODUCER_METRICS_URL")" || {
    log "ERROR: producer metrics are unavailable at ${PRODUCER_METRICS_URL}; not restarting Alloy"
    exit 1
}
if ! grep -q '^nano_snapshot_' <<<"$producer_metrics"; then
    log "ERROR: producer metrics are unavailable at ${PRODUCER_METRICS_URL}; not restarting Alloy"
    exit 1
fi

alloy_metrics="$(curl --fail --silent --show-error "$ALLOY_METRICS_URL")" || {
    log "ERROR: Alloy metrics are unavailable at ${ALLOY_METRICS_URL}"
    exit 1
}
last_sent="$(awk '/^prometheus_remote_storage_queue_highest_sent_timestamp_seconds\{.*component_id="prometheus.remote_write.grafana_cloud"/ { printf "%.0f\n", $NF; exit }' <<<"$alloy_metrics")"
if ! [[ "$last_sent" =~ ^[0-9]+$ ]] || [ "$last_sent" -eq 0 ]; then
    log "ERROR: Alloy does not expose a remote-write last-success timestamp"
    exit 1
fi

now="$(date +%s)"
age_seconds=$((now - last_sent))
if [ "$age_seconds" -le "$STALE_SECONDS" ]; then
    log "Alloy remote write is current (${age_seconds}s old)"
    exit 0
fi

last_restart=0
if [ -f "$LAST_RESTART_FILE" ]; then
    last_restart="$(<"$LAST_RESTART_FILE")"
fi
if ! [[ "$last_restart" =~ ^[0-9]+$ ]]; then
    log "ERROR: invalid watchdog state in ${LAST_RESTART_FILE}"
    exit 1
fi
restart_age=$((now - last_restart))
if [ "$restart_age" -lt "$RESTART_COOLDOWN_SECONDS" ]; then
    log "ERROR: Alloy remote write is stale (${age_seconds}s), but restart cooldown has ${RESTART_COOLDOWN_SECONDS}s; manual investigation required"
    exit 1
fi

mkdir -p "$STATE_DIR"
temp_file="${LAST_RESTART_FILE}.tmp"
printf '%s\n' "$now" > "$temp_file"
mv "$temp_file" "$LAST_RESTART_FILE"
log "WARNING: Alloy remote write is stale (${age_seconds}s); restarting nano-observability.service"
systemctl --user restart nano-observability.service
