from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUNTIME_CONFIG = Path("scripts/runtime-config.sh")


def parse_boolean(value: str | None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if value is None:
        environment.pop("TEST_BOOLEAN", None)
    else:
        environment["TEST_BOOLEAN"] = value
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; parse_boolean_env TEST_BOOLEAN false',
            "bash",
            str(RUNTIME_CONFIG),
        ],
        env=environment,
        capture_output=True,
        text=True,
    )


def test_shell_boolean_parser_defaults_for_unset_or_empty_values() -> None:
    assert parse_boolean(None).stdout == "false\n"
    assert parse_boolean("").stdout == "false\n"


def test_shell_boolean_parser_accepts_case_insensitive_values() -> None:
    assert parse_boolean("TRUE").stdout == "true\n"
    assert parse_boolean("False").stdout == "false\n"


def test_shell_boolean_parser_rejects_non_boolean_values() -> None:
    result = parse_boolean("1")

    assert result.returncode == 2
    assert "TEST_BOOLEAN must be true or false" in result.stderr
