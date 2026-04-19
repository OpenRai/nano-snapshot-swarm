#!/usr/bin/env bash
# nano-seed status — show seeder stats from the stats file + journal
# Run on the server as the deploy user, or remotely via SSH.
set -euo pipefail

DATA_DIR="${OUTPUT_DIR:-$HOME/nano-snapshots}"
STATS_FILE="$DATA_DIR/seeder-stats.json"
LINES="${1:-10}"

echo "=== Nano Torrent Seeder ==="
echo

# Service status (one-liner)
if systemctl --user is-active nano-seed &>/dev/null; then
    echo "Service: ACTIVE"
else
    echo "Service: INACTIVE"
fi
echo

# Stats file
if [[ -f "$STATS_FILE" ]]; then
    # Parse JSON with python (available on server)
    python3 -c "
import json, sys
s = json.load(open('$STATS_FILE'))

uptime = s.get('uptime_seconds', 0)
h, m = divmod(uptime, 3600)
m, sec = divmod(m, 60)

state = s.get('state', '?')
progress = s.get('progress_pct', 100)
state_str = f\"{state} ({progress}%)\" if state != 'seeding' else state
print(f\"  State:         {state_str}\")
print(f\"  Torrent:       {s.get('torrent_name', '?')}\")
print(f\"  Snapshot:      {s.get('snapshot_size_gib', '?')} GiB\")
print(f\"  Peers:         {s.get('peers', '?')}\")
print(f\"  Upload rate:   {s.get('upload_rate_kbps', '?')} KB/s\")
print(f\"  Download rate: {s.get('download_rate_kbps', '?')} KB/s\")
print(f\"  Total upload:  {s.get('total_upload_mib', '?')} MiB\")
print(f\"  Uptime:        {int(h)}h {int(m)}m {int(sec)}s\")
print(f\"  Updated:       {s.get('updated_at', '?')}\")
"
else
    echo "  (no stats file yet — seeder may not have started)"
fi

echo
echo "=== Recent Logs ==="
journalctl --user -u nano-seed -n "$LINES" --no-pager 2>/dev/null || echo "  (no journal entries)"
