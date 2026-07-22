import uuid
from datetime import datetime, timezone

from common_functions.db import get_db_cursor
from common_functions import config

VALID_ACCOUNT_STATUSES = ("Active", "Inactive")
VALID_SEARCH_KEYS = {"account_id", "account_number"}
VALID_UPDATE_KEYS = {
    "account_name",
    "utility_category_name",
    "provider_name",
    "provider_contact_email",
    "provider_contact_phone",
    "account_status",
}


def f_create_account_master(
    uuid_caller_user_id,
    str_caller_role,
    str_account_number,
    str_account_name,
    str_utility_category_name,
    str_provider_name,
    str_provider_contact_email,
    str_provider_contact_phone,
    str_account_status,
):
    try:
        # 1. Validate input
        if not isinstance(str_account_number, str) or not str_account_number.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(str_account_name, str) or not str_account_name.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(str_utility_category_name, str) or not str_utility_category_name.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(str_provider_name, str) or not str_provider_name.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_account_status is not None and not isinstance(str_account_status, str):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_account_status = (str_account_status or "Active").strip()
        if str_account_status not in VALID_ACCOUNT_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: uniqueness pre-check on account_number
            cursor.execute(
                """
                SELECT account_id FROM account_master
                WHERE account_number = %s AND account_status = 'Active'
                """,
                (str_account_number,),
            )
            if cursor.fetchone():
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "duplicate account_number", None

            # 4. Core DB operation: insert new account_master row
            account_id = str(uuid.uuid4())
            created_on = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO account_master (
                    account_id, account_number, account_name, utility_category_name,
                    provider_name, provider_contact_email, provider_contact_phone,
                    account_status, created_by, created_on
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    account_id,
                    str_account_number,
                    str_account_name,
                    str_utility_category_name,
                    str_provider_name,
                    str_provider_contact_email,
                    str_provider_contact_phone,
                    str_account_status,
                    uuid_caller_user_id,
                    created_on,
                ),
            )

        # 5. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"account_id": account_id, "account_number": str_account_number, "account_name": str_account_name},
        )

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_update_account_master(uuid_caller_user_id, str_caller_role, json_search_fields, json_update_fields):
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
        if "account_status" in json_update_fields and json_update_fields["account_status"] not in VALID_ACCOUNT_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the matching account_master row
            search_clauses = []
            search_values = []
            if json_search_fields.get("account_id"):
                search_clauses.append("account_id = %s")
                search_values.append(json_search_fields["account_id"])
            if json_search_fields.get("account_number"):
                search_clauses.append("account_number = %s")
                search_values.append(json_search_fields["account_number"])

            cursor.execute(
                f"SELECT account_id FROM account_master WHERE {' AND '.join(search_clauses)}",
                tuple(search_values),
            )
            matched_row = cursor.fetchone()

            # 4. Core DB operation
            if not matched_row:
                return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_NO_RECORD_FOUND, None

            account_id = matched_row["account_id"]

            set_clauses = []
            set_values = []
            if "account_name" in json_update_fields:
                set_clauses.append("account_name = %s")
                set_values.append(json_update_fields["account_name"])
            if "utility_category_name" in json_update_fields:
                set_clauses.append("utility_category_name = %s")
                set_values.append(json_update_fields["utility_category_name"])
            if "provider_name" in json_update_fields:
                set_clauses.append("provider_name = %s")
                set_values.append(json_update_fields["provider_name"])
            if "provider_contact_email" in json_update_fields:
                set_clauses.append("provider_contact_email = %s")
                set_values.append(json_update_fields["provider_contact_email"])
            if "provider_contact_phone" in json_update_fields:
                set_clauses.append("provider_contact_phone = %s")
                set_values.append(json_update_fields["provider_contact_phone"])
            if "account_status" in json_update_fields:
                set_clauses.append("account_status = %s")
                set_values.append(json_update_fields["account_status"])
            set_clauses.append("updated_by = %s")
            set_values.append(uuid_caller_user_id)
            set_clauses.append("updated_on = %s")
            set_values.append(datetime.now(timezone.utc))
            set_values.append(account_id)

            cursor.execute(
                f"UPDATE account_master SET {', '.join(set_clauses)} WHERE account_id = %s",
                tuple(set_values),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"account_id": account_id}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_filter_account_master(
    uuid_caller_user_id,
    str_caller_role,
    uuid_account_id,
    str_account_number,
    str_account_name,
    str_utility_category_name,
    str_provider_name,
    str_account_status,
    int_page_no,
    int_page_size,
):
    try:
        # 1. Validate input
        if uuid_account_id:
            try:
                uuid.UUID(str(uuid_account_id))
            except ValueError:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_account_status and str_account_status not in VALID_ACCOUNT_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        if int_page_no is None:
            int_page_no = 1
        if int_page_size is None:
            int_page_size = 20
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
            if uuid_account_id:
                where_clauses.append("account_id = %s")
                values.append(uuid_account_id)
            if str_account_number:
                where_clauses.append("account_number ILIKE %s")
                values.append(f"%{str_account_number}%")
            if str_account_name:
                where_clauses.append("account_name ILIKE %s")
                values.append(f"%{str_account_name}%")
            if str_utility_category_name:
                where_clauses.append("utility_category_name = %s")
                values.append(str_utility_category_name)
            if str_provider_name:
                where_clauses.append("provider_name = %s")
                values.append(str_provider_name)
            if str_account_status:
                where_clauses.append("account_status = %s")
                values.append(str_account_status)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM account_master {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"SELECT * FROM account_master {where_sql} ORDER BY created_on DESC LIMIT %s OFFSET %s",
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


def f_delete_account_master(uuid_caller_user_id, str_caller_role, uuid_account_id):
    try:
        # 1. Validate input
        if not uuid_account_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        try:
            uuid.UUID(str(uuid_account_id))
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the account_master row
            cursor.execute("SELECT account_id FROM account_master WHERE account_id = %s", (uuid_account_id,))
            matched_row = cursor.fetchone()

            # 4. Core DB operation: soft delete (deactivate)
            if not matched_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            cursor.execute(
                """
                UPDATE account_master
                SET account_status = 'Inactive', updated_by = %s, updated_on = %s
                WHERE account_id = %s
                """,
                (uuid_caller_user_id, datetime.now(timezone.utc), uuid_account_id),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"account_id": uuid_account_id, "account_status": "Inactive"}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
