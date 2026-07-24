import json
from typing import List, Dict, Any, Optional
from string import Template
from src.domain.interfaces import ILLMClient
from src.domain.models import DatasetMetadata, ChatMessage
from src.config.base import ApiConfig
from src.core.logger import logger


def _build_schema_context(metadata: Optional[DatasetMetadata]) -> str:
    """
    Builds the schema context string for the planner prompt.
    If schema_hints are available (from ApiConfig.duckdb_schema_hints), they are
    prepended before the auto-detected schema, giving the LLM semantic understanding.
    """
    if not metadata:
        return "NO DATASET AVAILABLE"

    parts = []

    # Predefined semantic hints (rich context)
    if metadata.schema_hints:
        parts.append("=== PREDEFINED SCHEMA CONTEXT (authoritative — use this to understand column semantics) ===")
        for table_name, table_hint in metadata.schema_hints.items():
            parts.append(f"\nTable: {table_name}")
            parts.append(f"  Description: {table_hint.get('description', '')}")
            parts.append("  Columns:")
            # Get actual columns present in the table
            actual_columns = metadata.schema_info.get(table_name, {})
            
            for col_name, col_info in table_hint.get("columns", {}).items():
                if col_name in actual_columns:
                    parts.append(
                        f"    - {col_name} ({col_info.get('type', 'unknown')}): "
                        f"{col_info.get('description', '')}"
                    )
        parts.append("\n=== AUTO-DETECTED SCHEMA (data types from DuckDB profiling) ===")

    # Auto-detected schema
    parts.append(json.dumps(metadata.schema_info, indent=2))
    return "\n".join(parts)


class PlannerAgent:
    def __init__(self, llm_client: ILLMClient, api_config: ApiConfig):
        self.llm_client = llm_client
        self.api_config = api_config

    async def plan_dashboard(
        self,
        user_message: str,
        metadata: Optional[DatasetMetadata],
        chat_history: Optional[List[ChatMessage]] = None,
    ) -> Dict[str, Any]:
        """
        Plans the response by deciding which block types and data are needed.
        Returns a JSON structure detailing the plan.
        """
        logger.info(f"Planning dashboard for message: {user_message}")

        history_context = ""
        if chat_history:
            formatted_history = "\n".join(
                [f"{msg.role.capitalize()}: {msg.content}" for msg in chat_history]
            )
            history_context = (
                f"\n\nPrevious Conversation Context:\n{formatted_history}\n\n"
                "Take this context into account when deciding what the user is currently asking for."
            )

        schema_context = _build_schema_context(metadata)

        system_prompt_template = Template(self.api_config.agent_prompts.planner_system_prompt)
        system_prompt = system_prompt_template.safe_substitute(
            history_context=history_context,
            schema_context=schema_context
        )

        try:
            plan = await self.llm_client.generate_json(system_prompt, user_message, temperature=0.2)
            return plan
        except Exception as e:
            logger.error(f"Error planning dashboard: {str(e)}")
            # Fallback plan
            return {
                "blocks": [
                    {
                        "type": "TABLE",
                        "title": "Data Table",
                        "intent": user_message,
                    },
                    {
                        "type": "MARKDOWN",
                        "title": "Analysis",
                        "intent": "Summarize the data table results.",
                    },
                ]
            }
