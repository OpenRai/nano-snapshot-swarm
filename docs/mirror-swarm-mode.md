# Swarm Mode: Long-Running Seeding Mirror

Swarm mode is the default operational mode. The mirror runs as a daemon, polling the DHT every `POLL_INTERVAL` seconds, downloading new snapshots as they appear, and seeding them back to the P2P network.

The published Docker image already targets the default OpenRAI producer stream. You only need to set `PRODUCER_SIGNING_PUBKEY` when you intentionally want to follow a different producer.

---

## Starting in Swarm Mode

### Docker Compose

```bash
docker compose up -d
```

This is the fire-and-forget path for anyone with spare disk and bandwidth. The container restarts automatically and keeps seeding the latest snapshot back to the network.

### Docker Run

```bash
docker run -d \
  --name nano-mirror \
  -p 6881:6881/tcp -p 6881:6881/udp \
  -v ./nano-data:/data \
  ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror:latest
```

---

## Monitoring

### Quick Status Check

```bash
# Is the container running?
docker compose ps

# One-line status: state + last log line
docker inspect --format='{{.State.Status}}' nano-mirror && \
  docker logs --tail 1 nano-mirror

# What snapshot is it tracking?
docker exec nano-mirror cat /data/mirror_state.json
```

### Kubernetes

Use a Deployment or StatefulSet with a persistent `/data` volume, TCP/UDP 6881 exposed, and a restart policy that keeps the pod alive.

```bash
kubectl get pods
kubectl logs -f deploy/nano-mirror
kubectl describe pod <pod-name>
```

### Docker Logs

```bash
docker compose logs -f
# or
docker logs -f nano-mirror

# Last 50 lines only
docker logs --tail 50 nano-mirror
```

### Understanding the Log Output

The mirror goes through distinct phases. Here's what to look for at each stage:

**Startup:**

| Log | Meaning |
|---|---|
| `Producer signing public key: <hex>...` | The configured key that verifies the producer's signed DHT records |
| `Mode: SWARM (continuous polling every Ns)` | Running as long-lived daemon |
| `libtorrent session started, listening on port 6881` | BitTorrent engine ready |

**Discovery (repeats every `POLL_INTERVAL`):**

| Log | Meaning |
|---|---|
| `DHT get_mutable_item requested` | Querying DHT for latest snapshot |
| `Discovered DHT item: seq=N` | Found a published snapshot |
| `Discovery seq N <= stored seq N; no update needed` | Already have the latest, nothing to do |
| `New snapshot detected! seq=N` | Newer snapshot found, will download |
| `No snapshot discovered from DHT` | DHT query returned nothing (item expired or not yet published) |

**Download (only when new snapshot found):**

| Log | Meaning |
|---|---|
| `Force recheck on existing data...` | Hashing local file to find reusable pieces |
| `State transition: downloading → checking_files` | Mirror phase changed |
| `Download: 45.2% \| State: downloading \| DL: 1234.5 KB/s \| Peers: 3` | Active transfer with progress |
| `Snapshot seeding complete` | Download finished, now seeding to others |
| `Download: 0.0% ... Peers: 0` | No reachable peers — check connectivity |

**Seeding (steady state):**

Once download completes, the mirror quietly seeds. There are no periodic seeding logs by default. Use `LOG_LEVEL=DEBUG` for verbose output.

**Shutdown:**

| Log | Meaning |
|---|---|
| `Received signal 15, initiating graceful shutdown` | SIGTERM received |
| `Mirror service stopped` | Clean exit |

### Healthcheck

```bash
docker compose ps
docker inspect --format='{{.State.Health.Status}}' nano-mirror
```

The container healthcheck verifies libtorrent can be imported and initialized.

### mirror_state.json

This file persists across restarts and tells you what the mirror is tracking:

```bash
docker exec nano-mirror cat /data/mirror_state.json
```

```json
{
  "last_seq": 42,
  "last_info_hash": "924a5772b2db194d...",
  "current_torrent_name": "nano-ledger-snapshot.7z",
  "phase": "seeding",
  "last_error": ""
}
```

| Field | Meaning |
|---|---|
| `last_seq` | Sequence number from DHT — increments with each new publish |
| `last_info_hash` | BitTorrent info-hash of the snapshot being tracked |
| `current_torrent_name` | Filename of the snapshot on disk |
| `phase` | Current mirror lifecycle phase |
| `last_error` | Last persisted error, if any |

If `last_seq` is `0`, the mirror has never successfully discovered a snapshot.

When a later v2 info-hash is discovered, the mirror keeps the canonical archive
in place, adds the replacement paused, resolves its metadata, and force-rechecks
the archive before it resumes piece requests. This lets normal BitTorrent piece
checking reuse matching data across snapshots; it does not promise a measured
daily compression-reuse rate.

See [Torrent and Magnet Format](torrent-format.md) for the torrent identity,
magnet, DHT pointer, and public-route contract used while diagnosing updates.

### snapshot-meta.json

This file is updated locally after DHT discovery and torrent metadata resolution, so mirrors and leechers can inspect the latest resolved snapshot details without scraping logs:

```bash
docker exec nano-mirror cat /data/snapshot-meta.json
```

```json
{
  "producer_signing_pubkey": "2b845d...",
  "dht_pubkey": "2b845d...",
  "dht_signature": "ed05ae...",
  "dht_seq": 70,
  "dht_salt": "daily",
  "torrent_info_hash": "f6d068...",
  "dht_verified": true,
  "current_torrent_name": "nano-ledger-snapshot.7z",
  "original_filename": "snapshot-2026-04-22T00-00-00Z.7z"
}
```

### Disk Usage

```bash
# Check data volume size
du -sh ./nano-data/

# List files in the data volume
docker exec nano-mirror ls -lh /data/
```

A complete compressed snapshot is approximately 60 GB or less as of 2026-04. During download, a partial file of the same size exists (BitTorrent pre-allocates).

### Network

The mirror needs:

- **UDP port 6881** — for DHT communication (discovery and seeding coordination)
- **TCP port 6881** — for BitTorrent peer connections

A UDP netcat probe does not prove reachability: UDP has no connection
handshake, so netcat can report success without a response from the mirror.
Confirm the local listener, then observe real DHT or BitTorrent traffic while
testing from another machine:

```bash
# On the mirror host: this only proves that the process has bound the UDP port.
ss -lunp | grep ':6881'

# On the mirror host, while a remote peer is discovering or downloading:
sudo tcpdump -ni any 'udp port 6881'
```

If behind NAT without port forwarding, the mirror can still download through outbound peer connections but cannot serve peers effectively.

---

## Tuning

### POLL_INTERVAL

How often to check DHT for new snapshots (in seconds). Default: `600` (10 minutes).

```yaml
environment:
  POLL_INTERVAL: "3600"  # Check once per hour
```

### DHT_SALT

The DHT namespace. Default: `daily`. Using a different salt lets you operate a separate snapshot stream (e.g., `weekly`) alongside the default.

```yaml
environment:
  DHT_SALT: "weekly"
```

### LOG_LEVEL

```yaml
environment:
  LOG_LEVEL: DEBUG  # Verbose logging
```

---

## Updating the Container

```bash
# Rebuild a native image from a local clone using the repo's default authority key
./scripts/nano-mirror-build.sh

# Build and publish both supported platforms after authenticating to GHCR
BUILD_ID=local ./scripts/nano-mirror-build.sh --platform linux/amd64 --push
BUILD_ID=local ./scripts/nano-mirror-build.sh --platform linux/arm64 --push
BUILD_ID=local ./scripts/nano-mirror-publish.sh

# Pull latest published image
docker pull ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror:latest

# Restart with new image
docker compose down
docker compose up -d
```

---

## Troubleshooting

### "Signature verification FAILED"

The DHT returned a mutable item at your authority key, but the Ed25519 signature didn't verify. This means either:
- The item was placed by a different private key
- The data was tampered with in transit

The mirror rejects such items and retries on the next poll cycle.

### DHT discovery takes a long time

DHT bootstrap can take 5–15 minutes on a cold start, especially behind NAT. The 30-second bootstrap wait is intentionally conservative. Mirror downloads are P2P-only.

### "No peers"

`Attempting connection to seed peer ...` means libtorrent queued an asynchronous
connection request. It does not confirm a TCP connection or BitTorrent handshake.
`No peers` is based on torrent status and means that no usable torrent peer has
connected yet while the download is incomplete. The mirror queues another
configured seed-peer request after each 60-second no-peer warning during the
download. A queued request does not confirm a TCP connection or BitTorrent
handshake. The seed peer must be reachable on TCP port 6881.

### Download appears stuck at 0%

Check `num_peers: 0`. If the torrent has no reachable peers, the download cannot proceed. This can happen if:
- The torrent info-hash is not yet announced to any tracker (if trackers are used)

Use `--log-level DEBUG` and look for `alert` messages to understand what's happening.

### Volume Permissions

If you see errors about `/data` being unwritable:

```bash
sudo chown -R 1000:1000 ./nano-data
```

The container runs as UID 1000 by default.

---

## Stopping

```bash
docker compose down
# or
docker stop nano-mirror
```

The mirror handles `SIGTERM` gracefully and saves state before exiting.
