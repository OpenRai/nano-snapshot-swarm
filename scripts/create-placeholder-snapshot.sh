#!/usr/bin/env bash
# Create a deterministic-size, non-archive payload for producer pipeline tests.
set -euo pipefail

PLACEHOLDER_SIZE_BYTES=$((128 * 1024 * 1024))
OUTPUT_DIR="${1:-${OUTPUT_DIR:-.}}"
OUTPUT_BASENAME="${2:-}"

mkdir -p "$OUTPUT_DIR"

if [[ -z "$OUTPUT_BASENAME" ]]; then
    TIMESTAMP=$(date -u '+%Y-%m-%dT%H-%M-%SZ')
    OUTPUT_BASENAME="nano-ledger-snapshot-${TIMESTAMP}.7z"
    suffix=0
    while [[ -e "$OUTPUT_DIR/$OUTPUT_BASENAME" ]]; do
        suffix=$((suffix + 1))
        OUTPUT_BASENAME="nano-ledger-snapshot-${TIMESTAMP}-${suffix}.7z"
    done
fi

OUTPUT_FILE="$OUTPUT_DIR/$OUTPUT_BASENAME"
TEMP_FILE=$(mktemp "$OUTPUT_DIR/.nano-placeholder.XXXXXX")
trap 'rm -f "$TEMP_FILE"' EXIT

echo "[$(date -Iseconds)] Creating placeholder snapshot: $OUTPUT_FILE" >&2
dd if=/dev/urandom of="$TEMP_FILE" bs=1048576 count=128 2>/dev/null
mv "$TEMP_FILE" "$OUTPUT_FILE"
trap - EXIT

ACTUAL_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE")
if [[ "$ACTUAL_SIZE" -ne "$PLACEHOLDER_SIZE_BYTES" ]]; then
    echo "ERROR: Placeholder size mismatch: ${ACTUAL_SIZE} bytes" >&2
    exit 1
fi

echo "[$(date -Iseconds)] Placeholder complete: ${OUTPUT_FILE} (${ACTUAL_SIZE} bytes)" >&2
printf '%s\n' "$OUTPUT_FILE"
