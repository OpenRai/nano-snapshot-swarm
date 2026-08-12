#!/usr/bin/env bash
# Build one mirror image platform and optionally push it to its registry.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_IMAGE_NAME="ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror"

usage() {
    cat <<EOF
Usage: ${0##*/} [--platform PLATFORM] [--push]

Build one platform of the mirror image. The build ID comes from BUILD_ID or
GITHUB_SHA and identifies the temporary platform image used by publication.

Environment:
  IMAGE_NAME           Image without tag (default: $DEFAULT_IMAGE_NAME).
  PRODUCER_SIGNING_PUBKEY     Override the key read from PRODUCER_SIGNING_PUBKEY.
  SEED_PEERS           Build-time HOST:PORT peers; defaults to the public seeder.
  BUILD_ID             Temporary image tag suffix; defaults to GITHUB_SHA.
  REGISTRY_USERNAME    Username for an authenticated push.
  REGISTRY_TOKEN       Password/token for an authenticated push.
EOF
}

IMAGE_NAME="${IMAGE_NAME:-$DEFAULT_IMAGE_NAME}"
SEED_PEERS="${SEED_PEERS-bandwidth-martyr.openrai.org:6881}"
PLATFORM=""
BUILD_ID="${BUILD_ID:-${GITHUB_SHA:-}}"
PUSH=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform)
            [[ -n "${2:-}" ]] || { usage >&2; exit 1; }
            PLATFORM="$2"
            shift 2
            ;;
        --push)
            PUSH=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ -z "$PLATFORM" ]]; then
    case "$(uname -m)" in
        arm64|aarch64) PLATFORM="linux/arm64" ;;
        amd64|x86_64) PLATFORM="linux/amd64" ;;
        *)
            echo "ERROR: unsupported host architecture: $(uname -m)" >&2
            exit 1
            ;;
    esac
fi

if [[ "$PLATFORM" != */* || "$PLATFORM" == *,* ]]; then
    echo "ERROR: --platform must name exactly one platform, for example linux/arm64" >&2
    exit 1
fi

PLATFORM_SUFFIX="${PLATFORM##*/}"
if [[ -n "$BUILD_ID" ]]; then
    IMAGE_TAG="$IMAGE_NAME:build-${BUILD_ID}-${PLATFORM_SUFFIX}"
else
    IMAGE_TAG="$IMAGE_NAME:latest"
fi

if [[ -v AUTHORITY_PUBKEY ]]; then
    echo "WARNING: AUTHORITY_PUBKEY is ignored; use PRODUCER_SIGNING_PUBKEY." >&2
fi

PRODUCER_SIGNING_PUBKEY_FILE="$REPO_DIR/PRODUCER_SIGNING_PUBKEY"
if [[ -n "${PRODUCER_SIGNING_PUBKEY:-}" ]]; then
    PUBKEY="$PRODUCER_SIGNING_PUBKEY"
elif [[ -f "$PRODUCER_SIGNING_PUBKEY_FILE" ]]; then
    IFS= read -r PUBKEY < "$PRODUCER_SIGNING_PUBKEY_FILE"
else
    echo "ERROR: missing $PRODUCER_SIGNING_PUBKEY_FILE" >&2
    exit 1
fi

PUBKEY="${PUBKEY#"${PUBKEY%%[![:space:]]*}"}"
PUBKEY="${PUBKEY%"${PUBKEY##*[![:space:]]}"}"
if [[ ! "$PUBKEY" =~ ^[a-f0-9]{64}$ ]]; then
    echo "ERROR: PRODUCER_SIGNING_PUBKEY must contain a 64-character lowercase hex key" >&2
    exit 1
fi

if [[ "$PUSH" == true && -n "${REGISTRY_TOKEN:-}" ]]; then
    REGISTRY="${IMAGE_NAME%%/*}"
    REGISTRY_USERNAME="${REGISTRY_USERNAME:-${GITHUB_ACTOR:-}}"
    if [[ -z "$REGISTRY_USERNAME" ]]; then
        echo "ERROR: REGISTRY_USERNAME is required when REGISTRY_TOKEN is set" >&2
        exit 1
    fi
    printf '%s' "$REGISTRY_TOKEN" | docker login "$REGISTRY" \
        --username "$REGISTRY_USERNAME" \
        --password-stdin
fi

BUILD_ARGS=(
    docker buildx build
    --platform "$PLATFORM"
    --build-arg "PRODUCER_SIGNING_PUBKEY=$PUBKEY"
    --build-arg "SEED_PEERS=$SEED_PEERS"
    --file mirror/Dockerfile
    --tag "$IMAGE_TAG"
)

if [[ "${GITHUB_ACTIONS:-false}" == true ]]; then
    BUILD_ARGS+=(
        --cache-from type=gha
        --cache-to type=gha,mode=max
        --label "org.opencontainers.image.source=https://github.com/${GITHUB_REPOSITORY:-OpenRai/nano-snapshot-swarm}"
        --label "org.opencontainers.image.revision=${GITHUB_SHA:-unknown}"
    )
fi

if [[ "$PUSH" == true ]]; then
    BUILD_ARGS+=(
        --provenance=mode=max
        --sbom=true
        --push
    )
else
    BUILD_ARGS+=(--load)
fi

echo "Building $IMAGE_TAG for $PLATFORM"
cd "$REPO_DIR"
"${BUILD_ARGS[@]}" .
echo "Built $IMAGE_TAG for $PLATFORM"
