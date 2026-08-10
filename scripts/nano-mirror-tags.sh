#!/usr/bin/env bash
# Print the final mirror image tags for the current GitHub Actions ref.
set -euo pipefail

DEFAULT_IMAGE_NAME="ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror"
IMAGE_NAME="${IMAGE_NAME:-$DEFAULT_IMAGE_NAME}"
EVENT_NAME="${GITHUB_EVENT_NAME:-push}"
REF_NAME="${GITHUB_REF_NAME:-}"
REF_TYPE="${GITHUB_REF_TYPE:-}"
SHORT_SHA="${GITHUB_SHA:-local}"
SHORT_SHA="${SHORT_SHA:0:7}"
TAGS=()

if [[ "$EVENT_NAME" == pull_request ]]; then
    TAGS+=("$IMAGE_NAME:pr-${GITHUB_EVENT_NUMBER:-pr}")
elif [[ "$REF_TYPE" == tag && -n "$REF_NAME" ]]; then
    VERSION="${REF_NAME#v}"
    TAGS+=("$IMAGE_NAME:$VERSION")
    if [[ "$VERSION" =~ ^([0-9]+)\.([0-9]+)\. ]]; then
        TAGS+=("$IMAGE_NAME:${BASH_REMATCH[1]}.${BASH_REMATCH[2]}")
    fi
elif [[ -n "$REF_NAME" ]]; then
    BRANCH_TAG="$(printf '%s' "$REF_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_.-]/-/g')"
    TAGS+=("$IMAGE_NAME:$BRANCH_TAG")
    if [[ "$REF_NAME" == main ]]; then
        TAGS+=("$IMAGE_NAME:latest")
    fi
fi

if [[ "${#TAGS[@]}" -eq 0 ]]; then
    TAGS+=("$IMAGE_NAME:latest")
fi

TAGS+=("$IMAGE_NAME:sha-$SHORT_SHA")

printf '%s\n' "${TAGS[@]}"
