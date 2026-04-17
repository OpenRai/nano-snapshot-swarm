# AGENTS.md

## Project: Nano P2P Ledger Snapshot Service

Decentralized Nano ledger snapshot distribution using BitTorrent BEP 46 (Mutable Torrents) with binary delta efficiency.

### Quick Start

```bash
# Install dependencies (producer development)
uv pip install pynacl bencodepy pytest ruff

# Run tests
PYTHONPATH=$(pwd) pytest tests/ -v

# Lint
ruff check shared/ producer/ mirror/ tests/
```

### Architecture

```
shared/nano_identity.py    — Ed25519 key handling, Nano address derivation, BEP 46 target ID
shared/bep46.py           — BEP 46 signature buffer, sign/verify, DHT value encoding
producer/snapshot.sh       — mdb_copy + zstd --rsyncable pipeline
producer/torrent_create.py — BitTorrent v2 .torrent generation via libtorrent
producer/publish.py        — BEP 46 DHT mutable item publisher
producer/cli.py           — Unified CLI entry point (snapshot/publish/full)
mirror/Dockerfile          — 2-stage build: libtorrent C++ lib + Python runtime
mirror/libtorrent_session.py — libtorrent session wrapper with alert loop, DHT ops
mirror/dht_discovery.py   — DHT mutable item retrieval with retry/verification
mirror/watcher.py          — Main sidecar: swarm daemon + leech (--once) mode
```

### Key Commands

```bash
# Producer: extract and compress ledger
python -m producer.cli snapshot --ledger-path /var/nano/data/data.ldb

# Producer: create torrent and publish to DHT
python -m producer.cli publish --private-key <HEX> --web-seed-url <URL>

# Producer: full pipeline (extract + compress + publish)
python -m producer.cli full --ledger-path /var/nano/data/data.ldb

# Producer: with custom DHT salt
python -m producer.cli publish --private-key <HEX> --salt weekly

# Mirror: Docker swarm mode (long-running)
docker compose up -d

# Mirror: Docker leech mode (one-shot download)
docker run --rm -e AUTHORITY_PUBKEY=<HEX> \
  -v $(pwd)/data:/data ghcr.io/openrai/nano-p2p-mirror:latest \
  --once --download-timeout 3600
```

### Documentation

| Document | Purpose |
|---|---|
| `docs/` | Full documentation directory |
| `docs/getting-started.md` | First run, Docker setup |
| `docs/mirror-swarm-mode.md` | Long-running mirror |
| `docs/mirror-leech-mode.md` | One-shot download (`--once`) |
| `docs/producer-guide.md` | Authority/producer notes |
| `docs/configuration.md` | Env vars, CLI flags, docker-compose |
| `docs/architecture.md` | BEP 46, DHT, delta updates |
| `docs/validation.md` | Manual test templates |

### Testing

```bash
PYTHONPATH=$(pwd) pytest tests/ -v
```

Tests cover: BEP 46 signature buffer construction, sign/verify round-trips, BEP 46 test vectors (official), Nano address derivation, DHT value encoding.

### Environment Variables

| Variable | Service | Description | Default |
|---|---|---|---|
| `NANO_LEDGER_PATH` | Producer | Path to live `data.ldb` | `/var/nano/data/data.ldb` |
| `OUTPUT_DIR` | Producer | Snapshot output directory | `.` |
| `DHT_PRIVATE_KEY` | Producer | Ed25519 private key (hex) | Required for publish |
| `DHT_SALT` | Both | DHT mutable item salt | `daily` |
| `AUTHORITY_PUBKEY` | Mirror | Ed25519 public key (hex) | Required for mirror |
| `DATA_DIR` | Mirror | Data volume path | `/data` |
| `POLL_INTERVAL` | Mirror | DHT poll interval (seconds) | `600` |
| `WEB_SEED_URL` | Mirror | S3/HTTP web seed URL | `https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest` |
| `LOG_LEVEL` | Both | Python log level | `INFO` |

### Mirror CLI Flags

The mirror watcher accepts these additional flags beyond env vars:

```bash
python -m mirror.watcher \
  --authority-pubkey <HEX> \     # required
  --salt daily \                   # DHT salt (env: DHT_SALT)
  --poll-interval 600 \            # swarm mode poll interval
  --web-seed-url <URL> \          # fallback web seed
  --log-level INFO \              # DEBUG, INFO, WARNING, ERROR
  --once \                        # leech mode: download once then exit
  --download-timeout 3600         # seconds (0=infinite; auto-3600 in --once)
```

### Dependencies

- **Runtime:** Python 3.12+, libtorrent 2.x (C++ built in Docker), pynacl, bencodepy, zstd
- **Build:** Docker, cmake, boost (libtorrent compilation handled in Dockerfile)

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
