# Fly.io Deployment Runbook for Status API

This guide covers how to set up, deploy, and automate the Nano Snapshot Status API on [Fly.io](https://fly.io).

The Status API is a standalone FastAPI service that receives signed snapshot updates from the Producer and serves them as JSON, `.torrent` files, and an SSR dashboard. It is intentionally lightweight (~100 MB, no libtorrent) and sits behind Cloudflare for CDN caching.

---

## 1. Prerequisites (Operator Setup)

1. **Sign up / log in** at [Fly.io](https://fly.io). You will need to add a credit card to activate the free tier.
2. **Install `flyctl`:**
   - **macOS (Homebrew):** `brew install flyctl`
   - **Linux:** `curl -L https://fly.io/install.sh | sh`
   - **Windows:** `pwsh -Command "iwr https://fly.io/install.ps1 -useb | iex"`
3. **Authenticate:**
   ```bash
   fly auth login
   ```

---

## 2. Files

The Fly config and Docker build live at the `status-api/` service root:

```
status-api/
├── Dockerfile
├── fly.toml
├── pyproject.toml
└── deploy/fly.io/
    └── README.md   # this file
```

Deploy commands below assume your working directory is **`status-api/`**.

---

## 3. Initial Setup (One-Time)

### 3.1 Create the App

```bash
cd status-api
fly apps create nano-snapshot-hub
```

> If the name `nano-snapshot-hub` is taken, pick another and update `fly.toml` accordingly.

### 3.2 Create the Persistent Volume

The API stores `status.json` and `torrent.bin` on a Fly volume so state survives redeploys.

```bash
fly volumes create status_data --size 1 --region sjc --app nano-snapshot-hub
```

> `--size 1` = 1 GB. The volume must be in the same region as `primary_region` in `fly.toml`.

### 3.3 Verify `fly.toml`

The checked-in `status-api/fly.toml` already embeds the OpenRAI `AUTHORITY_PUBKEY` and `DHT_SALT`. You should not need to edit it unless you are running a custom stream.

### 3.4 Deploy

```bash
cd status-api
fly deploy
```

This builds the Docker image (using `uv` + `pyproject.toml`, no `requirements.txt`) and deploys it to Fly.

---

## 4. Post-Deploy Verification

```bash
# Health check
fly status --app nano-snapshot-hub

# Logs
fly logs --app nano-snapshot-hub

# Test the API directly
curl https://nano-snapshot-hub.fly.dev/health
```

Initially, `GET /api/status` and `GET /api/torrent` will return `404` until the Producer pushes the first snapshot.

---

## 5. DNS & Cloudflare (Recommended)

Point a custom domain (e.g. `nano-snapshots.openrai.org`) to the Fly app and enable Cloudflare proxying:

1. In Fly: `fly certs create nano-snapshots.openrai.org --app nano-snapshot-hub`
2. In Cloudflare DNS: add a CNAME from `nano-snapshots` to `nano-snapshot-hub.fly.dev`
3. In Cloudflare SSL/TLS: set mode to **Full (strict)**
4. Add a Cloudflare Page Rule or Cache Rule:
   - `nano-snapshots.openrai.org/api/torrent` → **Cache Level: Cache Everything**, **Edge TTL: 1 hour**
   - `nano-snapshots.openrai.org/api/status*` → **Cache Level: Cache Everything**, **Edge TTL: 5–10 minutes**
   - `nano-snapshots.openrai.org/api/push` → **Cache Level: Bypass**

This keeps the Fly VM mostly idle (only ~1 request/hour from the Producer hits origin).

---

## 6. CI/CD (Automated Deployment)

### 6.1 Generate a Fly API Token

```bash
fly tokens create deploy -x 999999h
```

Copy the token immediately — you cannot view it again.

### 6.2 Add GitHub Secret

In your GitHub repository → **Settings** → **Secrets and variables** → **Actions**:

- Name: `FLY_API_TOKEN`
- Secret: paste the token from step 6.1

### 6.3 GitHub Actions Workflow

Create `.github/workflows/deploy-status-api.yml`:

```yaml
name: Deploy Status API

on:
  push:
    branches: [main]
    paths:
      - "status-api/**"

jobs:
  deploy:
    name: Deploy to Fly.io
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - name: Deploy
        run: fly deploy --config status-api/fly.toml
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

---

## 7. Producer Integration

Once the Status API is live, set `STATUS_API_URL` on the Producer:

```bash
# ~/.env on the producer server
STATUS_API_URL=https://nano-snapshots.openrai.org
```

The `daily-snapshot.sh` pipeline will automatically push to the API after each DHT publish (both full pipeline and re-publish paths). Push failures are logged as warnings but are **non-fatal** — DHT remains the source of truth.

You can also push manually:

```bash
cd /opt/nano-snapshot-swarm
./scripts/push-snapshot-status.sh
```

Or trigger the systemd timer:

```bash
systemctl --user start nano-status-push.service
```

---

## 8. Useful Commands

| Command | Purpose |
|---------|---------|
| `fly status --app nano-snapshot-hub` | App health and machine status |
| `fly logs --app nano-snapshot-hub` | Live logs |
| `fly ssh console --app nano-snapshot-hub` | Shell into the VM |
| `fly volumes list --app nano-snapshot-hub` | Check persistent volume |
| `fly deploy --app nano-snapshot-hub` | Redeploy after config/code changes |
| `fly apps destroy nano-snapshot-hub` | Tear everything down |

---

## 9. Cost Estimate

With Cloudflare caching in front:

- Fly VM (256 MB, shared CPU, mostly idle): **~$2–3/month**
- 1 GB volume: **~$0.15/month**
- Bandwidth: essentially free (served from Cloudflare edge)

Total expected: **under $5/month**.
