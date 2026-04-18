#!/usr/bin/env bash
# nano-snapshot restart — git pull + daemon-reload + restart timer
# Run on the server as the deploy user.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ME="${0##*/}"

usage() {
    cat <<EOF
Usage: $ME [--service]

Run on the server as the deploy user.

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

echo "=== Pulling latest from git ==="
cd "$REPO_DIR" && git pull

echo "=== Reloading systemd ==="
systemctl --user daemon-reload

echo "=== Restarting timer ==="
systemctl --user restart nano-snapshot.timer

if $RESTART_SERVICE; then
    echo "=== Restarting service ==="
    systemctl --user restart nano-snapshot.service
fi

echo "=== Done ==="
systemctl --user list-timers nano-snapshot --no-pager
