#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="${OUTPUT_DIR:-$HOME/nano-snapshots}"
LEDGER_OUTPUT="${OUTPUT_DIR}/data.ldb"
WEB_SEED_URL="${WEB_SEED_URL:-https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest}"
AGENT="nano-bootstrap-swarm/1.0"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

LATEST_URL=$(curl -sSL -A "$AGENT" "$WEB_SEED_URL" | tr -d '"')
if [ -z "$LATEST_URL" ]; then
    echo "ERROR: Could not determine latest snapshot URL from $WEB_SEED_URL" >&2
    exit 1
fi

FILENAME=$(basename "$LATEST_URL")
echo "[$(date -Iseconds)] Fetching $LATEST_URL"

curl -sSL -A "$AGENT" -o "${TMPDIR}/${FILENAME}" "$LATEST_URL"
echo "[$(date -Iseconds)] Downloaded ${FILENAME}"

if [ ! -f "${TMPDIR}/${FILENAME}" ]; then
    echo "ERROR: Download failed — file not found" >&2
    exit 1
fi

ORIG_SIZE=$(stat -c%s "${TMPDIR}/${FILENAME}")
echo "[$(date -Iseconds)] Downloaded size: ${ORIG_SIZE} bytes"

echo "[$(date -Iseconds)] Extracting with 7z"
7z x -y -o"${TMPDIR}" "${TMPDIR}/${FILENAME}" > /dev/null

if [ ! -f "${TMPDIR}/data.ldb" ]; then
    echo "ERROR: Extraction failed — data.ldb not found" >&2
    exit 1
fi

EXTRACTED_SIZE=$(stat -c%s "${TMPDIR}/data.ldb")
echo "[$(date -Iseconds)] Extracted size: ${EXTRACTED_SIZE} bytes"

echo "[$(date -Iseconds)] Copying to ${LEDGER_OUTPUT}"
cp "${TMPDIR}/data.ldb" "${LEDGER_OUTPUT}"

SHA256=$(sha256sum "${LEDGER_OUTPUT}" | cut -d' ' -f1)
echo "[$(date -Iseconds)] SHA-256: ${SHA256}"
echo "[$(date -Iseconds)] Ledger ready: ${LEDGER_OUTPUT}"
