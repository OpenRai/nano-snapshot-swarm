#!/usr/bin/env bash
# nano-snapshot start — manually trigger the snapshot pipeline
# Run on the server as the openrai user.
set -euo pipefail

ME="${0##*/}"

usage() {
    cat <<EOF
Usage: $ME [--follow]

Run on the server as the openrai user.

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

echo "=== Starting nano-snapshot.service ==="
systemctl --user start nano-snapshot.service

if $FOLLOW; then
    echo "=== Tailing logs (Ctrl-C to detach) ==="
    journalctl --user -u nano-snapshot -f
else
    echo "=== Recent logs ==="
    journalctl --user -u nano-snapshot -n 20 --no-pager
fi
