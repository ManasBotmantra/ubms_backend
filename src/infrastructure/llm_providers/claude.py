"""
src/infrastructure/llm_providers/claude.py
==========================================
LLM client implementation for Anthropic Claude.
Requires: ANTHROPIC_API_KEY and ANTHROPIC_MODEL in .env
Requires: pip install anthropic  (added to requirements.txt)
"""

import json
from src.domain.interfaces import ILLMClient
from src.core.config import settings
from src.core.logger import logger
from src.core.exceptions import LLMGenerationError


class ClaudeClient(ILLMClient):
    """Anthropic Claude implementation of ILLMClient."""

    def __init__(self) -> None:
        self.model = settings.ANTHROPIC_MODEL
        try:
            import anthropic
            self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        except ImportError:
            logger.error("anthropic package not installed. Run: pip install anthropic")
            self.client = None
        except Exception as e:
            logger.error(f"Failed to initialize ClaudeClient: {e}")
            self.client = None

    async def generate_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        if not self.client:
            raise LLMGenerationError(
                "ClaudeClient not initialized. Check ANTHROPIC_API_KEY in .env "
                "and ensure 'anthropic' is installed."
            )

        logger.info(f"[Claude] generate_completion | model={self.model}")
        try:
            # Claude uses a separate 'system' parameter (not inside messages array)
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"[Claude] generate_completion error: {e}")
            raise LLMGenerationError(f"Failed to generate completion: {e}")

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict:
        """
        Claude does not have a native json_object response_format.
        We instruct it via the system prompt and parse the output.
        """
        if not self.client:
            raise LLMGenerationError("ClaudeClient not initialized.")

        logger.info(f"[Claude] generate_json | model={self.model}")
        json_system_prompt = (
            system_prompt
            + "\n\nIMPORTANT: Return ONLY valid JSON. No markdown, no code blocks, no explanation."
        )
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=temperature,
                system=json_system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            # Strip any accidental markdown fences
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            return json.loads(raw.strip())
        except json.JSONDecodeError as e:
            logger.error(f"[Claude] JSON parse error: {e}")
            raise LLMGenerationError(f"Claude returned invalid JSON: {e}")
        except Exception as e:
            logger.error(f"[Claude] generate_json error: {e}")
            raise LLMGenerationError(f"Failed to generate JSON completion: {e}")
