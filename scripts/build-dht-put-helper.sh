#!/usr/bin/env bash
# Build the explicit-sequence BEP 46 publisher against the host libtorrent ABI.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${NANO_DHT_BUILD_ROOT:-${REPO_DIR}/.cache/dht-put-build}"
LIBTORRENT_TAG="RC_2_0"
BOOST_VERSION="1.83.0"

mkdir -p "$BUILD_ROOT"
if [[ ! -d "$BUILD_ROOT/libtorrent-src/.git" ]]; then
    git clone --depth 1 --branch "$LIBTORRENT_TAG" \
        https://github.com/arvidn/libtorrent.git "$BUILD_ROOT/libtorrent-src"
fi
if [[ ! -f "$BUILD_ROOT/boost_${BOOST_VERSION//./_}/boost/config.hpp" ]]; then
    archive="$BUILD_ROOT/boost_${BOOST_VERSION//./_}.tar.gz"
    curl -fsSL "https://archives.boost.io/release/${BOOST_VERSION}/source/boost_${BOOST_VERSION//./_}.tar.gz" \
        -o "$archive"
    tar -xzf "$archive" -C "$BUILD_ROOT"
fi

mkdir -p "$REPO_DIR/bin"
LIBTORRENT_LIBRARY="$(ldconfig -p | awk '/libtorrent-rasterbar\.so\.2\.0/{print $NF; exit}')"
if [[ -z "$LIBTORRENT_LIBRARY" || ! -f "$LIBTORRENT_LIBRARY" ]]; then
    echo "ERROR: libtorrent-rasterbar 2.0 runtime library not found" >&2
    exit 1
fi
"${CXX:-g++}" -std=c++17 -O2 -Wall -Wextra -Werror \
    -I"$BUILD_ROOT/libtorrent-src/include" \
    -I"$BUILD_ROOT/boost_${BOOST_VERSION//./_}" \
    "$REPO_DIR/producer/dht_put_helper.cpp" \
    "$LIBTORRENT_LIBRARY" -lcrypto -pthread \
    -o "$REPO_DIR/bin/nano-dht-put"

echo "Built $REPO_DIR/bin/nano-dht-put"
