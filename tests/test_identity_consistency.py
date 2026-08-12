from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_producer_identity_check_runs_without_exposing_private_key() -> None:
    repo_root = Path(__file__).parents[1]
    env = os.environ.copy()
    env.pop("DHT_PRIVATE_KEY", None)

    result = subprocess.run(
        [sys.executable, "scripts/verify-producer-identity.py"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Producer public key matches" in result.stdout
    assert "DHT_PRIVATE_KEY" not in result.stderr
