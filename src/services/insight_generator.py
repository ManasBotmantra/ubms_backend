import json
import pandas as pd
from src.domain.interfaces import ILLMClient
from src.config.base import ApiConfig
from src.services.json_formatting import format_dates_for_json
from src.core.logger import logger

class InsightGenerator:
    def __init__(self, llm_client: ILLMClient, api_config: ApiConfig):
        self.llm_client = llm_client
        self.api_config = api_config

    async def generate_insight(self, intent: str, df: pd.DataFrame) -> str:
        """
        Generates markdown insights based on the query intent and resulting data.
        """
        logger.info(f"Generating insights for intent: {intent}")
        
        # Limit rows for LLM context to prevent token overflow
        preview_df = format_dates_for_json(df.head(50))
        data_json = preview_df.to_json(orient="records")
        
        system_prompt = self.api_config.agent_prompts.insight_generator_system_prompt
        
        user_prompt = f"Intent: {intent}\nData Sample:\n{data_json}"
        
        try:
            insight = await self.llm_client.generate_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.2
            )
            return insight
        except Exception as e:
            logger.error(f"Error generating insight: {str(e)}")
            return "Unable to generate insights at this time due to an error."
