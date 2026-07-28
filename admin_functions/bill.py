import calendar
import re
import uuid
from datetime import date

from dateutil import parser as date_parser

from common_functions.db import get_db_cursor
from common_functions import config

VALID_BILL_STATUSES = ("Pending Approval", "Approved", "On Hold", "Resolved")

# Bounds on the LLM-facing summary so a large dataset can't blow up the payload the
# chatbot has to fit into its context window. The trend is one row per billing month
# so it's naturally bounded; only the "top" lists need an explicit cap.
TOP_BILLS_LIMIT = 10
BY_UNIT_LIMIT = 20

SORTABLE_BILL_COLUMNS = {
    "bill_id",
    "account_id",
    "batch_id",
    "bill_status",
    "billing_period",
    "bill_date",
    "total_amount",
    "created_on",
}


def _is_valid_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _parse_date(value):
    if isinstance(value, date):
        return value
    try:
        return date_parser.parse(str(value)).date()
    except (ValueError, OverflowError, TypeError):
        return None


def f_filter_bill(
    uuid_caller_user_id,
    str_caller_role,
    uuid_bill_id,
    uuid_account_id,
    uuid_unit_id,
    uuid_batch_id,
    str_bill_status,
    date_billing_period,
    date_bill_date_from,
    date_bill_date_to,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        for value in (uuid_bill_id, uuid_account_id, uuid_unit_id, uuid_batch_id):
            if value and not _is_valid_uuid(value):
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_bill_status and str_bill_status not in VALID_BILL_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        parsed_billing_period = None
        parsed_bill_date_from = None
        parsed_bill_date_to = None
        if date_billing_period:
            parsed_billing_period = _parse_date(date_billing_period)
            if parsed_billing_period is None:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if date_bill_date_from:
            parsed_bill_date_from = _parse_date(date_bill_date_from)
            if parsed_bill_date_from is None:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if date_bill_date_to:
            parsed_bill_date_to = _parse_date(date_bill_date_to)
            if parsed_bill_date_to is None:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no = 1 if int_page_no is None else int_page_no
        int_page_size = 20 if int_page_size is None else int_page_size
        if not isinstance(int_page_no, int) or int_page_no < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(int_page_size, int) or int_page_size < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by = str_sort_by or "bill_date"
        if str_sort_by not in SORTABLE_BILL_COLUMNS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        str_sort_order = (str_sort_order or "DESC").upper()
        if str_sort_order not in ("ASC", "DESC"):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role not in ("Administrator", "Leadership", "Unit Accountant"):
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: scope Unit Accountant to their assigned units (NFR-001)
            assigned_unit_ids = None
            if str_caller_role == "Unit Accountant":
                cursor.execute(
                    "SELECT unit_id FROM user_unit_details WHERE user_id = %s AND assignment_status = 'Active'",
                    (uuid_caller_user_id,),
                )
                assigned_unit_ids = [row["unit_id"] for row in cursor.fetchall()]

            # 4. Core DB operation: dynamic WHERE clause (joined to bill_unit_details when
            # unit-scoped) + pagination
            where_clauses = []
            values = []
            if uuid_bill_id:
                where_clauses.append("bill.bill_id = %s")
                values.append(uuid_bill_id)
            if uuid_account_id:
                where_clauses.append("bill.account_id = %s")
                values.append(uuid_account_id)
            if uuid_batch_id:
                where_clauses.append("bill.batch_id = %s")
                values.append(uuid_batch_id)
            if str_bill_status:
                where_clauses.append("bill.bill_status = %s")
                values.append(str_bill_status)
            if parsed_billing_period:
                where_clauses.append("bill.billing_period = %s")
                values.append(parsed_billing_period)
            if parsed_bill_date_from:
                where_clauses.append("bill.bill_date >= %s")
                values.append(parsed_bill_date_from)
            if parsed_bill_date_to:
                where_clauses.append("bill.bill_date <= %s")
                values.append(parsed_bill_date_to)

            unit_exists_conditions = []
            unit_exists_values = []
            if uuid_unit_id:
                unit_exists_conditions.append("bud.unit_id = %s")
                unit_exists_values.append(uuid_unit_id)
            if assigned_unit_ids is not None:
                # Cast to uuid[] -- psycopg2 sends the id list as text[], and `uuid = ANY(text[])`
                # has no operator; the cast also makes an empty list a valid (match-nothing) uuid[].
                unit_exists_conditions.append("bud.unit_id = ANY(%s::uuid[])")
                unit_exists_values.append(assigned_unit_ids)

            if unit_exists_conditions:
                where_clauses.append(
                    "EXISTS (SELECT 1 FROM bill_unit_details bud WHERE bud.bill_id = bill.bill_id AND "
                    + " AND ".join(unit_exists_conditions)
                    + ")"
                )
                values.extend(unit_exists_values)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM bill {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            # Join account_master for display fields so callers can show the account name/number
            # (and category) without a separate Administrator-only lookup.
            cursor.execute(
                f"""
                SELECT bill.*,
                    am.account_number AS account_number,
                    am.account_name AS account_name,
                    am.utility_category_name AS utility_category_name
                FROM bill
                JOIN account_master am ON bill.account_id = am.account_id
                {where_sql}
                ORDER BY bill.{str_sort_by} {str_sort_order}
                LIMIT %s OFFSET %s
                """,
                tuple(values) + (int_page_size, offset),
            )
            records = cursor.fetchall()

        # 5. No data found
        if not records:
            return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

        # 6. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"records": records, "page_no": int_page_no, "page_size": int_page_size, "total_count": total_count},
        )

    except Exception as ex:
        # 7. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def _f(value):
    """Decimal/None -> float, so the summary is a clean numeric JSON contract for the LLM
    (response.py serializes Decimals via str() otherwise, which would surface amounts as
    strings and confuse downstream numeric reasoning)."""
    return float(value) if value is not None else 0.0


def _iso(value):
    """date/datetime -> ISO string (or the value unchanged if it's already a plain type)."""
    return value.isoformat() if hasattr(value, "isoformat") else value

VALID_DAY_FLAGS = ("Daily", "Monthly", "Quarterly", "Yearly")

# Caller-chosen group_by dimensions (mirrors f_summarize_transaction_log's group_by).
# "unit_id" pulls its metrics from bill_unit_details (allocated_amount) via alloc_base;
# every other dimension pulls from bill (total_amount) via bill_base -- see the
# use_alloc_base switch in f_get_bill_summary_details.
VALID_GROUP_BY_FIELDS = {
    "bill_status": "bill.bill_status",
    "utility_category": "am.utility_category_name",
    "account_id": "bill.account_id",
    "billing_period": "bill.billing_period",
    "month": "DATE_TRUNC('month', bill.billing_period)",
    "quarter": "DATE_TRUNC('quarter', bill.billing_period)",
    "year": "DATE_TRUNC('year', bill.billing_period)",
    "bill_date": "bill.bill_date",
    "unit_id": "bud.unit_id",
}


def _parse_day_flag(str_day_flag, str_day_value):
    """Parse a day_flag + day_value pair into a (date_from, date_to, use_bill_date) tuple.

    billing_period is always stored as the first day of the month (YYYY-MM-01), so
    Monthly / Quarterly / Yearly filter on billing_period. Daily is different -- it
    filters on bill.bill_date (the actual transaction date) because billing_period
    would never equal a mid-month day.

        Daily      DD-MM-YYYY  ->  bill.bill_date = that date  (use_bill_date=True)
        Monthly    MM-YYYY     ->  billing_period = YYYY-MM-01 (exact, use_bill_date=False)
        Quarterly  Q1-YYYY     ->  billing_period in [quarter_start, quarter_end]
        Yearly     YYYY        ->  billing_period in [YYYY-01-01, YYYY-12-01]

    Returns (date_from, date_to, use_bill_date) on success, or raises ValueError.
    """
    if str_day_flag not in VALID_DAY_FLAGS:
        raise ValueError(f"day_flag must be one of {VALID_DAY_FLAGS}.")
    if not str_day_value or not isinstance(str_day_value, str):
        raise ValueError("day_value is required when day_flag is provided.")

    v = str_day_value.strip()

    if str_day_flag == "Daily":
        # Expected: DD-MM-YYYY (also tolerates an ISO YYYY-MM-DD, since LLM callers
        # occasionally send that format despite the documented contract).
        # Filters on bill.bill_date (NOT billing_period) because billing_period is always
        # the first of the month and a specific day would never match it.
        m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", v)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m_iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
            if not m_iso:
                raise ValueError("day_value for 'Daily' must be in DD-MM-YYYY format.")
            year, month, day = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        d = date(year, month, day)
        return d, d, True   # use_bill_date=True

    if str_day_flag == "Monthly":
        # Expected: MM-YYYY -- exact match on first-of-month billing_period (also tolerates
        # an ISO YYYY-MM, since LLM callers occasionally send that format).
        m = re.fullmatch(r"(\d{2})-(\d{4})", v)
        if m:
            month, year = int(m.group(1)), int(m.group(2))
        else:
            m_iso = re.fullmatch(r"(\d{4})-(\d{2})", v)
            if not m_iso:
                raise ValueError("day_value for 'Monthly' must be in MM-YYYY format.")
            year, month = int(m_iso.group(1)), int(m_iso.group(2))
        first_of_month = date(year, month, 1)
        return first_of_month, first_of_month, False

    if str_day_flag == "Quarterly":
        # Expected: Q1-YYYY .. Q4-YYYY -- range over first-of-month billing_period values
        m = re.fullmatch(r"Q([1-4])-(\d{4})", v)
        if not m:
            raise ValueError("day_value for 'Quarterly' must be in Q1-YYYY .. Q4-YYYY format.")
        quarter, year = int(m.group(1)), int(m.group(2))
        start_month = (quarter - 1) * 3 + 1          # Q1->1, Q2->4, Q3->7, Q4->10
        end_month = start_month + 2
        return date(year, start_month, 1), date(year, end_month, 1), False

    if str_day_flag == "Yearly":
        # Expected: YYYY -- range over all first-of-month billing_period values in the year
        m = re.fullmatch(r"(\d{4})", v)
        if not m:
            raise ValueError("day_value for 'Yearly' must be a 4-digit year (YYYY).")
        year = int(m.group(1))
        return date(year, 1, 1), date(year, 12, 1), False



def _bill_where(
    uuid_account_id, str_bill_status, date_from_date, date_to_date, date_exact_bill_date=None,
    str_utility_category_name=None, uuid_unit_id=None, date_bill_date_from=None, date_bill_date_to=None,
):
    """Bill-level WHERE fragments over the `FROM bill JOIN account_master am` base.

    date_exact_bill_date: when set (Daily flag), filters bill.bill_date = that date.
    date_from_date / date_to_date: when set, filter bill.billing_period as a range (day_flag).
    date_bill_date_from / date_bill_date_to: when set, filter bill.bill_date as a range
        (from_date/to_date) -- mutually exclusive with day_flag at the caller level.
    str_utility_category_name: when set, filters am.utility_category_name (case-insensitive).
    uuid_unit_id: when set, restricts to bills that have a bill_unit_details allocation row
        for that unit -- this base has no bud join, so it's expressed as an EXISTS subquery.
    """
    clauses = []
    values = []
    if uuid_account_id:
        clauses.append("bill.account_id = %s")
        values.append(uuid_account_id)
    if str_bill_status:
        clauses.append("bill.bill_status = %s")
        values.append(str_bill_status)
    if str_utility_category_name:
        clauses.append("am.utility_category_name ILIKE %s")
        values.append(str_utility_category_name)
    if uuid_unit_id:
        clauses.append(
            "EXISTS (SELECT 1 FROM bill_unit_details bud_f WHERE bud_f.bill_id = bill.bill_id AND bud_f.unit_id = %s)"
        )
        values.append(uuid_unit_id)
    if date_exact_bill_date:
        # Daily: exact match on the actual bill transaction date
        clauses.append("bill.bill_date = %s")
        values.append(date_exact_bill_date)
    else:
        if date_from_date:
            clauses.append("bill.billing_period >= %s")
            values.append(date_from_date)
        if date_to_date:
            clauses.append("bill.billing_period <= %s")
            values.append(date_to_date)
    if date_bill_date_from:
        clauses.append("bill.bill_date >= %s")
        values.append(date_bill_date_from)
    if date_bill_date_to:
        clauses.append("bill.bill_date <= %s")
        values.append(date_bill_date_to)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, values


def _alloc_where(
    uuid_account_id, str_bill_status, date_from_date, date_to_date, date_exact_bill_date=None,
    str_utility_category_name=None, uuid_unit_id=None, date_bill_date_from=None, date_bill_date_to=None,
):
    """Allocation-level WHERE fragments over the bill_unit_details (bud) join base.

    date_exact_bill_date: when set (Daily flag), filters bill.bill_date = that date.
    date_from_date / date_to_date: when set, filter bill.billing_period as a range (day_flag).
    date_bill_date_from / date_bill_date_to: when set, filter bill.bill_date as a range
        (from_date/to_date) -- mutually exclusive with day_flag at the caller level.
    str_utility_category_name: when set, filters am.utility_category_name (case-insensitive).
    uuid_unit_id: when set, filters bud.unit_id directly since this base already joins bud.
    """
    clauses = []
    values = []
    if uuid_account_id:
        clauses.append("bill.account_id = %s")
        values.append(uuid_account_id)
    if str_bill_status:
        clauses.append("bill.bill_status = %s")
        values.append(str_bill_status)
    if str_utility_category_name:
        clauses.append("am.utility_category_name ILIKE %s")
        values.append(str_utility_category_name)
    if uuid_unit_id:
        clauses.append("bud.unit_id = %s")
        values.append(uuid_unit_id)
    if date_exact_bill_date:
        # Daily: exact match on the actual bill transaction date
        clauses.append("bill.bill_date = %s")
        values.append(date_exact_bill_date)
    else:
        if date_from_date:
            clauses.append("bill.billing_period >= %s")
            values.append(date_from_date)
        if date_to_date:
            clauses.append("bill.billing_period <= %s")
            values.append(date_to_date)
    if date_bill_date_from:
        clauses.append("bill.bill_date >= %s")
        values.append(date_bill_date_from)
    if date_bill_date_to:
        clauses.append("bill.bill_date <= %s")
        values.append(date_bill_date_to)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, values


def f_get_bill_summary_details(
    uuid_caller_user_id,
    str_caller_role,
    uuid_account_id,
    str_bill_status,
    str_utility_category_name=None,
    uuid_unit_id=None,
    str_day_flag=None,
    str_day_value=None,
    str_date_from=None,
    str_date_to=None,
    list_group_by=None,
):
    """Consolidated, LLM-facing summary of the bill domain -- the counterpart to the reference
    `f_summarize_transaction_log`. Always returns the fixed set of pre-computed breakdowns
    (by_status/by_category/by_unit/monthly_trend/top_bills); when `list_group_by` is supplied it
    additionally returns a `grouped_results` array over the caller-chosen dimensions, same as the
    reference's group_by. Intended to be fetched by the chatbot backend and handed to the LLM as
    grounding context so it can answer open-ended bill questions.

    str_date_from / str_date_to : str, optional
        Arbitrary date range (any format dateutil can parse, e.g. "DD-MM-YYYY" or ISO), filtered
        against bill.bill_date (the actual transaction date), NOT billing_period. Either bound may
        be supplied alone for an open-ended range. Mutually exclusive with day_flag/day_value --
        supplying both is rejected as invalid input, since the two describe the period two
        different ways.

    str_utility_category_name : str, optional
        When supplied, restricts all sections to bills whose account belongs to the given utility
        category (case-insensitive match against account_master.utility_category_name).

    uuid_unit_id : str, optional
        When supplied, restricts all sections to bills allocated to the given unit -- accepts
        EITHER the unit's real UUID OR its human-readable unit_code (e.g. 'U-101'), resolved the
        same way uuid_account_id resolves an account_number. An unrecognized unit_code is rejected
        (mirrors the account_number behaviour); a stale/unknown UUID just narrows results to zero.

    list_group_by : list[str], optional
        Any of VALID_GROUP_BY_FIELDS: bill_status, utility_category, account_id, billing_period,
        month, quarter, year, bill_date, unit_id. "unit_id" switches the grouped metrics to
        allocation_count/allocated_amount (bill_unit_details basis); every other combination uses
        bill_count/total_amount (bill basis) -- mixing "unit_id" with other fields still resolves
        those other fields against the alloc_base join, since it already includes bill+am.

    All filters are optional and AND-combined; billing_period is scoped via day_flag + day_value:
        Daily      DD-MM-YYYY  ->  exact day
        Monthly    MM-YYYY     ->  full calendar month
        Quarterly  Q1-YYYY     ->  calendar quarter (Q1=Jan-Mar … Q4=Oct-Dec)
        Yearly     YYYY        ->  full calendar year

    Money follows a "both where relevant" basis: bill.total_amount for the org/status/category/
    monthly/top-bills sections (one row per bill), and bill_unit_details.allocated_amount for the
    by-unit section (per-unit allocations).

    Never returns 404 -- an empty/all-zero summary is a valid answer for the LLM (matching the
    dashboard summaries, unlike the paginated report endpoints).

    Returns: (int_status_code, str_status_description, str_error_message, json_response_body)
    """
    try:
        # 1. Validate input
        # NOTE: account_id is intentionally NOT format-validated here -- it doubles as either
        # a real UUID or a human-readable account_number (e.g. "ACCT-502"). Which one it is
        # gets resolved once, right after the RBAC check, before any WHERE clauses are built.
        if str_bill_status and str_bill_status not in VALID_BILL_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        list_group_by = list_group_by or []
        if not isinstance(list_group_by, list) or any(
            field not in VALID_GROUP_BY_FIELDS for field in list_group_by
        ):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 1a. Resolve date filter via day_flag (optional).
        parsed_date_from = None
        parsed_date_to = None
        use_bill_date = False     # True only for Daily -- filters bill.bill_date instead of billing_period
        day_flag_applied = None   # stored for filters_applied echo

        # 1b. Resolve arbitrary bill_date range via from_date/to_date (optional, mutually
        # exclusive with day_flag -- the two express the same concept two different ways).
        parsed_range_from = None
        parsed_range_to = None
        if str_date_from or str_date_to:
            if str_day_flag:
                return (
                    config.STATUS_INVALID_INPUT,
                    config.DESC_INVALID_INPUT,
                    "from_date/to_date cannot be combined with day_flag/day_value.",
                    None,
                )
            if str_date_from:
                parsed_range_from = _parse_date(str_date_from)
                if parsed_range_from is None:
                    return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "Invalid from_date.", None
            if str_date_to:
                parsed_range_to = _parse_date(str_date_to)
                if parsed_range_to is None:
                    return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "Invalid to_date.", None
            if parsed_range_from and parsed_range_to and parsed_range_from > parsed_range_to:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "from_date must not be after to_date.", None

        if str_day_flag:
            try:
                parsed_date_from, parsed_date_to, use_bill_date = _parse_day_flag(str_day_flag, str_day_value)
                day_flag_applied = {"day_flag": str_day_flag, "day_value": str_day_value.strip()}
            except ValueError as ve:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, str(ve), None

        # 2. RBAC check -- Administrator only (mirrors the reference being an admin_api)
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # ---- Resolve the account reference -- account_id doubles as either a real UUID or
            #      an account_number (e.g. "ACCT-502"); account_number is UNIQUE, so this always
            #      resolves to at most one row. A stale/unknown UUID is NOT an error (this
            #      endpoint never 404s -- it just narrows results to zero), but an unrecognized
            #      account_number IS rejected here, since there would be no account_id left to
            #      filter on afterwards. ----
            account_lookup = None
            if uuid_account_id:
                if _is_valid_uuid(uuid_account_id):
                    cursor.execute(
                        "SELECT account_id, account_number, account_name FROM account_master WHERE account_id = %s",
                        (uuid_account_id,),
                    )
                    account_lookup = cursor.fetchone()
                else:
                    cursor.execute(
                        "SELECT account_id, account_number, account_name FROM account_master WHERE account_number ILIKE %s",
                        (uuid_account_id,),
                    )
                    account_lookup = cursor.fetchone()
                    if not account_lookup:
                        return (
                            config.STATUS_INVALID_INPUT,
                            config.DESC_INVALID_INPUT,
                            f"No account found with account_number '{uuid_account_id}'.",
                            None,
                        )
                    uuid_account_id = account_lookup["account_id"]

            # ---- Resolve the unit reference -- unit_id doubles as either a real UUID or a
            #      unit_code (e.g. "U-101"), same dual-mode resolution as account_id above. ----
            unit_lookup = None
            if uuid_unit_id:
                if _is_valid_uuid(uuid_unit_id):
                    cursor.execute(
                        "SELECT unit_id, unit_code, unit_name FROM unit_master WHERE unit_id = %s",
                        (uuid_unit_id,),
                    )
                    unit_lookup = cursor.fetchone()
                else:
                    cursor.execute(
                        "SELECT unit_id, unit_code, unit_name FROM unit_master WHERE unit_code ILIKE %s",
                        (uuid_unit_id,),
                    )
                    unit_lookup = cursor.fetchone()
                    if not unit_lookup:
                        return (
                            config.STATUS_INVALID_INPUT,
                            config.DESC_INVALID_INPUT,
                            f"No unit found with unit_code '{uuid_unit_id}'.",
                            None,
                        )
                    uuid_unit_id = unit_lookup["unit_id"]

            # Note: account_id/unit_id are NOT existence-checked beyond the resolution above --
            # a stale/unknown UUID simply narrows the affected section(s) to zero rather than
            # failing the whole summary.
            # For Daily, pass the date as date_exact_bill_date so WHERE filters bill.bill_date.
            # For all other flags, pass as billing_period range bounds.
            exact_bill_date = parsed_date_from if use_bill_date else None
            bp_from = None if use_bill_date else parsed_date_from
            bp_to   = None if use_bill_date else parsed_date_to

            bill_where, bill_values = _bill_where(
                uuid_account_id, str_bill_status, bp_from, bp_to, exact_bill_date,
                str_utility_category_name, uuid_unit_id,
                date_bill_date_from=parsed_range_from, date_bill_date_to=parsed_range_to,
            )
            alloc_where, alloc_values = _alloc_where(
                uuid_account_id, str_bill_status, bp_from, bp_to, exact_bill_date,
                str_utility_category_name, uuid_unit_id,
                date_bill_date_from=parsed_range_from, date_bill_date_to=parsed_range_to,
            )
            bill_base = "FROM bill JOIN account_master am ON bill.account_id = am.account_id"
            alloc_base = """
                FROM bill_unit_details bud
                JOIN bill ON bud.bill_id = bill.bill_id
                JOIN account_master am ON bill.account_id = am.account_id
                JOIN unit_master um ON bud.unit_id = um.unit_id
            """

            # ---- Section: bill counts + amounts by status (drives the KPIs too) ----
            cursor.execute(
                f"""
                SELECT bill.bill_status AS bill_status,
                       COUNT(*) AS bill_count,
                       COALESCE(SUM(bill.total_amount), 0) AS total_amount
                {bill_base}
                {bill_where}
                GROUP BY bill.bill_status
                """,
                tuple(bill_values),
            )
            status_rows = {r["bill_status"]: r for r in cursor.fetchall()}

            total_bills = sum(r["bill_count"] for r in status_rows.values())
            total_amount = sum(_f(r["total_amount"]) for r in status_rows.values())

            def _status_count(name):
                row = status_rows.get(name)
                return row["bill_count"] if row else 0

            def _status_amount(name):
                row = status_rows.get(name)
                return _f(row["total_amount"]) if row else 0.0

            by_status = [
                {
                    "bill_status": status,
                    "bill_count": _status_count(status),
                    "total_amount": _status_amount(status),
                }
                for status in VALID_BILL_STATUSES
            ]

            # ---- Section: spend by utility category ----
            cursor.execute(
                f"""
                SELECT am.utility_category_name AS utility_category_name,
                       COUNT(*) AS bill_count,
                       COALESCE(SUM(bill.total_amount), 0) AS total_amount
                {bill_base}
                {bill_where}
                GROUP BY am.utility_category_name
                ORDER BY total_amount DESC, am.utility_category_name
                """,
                tuple(bill_values),
            )
            by_category = [
                {
                    "utility_category_name": r["utility_category_name"],
                    "bill_count": r["bill_count"],
                    "total_amount": _f(r["total_amount"]),
                }
                for r in cursor.fetchall()
            ]

            # ---- Section: monthly trend (by billing_period) ----
            cursor.execute(
                f"""
                SELECT bill.billing_period AS billing_period,
                       COUNT(*) AS bill_count,
                       COALESCE(SUM(bill.total_amount), 0) AS total_amount
                {bill_base}
                {bill_where}
                GROUP BY bill.billing_period
                ORDER BY bill.billing_period
                """,
                tuple(bill_values),
            )
            monthly_trend = [
                {
                    "billing_period": _iso(r["billing_period"]),
                    "bill_count": r["bill_count"],
                    "total_amount": _f(r["total_amount"]),
                }
                for r in cursor.fetchall()
            ]

            # ---- Section: top bills by amount ----
            cursor.execute(
                f"""
                SELECT bill.bill_id AS bill_id,
                       bill.account_id AS account_id,
                       am.account_number AS account_number,
                       am.account_name AS account_name,
                       am.utility_category_name AS utility_category_name,
                       bill.billing_period AS billing_period,
                       bill.bill_date AS bill_date,
                       bill.total_amount AS total_amount,
                       bill.currency AS currency,
                       bill.bill_status AS bill_status
                {bill_base}
                {bill_where}
                ORDER BY bill.total_amount DESC
                LIMIT %s
                """,
                tuple(bill_values) + (TOP_BILLS_LIMIT,),
            )
            top_bills = [
                {
                    "bill_id": r["bill_id"],
                    "account_id": r["account_id"],
                    "account_number": r["account_number"],
                    "account_name": r["account_name"],
                    "utility_category_name": r["utility_category_name"],
                    "billing_period": _iso(r["billing_period"]),
                    "bill_date": _iso(r["bill_date"]),
                    "total_amount": _f(r["total_amount"]),
                    "currency": r["currency"],
                    "bill_status": r["bill_status"],
                }
                for r in cursor.fetchall()
            ]

            # ---- Section: spend by unit (money from per-unit allocated_amount) ----
            cursor.execute(
                f"""
                SELECT bud.unit_id AS unit_id,
                       um.unit_code AS unit_code,
                       um.unit_name AS unit_name,
                       COUNT(*) AS allocation_count,
                       COALESCE(SUM(bud.allocated_amount), 0) AS allocated_amount
                {alloc_base}
                {alloc_where}
                GROUP BY bud.unit_id, um.unit_code, um.unit_name
                ORDER BY allocated_amount DESC
                LIMIT %s
                """,
                tuple(alloc_values) + (BY_UNIT_LIMIT,),
            )
            by_unit = [
                {
                    "unit_id": r["unit_id"],
                    "unit_code": r["unit_code"],
                    "unit_name": r["unit_name"],
                    "allocation_count": r["allocation_count"],
                    "allocated_amount": _f(r["allocated_amount"]),
                }
                for r in cursor.fetchall()
            ]

            # ---- Section: allocation totals across ALL matching units (unlimited by
            #      BY_UNIT_LIMIT) -- lets the LLM reconcile by_unit (top N only) against the
            #      org-wide total, and surfaces bills that don't have per-unit allocation rows
            #      yet (their money would otherwise silently disappear from by_unit). ----
            cursor.execute(
                f"""
                SELECT COUNT(DISTINCT bud.unit_id) AS unit_count,
                       COALESCE(SUM(bud.allocated_amount), 0) AS total_allocated_amount
                {alloc_base}
                {alloc_where}
                """,
                tuple(alloc_values),
            )
            alloc_totals_row = cursor.fetchone()
            distinct_unit_count = alloc_totals_row["unit_count"]
            total_allocated_amount = _f(alloc_totals_row["total_allocated_amount"])

            # ---- Optional caller-chosen grouping (mirrors f_summarize_transaction_log's
            #      group_by) -- runs over the same filtered WHERE as the sections above. ----
            grouped_results = None
            if list_group_by:
                use_alloc_base = "unit_id" in list_group_by
                group_base = alloc_base if use_alloc_base else bill_base
                group_where, group_values = (
                    (alloc_where, alloc_values) if use_alloc_base else (bill_where, bill_values)
                )

                group_select_exprs = []
                group_by_exprs = []
                for field in list_group_by:
                    expr = VALID_GROUP_BY_FIELDS[field]
                    group_select_exprs.append(f"{expr} AS {field}")
                    group_by_exprs.append(expr)
                    if field == "unit_id":
                        # Pull display columns alongside the id -- must also be in GROUP BY.
                        group_select_exprs += ["um.unit_code AS unit_code", "um.unit_name AS unit_name"]
                        group_by_exprs += ["um.unit_code", "um.unit_name"]

                if use_alloc_base:
                    metrics_select = (
                        "COUNT(*) AS allocation_count, "
                        "COALESCE(SUM(bud.allocated_amount), 0) AS allocated_amount"
                    )
                else:
                    metrics_select = (
                        "COUNT(*) AS bill_count, "
                        "COALESCE(SUM(bill.total_amount), 0) AS total_amount"
                    )

                cursor.execute(
                    f"""
                    SELECT {', '.join(group_select_exprs)}, {metrics_select}
                    {group_base}
                    {group_where}
                    GROUP BY {', '.join(group_by_exprs)}
                    ORDER BY {', '.join(group_by_exprs)}
                    """,
                    tuple(group_values),
                )
                grouped_results = []
                for row in cursor.fetchall():
                    record = dict(row)
                    for k, v in record.items():
                        if hasattr(v, "isoformat"):
                            record[k] = v.isoformat()
                        elif v is not None and not isinstance(v, (int, str)):
                            record[k] = float(v)
                    grouped_results.append(record)

        # Echo the applied (non-empty) filters back so the LLM knows exactly what scope the
        # numbers cover -- avoids it over-generalising a filtered summary to the whole org.
        filters_applied = {}
        if uuid_account_id:
            filters_applied["account_id"] = uuid_account_id
            if account_lookup:
                filters_applied["account_number"] = account_lookup["account_number"]
                filters_applied["account_name"] = account_lookup["account_name"]
            else:
                filters_applied["account_id_found"] = False
        if str_bill_status:
            filters_applied["bill_status"] = str_bill_status
        if str_utility_category_name:
            filters_applied["utility_category_name"] = str_utility_category_name
        if uuid_unit_id:
            filters_applied["unit_id"] = uuid_unit_id
            if unit_lookup:
                filters_applied["unit_code"] = unit_lookup["unit_code"]
                filters_applied["unit_name"] = unit_lookup["unit_name"]
            else:
                filters_applied["unit_id_found"] = False
        if day_flag_applied:
            # day_flag -- report the resolved billing_period bounds so the LLM sees the
            # exact date window that was applied.
            filters_applied["day_flag"] = day_flag_applied["day_flag"]
            filters_applied["day_value"] = day_flag_applied["day_value"]
            filters_applied["resolved_date_range"] = {
                "start": _iso(parsed_date_from),
                "end": _iso(parsed_date_to),
            }
        if parsed_range_from or parsed_range_to:
            filters_applied["from_date"] = _iso(parsed_range_from)
            filters_applied["to_date"] = _iso(parsed_range_to)

        # No STATUS_NO_DATA_FOUND: an empty/all-zero summary is a valid 200 answer for the LLM.
        response_body = {
            "metadata": {
                "currency": config.DEFAULT_CURRENCY,
                "filters_applied": filters_applied,
                "group_by_applied": list_group_by,
                "top_bills_limit": TOP_BILLS_LIMIT,
                "top_bills_total_count": total_bills,
                "by_unit_limit": BY_UNIT_LIMIT,
                "by_unit_total_unit_count": distinct_unit_count,
            },
            "kpis": {
                "total_bills": total_bills,
                "total_amount": round(total_amount, 2),
                "average_bill_amount": round(total_amount / total_bills, 2) if total_bills else 0.0,
                "pending_bills": _status_count("Pending Approval"),
                "pending_amount": round(_status_amount("Pending Approval"), 2),
                "approved_bills": _status_count("Approved"),
                "approved_amount": round(_status_amount("Approved"), 2),
                "on_hold_bills": _status_count("On Hold"),
                "on_hold_amount": round(_status_amount("On Hold"), 2),
                "resolved_bills": _status_count("Resolved"),
                "resolved_amount": round(_status_amount("Resolved"), 2),
                "total_allocated_amount": round(total_allocated_amount, 2),
                "unallocated_amount": round(total_amount - total_allocated_amount, 2),
            },
            "by_status": by_status,
            "by_category": by_category,
            "by_unit": by_unit,
            "monthly_trend": monthly_trend,
            "top_bills": top_bills,
        }
        if grouped_results is not None:
            response_body["grouped_results"] = grouped_results

        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", response_body

    except Exception as ex:
        # System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
