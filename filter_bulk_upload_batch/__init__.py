"""Azure Function to filter bulk_upload_batch records by optional criteria."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.bulk_upload_batch import f_filter_bulk_upload_batch


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Filters bulk_upload_batch records based on optional criteria; returns all records
    (paginated) if no filter values are supplied.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body:
        {
            "batch_id": "string",        # Optional
            "uploaded_by": "string",     # Optional
            "file_name": "string",       # Optional, partial match
            "batch_status": "string",    # Optional
            "from_date": "YYYY-MM-DD",   # Optional
            "to_date": "YYYY-MM-DD",     # Optional
            "page_no": 1,                 # Optional, default 1
            "page_size": 20,              # Optional, default 20
            "sort_by": "created_on",      # Optional, default created_on
            "sort_order": "DESC"          # Optional, default DESC
        }

    Returns:
        JSON response with status_code, status_description, error_message, and data.
    """
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(req)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail("filter_bulk_upload_batch", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("filter_bulk_upload_batch", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Extract optional filter fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        uuid_batch_id = request_body.get("batch_id")
        uuid_uploaded_by = request_body.get("uploaded_by")
        str_file_name = request_body.get("file_name")
        str_batch_status = request_body.get("batch_status")
        date_from_date = request_body.get("from_date")
        date_to_date = request_body.get("to_date")
        int_page_no = request_body.get("page_no", 1)
        int_page_size = request_body.get("page_size", 20)
        str_sort_by = request_body.get("sort_by", "created_on")
        str_sort_order = request_body.get("sort_order", "DESC")

        # 3. RBAC check: Administrator only (FR-DAT-004)
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("filter_bulk_upload_batch", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_filter_bulk_upload_batch(
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
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail(
            "filter_bulk_upload_batch", uuid_caller_user_id, request_body, json_response_body, response.status_code
        )

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("filter_bulk_upload_batch", uuid_caller_user_id, request_body, None, response.status_code)
        return response
