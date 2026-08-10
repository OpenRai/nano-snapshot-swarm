from __future__ import annotations

import pytest

from mirror.config import (
    DEFAULT_WEB_SEED_MODE,
    DEFAULT_WEB_SEED_URL,
    WEB_SEED_MODE_OFF,
    resolve_web_seed_url,
    resolve_web_seeds,
)


def test_web_seed_defaults_are_p2p_only() -> None:
    assert DEFAULT_WEB_SEED_URL == ""
    assert DEFAULT_WEB_SEED_MODE == WEB_SEED_MODE_OFF


def test_resolve_web_seeds_returns_empty_list_when_disabled() -> None:
    assert resolve_web_seeds("https://example.test/snapshots/latest", "off") == []


def test_resolve_web_seeds_returns_url_when_fallback_enabled() -> None:
    assert resolve_web_seeds(
        "https://example.test/snapshots/latest",
        "fallback",
    ) == ["https://example.test/snapshots/latest"]


def test_resolve_web_seeds_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        resolve_web_seeds("https://example.test/snapshots/latest", "prefer")


def test_resolve_web_seed_url_returns_none_when_disabled() -> None:
    assert resolve_web_seed_url("https://example.test/snapshots/latest", "off") is None


def test_resolve_web_seed_url_normalizes_fallback_mode() -> None:
    assert resolve_web_seed_url(" https://example.test/snapshots/latest ", "FALLBACK") == (
        "https://example.test/snapshots/latest"
    )
