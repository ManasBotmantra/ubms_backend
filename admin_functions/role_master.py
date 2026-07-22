import uuid
from datetime import datetime, timezone

from common_functions.db import get_db_cursor
from common_functions import config

VALID_ROLE_STATUSES = ("Active", "Inactive")
VALID_SEARCH_KEYS = {"role_id", "role_name"}
VALID_UPDATE_KEYS = {"role_name", "role_status"}


def f_create_role_master(uuid_caller_user_id, str_caller_role, str_role_name, str_role_status):
    try:
        # 1. Validate input
        if not isinstance(str_role_name, str) or not str_role_name.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_role_status is not None and not isinstance(str_role_status, str):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_role_status = (str_role_status or "Active").strip()
        if str_role_status not in VALID_ROLE_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: uniqueness pre-check on role_name
            cursor.execute(
                """
                SELECT role_id FROM role_master
                WHERE role_name = %s AND role_status = 'Active'
                """,
                (str_role_name,),
            )
            if cursor.fetchone():
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "duplicate role_name", None

            # 4. Core DB operation: insert new role_master row
            role_id = str(uuid.uuid4())
            created_on = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO role_master (role_id, role_name, role_status, created_by, created_on)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (role_id, str_role_name, str_role_status, uuid_caller_user_id, created_on),
            )

        # 5. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"role_id": role_id, "role_name": str_role_name, "role_status": str_role_status},
        )

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_update_role_master(uuid_caller_user_id, str_caller_role, json_search_fields, json_update_fields):
    try:
        # 1. Validate input
        if not isinstance(json_search_fields, dict) or not json_search_fields:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(json_update_fields, dict) or not json_update_fields:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not set(json_search_fields.keys()) <= VALID_SEARCH_KEYS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not set(json_update_fields.keys()) <= VALID_UPDATE_KEYS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not any(v not in (None, "") for v in json_search_fields.values()):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not any(v not in (None, "") for v in json_update_fields.values()):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if "role_name" in json_update_fields and not str(json_update_fields["role_name"] or "").strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if "role_status" in json_update_fields and json_update_fields["role_status"] not in VALID_ROLE_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the matching role_master row
            search_clauses = []
            search_values = []
            if json_search_fields.get("role_id"):
                search_clauses.append("role_id = %s")
                search_values.append(json_search_fields["role_id"])
            if json_search_fields.get("role_name"):
                search_clauses.append("role_name = %s")
                search_values.append(json_search_fields["role_name"])

            cursor.execute(
                f"SELECT role_id FROM role_master WHERE {' AND '.join(search_clauses)}",
                tuple(search_values),
            )
            matched_row = cursor.fetchone()

            # 4. Core DB operation
            if not matched_row:
                return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_NO_RECORD_FOUND, None

            role_id = matched_row["role_id"]

            if "role_name" in json_update_fields:
                cursor.execute(
                    """
                    SELECT role_id FROM role_master
                    WHERE role_name = %s AND role_status = 'Active' AND role_id != %s
                    """,
                    (json_update_fields["role_name"], role_id),
                )
                if cursor.fetchone():
                    return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "duplicate str_role_name", None

            set_clauses = []
            set_values = []
            if "role_name" in json_update_fields:
                set_clauses.append("role_name = %s")
                set_values.append(json_update_fields["role_name"])
            if "role_status" in json_update_fields:
                set_clauses.append("role_status = %s")
                set_values.append(json_update_fields["role_status"])
            set_clauses.append("updated_by = %s")
            set_values.append(uuid_caller_user_id)
            set_clauses.append("updated_on = %s")
            set_values.append(datetime.now(timezone.utc))
            set_values.append(role_id)

            cursor.execute(
                f"UPDATE role_master SET {', '.join(set_clauses)} WHERE role_id = %s",
                tuple(set_values),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"role_id": role_id}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_filter_role_master(
    uuid_caller_user_id, str_caller_role, uuid_role_id, str_role_name, str_role_status, int_page_no, int_page_size
):
    try:
        # 1. Validate input
        if uuid_role_id:
            try:
                uuid.UUID(str(uuid_role_id))
            except ValueError:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_role_status and str_role_status not in VALID_ROLE_STATUSES:
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
            if uuid_role_id:
                where_clauses.append("role_id = %s")
                values.append(uuid_role_id)
            if str_role_name:
                where_clauses.append("role_name ILIKE %s")
                values.append(f"%{str_role_name}%")
            if str_role_status:
                where_clauses.append("role_status = %s")
                values.append(str_role_status)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM role_master {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"SELECT * FROM role_master {where_sql} ORDER BY created_on DESC LIMIT %s OFFSET %s",
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


def f_delete_role_master(uuid_caller_user_id, str_caller_role, uuid_role_id):
    try:
        # 1. Validate input
        if not uuid_role_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        try:
            uuid.UUID(str(uuid_role_id))
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the role_master row
            cursor.execute("SELECT role_id FROM role_master WHERE role_id = %s", (uuid_role_id,))
            matched_row = cursor.fetchone()

            # 4. Core DB operation: soft delete (deactivate)
            if not matched_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            cursor.execute(
                """
                UPDATE role_master
                SET role_status = 'Inactive', updated_by = %s, updated_on = %s
                WHERE role_id = %s
                """,
                (uuid_caller_user_id, datetime.now(timezone.utc), uuid_role_id),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"role_id": uuid_role_id, "role_status": "Inactive"}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
