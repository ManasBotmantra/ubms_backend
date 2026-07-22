import uuid
from datetime import date, datetime, timedelta

from common_functions.db import get_db_cursor
from common_functions import config

VALID_BILL_STATUSES = ("Pending Approval", "Approved", "On Hold", "Resolved")


def _is_valid_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def _parse_date(value):
    """Returns a date object for value, or None if value is empty. Raises ValueError if malformed."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _resolve_pagination(int_page_no, int_page_size):
    int_page_no = 1 if int_page_no is None else int_page_no
    int_page_size = 20 if int_page_size is None else int_page_size
    if not isinstance(int_page_no, int) or int_page_no < 1:
        return None, None
    if not isinstance(int_page_size, int) or int_page_size < 1:
        return None, None
    return int_page_no, int_page_size


def _resolve_sort(str_sort_by, str_sort_order, allowed_columns, default_sort_by, default_sort_order):
    if str_sort_by and str_sort_by not in allowed_columns:
        return None, None
    if str_sort_order and str(str_sort_order).upper() not in ("ASC", "DESC"):
        return None, None
    str_sort_by = str_sort_by or default_sort_by
    str_sort_order = (str_sort_order or default_sort_order).upper()
    return str_sort_by, str_sort_order


def f_get_unit_consumption_trend_report(
    uuid_caller_user_id,
    str_caller_role,
    uuid_unit_id,
    str_utility_category_name,
    date_from_date,
    date_to_date,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        if not uuid_unit_id or not _is_valid_uuid(uuid_unit_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        try:
            date_to_date = _parse_date(date_to_date)
            date_from_date = _parse_date(date_from_date)
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        if date_to_date is None:
            date_to_date = date.today()
        if date_from_date is None:
            date_from_date = date_to_date - timedelta(days=365)

        int_page_no, int_page_size = _resolve_pagination(int_page_no, int_page_size)
        if int_page_no is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by, str_sort_order = _resolve_sort(
            str_sort_by, str_sort_order, {"billing_period", "total_amount"}, "billing_period", "ASC"
        )
        if str_sort_by is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Leadership":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: confirm uuid_unit_id exists in unit_master
            cursor.execute("SELECT unit_id FROM unit_master WHERE unit_id = %s", (uuid_unit_id,))
            if not cursor.fetchone():
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            # 4. Core DB operation: aggregate bill_unit_details for the unit, grouped by billing_period
            where_clauses = ["bill_unit_details.unit_id = %s", "bill.billing_period >= %s", "bill.billing_period <= %s"]
            values = [uuid_unit_id, date_from_date, date_to_date]
            if str_utility_category_name:
                where_clauses.append("account_master.utility_category_name = %s")
                values.append(str_utility_category_name)

            where_sql = f"WHERE {' AND '.join(where_clauses)}"
            join_sql = """
                FROM bill_unit_details
                JOIN bill ON bill_unit_details.bill_id = bill.bill_id
                JOIN account_master ON bill.account_id = account_master.account_id
            """

            cursor.execute(
                f"""
                SELECT COUNT(*) AS total_count FROM (
                    SELECT bill.billing_period
                    {join_sql}
                    {where_sql}
                    GROUP BY bill.billing_period
                ) AS grouped_rows
                """,
                tuple(values),
            )
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"""
                SELECT bill.billing_period AS billing_period,
                       SUM(bill_unit_details.allocated_amount) AS total_amount,
                       COUNT(*) AS bill_count
                {join_sql}
                {where_sql}
                GROUP BY bill.billing_period
                ORDER BY {str_sort_by} {str_sort_order}
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


def f_get_account_bifurcation_breakdown_report(
    uuid_caller_user_id,
    str_caller_role,
    uuid_account_id,
    date_billing_period,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        if not uuid_account_id or not _is_valid_uuid(uuid_account_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        try:
            date_billing_period = _parse_date(date_billing_period)
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no, int_page_size = _resolve_pagination(int_page_no, int_page_size)
        if int_page_no is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by, str_sort_order = _resolve_sort(
            str_sort_by,
            str_sort_order,
            {"allocated_amount", "allocated_percentage", "unit_id", "unit_name"},
            "allocated_amount",
            "DESC",
        )
        if str_sort_by is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Leadership":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: confirm account exists; resolve latest billing_period if omitted
            cursor.execute("SELECT account_id FROM account_master WHERE account_id = %s", (uuid_account_id,))
            if not cursor.fetchone():
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            if date_billing_period is None:
                cursor.execute(
                    "SELECT MAX(billing_period) AS latest_billing_period FROM bill WHERE account_id = %s",
                    (uuid_account_id,),
                )
                latest_row = cursor.fetchone()
                date_billing_period = latest_row["latest_billing_period"] if latest_row else None
                if date_billing_period is None:
                    return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            # 4. Core DB operation: bill + bill_unit_details allocations for the account/billing_period.
            # An account can have more than one bill in the same billing_period (separate invoices,
            # each independently approved) -- every one of them re-allocates the same units per the
            # account's fixed mapping, so this must GROUP BY unit rather than return one row per
            # bill, or the same unit appears once per bill instead of once per period.
            where_sql = "WHERE bill.account_id = %s AND bill.billing_period = %s"
            values = [uuid_account_id, date_billing_period]
            join_sql = """
                FROM bill_unit_details
                JOIN bill ON bill_unit_details.bill_id = bill.bill_id
                JOIN unit_master ON bill_unit_details.unit_id = unit_master.unit_id
            """
            group_by_sql = (
                "GROUP BY bill_unit_details.unit_id, unit_master.unit_name, "
                "bill.billing_period, bill_unit_details.allocated_percentage"
            )

            cursor.execute(
                f"""
                SELECT COUNT(*) AS total_count FROM (
                    SELECT 1 {join_sql} {where_sql} {group_by_sql}
                ) AS grouped_rows
                """,
                tuple(values),
            )
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"""
                SELECT bill_unit_details.unit_id AS unit_id,
                       unit_master.unit_name AS unit_name,
                       bill.billing_period AS billing_period,
                       bill_unit_details.allocated_percentage AS allocated_percentage,
                       SUM(bill_unit_details.allocated_amount) AS allocated_amount,
                       COUNT(*) AS bill_count
                {join_sql}
                {where_sql}
                {group_by_sql}
                ORDER BY {str_sort_by} {str_sort_order}
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


def f_get_bill_bifurcation_analytics_report(
    uuid_caller_user_id,
    str_caller_role,
    uuid_unit_id,
    uuid_account_id,
    str_utility_category_name,
    date_from_date,
    date_to_date,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        if uuid_unit_id and not _is_valid_uuid(uuid_unit_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_account_id and not _is_valid_uuid(uuid_account_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        try:
            date_from_date = _parse_date(date_from_date)
            date_to_date = _parse_date(date_to_date)
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no, int_page_size = _resolve_pagination(int_page_no, int_page_size)
        if int_page_no is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by, str_sort_order = _resolve_sort(
            str_sort_by,
            str_sort_order,
            {"billing_period", "account_id", "unit_id", "utility_category_name", "total_allocated_amount"},
            "billing_period",
            "DESC",
        )
        if str_sort_by is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role not in ("Leadership", "Administrator"):
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: none required beyond caller's own role
            # 4. Core DB operation: dynamic WHERE + cross-tab aggregation
            where_clauses = []
            values = []
            if uuid_unit_id:
                where_clauses.append("bill_unit_details.unit_id = %s")
                values.append(uuid_unit_id)
            if uuid_account_id:
                where_clauses.append("bill.account_id = %s")
                values.append(uuid_account_id)
            if str_utility_category_name:
                where_clauses.append("account_master.utility_category_name = %s")
                values.append(str_utility_category_name)
            if date_from_date:
                where_clauses.append("bill.billing_period >= %s")
                values.append(date_from_date)
            if date_to_date:
                where_clauses.append("bill.billing_period <= %s")
                values.append(date_to_date)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            join_sql = """
                FROM bill_unit_details
                JOIN bill ON bill_unit_details.bill_id = bill.bill_id
                JOIN account_master ON bill.account_id = account_master.account_id
                JOIN unit_master ON bill_unit_details.unit_id = unit_master.unit_id
            """
            group_by_sql = """
                GROUP BY bill.account_id, account_master.account_number, account_master.account_name,
                         bill_unit_details.unit_id, unit_master.unit_code, unit_master.unit_name,
                         account_master.utility_category_name, bill.billing_period
            """

            cursor.execute(
                f"""
                SELECT COUNT(*) AS total_count FROM (
                    SELECT 1 {join_sql} {where_sql} {group_by_sql}
                ) AS grouped_rows
                """,
                tuple(values),
            )
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"""
                SELECT bill.account_id AS account_id,
                       account_master.account_number AS account_number,
                       account_master.account_name AS account_name,
                       bill_unit_details.unit_id AS unit_id,
                       unit_master.unit_code AS unit_code,
                       unit_master.unit_name AS unit_name,
                       account_master.utility_category_name AS utility_category_name,
                       bill.billing_period AS billing_period,
                       SUM(bill_unit_details.allocated_amount) AS total_allocated_amount,
                       COUNT(*) AS bill_count
                {join_sql}
                {where_sql}
                {group_by_sql}
                ORDER BY {str_sort_by} {str_sort_order}
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


def f_get_monthly_bill_report(
    uuid_caller_user_id,
    str_caller_role,
    uuid_unit_id,
    date_billing_period,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        if uuid_unit_id and not _is_valid_uuid(uuid_unit_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        try:
            date_billing_period = _parse_date(date_billing_period)
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no, int_page_size = _resolve_pagination(int_page_no, int_page_size)
        if int_page_no is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by, str_sort_order = _resolve_sort(
            str_sort_by, str_sort_order, {"billing_period", "unit_id", "total_amount"}, "billing_period", "DESC"
        )
        if str_sort_by is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role not in ("Administrator", "Leadership"):
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: none required beyond caller's own role
            # 4. Core DB operation: dynamic WHERE + per-unit/billing_period aggregation
            where_clauses = []
            values = []
            if uuid_unit_id:
                where_clauses.append("bill_unit_details.unit_id = %s")
                values.append(uuid_unit_id)
            if date_billing_period:
                where_clauses.append("bill.billing_period = %s")
                values.append(date_billing_period)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            join_sql = """
                FROM bill_unit_details
                JOIN bill ON bill_unit_details.bill_id = bill.bill_id
                JOIN unit_master ON bill_unit_details.unit_id = unit_master.unit_id
            """
            group_by_sql = (
                "GROUP BY bill_unit_details.unit_id, unit_master.unit_code, unit_master.unit_name, bill.billing_period"
            )

            cursor.execute(
                f"""
                SELECT COUNT(*) AS total_count FROM (
                    SELECT 1 {join_sql} {where_sql} {group_by_sql}
                ) AS grouped_rows
                """,
                tuple(values),
            )
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"""
                SELECT bill_unit_details.unit_id AS unit_id,
                       unit_master.unit_code AS unit_code,
                       unit_master.unit_name AS unit_name,
                       bill.billing_period AS billing_period,
                       ROUND(SUM(bill_unit_details.allocated_amount)::numeric, 2) AS total_amount
                {join_sql}
                {where_sql}
                {group_by_sql}
                ORDER BY {str_sort_by} {str_sort_order}
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


def f_get_bill_status_report(
    uuid_caller_user_id,
    str_caller_role,
    uuid_account_id,
    uuid_unit_id,
    str_bill_status,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        if uuid_account_id and not _is_valid_uuid(uuid_account_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_unit_id and not _is_valid_uuid(uuid_unit_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_bill_status and str_bill_status not in VALID_BILL_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no, int_page_size = _resolve_pagination(int_page_no, int_page_size)
        if int_page_no is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by, str_sort_order = _resolve_sort(
            str_sort_by,
            str_sort_order,
            {"bill_date", "account_id", "unit_id", "bill_status", "billing_period"},
            "bill_date",
            "DESC",
        )
        if str_sort_by is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: none required beyond caller's own role
            # 4. Core DB operation: dynamic WHERE against bill + bill_unit_details
            where_clauses = []
            values = []
            if uuid_account_id:
                where_clauses.append("bill.account_id = %s")
                values.append(uuid_account_id)
            if uuid_unit_id:
                where_clauses.append("bill_unit_details.unit_id = %s")
                values.append(uuid_unit_id)
            if str_bill_status:
                where_clauses.append("bill.bill_status = %s")
                values.append(str_bill_status)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            join_sql = """
                FROM bill_unit_details
                JOIN bill ON bill_unit_details.bill_id = bill.bill_id
            """

            cursor.execute(f"SELECT COUNT(*) AS total_count {join_sql} {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"""
                SELECT bill.account_id AS account_id,
                       bill_unit_details.unit_id AS unit_id,
                       bill.bill_date AS bill_date,
                       bill.billing_period AS billing_period,
                       bill.bill_status AS bill_status,
                       bill_unit_details.allocated_amount AS allocated_amount
                {join_sql}
                {where_sql}
                ORDER BY {str_sort_by} {str_sort_order}
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


def f_get_leadership_operational_report(
    uuid_caller_user_id,
    str_caller_role,
    uuid_account_id,
    uuid_unit_id,
    str_utility_category_name,
    date_from_date,
    date_to_date,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        if uuid_account_id and not _is_valid_uuid(uuid_account_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_unit_id and not _is_valid_uuid(uuid_unit_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        try:
            date_from_date = _parse_date(date_from_date)
            date_to_date = _parse_date(date_to_date)
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no, int_page_size = _resolve_pagination(int_page_no, int_page_size)
        if int_page_no is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by, str_sort_order = _resolve_sort(
            str_sort_by,
            str_sort_order,
            {"billing_period", "account_id", "unit_id", "utility_category_name", "total_allocated_amount"},
            "billing_period",
            "DESC",
        )
        if str_sort_by is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Leadership":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: none required beyond caller's own role
            # 4. Core DB operation: dynamic WHERE against the confirmed subset of bill/bill_unit_details fields
            where_clauses = []
            values = []
            if uuid_account_id:
                where_clauses.append("bill.account_id = %s")
                values.append(uuid_account_id)
            if uuid_unit_id:
                where_clauses.append("bill_unit_details.unit_id = %s")
                values.append(uuid_unit_id)
            if str_utility_category_name:
                where_clauses.append("account_master.utility_category_name = %s")
                values.append(str_utility_category_name)
            if date_from_date:
                where_clauses.append("bill.billing_period >= %s")
                values.append(date_from_date)
            if date_to_date:
                where_clauses.append("bill.billing_period <= %s")
                values.append(date_to_date)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            join_sql = """
                FROM bill_unit_details
                JOIN bill ON bill_unit_details.bill_id = bill.bill_id
                JOIN account_master ON bill.account_id = account_master.account_id
                JOIN unit_master ON bill_unit_details.unit_id = unit_master.unit_id
            """
            group_by_sql = """
                GROUP BY bill.account_id, account_master.account_number, account_master.account_name,
                         bill_unit_details.unit_id, unit_master.unit_code, unit_master.unit_name,
                         account_master.utility_category_name, bill.billing_period
            """

            cursor.execute(
                f"""
                SELECT COUNT(*) AS total_count FROM (
                    SELECT 1 {join_sql} {where_sql} {group_by_sql}
                ) AS grouped_rows
                """,
                tuple(values),
            )
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"""
                SELECT bill.account_id AS account_id,
                       account_master.account_number AS account_number,
                       account_master.account_name AS account_name,
                       bill_unit_details.unit_id AS unit_id,
                       unit_master.unit_code AS unit_code,
                       unit_master.unit_name AS unit_name,
                       account_master.utility_category_name AS utility_category_name,
                       bill.billing_period AS billing_period,
                       SUM(bill_unit_details.allocated_amount) AS total_allocated_amount,
                       COUNT(*) AS bill_count
                {join_sql}
                {where_sql}
                {group_by_sql}
                ORDER BY {str_sort_by} {str_sort_order}
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
