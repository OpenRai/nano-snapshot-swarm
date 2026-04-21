# Leech Mode: One-Shot Download

Leech mode downloads the latest snapshot once, verifies it, and exits. No daemon, no polling. Useful for:

- **CI/CD pipelines** — fetch the latest snapshot as part of a build
- **One-off sync** — get the ledger file without running a long-lived service
- **Testing** — quickly verify DHT discovery works with a given authority key

---

## Usage

### Docker Run (recommended)

```bash
export AUTHORITY_PUBKEY=<your_pubkey_hex>
docker run --rm \
  -e AUTHORITY_PUBKEY \
  -v $(pwd)/data:/data \
  ghcr.io/openrai/nano-p2p-mirror:latest \
  --once --web-seed-mode off
```

### With Custom Timeout

Default timeout is 3600 seconds (1 hour). If the download is expected to be faster or slower, override:

```bash
docker run --rm \
  -e AUTHORITY_PUBKEY \
  -v $(pwd)/data:/data \
  ghcr.io/openrai/nano-p2p-mirror:latest \
  --once --download-timeout 7200 --web-seed-mode off
```

### With Docker Compose Override

```bash
docker compose run --rm nano-mirror \
  --once --download-timeout 3600 --web-seed-mode off
```

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Download complete, now seeding |
| `1` | Failure — no DHT response, signature verification failed, download timed out, or error |

This makes leech mode easy to use in shell scripts:

```bash
if docker run --rm -e AUTHORITY_PUBKEY="$PUBKEY" \
    -v $(pwd)/data:/data \
    ghcr.io/openrai/nano-p2p-mirror:latest \
    --once --download-timeout 3600; then
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
  -e AUTHORITY_PUBKEY=<pubkey> \
  -v $(pwd)/data:/data \
  ghcr.io/openrai/nano-p2p-mirror:latest \
  --once --salt weekly
```

---

## How It Works

1. Waits 15 seconds for DHT to bootstrap (shorter than swarm mode since no polling is needed)
2. Queries DHT for mutable item under `AUTHORITY_PUBKEY` with given salt
3. On success: adds the torrent, begins P2P download
4. If `--web-seed-mode fallback` is enabled, libtorrent may use the configured web seed when peers are unavailable
5. Tracks progress every 5 seconds
6. On seeding complete: logs file path, exits `0`
7. On timeout or error: logs error, exits `1`

## Validation Stream Example

Use the dedicated validation salt for manual system validation:

```bash
docker run --rm \
  -e AUTHORITY_PUBKEY=<pubkey> \
  -e DHT_SALT=validation \
  -e WEB_SEED_MODE=off \
  -v $(pwd)/data:/data \
  ghcr.io/openrai/nano-p2p-mirror:latest \
  --once --download-timeout 3600
```

Leech mode also persists `mirror_state.json` in `DATA_DIR`, alongside the downloaded archive or
extracted `data.ldb`. This preserves the last discovered sequence, info-hash, torrent name, and
phase for inspection after the run exits.

---

## CI/CD Example: GitHub Actions

```yaml
- name: Download latest Nano ledger snapshot
  run: |
    docker run --rm \
      -e AUTHORITY_PUBKEY="${{ secrets.AUTHORITY_PUBKEY }}" \
      -v ${{ github.workspace }}/data:/data \
      ghcr.io/openrai/nano-p2p-mirror:latest \
      --once --download-timeout 7200

    echo " Ledger downloaded:"
    ls -lh data/*.ldb.zst

- name: Decompress and verify
  run: |
    zstd -d data/*.ldb.zst -o data.ldb
    mdb_copy data.ldb /tmp/verify_copy
    echo "Ledger verified OK"
```
