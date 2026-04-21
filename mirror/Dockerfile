FROM python:3.13-trixie AS builder-cpp

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    libboost-system-dev \
    libboost-dev \
    libboost-python-dev \
    libssl-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir setuptools

WORKDIR /build

RUN git clone --depth 1 --branch v2.0.11 --recurse-submodules https://github.com/arvidn/libtorrent.git

WORKDIR /build/libtorrent
RUN mkdir build && cd build && \
    cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTS=OFF \
    -DBUILD_EXAMPLES=OFF \
    -Dpython-bindings=ON \
    -Dpython-egg-info=OFF \
    -Dpython-install-system-dir=ON && \
    make -j2 && \
    make install && \
    ldconfig /usr/local/lib


FROM python:3.13-trixie

RUN apt-get update && apt-get install -y --no-install-recommends \
    zstd \
    ca-certificates \
    libboost-system1.83.0 \
    libboost-python1.83.0 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY --from=builder-cpp /usr/local/lib/libtorrent*.so* /usr/local/lib/
COPY --from=builder-cpp /usr/local/lib/python3.13/ /usr/local/lib/python3.13/
RUN ldconfig /usr/local/lib

RUN pip install --no-cache-dir pynacl bencodepy

WORKDIR /app
COPY shared/ /app/shared/
COPY mirror/ /app/mirror/

ENV PYTHONPATH=/app
ENV DATA_DIR=/data
ENV POLL_INTERVAL=600
ENV LOG_LEVEL=INFO

# Public build-time defaults — override with --build-arg or at runtime with -e
ARG AUTHORITY_PUBKEY=b9036c6c3d05d70b9d8b3e255b15cec8f962e88e36ebaeb96232c08c1cf163f3
ARG DHT_SALT=daily
ARG WEB_SEED_URL=https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest

ENV AUTHORITY_PUBKEY=${AUTHORITY_PUBKEY}
ENV DHT_SALT=${DHT_SALT}
ENV WEB_SEED_URL=${WEB_SEED_URL}

VOLUME ["/data"]

EXPOSE 6881/tcp 6881/udp

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python3 -c "import libtorrent; libtorrent.session({'enable_dht': False}); print('ok')"

ENTRYPOINT ["python3", "-m", "mirror.watcher"]
