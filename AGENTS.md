# AGENTS.md

### Dev commands

```bash
# Tests (PYTHONPATH required — pytest won't find shared/ otherwise)
PYTHONPATH=$(pwd) uv run pytest tests/ -v

# Lint
uv run ruff check shared/ producer/ mirror/ tests/
```

### Key non-obvious facts

- `producer/publish.py` uses raw `lt.session` directly and is intentionally left untouched. All other libtorrent usage goes through `mirror/libtorrent_session.py`.
- `pop_alerts()` is removed from `LibtorrentSession` — do not add it back. Use the narrow wait APIs (`wait_for_dht_mutable_item`, `wait_for_dht_put`).
- `MirrorState.update()` preserves `current_torrent_name` when `torrent_name=None` — do not pass empty string.
- `mirror_state.json` is saved in **both** swarm and `--once` (leech) mode.
- Leech mode has **no download timeout** — it runs until complete or user cancels. A stall warning is logged if `download_rate == 0` for 300s, but it never exits. Do not add a wall-clock timeout to leech mode.
- Swarm mode exits after `--download-timeout` (default 1800s) of continuous DHT inactivity (no results from DHT), so the container can restart.
- `AUTHORITY_PUBKEY`, `DHT_SALT`, and `WEB_SEED_URL` are baked into the mirror Docker image as `ARG` defaults. The image runs with zero env vars. Override at build time with `--build-arg` or at runtime with `-e`.

### Mirror Docker image — run without any env vars

```bash
docker run --rm -v /data:/data ghcr.io/openrai/nano-p2p-mirror:latest --once
```

### Stale doc in CLI help (fix if editing)

`--download-timeout` help text says "ignored in --once mode" — that is correct. Do not reintroduce auto-setting it for leech mode.

### Deployment — remote host `bandwidth-martyr`

- Production runs as user-level systemd on the `openrai` user. **Always use `systemctl --user` / `journalctl --user`** — never system-level. Service units must NOT contain `User=`.
- Repo lives at `/opt/nano-bootstrap-swarm`. After any change: `git pull && systemctl --user daemon-reload && systemctl --user restart nano-snapshot.timer`.
- `.env` lives at `~/.env` on the remote (not in the repo). Contains `DHT_PRIVATE_KEY` and `AUTHORITY_PUBKEY`.

### E2E validation procedure

See `docs/manual-e2e-validation.md`. Uses `DHT_SALT=validation` and `WEB_SEED_MODE=off` to isolate from production. Run against a temporary nohup seeder on the remote, not the production seeder.

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

