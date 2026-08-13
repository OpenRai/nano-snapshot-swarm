# Producer Guide

Notes for running the authority side: generating keys, publishing snapshots, and scheduling.

---

## Prerequisites

- `aria2c` — resumable upstream archive download
- `7z` — archive inspection for the status push and optional validation fixtures
- Python 3.12+
- `uv` — [Astral uv](https://github.com/astral-sh/uv) package manager

```bash
# Install uv (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create and activate a virtual environment
uv venv .venv --python 3.12
source .venv/bin/activate

# Install the project dependencies
uv sync --extra dev
```

---

## Generating an Ed25519 Key Pair

The producer needs an Ed25519 key pair. The **private key** (hex) is used to sign DHT mutable items. The **public key** (hex) is what mirrors use as `PRODUCER_SIGNING_PUBKEY`.

Important: the DHT signer uses standard Ed25519 derivation. A Nano account uses
Nano's Ed25519-Blake2b derivation. Reusing one 32-byte secret does not make the
two public keys numerically equal.

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
print(f'PRODUCER_SIGNING_PUBKEY: {sk.verify_key.encode().hex()}')
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
print(f'Public key  (PRODUCER_SIGNING_PUBKEY):  {sk.verify_key.encode().hex()}')
"
```

The command prints a 64-character private key and a 64-character public key:
```
Private key (DHT_PRIVATE_KEY): <64 hex characters>
Public key  (PRODUCER_SIGNING_PUBKEY): <64 hex characters>
```

**Store the private key securely.** Never commit it, never log it, never share it. The public key is safe to share.

---

## Credentials

The private key lives on the producer server only. Mirror operators need only the public key.

On the server, credentials are stored in `/home/openrai/.env` (mode 600, owned by `openrai`):

```
DHT_PRIVATE_KEY=<your_64_char_hex_private_key>
# The seeder logs the derived PRODUCER_SIGNING_PUBKEY at startup. Copy that 64-character value into mirrors or a Status API for this producer; do not set it here.
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

### Placeholder producer test mode

Before launch, the complete producer-to-mirror update path can be tested without
downloading the full upstream archive. Add this temporary setting to
`/home/openrai/.env`:

```
USE_PLACEHOLDER_SNAPSHOT=1
```

Then trigger one normal user service run:

```bash
systemctl --user start nano-snapshot.service
journalctl --user -u nano-snapshot -f
```

The pipeline creates a fresh timestamped `nano-ledger-snapshot-*.7z` payload of
exactly 128 MiB, links it to the canonical `nano-ledger-snapshot.7z` name, and
continues through torrent creation, verified signed DHT publication, seeder reload, and
status push. The payload is intentionally not a valid 7z archive; this mode
tests transfer, mutation, recheck, and seeding behavior.

Remove the setting, or change it to `USE_PLACEHOLDER_SNAPSHOT=0`, immediately
after testing so the hourly timer returns to downloading real snapshots.

For the mirror acceptance test, run one mirror container in swarm mode with a
short `POLL_INTERVAL`. Confirm that it finishes the first placeholder download
and remains seeding. Trigger another producer service run, then verify without
restarting the mirror that its logs show the higher DHT sequence, changed info
hash, replacement, metadata recheck, resumed piece requests, and return to
seeding. Also verify that the mirror process uptime is continuous and that
`mirror_state.json` and `snapshot-meta.json` contain the new hash. A completed
download alone does not prove peer-sourced bytes; inspect peer/source counters
when recording P2P evidence.

### Individual steps (advanced)

```bash
# Create and publish a torrent for an existing .7z snapshot
source /home/openrai/.env
python -m producer.cli publish \
  --private-key "$DHT_PRIVATE_KEY" \
  --snapshot-file /opt/nano-snapshots/nano-ledger-snapshot.7z \
  --output-dir /opt/nano-snapshots
```

The `.torrent` contains BitTorrent metadata. The producer publishes the signed
raw-v2 DHT pointer separately. See [Torrent and Magnet Format](torrent-format.md)
for the hybrid hashes, DHT value, and magnet contract.

Expected publish output:
```
Verification using PRODUCER_SIGNING_PUBKEY: <64-char hex>
DHT mutable-item target ID (SHA-1): <short target>...
Publishing snapshot: publisher status sequence=1, torrent v2 info hash=<short hash>...
Value size: N bytes
Waiting for DHT to bootstrap...
DHT mutable-item put completed: sequence=N, direct acknowledgements=N
DHT mutable item verified: sequence=N, torrent v2 info hash=<short hash>...
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
docker run --rm -e PRODUCER_SIGNING_PUBKEY=<pubkey> -e DHT_SALT=weekly ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror:latest --once
```

For the default OpenRAI stream, the published mirror image already has the current producer public key baked in, so mirror and leech users do not need to set `PRODUCER_SIGNING_PUBKEY`.

---

## Scheduling with systemd

Snapshots run automatically via a **user-level** systemd timer on the producer server.

**Unit files:** Symlinked from `systemd/` in this repo to `~/.config/systemd/user/`.

**Schedule:** Hourly, with up to 5 minutes of random jitter and `Persistent=true` (catches up if the server was offline).

**Credentials:** The service reads `/home/openrai/.env` (EnvironmentFile), so keys are never in the unit file itself.

**Pipeline steps:** The timer invokes `/opt/nano-snapshot-swarm/scripts/daily-snapshot.sh`, which retrieves the latest upstream `.7z` archive, validates it, writes local publisher metadata, and publishes the torrent v2 info-hash to DHT. The upstream URL is not distributed to mirrors or embedded in the torrent. Set `SNAPSHOT_RETENTION=N` to retain and seed the last `N` prior canonical archive-plus-torrent pairs; the default `0` keeps only the current snapshot.

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

The pipeline unit allows 12 hours to start and finish. Its stop timeout is five
minutes. Do not stop it during an archive download unless you intend to resume
that download later.

---

## Status API Deployment

The **Status API** is a lightweight Fly.io service that makes your snapshot stream discoverable without requiring users to run the Mirror client. It receives signed pushes from the Producer and serves JSON metadata, `.torrent` files, and an SSR dashboard.

### Architecture

```
Producer ──HTTPS signed push──► Fly.io: nano-snapshot-hub
                                     ├── GET /api/status   (JSON)
                                     ├── GET /api/torrent  (latest redirect)
                                     ├── GET /api/torrents/{v2}/{name}.torrent
                                     ├── GET /api/latest.magnet
                                     ├── GET /             (SSR dashboard)
                                     └── /data volume      (persistent)
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

The `fly.toml` and `Dockerfile` live directly in `status-api/` (the service root). The checked-in config already embeds the OpenRAI `PRODUCER_SIGNING_PUBKEY`, so no env vars are needed at runtime.

### Producer Configuration

Add `STATUS_API_URL` to the producer's `~/.env`:

```bash
STATUS_API_URL=https://nano-snapshot-hub.fly.dev
```

The `daily-snapshot.sh` pipeline will then push after every DHT publish. Push failures are non-fatal. The public dashboard is available at `https://nano-snapshots.openrai.org`; use the direct Fly hostname for producer pushes unless Cloudflare explicitly permits `POST /api/push`.

You can also push manually or via systemd:

```bash
# Immediate manual push
./scripts/push-snapshot-status.sh

# Or via the dedicated systemd timer
systemctl --user start nano-status-push.service
systemctl --user enable nano-status-push.timer
```

### Cloudflare Caching (Recommended)

Place Cloudflare in front of the Fly app to cache immutable `.torrent` files at the edge. Keep the dashboard and live status routes bypassed so a reload cannot combine responses from different snapshot sequences. See `status-api/deploy/fly.io/README.md` §5 for exact DNS and cache-rule settings.

Fly.io charges and included allowances can change. Review the current Fly.io
pricing before creating the app or increasing capacity.

---

## Security

- **Never commit `DHT_PRIVATE_KEY`** to git. Use environment variables or a secrets manager.
- The private key controls your snapshot stream. If compromised, rotate to a new key and update your `PRODUCER_SIGNING_PUBKEY` in all mirrors.
- Logs contain your DHT public key and DHT target ID but **never** the private key.

---

## Sequence Number

The DHT mutable-item sequence is chosen by libtorrent from the current network
value and is verified by reading the signed item back. This is the sole public
sequence: it is shown by the Status API/dashboard and compared by mirrors.
`publisher_state.json` also contains a local revision counter for producer
bookkeeping, but it is not exposed to operators and must not be manually edited.
A publication is not pushed to the dashboard until the exact DHT value has been
verified and the seeder reports the matching torrent as loaded.

Producer read-back requires libtorrent's authoritative mutable-item response.
The first signed response from a DHT lookup may be an older network view, so it
must not be used to confirm a new publication.

The daily pipeline restarts an active `nano-seed.service` to load the canonical
torrent. This is safe even if the service is still bootstrapping; graceful
shutdown saves its DHT and resume state, while retained swarms stay on disk. If
the seeder is stopped, the pipeline starts it. The normal pipeline creates the
torrent first and defers the only DHT put to that long-lived seeder. The seeder
updates `publisher_state.json` only after authoritative verification, so the
dashboard cannot be advanced by a separate short-lived publisher race.

`seeder-stats.json` also exposes `dht_direct_acknowledgements`,
`dht_publish_attempt`, `dht_last_error`, and `seeder_ready`. The acknowledgement
count describes direct responses only; `dht_verified` and the matching hash are
the readiness conditions.
