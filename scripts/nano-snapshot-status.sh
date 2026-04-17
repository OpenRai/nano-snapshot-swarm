#!/usr/bin/env bash
# nano-snapshot status — check timer state and recent journal entries
set -euo pipefail

ME="${0##*/}"

usage() {
    cat <<EOF
Usage: $ME [log-lines]

Examples:
  $ME          # timer status + last 20 log lines
  $ME 50       # timer status + last 50 log lines
EOF
    exit 1
}

LINES=20
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    LINES="$1"
    shift
fi

# Detect: are we openrai on the server?
if ssh -o ConnectTimeout=5 openrai@185.208.206.54 'id -nu' 2>/dev/null | grep -q openrai; then
    REMOTE="openrai@185.208.206.54"
elif ssh -o ConnectTimeout=5 root@185.208.206.54 'id -nu' 2>/dev/null | grep -q root; then
    REMOTE="root@185.208.206.54"
else
    echo "ERROR: Cannot reach 185.208.206.54" >&2
    exit 1
fi

ssh "$REMOTE" "systemctl --user list-timers nano-snapshot --no-pager"
echo
ssh "$REMOTE" "journalctl --user -u nano-snapshot -n $LINES --no-pager"
