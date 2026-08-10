FROM debian:trixie-slim

# Debian ships native libtorrent Python bindings for amd64 and arm64. Using
# them avoids compiling libtorrent under QEMU during multi-platform builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    passwd \
    python3 \
    python3-libtorrent \
    python3-venv \
    zstd \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml /app/
RUN python3 -m venv --system-site-packages /venv \
    && uv pip install --python /venv/bin/python --no-cache -r pyproject.toml \
    && /venv/bin/python -c "import libtorrent; libtorrent.session({'enable_dht': False})"

# Keep the mirror unprivileged while retaining the documented UID for bind mounts.
RUN groupadd --gid 1000 mirror \
    && useradd --uid 1000 --gid mirror --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin mirror \
    && install -d --owner=mirror --group=mirror /data

COPY shared/ /app/shared/
COPY mirror/ /app/mirror/

ENV PYTHONPATH=/app
ENV PATH="/venv/bin:$PATH"
ENV DATA_DIR=/data
ENV POLL_INTERVAL=600
ENV LOG_LEVEL=INFO

# Public build-time defaults — AUTHORITY_PUBKEY comes from the repo root AUTHORITY_PUBKEY file
ARG AUTHORITY_PUBKEY
ARG DHT_SALT=daily
ARG SEED_PEERS=bandwidth-martyr.openrai.org:6881

RUN test -n "$AUTHORITY_PUBKEY"

ENV AUTHORITY_PUBKEY=${AUTHORITY_PUBKEY}
ENV DHT_SALT=${DHT_SALT}
ENV SEED_PEERS=${SEED_PEERS}

VOLUME ["/data"]

USER 1000:1000

EXPOSE 6881/tcp 6881/udp

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python3 -c "import libtorrent; libtorrent.session({'enable_dht': False}); print('ok')"

ENTRYPOINT ["python3", "-m", "mirror.watcher"]
