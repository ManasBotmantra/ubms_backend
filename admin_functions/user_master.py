import uuid
from datetime import datetime, timezone

import bcrypt

from common_functions.db import get_db_cursor
from common_functions import config

VALID_USER_STATUSES = ("Active", "Inactive")
VALID_SEARCH_KEYS = {"user_id", "email"}
VALID_UPDATE_KEYS = {"role_id", "full_name", "email", "user_status", "password"}
MIN_PASSWORD_LENGTH = 8


def _hash_password(str_password):
    return bcrypt.hashpw(str_password.encode(), bcrypt.gensalt()).decode()


def _is_valid_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def f_create_user_master(
    uuid_caller_user_id, str_caller_role, uuid_role_id, str_full_name, str_email, str_password, str_user_status
):
    try:
        # 1. Validate input
        if not uuid_role_id or not _is_valid_uuid(uuid_role_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(str_full_name, str) or not str_full_name.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(str_email, str) or not str_email.strip() or "@" not in str_email:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        str_email = str_email.strip().lower()
        if not isinstance(str_password, str) or len(str_password) < MIN_PASSWORD_LENGTH:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_user_status is not None and not isinstance(str_user_status, str):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_user_status = (str_user_status or "Active").strip()
        if str_user_status not in VALID_USER_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: uniqueness pre-check on email (case-insensitive --
            # emails are treated as case-insensitive identifiers, matching industry convention
            # and catching any pre-existing mixed-case row even though new writes are normalized)
            cursor.execute(
                """
                SELECT user_id FROM user_master
                WHERE LOWER(email) = %s AND user_status = 'Active'
                """,
                (str_email,),
            )
            if cursor.fetchone():
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "duplicate email", None

            # 4. Core DB operation: insert new user_master row
            user_id = str(uuid.uuid4())
            created_on = datetime.now(timezone.utc)
            password_hash = _hash_password(str_password)
            cursor.execute(
                """
                INSERT INTO user_master
                    (user_id, role_id, full_name, email, password_hash, user_status, created_by, created_on)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    uuid_role_id,
                    str_full_name,
                    str_email,
                    password_hash,
                    str_user_status,
                    uuid_caller_user_id,
                    created_on,
                ),
            )

        # 5. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"user_id": user_id, "role_id": uuid_role_id, "full_name": str_full_name, "email": str_email},
        )

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_update_user_master(uuid_caller_user_id, str_caller_role, json_search_fields, json_update_fields):
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
        if "user_status" in json_update_fields and json_update_fields["user_status"] not in VALID_USER_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if "role_id" in json_update_fields and not _is_valid_uuid(json_update_fields["role_id"]):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if "email" in json_update_fields and (
            not isinstance(json_update_fields["email"], str)
            or not json_update_fields["email"].strip()
            or "@" not in json_update_fields["email"]
        ):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if "user_id" in json_search_fields and not _is_valid_uuid(json_search_fields["user_id"]):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if "email" in json_update_fields:
            json_update_fields["email"] = json_update_fields["email"].strip().lower()
        if "password" in json_update_fields and (
            not isinstance(json_update_fields["password"], str)
            or len(json_update_fields["password"]) < MIN_PASSWORD_LENGTH
        ):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not any(v not in (None, "") for v in json_search_fields.values()):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the matching user_master row
            search_clauses = []
            search_values = []
            if json_search_fields.get("user_id"):
                search_clauses.append("user_id = %s")
                search_values.append(json_search_fields["user_id"])
            if json_search_fields.get("email"):
                search_clauses.append("LOWER(email) = %s")
                search_values.append(str(json_search_fields["email"]).strip().lower())

            cursor.execute(
                f"SELECT user_id FROM user_master WHERE {' AND '.join(search_clauses)}",
                tuple(search_values),
            )
            matched_row = cursor.fetchone()

            # 4. Core DB operation
            if not matched_row:
                return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_NO_RECORD_FOUND, None

            user_id = matched_row["user_id"]

            if "email" in json_update_fields:
                cursor.execute(
                    """
                    SELECT user_id FROM user_master
                    WHERE LOWER(email) = %s AND user_status = 'Active' AND user_id != %s
                    """,
                    (json_update_fields["email"], user_id),
                )
                if cursor.fetchone():
                    return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "duplicate str_email", None

            set_clauses = []
            set_values = []
            if "role_id" in json_update_fields:
                set_clauses.append("role_id = %s")
                set_values.append(json_update_fields["role_id"])
            if "full_name" in json_update_fields:
                set_clauses.append("full_name = %s")
                set_values.append(json_update_fields["full_name"])
            if "email" in json_update_fields:
                set_clauses.append("email = %s")
                set_values.append(json_update_fields["email"])
            if "user_status" in json_update_fields:
                set_clauses.append("user_status = %s")
                set_values.append(json_update_fields["user_status"])
            if "password" in json_update_fields:
                set_clauses.append("password_hash = %s")
                set_values.append(_hash_password(json_update_fields["password"]))
            set_clauses.append("updated_by = %s")
            set_values.append(uuid_caller_user_id)
            set_clauses.append("updated_on = %s")
            set_values.append(datetime.now(timezone.utc))
            set_values.append(user_id)

            cursor.execute(
                f"UPDATE user_master SET {', '.join(set_clauses)} WHERE user_id = %s",
                tuple(set_values),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"user_id": user_id}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_filter_user_master(
    uuid_caller_user_id,
    str_caller_role,
    uuid_user_id,
    uuid_role_id,
    str_full_name,
    str_email,
    str_user_status,
    int_page_no,
    int_page_size,
):
    try:
        # 1. Validate input
        if uuid_user_id and not _is_valid_uuid(uuid_user_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_role_id and not _is_valid_uuid(uuid_role_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_user_status and str_user_status not in VALID_USER_STATUSES:
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
            if uuid_user_id:
                where_clauses.append("user_id = %s")
                values.append(uuid_user_id)
            if uuid_role_id:
                where_clauses.append("role_id = %s")
                values.append(uuid_role_id)
            if str_full_name:
                where_clauses.append("full_name ILIKE %s")
                values.append(f"%{str_full_name}%")
            if str_email:
                where_clauses.append("email ILIKE %s")
                values.append(f"%{str_email}%")
            if str_user_status:
                where_clauses.append("user_status = %s")
                values.append(str_user_status)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM user_master {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"""
                SELECT user_id, role_id, full_name, email, user_status,
                       created_by, created_on, updated_by, updated_on
                FROM user_master {where_sql}
                ORDER BY created_on DESC LIMIT %s OFFSET %s
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


def f_delete_user_master(uuid_caller_user_id, str_caller_role, uuid_user_id):
    try:
        # 1. Validate input
        if not uuid_user_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not _is_valid_uuid(uuid_user_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the user_master row
            cursor.execute("SELECT user_id FROM user_master WHERE user_id = %s", (uuid_user_id,))
            matched_row = cursor.fetchone()

            # 4. Core DB operation: soft delete (deactivate)
            if not matched_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            cursor.execute(
                """
                UPDATE user_master
                SET user_status = 'Inactive', updated_by = %s, updated_on = %s
                WHERE user_id = %s
                """,
                (uuid_caller_user_id, datetime.now(timezone.utc), uuid_user_id),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"user_id": uuid_user_id, "user_status": "Inactive"}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
