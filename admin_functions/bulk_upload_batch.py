import base64
import io
import uuid
from datetime import date, datetime, timedelta, timezone

from dateutil import parser as date_parser
import openpyxl

from common_functions.db import get_db_cursor
from common_functions import config

SUPPORTED_EXTENSIONS = (".xlsx", ".xls")
VALID_BATCH_STATUSES = ("Processing", "Completed", "Completed with Errors", "Failed")
SORTABLE_BATCH_COLUMNS = {
    "batch_id",
    "uploaded_by",
    "file_name",
    "batch_status",
    "created_on",
    "total_records",
    "success_count",
    "error_count",
}
# OQ-005 resolved: stakeholder confirmed AED as the operating currency for all bills.
DEFAULT_CURRENCY = "AED"

# OQ-001 (workbook template pending stakeholder confirmation): the parser assumes a
# single-sheet workbook whose header row contains these column names (case-insensitive).
EXPECTED_COLUMNS = ("account_number", "billing_period", "bill_date", "total_amount", "due_date")


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


def _parse_workbook_rows(bytes_file_data):
    # NOTE: requires the `openpyxl` package, which is not yet declared in requirements.txt.
    workbook = openpyxl.load_workbook(io.BytesIO(bytes_file_data), data_only=True)
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)

    header_row = next(rows_iter, None)
    if header_row is None:
        return []

    headers = [str(cell).strip().lower() if cell is not None else "" for cell in header_row]

    rows = []
    for row in rows_iter:
        if row is None or all(cell is None for cell in row):
            continue
        rows.append(dict(zip(headers, row)))
    return rows


def _bifurcate_bill(cursor, uuid_bill_id, uuid_account_id, total_amount, uuid_caller_user_id, created_on):
    """Apply the Active account_unit_details mapping for the account and insert the
    resulting bill_unit_details allocation rows (NFR-008: rounded to two decimals)."""
    cursor.execute(
        """
        SELECT mapping_id, unit_id, allocation_percentage
        FROM account_unit_details
        WHERE account_id = %s AND mapping_status = 'Active'
        """,
        (uuid_account_id,),
    )
    mappings = cursor.fetchall()
    if not mappings:
        return False

    for mapping in mappings:
        allocated_amount = round(float(total_amount) * float(mapping["allocation_percentage"]) / 100.0, 2)
        allocation_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO bill_unit_details
                (allocation_id, bill_id, unit_id, mapping_id,
                 allocated_percentage, allocated_amount, created_by, created_on)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                allocation_id,
                uuid_bill_id,
                mapping["unit_id"],
                mapping["mapping_id"],
                mapping["allocation_percentage"],
                allocated_amount,
                uuid_caller_user_id,
                created_on,
            ),
        )
    return True


def f_upload_bulk_upload_batch(uuid_caller_user_id, str_caller_role, str_file_name, blob_file_data):
    try:
        # 1. Validate input
        if not str_file_name or not str_file_name.strip():
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not blob_file_data:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not str_file_name.lower().endswith(SUPPORTED_EXTENSIONS):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        try:
            file_bytes = base64.b64decode(blob_file_data, validate=True)
        except Exception:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        try:
            parsed_rows = _parse_workbook_rows(file_bytes)
        except Exception:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor(commit=True) as cursor:
            # 4. Core DB operation: create the bulk_upload_batch header row.
            # The uploaded file is parsed in-memory only and not persisted to blob
            # storage -- bulk_upload_batch has no column to store a file reference.
            batch_id = str(uuid.uuid4())
            created_on = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO bulk_upload_batch
                    (batch_id, file_name, uploaded_by, batch_status,
                     total_records, success_count, error_count, created_on)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    batch_id,
                    str_file_name,
                    uuid_caller_user_id,
                    "Processing",
                    0,
                    0,
                    0,
                    created_on,
                ),
            )

            total_records = len(parsed_rows)
            success_count = 0
            error_count = 0

            for row in parsed_rows:
                # Use a savepoint per row so a single bad row (e.g. FK/constraint
                # violation) does not abort the whole batch transaction.
                cursor.execute("SAVEPOINT row_savepoint")
                try:
                    account_number = row.get("account_number")
                    billing_period = row.get("billing_period")
                    bill_date = row.get("bill_date")
                    total_amount = row.get("total_amount")
                    due_date_raw = row.get("due_date")
                    due_date = _parse_date(due_date_raw) if due_date_raw not in (None, "") else None

                    if not account_number or total_amount in (None, ""):
                        cursor.execute("ROLLBACK TO SAVEPOINT row_savepoint")
                        cursor.execute("RELEASE SAVEPOINT row_savepoint")
                        error_count += 1
                        continue

                    cursor.execute(
                        "SELECT account_id FROM account_master WHERE account_number = %s AND account_status = 'Active'",
                        (str(account_number),),
                    )
                    account_row = cursor.fetchone()
                    if not account_row:
                        # OQ-002: unrecognized/inactive account_number -> flagged, batch continues.
                        cursor.execute("ROLLBACK TO SAVEPOINT row_savepoint")
                        cursor.execute("RELEASE SAVEPOINT row_savepoint")
                        error_count += 1
                        continue

                    uuid_account_id = account_row["account_id"]
                    bill_id = str(uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT INTO bill
                            (bill_id, batch_id, account_id, billing_period, bill_date,
                             due_date, total_amount, currency, bill_status, created_by, created_on)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            bill_id,
                            batch_id,
                            uuid_account_id,
                            billing_period,
                            bill_date,
                            due_date,
                            total_amount,
                            DEFAULT_CURRENCY,
                            "Pending Approval",
                            uuid_caller_user_id,
                            created_on,
                        ),
                    )

                    bifurcated = _bifurcate_bill(
                        cursor, bill_id, uuid_account_id, total_amount, uuid_caller_user_id, created_on
                    )
                    if not bifurcated:
                        cursor.execute("ROLLBACK TO SAVEPOINT row_savepoint")
                        cursor.execute("RELEASE SAVEPOINT row_savepoint")
                        error_count += 1
                        continue

                    cursor.execute("RELEASE SAVEPOINT row_savepoint")
                    success_count += 1
                except Exception:
                    cursor.execute("ROLLBACK TO SAVEPOINT row_savepoint")
                    cursor.execute("RELEASE SAVEPOINT row_savepoint")
                    error_count += 1
                    continue

            if error_count == 0:
                batch_status = "Completed"
            elif success_count == 0:
                batch_status = "Failed"
            else:
                batch_status = "Completed with Errors"

            cursor.execute(
                """
                UPDATE bulk_upload_batch
                SET total_records = %s, success_count = %s, error_count = %s, batch_status = %s
                WHERE batch_id = %s
                """,
                (
                    total_records,
                    success_count,
                    error_count,
                    batch_status,
                    batch_id,
                ),
            )

        # 7. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {
                "batch_id": batch_id,
                "total_records": total_records,
                "success_count": success_count,
                "error_count": error_count,
                "batch_status": batch_status,
            },
        )

    except Exception as ex:
        # 8. System exception (blob_file_data is never included in the error message)
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_filter_bulk_upload_batch(
    uuid_caller_user_id,
    str_caller_role,
    uuid_batch_id,
    uuid_uploaded_by,
    str_file_name,
    str_batch_status,
    date_from_date,
    date_to_date,
    int_page_no,
    int_page_size,
    str_sort_by,
    str_sort_order,
):
    try:
        # 1. Validate input
        if uuid_batch_id and not _is_valid_uuid(uuid_batch_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_uploaded_by and not _is_valid_uuid(uuid_uploaded_by):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if str_batch_status and str_batch_status not in VALID_BATCH_STATUSES:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        parsed_from_date = None
        parsed_to_date = None
        if date_from_date:
            parsed_from_date = _parse_date(date_from_date)
            if parsed_from_date is None:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if date_to_date:
            parsed_to_date = _parse_date(date_to_date)
            if parsed_to_date is None:
                return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        int_page_no = 1 if int_page_no is None else int_page_no
        int_page_size = 20 if int_page_size is None else int_page_size
        if not isinstance(int_page_no, int) or int_page_no < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if not isinstance(int_page_size, int) or int_page_size < 1:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        str_sort_by = str_sort_by or "created_on"
        if str_sort_by not in SORTABLE_BATCH_COLUMNS:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        str_sort_order = (str_sort_order or "DESC").upper()
        if str_sort_order not in ("ASC", "DESC"):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve dependent lookup: none required beyond caller's own role
            # 4. Core DB operation: dynamic WHERE clause + pagination
            where_clauses = []
            values = []
            if uuid_batch_id:
                where_clauses.append("batch_id = %s")
                values.append(uuid_batch_id)
            if uuid_uploaded_by:
                where_clauses.append("uploaded_by = %s")
                values.append(uuid_uploaded_by)
            if str_file_name:
                where_clauses.append("file_name ILIKE %s")
                values.append(f"%{str_file_name}%")
            if str_batch_status:
                where_clauses.append("batch_status = %s")
                values.append(str_batch_status)
            if parsed_from_date:
                where_clauses.append("created_on >= %s")
                values.append(datetime.combine(parsed_from_date, datetime.min.time()))
            if parsed_to_date:
                where_clauses.append("created_on < %s")
                values.append(datetime.combine(parsed_to_date + timedelta(days=1), datetime.min.time()))

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) AS total_count FROM bulk_upload_batch {where_sql}", tuple(values))
            total_count = cursor.fetchone()["total_count"]

            offset = (int_page_no - 1) * int_page_size
            cursor.execute(
                f"SELECT * FROM bulk_upload_batch {where_sql} ORDER BY {str_sort_by} {str_sort_order} LIMIT %s OFFSET %s",
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
