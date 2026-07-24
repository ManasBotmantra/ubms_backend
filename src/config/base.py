"""
src/config/base.py
==================
Pydantic base models that define the shape every API config must follow.
Add a new ApiConfig instance in src/config/api_configs/ to support a new API
without touching any service code.
"""

from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class AuthConfig(BaseModel):
    """Describes how to authenticate before fetching data."""
    type: Literal["bearer_token", "api_key_header", "basic_auth", "none", "passed_token"] = "none"

    # bearer_token: POST to an auth endpoint, extract a JWT/token from the response
    endpoint_env: Optional[str] = None
    """Name of the .env key that holds the auth URL (e.g. 'VALIDATE_USER')."""

    payload_template: Dict[str, str] = Field(default_factory=dict)
    """
    Maps payload field names to values.
    Values prefixed with '$' are resolved from settings at runtime.
    e.g. {"user_id": "$USER_ID", "password": "$PASSWORD"}
    """

    token_path: str = ""
    """
    Dot-separated path into the JSON response to extract the token.
    e.g. 'response_body.authorize_token'
    """

    inject_as_header: str = ""
    """Header name to inject the token into the subsequent fetch request."""

    # api_key_header: just read a key from settings and put it in a header
    api_key_env: Optional[str] = None
    """Name of the .env key that holds the API key."""

    api_key_header_name: str = "Authorization"
    """Header name to inject the API key into."""


# ---------------------------------------------------------------------------
# Response → DuckDB Table Mapping
# ---------------------------------------------------------------------------

class TableMappingConfig(BaseModel):
    """Describes one DuckDB table extracted from the API response body."""

    name: str
    """DuckDB table name registered in the database (e.g. 'grouped_results')."""

    path: str
    """
    Dot-separated path into the response_body dict to find this table's data.
    e.g. 'grouped_results', 'data.records'
    """

    type: Literal["single_row", "list_of_dicts"]
    """
    single_row    — dict   → pd.DataFrame([dict])
    list_of_dicts — list   → pd.DataFrame(list)
    """

    fallback_columns: List[str] = Field(default_factory=list)
    """
    Column names used to create an empty DataFrame when the API returns no rows.
    Ensures the LLM still sees a valid schema for SQL generation.
    """


# ---------------------------------------------------------------------------
# DuckDB Schema Hints — semantic descriptions injected into LLM prompts
# ---------------------------------------------------------------------------

class ColumnHint(BaseModel):
    """Human-readable description of one column — injected into LLM prompts."""
    type: str
    """DuckDB type string: TIMESTAMP, VARCHAR, INTEGER, FLOAT, BOOLEAN, etc."""
    description: str
    """Plain English description of what this column means and how to use it."""


class TableHint(BaseModel):
    """Human-readable description of one DuckDB table — injected into LLM prompts."""
    description: str
    """Table-level description explaining what rows represent."""
    columns: Dict[str, ColumnHint] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Filter Schema — tells DataDecisionAgent which fields to extract from user message
# ---------------------------------------------------------------------------

class FilterFieldConfig(BaseModel):
    """Describes one filter field the DataDecisionAgent can extract from the user."""
    type: str
    """
    Logical type: 'string', 'list[string]', 'date_range', 'integer', 'boolean'.
    Used to build the prompt's API schema section.
    """
    required: bool = False
    description: str = ""
    allowed_values: List[str] = Field(default_factory=list)
    """If non-empty, only these values are valid (e.g. ['SUCCESS', 'ERROR'])."""


class GroupByConfig(BaseModel):
    """Configuration for group_by parameter extraction."""
    allowed: List[str] = Field(default_factory=list)
    default: List[str] = Field(default_factory=list)


class FilterSchemaConfig(BaseModel):
    """Full filter schema for one API — used to dynamically build the agent prompt."""
    fields: Dict[str, FilterFieldConfig] = Field(default_factory=dict)
    group_by: GroupByConfig = Field(default_factory=GroupByConfig)


# ---------------------------------------------------------------------------
# Agent Prompt Fragments — per-API customisations injected into agent prompts
# ---------------------------------------------------------------------------

class AgentPromptConfig(BaseModel):
    """Per-API prompt fragments injected into the DataDecisionAgent system prompt."""
    context_hint: str = ""
    """What this API is and the most important rule (e.g. 'org_name is required')."""

    group_by_rules: str = ""
    """How to interpret user phrases like 'hourly', 'by status', 'monthly'."""

    date_range_rules: str = ""
    """How to parse relative dates: 'last week', 'this month', 'yesterday'."""

    decision_rules_extra: str = ""
    """Any extra NEEDS FETCH = TRUE/FALSE rules specific to this API."""

    planner_system_prompt: str = ""
    """The system prompt template for the PlannerAgent. Use $history_context and $schema_context for injection."""

    sql_generator_system_prompt: str = ""
    """The system prompt template for the SQLGenerator. Use $history_context, $table_names, $now_str, $schema_context, and $statistics."""

    insight_generator_system_prompt: str = ""
    """The system prompt template for the InsightGenerator."""


# ---------------------------------------------------------------------------
# Top-Level ApiConfig — the full contract for one external API source
# ---------------------------------------------------------------------------

class ApiConfig(BaseModel):
    """
    Complete, self-contained contract for one external API data source.

    To add support for a new API:
      1. Create  src/config/api_configs/<name>.py  with an ApiConfig instance.
      2. Register it in src/config/loader.py  _API_REGISTRY dict.
      3. Set  ACTIVE_API_CONFIG=<name>  in .env.

    No changes required in any service or infrastructure file.
    """

    name: str
    """Unique identifier — must match the key in _API_REGISTRY in loader.py."""

    display_name: str = ""
    """Human-readable name used in log messages."""

    # ── Auth ──────────────────────────────────────────────────────────────────
    auth: AuthConfig = Field(default_factory=AuthConfig)

    # ── Fetch ─────────────────────────────────────────────────────────────────
    fetch_endpoint_env: str
    """Name of the .env key that holds the data fetch URL (e.g. 'ENDPOINT')."""

    fetch_method: str = "POST"

    fetch_headers: Dict[str, str] = Field(default_factory=dict)

    payload_template: Dict[str, Any] = Field(default_factory=dict)
    """
    Maps request body keys to values or resolution tokens:
      '$filters'           → replaced with the full filters dict
      '$filters.org_name'  → replaced with filters['org_name']
      '$group_by'          → replaced with the group_by list
    """

    # ── Response Parsing ──────────────────────────────────────────────────────
    response_success_field: str = "status_code"
    response_success_value: Any = 200
    response_error_path: str = "error_message"
    response_data_root: str = "response_body"
    tables: List[TableMappingConfig] = Field(default_factory=list)

    # ── DataDecisionAgent Config ───────────────────────────────────────────────
    filter_schema: FilterSchemaConfig = Field(default_factory=FilterSchemaConfig)
    agent_prompts: AgentPromptConfig = Field(default_factory=AgentPromptConfig)

    # ── DuckDB Schema Hints ────────────────────────────────────────────────────
    duckdb_schema_hints: Dict[str, TableHint] = Field(default_factory=dict)
    """
    Predefined semantic descriptions of tables/columns produced by this API.
    Injected into SQL generator and planner prompts BEFORE the auto-detected schema,
    giving the LLM semantic context (not just type names).
    """

    # ── Metadata ──────────────────────────────────────────────────────────────
    dataset_source_label: str = "api"
    """Written to DatasetMetadata.source when data is fetched via this API."""
