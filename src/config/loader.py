"""
src/config/loader.py
====================
Registry of all API configs + factory to load the active one.

To add a new API:
  1. Create src/config/api_configs/<name>.py with a MY_CONFIG = ApiConfig(...) instance.
  2. Import it here and add it to _API_REGISTRY.
  3. Set ACTIVE_API_CONFIG=<name> in .env.
"""

from src.config.base import ApiConfig
from src.config.api_configs.transaction_log import TRANSACTION_LOG_CONFIG
from src.config.api_configs.bill_summary import BILL_SUMMARY_CONFIG

# ── Registry ─────────────────────────────────────────────────────────────────
# Key must match the ApiConfig.name field and the ACTIVE_API_CONFIG .env value.

_API_REGISTRY: dict[str, ApiConfig] = {
    "transaction_log": TRANSACTION_LOG_CONFIG,
    "bill_summary": BILL_SUMMARY_CONFIG,
    # "my_new_api": MY_NEW_API_CONFIG,   ← add future APIs here
}


def get_active_api_config() -> ApiConfig:
    """
    Returns the ApiConfig matching the ACTIVE_API_CONFIG setting.
    Called once and cached in dependencies.py via @lru_cache.

    Raises:
        ValueError — if the configured name is not found in the registry.
    """
    from src.core.config import settings
    name = settings.ACTIVE_API_CONFIG
    if name not in _API_REGISTRY:
        available = list(_API_REGISTRY.keys())
        raise ValueError(
            f"ACTIVE_API_CONFIG='{name}' not found in registry. "
            f"Available configs: {available}"
        )
    return _API_REGISTRY[name]


def list_available_apis() -> list[str]:
    """Returns names of all registered API configs."""
    return list(_API_REGISTRY.keys())
