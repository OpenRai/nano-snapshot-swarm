from shared.web_seed import (
    WEB_SEED_MODE_FALLBACK,
    WEB_SEED_MODE_OFF,
    WEB_SEED_MODES,
    resolve_web_seed_url,
    resolve_web_seeds,
)

DEFAULT_WEB_SEED_URL = ""
DEFAULT_WEB_SEED_MODE = WEB_SEED_MODE_OFF

__all__ = [
    "WEB_SEED_MODE_FALLBACK",
    "WEB_SEED_MODE_OFF",
    "WEB_SEED_MODES",
    "DEFAULT_WEB_SEED_MODE",
    "DEFAULT_WEB_SEED_URL",
    "resolve_web_seed_url",
    "resolve_web_seeds",
]
