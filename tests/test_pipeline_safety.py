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
