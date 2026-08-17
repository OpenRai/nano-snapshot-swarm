# nano-snapshot-swarm

Decentralized [Nano](https://nano.org) ledger snapshot distribution over
BitTorrent. The producer signs a Mainline DHT mutable item that points to the
current BitTorrent v2 info hash. The value is a project-specific v2 extension
inspired by BEP 46; see [Torrent and Magnet Format](docs/torrent-format.md).

There are two main user-facing workflows:

1. Download the latest snapshot once, then unpack it into a Nano node data directory.
2. Run a long-lived mirror that discovers updates and seeds fresh snapshots for the community.

Rather than relying on a single download host, this system lets anyone
contribute bandwidth by seeding snapshots peer-to-peer. Mirrors recheck their
existing archive before requesting a replacement torrent, so matching pieces
can be reused. Reuse depends on the two torrent contents and is not guaranteed.

---

## Two Services

| Service | Location | Description |
|---|---|---|
| **Mirror** | `mirror/` | Mirror service. Discovers snapshots via DHT, downloads and seeds them. |
| **Producer** | `producer/` | Pipeline and long-lived seeder. Creates torrents, publishes the signed DHT pointer, and seeds the current snapshot. |

For the exact hybrid torrent, raw-v2 DHT pointer, magnet, and download-route contract, see [Torrent and Magnet Format](docs/torrent-format.md).

## Two Mirror Modes

| Mode | Flag | Use Case |
|---|---|---|
| **Swarm** | (default, daemon) | Long-running mirror. Polls DHT every N seconds, auto-updates, seeds back to the P2P network. |
| **Leech** | `--once` | One-shot download. Discover latest → download → optional extract → exit. Good for CI, one-off syncs, testing. |

## Quick Start

### 1. Get the Latest Snapshot Once

Use leech mode when you just want the newest snapshot archive or extracted ledger and do not want to run a mirror daemon. The published mirror image already has the current OpenRAI producer public key baked in, so downloaders do not need to go hunting for `PRODUCER_SIGNING_PUBKEY`.

```bash
# uvx from a local git clone: read the baked-in default key from the repo root
PRODUCER_SIGNING_PUBKEY="$(<PRODUCER_SIGNING_PUBKEY)" uvx --from . nano-mirror --once --extract --data-dir ./nano-data

# or, with Docker: no PRODUCER_SIGNING_PUBKEY needed for the default stream
mkdir -p ./nano-data
chmod 0777 ./nano-data
docker run --rm \
  -v ./nano-data:/data \
  ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror:latest \
  --once --extract
```

To unpack the archive into the directory that the official Nano node Docker image uses, mount your Nano node data directory at `/root` and copy the extracted `data.ldb` there. The Nano docs describe Docker as using the host path supplied by `-v`/`--volume` for the node's data directory, and the container keeps the ledger under `/root`.

```bash
cp nano-data/data.ldb /path/to/nano-node-data/data.ldb
```

Provision space for the downloaded archive, the extracted ledger, and working
headroom. Check the current archive size before starting an extraction.

### 2. Host a P2P Mirror

Use swarm mode when you want to contribute bandwidth and keep the latest snapshot flowing through the network. The published image and repo `docker-compose.yml` already target the default OpenRAI snapshot stream.

```bash
mkdir -p ./nano-data
chmod 0777 ./nano-data
docker compose up -d
docker compose logs -f nano-mirror
```

For Kubernetes, run the same container with a persistent `/data` volume and expose TCP/UDP 6881. Monitor it with pod logs and the health check.

```bash
kubectl get pods
kubectl logs -f deploy/nano-mirror
kubectl describe pod <pod-name>
```

Provision storage for one complete archive plus filesystem and resume-data
headroom. The required size changes with the published snapshot.

---

## Documentation

| Document | What it covers |
|---|---|
| [docs/mirror-swarm-mode.md](docs/mirror-swarm-mode.md) | Long-running seeding mirror |
| [docs/mirror-leech-mode.md](docs/mirror-leech-mode.md) | One-shot download (--once) |
| [docs/producer-guide.md](docs/producer-guide.md) | Running the Producer, key generation, scheduling |
| [docs/configuration.md](docs/configuration.md) | All environment variables, CLI flags, docker-compose reference |
| [docs/public-beta-runbook.md](docs/public-beta-runbook.md) | Operating and troubleshooting a beta mirror |
| [docs/release-checklist.md](docs/release-checklist.md) | Evidence required before a public beta announcement |
| [docs/observability.md](docs/observability.md) | Prometheus metrics, Grafana Cloud collection, and the public dashboard |
