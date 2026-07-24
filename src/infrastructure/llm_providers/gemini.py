"""
src/infrastructure/llm_providers/gemini.py
==========================================
LLM client implementation for Google Gemini.
Requires: GOOGLE_API_KEY and GOOGLE_MODEL in .env
Requires: pip install google-generativeai  (added to requirements.txt)
"""

import json
from src.domain.interfaces import ILLMClient
from src.core.config import settings
from src.core.logger import logger
from src.core.exceptions import LLMGenerationError


class GeminiClient(ILLMClient):
    """Google Gemini implementation of ILLMClient."""

    def __init__(self) -> None:
        self.model_name = settings.GOOGLE_MODEL
        try:
            import google.generativeai as genai
            genai.configure(api_key=settings.GOOGLE_API_KEY)
            self._genai = genai
        except ImportError:
            logger.error("google-generativeai package not installed. Run: pip install google-generativeai")
            self._genai = None
        except Exception as e:
            logger.error(f"Failed to initialize GeminiClient: {e}")
            self._genai = None

    async def generate_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        if not self._genai:
            raise LLMGenerationError(
                "GeminiClient not initialized. Check GOOGLE_API_KEY in .env "
                "and ensure 'google-generativeai' is installed."
            )

        logger.info(f"[Gemini] generate_completion | model={self.model_name}")
        try:
            model = self._genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt,
                generation_config=self._genai.types.GenerationConfig(
                    temperature=temperature,
                ),
            )
            # GenerativeModel.generate_content_async for async support
            response = await model.generate_content_async(user_prompt)
            return response.text
        except Exception as e:
            logger.error(f"[Gemini] generate_completion error: {e}")
            raise LLMGenerationError(f"Failed to generate completion: {e}")

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict:
        """
        Gemini supports JSON mode via response_mime_type='application/json'.
        """
        if not self._genai:
            raise LLMGenerationError("GeminiClient not initialized.")

        logger.info(f"[Gemini] generate_json | model={self.model_name}")
        try:
            model = self._genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt,
                generation_config=self._genai.types.GenerationConfig(
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )
            response = await model.generate_content_async(user_prompt)
            raw = response.text.strip()
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"[Gemini] JSON parse error: {e}")
            raise LLMGenerationError(f"Gemini returned invalid JSON: {e}")
        except Exception as e:
            logger.error(f"[Gemini] generate_json error: {e}")
            raise LLMGenerationError(f"Failed to generate JSON completion: {e}")
