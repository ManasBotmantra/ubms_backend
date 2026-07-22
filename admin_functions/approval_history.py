import uuid
from datetime import date, datetime, timedelta, timezone

from dateutil import parser as date_parser

from common_functions.db import get_db_cursor
from common_functions import config

VALID_HISTORY_STATUSES = ("Approved", "Rejected", "Resolved")
SORTABLE_HISTORY_COLUMNS = {
    "history_id",
    "allocation_id",
    "history_status",
    "performed_by",
    "performed_on",
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


def f_resolve_approval_history(
    uuid_caller_user_id, str_caller_role, uuid_allocation_id, str_resolution_action, str_reason
):
    try:
        # 1. Validate input
        if not uuid_allocation_id or not _is_valid_uuid(uuid_allocation_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not str_resolution_action or not str_resolution_action.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup
            cursor.execute(
                "SELECT allocation_id, bill_id FROM bill_unit_details WHERE allocation_id = %s",
                (uuid_allocation_id,),
            )
            allocation_row = cursor.fetchone()
            if not allocation_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            cursor.execute(
                """
                SELECT history_status FROM approval_history_log
                WHERE allocation_id = %s
                ORDER BY performed_on DESC
                LIMIT 1
                """,
                (uuid_allocation_id,),
            )
            latest = cursor.fetchone()
            if not latest or latest["history_status"] != "Rejected":
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, config.ERR_NOTHING_TO_RESOLVE, None

            # 4. Core DB operation: insert approval_history_log row (BR-004: final
            # payment responsibility resolved so the bill is never left indefinitely on hold)
            history_id = str(uuid.uuid4())
            performed_on = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO approval_history_log
                    (history_id, allocation_id, history_status, resolution_action, reason,
                     performed_by, performed_on, created_on)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    history_id,
                    uuid_allocation_id,
                    "Resolved",
                    str_resolution_action,
                    str_reason,
                    uuid_caller_user_id,
                    performed_on,
                    performed_on,
                ),
            )

            # 5. Re-evaluate bill_status: once every allocation for the bill is Resolved/Approved
            uuid_bill_id = allocation_row["bill_id"]
            cursor.execute(
                """
                SELECT bud.allocation_id,
                    (SELECT ahl.history_status FROM approval_history_log ahl
                     WHERE ahl.allocation_id = bud.allocation_id
                     ORDER BY ahl.performed_on DESC LIMIT 1) AS current_status
                FROM bill_unit_details bud
                WHERE bud.bill_id = %s
                """,
                (uuid_bill_id,),
            )
            all_rows = cursor.fetchall()
            if all_rows and all(row["current_status"] in ("Approved", "Resolved") for row in all_rows):
                final_bill_status = (
                    "Resolved" if any(row["current_status"] == "Resolved" for row in all_rows) else "Approved"
                )
                cursor.execute(
                    "UPDATE bill SET bill_status = %s, updated_by = %s, updated_on = %s WHERE bill_id = %s",
                    (final_bill_status, uuid_caller_user_id, performed_on, uuid_bill_id),
                )

        # 6. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"allocation_id": uuid_allocation_id, "history_status": "Resolved"}

    except Exception as ex:
        # 7. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_filter_approval_history(
    uuid_caller_user_id,
    str_caller_role,
    uuid_history_id,
    uuid_allocation_id,
    str_history_status,
    uuid_performed_by,
    date_performed_on_from,
    date_performed_on_to,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        for value in (uuid_history_id, uuid_allocation_id, uuid_performed_by):
            if value and not _is_valid_uuid(value):
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_history_status and str_history_status not in VALID_HISTORY_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        parsed_from = None
        parsed_to = None
        if date_performed_on_from:
            parsed_from = _parse_date(date_performed_on_from)
            if parsed_from is None:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if date_performed_on_to:
            parsed_to = _parse_date(date_performed_on_to)
            if parsed_to is None:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no = 1 if int_page_no is None else int_page_no
        int_page_size = 20 if int_page_size is None else int_page_size
        if not isinstance(int_page_no, int) or int_page_no < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(int_page_size, int) or int_page_size < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by = str_sort_by or "performed_on"
        if str_sort_by not in SORTABLE_HISTORY_COLUMNS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        str_sort_order = (str_sort_order or "DESC").upper()
        if str_sort_order not in ("ASC", "DESC"):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role not in ("Administrator", "Unit Accountant"):
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

            # 4. Core DB operation: dynamic WHERE clause + pagination
            where_clauses = []
            values = []
            if uuid_history_id:
                where_clauses.append("ahl.history_id = %s")
                values.append(uuid_history_id)
            if uuid_allocation_id:
                where_clauses.append("ahl.allocation_id = %s")
                values.append(uuid_allocation_id)
            if str_history_status:
                where_clauses.append("ahl.history_status = %s")
                values.append(str_history_status)
            if uuid_performed_by:
                where_clauses.append("ahl.performed_by = %s")
                values.append(uuid_performed_by)
            if parsed_from:
                where_clauses.append("ahl.performed_on >= %s")
                values.append(datetime.combine(parsed_from, datetime.min.time()))
            if parsed_to:
                where_clauses.append("ahl.performed_on < %s")
                values.append(datetime.combine(parsed_to + timedelta(days=1), datetime.min.time()))
            if assigned_unit_ids is not None:
                where_clauses.append(
                    # Cast to uuid[] -- psycopg2 sends the id list as text[], and
                    # `uuid = ANY(text[])` has no operator; the cast also makes an empty list valid.
                    "EXISTS (SELECT 1 FROM bill_unit_details bud "
                    "WHERE bud.allocation_id = ahl.allocation_id AND bud.unit_id = ANY(%s::uuid[]))"
                )
                values.append(assigned_unit_ids)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM approval_history_log ahl {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            # Enrich each history row with unit/bill/account context and the performer's name so
            # the approval-history view is readable without Administrator-only lookups.
            cursor.execute(
                f"""
                SELECT ahl.*,
                    bud.unit_id AS unit_id,
                    um.unit_code AS unit_code,
                    um.unit_name AS unit_name,
                    b.bill_id AS bill_id,
                    b.billing_period AS billing_period,
                    am.account_number AS account_number,
                    am.account_name AS account_name,
                    perf.full_name AS performed_by_name
                FROM approval_history_log ahl
                JOIN bill_unit_details bud ON bud.allocation_id = ahl.allocation_id
                JOIN bill b ON bud.bill_id = b.bill_id
                JOIN account_master am ON b.account_id = am.account_id
                JOIN unit_master um ON bud.unit_id = um.unit_id
                LEFT JOIN user_master perf ON perf.user_id = ahl.performed_by
                {where_sql}
                ORDER BY ahl.{str_sort_by} {str_sort_order}
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
