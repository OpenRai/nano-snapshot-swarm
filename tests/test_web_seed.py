from __future__ import annotations

import pytest

from shared.web_seed import resolve_web_seed_url, resolve_web_seeds


def test_producer_and_mirror_share_off_policy() -> None:
    assert resolve_web_seed_url("https://example.test/snapshot", "off") is None
    assert resolve_web_seeds("https://example.test/snapshot", "off") == []


def test_producer_and_mirror_share_fallback_policy() -> None:
    url = "https://example.test/snapshot"
    assert resolve_web_seed_url(url, "fallback") == url
    assert resolve_web_seeds(url, "fallback") == [url]


def test_web_seed_policy_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        resolve_web_seed_url("https://example.test/snapshot", "prefer")
