import uuid
from datetime import datetime, timezone

from common_functions.db import get_db_cursor
from common_functions import config

VALID_ASSIGNMENT_STATUSES = ("Active", "Inactive")
VALID_SEARCH_KEYS = {"assignment_id", "user_id", "unit_id"}
VALID_UPDATE_KEYS = {"assignment_status"}


def _is_valid_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def f_create_user_unit_details(uuid_caller_user_id, str_caller_role, uuid_user_id, uuid_unit_id, str_assignment_status):
    try:
        # 1. Validate input
        if not uuid_user_id or not _is_valid_uuid(uuid_user_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not uuid_unit_id or not _is_valid_uuid(uuid_unit_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        if str_assignment_status is not None and not isinstance(str_assignment_status, str):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        str_assignment_status = (str_assignment_status or "Active").strip()
        if str_assignment_status not in VALID_ASSIGNMENT_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: uniqueness pre-check on user_id + unit_id while Active
            cursor.execute(
                """
                SELECT assignment_id FROM user_unit_details
                WHERE user_id = %s AND unit_id = %s AND assignment_status = 'Active'
                """,
                (uuid_user_id, uuid_unit_id),
            )
            if cursor.fetchone():
                # 4. Conflict: skip insert
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "duplicate user_id", None

            # 4. Core DB operation: insert new user_unit_details row
            assignment_id = str(uuid.uuid4())
            created_on = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO user_unit_details
                    (assignment_id, user_id, unit_id, assignment_status, created_by, created_on)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (assignment_id, uuid_user_id, uuid_unit_id, str_assignment_status, uuid_caller_user_id, created_on),
            )

        # 5. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"assignment_id": assignment_id, "user_id": uuid_user_id, "unit_id": uuid_unit_id},
        )

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_update_user_unit_details(uuid_caller_user_id, str_caller_role, json_search_fields, json_update_fields):
    try:
        # 1. Validate input
        if not isinstance(json_search_fields, dict) or not json_search_fields:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(json_update_fields, dict) or not json_update_fields:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not set(json_search_fields.keys()) <= VALID_SEARCH_KEYS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not any(json_search_fields.get(key) for key in VALID_SEARCH_KEYS):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not set(json_update_fields.keys()) <= VALID_UPDATE_KEYS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if (
            "assignment_status" in json_update_fields
            and json_update_fields["assignment_status"] not in VALID_ASSIGNMENT_STATUSES
        ):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the matching user_unit_details row
            search_clauses = []
            search_values = []
            if json_search_fields.get("assignment_id"):
                search_clauses.append("assignment_id = %s")
                search_values.append(json_search_fields["assignment_id"])
            if json_search_fields.get("user_id"):
                search_clauses.append("user_id = %s")
                search_values.append(json_search_fields["user_id"])
            if json_search_fields.get("unit_id"):
                search_clauses.append("unit_id = %s")
                search_values.append(json_search_fields["unit_id"])

            cursor.execute(
                f"SELECT assignment_id FROM user_unit_details WHERE {' AND '.join(search_clauses)}",
                tuple(search_values),
            )
            matched_row = cursor.fetchone()

            # 4. Core DB operation
            if not matched_row:
                return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_NO_RECORD_FOUND, None

            assignment_id = matched_row["assignment_id"]

            set_clauses = []
            set_values = []
            if "assignment_status" in json_update_fields:
                set_clauses.append("assignment_status = %s")
                set_values.append(json_update_fields["assignment_status"])
            set_clauses.append("updated_by = %s")
            set_values.append(uuid_caller_user_id)
            set_clauses.append("updated_on = %s")
            set_values.append(datetime.now(timezone.utc))
            set_values.append(assignment_id)

            cursor.execute(
                f"UPDATE user_unit_details SET {', '.join(set_clauses)} WHERE assignment_id = %s",
                tuple(set_values),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"assignment_id": assignment_id}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_filter_user_unit_details(
    uuid_caller_user_id,
    str_caller_role,
    uuid_assignment_id,
    uuid_user_id,
    uuid_unit_id,
    str_assignment_status,
    int_page_no,
    int_page_size,
):
    try:
        # 1. Validate input
        if uuid_assignment_id and not _is_valid_uuid(uuid_assignment_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_user_id and not _is_valid_uuid(uuid_user_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_unit_id and not _is_valid_uuid(uuid_unit_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_assignment_status and str_assignment_status not in VALID_ASSIGNMENT_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no = 1 if int_page_no is None else int_page_no
        int_page_size = 20 if int_page_size is None else int_page_size
        if not isinstance(int_page_no, int) or int_page_no < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(int_page_size, int) or int_page_size < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: none required beyond caller's own role/scope
            # 4. Core DB operation: dynamic WHERE clause + pagination
            where_clauses = []
            values = []
            if uuid_assignment_id:
                where_clauses.append("assignment_id = %s")
                values.append(uuid_assignment_id)
            if uuid_user_id:
                where_clauses.append("user_id = %s")
                values.append(uuid_user_id)
            if uuid_unit_id:
                where_clauses.append("unit_id = %s")
                values.append(uuid_unit_id)
            if str_assignment_status:
                where_clauses.append("assignment_status = %s")
                values.append(str_assignment_status)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM user_unit_details {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"SELECT * FROM user_unit_details {where_sql} ORDER BY created_on DESC LIMIT %s OFFSET %s",
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


def f_delete_user_unit_details(uuid_caller_user_id, str_caller_role, uuid_assignment_id):
    try:
        # 1. Validate input
        if not uuid_assignment_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not _is_valid_uuid(uuid_assignment_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the user_unit_details row
            cursor.execute("SELECT assignment_id FROM user_unit_details WHERE assignment_id = %s", (uuid_assignment_id,))
            matched_row = cursor.fetchone()

            # 4. Core DB operation: soft delete (deactivate)
            if not matched_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            cursor.execute(
                """
                UPDATE user_unit_details
                SET assignment_status = 'Inactive', updated_by = %s, updated_on = %s
                WHERE assignment_id = %s
                """,
                (uuid_caller_user_id, datetime.now(timezone.utc), uuid_assignment_id),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"assignment_id": uuid_assignment_id, "assignment_status": "Inactive"}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
