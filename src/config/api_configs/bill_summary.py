"""
src/config/api_configs/bill_summary.py
========================================
ApiConfig for the get_bill_summary_details API
(D:\\UBMS\\APIs\\get_bill_summary_details -> admin_functions.bill.f_get_bill_summary_details).

Returns a consolidated, Administrator-only, read-only summary of the bill domain
(bills, statuses, categories, units, monthly trend, top bills) for LLM grounding.
Unlike the transaction_log API, EVERY filter is optional here -- an empty body is
a valid request that returns an org-wide, all-time summary.
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

BILL_SUMMARY_CONFIG = ApiConfig(
    name="bill_summary",
    display_name="Bill Summary",

    # ── Auth ──────────────────────────────────────────────────────────────────
    # Same pattern as transaction_log: the chatbot's own `authorize_token` header
    # is passed straight through as the bearer token for this Administrator-only API.
    auth=AuthConfig(
        type="passed_token",
        inject_as_header="authorize_token",
    ),

    # ── Fetch ─────────────────────────────────────────────────────────────────
    fetch_endpoint_env="BILL_SUMMARY_ENDPOINT",
    fetch_method="POST",
    fetch_headers={
        "Content-Type": "application/json",
        "request_type": "application/json",
    },
    # get_bill_summary_details/__init__.py reads exactly:
    #   filters   = request_body.get("filters") or {}
    #   group_by  = request_body.get("group_by") or []
    # No top-level duplicate keys required (unlike transaction_log's org_name).
    payload_template={
        "filters": "$filters",
        "group_by": "$group_by",
    },

    # ── Response → DuckDB Tables ───────────────────────────────────────────────
    # build_api_response() envelope is {status_code, status_description, error_message, data}
    # -- data root is "data", NOT "response_body" like transaction_log.
    response_data_root="data",
    response_success_field="status_code",
    response_success_value=200,
    response_error_path="error_message",
    tables=[
        TableMappingConfig(
            name="kpis",
            path="kpis",
            type="single_row",
        ),
        TableMappingConfig(
            name="by_status",
            path="by_status",
            type="list_of_dicts",
            fallback_columns=["bill_status", "bill_count", "total_amount"],
        ),
        TableMappingConfig(
            name="by_category",
            path="by_category",
            type="list_of_dicts",
            fallback_columns=["utility_category_name", "bill_count", "total_amount"],
        ),
        TableMappingConfig(
            name="by_unit",
            path="by_unit",
            type="list_of_dicts",
            fallback_columns=["unit_id", "unit_code", "unit_name", "allocation_count", "allocated_amount"],
        ),
        TableMappingConfig(
            name="monthly_trend",
            path="monthly_trend",
            type="list_of_dicts",
            fallback_columns=["billing_period", "bill_count", "total_amount"],
        ),
        TableMappingConfig(
            name="top_bills",
            path="top_bills",
            type="list_of_dicts",
            fallback_columns=[
                "bill_id", "account_id", "account_number", "account_name",
                "utility_category_name", "billing_period", "bill_date",
                "total_amount", "currency", "bill_status",
            ],
        ),
        # Only present in the response when group_by was supplied on the request.
        # Column set varies with the caller-chosen dimensions, so the fallback
        # covers every possible group_by column + both metric bases (bill vs. unit).
        TableMappingConfig(
            name="grouped_results",
            path="grouped_results",
            type="list_of_dicts",
            fallback_columns=[
                "bill_status", "utility_category_name", "account_id", "billing_period",
                "month", "quarter", "year", "bill_date", "unit_id", "unit_code", "unit_name",
                "bill_count", "total_amount", "allocation_count", "allocated_amount",
            ],
        ),
    ],

    # ── DataDecisionAgent Filter Schema ────────────────────────────────────────
    # Mirrors f_get_bill_summary_details's actual parameters exactly. NOTE: none
    # are required=True here -- every filter is optional per the API's own contract
    # ("all optional; AND-combined... an unfiltered, org-wide summary is valid").
    filter_schema=FilterSchemaConfig(
        fields={
            "account_id": FilterFieldConfig(
                type="string",
                required=False,
                description=(
                    "The account (org/property) to scope the summary to -- accepts EITHER the "
                    "account's real UUID (only use this if you already know it, e.g. from earlier "
                    "tool output) OR its human-readable account_number as mentioned by the user "
                    "(e.g. 'ACCT-502'). The API resolves account_number to the real UUID itself -- "
                    "just pass through whatever the user said (account_number or UUID), do not "
                    "invent or guess a UUID. Omit entirely for an org-wide summary across all "
                    "accounts. A stale/unknown UUID is NOT an error -- it just narrows results to "
                    "zero -- but an unrecognized account_number IS rejected by the API."
                ),
            ),
            "bill_status": FilterFieldConfig(
                type="string",
                required=False,
                description="Filter to bills in this status only. Omit for all statuses.",
                allowed_values=["Pending Approval", "Approved", "On Hold", "Resolved"],
            ),
            "utility_category_name": FilterFieldConfig(
                type="string",
                required=False,
                description="Filter to bills for a specific utility category (e.g. 'Water', 'Electricity', 'Gas'). Omit for all categories.",
            ),
            "unit_id": FilterFieldConfig(
                type="string",
                required=False,
                description=(
                    "Restrict the summary to bills allocated to a single unit -- accepts EITHER "
                    "the unit's real UUID (only if already known, e.g. from earlier tool output) "
                    "OR its human-readable unit_code as mentioned by the user (e.g. 'U-101' or "
                    "'Unit 101'). The API resolves unit_code to the real UUID itself -- just pass "
                    "through whatever the user said, do not invent or guess a UUID. Omit entirely "
                    "for a summary across all units. A stale/unknown UUID is NOT an error -- it "
                    "just narrows results to zero -- but an unrecognized unit_code IS rejected by "
                    "the API."
                ),
            ),
            "day_flag": FilterFieldConfig(
                type="string",
                required=False,
                description=(
                    "Granularity of the period filter below. MUST be paired with day_value "
                    "in the matching format. Omit both day_flag and day_value entirely for "
                    "an all-time summary -- do not invent a default period."
                ),
                allowed_values=["Daily", "Monthly", "Quarterly", "Yearly"],
            ),
            "day_value": FilterFieldConfig(
                type="string",
                required=False,
                description=(
                    "Period value in the format matching day_flag: "
                    "Daily='DD-MM-YYYY', Monthly='MM-YYYY', Quarterly='Q1-YYYY'..'Q4-YYYY', "
                    "Yearly='YYYY'. Required whenever day_flag is set; invalid format causes "
                    "the API to reject the request."
                ),
            ),
            "from_date": FilterFieldConfig(
                type="string",
                required=False,
                description=(
                    "Start of an arbitrary date range (format 'DD-MM-YYYY'), filtered against "
                    "billing_period (the bill's month), NOT bill_date -- this keeps the range "
                    "consistent with the monthly_trend breakdown, which is grouped by billing_period. "
                    "Use this ONLY when the user asks for a custom range spanning multiple "
                    "months/quarters that day_flag/day_value cannot express (e.g. 'between 15 Jan "
                    "2025 and 10 March 2025'). MUST NOT be combined with day_flag/day_value -- the "
                    "API rejects the request if both are set. May be supplied alone (open-ended "
                    "range) with just to_date, or vice versa."
                ),
            ),
            "to_date": FilterFieldConfig(
                type="string",
                required=False,
                description=(
                    "End of an arbitrary date range (format 'DD-MM-YYYY'), filtered against "
                    "billing_period (the bill's month), NOT bill_date. Pairs with from_date -- see "
                    "its description. MUST NOT be combined with day_flag/day_value."
                ),
            ),
        },
        group_by=GroupByConfig(
            allowed=[
                "bill_status", "utility_category", "account_id", "billing_period",
                "month", "quarter", "year", "bill_date", "unit_id",
            ],
            # Empty by default: the fixed breakdowns (by_status/by_category/by_unit/
            # monthly_trend/top_bills) are ALWAYS returned regardless of group_by, so
            # extra grouping is only needed for a caller-chosen cross-tab.
            default=[], 
        ),
    ),

    # ── Per-API Prompt Fragments ───────────────────────────────────────────────
    agent_prompts=AgentPromptConfig(
        context_hint=(
            "This API returns a consolidated, read-only summary of the bill domain "
            "(bills, statuses, utility categories, units, monthly trend, top bills) for "
            "an Administrator. ALL filters are optional -- an empty request is valid and "
            "returns an org-wide, all-time summary. Do NOT set needs_fetch=false just "
            "because the user didn't mention an account or a date."
        ),
        group_by_rules="""
- If the user's question is already answered by the standard breakdowns (overall totals,
  by status, by utility category, by unit, monthly trend, top bills), leave group_by empty --
  those are always returned regardless of group_by.
- Only set group_by when the user asks for a cross-tab not covered by the above, e.g.:
  - "by status" / "per status" -> ["bill_status"]
  - "by category" / "by utility category" -> ["utility_category"]
  - "by account" -> ["account_id"]
  - "by month" / "monthly" -> ["month"]
  - "by quarter" / "quarterly" -> ["quarter"]
  - "by year" / "yearly" -> ["year"]
  - "by unit" / "per unit" -> ["unit_id"] (switches metrics to allocation_count/allocated_amount)
  - "by billing period" -> ["billing_period"]
  - "by bill date" -> ["bill_date"]
  - Combinations are valid, e.g. "monthly by status" -> ["month", "bill_status"]
""",
        date_range_rules="""
- Only set a date filter when the user actually mentions a period. If no period is
  mentioned, OMIT all of day_flag/day_value/from_date/to_date for an all-time summary --
  do not default to "last 30 days".
- A specific SINGLE day (e.g. "on 5 March 2025") -> day_flag="Daily", day_value="YYYY-MM-DD"
- A specific SINGLE month: "this month" / "last month" / a named month (e.g. "March 2025") -> day_flag="Monthly",
  day_value="MM-YYYY" (resolve "this/last month" relative to the current datetime). NEVER use day_flag="Monthly" for multi-month ranges like "last 6 months"!
- "Q1 2025" / "first quarter of 2025" -> day_flag="Quarterly", day_value="Q<1-4>-YYYY"
- "this year" / "last year" / "in 2025" -> day_flag="Yearly", day_value="YYYY"
- A MULTI-MONTH OR CUSTOM RANGE (e.g. "last 6 months", "last 3 months", "past 6 months", "between 15 Jan 2025 and 10 March 2025", "from March to July 2025", "last 45 days") -> MUST use from_date/to_date instead (format 'YYYY-MM-DD' each, e.g. for last 6 months computed from the CURRENT DATETIME above, not a fixed example date: from_date = first day of the month 5 months before the current month, to_date = last day of the current month), and do NOT set day_flag/day_value. Round from_date down to the 1st of its month and to_date up to the last day of its month -- from_date/to_date filter on billing_period (the bill's month), so full-month bounds keep the range aligned with whole calendar months instead of splitting one. Either bound may be omitted for an open-ended range (e.g. only from_date -> "since 1 Jan 2025").
- NEVER set both day_flag/day_value AND from_date/to_date together -- the API rejects the
  request if both are present. Pick exactly one way to express the period.
- CALENDAR VALIDITY & LEAP YEARS: Always check calendar validity when outputting dates!
  February has only 28 days in non-leap years (e.g. 2025, 2026, 2027). Only leap years (divisible by 4, e.g. 2024, 2028) have 29 days in February. Never output 'YYYY-02-29' (except in leap years) or 'YYYY-04-31' or 'YYYY-06-31'.
- DATE FORMAT FOR from_date/to_date: Always use ISO format 'YYYY-MM-DD' (e.g. '2025-11-01').
  This is the only accepted format for from_date and to_date.
- Always double-check the format matches exactly -- an invalid day_value/from_date/to_date
  format causes the API to reject the whole request.
""",
        decision_rules_extra="""
- NEEDS FETCH = TRUE whenever the user asks about bills, spend, dues/pending amounts,
  categories, units, or billing trends -- EVEN IF no account, status, or period is
  mentioned. An unfiltered, org-wide, all-time summary is a valid and common request here
  (unlike transaction_log, no field is required to fetch).
- NEEDS FETCH = TRUE when the user asks for a different account, status, or period than
  what's currently loaded, or says "refresh"/"reload"/"latest".
- NEEDS FETCH = FALSE only for casual conversation or topics unrelated to bills.
""",
        planner_system_prompt="""
        You are an AI Analytics Dashboard Planner.
        Based on the user's message and the dataset schema, plan the visual blocks needed.$history_context

        Available block types: MARKDOWN, KPI_CARD, BAR_CHART, LINE_CHART, PIE_CHART, SCATTER_CHART, AREA_CHART, RADAR_CHART, TABLE.

        CRITICAL RULES FOR PLANNING:
        1. CHART COUNT — MANDATORY: You MUST include EXACTLY 3 chart blocks (BAR_CHART, LINE_CHART, PIE_CHART, AREA_CHART, SCATTER_CHART, RADAR_CHART) in every response that involves data. Not 1, not 2, not 4 — always exactly 3. TABLE and KPI_CARD blocks do NOT count toward this limit. Choose the 3 most informative and varied chart types for the user's question.
        2. MARKDOWN BLOCK — MANDATORY: You MUST include EXACTLY 1 MARKDOWN block in every response that involves data. No more, no less. This MARKDOWN block provides the narrative summary/analysis. Together with the 3 charts, every data response MUST have exactly 4 blocks minimum: 1 MARKDOWN + 3 CHARTS (plus any TABLE or KPI_CARD blocks as needed). Be highly creative with chart selection — use vibrant, Power BI-style colors and diverse chart types (e.g., a mix of LINE_CHART, BAR_CHART, PIE_CHART).
        3. Ensure charts make sense: LINE_CHART or AREA_CHART for monthly_trend, BAR_CHART for by_category/by_unit/by_status, RADAR_CHART for multi-variable comparisons. ONLY use PIE_CHART for counts or sums that represent parts of a whole (e.g., bill_count or total_amount by status/category). NEVER use PIE_CHART for averages or rates.
        4. For TABLE blocks, prefer aggregated data (by_status, by_category, by_unit, monthly_trend, top_bills). If raw/top_bills data is needed, it is already capped by the API -- do not add an additional LIMIT unless the user asks for fewer rows.
        5. Every chart must show multiple rows, one per dimension value (e.g. one bar per bill_status, one point per month). NEVER plot a single row of totals in a chart; use KPI_CARD for single totals (e.g. total_amount, pending_amount) instead. NOTE: by_status/by_category/by_unit/monthly_trend are ALREADY one row per dimension — the intent should say "select" / "show", NOT "group by", since no further aggregation is needed on these tables (only top_bills would ever need aggregating).
        6. The intent string MUST explicitly tell the SQL generator to alias the dimension column as "name" (e.g., "Show bill_status AS name and total_amount from the status breakdown").
        7. If generating charts or analyzing data, ALWAYS include EXACTLY ONE MARKDOWN block in the "blocks" array that provides a summary, analysis, or explanation of the data and charts being displayed.
           - For any overview, summary, or dashboard-level request, place this MARKDOWN block FIRST in the blocks array.
           - The "intent" for this overview MARKDOWN block MUST instruct the SQL generator to produce a UNION ALL analytics snapshot. Write the intent EXACTLY in this format (fill in the bracketed parts from context):
             "ANALYTICS SNAPSHOT: Write a UNION ALL query combining data from all five analytics tables into a uniform 3-column shape (section VARCHAR, metric VARCHAR, value VARCHAR). IMPORTANT: Do NOT add ORDER BY inside any individual branch — ORDER BY inside a UNION ALL is invalid SQL and will crash. Include the five branches with no ORDER BY inside them: (1) SELECT 'kpi' AS section, 'total_bills' AS metric, CAST(total_bills AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'total_amount' AS metric, CAST(total_amount AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'average_bill_amount' AS metric, CAST(average_bill_amount AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'pending_amount' AS metric, CAST(pending_amount AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'approved_amount' AS metric, CAST(approved_amount AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'on_hold_amount' AS metric, CAST(on_hold_amount AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'resolved_amount' AS metric, CAST(resolved_amount AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'pending_bills' AS metric, CAST(pending_bills AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'approved_bills' AS metric, CAST(approved_bills AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'on_hold_bills' AS metric, CAST(on_hold_bills AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'resolved_bills' AS metric, CAST(resolved_bills AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'total_allocated_amount' AS metric, CAST(total_allocated_amount AS VARCHAR) AS value FROM kpis UNION ALL SELECT 'kpi' AS section, 'unallocated_amount' AS metric, CAST(unallocated_amount AS VARCHAR) AS value FROM kpis UNION ALL (2) SELECT 'monthly_trend' AS section, CAST(billing_period AS VARCHAR) AS metric, CAST(total_amount AS VARCHAR) AS value FROM monthly_trend (3) SELECT 'by_status' AS section, bill_status AS metric, CAST(total_amount AS VARCHAR) AS value FROM by_status (4) SELECT 'by_category' AS section, utility_category_name AS metric, CAST(total_amount AS VARCHAR) AS value FROM by_category (5) SELECT 'top_bills' AS section, (account_number || ' | ' || CAST(billing_period AS VARCHAR) || ' | ' || bill_status) AS metric, CAST(total_amount AS VARCHAR) AS value FROM top_bills LIMIT 5. The result will be fed to the insight generator to write a comprehensive executive dashboard narrative covering: [list all charts being shown in the dashboard]."
        8. SPECIFIC ANALYTICAL QUESTIONS: If the user asks a specific analytical question (e.g., "what are the anomalies?", "why did spend spike in June?", "what is driving the high pending amount?"):
           - Do NOT use the generic "ANALYTICS SNAPSHOT" intent. Do NOT generate a full dashboard.
           - Each block's intent is executed as its OWN independent SQL query against ONE table — a MARKDOWN block cannot see the data behind a sibling chart block, and asking one intent to pull from two tables at once will crash (multi-table SQL is ONLY valid via the exact "ANALYTICS SNAPSHOT:" UNION format from rule 7 — never invent an ad hoc UNION/JOIN outside that pattern).
           - Generate EXACTLY 1 MARKDOWN block whose intent explicitly repeats the user's specific question and requests data from the SINGLE table best suited to answer it (e.g., "Analyze the monthly_trend data to identify any anomalies, unusual spikes, or outliers in spending." — pick monthly_trend for trend/spike questions, top_bills for "which bills" questions, by_status/by_category for status/category-driven questions).
           - You MUST include EXACTLY 3 charts to support the specific answer (each with its own single-table intent). Pick the 3 most relevant single-table chart intents that illuminate the user's question from different angles (e.g., one chart on monthly_trend, one on by_status, one on by_category). This is non-negotiable — even for focused analytical questions, always produce exactly 3 charts.
        9. CASUAL CONVERSATION: If the user's message is just a greeting (e.g., "hi", "hello"), a thank you, or general chat that does not require querying the dataset:
           - Return a single MARKDOWN block.
           - Set the "intent" field EXACTLY to "NONE".
           - Include a "text" field in the block with your conversational response (e.g., "Hello! How can I help you with your bill summary?").
        10. NO DATASET: If the Schema below says "NO DATASET AVAILABLE", you cannot generate any charts or SQL. You MUST return a single MARKDOWN block with intent "NONE" and a "text" field informing the user that no data could be fetched for their query.
        11. PRE-FILTERED DATA: The dataset is already scoped to whichever account_id/bill_status/utility_category/day_flag filters were applied at fetch time — this includes any period/quarter/month/year AND any specific utility category (e.g. Water) or account the user asked for. CRITICAL consequences for intent writing:
           - NEVER tell the SQL generator to add a WHERE clause filtering by billing_period, bill_date, month, quarter, year, or utility_category_name on the pre-aggregated tables (kpis, by_status, by_category, by_unit, monthly_trend) — those tables DO NOT HAVE a utility_category_name column and the data IS already scoped.
           - For a 'Monthly spend trend for Water' intent: just say 'Show billing_period AS name and total_amount from monthly_trend' — do NOT say 'filter to Water' or 'WHERE utility_category_name = Water'.
           - For a 'Status breakdown for Water bills' intent: just say 'Show bill_status AS name and total_amount from by_status' — the data is already Water-only, no filter needed.
           - Only top_bills has a utility_category_name column — a WHERE filter there is allowed if the user wants a specific category within top_bills.
        12. TABLE CHOICE: kpis/by_status/by_category/by_unit/monthly_trend/top_bills already cover every single-dimension breakdown. Only mention "grouped_results" in an intent when the user needs a cross-tab of TWO OR MORE dimensions not covered by those (e.g. "spend by category per month") — and even then, only if grouped_results was actually populated (it's empty unless a caller-chosen group_by was used for this fetch).

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
        Return exactly ONE SQL statement — never separate multiple statements with a semicolon, and never place anything after a trailing semicolon.

        SINGLE-TABLE RULE — applies to every intent EXCEPT one starting with "ANALYTICS SNAPSHOT:": query exactly ONE table. If the intent mentions more than one table (e.g. "analyze monthly_trend and top_bills"), that is NOT permission to UNION/JOIN them yourself — the ANALYTICS SNAPSHOT UNION format (below) is the ONLY multi-table pattern this system supports, and it requires that exact "ANALYTICS SNAPSHOT:" prefix. For any other intent, pick the single table most central to the question and query only that one, ignoring the rest of the intent's table mentions.

        CRITICAL DUCKDB RULES & CONTEXT:
        - Current Datetime: $now_str. Use this if you need to calculate relative dates (e.g. 'last month').
        - PRE-FILTERED DATA — READ CAREFULLY: The data in EVERY table (kpis, by_status, by_category, by_unit, monthly_trend, top_bills) has *already* been filtered by the backend API per whichever account_id/bill_status/day_flag/day_value filters were requested — this includes any quarter/month/year/date range the user asked for. NEVER add a WHERE clause that filters billing_period, bill_date, month, quarter, or year on these tables — there is nothing further to filter, the rows you see ARE the requested period. Only add a WHERE clause on these tables for a dimension the user is narrowing WITHIN the loaded data that ISN'T already a fetch-time filter (e.g. picking one status among the 4 rows in by_status).
        - EMPTY TABLE GUARD: Before using ANY table, check its column stats in the Statistics section below. If every column shows "distinct_count": 0, the table is EMPTY — do not SELECT FROM it, do not JOIN it, and do not use it in a subquery or IN(...) clause. This applies especially to `grouped_results`, which is empty unless the fetch explicitly requested a caller-chosen group_by (rare — the fixed breakdown tables cover almost every question). If you need a single-dimension breakdown (by status, by category, by unit, by month), use the matching dedicated table (by_status/by_category/by_unit/monthly_trend) — never grouped_results — and never invent a join between two tables to "filter" one by the other's rows.
        - GROUPED_RESULTS COLUMN SWITCH — CRITICAL, CHECK BEFORE EVERY grouped_results QUERY: this table does NOT always have the same metric columns. Check the Statistics section for whether `unit_id` has any non-null/non-zero-distinct_count values in this specific result set:
            - If `unit_id` was part of the group_by (i.e. the grouped_results rows carry a populated unit_id/unit_code/unit_name), the ONLY metric columns present are `allocation_count` and `allocated_amount` — `bill_count` and `total_amount` DO NOT EXIST in this case and selecting them will crash the query with a missing-column error.
            - If `unit_id` was NOT part of the group_by, the metric columns are `bill_count` and `total_amount` — `allocation_count`/`allocated_amount` DO NOT EXIST in this case.
            - Never SELECT both metric pairs from grouped_results in the same query, and never guess — verify which pair is actually populated via the Statistics section first.
        - Money columns (total_amount, allocated_amount, average_bill_amount, etc.) are already-computed floats — do not re-derive them unless the user asks for a different aggregation.
        - Do not use SQLite-specific functions like JULIANDAY.
        - For dates and timestamps (billing_period, bill_date), check the sample values in the Statistics! If the sample values are standard ISO format, you MUST use CAST(col AS TIMESTAMP).
        - If you need to treat a VARCHAR/string column as a date, use TRY_CAST(col AS TIMESTAMP) instead of CAST to avoid crashes on invalid formats. Do not cast columns that are already of type TIMESTAMP or DATETIME.
        - Use standard DuckDB functions for aggregation and string manipulation.
        - GROUP BY — WHEN TO USE IT: by_status, by_category, by_unit, monthly_trend, and kpis are already pre-aggregated at exactly ONE ROW PER DIMENSION VALUE (or one row total for kpis). Selecting directly from these needs NO GROUP BY at all — just `SELECT bill_status AS name, total_amount FROM by_status` with no GROUP BY clause, since there is nothing left to aggregate and every non-aggregated column is already unique per row. Only add a GROUP BY when you are aggregating multiple raw rows down to fewer rows yourself (e.g. computing a new breakdown from top_bills that the fixed tables don't already provide). Adding a superfluous GROUP BY on an already-aggregated table will error ("column must appear in the GROUP BY clause or be part of an aggregate function") because the other selected columns aren't wrapped in an aggregate.
        - You MUST alias the categorical or time-based dimension column exactly as `name` in the SELECT clause (e.g. SELECT bill_status AS name, total_amount FROM by_status) whether or not a GROUP BY is used. NEVER use the 'AS' keyword inside a GROUP BY clause (e.g. use GROUP BY name or GROUP BY bill_status, NOT GROUP BY bill_status AS name).
        - The by_status, by_category, by_unit, and monthly_trend tables are already pre-aggregated one-row-per-dimension — ALWAYS prefer querying them directly (plain SELECT, no extra WHERE/JOIN/GROUP BY) over re-aggregating top_bills or touching grouped_results.
        - CRITICAL: You MUST ALWAYS include a FROM clause with the correct table name (e.g., FROM kpis, FROM by_status, FROM monthly_trend), even if you are just selecting a single aggregated scalar value (like total_amount) for a KPI Card!

        ANALYTICS SNAPSHOT UNION — THE ONLY ALLOWED MULTI-TABLE PATTERN:
        When the intent starts with "ANALYTICS SNAPSHOT:", you MUST produce a single UNION ALL query with EXACTLY 3 columns in every SELECT branch: section VARCHAR, metric VARCHAR, value VARCHAR.
        Follow the intent's branch list precisely.

        STRICT RULES — violating any of these causes a SQL syntax error:
          - ABSOLUTELY NO ORDER BY inside any individual branch. ORDER BY inside a UNION ALL branch is INVALID SQL in DuckDB and will crash with a parser error. The only valid place for ORDER BY is at the very end of the entire UNION ALL query, and even then it is not needed here.
          - ABSOLUTELY NO LIMIT inside any individual branch EXCEPT the top_bills branch (LIMIT 5 is allowed there). LIMIT inside a non-final UNION branch is invalid SQL — only the top_bills branch is exempt because it is ALWAYS the last branch.
          - Each branch must cast ALL non-VARCHAR values to VARCHAR: CAST(total_amount AS VARCHAR), CAST(billing_period AS VARCHAR), CAST(total_bills AS VARCHAR), etc.
          - Only include a branch for a table if that table is NOT empty (Statistics section: if every column has distinct_count: 0, skip that branch and its UNION ALL keyword).
          - NEVER add a WHERE clause to any branch — the data is already pre-filtered by the API.
          - This is the ONLY case where querying more than one table in a single query is allowed.

        Example of a valid ANALYTICS SNAPSHOT query structure:
          SELECT 'kpi' AS section, 'total_amount' AS metric, CAST(total_amount AS VARCHAR) AS value FROM kpis
          UNION ALL SELECT 'kpi' AS section, 'total_bills' AS metric, CAST(total_bills AS VARCHAR) AS value FROM kpis
          UNION ALL SELECT 'monthly_trend' AS section, CAST(billing_period AS VARCHAR) AS metric, CAST(total_amount AS VARCHAR) AS value FROM monthly_trend
          UNION ALL SELECT 'by_status' AS section, bill_status AS metric, CAST(total_amount AS VARCHAR) AS value FROM by_status
          UNION ALL SELECT 'by_category' AS section, utility_category_name AS metric, CAST(total_amount AS VARCHAR) AS value FROM by_category
          UNION ALL SELECT 'top_bills' AS section, (account_number || ' | ' || CAST(billing_period AS VARCHAR) || ' | ' || bill_status) AS metric, CAST(total_amount AS VARCHAR) AS value FROM top_bills LIMIT 5

        Schemas:
        $schema_context

        Statistics:
        $statistics
        """,
        insight_generator_system_prompt="""
        You are an expert AI Data Analyst and Business Intelligence writer.
        You are given an 'intent' which is what the user wants to understand, and a data sample 'data' (JSON).
        Write a rich, well-structured markdown summary highlighting the key insights from this data.

        CRITICAL LENGTH & DETAIL RULES — read carefully and apply the correct rule:

        RULE 1 — DASHBOARD OVERVIEW / INTRODUCTION BLOCK:
        If the intent starts with "ANALYTICS SNAPSHOT:", or mentions a dashboard, overview, executive summary, or references multiple accompanying charts/tables (e.g., "line chart", "bar chart", "pie chart", "top bills table"), this is a full dashboard narrative. You MUST write a comprehensive, structured introduction of at minimum 5-7 paragraphs. Follow this structure:
          - **Opening paragraph**: State clearly what this dashboard covers — the account(s), time period, applied filters, and what the user is looking at.
          - **Financial Highlights**: Summarise the headline KPI figures (total spend, number of bills, average bill amount) with interpretation — is spend high or low, how does it compare across months?
          - **Monthly Spend Trend**: Narrate the month-by-month spending pattern. Call out spikes, dips, the peak month, and what may be driving that pattern.
          - **Utility Category Breakdown**: Describe what utility categories are present, which dominates, and any noteworthy imbalances.
          - **Bill Status Distribution**: Interpret the status split (Pending Approval, Approved, On Hold, Resolved). Flag any risk — e.g., a high proportion of On Hold or Pending bills that represent blocked cash flow or unresolved issues.
          - **Top Bills**: Call out the highest individual bills — which billing period they fall in, their status, and whether any unusually large amounts stand out.
          - **Closing Summary**: Provide a one-paragraph executive conclusion — key takeaways, any action items or anomalies the user should be aware of.
        Do NOT produce a short paragraph for this case. A dashboard narrative under 400 words is unacceptable.

        RULE 2 — TARGETED / SINGLE-METRIC QUESTION:
        If the intent is a simple, focused question (e.g., just asking for a single total, a pending amount, or one specific value), keep your response concise (4-6 sentences max).

        RULE 3 — DETAILED TREND / ANALYSIS BLOCK (non-overview):
        If the intent asks for a specific analysis (e.g., trend analysis, category breakdown, status comparison) that is NOT a full dashboard overview, write a focused deep-dive (200-400 words). Use bullet points, call out anomalies, and explain what the numbers mean — not just what they are.

        GENERAL RULES (apply to all):
        - If the data is empty, clearly state that no results were found for the query.
        - Format nicely with markdown: use headers (##, ###), bold key figures, and bullet lists where appropriate.
        - Do not include raw JSON or code blocks.
        - Never just list numbers — always interpret and explain what they mean in a business context.
        - CURRENCY: If the data sample includes a `currency` column (e.g. rows from top_bills), use the currency value(s) actually present there when narrating those amounts — do not override it with AED if the data says otherwise. For aggregated tables with no currency column (kpis, by_status, by_category, by_unit, monthly_trend), default to AED (UAE Dirham), this org's base currency. Never invent USD, $, or any other currency that isn't backed by the data.
        """
    ),

    # ── Predefined DuckDB Schema Hints ────────────────────────────────────────
    # Injected into SQL generator + planner prompts BEFORE the auto-detected schema,
    # mirroring exactly the response_body shape built in admin_functions/bill.py.
    duckdb_schema_hints={
        "kpis": TableHint(
            description=(
                "Single aggregate row with org-wide (or filter-scoped) totals across the "
                "whole matching period. Use KPI_CARD blocks for values from this table."
            ),
            columns={
                "total_bills": ColumnHint(type="INTEGER", description="Total number of bills matching the applied filters."),
                "total_amount": ColumnHint(type="FLOAT", description="Sum of bill.total_amount across all matching bills."),
                "average_bill_amount": ColumnHint(type="FLOAT", description="total_amount / total_bills (0 if no bills)."),
                "pending_bills": ColumnHint(type="INTEGER", description="Count of bills with bill_status = 'Pending Approval'."),
                "pending_amount": ColumnHint(type="FLOAT", description="Sum of total_amount for 'Pending Approval' bills."),
                "approved_bills": ColumnHint(type="INTEGER", description="Count of bills with bill_status = 'Approved'."),
                "approved_amount": ColumnHint(type="FLOAT", description="Sum of total_amount for 'Approved' bills."),
                "on_hold_bills": ColumnHint(type="INTEGER", description="Count of bills with bill_status = 'On Hold'."),
                "on_hold_amount": ColumnHint(type="FLOAT", description="Sum of total_amount for 'On Hold' bills."),
                "resolved_bills": ColumnHint(type="INTEGER", description="Count of bills with bill_status = 'Resolved'."),
                "resolved_amount": ColumnHint(type="FLOAT", description="Sum of total_amount for 'Resolved' bills."),
                "total_allocated_amount": ColumnHint(type="FLOAT", description="Sum of bill_unit_details.allocated_amount across all matching unit allocations."),
                "unallocated_amount": ColumnHint(type="FLOAT", description="total_amount - total_allocated_amount -- money not yet allocated to a specific unit."),
            },
        ),
        "by_status": TableHint(
            description="One row per bill_status (Pending Approval, Approved, On Hold, Resolved) — always all four, even if zero.",
            columns={
                "bill_status": ColumnHint(type="VARCHAR", description="One of: Pending Approval, Approved, On Hold, Resolved."),
                "bill_count": ColumnHint(type="INTEGER", description="Number of bills in this status."),
                "total_amount": ColumnHint(type="FLOAT", description="Sum of total_amount for bills in this status."),
            },
        ),
        "by_category": TableHint(
            description="One row per utility category (e.g. Electricity, Water, Gas), ordered by total_amount descending.",
            columns={
                "utility_category_name": ColumnHint(type="VARCHAR", description="Name of the utility category."),
                "bill_count": ColumnHint(type="INTEGER", description="Number of bills in this category."),
                "total_amount": ColumnHint(type="FLOAT", description="Sum of total_amount for bills in this category."),
            },
        ),
        "by_unit": TableHint(
            description=(
                "Top units by allocated spend (capped server-side, see metadata.by_unit_limit / "
                "by_unit_total_unit_count for the true totals). Money here is bill_unit_details.allocated_amount, "
                "NOT bill.total_amount."
            ),
            columns={
                "unit_id": ColumnHint(type="VARCHAR", description="UUID of the unit."),
                "unit_code": ColumnHint(type="VARCHAR", description="Short unit code/identifier."),
                "unit_name": ColumnHint(type="VARCHAR", description="Human-readable unit name."),
                "allocation_count": ColumnHint(type="INTEGER", description="Number of per-unit allocation rows for this unit."),
                "allocated_amount": ColumnHint(type="FLOAT", description="Sum of allocated_amount for this unit."),
            },
        ),
        "monthly_trend": TableHint(
            description="One row per calendar month (billing_period), ordered chronologically — the primary time-series table for trend charts.",
            columns={
                "billing_period": ColumnHint(type="TIMESTAMP", description="First day of the billing month. Cast with CAST(billing_period AS TIMESTAMP) if needed."),
                "bill_count": ColumnHint(type="INTEGER", description="Number of bills in this month."),
                "total_amount": ColumnHint(type="FLOAT", description="Sum of total_amount for bills in this month."),
            },
        ),
        "top_bills": TableHint(
            description="The largest individual bills by total_amount (capped at metadata.top_bills_limit, default 10) — use for a 'largest bills' TABLE, not for aggregation.",
            columns={
                "bill_id": ColumnHint(type="VARCHAR", description="UUID of the bill."),
                "account_id": ColumnHint(type="VARCHAR", description="UUID of the account the bill belongs to."),
                "account_number": ColumnHint(type="VARCHAR", description="Human-readable account number."),
                "account_name": ColumnHint(type="VARCHAR", description="Account/property name."),
                "utility_category_name": ColumnHint(type="VARCHAR", description="Utility category of the bill."),
                "billing_period": ColumnHint(type="TIMESTAMP", description="First day of the billing month for this bill."),
                "bill_date": ColumnHint(type="TIMESTAMP", description="Actual bill transaction date."),
                "total_amount": ColumnHint(type="FLOAT", description="Total amount of this bill."),
                "currency": ColumnHint(type="VARCHAR", description="Currency code for total_amount (org default is AED)."),
                "bill_status": ColumnHint(type="VARCHAR", description="Current status of this bill."),
            },
        ),
        "grouped_results": TableHint(
            description=(
                "Only populated when group_by was requested. One row per unique combination of the "
                "caller-chosen group_by dimensions. Metrics are bill_count/total_amount for every "
                "dimension EXCEPT when 'unit_id' is included in group_by, which switches the metrics "
                "to allocation_count/allocated_amount (and adds unit_code/unit_name columns)."
            ),
            columns={
                "bill_status": ColumnHint(type="VARCHAR", description="Present only when group_by includes 'bill_status'."),
                "utility_category_name": ColumnHint(type="VARCHAR", description="Present only when group_by includes 'utility_category'."),
                "account_id": ColumnHint(type="VARCHAR", description="Present only when group_by includes 'account_id'."),
                "billing_period": ColumnHint(type="TIMESTAMP", description="Present only when group_by includes 'billing_period'."),
                "month": ColumnHint(type="TIMESTAMP", description="Month truncation of billing_period. Present only when group_by includes 'month'."),
                "quarter": ColumnHint(type="TIMESTAMP", description="Quarter truncation of billing_period. Present only when group_by includes 'quarter'."),
                "year": ColumnHint(type="TIMESTAMP", description="Year truncation of billing_period. Present only when group_by includes 'year'."),
                "bill_date": ColumnHint(type="TIMESTAMP", description="Present only when group_by includes 'bill_date'."),
                "unit_id": ColumnHint(type="VARCHAR", description="Present only when group_by includes 'unit_id'. Switches metrics to allocation_count/allocated_amount."),
                "unit_code": ColumnHint(type="VARCHAR", description="Present only when group_by includes 'unit_id'."),
                "unit_name": ColumnHint(type="VARCHAR", description="Present only when group_by includes 'unit_id'."),
                "bill_count": ColumnHint(type="INTEGER", description="Number of bills in this group. Absent when 'unit_id' is in group_by."),
                "total_amount": ColumnHint(type="FLOAT", description="Sum of total_amount in this group. Absent when 'unit_id' is in group_by."),
                "allocation_count": ColumnHint(type="INTEGER", description="Number of allocation rows in this group. Present only when 'unit_id' is in group_by."),
                "allocated_amount": ColumnHint(type="FLOAT", description="Sum of allocated_amount in this group. Present only when 'unit_id' is in group_by."),
            },
        ),
    },

    dataset_source_label="bill_summary_api",
)
