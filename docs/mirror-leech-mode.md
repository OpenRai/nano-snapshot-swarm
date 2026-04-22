# Leech Mode: One-Shot Download

Leech mode downloads the latest snapshot once, verifies it, and exits. No daemon, no polling. It is the simplest way to get a fresh snapshot archive for a Nano node. Useful for:

- **CI/CD pipelines** — fetch the latest snapshot as part of a build
- **One-off sync** — get the ledger file without running a long-lived service
- **Testing** — quickly verify DHT discovery works

The published Docker image already has the current OpenRAI producer public key baked in. You only need to set `AUTHORITY_PUBKEY` if you want to follow a different producer.

---

## Usage

### Docker Run (recommended)

```bash
docker run --rm \
  -v $(pwd)/data:/data \
  ghcr.io/openrai/nano-p2p-mirror:latest \
  --once
```

### uvx

```bash
read -r AUTHORITY_PUBKEY < AUTHORITY_PUBKEY
export AUTHORITY_PUBKEY
uvx --from . nano-mirror --once
```

That `uvx` snippet is only needed when running from a local git clone, because the source tree keeps the default producer key in the repo root `AUTHORITY_PUBKEY` file rather than hardcoding it into shell examples.

### With Custom Timeout

Leech mode has no wall-clock download timeout. `--download-timeout` only applies to swarm mode DHT inactivity, so there is nothing to tune here for `--once`.

### With Docker Compose Override

```bash
docker compose run --rm nano-mirror \
  --once
```

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Download complete, archive saved |
| `1` | Failure — no DHT response, signature verification failed, or other error |

This makes leech mode easy to use in shell scripts:

```bash
if docker run --rm \
    -v $(pwd)/data:/data \
    ghcr.io/openrai/nano-p2p-mirror:latest \
    --once; then
  echo "Download succeeded"
  # decompress and use the ledger
else
  echo "Download failed"
fi
```

---

## Where the File Ends Up

The downloaded file is saved to `DATA_DIR` (default `/data`) using the torrent's filename, typically:

```
/data/nano-daily.ldb.zst
```

---

## Decompressing the Ledger

```bash
zstd -d /data/nano-daily.ldb.zst -o /tmp/data.ldb
# Verify it opens with mdb_copy
mdb_copy /tmp/data.ldb /tmp/data_copy
```

For the official Nano node Docker image, place the resulting `data.ldb` into the host directory you mount at `/root` inside the node container. Nano's Docker docs describe the node data directory as the path bound with `-v`/`--volume`, and they recommend keeping that directory persistent instead of treating the ledger as disposable.

As of 2026-04, unpacking needs roughly `{compressed size} + {2 * compressed size}` GB of temporary space, so a ~60 GB archive means about ~180 GB free while decompressing.

---

## Replacing Your Nano Node's Ledger

```bash
# Stop your Nano node
sudo systemctl stop nano

# Backup existing ledger
sudo mv /var/nano/data/data.ldb /var/nano/data/data.ldb.backup

# Replace with new ledger
sudo cp /path/to/downloaded/data.ldb /var/nano/data/data.ldb

# Fix permissions
sudo chown nano:nano /var/nano/data/data.ldb

# Restart
sudo systemctl start nano
```

**Warning**: Always verify the ledger opens correctly with `mdb_copy` before replacing the live database.

---

## Using a Custom Salt

If the producer publishes to a non-default DHT salt:

```bash
docker run --rm \
  -v $(pwd)/data:/data \
  ghcr.io/openrai/nano-p2p-mirror:latest \
  --once --salt weekly
```

---

## How It Works

1. Waits 15 seconds for DHT to bootstrap (shorter than swarm mode since no polling is needed)
2. Queries DHT for the latest mutable item under the configured authority key and salt
3. On success: adds the torrent, begins P2P download
4. If `--web-seed-mode fallback` is enabled, libtorrent may use the configured web seed when peers are unavailable
5. Tracks progress every 5 seconds
6. On seeding complete: logs file path, exits `0`
7. On error: logs error, exits `1`

## Validation Stream Example

Use the dedicated validation salt for manual system validation:

```bash
docker run --rm \
  -e DHT_SALT=validation \
  -e WEB_SEED_MODE=off \
  -v $(pwd)/data:/data \
  ghcr.io/openrai/nano-p2p-mirror:latest \
  --once
```

Leech mode also persists two local metadata files in `DATA_DIR`, alongside the downloaded archive or
extracted `data.ldb`:

- `mirror_state.json` records the last discovered sequence, info-hash, torrent name, and phase.
- `snapshot-meta.json` records the authority pubkey, DHT signature, torrent info-hash, and original upstream `.7z` filename once discovery and torrent metadata resolution complete.

---

## CI/CD Example: GitHub Actions

```yaml
- name: Download latest Nano ledger snapshot
  run: |
    docker run --rm \
      -v ${{ github.workspace }}/data:/data \
      ghcr.io/openrai/nano-p2p-mirror:latest \
      --once

    echo " Ledger downloaded:"
    ls -lh data/*.ldb.zst

- name: Decompress and verify
  run: |
    zstd -d data/*.ldb.zst -o data.ldb
    mdb_copy data.ldb /tmp/verify_copy
    echo "Ledger verified OK"
```
