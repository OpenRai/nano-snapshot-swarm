#!/usr/bin/env bash
# nano-snapshot restart — git pull + daemon-reload + restart timer
set -euo pipefail

ME="${0##*/}"

usage() {
    cat <<EOF
Usage: $ME [--service]

Options:
  --service   Also restart the .service unit (clears stale state)
EOF
    exit 1
}

RESTART_SERVICE=false
while [[ "${1:-}" == --* ]]; do
    case "$1" in
        --service) RESTART_SERVICE=true; shift ;;
        *) usage ;;
    esac
done

REMOTE="openrai@185.208.206.54"

echo "=== Pulling latest from git ==="
ssh "$REMOTE" "cd /opt/nano-bootstrap-swarm && git pull"

echo "=== Reloading systemd ==="
ssh "$REMOTE" "systemctl --user daemon-reload"

echo "=== Restarting timer ==="
ssh "$REMOTE" "systemctl --user restart nano-snapshot.timer"

if $RESTART_SERVICE; then
    echo "=== Restarting service ==="
    ssh "$REMOTE" "systemctl --user restart nano-snapshot.service"
fi

echo "=== Done ==="
ssh "$REMOTE" "systemctl --user list-timers nano-snapshot --no-pager"
