from __future__ import annotations

import re
import subprocess
from pathlib import Path

PLACEHOLDER_SCRIPT = Path("scripts/create-placeholder-snapshot.sh")
PLACEHOLDER_SIZE = 128 * 1024 * 1024


def test_placeholder_script_creates_exact_timestamped_payload(tmp_path) -> None:
    result = subprocess.run(
        ["bash", str(PLACEHOLDER_SCRIPT), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    output_file = Path(result.stdout.strip())
    assert output_file.parent == tmp_path
    assert re.fullmatch(
        r"nano-ledger-snapshot-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(?:-\d+)?\.7z",
        output_file.name,
    )
    assert output_file.stat().st_size == PLACEHOLDER_SIZE


def test_placeholder_script_avoids_same_second_name_collision(tmp_path) -> None:
    first = subprocess.run(
        ["bash", str(PLACEHOLDER_SCRIPT), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    second = subprocess.run(
        ["bash", str(PLACEHOLDER_SCRIPT), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert first != second
    assert len(list(tmp_path.glob("nano-ledger-snapshot-*.7z"))) == 2


def test_daily_pipeline_gates_upstream_download_and_archive_validation() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert 'USE_PLACEHOLDER_SNAPSHOT="${USE_PLACEHOLDER_SNAPSHOT:-0}"' in script
    assert 'if [[ "$USE_PLACEHOLDER_SNAPSHOT" == 1 ]]; then' in script
    assert 'create-placeholder-snapshot.sh' in script
    assert "aria2c" in script
    assert 'if [ "$MAGIC" != "377abcaf271c" ]; then' in script
