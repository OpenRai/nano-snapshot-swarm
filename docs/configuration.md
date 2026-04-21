# Configuration Reference

All environment variables, CLI flags, and Docker Compose options.

---

## Mirror: Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `AUTHORITY_PUBKEY` | — | **Yes** | 32-byte Ed25519 public key (hex, 64 chars) |
| `DATA_DIR` | `/data` | No | Directory for ledger data and state |
| `DHT_SALT` | `daily` | No | DHT mutable item salt namespace |
| `POLL_INTERVAL` | `600` | No | DHT poll interval in seconds (swarm mode only) |
| `WEB_SEED_URL` | `https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest` | No | Fallback HTTP/HTTPS URL for torrent web-seeds |
| `WEB_SEED_MODE` | `fallback` | No | Web seed policy: `fallback` or `off` |
| `LOG_LEVEL` | `INFO` | No | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Mirror: CLI Flags

```bash
python -m mirror.watcher [flags]
```

| Flag | Env | Default | Description |
|---|---|---|---|
| `--authority-pubkey` | `AUTHORITY_PUBKEY` | _(required)_ | Ed25519 public key hex |
| `--data-dir` | `DATA_DIR` | `/data` | Data directory |
| `--salt` | `DHT_SALT` | `daily` | DHT salt |
| `--poll-interval` | `POLL_INTERVAL` | `600` | Poll interval in seconds |
| `--web-seed-url` | `WEB_SEED_URL` | _(see above)_ | Web seed URL |
| `--web-seed-mode` | `WEB_SEED_MODE` | `fallback` | Web seed policy: `fallback` or `off` |
| `--log-level` | `LOG_LEVEL` | `INFO` | Log level |
| `--once` | — | `False` | Leech mode: download once then exit |
| `--download-timeout` | — | `0` | Download timeout in seconds (`0`=infinite; auto-3600 in `--once` mode) |

---

## Docker Compose Reference

```yaml
services:
  nano-mirror:
    image: ghcr.io/openrai/nano-p2p-mirror:latest
    environment:
      AUTHORITY_PUBKEY: "${AUTHORITY_PUBKEY:?AUTHORITY_PUBKEY is required}"
      SEED_PEERS: "${SEED_PEERS:-}"
      DATA_DIR: /data
      DHT_SALT: "${DHT_SALT:-daily}"
      POLL_INTERVAL: "${POLL_INTERVAL:-600}"
      WEB_SEED_URL: "${WEB_SEED_URL:-https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest}"
      WEB_SEED_MODE: "${WEB_SEED_MODE:-fallback}"
      LOG_LEVEL: "${LOG_LEVEL:-INFO}"
    volumes:
      - nano-data:/data          # Named volume (recommended)
      # OR: $(pwd)/data:/data   # Bind mount
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

volumes:
  nano-data:
```

### Override POLL_INTERVAL via CLI

```bash
docker compose run -e POLL_INTERVAL=60 nano-mirror
```

### Override Command for Leech Mode

```bash
docker compose run --rm nano-mirror --once --download-timeout 3600
```

---

## Producer: Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `NANO_LEDGER_PATH` | `/var/nano/data/data.ldb` | No | Path to live LMDB |
| `OUTPUT_DIR` | `.` | No | Output directory for `.zst` and `.torrent` files |
| `DHT_PRIVATE_KEY` | — | **Yes** | Ed25519 private key (hex, 64 chars) |
| `WEB_SEED_URL` | _(empty)_ | No | S3/HTTP URL added as web seed to torrent |
| `DHT_SALT` | `daily` | No | DHT salt namespace |

---

## Producer: CLI Flags

```bash
python -m producer.cli <command> [flags]
```

Common flags (available to all subcommands):

| Flag | Description |
|---|---|
| `--ledger-path` | Path to data.ldb (overrides `NANO_LEDGER_PATH`) |
| `--output-dir` | Output directory (overrides `OUTPUT_DIR`) |
| `--private-key` | Ed25519 private key hex (overrides `DHT_PRIVATE_KEY`) |
| `--web-seed-url` | Web seed URL (overrides `WEB_SEED_URL`) |
| `validation-fixture create --size` | Validation fixture source size (e.g. `1g`) |
| `validation-fixture publish --salt` | Validation DHT salt (default: `validation`) |
| `--piece-size` | Torrent piece size in bytes (default: 32 MiB) |
| `--state-file` | Path to publisher state file (default: `publisher_state.json`) |
| `--dry-run` | Create payload but don't publish to DHT |
| `--salt` | DHT salt (overrides `DHT_SALT`) |

Subcommands:

| Command | Description |
|---|---|
| `snapshot` | Extract ledger with `mdb_copy` and compress with `zstd` |
| `publish` | Create torrent and publish info-hash to DHT |
| `full` | Run snapshot then publish |

---

## mirror_state.json

Persisted in `DATA_DIR` by the mirror in swarm mode.

```json
{
  "last_seq": 42,
  "last_info_hash": "abcd1234...",
  "current_torrent_name": "nano-daily.ldb.zst",
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
