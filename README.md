# nano-bootstrap-swarm

Decentralized [Nano](https://nano.org) ledger snapshot distribution using BitTorrent BEP 46 mutable torrents and DHT.

There are two main user-facing workflows:

1. Download the latest snapshot once, then unpack it into a Nano node data directory.
2. Run a long-lived mirror that continuously republishes and seeds fresh snapshots for the community.

The ledger lives as an LMDB database (~80 GB). Rather than serving it from a single S3 bucket, this system lets anyone contribute bandwidth by seeding snapshots peer-to-peer. New snapshots are published to the Mainline DHT under an Ed25519 authority key; mirrors discover updates and download only the changed pieces via BitTorrent v2 delta efficiency.

---

## Two Services

| Service | Location | Description |
|---|---|---|
| **Mirror** | `mirror/` | Mirror service. Discovers snapshots via DHT, downloads and seeds them. |
| **Producer** | `producer/` | CLI tool. Creates a torrent for a snapshot archive and publishes its info-hash to the DHT. |

## Two Mirror Modes

| Mode | Flag | Use Case |
|---|---|---|
| **Swarm** | (default, daemon) | Long-running mirror. Polls DHT every N seconds, auto-updates, seeds back to the P2P network. |
| **Leech** | `--once` | One-shot download. Discover latest → download → optional extract → exit. Good for CI, one-off syncs, testing. |

## Quick Start

### 1. Get the Latest Snapshot Once

Use leech mode when you just want the newest snapshot archive or extracted ledger and do not want to run a mirror daemon. The published mirror image already has the current OpenRAI producer public key baked in, so downloaders do not need to go hunting for `AUTHORITY_PUBKEY`.

```bash
# uvx from a local git clone: read the baked-in default key from the repo root
AUTHORITY_PUBKEY="$(<AUTHORITY_PUBKEY)" uvx --from . nano-mirror --once --extract --data-dir ./data

# or, with Docker: no AUTHORITY_PUBKEY needed for the default stream
docker run --rm \
  -v $(pwd)/data:/data \
  ghcr.io/openrai/nano-p2p-mirror:latest \
  --once --extract
```

The snapshot archive is written into `/data` (or your chosen volume). To unpack it into the directory that the official Nano node Docker image uses, mount your Nano node data directory at `/root` and copy the extracted `data.ldb` there. The Nano docs describe Docker as using the host path supplied by `-v`/`--volume` for the node's data directory, and the container keeps the ledger under `/root`.

```bash
cp data/data.ldb /path/to/nano-node-data/data.ldb
```

That extraction path needs roughly `{compressed size} + {2 * compressed size}` GB of temporary space, so as of 2026-04 a ~60 GB archive means about ~180 GB free.

### 2. Host a P2P Mirror

Use swarm mode when you want to contribute bandwidth and keep the latest snapshot flowing through the network. The published image and repo `docker-compose.yml` already target the default OpenRAI snapshot stream.

```bash
docker compose up -d
docker compose logs -f nano-mirror
```

For Kubernetes, run the same container with a persistent `/data` volume and expose TCP/UDP 6881. Monitor it with pod logs and the health check.

```bash
kubectl get pods
kubectl logs -f deploy/nano-mirror
kubectl describe pod <pod-name>
```

As of 2026-04, compressed snapshot hosting needs less than 60 GB of disk space.

---

## Documentation

| Document | What it covers |
|---|---|
| [docs/mirror-swarm-mode.md](docs/mirror-swarm-mode.md) | Long-running seeding mirror |
| [docs/mirror-leech-mode.md](docs/mirror-leech-mode.md) | One-shot download (--once) |
| [docs/producer-guide.md](docs/producer-guide.md) | Running the Producer, key generation, scheduling |
| [docs/configuration.md](docs/configuration.md) | All environment variables, CLI flags, docker-compose reference |
