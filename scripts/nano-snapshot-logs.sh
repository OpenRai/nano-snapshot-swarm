#!/usr/bin/env bash
# nano-snapshot logs — tail the journal for nano-snapshot
# Run on the server as the deploy user.
set -euo pipefail

ME="${0##*/}"

LINES=30
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    LINES="$1"
fi

journalctl --user -u nano-snapshot -n "$LINES" --no-pager -f
