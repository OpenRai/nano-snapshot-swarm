from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


def _disk_budget_result(
    tmp_path: Path,
    *,
    available_kib: int,
    incoming_bytes: int,
    partial_bytes: int,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "df",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
        f"printf '%s\\n' '/dev/test 1 1 {available_kib} 1% /'\n",
    )
    return subprocess.run(
        [
            PROJECT_ROOT / "scripts/check-snapshot-disk-budget.sh",
            "--output-dir",
            str(tmp_path),
            "--incoming-bytes",
            str(incoming_bytes),
            "--partial-bytes",
            str(partial_bytes),
            "--retention-count",
            "1",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "SNAPSHOT_DISK_SAFETY_BYTES": "100",
        },
    )


def test_snapshot_disk_budget_accepts_remaining_download_and_hardlink_retention(
    tmp_path: Path,
) -> None:
    result = _disk_budget_result(
        tmp_path,
        available_kib=2,
        incoming_bytes=2000,
        partial_bytes=1000,
    )

    assert result.returncode == 0, result.stderr
    assert "remaining=1000" in result.stdout
    assert "retention=1 hardlink_reserve=0" in result.stdout
    assert "required=1100" in result.stdout


def test_snapshot_disk_budget_refuses_insufficient_space_before_download(tmp_path: Path) -> None:
    result = _disk_budget_result(
        tmp_path,
        available_kib=1,
        incoming_bytes=2000,
        partial_bytes=1000,
    )

    assert result.returncode == 1
    assert "insufficient disk budget" in result.stdout
    assert "required=1100" in result.stdout
    assert "available=1024" in result.stdout


def _watchdog_environment(
    tmp_path: Path, *, available_kib: int = 1024 * 1024
) -> tuple[dict[str, str], Path, Path]:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    _write_executable(
        bin_dir / "df",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
        f"printf '%s\\n' '/dev/test 1 1 {available_kib} 1% /'\n",
    )
    _write_executable(
        bin_dir / "systemctl",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --user ]; then shift; fi\n"
        "if [ \"$1\" = is-active ]; then exit 0; fi\n"
        "printf '%s\\n' \"$*\" >> \"${TEST_SYSTEMCTL_LOG:?}\"\n",
    )
    _write_executable(
        bin_dir / "stat",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = -c ] && [ \"$2\" = %Y ]; then\n"
        "  printf '%s\\n' \"${TEST_STATS_MTIME:?}\"; exit 0\n"
        "fi\n"
        "exec /usr/bin/stat \"$@\"\n",
    )
    environment = {
        **os.environ,
        "OUTPUT_DIR": str(output_dir),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TEST_SYSTEMCTL_LOG": str(systemctl_log),
        "TEST_STATS_MTIME": str(int(time.time())),
        "PRODUCER_RECOVERY_MIN_FREE_BYTES": "1",
        "PRODUCER_RECOVERY_UNHEALTHY_SECONDS": "1",
        "PRODUCER_RECOVERY_STALE_STATS_SECONDS": "600",
        "PRODUCER_RECOVERY_RESTART_COOLDOWN_SECONDS": "3600",
    }
    return environment, output_dir, systemctl_log


def _run_watchdog(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PROJECT_ROOT / "scripts/check-seeder-recovery.sh"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def test_seeder_watchdog_does_not_restart_a_fresh_file_check(tmp_path: Path) -> None:
    environment, output_dir, systemctl_log = _watchdog_environment(tmp_path)
    (output_dir / "seeder-stats.json").write_text(
        json.dumps({"state": "checking_files", "dht_verified": True, "seeder_ready": False})
    )

    result = _run_watchdog(environment)

    assert result.returncode == 0, result.stderr
    assert "checking files" in result.stdout
    assert not systemctl_log.exists()


def test_seeder_watchdog_blocks_recovery_until_disk_is_reclaimed(tmp_path: Path) -> None:
    environment, output_dir, systemctl_log = _watchdog_environment(tmp_path, available_kib=0)
    (output_dir / "seeder-stats.json").write_text(
        json.dumps({"state": "seeding", "dht_verified": False, "seeder_ready": False})
    )
    environment["PRODUCER_RECOVERY_MIN_FREE_BYTES"] = "1"

    result = _run_watchdog(environment)

    assert result.returncode == 1
    assert "Recovery is blocked until disk space is reclaimed" in result.stdout
    assert not systemctl_log.exists()


def test_seeder_watchdog_restarts_persistent_failure_then_pushes_when_ready(
    tmp_path: Path,
) -> None:
    environment, output_dir, systemctl_log = _watchdog_environment(tmp_path)
    stats_file = output_dir / "seeder-stats.json"
    stats_file.write_text(
        json.dumps({"state": "seeding", "dht_verified": False, "seeder_ready": False})
    )
    state_dir = Path(environment["XDG_STATE_HOME"]) / "nano-seed-recovery-watchdog"
    state_dir.mkdir(parents=True)
    (state_dir / "unhealthy-since").write_text(f"{int(time.time()) - 2}\n")

    restarted = _run_watchdog(environment)

    assert restarted.returncode == 0, restarted.stderr
    assert "restarting nano-seed.service" in restarted.stdout
    assert systemctl_log.read_text().strip() == "restart nano-seed.service"
    assert (state_dir / "status-push-pending").exists()

    (state_dir / "unhealthy-since").write_text(f"{int(time.time()) - 2}\n")
    cooldown = _run_watchdog(environment)

    assert cooldown.returncode == 1
    assert "restart cooldown" in cooldown.stdout
    assert systemctl_log.read_text().splitlines() == ["restart nano-seed.service"]

    stats_file.write_text(
        json.dumps({"state": "seeding", "dht_verified": True, "seeder_ready": True})
    )
    pushed = _run_watchdog(environment)

    assert pushed.returncode == 0, pushed.stderr
    assert "Seeder recovered; pushing verified status" in pushed.stdout
    assert systemctl_log.read_text().splitlines() == [
        "restart nano-seed.service",
        "start nano-status-push.service",
    ]
    assert not (state_dir / "status-push-pending").exists()


def test_producer_recovery_assets_document_the_bounded_policy() -> None:
    unit = (PROJECT_ROOT / "systemd/nano-seed-recovery-watchdog.service").read_text()
    timer = (PROJECT_ROOT / "systemd/nano-seed-recovery-watchdog.timer").read_text()
    template = (PROJECT_ROOT / ".env.example").read_text()
    pipeline = (PROJECT_ROOT / "scripts/daily-snapshot.sh").read_text()
    validation = (PROJECT_ROOT / "docs/manual-e2e-validation.md").read_text()

    assert "ExecStart=%h/nano-snapshot-swarm/scripts/check-seeder-recovery.sh" in unit
    assert "OnUnitActiveSec=5m" in timer
    assert "Persistent=true" in timer
    assert "PRODUCER_RECOVERY_UNHEALTHY_SECONDS=900" in template
    assert "check-snapshot-disk-budget.sh" in pipeline
    assert "SNAPSHOT_UNKNOWN_SIZE_RESERVE_BYTES" in pipeline
    assert "mktemp -d /tmp/nano-validation-e2e.XXXXXX" in validation
    assert "trap 'rm -rf \"$VALIDATION_DIR\"' EXIT INT TERM" in validation
