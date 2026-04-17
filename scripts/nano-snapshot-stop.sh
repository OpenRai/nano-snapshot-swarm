#!/usr/bin/env bash
# nano-snapshot stop — stop a running snapshot pipeline
set -euo pipefail

ME="${0##*/}"

REMOTE="openrai@185.208.206.54"

echo "=== Stopping nano-snapshot.service ==="
ssh "$REMOTE" "systemctl --user stop nano-snapshot.service" || true
echo "=== Done ==="
