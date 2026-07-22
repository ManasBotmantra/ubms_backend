import uuid
from datetime import datetime, timezone

from common_functions.db import get_db_cursor
from common_functions import config

SORTABLE_ALLOCATION_COLUMNS = {
    "allocation_id",
    "bill_id",
    "unit_id",
    "mapping_id",
    "allocated_percentage",
    "allocated_amount",
    "created_on",
}
# "Rejected" is terminal for approve: once rejected, only an Administrator can move an
# allocation forward again via resolve_approval_history (BR-004) -- the same Unit Accountant
# who rejected it must not be able to silently self-approve it back to normal.
TERMINAL_STATUSES_FOR_APPROVE = ("Approved", "Rejected", "Resolved")
TERMINAL_STATUSES_FOR_REJECT = ("Approved", "Rejected", "Resolved")


def _is_valid_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _get_latest_history_status(cursor, uuid_allocation_id):
    cursor.execute(
        """
        SELECT history_status FROM approval_history_log
        WHERE allocation_id = %s
        ORDER BY performed_on DESC
        LIMIT 1
        """,
        (uuid_allocation_id,),
    )
    row = cursor.fetchone()
    return row["history_status"] if row else None


def _reevaluate_bill_status_for_approve(cursor, uuid_bill_id, uuid_caller_user_id, performed_on):
    """FR-APR-003: if every allocation for the parent bill is now most-recently Approved,
    mark the bill Approved."""
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
    if all_rows and all(row["current_status"] == "Approved" for row in all_rows):
        cursor.execute(
            "UPDATE bill SET bill_status = %s, updated_by = %s, updated_on = %s WHERE bill_id = %s",
            ("Approved", uuid_caller_user_id, performed_on, uuid_bill_id),
        )


def f_filter_bill_unit_details(
    uuid_caller_user_id,
    str_caller_role,
    uuid_allocation_id,
    uuid_bill_id,
    uuid_unit_id,
    uuid_mapping_id,
    uuid_account_id,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        for value in (uuid_allocation_id, uuid_bill_id, uuid_unit_id, uuid_mapping_id, uuid_account_id):
            if value and not _is_valid_uuid(value):
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no = 1 if int_page_no is None else int_page_no
        int_page_size = 20 if int_page_size is None else int_page_size
        if not isinstance(int_page_no, int) or int_page_no < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(int_page_size, int) or int_page_size < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by = str_sort_by or "created_on"
        if str_sort_by not in SORTABLE_ALLOCATION_COLUMNS:
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

            # 4. Core DB operation: dynamic WHERE clause + pagination; resolve each record's
            # current status from the most recent approval_history_log row for that allocation_id
            where_clauses = []
            values = []
            if uuid_allocation_id:
                where_clauses.append("bud.allocation_id = %s")
                values.append(uuid_allocation_id)
            if uuid_bill_id:
                where_clauses.append("bud.bill_id = %s")
                values.append(uuid_bill_id)
            if uuid_unit_id:
                where_clauses.append("bud.unit_id = %s")
                values.append(uuid_unit_id)
            if uuid_mapping_id:
                where_clauses.append("bud.mapping_id = %s")
                values.append(uuid_mapping_id)
            if uuid_account_id:
                # bill_unit_details has no account_id column -- resolve it through the
                # mapping_id link to account_unit_details instead of a direct column match.
                where_clauses.append(
                    "bud.mapping_id IN (SELECT mapping_id FROM account_unit_details WHERE account_id = %s)"
                )
                values.append(uuid_account_id)
            if assigned_unit_ids is not None:
                # Cast to uuid[]: psycopg2 sends the id list as a text[] (uuids come back as str),
                # and `uuid = ANY(text[])` has no operator. The cast also makes an empty list a
                # valid empty uuid[] -- an unassigned Unit Accountant matches nothing, not an error.
                where_clauses.append("bud.unit_id = ANY(%s::uuid[])")
                values.append(assigned_unit_ids)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM bill_unit_details bud {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            # Enrich each allocation with its unit/bill/account display fields so callers
            # (notably Unit Accountants, who cannot call the Administrator-only unit/account
            # lookups) can render human-readable context without extra requests.
            cursor.execute(
                f"""
                SELECT bud.*,
                    um.unit_code AS unit_code,
                    um.unit_name AS unit_name,
                    b.billing_period AS billing_period,
                    b.bill_date AS bill_date,
                    b.bill_status AS bill_status,
                    b.total_amount AS bill_total_amount,
                    b.currency AS currency,
                    b.account_id AS account_id,
                    am.account_number AS account_number,
                    am.account_name AS account_name,
                    am.utility_category_name AS utility_category_name,
                    (SELECT ahl.history_status FROM approval_history_log ahl
                     WHERE ahl.allocation_id = bud.allocation_id
                     ORDER BY ahl.performed_on DESC LIMIT 1) AS current_status
                FROM bill_unit_details bud
                JOIN bill b ON bud.bill_id = b.bill_id
                JOIN account_master am ON b.account_id = am.account_id
                JOIN unit_master um ON bud.unit_id = um.unit_id
                {where_sql}
                ORDER BY bud.{str_sort_by} {str_sort_order}
                LIMIT %s OFFSET %s
                """,
                tuple(values) + (int_page_size, offset),
            )
            records = cursor.fetchall()

        # 5. No data found
        if not records:
            return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

        for record in records:
            if not record.get("current_status"):
                record["current_status"] = "Pending Approval"

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


def f_approve_bill_unit_details(uuid_caller_user_id, str_caller_role, uuid_allocation_id):
    try:
        # 1. Validate input
        if not uuid_allocation_id or not _is_valid_uuid(uuid_allocation_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Unit Accountant":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookups
            cursor.execute(
                "SELECT allocation_id, unit_id, bill_id FROM bill_unit_details WHERE allocation_id = %s",
                (uuid_allocation_id,),
            )
            allocation_row = cursor.fetchone()
            if not allocation_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            cursor.execute(
                """
                SELECT 1 FROM user_unit_details
                WHERE user_id = %s AND unit_id = %s AND assignment_status = 'Active'
                """,
                (uuid_caller_user_id, allocation_row["unit_id"]),
            )
            if not cursor.fetchone():
                return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_UNIT_NOT_ASSIGNED_TO_CALLER, None

            # 4. Core DB operation: terminal-state check + insert approval_history_log row
            latest_status = _get_latest_history_status(cursor, uuid_allocation_id)
            if latest_status in TERMINAL_STATUSES_FOR_APPROVE:
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, config.ERR_ALLOCATION_TERMINAL_STATE, None

            history_id = str(uuid.uuid4())
            performed_on = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO approval_history_log
                    (history_id, allocation_id, history_status, performed_by, performed_on, created_on)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (history_id, uuid_allocation_id, "Approved", uuid_caller_user_id, performed_on, performed_on),
            )

            # 5. Re-evaluate bill_status (FR-APR-003)
            _reevaluate_bill_status_for_approve(cursor, allocation_row["bill_id"], uuid_caller_user_id, performed_on)

        # 6. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"allocation_id": uuid_allocation_id, "history_status": "Approved"}

    except Exception as ex:
        # 7. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_reject_bill_unit_details(uuid_caller_user_id, str_caller_role, uuid_allocation_id, str_reason):
    try:
        # 1. Validate input
        if not uuid_allocation_id or not _is_valid_uuid(uuid_allocation_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not str_reason or not str_reason.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Unit Accountant":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookups
            cursor.execute(
                "SELECT allocation_id, unit_id, bill_id FROM bill_unit_details WHERE allocation_id = %s",
                (uuid_allocation_id,),
            )
            allocation_row = cursor.fetchone()
            if not allocation_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            cursor.execute(
                """
                SELECT 1 FROM user_unit_details
                WHERE user_id = %s AND unit_id = %s AND assignment_status = 'Active'
                """,
                (uuid_caller_user_id, allocation_row["unit_id"]),
            )
            if not cursor.fetchone():
                return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_UNIT_NOT_ASSIGNED_TO_CALLER, None

            # 4. Core DB operation: terminal-state check + insert approval_history_log row
            latest_status = _get_latest_history_status(cursor, uuid_allocation_id)
            if latest_status in TERMINAL_STATUSES_FOR_REJECT:
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, config.ERR_ALLOCATION_TERMINAL_STATE, None

            history_id = str(uuid.uuid4())
            performed_on = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO approval_history_log
                    (history_id, allocation_id, history_status, reason, performed_by, performed_on, created_on)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    history_id,
                    uuid_allocation_id,
                    "Rejected",
                    str_reason,
                    uuid_caller_user_id,
                    performed_on,
                    performed_on,
                ),
            )

            # 5. Core DB operation: bill goes On Hold in full (BR-003/FR-APR-004: all-or-nothing)
            cursor.execute(
                "UPDATE bill SET bill_status = %s, updated_by = %s, updated_on = %s WHERE bill_id = %s",
                ("On Hold", uuid_caller_user_id, performed_on, allocation_row["bill_id"]),
            )

        # 7. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"allocation_id": uuid_allocation_id, "history_status": "Rejected", "bill_status": "On Hold"},
        )

    except Exception as ex:
        # 8. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
