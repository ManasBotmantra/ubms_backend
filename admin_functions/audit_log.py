import uuid
from datetime import datetime

from common_functions.db import get_db_cursor
from common_functions import config

VALID_SORT_FIELDS = {"trail_id", "api_name", "caller_user_id", "status_code", "logged_on"}
VALID_SORT_ORDERS = {"ASC", "DESC"}


def _parse_date(date_value):
    if isinstance(date_value, datetime):
        return date_value.date()
    return datetime.strptime(str(date_value), "%Y-%m-%d").date()


def f_filter_audit_log(
    uuid_caller_user_id,
    str_caller_role,
    uuid_trail_id,
    str_api_name,
    uuid_target_caller_user_id,
    int_status_code,
    date_logged_on_from,
    date_logged_on_to,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        if uuid_trail_id:
            try:
                uuid.UUID(str(uuid_trail_id))
            except ValueError:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_api_name and len(str_api_name) > 100:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_target_caller_user_id:
            try:
                uuid.UUID(str(uuid_target_caller_user_id))
            except ValueError:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if int_status_code is not None and not isinstance(int_status_code, int):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        if date_logged_on_from:
            try:
                date_logged_on_from = _parse_date(date_logged_on_from)
            except ValueError:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if date_logged_on_to:
            try:
                date_logged_on_to = _parse_date(date_logged_on_to)
            except ValueError:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no = 1 if int_page_no is None else int_page_no
        int_page_size = 20 if int_page_size is None else int_page_size
        if not isinstance(int_page_no, int) or int_page_no < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(int_page_size, int) or int_page_size < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by = str_sort_by or "logged_on"
        if str_sort_by not in VALID_SORT_FIELDS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        str_sort_order = (str_sort_order or "DESC").upper()
        if str_sort_order not in VALID_SORT_ORDERS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: none required beyond caller's own role
            # 4. Core DB operation: dynamic WHERE clause + pagination + sort
            where_clauses = []
            values = []
            if uuid_trail_id:
                where_clauses.append("trail_id = %s")
                values.append(uuid_trail_id)
            if str_api_name:
                where_clauses.append("api_name = %s")
                values.append(str_api_name)
            if uuid_target_caller_user_id:
                where_clauses.append("caller_user_id = %s")
                values.append(uuid_target_caller_user_id)
            if int_status_code is not None:
                where_clauses.append("status_code = %s")
                values.append(int_status_code)
            if date_logged_on_from:
                where_clauses.append("logged_on::date >= %s")
                values.append(date_logged_on_from)
            if date_logged_on_to:
                where_clauses.append("logged_on::date <= %s")
                values.append(date_logged_on_to)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM audit_log {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"SELECT * FROM audit_log {where_sql} ORDER BY {str_sort_by} {str_sort_order} LIMIT %s OFFSET %s",
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
