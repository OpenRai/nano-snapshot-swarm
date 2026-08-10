#!/usr/bin/env bash
# Publish a multi-platform mirror manifest from pushed platform images.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_IMAGE_NAME="ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror"

usage() {
    cat <<EOF
Usage: ${0##*/} [--platforms LIST]

Create the final image tags from the platform images built by
nano-mirror-build.sh. The build ID comes from BUILD_ID or GITHUB_SHA.

Environment:
  IMAGE_NAME           Image without tag (default: $DEFAULT_IMAGE_NAME).
  BUILD_ID             Temporary image tag suffix; defaults to GITHUB_SHA.
  REGISTRY_USERNAME    Username for an authenticated push.
  REGISTRY_TOKEN       Password/token for an authenticated push.
EOF
}

IMAGE_NAME="${IMAGE_NAME:-$DEFAULT_IMAGE_NAME}"
PLATFORMS="linux/amd64,linux/arm64"
BUILD_ID="${BUILD_ID:-${GITHUB_SHA:-}}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platforms)
            [[ -n "${2:-}" ]] || { usage >&2; exit 1; }
            PLATFORMS="$2"
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
    echo "ERROR: BUILD_ID or GITHUB_SHA is required" >&2
    exit 1
fi

IFS=',' read -r -a PLATFORM_LIST <<< "$PLATFORMS"
SOURCE_IMAGES=()
for PLATFORM in "${PLATFORM_LIST[@]}"; do
    if [[ "$PLATFORM" != */* ]]; then
        echo "ERROR: invalid platform: $PLATFORM" >&2
        exit 1
    fi
    SOURCE_IMAGES+=("$IMAGE_NAME:build-${BUILD_ID}-${PLATFORM##*/}")
done

FINAL_TAGS=()
while IFS= read -r TAG; do
    FINAL_TAGS+=("$TAG")
done < <("$SCRIPT_DIR/nano-mirror-tags.sh")

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
