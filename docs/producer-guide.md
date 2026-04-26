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
uv pip install pynacl bencodepy nano_lib_py

# libtorrent is C++ and must be installed separately — see mirror/Dockerfile for build instructions
```

---

## Generating an Ed25519 Key Pair

The producer needs an Ed25519 key pair. The **private key** (hex) is used to sign DHT mutable items. The **public key** (hex) is what mirrors use as `AUTHORITY_PUBKEY`.

Important: the BEP 46 / libtorrent keypair uses standard Ed25519 derivation. A Nano account address uses Nano's Ed25519-Blake2b derivation. Reusing the same 32-byte secret across both systems does not make the DHT public key numerically equal to the Nano account public key.

### Option A: Reuse a 32-byte secret you already control

If you already have a 32-byte secret, you can reuse it as `DHT_PRIVATE_KEY`. The helper below derives the corresponding DHT public key that mirrors must follow:

```bash
cd /opt/nano-snapshot-swarm
uv pip install nano_lib_py
.venv/bin/python3 -c "
import getpass
from nacl.signing import SigningKey

secret = getpass.getpass('Enter 32-byte secret key hex: ').strip()
sk = SigningKey(bytes.fromhex(secret))
print(f'DHT_PRIVATE_KEY: {secret}')
print(f'AUTHORITY_PUBKEY: {sk.verify_key.encode().hex()}')
"
```

If that same secret also controls a Nano account, treat that as an operational convenience only. The mirror follows the DHT public key, not your Nano account address.

### Option B: Generate a fresh random key

```bash
cd /opt/nano-snapshot-swarm
.venv/bin/python3 -c "
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

## Credentials

The private key lives on the producer server only. Mirror operators need only the public key.

On the server, credentials are stored in `/home/openrai/.env` (mode 600, owned by `openrai`):

```
DHT_PRIVATE_KEY=<your_64_char_hex_private_key>
AUTHORITY_PUBKEY=<your_32_char_hex_public_key>
NANO_LEDGER_PATH=/var/nano/data/data.ldb
OUTPUT_DIR=/opt/nano-snapshots
```

This file is read by the systemd service via `EnvironmentFile=-/home/openrai/.env` and by `scripts/daily-snapshot.sh` when run manually.

---

## Running the Snapshot Pipeline

### Automated (systemd timer)

The production pipeline runs automatically via systemd. See [Scheduling with systemd](#scheduling-with-systemd) below.

### Manual ad-hoc run

```bash
cd /opt/nano-snapshot-swarm
source .venv/bin/activate
if [ -z "$DHT_PRIVATE_KEY" ] && [ -f /home/openrai/.env ]; then
    source /home/openrai/.env
fi

./scripts/daily-snapshot.sh
```

### Individual steps (advanced)

```bash
# Create and publish a torrent for an existing .7z snapshot
source /home/openrai/.env
python -m producer.cli publish \
  --private-key "$DHT_PRIVATE_KEY" \
  --snapshot-file /opt/nano-snapshots/nano-ledger-snapshot.7z \
  --web-seed-url https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest \
  --output-dir /opt/nano-snapshots
```

Expected publish output:
```
Publisher DHT pubkey (Nano-format): nano_...
DHT target ID (SHA-1): <target>
Publishing seq=1, info_hash=<info_hash>
Signature: <sig_hex>
Value size: N bytes
Waiting for DHT to bootstrap...
DHT put confirmed
Published seq=1 to DHT
```

---

## Salt Convention

Use `--salt daily` (default) for the main stream or `--salt weekly` for a separate stream. Mirrors must use the matching `--salt` / `DHT_SALT` to discover your items.

Example for a separate stream:

```bash
python -m producer.cli publish \
  --private-key "$DHT_PRIVATE_KEY" \
  --snapshot-file /opt/nano-snapshots/nano-ledger-snapshot.7z \
  --salt weekly
```

And on a mirror following that separate stream:
```bash
docker run --rm -e AUTHORITY_PUBKEY=<pubkey> -e DHT_SALT=weekly ghcr.io/openrai/nano-p2p-mirror:latest --once
```

For the default OpenRAI stream, the published mirror image already has the current producer public key baked in, so mirror and leech users do not need to set `AUTHORITY_PUBKEY`.

---

## Scheduling with systemd

Snapshots run automatically via a **user-level** systemd timer on the producer server.

**Unit files:** Symlinked from `systemd/` in this repo to `~/.config/systemd/user/`.

**Schedule:** Hourly, with up to 5 minutes of random jitter and `Persistent=true` (catches up if the server was offline).

**Credentials:** The service reads `/home/openrai/.env` (EnvironmentFile), so keys are never in the unit file itself.

**Pipeline steps:** The timer invokes `/opt/nano-snapshot-swarm/scripts/daily-snapshot.sh`, which resolves the latest `.7z` archive from the web seed, downloads it, validates it, writes provenance metadata, and publishes the torrent info-hash to DHT.

```bash
# Check timer status
systemctl --user status nano-snapshot.timer
systemctl --user list-timers nano-snapshot

# View live logs
journalctl --user -u nano-snapshot -f

# Manual trigger (e.g., after server downtime)
systemctl --user start nano-snapshot.service

# The pipeline log is also written to:
# /opt/nano-snapshots/nano-snapshot.log
```

The service runs with `TimeoutStopSec=3600` (1 hour) to accommodate large downloads.

---

## Status API Deployment

The **Status API** is a lightweight Fly.io service that makes your snapshot stream discoverable without requiring users to run the Mirror client. It receives signed pushes from the Producer and serves JSON metadata, `.torrent` files, and an SSR dashboard.

### Architecture

```
Producer ──HTTPS signed push──► Fly.io: nano-snapshot-hub
                                     ├── GET /api/status   (JSON)
                                     ├── GET /api/torrent  (.torrent file)
                                     ├── GET /             (SSR dashboard)
                                     └── /data volume      (persistent)
                                          ▼
                                   Cloudflare CDN cache
                                          ▼
                              GitHub Pages static dashboard
```

### Deploy the Status API

See the full runbook at `status-api/deploy/fly.io/README.md`. Quick start:

```bash
cd status-api

# One-time setup
fly apps create nano-snapshot-hub
fly volumes create status_data --size 1 --region sjc --app nano-snapshot-hub

# Deploy
fly deploy
```

The `fly.toml` and `Dockerfile` live directly in `status-api/` (the service root). The checked-in config already embeds the OpenRAI `AUTHORITY_PUBKEY`, so no env vars are needed at runtime.

### Producer Configuration

Add `STATUS_API_URL` to the producer's `~/.env`:

```bash
STATUS_API_URL=https://nano-snapshot-hub.fly.dev
```

The `daily-snapshot.sh` pipeline will then push after every DHT publish. Push failures are non-fatal.

You can also push manually or via systemd:

```bash
# Immediate manual push
./scripts/push-snapshot-status.sh

# Or via the dedicated systemd timer
systemctl --user start nano-status-push.service
systemctl --user enable nano-status-push.timer
```

### Cloudflare Caching (Recommended)

Place Cloudflare in front of the Fly app to cache `.torrent` files and status JSON at the edge. See `status-api/deploy/fly.io/README.md` §5 for exact DNS and cache-rule settings.

Expected Fly.io cost: **under $5/month** (mostly idle 256 MB VM + 1 GB volume).

---

## Security

- **Never commit `DHT_PRIVATE_KEY`** to git. Use environment variables or a secrets manager.
- The private key controls your snapshot stream. If compromised, rotate to a new key and update your `AUTHORITY_PUBKEY` in all mirrors.
- Logs contain your DHT public key and DHT target ID but **never** the private key.

---

## Sequence Number

Each publish increments the sequence number. Mirrors use this to detect whether they have the latest snapshot. Do not manually edit `publisher_state.json` — the sequence number must monotonically increase.
