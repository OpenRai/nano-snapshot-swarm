from __future__ import annotations

import json
from pathlib import Path

import pytest

from producer import dht_put


def test_native_publisher_uses_helper_and_checks_monotonic_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = tmp_path / "nano-dht-put"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o700)
    monkeypatch.setenv("DHT_PUT_HELPER", str(helper))

    class Result:
        returncode = 0
        stdout = json.dumps(
            {"sequence": 1361, "observed_sequence": 1360, "direct_acknowledgements": 7}
        )
        stderr = ""

    captured: dict[str, object] = {}

    def fake_run(arguments: list[str], **kwargs: object) -> Result:
        captured["arguments"] = arguments
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(dht_put.subprocess, "run", fake_run)

    result = dht_put.publish_with_highest_sequence("ab" * 32, "daily")

    assert captured["arguments"] == [str(helper), "--info-hash", "ab" * 32, "--salt", "daily"]
    assert result == {
        "sequence": 1361,
        "observed_sequence": 1360,
        "direct_acknowledgements": 7,
    }


def test_native_publisher_rejects_nonadvancing_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = tmp_path / "nano-dht-put"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o700)
    monkeypatch.setenv("DHT_PUT_HELPER", str(helper))

    class Result:
        returncode = 0
        stdout = json.dumps(
            {"sequence": 1360, "observed_sequence": 1360, "direct_acknowledgements": 7}
        )
        stderr = ""

    monkeypatch.setattr(dht_put.subprocess, "run", lambda *_args, **_kwargs: Result())

    with pytest.raises(RuntimeError, match="did not advance"):
        dht_put.publish_with_highest_sequence("ab" * 32, "daily")
