import uuid
from datetime import datetime, timezone

from common_functions.db import get_db_cursor
from common_functions import config

VALID_MAPPING_STATUSES = ("Active", "Inactive")
VALID_SEARCH_KEYS = {"mapping_id", "account_id"}
VALID_UPDATE_KEYS = {"allocation_percentage", "mapping_status"}


def f_create_account_unit_details(uuid_caller_user_id, str_caller_role, uuid_account_id, json_unit_allocations):
    try:
        # 1. Validate input
        if not uuid_account_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        try:
            uuid.UUID(str(uuid_account_id))
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        if not isinstance(json_unit_allocations, list) or len(json_unit_allocations) < 2:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        for entry in json_unit_allocations:
            if not isinstance(entry, dict):
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
            uuid_unit_id = entry.get("unit_id")
            decimal_allocation_percentage = entry.get("allocation_percentage")
            if not uuid_unit_id or decimal_allocation_percentage is None:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
            try:
                uuid.UUID(str(uuid_unit_id))
            except ValueError:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
            if not isinstance(decimal_allocation_percentage, (int, float)):
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        unit_ids_in_request = [entry.get("unit_id") for entry in json_unit_allocations]
        if len(set(unit_ids_in_request)) != len(unit_ids_in_request):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "duplicate unit_id in unit_allocations", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookups: account_id exists, each unit_id exists,
            #    and no Active allocation set already exists for account_id (at most one
            #    Active set per account -- the caller must deactivate the existing set's
            #    rows down to zero before creating a new one).
            cursor.execute("SELECT account_id FROM account_master WHERE account_id = %s", (uuid_account_id,))
            if not cursor.fetchone():
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "account_id does not exist", None

            for entry in json_unit_allocations:
                cursor.execute("SELECT unit_id FROM unit_master WHERE unit_id = %s", (entry.get("unit_id"),))
                if not cursor.fetchone():
                    return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "unit_id does not exist", None

            cursor.execute(
                "SELECT mapping_id FROM account_unit_details WHERE account_id = %s AND mapping_status = 'Active'",
                (uuid_account_id,),
            )
            duplicate_exists = cursor.fetchone() is not None

            # 4. Validate business rule (FR-BIF-002/BR-002): SUM(allocation_percentage) must equal exactly 100.00
            total_allocation = round(
                sum(float(entry.get("allocation_percentage")) for entry in json_unit_allocations), 2
            )
            if total_allocation != 100.00:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, config.ERR_ALLOCATION_SUM_NOT_100, None

            # 5. Core DB operation: insert one row per unit_allocations entry (or 409 if an active set already exists)
            if duplicate_exists:
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, "an active allocation set already exists for this account_id", None

            created_on = datetime.now(timezone.utc)
            date_effective_from = created_on.date()
            mapping_ids = []
            for entry in json_unit_allocations:
                mapping_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO account_unit_details (
                        mapping_id, account_id, unit_id, allocation_percentage,
                        effective_from, mapping_status,
                        created_by, created_on
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        mapping_id,
                        uuid_account_id,
                        entry.get("unit_id"),
                        entry.get("allocation_percentage"),
                        date_effective_from,
                        "Active",
                        uuid_caller_user_id,
                        created_on,
                    ),
                )
                mapping_ids.append(mapping_id)

        # 6. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {"account_id": uuid_account_id, "mapping_ids": mapping_ids},
        )

    except Exception as ex:
        # 7. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_update_account_unit_details(uuid_caller_user_id, str_caller_role, json_search_fields, json_update_fields):
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
        if (
            "mapping_status" in json_update_fields
            and json_update_fields["mapping_status"] not in VALID_MAPPING_STATUSES
        ):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if "allocation_percentage" in json_update_fields and not isinstance(
            json_update_fields["allocation_percentage"], (int, float)
        ):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the matching account_unit_details row
            search_clauses = []
            search_values = []
            if json_search_fields.get("mapping_id"):
                search_clauses.append("mapping_id = %s")
                search_values.append(json_search_fields["mapping_id"])
            if json_search_fields.get("account_id"):
                search_clauses.append("account_id = %s")
                search_values.append(json_search_fields["account_id"])

            cursor.execute(
                f"SELECT mapping_id, account_id FROM account_unit_details WHERE {' AND '.join(search_clauses)}",
                tuple(search_values),
            )
            matched_rows = cursor.fetchall()

            # 4. Core DB operation
            if not matched_rows:
                return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_NO_RECORD_FOUND, None
            if len(matched_rows) > 1:
                return config.STATUS_DUPLICATE_CONFLICT, config.DESC_DUPLICATE_CONFLICT, config.ERR_MULTIPLE_ROWS_MATCHED, None

            mapping_id = matched_rows[0]["mapping_id"]
            account_id = matched_rows[0]["account_id"]

            # 5. If allocation_percentage is part of update_fields, re-validate that SUM(allocation_percentage)
            #    across all Active rows for the affected account_id would still equal exactly 100.00
            #    (checked before issuing the UPDATE so nothing partial is ever committed).
            if "allocation_percentage" in json_update_fields:
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(allocation_percentage), 0) AS total_allocation
                    FROM account_unit_details
                    WHERE account_id = %s AND mapping_status = 'Active' AND mapping_id != %s
                    """,
                    (account_id, mapping_id),
                )
                existing_total = float(cursor.fetchone()["total_allocation"])
                new_total = round(existing_total + float(json_update_fields["allocation_percentage"]), 2)
                if new_total != 100.00:
                    return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, config.ERR_ALLOCATION_SUM_NOT_100, None

            # 5b. BR-002: deactivating this row must not leave the account's remaining Active
            #     rows summing to anything other than exactly 100.00 or 0 (fully closed out) --
            #     re-checked here since deactivation alone (no percentage change) previously
            #     skipped this invariant entirely.
            if json_update_fields.get("mapping_status") == "Inactive":
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(allocation_percentage), 0) AS total_allocation, COUNT(*) AS active_count
                    FROM account_unit_details
                    WHERE account_id = %s AND mapping_status = 'Active' AND mapping_id != %s
                    """,
                    (account_id, mapping_id),
                )
                remaining = cursor.fetchone()
                remaining_total = round(float(remaining["total_allocation"]), 2)
                if remaining["active_count"] > 0 and remaining_total != 100.00:
                    return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, config.ERR_ALLOCATION_SUM_NOT_100, None

            set_clauses = []
            set_values = []
            if "allocation_percentage" in json_update_fields:
                set_clauses.append("allocation_percentage = %s")
                set_values.append(json_update_fields["allocation_percentage"])
            if "mapping_status" in json_update_fields:
                set_clauses.append("mapping_status = %s")
                set_values.append(json_update_fields["mapping_status"])
            set_clauses.append("updated_by = %s")
            set_values.append(uuid_caller_user_id)
            set_clauses.append("updated_on = %s")
            set_values.append(datetime.now(timezone.utc))
            set_values.append(mapping_id)

            cursor.execute(
                f"UPDATE account_unit_details SET {', '.join(set_clauses)} WHERE mapping_id = %s",
                tuple(set_values),
            )

        # 6. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"mapping_id": mapping_id}

    except Exception as ex:
        # 7. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_filter_account_unit_details(
    uuid_caller_user_id,
    str_caller_role,
    uuid_mapping_id,
    uuid_account_id,
    uuid_unit_id,
    str_mapping_status,
    int_page_no,
    int_page_size,
):
    try:
        # 1. Validate input
        for value in (uuid_mapping_id, uuid_account_id, uuid_unit_id):
            if value:
                try:
                    uuid.UUID(str(value))
                except ValueError:
                    return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_mapping_status and str_mapping_status not in VALID_MAPPING_STATUSES:
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
            # 3. Resolve dependent lookup: none required beyond caller's own role
            # 4. Core DB operation: dynamic WHERE clause + pagination
            where_clauses = []
            values = []
            if uuid_mapping_id:
                where_clauses.append("mapping_id = %s")
                values.append(uuid_mapping_id)
            if uuid_account_id:
                where_clauses.append("account_id = %s")
                values.append(uuid_account_id)
            if uuid_unit_id:
                where_clauses.append("unit_id = %s")
                values.append(uuid_unit_id)
            if str_mapping_status:
                where_clauses.append("mapping_status = %s")
                values.append(str_mapping_status)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM account_unit_details {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"SELECT * FROM account_unit_details {where_sql} ORDER BY created_on DESC LIMIT %s OFFSET %s",
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


def f_delete_account_unit_details(uuid_caller_user_id, str_caller_role, uuid_mapping_id):
    try:
        # 1. Validate input
        if not uuid_mapping_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        try:
            uuid.UUID(str(uuid_mapping_id))
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 3. Resolve dependent lookup: locate the account_unit_details row
            cursor.execute(
                "SELECT mapping_id, account_id, mapping_status FROM account_unit_details WHERE mapping_id = %s",
                (uuid_mapping_id,),
            )
            matched_row = cursor.fetchone()

            # 4. Core DB operation: soft delete (deactivate)
            if not matched_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            # 4b. BR-002: deactivating this row must not leave the account's remaining Active
            #     rows summing to anything other than exactly 100.00 or 0 (fully closed out).
            if matched_row["mapping_status"] == "Active":
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(allocation_percentage), 0) AS total_allocation, COUNT(*) AS active_count
                    FROM account_unit_details
                    WHERE account_id = %s AND mapping_status = 'Active' AND mapping_id != %s
                    """,
                    (matched_row["account_id"], uuid_mapping_id),
                )
                remaining = cursor.fetchone()
                remaining_total = round(float(remaining["total_allocation"]), 2)
                if remaining["active_count"] > 0 and remaining_total != 100.00:
                    return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, config.ERR_ALLOCATION_SUM_NOT_100, None

            cursor.execute(
                """
                UPDATE account_unit_details
                SET mapping_status = 'Inactive', updated_by = %s, updated_on = %s
                WHERE mapping_id = %s
                """,
                (uuid_caller_user_id, datetime.now(timezone.utc), uuid_mapping_id),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"mapping_id": uuid_mapping_id, "mapping_status": "Inactive"}

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
