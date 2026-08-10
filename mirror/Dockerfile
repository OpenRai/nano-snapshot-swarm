FROM debian:trixie-slim

# Debian ships native libtorrent Python bindings for amd64 and arm64. Using
# them avoids compiling libtorrent under QEMU during multi-platform builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    python3-libtorrent \
    zstd \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml /app/
RUN uv pip install --system --no-cache -r pyproject.toml

COPY shared/ /app/shared/
COPY mirror/ /app/mirror/

ENV PYTHONPATH=/app
ENV DATA_DIR=/data
ENV POLL_INTERVAL=600
ENV LOG_LEVEL=INFO

# Public build-time defaults — AUTHORITY_PUBKEY comes from the repo root AUTHORITY_PUBKEY file
ARG AUTHORITY_PUBKEY
ARG DHT_SALT=daily
ARG WEB_SEED_URL=https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest

RUN test -n "$AUTHORITY_PUBKEY"

ENV AUTHORITY_PUBKEY=${AUTHORITY_PUBKEY}
ENV DHT_SALT=${DHT_SALT}
ENV WEB_SEED_URL=${WEB_SEED_URL}

VOLUME ["/data"]

EXPOSE 6881/tcp 6881/udp

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python3 -c "import libtorrent; libtorrent.session({'enable_dht': False}); print('ok')"

ENTRYPOINT ["python3", "-m", "mirror.watcher"]
