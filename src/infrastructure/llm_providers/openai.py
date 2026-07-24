"""
src/infrastructure/llm_providers/openai.py
===========================================
LLM client implementation for standard OpenAI (api.openai.com).
Requires: OPENAI_API_KEY and OPENAI_MODEL in .env
"""

import json
from openai import AsyncOpenAI

from src.domain.interfaces import ILLMClient
from src.core.config import settings
from src.core.logger import logger
from src.core.exceptions import LLMGenerationError
from src.infrastructure.llm_providers.azure_openai import _accumulate_tokens


class OpenAIClient(ILLMClient):
    """Standard OpenAI (api.openai.com) implementation of ILLMClient."""

    def __init__(self) -> None:
        self.model = settings.OPENAI_MODEL
        try:
            self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        except Exception as e:
            logger.error(f"Failed to initialize OpenAIClient: {e}")
            self.client = None

    async def generate_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        if not self.client:
            raise LLMGenerationError("OpenAIClient not initialized. Check OPENAI_API_KEY in .env.")

        logger.info(f"[OpenAI] generate_completion | model={self.model}")
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
            _accumulate_tokens(response.usage)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"[OpenAI] generate_completion error: {e}")
            raise LLMGenerationError(f"Failed to generate completion: {e}")

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict:
        if not self.client:
            raise LLMGenerationError("OpenAIClient not initialized.")

        logger.info(f"[OpenAI] generate_json | model={self.model}")
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
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
            logger.error(f"[OpenAI] generate_json error: {e}")
            raise LLMGenerationError(f"Failed to generate JSON completion: {e}")
