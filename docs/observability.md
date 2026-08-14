# Observability

The producer and mirror expose Prometheus metrics from their own process. A
scrape only serializes a cached registry: it never calls libtorrent or triggers
DHT work. The metrics listener is loopback-only by default.

The initial public dashboard intentionally reports the authoritative producer
only. Community mirror operators are not tracked and do not send telemetry to
OpenRAI. A mirror can be scraped locally by its operator.

## Local endpoint

Metrics start automatically unless `METRICS_ENABLED=false`. The value is a
strict case-insensitive boolean; unset or empty uses the default `true`.

| Service | Default endpoint | Override |
|---|---|---|
| Producer seeder | `http://127.0.0.1:9108/metrics` | `METRICS_PORT`, `METRICS_BIND` |
| Mirror / leecher | `http://127.0.0.1:9109/metrics` | `METRICS_PORT`, `METRICS_BIND` |

Do not set `METRICS_BIND=0.0.0.0` on the public producer. The endpoint has no
authentication and is meant to be collected locally.

The important metrics are:

| Metric | Meaning |
|---|---|
| `nano_snapshot_dht_sequence` | Exact active BEP-46 mutable-item sequence. |
| `nano_snapshot_generation_info` | Current torrent v2 info hash and DHT sequence; the only metric that labels an info hash. |
| `nano_snapshot_size_bytes` | Active archive size. |
| `nano_snapshot_bytes_uploaded_total`, `nano_snapshot_bytes_downloaded_total` | Process-lifetime BitTorrent transfer counters. Use `rate(...[5m])` for throughput. |
| `nano_snapshot_swarm_peers`, `nano_snapshot_swarm_connections` | Current connected swarm participants. |
| `nano_snapshot_swarm_dht_nodes`, `nano_snapshot_ready`, `nano_snapshot_state_info` | DHT reachability and service health. |

Counters reset when their process restarts. Prometheus handles counter resets in
`rate()` queries. The active-generation info metric is replaced when a new
snapshot is observed; no high-churn transfer metric is labeled by info hash.

## Producer collection with Grafana Cloud

1. In Grafana Cloud, create an Access Policy token scoped to `metrics:write`
   and obtain its Remote Write Endpoint, Username / Instance ID, and token.
2. Install the Grafana Alloy binary on the producer host following Grafana’s
   current installation instructions. The committed user-level unit expects it
   at `/usr/bin/alloy`.
3. After pulling this release, sync the producer environment so it includes
   `prometheus-client`:

   ```sh
   cd /opt/nano-snapshot-swarm
   uv sync --extra dev
   ```

4. Add these secrets to the producer user’s `~/.env`:

   ```sh
   GRAFANA_CLOUD_PROMETHEUS_REMOTE_WRITE_URL=https://prometheus-.../api/prom/push
   GRAFANA_CLOUD_PROMETHEUS_INSTANCE_ID=...
   GRAFANA_CLOUD_PROMETHEUS_WRITE_TOKEN=...
   ```

   If `GRAFANA_CLOUD_PROMETHEUS_REMOTE_WRITE_URL` is unset or empty, the
   optional Grafana Alloy service is completely bypassed. The producer’s local
   `127.0.0.1:9108/metrics` endpoint remains available.

5. Symlink `systemd/nano-observability.service` into
   `~/.config/systemd/user/`, then reload and start it:

   ```sh
   systemctl --user daemon-reload
   systemctl --user enable --now nano-observability.service
   journalctl --user -u nano-observability.service -f
   ```

6. Verify both ends before using the dashboard:

   ```sh
   curl --fail http://127.0.0.1:9108/metrics | rg nano_snapshot
   systemctl --user status nano-observability.service
   ```

The Alloy template at
[`observability/nano-snapshot-swarm.alloy`](../observability/nano-snapshot-swarm.alloy)
scrapes only `127.0.0.1:9108` every 30 seconds and remote-writes it with fixed
producer labels.

## Public dashboard

Import
[`observability/nano-snapshot-swarm-dashboard.json`](../observability/nano-snapshot-swarm-dashboard.json)
into Grafana Cloud and select the Cloud Prometheus data source. It has a
60-second refresh and shows producer readiness, exact DHT sequence, snapshot
size, DHT nodes, transfer rates, swarm health, and the active generation.

After confirming the panels, use **Share → Share externally → Anyone with the
link**. Externally shared dashboards are read-only and can execute only the
saved queries, but anyone with the URL can view all data rendered by the
dashboard. Keep this dashboard limited to the producer metrics above; do not
add secrets, private addresses, or unaudited data sources.
