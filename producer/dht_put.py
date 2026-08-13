"""Run the native explicit-sequence BEP 46 publisher."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def publish_with_highest_sequence(info_hash: str, salt: str) -> dict[str, int]:
    """Publish with the configured native helper and validate its result."""
    helper = os.environ.get("DHT_PUT_HELPER")
    if not helper:
        raise RuntimeError("DHT_PUT_HELPER is not configured")
    helper_path = Path(helper)
    if not helper_path.is_file() or not os.access(helper_path, os.X_OK):
        raise RuntimeError(f"DHT_PUT_HELPER is not executable: {helper_path}")

    completed = subprocess.run(
        [str(helper_path), "--info-hash", info_hash, "--salt", salt],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "no error detail"
        raise RuntimeError(f"native DHT publisher failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
        sequence = payload["sequence"]
        acknowledgements = payload["direct_acknowledgements"]
        observed_sequence = payload["observed_sequence"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("native DHT publisher returned invalid JSON") from exc
    if not all(isinstance(value, int) and value >= 0 for value in (
        sequence,
        acknowledgements,
        observed_sequence,
    )):
        raise RuntimeError("native DHT publisher returned invalid sequence metadata")
    if sequence <= observed_sequence:
        raise RuntimeError("native DHT publisher did not advance the observed sequence")
    return {
        "sequence": sequence,
        "direct_acknowledgements": acknowledgements,
        "observed_sequence": observed_sequence,
    }
