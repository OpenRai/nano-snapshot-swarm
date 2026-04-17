FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    libboost-system-dev \
    libboost-python-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN git clone --depth 1 --branch v2.0.11 https://github.com/arvidn/libtorrent.git

WORKDIR /build/libtorrent
RUN mkdir build && cd build && \
    cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -Dpython-bindings=ON \
    -Dpython-install-system=ON \
    -Dstatic-boost=ON \
    -Dboost-system=boost_system \
    -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTS=OFF \
    -DBUILD_EXAMPLES=OFF && \
    make -j$(nproc) && \
    make install && \
    ldconfig /usr/local/lib


FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libboost-system1.84.0 \
    libboost-python1.84.0 \
    libssl3 \
    zstd \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY --from=builder /usr/local/lib/libtorrent*.so* /usr/local/lib/
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
RUN ldconfig /usr/local/lib

COPY requirements-mirror.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

WORKDIR /app
COPY shared/ /app/shared/
COPY mirror/ /app/mirror/

ENV PYTHONPATH=/app
ENV DATA_DIR=/data
ENV POLL_INTERVAL=600
ENV LOG_LEVEL=INFO

VOLUME ["/data"]

EXPOSE 6881/tcp 6881/udp

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python3 -c "import libtorrent; s=libtorrent.session({'enable_dht':False}); print('ok')"

ENTRYPOINT ["python3", "-m", "mirror.watcher"]