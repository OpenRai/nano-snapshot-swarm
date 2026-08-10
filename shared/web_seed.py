from __future__ import annotations

WEB_SEED_MODE_OFF = "off"
WEB_SEED_MODE_FALLBACK = "fallback"
WEB_SEED_MODES = frozenset({WEB_SEED_MODE_OFF, WEB_SEED_MODE_FALLBACK})


def normalize_web_seed_mode(web_seed_mode: str) -> str:
    mode = web_seed_mode.strip().lower()
    if mode not in WEB_SEED_MODES:
        raise ValueError(f"unsupported web seed mode: {web_seed_mode}")
    return mode


def resolve_web_seed_url(web_seed_url: str, web_seed_mode: str) -> str | None:
    if normalize_web_seed_mode(web_seed_mode) == WEB_SEED_MODE_OFF:
        return None
    url = web_seed_url.strip()
    return url or None


def resolve_web_seeds(web_seed_url: str, web_seed_mode: str) -> list[str]:
    url = resolve_web_seed_url(web_seed_url, web_seed_mode)
    return [url] if url else []
