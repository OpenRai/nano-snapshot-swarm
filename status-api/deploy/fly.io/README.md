# Fly.io Deployment Runbook for Status API

This guide covers how to set up, deploy, and automate the Nano Snapshot Status API on [Fly.io](https://fly.io).

The Status API is a standalone FastAPI service that receives signed snapshot
updates from the Producer and serves JSON, `.torrent` files, and an SSR
dashboard. It does not import or run libtorrent.

---

## 1. Prerequisites (Operator Setup)

1. **Sign up / log in** at [Fly.io](https://fly.io). Check Fly.io's current account and billing requirements.
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

The API stores `status.json`, `torrent.bin`, and immutable named torrent files on a Fly volume so state survives redeploys.

```bash
fly volumes create status_data --size 1 --region sjc --app nano-snapshot-hub
```

> `--size 1` = 1 GB. The volume must be in the same region as `primary_region` in `fly.toml`.

### 3.3 Verify `fly.toml`

The checked-in `status-api/fly.toml` already embeds the OpenRAI `PRODUCER_SIGNING_PUBKEY` and `DHT_SALT`. You should not need to edit it unless you are running a custom stream.

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
curl https://nano-snapshots.openrai.org/health
```

Initially, `GET /api/status`, `GET /api/torrent`, and `GET /api/latest.magnet` return `404` until the Producer pushes the first snapshot.

---

## 5. DNS & Cloudflare (Recommended)

The canonical public URL is `https://nano-snapshots.openrai.org`, fronted by Cloudflare.

1. In Fly: `fly certs add nano-snapshots.openrai.org --app nano-snapshot-hub`
2. In Cloudflare DNS for `openrai.org`, create the A, AAAA, and ownership TXT
   records that `fly certs add` reports for this app. Do not copy fixed IP
   addresses or ownership tokens from another deployment.
3. In Cloudflare SSL/TLS: set mode to **Full (strict)**
4. Optionally add Cloudflare Cache Rules:
   - `nano-snapshots.openrai.org/` and `/api/status*` → **Cache Level: Bypass**
   - `nano-snapshots.openrai.org/api/torrent` and `/api/latest.magnet` → **Cache Level: Bypass**
   - `nano-snapshots.openrai.org/api/torrents/*` → **Cache Level: Cache Everything**, **Edge TTL: 1 year**
   - `nano-snapshots.openrai.org/api/push` → **Cache Level: Bypass**

Cache bypass does not disable Cloudflare security rules. If `POST /api/push` returns HTTP 403, add a narrowly scoped WAF/security skip for that path or keep the Producer on the direct Fly hostname.

Keep the DNS records required by Fly.io while the custom domain is active.

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

The repository already contains `.github/workflows/deploy-status-api.yml`. It
deploys on pushes to `main` that change `status-api/**`:

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
        run: flyctl deploy --config fly.toml
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

---

## 7. Producer Integration

Once the Status API is live, set `STATUS_API_URL` on the Producer:

```bash
# ~/.env on the producer server
STATUS_API_URL=https://nano-snapshot-hub.fly.dev
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

## 9. Cost and capacity

Billing depends on Fly.io's current plans, VM uptime, volume size, and egress.
Review the provider's current pricing and monitor the app's usage after launch.
