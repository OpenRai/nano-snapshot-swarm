from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_daily_pipeline_does_not_interpolate_metadata_into_python_source() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert "python3 -c" not in script
    assert "python -c" not in script
    assert "private_key_hex='$DHT_PRIVATE_KEY'" not in script
    assert "original_filename': '$FILENAME'" not in script
    assert 'open(\'$META_FILE\'' not in script


def test_daily_pipeline_reloads_active_seeder_or_starts_an_inactive_one() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert "systemctl --user reload nano-seed.service" in script
    assert "systemctl --user start nano-seed.service" in script
    assert "systemctl --user restart nano-seed.service" not in script
    assert "systemctl --user kill --signal=HUP nano-seed.service" not in script
    assert '"dht_verified"' in script
    assert "--defer-dht-publish" in script
    assert "last_dht_info_hash" in script
    assert "from producer.publish import publish_to_dht" not in script


def test_daily_pipeline_leaves_an_unchanged_snapshot_to_existing_timers() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    unchanged_start = script.index('log "Snapshot unchanged')
    unchanged_end = script.index('exit 0', unchanged_start)
    unchanged_path = script[unchanged_start:unchanged_end]

    assert "no seeder reload or DHT publication requested" in unchanged_path
    assert "systemctl --user" not in unchanged_path
    assert "push-snapshot-status.sh" not in unchanged_path


def test_daily_pipeline_waits_for_the_full_cold_seeder_publication_budget() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert "seq 1 360" in script


def test_producer_unit_builds_explicit_sequence_helper_before_start() -> None:
    unit = Path("systemd/nano-seed.service").read_text()

    assert "DHT_PUT_HELPER=%h/nano-snapshot-swarm/bin/nano-dht-put" in unit
    assert "ExecStartPre=%h/nano-snapshot-swarm/scripts/build-dht-put-helper.sh" in unit
    assert "ExecReload=/bin/kill -HUP $MAINPID" in unit


def test_seeder_accepts_reload_before_dht_bootstrap() -> None:
    seeder = Path("producer/seeder.py").read_text()

    assert seeder.index("signal.signal(signal.SIGHUP, on_signal)") < seeder.index(
        "session.wait_for_dht_bootstrap(timeout=120)"
    )


def test_shell_special_filename_stays_data_when_passed_as_an_argument(tmp_path) -> None:
    metadata_path = tmp_path / "metadata.json"
    marker = tmp_path / "SHOULD_NOT_RUN"
    filename = f"quote'\\dollar$\\n{marker.name}\narchive.7z"
    writer = "import json, sys; json.dump({'filename': sys.argv[2]}, open(sys.argv[1], 'w'))"

    subprocess.run(
        [sys.executable, "-c", writer, str(metadata_path), filename],
        check=True,
    )

    assert json.loads(metadata_path.read_text())["filename"] == filename
    assert not marker.exists()
