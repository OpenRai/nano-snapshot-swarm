# Configuration Reference

All environment variables, CLI flags, and Docker Compose options.

---

## Mirror: Environment Variables

The default OpenRAI mirror stream is baked into the published Docker image and stored in the repo root `AUTHORITY_PUBKEY` file for local builds and `uvx --from .` usage. Most mirror and leech users do not need to set `AUTHORITY_PUBKEY` unless they are following a different producer.

| Variable | Default | Required | Description |
|---|---|---|---|
| `AUTHORITY_PUBKEY` | baked into image / repo | No | 32-byte Ed25519 public key (hex, 64 chars); override only to follow a different producer |
| `DATA_DIR` | `/data` | No | Directory for ledger data and state |
| `DHT_SALT` | `daily` | No | DHT mutable item salt namespace |
| `POLL_INTERVAL` | `600` | No | DHT poll interval in seconds (swarm mode only) |
| `SEED_PEERS` | `bandwidth-martyr.openrai.org:6881` in the public image | No | Comma-separated explicit P2P peers for initial connection |
| `LOG_LEVEL` | `INFO` | No | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Mirror: CLI Flags

```bash
python -m mirror.watcher [flags]
```

| Flag | Env | Default | Description |
|---|---|---|---|
| `--authority-pubkey` | `AUTHORITY_PUBKEY` | baked into image / repo | Ed25519 public key hex |
| `--data-dir` | `DATA_DIR` | `/data` | Data directory |
| `--salt` | `DHT_SALT` | `daily` | DHT salt |
| `--poll-interval` | `POLL_INTERVAL` | `600` | Poll interval in seconds |
| `--log-level` | `LOG_LEVEL` | `INFO` | Log level |
| `--once` | — | `False` | Leech mode: download once then exit |
| `--download-timeout` | — | `1800` | Swarm-only inactivity timeout in seconds (`0`=never exit; ignored in `--once` mode) |

---

## Docker Compose Reference

```yaml
services:
  nano-mirror:
    image: ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror:latest
    environment:
      DATA_DIR: /data
      DHT_SALT: "${DHT_SALT:-daily}"
      POLL_INTERVAL: "${POLL_INTERVAL:-600}"
      SEED_PEERS: "${SEED_PEERS:-bandwidth-martyr.openrai.org:6881}"
      LOG_LEVEL: "${LOG_LEVEL:-INFO}"
    volumes:
      - ./nano-data:/data
    ports:
      - "${MIRROR_HOST_PORT:-6881}:6881/tcp"
      - "${MIRROR_HOST_PORT:-6881}:6881/udp"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python3", "-c", "import libtorrent; libtorrent.session({'enable_dht': False})"]
      interval: 60s
      timeout: 10s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

```

### Override POLL_INTERVAL via CLI

```bash
docker compose run -e POLL_INTERVAL=60 nano-mirror
```

### Follow a Different Producer

Only set `AUTHORITY_PUBKEY` if you want to follow a non-default snapshot stream:

```bash
docker run --rm \
  -e AUTHORITY_PUBKEY=<other_producer_pubkey> \
  -v ./nano-data:/data \
  ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror:latest \
  --once
```

---

## Producer: Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `OUTPUT_DIR` | `.` | No | Output directory for `.7z`, `.torrent`, and metadata files |
| `DHT_PRIVATE_KEY` | — | **Yes** | Ed25519 private key (hex, 64 chars) |
| `DHT_SALT` | `daily` | No | DHT salt namespace |
| `STATUS_API_URL` | _(empty)_ | No | URL of the status API to push snapshot metadata (e.g. `https://nano-snapshots.openrai.org`) |

---

## Producer: CLI Flags

```bash
python -m producer.cli publish [flags]
python -m producer.cli validation-fixture create [flags]
python -m producer.cli validation-fixture publish [flags]
```

#### `publish`

| Flag | Description |
|---|---|
| `--snapshot-file` | Path to the `.7z` archive to torrent (defaults to `OUTPUT_DIR/nano-ledger-snapshot.7z`) |
| `--output-dir` | Output directory for auto-detecting the snapshot file |
| `--private-key` | Ed25519 private key hex (overrides `DHT_PRIVATE_KEY`) |
| `--piece-size` | Torrent piece size in bytes (default: 32 MiB) |
| `--original-filename` | Original snapshot filename stored in torrent metadata |
| `--state-file` | Path to publisher state file (default: `publisher_state.json`) |
| `--dry-run` | Create torrent metadata but don't publish to DHT |
| `--salt` | DHT salt (overrides `DHT_SALT`) |

#### `push-status`

Push snapshot metadata and `.torrent` file to the status API after publishing.

| Flag | Description |
|---|---|
| `--status-api-url` | Status API base URL (overrides `STATUS_API_URL`) |
| `--private-key` | Ed25519 private key hex (overrides `DHT_PRIVATE_KEY`) |
| `--state-file` | Path to publisher state file (default: `publisher_state.json`) |
| `--meta-file` | Path to snapshot metadata file (default: `snapshot-meta.json`) |
| `--torrent-file` | Path to `.torrent` file |
| `--snapshot-file` | Path to `.7z` snapshot file |
| `--torrent-name` | Torrent name/filename (default: `nano-ledger-snapshot.7z`) |
| `--piece-size` | Torrent piece size in bytes (default: 32 MiB) |

#### `validation-fixture create`

| Flag | Description |
|---|---|
| `--output-dir` | Output directory for validation fixture files |
| `--archive-name` | Validation archive filename |
| `--size` | Uncompressed random source size (e.g. `1g`) |
| `--force` | Overwrite existing files |
| `--keep-source` | Keep the uncompressed source file after archive creation |

#### `validation-fixture publish`

| Flag | Description |
|---|---|
| `--output-dir` | Directory containing the validation archive |
| `--archive-name` | Validation archive filename |
| `--private-key` | Ed25519 private key hex (overrides `DHT_PRIVATE_KEY`) |
| `--piece-size` | Torrent piece size in bytes (default: 32 MiB) |
| `--state-file` | Path to validation publisher state file |
| `--dry-run` | Create torrent metadata but don't publish to DHT |
| `--salt` | Validation DHT salt (default: `validation`) |

---

## mirror_state.json

Persisted in `DATA_DIR` by the mirror in swarm mode.

```json
{
  "last_seq": 42,
  "last_info_hash": "abcd1234...",
  "current_torrent_name": "nano-ledger-snapshot.7z",
  "phase": "seeding",
  "last_error": ""
}
```

Mirrors use `last_seq` and `last_info_hash` to determine if a newly discovered item is newer than what they already have.

### Validation Fixture Workflow

Create a 1 GiB synthetic validation artifact:

```bash
python -m producer.cli validation-fixture create --output-dir ./validation --size 1g
```

Publish it under a separate DHT namespace:

```bash
python -m producer.cli validation-fixture publish \
  --output-dir ./validation \
  --private-key "$DHT_PRIVATE_KEY" \
  --salt validation
```

---

## publisher_state.json

Persisted by the producer in the output directory.

```json
{
  "last_seq": 42,
  "last_info_hash": "abcd1234..."
}
```

The sequence number is incremented on each publish. Do not edit this file manually.

---

## DHT Internals

| Setting | Value | Description |
|---|---|---|
| Bootstrap nodes | `router.bittorrent.com:6881`, `router.utorrent.com:6881`, `dht.transmissionbt.com:6881` | Public DHT bootstrap nodes |
| DHT timeout | 120s per attempt | How long to wait for a DHT response |
| DHT retries | 3 attempts | With backoff: 10s, 30s, 60s |
| Leech bootstrap wait | 15s | Short wait for DHT to come up |
| Swarm bootstrap wait | 30s | Longer wait for full DHT mesh |
| Default DHT port | 6881 | TCP and UDP |

The status API optionally adds a direct peer hint to generated magnets when
`MAGNET_PEER_HOST` is configured. Production sets it to
`bandwidth-martyr.openrai.org` with port `6881`; leave the host empty to omit
the `x.pe` parameter. DHT bootstrap nodes listed above are client/DHT settings,
not `tr=` tracker entries in the generated magnet.
