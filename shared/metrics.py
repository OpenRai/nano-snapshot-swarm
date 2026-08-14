"""Bounded, process-local Prometheus metrics for snapshot services.

The services update this collector from their normal status/event loops.  The
Prometheus HTTP handler only serializes the registry, so a slow scrape can
never block or synchronously query libtorrent.
"""
from __future__ import annotations

import logging
import os
from typing import Literal

from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server

logger = logging.getLogger("shared.metrics")


def _boolean_from_env(name: str, default: bool) -> bool:
    """Read a strict boolean while treating an unset or empty value as default."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false (case-insensitive); got {value!r}")


class SnapshotMetrics:
    """Metrics for one producer or mirror process.

    Info-hash labels are deliberately confined to ``generation_info``.  They
    are cleared before a new generation is recorded, keeping the live scrape
    bounded even though a new torrent is created for each snapshot.
    """

    def __init__(self, service: Literal["producer", "mirror"]):
        self.service = service
        self.registry = CollectorRegistry(auto_describe=True)
        self.bytes_uploaded = Counter(
            "nano_snapshot_bytes_uploaded",
            "BitTorrent bytes uploaded since this process started.",
            registry=self.registry,
        )
        self.bytes_downloaded = Counter(
            "nano_snapshot_bytes_downloaded",
            "BitTorrent bytes downloaded since this process started.",
            registry=self.registry,
        )
        self.snapshot_size = Gauge(
            "nano_snapshot_size_bytes",
            "Size of the active snapshot archive in bytes.",
            ["service"],
            registry=self.registry,
        )
        self.dht_sequence = Gauge(
            "nano_snapshot_dht_sequence",
            "Exact BEP-46 DHT mutable-item sequence for the active snapshot.",
            ["service"],
            registry=self.registry,
        )
        self.generation = Gauge(
            "nano_snapshot_generation_info",
            "The active snapshot generation. This is the only metric with an info hash label.",
            ["service", "info_hash", "sequence"],
            registry=self.registry,
        )
        self.swarm_peers = Gauge(
            "nano_snapshot_swarm_peers",
            "Connected peers by BitTorrent role for the active swarm.",
            ["role"],
            registry=self.registry,
        )
        self.connections = Gauge(
            "nano_snapshot_swarm_connections",
            "Connected BitTorrent peers and seeds for the active swarm.",
            registry=self.registry,
        )
        self.dht_nodes = Gauge(
            "nano_snapshot_swarm_dht_nodes",
            "DHT nodes currently known to the local libtorrent session.",
            registry=self.registry,
        )
        self.ready = Gauge(
            "nano_snapshot_ready",
            "Whether this service is ready for its current role (1 ready, 0 not ready).",
            registry=self.registry,
        )
        self.state = Gauge(
            "nano_snapshot_state",
            "Current service state.",
            ["service", "state"],
            registry=self.registry,
        )
        self._last_uploaded = 0
        self._last_downloaded = 0

    def start_http_server(self, port: int) -> None:
        """Expose this registry on loopback, unless metrics are disabled."""
        if not _boolean_from_env("METRICS_ENABLED", default=True):
            logger.info("Prometheus metrics disabled by METRICS_ENABLED")
            return
        address = os.environ.get("METRICS_BIND", "127.0.0.1")
        try:
            start_http_server(port, addr=address, registry=self.registry)
        except OSError as exc:
            # Observability must not take down an otherwise healthy seeder or mirror.
            logger.warning("Could not start Prometheus metrics endpoint: %s", exc)
            return
        logger.info("Prometheus metrics listening on http://%s:%s/metrics", address, port)

    def observe_generation(
        self,
        *,
        info_hash: str,
        sequence: int,
        size_bytes: int | None = None,
    ) -> None:
        self.dht_sequence.labels(service=self.service).set(sequence)
        self.generation.clear()
        self.generation.labels(
            service=self.service,
            info_hash=info_hash,
            sequence=str(sequence),
        ).set(1)
        if size_bytes is not None:
            self.snapshot_size.labels(service=self.service).set(size_bytes)

    def observe_transfer(
        self,
        *,
        total_upload: int,
        total_download: int,
        peers: int,
        seeds: int,
        connections: int,
    ) -> None:
        upload_delta = total_upload - self._last_uploaded
        download_delta = total_download - self._last_downloaded
        if upload_delta > 0:
            self.bytes_uploaded.inc(upload_delta)
        if download_delta > 0:
            self.bytes_downloaded.inc(download_delta)
        self._last_uploaded = total_upload
        self._last_downloaded = total_download
        self.swarm_peers.labels(role="leecher").set(peers)
        self.swarm_peers.labels(role="seeder").set(seeds)
        self.connections.set(connections)

    def observe_state(self, state: str, *, ready: bool) -> None:
        self.state.clear()
        self.state.labels(service=self.service, state=state).set(1)
        self.ready.set(1 if ready else 0)
