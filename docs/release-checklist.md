# Public beta release checklist

Record one release commit and do not mix image, producer, or dashboard commits.

- [ ] `git rev-parse HEAD` is the intended release commit.
- [ ] Ruff and the complete pytest suite pass.
- [ ] GitHub Actions Build and Test is green for that commit.
- [ ] GHCR manifest contains the supported image platforms and digest.
- [ ] Producer checkout, user-level systemd units, and dashboard deployment are
      verified at the same release commit or documented deployment artifact.
- [ ] `docker run` with only the documented bind mount reaches `seeding`.
- [ ] A validation-salt E2E records DHT sequence, v2 info hash, peer/source
      evidence, upload counters, and dashboard response.
- [ ] Producer restart and two consecutive placeholder rotations pass.
- [ ] The 24-hour soak completes with no unexplained missed update.

If a gate fails, do not announce availability. Preserve the evidence bundle and
record the failure in the corresponding Beads issue.
