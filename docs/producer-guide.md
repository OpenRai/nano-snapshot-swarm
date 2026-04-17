# Producer Guide

Notes for running the authority side: generating keys, publishing snapshots, and scheduling.

---

## Prerequisites

- `mdb_copy` — from [LMDB](https://github.com/LMDB/lmdb), usually installed as `lmdb-utils` or `lmdb` package
- `zstd` — [Facebook zstd](https://facebook.github.io/zstd/), widely available
- Python 3.12+
- `uv` — [Astral uv](https://github.com/astral-sh/uv) package manager

```bash
# Install uv (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create and activate a virtual environment
uv venv .venv --python 3.12
source .venv/bin/activate

# Install Python dependencies
uv pip install pynacl bencodepy

# libtorrent is C++ and must be installed separately — see mirror/Dockerfile for build instructions
```

---

## Generating an Ed25519 Key Pair

The producer needs an Ed25519 key pair. The **private key** (hex) is used to sign DHT mutable items. The **public key** (hex) is what mirrors use as `AUTHORITY_PUBKEY`.

```bash
python3 -c "
from nacl.signing import SigningKey
sk = SigningKey.generate()
print(f'Private key (DHT_PRIVATE_KEY): {sk.encode().hex()}')
print(f'Public key  (AUTHORITY_PUBKEY):  {sk.verify_key.encode().hex()}')
"
```

Sample output:
```
Private key (DHT_PRIVATE_KEY): a06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d...
Public key  (AUTHORITY_PUBKEY):  77ff84905a91936367c01360803104f92432fcd904a43511876df5cdf3e7e548...
```

**Store the private key securely.** Never commit it, never log it, never share it. The public key is safe to share.

---

## Publishing Your Public Key

Mirrors need to know your public key through a trusted channel before they can verify your DHT items. Communicate it out-of-band (e.g., your website, a GitHub release note, a Discord announcement).

---

## Running the Snapshot Pipeline

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NANO_LEDGER_PATH` | `/var/nano/data/data.ldb` | Path to live LMDB |
| `OUTPUT_DIR` | `.` | Where to write `.zst` and `.torrent` files |
| `DHT_PRIVATE_KEY` | _(required)_ | Ed25519 private key hex |
| `WEB_SEED_URL` | _(empty)_ | S3/HTTP URL for web seeding |

### Step 1: Extract and Compress

```bash
export NANO_LEDGER_PATH=/var/nano/data/data.ldb
export OUTPUT_DIR=/opt/nano-snapshots

python -m producer.cli snapshot
# Writes: /opt/nano-snapshots/nano-daily.ldb.zst
```

### Step 2: Create Torrent and Publish to DHT

```bash
export DHT_PRIVATE_KEY=<your_64_char_hex_private_key>
export WEB_SEED_URL=https://s3.us-east-2.amazonaws.com/your-bucket/snapshots/

python -m producer.cli publish \
  --private-key "$DHT_PRIVATE_KEY" \
  --web-seed-url "$WEB_SEED_URL" \
  --output-dir /opt/nano-snapshots
```

Expected output:
```
Publisher identity: nano_...
DHT target ID (SHA-1): <target>
Publishing seq=1, info_hash=<info_hash>
Signature: <sig_hex>
Value size: N bytes
Waiting for DHT to bootstrap...
DHT put confirmed
Published seq=1 to DHT
```

### Full Pipeline (snapshot + publish)

```bash
python -m producer.cli full \
  --ledger-path /var/nano/data/data.ldb \
  --private-key "$DHT_PRIVATE_KEY" \
  --web-seed-url "$WEB_SEED_URL"
```

---

## Salt Convention

Use `--salt daily` (default) for daily snapshots or `--salt weekly` for weekly. Mirrors must use the matching `--salt` / `DHT_SALT` to discover your items.

Example for weekly snapshots:

```bash
python -m producer.cli publish \
  --private-key "$DHT_PRIVATE_KEY" \
  --salt weekly
```

And on the mirror:
```bash
docker run --rm -e AUTHORITY_PUBKEY=<pubkey> -e DHT_SALT=weekly ghcr.io/openrai/nano-p2p-mirror:latest --once
```

---

## Scheduling with Cron

Example crontab entry for daily snapshots at 04:00 UTC:

```cron
0 4 * * * cd /opt/nano-snapshot && \
  NANO_LEDGER_PATH=/var/nano/data/data.ldb \
  OUTPUT_DIR=/opt/nano-snapshots \
  DHT_PRIVATE_KEY=<your_key_hex> \
  WEB_SEED_URL=https://s3.us-east-2.amazonaws.com/your-bucket/snapshots/ \
  python -m producer.cli full >> /var/log/nano-snapshot.log 2>&1
```

---

## Security

- **Never commit `DHT_PRIVATE_KEY`** to git. Use environment variables or a secrets manager.
- The private key controls your snapshot stream. If compromised, rotate to a new key and update your `AUTHORITY_PUBKEY` in all mirrors.
- Logs contain your public key and DHT target ID but **never** the private key.

---

## Sequence Number

Each publish increments the sequence number. Mirrors use this to detect whether they have the latest snapshot. Do not manually edit `publisher_state.json` — the sequence number must monotonically increase.
