"""Azure Function to get the bill status report."""

import azure.functions as func

from common_functions.auth import validate_auth_token
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.reports import f_get_bill_status_report


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns the bill status report, optionally filtered by account, unit, and
    bill status.

    Request:
        Headers:
            - request_type = application/json
            - authorize_token = str

        Body:
        {
            "account_id": "string",     # Optional
            "unit_id": "string",        # Optional
            "bill_status": "string",    # Optional
            "page_no": 1,                 # Optional, default 1
            "page_size": 20,              # Optional, default 20
            "sort_by": "string",           # Optional
            "sort_order": "string"          # Optional
        }

    Returns:
        JSON response with status_code, status_description, error_message, and data.
    """
    authorize_token = req.headers.get("authorize_token")
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(authorize_token)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail(
                "get_bill_status_report", uuid_caller_user_id, request_body, None, response.status_code
            )
            return response

        # 2. Extract optional filter fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        uuid_account_id = request_body.get("account_id")
        uuid_unit_id = request_body.get("unit_id")
        str_bill_status = request_body.get("bill_status")
        int_page_no = request_body.get("page_no", 1)
        int_page_size = request_body.get("page_size", 20)
        str_sort_by = request_body.get("sort_by")
        str_sort_order = request_body.get("sort_order")

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail(
                "get_bill_status_report", uuid_caller_user_id, request_body, None, response.status_code
            )
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_get_bill_status_report(
            uuid_caller_user_id,
            str_caller_role,
            uuid_account_id,
            uuid_unit_id,
            str_bill_status,
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
            "get_bill_status_report", uuid_caller_user_id, request_body, json_response_body, response.status_code
        )

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail(
            "get_bill_status_report", uuid_caller_user_id, request_body, None, response.status_code
        )
        return response
