# Manual End-to-End Validation

This guide documents a full manual validation run using:

- a **remote producer/seeder** on `bandwidth-martyr`
- a **local mirror** in Docker on your macOS machine
- a separate DHT namespace via `DHT_SALT=validation`
- **P2P only** transfer with `WEB_SEED_MODE=off`

It is intended to validate the real system behavior without touching production systemd units beyond a temporary stop/start window.

---

## What This Validates

This procedure proves all of the following in one run:

1. The producer can create and publish a synthetic validation artifact.
2. The validation artifact is isolated from production by using a separate DHT salt.
3. A local mirror can discover the validation stream via DHT.
4. The mirror can complete a one-shot download via **P2P only**.
5. Production services can be stopped and restored cleanly after the test.

---

## Validation Settings

Use these values consistently during the run:

```bash
AUTHORITY_PUBKEY=b9036c6c3d05d70b9d8b3e255b15cec8f962e88e36ebaeb96232c08c1cf163f3
DHT_SALT=validation
WEB_SEED_MODE=off
```

Recommended artifact size for manual validation:

```bash
256m
```

This is large enough to exercise real BitTorrent behavior, but small enough to complete quickly.

---

## Step 1: Stop Production Services

On the remote server, stop the production mirror and producer services so the validation run is isolated:

```bash
ssh bandwidth-martyr

systemctl --user stop nano-seed.service nano-snapshot.timer nano-snapshot.service 2>/dev/null || true

cd /opt/nano-bootstrap-swarm
docker compose down
```

Verify they are stopped:

```bash
systemctl --user is-active nano-seed.service nano-snapshot.timer nano-snapshot.service 2>/dev/null || true
docker ps --format '{{.Names}} {{.Status}}'
```

Expected result:

```text
inactive
inactive
inactive
```

---

## Step 2: Create the Validation Artifact

Still on the remote server:

```bash
cd /opt/nano-bootstrap-swarm

./.venv/bin/python -m producer.cli validation-fixture create \
  --output-dir /home/openrai/nano-validation \
  --size 256m \
  --force
```

Expected output includes:

```json
{
  "archive_path": "/home/openrai/nano-validation/nano-validation-snapshot.7z",
  "salt": "validation"
}
```

---

## Step 3: Publish the Validation Artifact

Publish under the separate `validation` salt using a dedicated validation publisher state file:

```bash
cd /opt/nano-bootstrap-swarm

DHT_PRIVATE_KEY=$(grep '^DHT_PRIVATE_KEY=' ~/.env | cut -d= -f2-)

./.venv/bin/python -m producer.cli validation-fixture publish \
  --output-dir /home/openrai/nano-validation \
  --private-key "$DHT_PRIVATE_KEY" \
  --salt validation \
  --state-file /home/openrai/nano-validation/publisher_state.validation.json
```

Expected output includes:

1. a new torrent info-hash
2. `salt='validation'`
3. `confirmed: true`

---

## Step 4: Start a Temporary Validation Seeder

The existing long-lived seeder is hardcoded to `nano-ledger-snapshot.7z`, so create symlinks that match its expected filenames.

On the remote server:

```bash
python3 - <<'PY'
from pathlib import Path
import json

base = Path('/home/openrai/nano-validation')
archive = base / 'nano-validation-snapshot.7z'
torrent = base / 'nano-validation-snapshot.7z.torrent'

(base / 'nano-ledger-snapshot.7z').unlink(missing_ok=True)
(base / 'nano-ledger-snapshot.7z.torrent').unlink(missing_ok=True)

(base / 'nano-ledger-snapshot.7z').symlink_to(archive)
(base / 'nano-ledger-snapshot.7z.torrent').symlink_to(torrent)

# Replace the info hash below with the one printed by the publish step.
(base / 'snapshot-meta.json').write_text(json.dumps({
    'torrent_info_hash': '<PASTE_INFO_HASH_HERE>'
}, indent=2) + '\n')
PY
```

Start a temporary seeder with `nohup`:

```bash
cd /opt/nano-bootstrap-swarm

nohup env \
  OUTPUT_DIR=/home/openrai/nano-validation \
  DHT_SALT=validation \
  DHT_PRIVATE_KEY=$(grep '^DHT_PRIVATE_KEY=' ~/.env | cut -d= -f2-) \
  LOG_LEVEL=INFO \
  ./.venv/bin/python producer/seeder.py \
  >/home/openrai/nano-validation/validation-seeder.log 2>&1 < /dev/null &
```

Check the seeder log:

```bash
sed -n '1,80p' /home/openrai/nano-validation/validation-seeder.log
```

Expected lines include:

```text
Seeding: /home/openrai/nano-validation/nano-ledger-snapshot.7z
Torrent added, seeding...
DHT publish: info_hash=... salt='validation'
```

---

## Step 5: Build the Local Mirror Image

On your local macOS machine:

```bash
cd /Users/conny/Developer/nano/OpenRai/nano-bootstrap-swarm

docker build -f mirror/Dockerfile -t nano-bootstrap-mirror-validation .
```

---

## Step 6: Run the Local Mirror in One-Shot Mode

Use a local workspace under `/tmp`:

```bash
rm -rf "/tmp/nano-validation-mirror"
mkdir -p "/tmp/nano-validation-mirror"
```

Run the mirror:

```bash
docker run --rm \
  --name nano-validation-mirror \
  -e AUTHORITY_PUBKEY="b9036c6c3d05d70b9d8b3e255b15cec8f962e88e36ebaeb96232c08c1cf163f3" \
  -e DHT_SALT="validation" \
  -e WEB_SEED_MODE="off" \
  -v "/tmp/nano-validation-mirror:/data" \
  nano-bootstrap-mirror-validation \
  --once --download-timeout 1800
```

Expected successful log sequence:

```text
Querying DHT for mutable item ... salt: 'validation'
Discovered DHT item: seq=1, info_hash=...
Torrent metadata resolved: nano-validation-snapshot.7z
Download: ... | State: downloading | ... | Peers: 1
Snapshot seeding complete: ...
Leecher: download complete, seeding. File: nano-validation-snapshot.7z
```

This run should complete using **P2P only** because `WEB_SEED_MODE=off`.

---

## Step 7: Clean Up the Temporary Validation Seeder

Back on the remote server:

```bash
pkill -f '/home/openrai/nano-validation' || true
pgrep -af 'producer/seeder.py|nano-validation' || true
```

The second command should produce no output.

---

## Step 8: Restore Production Services

On the remote server:

```bash
cd /opt/nano-bootstrap-swarm
docker compose up -d

systemctl --user start nano-seed.service nano-snapshot.timer
```

Verify restore:

```bash
systemctl --user is-active nano-seed.service nano-snapshot.timer
docker compose ps
docker logs --tail 15 nano-bootstrap-swarm-nano-mirror-1 2>&1
```

Expected result:

1. `nano-seed.service` is `active`
2. `nano-snapshot.timer` is `active`
3. the mirror container is running again with `DHT_SALT=daily`

---

## What Success Looks Like

The run is successful if all of these are true:

1. The validation publish confirms under `salt='validation'`.
2. The temporary validation seeder reports `Torrent added, seeding...`.
3. The local mirror discovers `seq=1` from `validation`.
4. The local mirror downloads to 100% with `WEB_SEED_MODE=off`.
5. The local mirror reaches `Snapshot seeding complete`.
6. Production services are restored afterward.

---

## Notes

1. This validation procedure intentionally reuses the production authority key and isolates the run with a separate DHT salt.
2. The validation artifact lives outside the production snapshot directory.
3. The temporary seeder is intentionally started with `nohup` instead of a new systemd unit.
4. The local mirror uses `/tmp` so the validation workspace stays isolated from the normal repository and Docker volumes.
