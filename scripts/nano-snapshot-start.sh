#!/usr/bin/env bash
# nano-snapshot start — manually trigger the snapshot pipeline
set -euo pipefail

ME="${0##*/}"

usage() {
    cat <<EOF
Usage: $ME [--follow]

Options:
  --follow   Tail journal logs after starting
EOF
    exit 1
}

FOLLOW=false
while [[ "${1:-}" == --* ]]; do
    case "$1" in
        --follow) FOLLOW=true; shift ;;
        *) usage ;;
    esac
done

REMOTE="openrai@185.208.206.54"

echo "=== Starting nano-snapshot.service ==="
ssh "$REMOTE" "systemctl --user start nano-snapshot.service"

if $FOLLOW; then
    echo "=== Tailing logs (Ctrl-C to detach) ==="
    ssh -t "$REMOTE" "journalctl --user -u nano-snapshot -f"
else
    echo "=== Recent logs ==="
    ssh "$REMOTE" "journalctl --user -u nano-snapshot -n 20 --no-pager"
fi
