"""
src/infrastructure/llm_client.py
=================================
Factory that creates the active LLM client based on the LLM_PROVIDER setting.
Set LLM_PROVIDER in .env to switch providers:
  azure_openai | openai | claude | gemini

Token tracking helpers are re-exported here for backward compatibility with
any code that imports them from this module.
"""

from src.domain.interfaces import ILLMClient
from src.core.config import settings
from src.core.logger import logger

# Re-export token helpers — code that does `from src.infrastructure.llm_client import reset_tokens`
# continues to work without changes.
from src.infrastructure.llm_providers.azure_openai import (
    reset_tokens,
    get_tokens,
)


def create_llm_client() -> ILLMClient:
    """
    Factory: reads LLM_PROVIDER from settings and returns the matching client.

    Raises:
        ValueError — if LLM_PROVIDER is not a recognised value.
    """
    provider = settings.LLM_PROVIDER
    logger.info(f"Creating LLM client | provider={provider}")

    if provider == "azure_openai":
        from src.infrastructure.llm_providers.azure_openai import AzureOpenAIClient
        return AzureOpenAIClient()

    if provider == "openai":
        from src.infrastructure.llm_providers.openai import OpenAIClient
        return OpenAIClient()

    if provider == "claude":
        from src.infrastructure.llm_providers.claude import ClaudeClient
        return ClaudeClient()

    if provider == "gemini":
        from src.infrastructure.llm_providers.gemini import GeminiClient
        return GeminiClient()

    raise ValueError(
        f"Unknown LLM_PROVIDER='{provider}'. "
        f"Valid choices: azure_openai, openai, claude, gemini"
    )


# ---------------------------------------------------------------------------
# Backward-compat alias — dependencies.py previously imported OpenAIClient
# directly; it now uses create_llm_client() but we keep this alias so any
# other code that still does `from src.infrastructure.llm_client import OpenAIClient`
# doesn't break immediately.
# ---------------------------------------------------------------------------
from src.infrastructure.llm_providers.azure_openai import AzureOpenAIClient as OpenAIClient  # noqa: E402
