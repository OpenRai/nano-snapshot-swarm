#!/usr/bin/env bash
# Build one mirror image platform and optionally push it to its registry.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ME="${0##*/}"
DEFAULT_IMAGE_NAME="ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror"

usage() {
    cat <<EOF
Usage: $ME [OPTIONS]

Build one platform of the mirror image.

Options:
  --platform PLATFORM   Target platform (default: host platform).
  --build-id ID         Tag the image as IMAGE_NAME:build-ID-PLATFORM.
  --tag IMAGE:TAG       Explicit output tag.
  --push                Push the image instead of loading it locally.
  --no-push             Build and load locally, even when PUSH_IMAGE is set.
  -h, --help            Show this help.

Environment:
  IMAGE_NAME           Image without tag (default: $DEFAULT_IMAGE_NAME).
  AUTHORITY_PUBKEY     Override the key read from AUTHORITY_PUBKEY.
  PUSH_IMAGE           Set to true to push without passing --push.
  REGISTRY_USERNAME    Username for an authenticated push.
  REGISTRY_TOKEN       Password/token for an authenticated push.
EOF
}

IMAGE_NAME="${IMAGE_NAME:-$DEFAULT_IMAGE_NAME}"
PLATFORM="${NANO_MIRROR_PLATFORM:-}"
BUILD_ID="${BUILD_ID:-}"
IMAGE_TAG=""
PUSH="${PUSH_IMAGE:-false}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform)
            [[ -n "${2:-}" ]] || { usage; exit 1; }
            PLATFORM="$2"
            shift 2
            ;;
        --build-id)
            [[ -n "${2:-}" ]] || { usage; exit 1; }
            BUILD_ID="$2"
            shift 2
            ;;
        --tag)
            [[ -n "${2:-}" ]] || { usage; exit 1; }
            IMAGE_TAG="$2"
            shift 2
            ;;
        --push)
            PUSH=true
            shift
            ;;
        --no-push)
            PUSH=false
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
if [[ -z "$IMAGE_TAG" ]]; then
    if [[ -n "$BUILD_ID" ]]; then
        IMAGE_TAG="$IMAGE_NAME:build-${BUILD_ID}-${PLATFORM_SUFFIX}"
    else
        IMAGE_TAG="$IMAGE_NAME:latest"
    fi
fi

AUTHORITY_PUBKEY_FILE="$REPO_DIR/AUTHORITY_PUBKEY"
if [[ -n "${AUTHORITY_PUBKEY:-}" ]]; then
    PUBKEY="$AUTHORITY_PUBKEY"
else
    if [[ ! -f "$AUTHORITY_PUBKEY_FILE" ]]; then
        echo "ERROR: missing $AUTHORITY_PUBKEY_FILE" >&2
        exit 1
    fi
    IFS= read -r PUBKEY < "$AUTHORITY_PUBKEY_FILE"
fi

PUBKEY="${PUBKEY#"${PUBKEY%%[![:space:]]*}"}"
PUBKEY="${PUBKEY%"${PUBKEY##*[![:space:]]}"}"
if [[ ! "$PUBKEY" =~ ^[a-f0-9]{64}$ ]]; then
    echo "ERROR: AUTHORITY_PUBKEY must contain a 64-character lowercase hex key" >&2
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
    --build-arg "AUTHORITY_PUBKEY=$PUBKEY"
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
    BUILD_ARGS+=(--push)
else
    BUILD_ARGS+=(--load)
fi

echo "Building $IMAGE_TAG for $PLATFORM"
cd "$REPO_DIR"
"${BUILD_ARGS[@]}" .
echo "Built $IMAGE_TAG for $PLATFORM"
