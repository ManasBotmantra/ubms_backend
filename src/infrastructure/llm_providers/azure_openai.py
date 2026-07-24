"""
src/infrastructure/llm_providers/azure_openai.py
=================================================
LLM client implementation for Azure OpenAI.
Previously this was 'OpenAIClient' in llm_client.py — renamed and moved here.
"""

import json
import contextvars
from openai import AsyncAzureOpenAI

from src.domain.interfaces import ILLMClient
from src.core.config import settings
from src.core.logger import logger
from src.core.exceptions import LLMGenerationError

# Context variables to track tokens per request (shared across all providers)
prompt_tokens_var = contextvars.ContextVar("prompt_tokens", default=0)
completion_tokens_var = contextvars.ContextVar("completion_tokens", default=0)


def reset_tokens() -> None:
    prompt_tokens_var.set(0)
    completion_tokens_var.set(0)


def get_tokens() -> tuple[int, int]:
    return prompt_tokens_var.get(), completion_tokens_var.get()


def _accumulate_tokens(usage) -> None:
    if usage:
        prompt_tokens_var.set(prompt_tokens_var.get() + usage.prompt_tokens)
        completion_tokens_var.set(completion_tokens_var.get() + usage.completion_tokens)


class AzureOpenAIClient(ILLMClient):
    """Azure OpenAI (AsyncAzureOpenAI) implementation of ILLMClient."""

    def __init__(self) -> None:
        self.deployment_name = settings.AZURE_OPENAI_DEPLOYMENT_NAME
        try:
            self.client = AsyncAzureOpenAI(
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            )
        except Exception as e:
            logger.error(f"Failed to initialize AzureOpenAIClient: {e}")
            self.client = None

    async def generate_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        if not self.client:
            raise LLMGenerationError("AzureOpenAIClient not initialized. Check API keys in .env.")

        logger.info(f"[AzureOpenAI] generate_completion | deployment={self.deployment_name}")
        try:
            response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
            _accumulate_tokens(response.usage)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"[AzureOpenAI] generate_completion error: {e}")
            raise LLMGenerationError(f"Failed to generate completion: {e}")

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict:
        if not self.client:
            raise LLMGenerationError("AzureOpenAIClient not initialized.")

        logger.info(f"[AzureOpenAI] generate_json | deployment={self.deployment_name}")
        try:
            response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            _accumulate_tokens(response.usage)
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"[AzureOpenAI] generate_json error: {e}")
            raise LLMGenerationError(f"Failed to generate JSON completion: {e}")
