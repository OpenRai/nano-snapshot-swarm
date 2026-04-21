from __future__ import annotations

WEB_SEED_MODE_OFF = "off"
WEB_SEED_MODE_FALLBACK = "fallback"
WEB_SEED_MODES = {WEB_SEED_MODE_OFF, WEB_SEED_MODE_FALLBACK}


def resolve_web_seeds(web_seed_url: str, web_seed_mode: str) -> list[str]:
    mode = web_seed_mode.strip().lower()
    if mode not in WEB_SEED_MODES:
        raise ValueError(f"unsupported web seed mode: {web_seed_mode}")
    if mode == WEB_SEED_MODE_OFF:
        return []
    url = web_seed_url.strip()
    return [url] if url else []
