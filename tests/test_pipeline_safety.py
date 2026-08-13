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


def test_daily_pipeline_reloads_seeder_without_destroying_its_dht_session() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert "systemctl --user kill --signal=HUP nano-seed.service" in script
    assert "systemctl --user restart nano-seed.service" not in script
    assert '"dht_verified"' in script
    assert "--defer-dht-publish" in script
    assert "last_dht_info_hash" in script
    assert "from producer.publish import publish_to_dht" not in script


def test_daily_pipeline_waits_for_the_full_cold_seeder_publication_budget() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert "seq 1 360" in script


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
