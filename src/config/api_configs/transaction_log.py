"""
src/config/api_configs/transaction_log.py
==========================================
ApiConfig for the BotIQ Transaction Log API
(summarize_transaction_log_admin_api + validate_user_admin_api).

Previously this logic was scattered across:
  - src/infrastructure/api_client.py   (auth + fetch payload)
  - src/services/dataset_service.py    (response parsing, table names)
  - src/services/data_decision_agent.py (prompt filter schema)
  - src/domain/models.py               (TransactionLogFilters, DataFetchParams)

It now lives here in one place. No service code changes needed to swap APIs.
"""

from src.config.base import (
    AgentPromptConfig,
    ApiConfig,
    AuthConfig,
    ColumnHint,
    FilterFieldConfig,
    FilterSchemaConfig,
    GroupByConfig,
    TableHint,
    TableMappingConfig,
)

TRANSACTION_LOG_CONFIG = ApiConfig(
    name="transaction_log",
    display_name="BotIQ Transaction Log",

    # ── Auth ──────────────────────────────────────────────────────────────────
    auth=AuthConfig(
        type="passed_token",
        inject_as_header="authorize_token",
    ),

    # ── Fetch ─────────────────────────────────────────────────────────────────
    fetch_endpoint_env="ENDPOINT",
    fetch_method="POST",
    fetch_headers={
        "Content-Type": "application/json",
        "request_type": "application/json",
    },
    # Values starting with '$filters.' are resolved from the filters dict.
    # '$filters' alone is replaced with the full filters dict.
    # '$group_by' is replaced with the group_by list.
    payload_template={
        "org_name": "$filters.org_name",   # top-level duplicate — required by this API
        "filters": "$filters",
        "group_by": "$group_by",
    },

    # ── Response → DuckDB Tables ───────────────────────────────────────────────
    response_data_root="response_body",
    response_success_field="status_code",
    response_success_value=200,
    response_error_path="error_message",
    tables=[
        TableMappingConfig(
            name="summary",
            path="summary",
            type="single_row",
        ),
        TableMappingConfig(
            name="grouped_results",
            path="grouped_results",
            type="list_of_dicts",
            fallback_columns=[
                "date",
                "api_name",
                "matching_transaction_count",
                "passed",
                "failed",
                "initiated",
                "failure_rate_pct",
                "total_input_tokens",
                "total_output_tokens",
                "avg_latency_ms",
            ],
        ),
    ],

    # ── DataDecisionAgent Filter Schema ────────────────────────────────────────
    filter_schema=FilterSchemaConfig(
        fields={
            "org_name": FilterFieldConfig(
                type="string",
                required=True,
                description=(
                    "Organisation name — REQUIRED. "
                    "If the user has not mentioned an organisation, set needs_fetch=false."
                ),
            ),
            "date_range": FilterFieldConfig(
                type="dict {start: 'YYYY-MM-DD HH:MM:SS', end: 'YYYY-MM-DD HH:MM:SS'}",
                description=(
                    "Inclusive date range. Both start and end are datetime strings "
                    "in 'YYYY-MM-DD HH:MM:SS' format. If no date is mentioned, "
                    "use last 30 days (start = 30 days ago, end = now)."
                ),
            ),
            "api_names": FilterFieldConfig(
                type="list[string]",
                description=(
                    "Filter to specific API names (e.g. 'extract_document_data'). "
                    "Empty list = no filter."
                ),
            ),
            "keys": FilterFieldConfig(
                type="list[string]",
                description="Filter to specific API keys. Empty list = no filter.",
            ),
            "statuses": FilterFieldConfig(
                type="list[string]",
                description="Filter by transaction status. Empty list = all statuses.",
                allowed_values=["SUCCESS", "ERROR", "INITIATED"],
            ),
            "request_models": FilterFieldConfig(
                type="list[string]",
                description=(
                    "Filter by LLM model name (e.g. 'gpt-4o', 'claude-3-5-sonnet'). "
                    "Empty list = no filter."
                ),
            ),
        },
        group_by=GroupByConfig(
            allowed=["date", "hour", "week", "month", "api_name", "key", "status", "request_model"],
            default=["date", "api_name"],
        ),
    ),

    # ── Per-API Prompt Fragments ───────────────────────────────────────────────
    agent_prompts=AgentPromptConfig(
        context_hint=(
            "This API provides transaction analytics data for an organisation. "
            "org_name is REQUIRED — if the user has not mentioned an org name, "
            "set needs_fetch=false."
        ),
        group_by_rules="""
- If user does not specify: use the default ["date", "api_name"]
- If user mentions "hourly": use "hour" instead of "date"
- If user mentions "weekly": use "week"
- If user mentions "monthly" or "month-over-month": use "month"
- If user mentions "by key" or "per key": include "key"
- If user mentions "by status": include "status"
- If user mentions "by model": include "request_model"
""",
        date_range_rules="""
- "last week"  = 7 days before today
- "last month" = 30 days before today
- "this month" = first day of current month to now
- "today"      = today 00:00:00 to now
- "yesterday"  = yesterday 00:00:00 to yesterday 23:59:59
- Specific month (e.g. "May") = that month in the current year
- No date mentioned: use last 30 days (start = 30 days ago, end = now)
- Always format as "YYYY-MM-DD HH:MM:SS"
""",
        decision_rules_extra="""
- CRITICAL: If has_valid_dataset is False, you MUST set NEEDS FETCH = TRUE if the user is asking about transaction data or analytics, EVEN IF it is a follow-up question to previous history! The data has been lost and must be re-fetched.
- NEEDS FETCH = TRUE when: user mentions an org name AND is asking about transaction data/analytics
- NEEDS FETCH = FALSE when: casual conversation, general question, or org_name cannot be extracted
""",
        planner_system_prompt="""
        You are an AI Analytics Dashboard Planner.
        Based on the user's message and the dataset schema, plan the visual blocks needed.$history_context

        Available block types: MARKDOWN, KPI_CARD, BAR_CHART, LINE_CHART, PIE_CHART, SCATTER_CHART, AREA_CHART, RADAR_CHART, TABLE.

        CRITICAL RULES FOR PLANNING:
        1. Be highly creative! If the user asks for a summary or overview, generate MULTIPLE distinct charts (e.g., time trends, categorical distributions, success/failure ratios). Don't just return flat numbers. Use themes and colors that are vibrant and creative like Power BI.
        2. Ensure charts make sense: LINE_CHART or AREA_CHART for time series, BAR_CHART for categories or averages, RADAR_CHART for multi-variable comparisons. ONLY use PIE_CHART for counts or sums that represent parts of a whole (e.g., total requests by status). NEVER use PIE_CHART for averages or rates (like error percentages), as they do not add up to 100%.
        3. For TABLE blocks, prefer aggregated data. If raw data is needed, explicitly state in the intent to LIMIT to 10 rows.
        4. Every chart intent MUST imply a GROUP BY aggregation so the data has multiple rows (e.g. Group by Date, Group by Category). NEVER plot a single row of averages/totals in a chart; use KPI_CARD for single totals instead.
        5. The intent string MUST explicitly tell the SQL generator to alias the grouping column as "name" (e.g., "Group by api_name AS name, show average tokens").
        6. If generating charts or analyzing data, ALWAYS include at least one MARKDOWN block in the "blocks" array that provides a summary, analysis, or explanation of the data and charts being displayed.
        7. CASUAL CONVERSATION: If the user's message is just a greeting (e.g., "hi", "hello"), a thank you, or general chat that does not require querying the dataset:
           - Return a single MARKDOWN block.
           - Set the "intent" field EXACTLY to "NONE".
           - Include a "text" field in the block with your conversational response (e.g., "Hello! How can I help you analyze this dataset?").
        8. NO DATASET: If the Schema below says "NO DATASET AVAILABLE", you cannot generate any charts or SQL. You MUST return a single MARKDOWN block with intent "NONE" and a "text" field informing the user that no data could be fetched for their query.
        9. PRE-FILTERED DATA: The dataset is ALREADY pre-filtered for the user's requested organization. DO NOT include the organization name in your 'intent' string (e.g., never write "filter by key = Botmantra"). The 'key' column represents API Keys, not organization names.

        You must return a JSON object with a single key "blocks".
        "blocks" should be a list of objects. Each object must have:
        - "type": One of the available block types.
        - "title": A title for the block.
        - "intent": What data this block should show (this will be passed to the SQL generator). Use "NONE" for casual chat.
        - "text": ONLY use this field for casual conversation responses when intent is "NONE".
        - "colors": A list of 5-6 hex color string codes that fit a coherent, beautiful, creative theme. Required for all CHART types.

        Schema:
        $schema_context
        """,
        sql_generator_system_prompt="""
        You are an expert Data Analyst and SQL Generator.
        Your job is to generate a highly optimized and correct SQL (specifically DuckDB dialect) query based on the user's intent.$history_context
        You must generate a valid DuckDB SQL query to answer the user's question based on the provided dataset schemas.
        You have the following tables available to query: $table_names
        You MUST use the table name(s) listed above in your FROM clause — they are the actual names registered in DuckDB.
        Return ONLY the raw SQL query. Do not wrap it in markdown block quotes like ```sql or ```.
        Do not include any explanations.

        CRITICAL DUCKDB RULES & CONTEXT:
        - Current Datetime: $now_str. Use this if you need to calculate relative dates (e.g. 'last month').
        - PRE-FILTERED DATA: The data in the DuckDB tables has *already* been filtered by the backend API to match the user's requested date range AND organization. DO NOT add WHERE clauses for dates or time periods unless the user explicitly asks to compare sub-periods.
        - DO NOT add WHERE clauses for the organization name (e.g., do not use `WHERE key = 'Botmantra'`). The data ONLY contains the requested organization.
        - Do NOT use SQLite-specific functions like JULIANDAY.
        - For dates and timestamps, check the sample values in the Statistics! If the sample values are standard ISO format, you MUST use CAST(col AS TIMESTAMP).
        - If you need to treat a VARCHAR/string column as a date, use TRY_CAST(col AS TIMESTAMP) instead of CAST to avoid crashes on invalid formats. Do not cast columns that are already of type TIMESTAMP or DATETIME.
        - Use standard DuckDB functions for aggregation and string manipulation.
        - If generating a query for a CHART, you MUST include a GROUP BY or TIME BUCKET. You MUST alias the categorical or time-based grouping column exactly as `name` in the SELECT clause (e.g. SELECT api_name AS name, ...). NEVER use the 'AS' keyword inside the GROUP BY clause (e.g. use GROUP BY name or GROUP BY api_name, NOT GROUP BY api_name AS name).
        - If generating a query for a TABLE without explicit aggregations, you MUST append LIMIT 10 to the query to avoid crashing the UI, unless the user specifically requested full CSV/JSON.
        - CRITICAL: You MUST ALWAYS include a FROM clause with the correct table name (e.g., FROM summary or FROM grouped_results), even if you are just selecting a single aggregated scalar value (like a SUM or AVG) for a KPI Card!

        Schemas:
        $schema_context

        Statistics:
        $statistics
        """,
        insight_generator_system_prompt="""
        You are an expert AI Data Analyst.
        You are given an 'intent' which is what the user wants to understand, and a data sample 'data' (JSON).
        Write a markdown summary highlighting the key insights from this data.
        
        CRITICAL LENGTH & DETAIL RULE:
        - If the intent is a simple, targeted question (e.g., just asking for a single total or a basic count), keep your response short and concise (4-6 sentences).
        - If the intent asks for a detailed overview, trend analysis, data variation, or complex summary, YOU MUST provide a deep-dive analysis. Write a comprehensive explanation (up to 300-400 words). Include bullet points, actionable pointers, and note any interesting patterns or anomalies in the data. Do not just spit out the numbers—explain what the numbers mean.
        
        If the data is empty, mention that no results were found for the query.
        Format nicely with markdown. Do not include raw json or code blocks.
        """
    ),

    # ── Predefined DuckDB Schema Hints ────────────────────────────────────────
    # These descriptions are injected into SQL generator + planner prompts BEFORE
    # the auto-detected schema, giving the LLM semantic understanding of each field.
    duckdb_schema_hints={
        "grouped_results": TableHint(
            description=(
                "One row per unique combination of group_by dimensions. "
                "This is the main analytical table used for charts and trends."
            ),
            columns={
                "date": ColumnHint(
                    type="TIMESTAMP",
                    description="Date of the transaction group. Cast with CAST(date AS TIMESTAMP). Present only when group_by includes 'date'.",
                ),
                "hour": ColumnHint(
                    type="TIMESTAMP",
                    description="Hour of the transaction group. Cast with CAST(hour AS TIMESTAMP). Present only when group_by includes 'hour'.",
                ),
                "week": ColumnHint(
                    type="TIMESTAMP",
                    description="Week of the transaction group. Cast with CAST(week AS TIMESTAMP). Present only when group_by includes 'week'.",
                ),
                "month": ColumnHint(
                    type="TIMESTAMP",
                    description="Month of the transaction group. Cast with CAST(month AS TIMESTAMP). Present only when group_by includes 'month'.",
                ),
                "api_name": ColumnHint(
                    type="VARCHAR",
                    description="Name of the API called (e.g. 'extract_document_data'). Present only when group_by includes 'api_name'.",
                ),
                "key": ColumnHint(
                    type="VARCHAR",
                    description="API key used to make the transaction. Present only when group_by includes 'key'.",
                ),
                "status": ColumnHint(
                    type="VARCHAR",
                    description="Transaction status: SUCCESS, ERROR, or INITIATED. Present only when group_by includes 'status'.",
                ),
                "request_model": ColumnHint(
                    type="VARCHAR",
                    description="request model of that api. Present only when group_by includes 'request_model'.",
                ),
                "matching_transaction_count": ColumnHint(
                    type="INTEGER",
                    description="Total number of transactions in this group.",
                ),
                "passed": ColumnHint(
                    type="INTEGER",
                    description="Number of successful transactions (status = SUCCESS) in this group.",
                ),
                "failed": ColumnHint(
                    type="INTEGER",
                    description="Number of failed transactions (status = ERROR) in this group.",
                ),
                "initiated": ColumnHint(
                    type="INTEGER",
                    description="Number of transactions still in INITIATED state in this group.",
                ),
                "failure_rate_pct": ColumnHint(
                    type="FLOAT",
                    description="Failure rate as a percentage: (failed / matching_transaction_count) * 100.",
                ),
                "total_input_tokens": ColumnHint(
                    type="INTEGER",
                    description="Sum of LLM prompt (input) tokens consumed by all transactions in this group.",
                ),
                "total_output_tokens": ColumnHint(
                    type="INTEGER",
                    description="Sum of LLM completion (output) tokens produced in this group.",
                ),
                "total_pages": ColumnHint(
                    type="INTEGER",
                    description="Sum of document pages processed in this group (where applicable).",
                ),
                "avg_latency_ms": ColumnHint(
                    type="FLOAT",
                    description="Average end-to-end latency of API calls in milliseconds for this group.",
                ),
            },
        ),
        "summary": TableHint(
            description=(
                "Single aggregate row containing overall totals and rates "
                "across the entire filtered period. Use KPI_CARD blocks for values from this table."
            ),
            columns={
                "matching_transaction_count": ColumnHint(
                    type="INTEGER",
                    description="Total transactions in the filtered period.",
                ),
                "passed": ColumnHint(
                    type="INTEGER",
                    description="Total successful transactions (status = SUCCESS).",
                ),
                "failed": ColumnHint(
                    type="INTEGER",
                    description="Total failed transactions (status = ERROR).",
                ),
                "initiated": ColumnHint(
                    type="INTEGER",
                    description="Total transactions still in INITIATED state.",
                ),
                "failure_rate_pct": ColumnHint(
                    type="FLOAT",
                    description="Overall failure rate percentage: (failed / total) * 100.",
                ),
                "total_input_tokens": ColumnHint(
                    type="INTEGER",
                    description="Total LLM input tokens consumed across the period.",
                ),
                "total_output_tokens": ColumnHint(
                    type="INTEGER",
                    description="Total LLM output tokens produced across the period.",
                ),
                "total_pages": ColumnHint(
                    type="INTEGER",
                    description="Total document pages processed across the period.",
                ),
                "avg_latency_ms": ColumnHint(
                    type="FLOAT",
                    description="Overall average latency in milliseconds across all transactions.",
                ),
            },
        ),
    },

    dataset_source_label="transaction_log_api",
)
