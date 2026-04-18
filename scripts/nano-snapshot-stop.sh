#!/usr/bin/env bash
# nano-snapshot stop — stop a running snapshot pipeline
# Run on the server as the deploy user.
set -euo pipefail

ME="${0##*/}"

echo "=== Stopping nano-snapshot.service ==="
systemctl --user stop nano-snapshot.service || true
echo "=== Done ==="
