"""
src/services/data_decision_agent.py
=====================================
LLM-powered agent that decides whether to fetch fresh data from the active API
and what parameters to use.

Now config-driven: reads filter schema, group_by config, and prompt fragments
from the active ApiConfig. No hardcoded transaction-log-specific fields here.

This is a stateless component — all context comes in via the arguments.
"""
import calendar
import json
import datetime
from typing import List, Optional

from src.config.base import ApiConfig
from src.domain.interfaces import ILLMClient
from src.domain.models import ChatMessage, DataDecision, DatasetMetadata
from src.core.logger import logger


def _sanitize_date_string(date_str: str) -> str:
    """Validates and fixes invalid calendar dates (e.g. 29-02-2026 -> 28-02-2026)."""
    if not isinstance(date_str, str) or not date_str.strip():
        return date_str

    s = date_str.strip()
    parts = s.split("-")
    if len(parts) == 3:
        try:
            # Format could be DD-MM-YYYY or YYYY-MM-DD
            if len(parts[0]) == 4:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                fmt = "ISO"
            else:
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                fmt = "DD-MM-YYYY"

            if 1 <= month <= 12 and year > 1900:
                _, max_days = calendar.monthrange(year, month)
                if day > max_days:
                    logger.warning(
                        f"Clamping invalid date day {day} for month {month}/{year} to max valid day {max_days}"
                    )
                    day = max_days

                if fmt == "ISO":
                    return f"{year:04d}-{month:02d}-{day:02d}"
                else:
                    return f"{day:02d}-{month:02d}-{year:04d}"
        except Exception:
            pass
    return date_str


def _build_filter_schema_block(api_config: ApiConfig) -> str:
    """Converts ApiConfig.filter_schema into a readable prompt block."""
    lines = ["filters:"]
    for field_name, field_cfg in api_config.filter_schema.fields.items():
        parts = [f"  {field_name}: {field_cfg.type}"]
        if field_cfg.required:
            parts.append("(REQUIRED)")
        if field_cfg.allowed_values:
            parts.append(f"— allowed: {field_cfg.allowed_values}")
        lines.append(" ".join(parts))
        if field_cfg.description:
            lines.append(f"    {field_cfg.description}")
    gb = api_config.filter_schema.group_by
    if gb.allowed:
        lines.append(f"group_by: list[string] — allowed: {gb.allowed} | default: {gb.default}")
    return "\n".join(lines)


class DataDecisionAgent:
    """
    Uses the LLM to analyse the user message + history and decide:
      1. Does the user need fresh data from the active API?
      2. If yes, what filters and group_by should be used?

    Falls back to needs_fetch=False on any LLM/parsing failure (safe default —
    the user can retry or the planner will ask them to clarify).
    """

    def __init__(self, llm_client: ILLMClient, api_config: ApiConfig) -> None:
        self.llm_client = llm_client
        self.api_config = api_config

    async def decide(
        self,
        user_message: str,
        chat_history: Optional[List[ChatMessage]],
        has_valid_dataset: bool,
        metadata: Optional[DatasetMetadata] = None,
    ) -> DataDecision:
        """
        Args:
            user_message       — the latest user message
            chat_history       — prior turns (from frontend, stateless)
            has_valid_dataset  — True if a DuckDB file for the current
                                 dataset_id already exists on disk
            metadata           — the currently loaded dataset's metadata, if any. Its
                                 applied_filters/applied_group_by (the REAL params the
                                 loaded data was fetched with) are shown to the LLM so it
                                 can compare the user's new request against what's actually
                                 loaded, instead of guessing from conversation text alone —
                                 which can be confidently wrong (e.g. deciding "same data
                                 already loaded for account X" when the loaded dataset is
                                 actually unfiltered/org-wide).

        Returns:
            DataDecision with needs_fetch and optional generic fetch_params dict
        """
        logger.info(
            f"DataDecisionAgent.decide | api={self.api_config.name} | "
            f"has_valid_dataset={has_valid_dataset} | "
            f"message='{user_message[:80]}...'"
        )

        history_context = ""
        if chat_history:
            lines = [f"{m.role.capitalize()}: {m.content}" for m in chat_history[-6:]]
            history_context = "\n\nConversation History (last 6 turns):\n" + "\n".join(lines)

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filter_schema_block = _build_filter_schema_block(self.api_config)
        prompts = self.api_config.agent_prompts
        gb_cfg = self.api_config.filter_schema.group_by

        if has_valid_dataset and metadata and metadata.applied_filters is not None:
            loaded_filters_block = (
                "Currently loaded dataset filters (this is what's ACTUALLY loaded — compare "
                "the user's request against these real values, do not assume from conversation "
                "text alone):\n"
                f"  filters: {json.dumps(metadata.applied_filters)}\n"
                f"  group_by: {json.dumps(metadata.applied_group_by or [])}"
            )
        elif has_valid_dataset:
            # Loaded, but we don't know what it was fetched with (e.g. a file upload, or older
            # metadata predating this field) -- fall back to the old, less precise signal.
            loaded_filters_block = "Currently loaded dataset filters: unknown (assume it may not match a newly-specified filter)."
        else:
            loaded_filters_block = "No dataset is currently loaded."

        def get_sample_val(cfg_type: str):
            if cfg_type == "string":
                return "<value>"
            elif cfg_type.startswith("dict"):
                return {}
            elif cfg_type.startswith("list"):
                return []
            return None

        sample_filters = {
            field: get_sample_val(cfg.type)
            for field, cfg in self.api_config.filter_schema.fields.items()
        }
        
        sample_output_true = json.dumps(
            {
                "needs_fetch": True,
                "fetch_params": {
                    "filters": sample_filters,
                    "group_by": gb_cfg.default,
                },
                "reason": "User requested data for <org> in <period>",
            },
            indent=2,
        )
        sample_output_false = json.dumps(
            {
                "needs_fetch": False,
                "fetch_params": None,
                "reason": "User is drilling down / casual question / unrelated topic",
            },
            indent=2,
        )

        system_prompt = f"""You are a Data Context Manager for an AI analytics chatbot.
Your only job is to decide whether the user's request needs FRESH data fetched
from the {self.api_config.display_name or self.api_config.name} API,
and if so, to extract the API call parameters from their message AND the Conversation History.
If a required field is not explicitly mentioned in the latest message, you MUST extract it from the Conversation History to maintain context.

=== CONTEXT ===
Current datetime: {now_str}
has_valid_dataset: {has_valid_dataset}
(True = there is already data loaded for this session)
{loaded_filters_block}
{history_context}

=== API: {self.api_config.display_name or self.api_config.name} ===
{prompts.context_hint}

=== FILTER SCHEMA (produce this when needs_fetch=true) ===
{filter_schema_block}

=== GROUP BY RULES ===
{prompts.group_by_rules}

=== DATE RANGE RULES ===
{prompts.date_range_rules}

=== DECISION RULES ===
1. NEEDS FETCH = TRUE when:
   - has_valid_dataset is False AND the user is asking for analytics data
   - The user explicitly asks for a different period, filter, or grouping
   - The user says "refresh", "reload", "get new data", "latest", "update"
   - CRITICAL: The filters implied by the user's CURRENT message (account, status, period, or
     any other filter field) differ in ANY way from "Currently loaded dataset filters" above —
     e.g. loaded filters show no account set but the user now names one, or loaded filters show
     one account/period and the user names a different one. When in doubt about whether it
     matches, prefer NEEDS FETCH = TRUE — serving stale/mismatched data is worse than an extra
     fetch. Do NOT rely on conversation history text alone to decide this; the loaded filters
     shown above are the ground truth for what's actually loaded.
   - CRITICAL: The user's request implies a breakdown/cross-tab/trend (e.g. "by category",
     "trends", "over time", "per month") that needs a grouping dimension NOT already present in
     the loaded dataset's "group_by" shown above — e.g. loaded group_by is empty or only
     ["utility_category"] but the user now asks for a per-month or per-period trend, which
     needs the time dimension added too. A missing grouping dimension is a mismatch just like a
     missing filter — prefer NEEDS FETCH = TRUE with group_by updated to include the dimension(s)
     the question needs, rather than reusing a dataset that structurally cannot answer it.
{prompts.decision_rules_extra}

2. NEEDS FETCH = FALSE when:
   - has_valid_dataset is True AND the user asks about the SAME data already loaded — verified
     against "Currently loaded dataset filters" above, not just inferred from conversation tone
   - The user is doing CASUAL CONVERSATION (greetings, thank-you, general questions)
   - The user's message is unclear and required filter fields cannot be determined
   - The message is about the chatbot itself, capabilities, or non-analytics topics

=== OUTPUT FORMAT ===
Return ONLY valid JSON. No markdown, no explanation, no code blocks.

When needs_fetch=true:
{sample_output_true}

When needs_fetch=false:
{sample_output_false}
"""

        try:
            raw = await self.llm_client.generate_completion(
                system_prompt=system_prompt,
                user_prompt=user_message,
                temperature=0.0,
            )
            raw = raw.strip()
            # Strip any accidental markdown fences
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]

            parsed = json.loads(raw.strip())
            decision = self._validate_and_build(parsed)
            logger.info(
                f"DataDecisionAgent decision: needs_fetch={decision.needs_fetch} | "
                f"reason='{decision.reason}'"
            )
            return decision

        except Exception as e:
            logger.error(f"DataDecisionAgent LLM/parse error: {e} — defaulting to needs_fetch=False")
            return DataDecision(
                needs_fetch=False,
                fetch_params=None,
                reason=f"Decision agent error (safe fallback): {str(e)}",
            )

    def _apply_filter_defaults(self, filters: dict) -> dict:
        """
        Ensures every filter field has a sensible default when the LLM omits it.
        """
        result = dict(filters)

        for field_name, field_cfg in self.api_config.filter_schema.fields.items():
            if field_name in result:
                continue  # LLM provided it — keep as-is

            if field_cfg.type.startswith("list"):
                result[field_name] = []
            elif field_cfg.type == "string":
                result[field_name] = ""
            elif field_cfg.type.startswith("dict"):
                result[field_name] = {}
            else:
                result[field_name] = None

        return result

    def _validate_and_build(self, parsed: dict) -> DataDecision:
        """
        Validates the LLM JSON output and constructs a DataDecision.
        Clamps group_by to only the allowed values from config.
        Clamps any list-of-string filter field to its allowed_values if specified.
        Applies defaults to any omitted optional fields.
        """
        needs_fetch: bool = bool(parsed.get("needs_fetch", False))
        reason: str = parsed.get("reason", "")
        fetch_params_raw = parsed.get("fetch_params")

        if not needs_fetch:
            return DataDecision(needs_fetch=False, fetch_params=None, reason=reason)

        if fetch_params_raw is None:
            logger.warning("LLM set needs_fetch=true but fetch_params is null — defaulting to False")
            return DataDecision(
                needs_fetch=False,
                fetch_params=None,
                reason="Missing fetch_params despite needs_fetch=true (LLM error)",
            )

        # Guard: fetch_params must be a dict
        if not isinstance(fetch_params_raw, dict):
            logger.warning(f"fetch_params is not a dict (got {type(fetch_params_raw)}) — defaulting to False")
            return DataDecision(
                needs_fetch=False,
                fetch_params=None,
                reason=f"Invalid fetch_params type from LLM: {type(fetch_params_raw).__name__}",
            )

        filters_raw = fetch_params_raw.get("filters", {})

        # Guard: filters must be a dict
        if not isinstance(filters_raw, dict):
            logger.warning(f"filters is not a dict (got {type(filters_raw)}) — defaulting to False")
            return DataDecision(
                needs_fetch=False,
                fetch_params=None,
                reason=f"Invalid filters type from LLM: {type(filters_raw).__name__}",
            )

        gb_cfg = self.api_config.filter_schema.group_by

        # Validate required fields
        for field_name, field_cfg in self.api_config.filter_schema.fields.items():
            if field_cfg.required:
                val = filters_raw.get(field_name)
                if not val or (isinstance(val, str) and not val.strip()):
                    logger.warning(
                        f"Required filter field '{field_name}' is empty — defaulting to needs_fetch=False"
                    )
                    return DataDecision(
                        needs_fetch=False,
                        fetch_params=None,
                        reason=f"Required field '{field_name}' could not be extracted from user message",
                    )

        # Clamp list-of-string fields to their allowed_values
        cleaned_filters = dict(filters_raw)
        for field_name, field_cfg in self.api_config.filter_schema.fields.items():
            if field_cfg.allowed_values and field_name in cleaned_filters:
                raw_vals = cleaned_filters[field_name]
                if isinstance(raw_vals, list):
                    cleaned_filters[field_name] = [
                        v for v in [str(v).upper() for v in raw_vals]
                        if v in [a.upper() for a in field_cfg.allowed_values]
                    ]

        # Sanitize any date-like string filter values (from_date, to_date, day_value)
        for date_field in ("from_date", "to_date", "day_value"):
            if date_field in cleaned_filters and isinstance(cleaned_filters[date_field], str):
                cleaned_filters[date_field] = _sanitize_date_string(cleaned_filters[date_field])

        # Apply defaults for fields the LLM omitted (replaces Pydantic model defaults)
        cleaned_filters = self._apply_filter_defaults(cleaned_filters)

        # Clamp group_by to allowed dimensions
        raw_group_by = fetch_params_raw.get("group_by", gb_cfg.default)
        if not isinstance(raw_group_by, list):
            raw_group_by = list(gb_cfg.default)
        group_by = [g for g in raw_group_by if not gb_cfg.allowed or g in gb_cfg.allowed]
        if not group_by:
            group_by = list(gb_cfg.default)

        logger.info(
            f"DataDecisionAgent validated fetch_params: "
            f"filters_keys={list(cleaned_filters.keys())} | group_by={group_by}"
        )

        return DataDecision(
            needs_fetch=True,
            fetch_params={
                "filters": cleaned_filters,
                "group_by": group_by,
            },
            reason=reason,
        )

