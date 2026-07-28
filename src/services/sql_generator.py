import json
import re
import datetime
from string import Template
from typing import Optional, List
import sqlglot
from sqlglot import exp
from src.domain.interfaces import ILLMClient
from src.domain.models import DatasetMetadata, ChatMessage
from src.config.base import ApiConfig
from src.core.exceptions import SQLGenerationRefusedError
from src.core.logger import logger


def _check_columns_exist(sql_query: str, metadata: DatasetMetadata) -> Optional[str]:
    """
    Deterministically verifies every column referenced in the query actually exists in the
    table it's queried against, per metadata.schema_info (the auto-detected, real schema for
    THIS dataset instance -- e.g. a dynamic table like grouped_results may only have the
    columns for whichever group_by dimensions were actually fetched, regardless of what the
    predefined schema hints describe as theoretically possible).

    Returns an error message string if a referenced column doesn't exist, else None.
    Only checks single-table queries (no JOINs) -- that's the only shape this system's
    generator is instructed to produce, and resolving columns across a join is ambiguous
    without deeper alias tracking.
    """
    try:
        parsed = sqlglot.parse_one(sql_query, dialect="duckdb")
    except Exception:
        return None  # let the real SQL validator surface parse errors

    tables = {t.name for t in parsed.find_all(exp.Table)}
    if len(tables) != 1:
        return None

    table_name = next(iter(tables))
    if table_name not in metadata.schema_info:
        return None

    valid_columns = set(metadata.schema_info[table_name].keys())
    valid_columns |= {a.alias for a in parsed.find_all(exp.Alias) if a.alias}

    referenced = {c.name for c in parsed.find_all(exp.Column) if c.name}
    missing = sorted(referenced - valid_columns)
    if not missing:
        return None

    return (
        f"The generated query referenced column(s) {missing} which don't exist in table "
        f"'{table_name}' for this dataset (available columns: {sorted(valid_columns)}). "
        "This usually means the requested breakdown needs data that isn't in what's "
        "currently loaded -- try rephrasing to ask for a fresh fetch with the right grouping."
    )


def _build_schema_context(metadata: DatasetMetadata) -> str:
    """
    Builds the schema + statistics context string for the SQL generator prompt.
    If schema_hints are available, they are prepended before the auto-detected schema
    so the LLM understands the semantic meaning of each column.
    """
    parts = []

    if metadata.schema_hints:
        parts.append(
            "=== PREDEFINED SCHEMA CONTEXT (authoritative — read this to understand column meanings) ==="
        )
        for table_name, table_hint in metadata.schema_hints.items():
            parts.append(f"\nTable: {table_name}")
            parts.append(f"  Description: {table_hint.get('description', '')}")
            parts.append("  Columns:")
            actual_columns = metadata.schema_info.get(table_name, {})
            
            for col_name, col_info in table_hint.get("columns", {}).items():
                if col_name in actual_columns:
                    parts.append(
                        f"    - {col_name} ({col_info.get('type', 'unknown')}): "
                        f"{col_info.get('description', '')}"
                    )
        parts.append("\n=== AUTO-DETECTED SCHEMA (raw types from DuckDB profiling) ===")

    parts.append(json.dumps(metadata.schema_info, indent=2))
    return "\n".join(parts)


class SQLGenerator:
    def __init__(self, llm_client: ILLMClient, api_config: ApiConfig):
        self.llm_client = llm_client
        self.api_config = api_config

    async def generate_sql(
        self,
        intent: str,
        metadata: DatasetMetadata,
        chat_history: Optional[List[ChatMessage]] = None,
    ) -> str:
        """
        Generates DuckDB SQL using the LLM based on user query and dataset metadata.
        """
        logger.info(f"Generating SQL for query: {intent}")

        if metadata is None:
            raise ValueError("Cannot generate SQL: no dataset metadata is available (dataset not loaded).")

        table_names = list(metadata.schema_info.keys())

        history_context = ""
        if chat_history:
            formatted_history = "\n".join(
                [f"{msg.role.capitalize()}: {msg.content}" for msg in chat_history]
            )
            history_context = (
                f"\n\nPrevious Conversation Context:\n{formatted_history}\n\n"
                "Use this context to resolve any ambiguities (e.g. if the intent says "
                "'group that by region', refer to the history to know what 'that' refers to)."
            )

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        schema_context = _build_schema_context(metadata)

        system_prompt_template = Template(self.api_config.agent_prompts.sql_generator_system_prompt)
        system_prompt = system_prompt_template.safe_substitute(
            history_context=history_context,
            table_names=", ".join(table_names),
            now_str=now_str,
            schema_context=schema_context,
            statistics=json.dumps(metadata.statistics, indent=2)
        )

        try:
            sql_query = await self.llm_client.generate_completion(
                system_prompt=system_prompt,
                user_prompt=intent,
                temperature=0.2,
            )
            # Cleanup in case LLM still outputs markdown ticks
            sql_query = sql_query.strip()
            if sql_query.startswith("```sql"):
                sql_query = sql_query[6:]
            if sql_query.startswith("```"):
                sql_query = sql_query[3:]
            if sql_query.endswith("```"):
                sql_query = sql_query[:-3]

            # Bulletproof cleanup for the GROUP BY alias hallucination
            sql_query = re.sub(
                r'GROUP BY\s+([a-zA-Z0-9_.]+)\s+AS\s+[a-zA-Z0-9_]+',
                r'GROUP BY \1',
                sql_query,
                flags=re.IGNORECASE,
            )
            sql_query = sql_query.strip()

            # The system prompt tells the LLM to refuse (in plain English) rather than query a
            # table it's been told is empty (see the EMPTY TABLE GUARD rule). Catch that refusal
            # here instead of letting prose reach the SQL validator/executor -- an unescaped
            # apostrophe in a sentence like "you'd" reads to the SQL tokenizer as an unterminated
            # string literal and blows up with a confusing tokenizer error.
            if not re.match(r'^\s*(SELECT|WITH)\b', sql_query, re.IGNORECASE):
                logger.warning(f"SQL generator declined to produce SQL: {sql_query}")
                raise SQLGenerationRefusedError(sql_query)

            column_error = _check_columns_exist(sql_query, metadata)
            if column_error:
                logger.warning(f"Generated SQL references non-existent column(s): {column_error}")
                raise SQLGenerationRefusedError(column_error)

            return sql_query
        except Exception as e:
            logger.error(f"Error generating SQL: {str(e)}")
            raise
