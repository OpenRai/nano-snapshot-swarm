from __future__ import annotations

import pytest

from producer.validation_fixture import parse_size_bytes


def test_parse_size_bytes_accepts_suffixes() -> None:
    assert parse_size_bytes("1k") == 1024
    assert parse_size_bytes("1m") == 1024**2
    assert parse_size_bytes("1g") == 1024**3
    assert parse_size_bytes("1.5m") == int(1.5 * 1024**2)


def test_parse_size_bytes_accepts_raw_integer() -> None:
    assert parse_size_bytes("4096") == 4096


def test_parse_size_bytes_rejects_empty_string() -> None:
    with pytest.raises(ValueError):
        parse_size_bytes("   ")
