ARG AUTHORITY_PUBKEY
FROM python:3 AS builder-cpp

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
    -Dpython-egg-info=OFF && \
    make -j2 && \
    make install && \
    ldconfig /usr/local/lib


FROM python:3

RUN apt-get update && apt-get install -y --no-install-recommends \
    zstd \
    ca-certificates \
    libboost-system1.83.0 \
    libboost-python1.83.0 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY --from=builder-cpp /usr/local/lib/libtorrent*.so* /usr/local/lib/
COPY --from=builder-cpp /usr/local/lib/python3/ /usr/local/lib/python3/
RUN ldconfig /usr/local/lib

RUN pip install --no-cache-dir pynacl bencodepy

WORKDIR /app
COPY shared/ /app/shared/
COPY mirror/ /app/mirror/

ENV PYTHONPATH=/app
ENV DATA_DIR=/data
ENV POLL_INTERVAL=600
ENV LOG_LEVEL=INFO
ARG AUTHORITY_PUBKEY
ENV AUTHORITY_PUBKEY=${AUTHORITY_PUBKEY}

VOLUME ["/data"]

EXPOSE 6881/tcp 6881/udp

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python3 -c "import libtorrent; libtorrent.session({'enable_dht': False}); print('ok')"

ENTRYPOINT ["python3", "-m", "mirror.watcher"]
