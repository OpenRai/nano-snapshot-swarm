#!/usr/bin/env bash
# nano-mirror-build — build the mirror Docker image with authority pubkey baked in
# Run on the server as the deploy user.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ME="${0##*/}"

usage() {
    cat <<EOF
Usage: $ME [--push]

Build the mirror image and optionally push to GHCR.
Reads AUTHORITY_PUBKEY from \$HOME/.env.

Run on the server as the deploy user.

Options:
  --push   Build and push to GHCR (ghcr.io/openrai/nano-p2p-mirror:latest)
EOF
    exit 1
}

PUSH=false
while [[ "${1:-}" == --* ]]; do
    case "$1" in
        --push) PUSH=true; shift ;;
        *) usage ;;
    esac
done

echo "=== Reading AUTHORITY_PUBKEY from $HOME/.env ==="
PUBKEY_FULL=$(grep '^AUTHORITY_PUBKEY=' "$HOME/.env")
if [[ "$PUBKEY_FULL" =~ ^AUTHORITY_PUBKEY=([a-f0-9]+) ]]; then
    PUBKEY="${BASH_REMATCH[1]}"
    echo "Found pubkey: ${PUBKEY:0:16}..."
else
    echo "ERROR: Could not parse AUTHORITY_PUBKEY from .env" >&2
    exit 1
fi

echo "=== Building mirror image ==="
cd "$REPO_DIR"
IMG_TAG="ghcr.io/openrai/nano-p2p-mirror:latest"
docker build \
    --build-arg AUTHORITY_PUBKEY="$PUBKEY" \
    -t "$IMG_TAG" \
    -f mirror/Dockerfile .

echo "=== Image built: $IMG_TAG ==="
docker images "$IMG_TAG" --format "  {{.Repository}}:{{.Tag}}  {{.Size}}  {{.CreatedSince}}"

if $PUSH; then
    echo "=== Pushing to GHCR ==="
    docker push "$IMG_TAG"
    echo "=== Pushed: $IMG_TAG ==="
else
    echo ""
    echo "=== Not pushing (use --push to push to GHCR) ==="
fi
