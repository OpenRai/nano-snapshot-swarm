from __future__ import annotations

import pytest

from mirror.config import resolve_web_seeds


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
