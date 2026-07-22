import uuid
from datetime import datetime, timezone

from common_functions.db import get_db_cursor
from common_functions import config

VALID_UNIT_STATUSES = ("Active", "Inactive")
VALID_SEARCH_KEYS = {"unit_id", "unit_code"}
VALID_UPDATE_KEYS = {"unit_code", "unit_name", "address", "unit_status"}


def f_create_unit_master(uuid_caller_user_id, str_caller_role, str_unit_code, str_unit_name, str_address, str_unit_status):
    try:
        # 1. Validate input
        if not isinstance(str_unit_code, str) or not str_unit_code.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(str_unit_name, str) or not str_unit_name.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_unit_status is not None and not isinstance(str_unit_status, str):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_unit_status = (str_unit_status or "Active").strip()
        if str_unit_status not in VALID_UNIT_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: uniqueness pre-check on unit_code
            cursor.execute(
                """
                SELECT unit_id FROM unit_master
                WHERE unit_code = %s AND unit_status = 'Active'
                """,
                (str_unit_code,),
            )
            if cursor.fetchone():
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "duplicate unit_code", None

            # 4. Core DB operation: insert new unit_master row
            unit_id = str(uuid.uuid4())
            created_on = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO unit_master (unit_id, unit_code, unit_name, address, unit_status, created_by, created_on)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (unit_id, str_unit_code, str_unit_name, str_address, str_unit_status, uuid_caller_user_id, created_on),
            )

        # 5. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"unit_id": unit_id, "unit_code": str_unit_code, "unit_name": str_unit_name},
        )

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_update_unit_master(uuid_caller_user_id, str_caller_role, json_search_fields, json_update_fields):
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
        if "unit_status" in json_update_fields and json_update_fields["unit_status"] not in VALID_UNIT_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the matching unit_master row
            search_clauses = []
            search_values = []
            if json_search_fields.get("unit_id"):
                search_clauses.append("unit_id = %s")
                search_values.append(json_search_fields["unit_id"])
            if json_search_fields.get("unit_code"):
                search_clauses.append("unit_code = %s")
                search_values.append(json_search_fields["unit_code"])

            cursor.execute(
                f"SELECT unit_id FROM unit_master WHERE {' AND '.join(search_clauses)}",
                tuple(search_values),
            )
            matched_row = cursor.fetchone()

            # 4. Core DB operation
            if not matched_row:
                return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_NO_RECORD_FOUND, None

            unit_id = matched_row["unit_id"]

            if "unit_code" in json_update_fields:
                cursor.execute(
                    """
                    SELECT unit_id FROM unit_master
                    WHERE unit_code = %s AND unit_status = 'Active' AND unit_id != %s
                    """,
                    (json_update_fields["unit_code"], unit_id),
                )
                if cursor.fetchone():
                    return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "duplicate str_unit_code", None

            set_clauses = []
            set_values = []
            if "unit_code" in json_update_fields:
                set_clauses.append("unit_code = %s")
                set_values.append(json_update_fields["unit_code"])
            if "unit_name" in json_update_fields:
                set_clauses.append("unit_name = %s")
                set_values.append(json_update_fields["unit_name"])
            if "address" in json_update_fields:
                set_clauses.append("address = %s")
                set_values.append(json_update_fields["address"])
            if "unit_status" in json_update_fields:
                set_clauses.append("unit_status = %s")
                set_values.append(json_update_fields["unit_status"])
            set_clauses.append("updated_by = %s")
            set_values.append(uuid_caller_user_id)
            set_clauses.append("updated_on = %s")
            set_values.append(datetime.now(timezone.utc))
            set_values.append(unit_id)

            cursor.execute(
                f"UPDATE unit_master SET {', '.join(set_clauses)} WHERE unit_id = %s",
                tuple(set_values),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"unit_id": unit_id}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_filter_unit_master(
    uuid_caller_user_id, str_caller_role, uuid_unit_id, str_unit_code, str_unit_name, str_unit_status, int_page_no, int_page_size
):
    try:
        # 1. Validate input
        if uuid_unit_id:
            try:
                uuid.UUID(str(uuid_unit_id))
            except ValueError:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_unit_status and str_unit_status not in VALID_UNIT_STATUSES:
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
            if uuid_unit_id:
                where_clauses.append("unit_id = %s")
                values.append(uuid_unit_id)
            if str_unit_code:
                where_clauses.append("unit_code ILIKE %s")
                values.append(f"%{str_unit_code}%")
            if str_unit_name:
                where_clauses.append("unit_name ILIKE %s")
                values.append(f"%{str_unit_name}%")
            if str_unit_status:
                where_clauses.append("unit_status = %s")
                values.append(str_unit_status)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM unit_master {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"SELECT * FROM unit_master {where_sql} ORDER BY created_on DESC LIMIT %s OFFSET %s",
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


def f_delete_unit_master(uuid_caller_user_id, str_caller_role, uuid_unit_id):
    try:
        # 1. Validate input
        if not uuid_unit_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        try:
            uuid.UUID(str(uuid_unit_id))
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the unit_master row
            cursor.execute("SELECT unit_id FROM unit_master WHERE unit_id = %s", (uuid_unit_id,))
            matched_row = cursor.fetchone()

            # 4. Core DB operation: soft delete (deactivate)
            if not matched_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            cursor.execute(
                """
                UPDATE unit_master
                SET unit_status = 'Inactive', updated_by = %s, updated_on = %s
                WHERE unit_id = %s
                """,
                (uuid_caller_user_id, datetime.now(timezone.utc), uuid_unit_id),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"unit_id": uuid_unit_id, "unit_status": "Inactive"}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
