#!/usr/bin/env bash
# Publish a multi-platform mirror manifest from already-pushed platform images.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ME="${0##*/}"
DEFAULT_IMAGE_NAME="ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror"

usage() {
    cat <<EOF
Usage: $ME [OPTIONS]

Create final image tags from platform-specific build images.

Options:
  --platforms LIST     Comma-separated platforms (default: linux/amd64,linux/arm64).
  --build-id ID        Build identifier used by nano-mirror-build.sh.
  --tag IMAGE:TAG      Explicit final tag. May be repeated.
  -h, --help           Show this help.

Environment:
  IMAGE_NAME           Image without tag (default: $DEFAULT_IMAGE_NAME).
  REGISTRY_USERNAME    Username for an authenticated push.
  REGISTRY_TOKEN       Password/token for an authenticated push.
EOF
}

IMAGE_NAME="${IMAGE_NAME:-$DEFAULT_IMAGE_NAME}"
PLATFORMS="${NANO_MIRROR_PLATFORMS:-linux/amd64,linux/arm64}"
BUILD_ID="${BUILD_ID:-${GITHUB_SHA:-}}"
FINAL_TAGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platforms)
            [[ -n "${2:-}" ]] || { usage; exit 1; }
            PLATFORMS="$2"
            shift 2
            ;;
        --build-id)
            [[ -n "${2:-}" ]] || { usage; exit 1; }
            BUILD_ID="$2"
            shift 2
            ;;
        --tag)
            [[ -n "${2:-}" ]] || { usage; exit 1; }
            FINAL_TAGS+=("$2")
            shift 2
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

if [[ -z "$BUILD_ID" ]]; then
    echo "ERROR: --build-id or GITHUB_SHA is required" >&2
    exit 1
fi

IFS=',' read -r -a PLATFORM_LIST <<< "$PLATFORMS"
if [[ "${#PLATFORM_LIST[@]}" -eq 0 ]]; then
    echo "ERROR: --platforms cannot be empty" >&2
    exit 1
fi

SOURCE_IMAGES=()
for PLATFORM in "${PLATFORM_LIST[@]}"; do
    if [[ "$PLATFORM" != */* ]]; then
        echo "ERROR: invalid platform: $PLATFORM" >&2
        exit 1
    fi
    SOURCE_IMAGES+=("$IMAGE_NAME:build-${BUILD_ID}-${PLATFORM##*/}")
done

if [[ "${#FINAL_TAGS[@]}" -eq 0 ]]; then
    while IFS= read -r TAG; do
        FINAL_TAGS+=("$TAG")
    done < <("$SCRIPT_DIR/nano-mirror-tags.sh")
fi

if [[ "${#FINAL_TAGS[@]}" -eq 0 ]]; then
    echo "ERROR: no final image tags were generated" >&2
    exit 1
fi

if [[ -n "${REGISTRY_TOKEN:-}" ]]; then
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

CREATE_ARGS=(docker buildx imagetools create)
for TAG in "${FINAL_TAGS[@]}"; do
    CREATE_ARGS+=(--tag "$TAG")
done
CREATE_ARGS+=("${SOURCE_IMAGES[@]}")

echo "Publishing ${#FINAL_TAGS[@]} tag(s) from ${#SOURCE_IMAGES[@]} platform image(s)"
"${CREATE_ARGS[@]}"
printf 'Published: %s\n' "${FINAL_TAGS[@]}"
