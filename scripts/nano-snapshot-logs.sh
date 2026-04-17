#!/usr/bin/env bash
# nano-snapshot logs — tail the journal for nano-snapshot
set -euo pipefail

ME="${0##*/}"

LINES=30
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    LINES="$1"
fi

REMOTE="openrai@185.208.206.54"
ssh -t "$REMOTE" "journalctl --user -u nano-snapshot -n $LINES --no-pager -f"
