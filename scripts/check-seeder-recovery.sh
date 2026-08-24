#!/usr/bin/env bash
# Recover an active producer only after a persistent unhealthy state and disk recovery.
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

output_dir="${OUTPUT_DIR:-$HOME/nano-snapshots}"
min_free_bytes="${PRODUCER_RECOVERY_MIN_FREE_BYTES:-5368709120}"
unhealthy_seconds="${PRODUCER_RECOVERY_UNHEALTHY_SECONDS:-900}"
stale_stats_seconds="${PRODUCER_RECOVERY_STALE_STATS_SECONDS:-600}"
restart_cooldown_seconds="${PRODUCER_RECOVERY_RESTART_COOLDOWN_SECONDS:-3600}"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/nano-seed-recovery-watchdog"
unhealthy_since_file="${state_dir}/unhealthy-since"
last_restart_file="${state_dir}/last-restart"
status_push_pending_file="${state_dir}/status-push-pending"
stats_file="${output_dir}/seeder-stats.json"

require_positive_integer PRODUCER_RECOVERY_MIN_FREE_BYTES "$min_free_bytes"
require_positive_integer PRODUCER_RECOVERY_UNHEALTHY_SECONDS "$unhealthy_seconds"
require_positive_integer PRODUCER_RECOVERY_STALE_STATS_SECONDS "$stale_stats_seconds"
require_positive_integer PRODUCER_RECOVERY_RESTART_COOLDOWN_SECONDS "$restart_cooldown_seconds"

clear_unhealthy() {
    rm -f "$unhealthy_since_file"
}

read_timestamp() {
    local file="$1"
    if [ -f "$file" ]; then
        local value
        value="$(<"$file")"
        if [[ "$value" =~ ^[0-9]+$ ]]; then
            printf '%s\n' "$value"
            return
        fi
    fi
    printf '0\n'
}

recover_after_persistent_failure() {
    local now="$1"
    local reason="$2"
    local unhealthy_since
    unhealthy_since="$(read_timestamp "$unhealthy_since_file")"
    if [ "$unhealthy_since" -eq 0 ]; then
        mkdir -p "$state_dir"
        printf '%s\n' "$now" > "${unhealthy_since_file}.tmp"
        mv "${unhealthy_since_file}.tmp" "$unhealthy_since_file"
        log "WARNING: seeder is unhealthy (${reason}); waiting ${unhealthy_seconds}s before recovery"
        return
    fi

    local unhealthy_age=$((now - unhealthy_since))
    if [ "$unhealthy_age" -lt "$unhealthy_seconds" ]; then
        log "WARNING: seeder is unhealthy (${reason}) for ${unhealthy_age}s; waiting ${unhealthy_seconds}s before recovery"
        return
    fi

    local last_restart
    last_restart="$(read_timestamp "$last_restart_file")"
    local restart_age=$((now - last_restart))
    if [ "$last_restart" -ne 0 ] && [ "$restart_age" -lt "$restart_cooldown_seconds" ]; then
        log "ERROR: seeder is unhealthy (${reason}), but restart cooldown has ${restart_cooldown_seconds}s; manual investigation required"
        exit 1
    fi

    mkdir -p "$state_dir"
    printf '%s\n' "$now" > "${last_restart_file}.tmp"
    mv "${last_restart_file}.tmp" "$last_restart_file"
    : > "$status_push_pending_file"
    clear_unhealthy
    log "WARNING: seeder stayed unhealthy after disk recovery (${reason}); restarting nano-seed.service"
    systemctl --user restart nano-seed.service
}

available_kib="$(df -Pk "$output_dir" | awk 'NR == 2 { print $4 }')"
if ! [[ "$available_kib" =~ ^[0-9]+$ ]]; then
    log "ERROR: unable to determine available disk space for ${output_dir}"
    exit 1
fi
available_bytes=$((available_kib * 1024))
if [ "$available_bytes" -lt "$min_free_bytes" ]; then
    log "ERROR: only ${available_bytes} bytes free; need at least ${min_free_bytes}. Recovery is blocked until disk space is reclaimed."
    exit 1
fi

if ! systemctl --user is-active --quiet nano-seed.service; then
    log "ERROR: nano-seed.service is inactive; its normal systemd policy owns startup"
    exit 1
fi

now="$(date +%s)"
if [ ! -f "$stats_file" ]; then
    recover_after_persistent_failure "$now" "seeder stats file is missing"
    exit 0
fi

stats_mtime="$(stat -c %Y "$stats_file")"
if ! [[ "$stats_mtime" =~ ^[0-9]+$ ]]; then
    recover_after_persistent_failure "$now" "seeder stats timestamp is invalid"
    exit 0
fi
stats_age=$((now - stats_mtime))

if ! stats="$(python3 - "$stats_file" <<'PY'
import json
import sys

try:
    value = json.load(open(sys.argv[1]))
    print(value.get("state", ""))
    print("true" if value.get("dht_verified") is True else "false")
    print("true" if value.get("seeder_ready") is True else "false")
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
PY
)"; then
    recover_after_persistent_failure "$now" "seeder stats are unreadable"
    exit 0
fi
readarray -t stat_fields <<<"$stats"
state="${stat_fields[0]:-}"
dht_verified="${stat_fields[1]:-false}"
seeder_ready="${stat_fields[2]:-false}"

if [ "$seeder_ready" = true ] && [ "$stats_age" -le "$stale_stats_seconds" ]; then
    clear_unhealthy
    if [ -f "$status_push_pending_file" ]; then
        log "Seeder recovered; pushing verified status"
        if systemctl --user start nano-status-push.service; then
            rm -f "$status_push_pending_file"
        else
            log "ERROR: verified seeder recovered but status push failed; retrying on the next check"
            exit 1
        fi
    fi
    log "Seeder is ready (${stats_age}s since stats update)"
    exit 0
fi

# A file check can take as long as the archive and is healthy while it keeps
# writing fresh stats. A stale check is handled below as a genuine failure.
if [ "$state" = checking_files ] && [ "$stats_age" -le "$stale_stats_seconds" ]; then
    clear_unhealthy
    log "Seeder is checking files (${stats_age}s since stats update); recovery is not needed"
    exit 0
fi

if [ "$stats_age" -gt "$stale_stats_seconds" ]; then
    recover_after_persistent_failure "$now" "seeder stats are stale (${stats_age}s)"
else
    recover_after_persistent_failure "$now" "state=${state:-unknown}, dht_verified=${dht_verified}, seeder_ready=${seeder_ready}"
fi
