# AGENTS.md

### Dev commands

```bash
# Tests (PYTHONPATH required — pytest won't find shared/ otherwise)
PYTHONPATH=$(pwd) uv run pytest tests/ -v

# Lint
uv run ruff check shared/ producer/ mirror/ tests/ status-api/
```

### Architecture

- **`shared/`** — crypto, DHT value building, signing (used by both producer and mirror)
- **`producer/`** — torrent creation, DHT publishing, seeder, status push
- **`mirror/`** — libtorrent-based downloader (swarm or leech mode)
- **`status-api/`** — standalone FastAPI app, no imports from shared/producer/mirror. Only needs `pynacl` for signature verification.
- **`tests/`** — covers shared, producer, status-api, and validation fixture

### Status API deployment

- Deploys to Fly.io (app `nano-snapshot-hub`, region `sjc`) via GitHub Actions on push to `main` when `status-api/**` changes.
- `status-api/app/static/` is served at `/static` via `StaticFiles` mount.
- `producer/push_status.py` uses `urllib.request` (stdlib) — no new deps for the main project.
- Push failures are non-fatal. DHT publish is the source of truth; status API is best-effort.
- `AUTHORITY_PUBKEY` is baked into `fly.toml` as an env var.

### Key non-obvious facts

- `producer/publish.py` uses raw `lt.session` directly — intentionally untouched. All other libtorrent usage goes through `mirror/libtorrent_session.py`.
- `pop_alerts()` is removed from `LibtorrentSession` — do not add it back. Use the narrow wait APIs (`wait_for_dht_mutable_item`, `wait_for_dht_put`).
- `MirrorState.update()` preserves `current_torrent_name` when `torrent_name=None` — do not pass empty string.
- `mirror_state.json` is saved in both swarm and `--once` (leech) mode.
- Leech mode has no download timeout — runs until complete or user cancels. Stall warning at 300s of zero rate, but never exits. Do not add a wall-clock timeout.
- Swarm mode exits after `--download-timeout` (default 1800s) of continuous DHT inactivity so the container can restart.
- `AUTHORITY_PUBKEY`, `DHT_SALT`, and `WEB_SEED_URL` are baked into the mirror Docker image as `ARG` defaults. Image runs with zero env vars.

### Deployment — remote host `bandwidth-martyr`

- Production runs as user-level systemd on the `openrai` user. **Always `systemctl --user` / `journalctl --user`** — never system-level. Service units must NOT contain `User=`.
- Repo at `/opt/nano-snapshot-swarm`. After code changes: `git pull && systemctl --user daemon-reload && systemctl --user restart nano-snapshot.timer`.
- `.env` lives at `~/.env` on the remote (not in the repo). `DHT_PRIVATE_KEY` is required.
- To manually trigger a status push (e.g. after deploying template changes):
  ```bash
  ssh bandwidth-martyr 'cd /opt/nano-snapshot-swarm && git pull --rebase && set -a && source ~/.env && set +a && .venv/bin/python -m producer.push_status --status-api-url https://nano-snapshot.ninzin.net --state-file publisher_state.json --torrent-file /home/openrai/nano-snapshots/nano-ledger-snapshot.7z.torrent --snapshot-file /home/openrai/nano-snapshots/nano-ledger-snapshot.7z --web-seed-url https://s3.us-east-2.amazonaws.com/repo.nano.org/snapshots/latest/'
  ```
- From the repo root, refresh the checked-in authority key file with `./derive-authority-pubkey | tee AUTHORITY_PUBKEY`.

### E2E validation procedure

See `docs/manual-e2e-validation.md`. Uses `DHT_SALT=validation` and `WEB_SEED_MODE=off` to isolate from production. Run against a temporary nohup seeder on the remote, not the production seeder.

### Stale doc in CLI help (fix if editing)

`--download-timeout` help text says "ignored in --once mode" — that is correct. Do not reintroduce auto-setting it for leech mode.

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
