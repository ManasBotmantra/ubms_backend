import asyncio
import json
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from src.domain.models import Block, BlockType, DatasetMetadata, ChatMessage
from src.services.planner_agent import PlannerAgent
from src.services.sql_generator import SQLGenerator
from src.services.sql_executor import SQLExecutor
from src.services.insight_generator import InsightGenerator
from src.services.data_decision_agent import DataDecisionAgent
from src.services.json_formatting import format_dates_for_json
from src.infrastructure.llm_client import reset_tokens, get_tokens
from src.core.exceptions import SQLGenerationRefusedError
from src.core.logger import logger


class ResponseBuilder:
    def __init__(
        self,
        planner: PlannerAgent,
        sql_generator: SQLGenerator,
        sql_executor: SQLExecutor,
        insight_generator: InsightGenerator,
        data_decision_agent: DataDecisionAgent,
        dataset_service,           # IDatasetService — injected to call fetch_from_api
        db_engine,                 # IDatabaseEngine — injected to call dataset_exists
    ):
        self.planner = planner
        self.sql_generator = sql_generator
        self.sql_executor = sql_executor
        self.insight_generator = insight_generator
        self.data_decision_agent = data_decision_agent
        self.dataset_service = dataset_service
        self.db_engine = db_engine

    async def _friendly_error_message(self, raw_error: str, has_fallback_data: bool) -> str:
        """Turns a raw backend/network error into a short, plain-language explanation for the
        end user, instead of exposing raw error text or generic boilerplate that may not even
        apply (e.g. "check your credentials" for a business-validation error like an unknown
        account). Reuses InsightGenerator's llm_client rather than adding a new constructor
        dependency. Falls back to the raw error text (not misleading boilerplate) if the LLM
        call itself fails. """
        context = (
            "The user's previously loaded data is still available and can still be asked about."
            if has_fallback_data else
            "No data is currently loaded for this conversation."
        )
        system_prompt = (
            "You are explaining a technical error from a bill-analytics API to a non-technical "
            "end user of a chatbot. Given the raw error message below, write a SHORT (1-2 "
            "sentences) plain-language explanation of what went wrong and, if relevant, what the "
            "user should try instead (e.g. check a value they typed, rephrase, or try again). "
            "Do not show raw JSON, status codes, or technical jargon. Do not guess at causes not "
            "supported by the error text — if it clearly names something invalid (like an "
            "unrecognized account), just say that plainly.\n\n"
            f"Context: {context}"
        )
        try:
            return await self.insight_generator.llm_client.generate_completion(
                system_prompt=system_prompt,
                user_prompt=raw_error,
                temperature=0.2,
            )
        except Exception:
            return raw_error

    async def build_response(
        self,
        user_message: str,
        dataset_id: Optional[str],
        metadata: Optional[DatasetMetadata],
        chat_history: Optional[List[ChatMessage]] = None,
        authorize_token: Optional[str] = None,
    ) -> Tuple[List[Block], Optional[str]]:
        """
        Orchestrates the full chat response pipeline.

        New Phase 1 & 2 (before the existing pipeline):
          Phase 1 — DataDecisionAgent: decide if fresh API fetch is needed
          Phase 2 — Fetch from API if required (updates dataset_id + metadata)

        Existing Phases (unchanged):
          Phase 3 — PlannerAgent: plan dashboard blocks
          Phase 4 — Per-block: SQL generation → execution → formatting

        Returns:
            (List[Block], resolved_dataset_id)
            The resolved_dataset_id may differ from the input dataset_id when a
            fresh API fetch created a new DuckDB file.
        """
        reset_tokens()
        blocks_res: List[Block] = []
        resolved_dataset_id = dataset_id

        # ── Phase 1: Data Decision ────────────────────────────────────────────
        has_valid_dataset = (
            dataset_id is not None
            and self.db_engine.dataset_exists(dataset_id)
        )

        decision = await self.data_decision_agent.decide(
            user_message=user_message,
            chat_history=chat_history,
            has_valid_dataset=has_valid_dataset,
            metadata=metadata,
        )
        logger.info(
            f"Data decision: needs_fetch={decision.needs_fetch} | "
            f"has_valid_dataset={has_valid_dataset} | reason='{decision.reason}'"
        )

        # ── Phase 2: Fetch from API if needed ────────────────────────────────
        if decision.needs_fetch and not has_valid_dataset:
            try:
                fp = decision.fetch_params  # generic dict: {"filters": {...}, "group_by": [...]}
                resolved_dataset_id, metadata = await self.dataset_service.fetch_from_api(
                    filters=fp["filters"],
                    group_by=fp.get("group_by", []),
                    authorize_token=authorize_token,
                )
                logger.info(f"Fetched fresh data — new dataset_id={resolved_dataset_id}")
            except Exception as e:
                logger.error(f"API fetch failed: {e}")
                # Return a user-friendly error block — do NOT crash the request.
                # Use the incoming dataset_id (may be None) as fallback.
                friendly = await self._friendly_error_message(str(e), has_fallback_data=False)
                return (
                    [Block(type=BlockType.MARKDOWN, title="Data Fetch Error", data=f"⚠️ {friendly}")],
                    dataset_id,  # fall back to the incoming dataset_id (may be None)
                )

        elif decision.needs_fetch and has_valid_dataset:
            # LLM wanted a re-fetch — respect its intent (covers different period/org requests)
            logger.info("needs_fetch=True and has_valid_dataset=True — re-fetching per LLM decision")
            try:
                fp = decision.fetch_params  # generic dict
                resolved_dataset_id, metadata = await self.dataset_service.fetch_from_api(
                    filters=fp["filters"],
                    group_by=fp.get("group_by", []),
                    authorize_token=authorize_token,
                )
                logger.info(f"Re-fetched data — new dataset_id={resolved_dataset_id}")
            except Exception as e:
                logger.error(f"API re-fetch failed: {e}")
                # Surface the failure instead of silently querying the stale, previously-loaded
                # dataset — that would answer using the OLD filters' data while the planner/SQL
                # block titles (built from the NEW user_message) claim it matches the new
                # request, producing a confusing, wrong-looking mismatch (e.g. titled "WATER-002"
                # but every row is actually WATER-001's stale data).
                friendly = await self._friendly_error_message(str(e), has_fallback_data=True)
                return (
                    [Block(type=BlockType.MARKDOWN, title="Data Fetch Error", data=f"⚠️ {friendly}")],
                    dataset_id,  # keep the existing (still-valid) dataset_id, not the failed resolved one
                )

        # ── Handle: No data at all and no fetch ──────────────────────────────
        if not has_valid_dataset and not decision.needs_fetch and metadata is None:
            # Either casual chat or unrelated question — pass through to planner
            # with no metadata; PlannerAgent will return a friendly NONE-intent block
            logger.info("No dataset, no fetch needed — passing to planner for conversational response")

        # ── Phase 3+: Existing pipeline (planner → SQL → blocks) ─────────────
        plan = await self.planner.plan_dashboard(user_message, metadata, chat_history)
        planned_blocks = plan.get("blocks", [])

        async def process_single_block(p_block):
            try:
                if not isinstance(p_block, dict):
                    logger.warning(f"Skipping non-dict block from planner: {type(p_block)} — {p_block}")
                    return None

                b_type = p_block.get("type")
                b_title = p_block.get("title")
                b_intent = p_block.get("intent", user_message)
                b_colors = p_block.get("colors", None)
                b_text = p_block.get("text", "")

                # Check if valid block type
                try:
                    block_type_enum = BlockType(b_type)
                except ValueError:
                    logger.warning(f"Unknown block type {b_type}, defaulting to MARKDOWN")
                    block_type_enum = BlockType.MARKDOWN

                if b_intent == "NONE":
                    # Bypass SQL and Insight generation for casual chat / unrelated questions
                    return Block(
                        type=block_type_enum,
                        title=b_title,
                        data=b_text,
                        colors=b_colors,
                    )

                # Phase 4a: Generate SQL
                # Guard: if metadata is None no dataset is loaded — SQL generation would
                # crash on metadata.schema_info. Surface a friendly message instead.
                if metadata is None:
                    logger.warning("process_single_block: metadata is None, skipping SQL generation")
                    return Block(
                        type=BlockType.MARKDOWN,
                        title=b_title or "No Data",
                        data="No data has been loaded yet. Please ask a question about your bills or spending and the assistant will fetch the data automatically.",
                    )
                sql_query = await self.sql_generator.generate_sql(b_intent, metadata, chat_history)

                # Phase 4b: Execute SQL
                df_result = self.sql_executor.execute(resolved_dataset_id, sql_query)

                # Phase 4c: Format data into block
                if block_type_enum == BlockType.MARKDOWN:
                    insight_text = await self.insight_generator.generate_insight(b_intent, df_result)
                    return Block(type=block_type_enum, title=b_title, data=insight_text, colors=b_colors)

                elif block_type_enum == BlockType.KPI_CARD:
                    val = None
                    if not df_result.empty:
                        val = df_result.iloc[0, 0]
                        if isinstance(val, (np.integer, np.floating)):
                            val = val.item()
                        elif isinstance(val, pd.Timestamp):
                            val = val.strftime("%d-%m-%Y") if not pd.isna(val) else None
                    return Block(type=block_type_enum, title=b_title, data={"value": val}, colors=b_colors)

                else:
                    # CHART or TABLE: return records. Dates are pre-formatted to DD-MM-YYYY
                    # (see json_formatting.py) since to_json's date_format only offers iso/epoch.
                    df_clean = format_dates_for_json(df_result).replace({np.nan: None})
                    records = json.loads(df_clean.to_json(orient="records"))
                    return Block(type=block_type_enum, title=b_title, data=records, colors=b_colors)

            except SQLGenerationRefusedError as e:
                # The SQL generator LLM declined (e.g. the only table that could answer this
                # needs a fresh fetch/different grouping) and its message is already
                # user-appropriate plain language -- surface it as-is, not as a failure.
                logger.warning(f"SQL generation refused for block {p_block}: {str(e)}")
                return Block(
                    type=BlockType.MARKDOWN,
                    title=p_block.get("title", "Note") if isinstance(p_block, dict) else "Note",
                    data=str(e),
                )
            except Exception as e:
                logger.error(f"Error processing block {p_block}: {str(e)}")
                return Block(
                    type=BlockType.MARKDOWN,
                    title=p_block.get("title", "Error") if isinstance(p_block, dict) else "Error",
                    data=f"Failed to load this section: {str(e)}",
                )

        tasks = [process_single_block(b) for b in planned_blocks]
        results = await asyncio.gather(*tasks)
        for b in results:
            if b is not None:
                blocks_res.append(b)

        # Log total token usage for the request
        p_tok, c_tok = get_tokens()
        logger.info("=" * 40)
        logger.info("QUERY LLM TOKEN USAGE SUMMARY")
        logger.info(f"Input Tokens (Prompt):       {p_tok}")
        logger.info(f"Output Tokens (Completion):  {c_tok}")
        logger.info(f"Total Tokens:                {p_tok + c_tok}")
        logger.info("=" * 40)

        return blocks_res, resolved_dataset_id
