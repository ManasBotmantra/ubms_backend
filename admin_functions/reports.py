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


def f_filter_bills_by_unit_category(
    uuid_caller_user_id,
    str_caller_role,
    date_from_date,
    date_to_date,
    str_unit_name,
    str_category,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    """
    Filters bill records based on from_date, to_date, unit_name, and category.
    Returns paginated bill records joined with unit_master and account_master details.
    """
    try:
        # 1. Parse & validate dates and pagination
        try:
            parsed_from_date = _parse_date(date_from_date) if date_from_date else None
            parsed_to_date = _parse_date(date_to_date) if date_to_date else None
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no, int_page_size = _resolve_pagination(int_page_no, int_page_size)
        if int_page_no is None:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        allowed_sort_columns = {
            "bill_date",
            "billing_period",
            "due_date",
            "total_amount",
            "allocated_amount",
            "unit_name",
            "utility_category_name",
            "category",
            "created_on",
        }
        sort_column_map = {
            "category": "account_master.utility_category_name",
            "utility_category_name": "account_master.utility_category_name",
            "unit_name": "unit_master.unit_name",
            "bill_date": "bill.bill_date",
            "billing_period": "bill.billing_period",
            "due_date": "bill.due_date",
            "total_amount": "bill.total_amount",
            "allocated_amount": "bill_unit_details.allocated_amount",
            "created_on": "bill.created_on",
        }

        if str_sort_by and str_sort_by not in allowed_sort_columns:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        sql_sort_by = sort_column_map.get(str_sort_by or "bill_date", "bill.bill_date")
        str_sort_order = (str_sort_order or "DESC").upper()
        if str_sort_order not in ("ASC", "DESC"):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role not in ("Administrator", "Leadership", "Unit Accountant"):
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: scope Unit Accountant to assigned units
            assigned_unit_ids = None
            if str_caller_role == "Unit Accountant":
                cursor.execute(
                    "SELECT unit_id FROM user_unit_details WHERE user_id = %s AND assignment_status = 'Active'",
                    (uuid_caller_user_id,),
                )
                assigned_unit_ids = [row["unit_id"] for row in cursor.fetchall()]
                if not assigned_unit_ids:
                    return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            # 4. Core DB operation: dynamic WHERE clause + pagination
            where_clauses = []
            values = []

            if parsed_from_date:
                where_clauses.append("COALESCE(bill.bill_date, bill.billing_period) >= %s")
                values.append(parsed_from_date)

            if parsed_to_date:
                where_clauses.append("COALESCE(bill.bill_date, bill.billing_period) <= %s")
                values.append(parsed_to_date)

            # Handle unit identifier (unit_name, unit_code, or unit_id - accepts string or list of strings)
            unit_name_list = []
            if isinstance(str_unit_name, (list, tuple, set)):
                unit_name_list = [str(x).strip() for x in str_unit_name if x and str(x).strip()]
            elif str_unit_name and str(str_unit_name).strip():
                unit_name_list = [str(str_unit_name).strip()]

            if unit_name_list:
                unit_or_clauses = [
                    "(unit_master.unit_name ILIKE %s OR unit_master.unit_code ILIKE %s OR CAST(unit_master.unit_id AS TEXT) ILIKE %s)"
                    for _ in unit_name_list
                ]
                where_clauses.append(f"({' OR '.join(unit_or_clauses)})")
                for u in unit_name_list:
                    val = f"%{u}%"
                    values.extend([val, val, val])

            # Handle category (accepts string or list of strings)
            category_list = []
            if isinstance(str_category, (list, tuple, set)):
                category_list = [str(x).strip() for x in str_category if x and str(x).strip()]
            elif str_category and str(str_category).strip():
                category_list = [str(str_category).strip()]

            if category_list:
                cat_or_clauses = ["account_master.utility_category_name ILIKE %s" for _ in category_list]
                where_clauses.append(f"({' OR '.join(cat_or_clauses)})")
                values.extend([f"%{c}%" for c in category_list])

            if assigned_unit_ids is not None:
                where_clauses.append("bill_unit_details.unit_id = ANY(%s)")
                values.append(assigned_unit_ids)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            join_sql = """
                FROM bill
                LEFT JOIN bill_unit_details ON bill.bill_id = bill_unit_details.bill_id
                LEFT JOIN unit_master ON bill_unit_details.unit_id = unit_master.unit_id
                LEFT JOIN account_master ON bill.account_id = account_master.account_id
            """

            cursor.execute(f"SELECT COUNT(*) AS total_count {join_sql} {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"""
                SELECT
                    bill.bill_id,
                    bill.bill_date,
                    bill.billing_period,
                    bill.due_date,
                    bill.bill_status,
                    bill.total_amount,
                    bill.currency,
                    bill_unit_details.allocation_id,
                    bill_unit_details.allocated_percentage,
                    bill_unit_details.allocated_amount,
                    unit_master.unit_id,
                    unit_master.unit_code,
                    unit_master.unit_name,
                    account_master.account_id,
                    account_master.account_number,
                    account_master.account_name,
                    account_master.utility_category_name AS category,
                    account_master.provider_name
                {join_sql}
                {where_sql}
                ORDER BY {sql_sort_by} {str_sort_order}
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


def f_get_unit_and_category_master(
    uuid_caller_user_id,
    str_caller_role,
    str_unit_name=None,
    str_category=None,
):
    """
    Returns active units and utility categories master data.
    Supports filtering by unit_name (list/string) and category (list/string).
    If empty/omitted, returns all active units and utility categories.
    """
    try:
        # 1. RBAC check
        if str_caller_role not in ("Administrator", "Leadership", "Unit Accountant"):
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 2. Scope Unit Accountant to assigned units
            assigned_unit_ids = None
            if str_caller_role == "Unit Accountant":
                cursor.execute(
                    "SELECT unit_id FROM user_unit_details WHERE user_id = %s AND assignment_status = 'Active'",
                    (uuid_caller_user_id,),
                )
                assigned_unit_ids = [row["unit_id"] for row in cursor.fetchall()]
                if not assigned_unit_ids:
                    return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {
                        "units": [],
                        "categories": [],
                        "total_units": 0,
                        "total_categories": 0,
                    }

            # 3. Query Units
            unit_where_clauses = ["unit_status = 'Active'"]
            unit_values = []

            if assigned_unit_ids is not None:
                unit_where_clauses.append("unit_id = ANY(%s)")
                unit_values.append(assigned_unit_ids)

            unit_name_list = []
            if isinstance(str_unit_name, (list, tuple, set)):
                unit_name_list = [str(x).strip() for x in str_unit_name if x and str(x).strip()]
            elif str_unit_name and str(str_unit_name).strip():
                unit_name_list = [str(str_unit_name).strip()]

            if unit_name_list:
                unit_or_clauses = [
                    "(unit_name ILIKE %s OR unit_code ILIKE %s OR CAST(unit_id AS TEXT) ILIKE %s)"
                    for _ in unit_name_list
                ]
                unit_where_clauses.append(f"({' OR '.join(unit_or_clauses)})")
                for u in unit_name_list:
                    val = f"%{u}%"
                    unit_values.extend([val, val, val])

            unit_where_sql = f"WHERE {' AND '.join(unit_where_clauses)}"
            cursor.execute(
                f"""
                SELECT unit_id, unit_code, unit_name, address, unit_status
                FROM unit_master
                {unit_where_sql}
                ORDER BY unit_name ASC
                """,
                tuple(unit_values),
            )
            units = cursor.fetchall()

            # 4. Query Utility Categories
            cat_where_clauses = ["account_status = 'Active'", "utility_category_name IS NOT NULL"]
            cat_values = []

            category_list = []
            if isinstance(str_category, (list, tuple, set)):
                category_list = [str(x).strip() for x in str_category if x and str(x).strip()]
            elif str_category and str(str_category).strip():
                category_list = [str(str_category).strip()]

            if category_list:
                cat_or_clauses = ["utility_category_name ILIKE %s" for _ in category_list]
                cat_where_clauses.append(f"({' OR '.join(cat_or_clauses)})")
                cat_values.extend([f"%{c}%" for c in category_list])

            cat_where_sql = f"WHERE {' AND '.join(cat_where_clauses)}"
            cursor.execute(
                f"""
                SELECT DISTINCT utility_category_name AS category
                FROM account_master
                {cat_where_sql}
                ORDER BY category ASC
                """,
                tuple(cat_values),
            )
            categories = [row["category"] for row in cursor.fetchall()]

        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {
                "units": units,
                "categories": categories,
                "total_units": len(units),
                "total_categories": len(categories),
            },
        )

    except Exception as ex:
        # 5. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_get_unit_master_details(
    uuid_caller_user_id,
    str_caller_role,
    str_unit_name=None,
):
    """
    Returns active unit master records.
    Sending no filter returns all active units.
    Sending str_unit_name filters by unit_name, unit_code, or unit_id (partial match).
    Unit Accountants are scoped to their assigned units.
    """
    try:
        # 1. RBAC check
        if str_caller_role not in ("Administrator", "Leadership", "Unit Accountant"):
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 2. Scope Unit Accountant to assigned units
            assigned_unit_ids = None
            if str_caller_role == "Unit Accountant":
                cursor.execute(
                    "SELECT unit_id FROM user_unit_details WHERE user_id = %s AND assignment_status = 'Active'",
                    (uuid_caller_user_id,),
                )
                assigned_unit_ids = [row["unit_id"] for row in cursor.fetchall()]
                if not assigned_unit_ids:
                    return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"units": [], "total_units": 0}

            # 3. Build WHERE clause
            where_clauses = ["unit_status = 'Active'"]
            values = []

            if assigned_unit_ids is not None:
                where_clauses.append("unit_id = ANY(%s)")
                values.append(assigned_unit_ids)

            unit_name_list = []
            if isinstance(str_unit_name, (list, tuple, set)):
                unit_name_list = [str(x).strip() for x in str_unit_name if x and str(x).strip()]
            elif str_unit_name and str(str_unit_name).strip():
                unit_name_list = [str(str_unit_name).strip()]

            if unit_name_list:
                or_clauses = [
                    "(unit_name ILIKE %s OR unit_code ILIKE %s OR CAST(unit_id AS TEXT) ILIKE %s)"
                    for _ in unit_name_list
                ]
                where_clauses.append(f"({' OR '.join(or_clauses)})")
                for u in unit_name_list:
                    val = f"%{u}%"
                    values.extend([val, val, val])

            where_sql = f"WHERE {' AND '.join(where_clauses)}"
            cursor.execute(
                f"""
                SELECT unit_id, unit_code, unit_name, address, unit_status
                FROM unit_master
                {where_sql}
                ORDER BY unit_name ASC
                """,
                tuple(values),
            )
            units = cursor.fetchall()

        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"units": units, "total_units": len(units)},
        )

    except Exception as ex:
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_get_category_master_details(
    uuid_caller_user_id,
    str_caller_role,
    str_category=None,
):
    """
    Returns distinct active utility category names from account_master.
    Sending no filter returns all active categories.
    Sending str_category filters by utility_category_name (partial match).
    """
    try:
        # 1. RBAC check
        if str_caller_role not in ("Administrator", "Leadership", "Unit Accountant"):
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 2. Build WHERE clause
            where_clauses = ["account_status = 'Active'", "utility_category_name IS NOT NULL"]
            values = []

            category_list = []
            if isinstance(str_category, (list, tuple, set)):
                category_list = [str(x).strip() for x in str_category if x and str(x).strip()]
            elif str_category and str(str_category).strip():
                category_list = [str(str_category).strip()]

            if category_list:
                or_clauses = ["utility_category_name ILIKE %s" for _ in category_list]
                where_clauses.append(f"({' OR '.join(or_clauses)})")
                values.extend([f"%{c}%" for c in category_list])

            where_sql = f"WHERE {' AND '.join(where_clauses)}"
            cursor.execute(
                f"""
                SELECT DISTINCT utility_category_name AS category
                FROM account_master
                {where_sql}
                ORDER BY category ASC
                """,
                tuple(values),
            )
            categories = [row["category"] for row in cursor.fetchall()]

        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"categories": categories, "total_categories": len(categories)},
        )

    except Exception as ex:
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
