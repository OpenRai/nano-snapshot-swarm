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

### Option A: Derive from your Nano secret key (recommended)

Your Nano secret key (64-char hex, 32 bytes) is already an Ed25519 seed. The same seed produces both your Nano address and the BEP 46 signing key.

```bash
cd /opt/nano-bootstrap-swarm
.venv/bin/pip install nano_lib_py
.venv/bin/python3 -c "
import getpass
from nano_lib_py import get_account_id, get_account_key_pair

NANO_SECRET = getpass.getpass('Enter Nano secret key: ').strip()
print(f'Nano address: {get_account_id(private_key=NANO_SECRET)}')
kp = get_account_key_pair(NANO_SECRET)
print(f'DHT_PRIVATE_KEY: {NANO_SECRET}')
print(f'AUTHORITY_PUBKEY:  {kp.public}')
"
```

This uses the same Ed25519-Blake2b seed expansion as `nano-vanity`, so the address will match exactly.

### Option B: Generate a fresh random key

```bash
cd /opt/nano-bootstrap-swarm
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

This file is read by the systemd service via `EnvironmentFile=-/home/openrai/.env` and by the daily script when run manually (not via systemd).

---

## Running the Snapshot Pipeline

### Automated (systemd timer)

The production pipeline runs automatically via systemd. See [Scheduling with systemd](#scheduling-with-systemd) below.

### Manual ad-hoc run

```bash
cd /opt/nano-bootstrap-swarm
source .venv/bin/activate
if [ -z "$DHT_PRIVATE_KEY" ] && [ -f /home/openrai/.env ]; then
    source /home/openrai/.env
fi

python -m producer.cli full \
  --ledger-path /var/nano/data/data.ldb \
  --private-key "$DHT_PRIVATE_KEY" \
  --web-seed-url https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest
```

### Individual steps (advanced)

```bash
# Step 1: Extract and compress
export NANO_LEDGER_PATH=/var/nano/data/data.ldb
export OUTPUT_DIR=/opt/nano-snapshots
python -m producer.cli snapshot

# Step 2: Create torrent and publish to DHT
source /home/openrai/.env
python -m producer.cli publish \
  --private-key "$DHT_PRIVATE_KEY" \
  --web-seed-url https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest \
  --output-dir /opt/nano-snapshots
```

Expected publish output:
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

## Scheduling with systemd

Snapshots run automatically via a **user-level** systemd timer on the producer server.

**Unit files:** Symlinked from `systemd/` in this repo to `~/.config/systemd/user/`.

**Schedule:** Hourly, with up to 5 minutes of random jitter and `Persistent=true` (catches up if the server was offline).

**Credentials:** The service reads `/home/openrai/.env` (EnvironmentFile), so keys are never in the unit file itself.

**Pipeline steps:** The timer invokes `/opt/nano-bootstrap-swarm/scripts/daily-snapshot.sh`, which downloads from S3, extracts, compacts with `mdb_copy`, compresses with `zstd --rsyncable`, then runs `producer.cli publish`.

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

## Security

- **Never commit `DHT_PRIVATE_KEY`** to git. Use environment variables or a secrets manager.
- The private key controls your snapshot stream. If compromised, rotate to a new key and update your `AUTHORITY_PUBKEY` in all mirrors.
- Logs contain your public key and DHT target ID but **never** the private key.

---

## Sequence Number

Each publish increments the sequence number. Mirrors use this to detect whether they have the latest snapshot. Do not manually edit `publisher_state.json` — the sequence number must monotonically increase.
