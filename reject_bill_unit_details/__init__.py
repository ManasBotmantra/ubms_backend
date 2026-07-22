"""Azure Function to reject a bill_unit_details allocation."""

import azure.functions as func

from common_functions.auth import validate_auth_token
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.bill_unit_details import f_reject_bill_unit_details

REQUIRED_FIELDS = ["allocation_id", "reason"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Rejects a bill_unit_details allocation. Only the Unit Accountant assigned to
    the allocation's unit may reject it (NFR-001, checked inside the mapping function).

    Request:
        Headers:
            - request_type = application/json
            - authorize_token = str

        Body:
        {
            "allocation_id": "string",
            "reason": "string"
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
            return response

        # 2. Validate request body against required fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        missing_fields = [field for field in REQUIRED_FIELDS if not request_body.get(field)]
        if missing_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            return response

        uuid_allocation_id = request_body.get("allocation_id")
        str_reason = request_body.get("reason")

        # 3. RBAC check: only Unit Accountant may call this API (FR-APR-002). The
        # unit-assignment scoping check (NFR-001) requires resolving the allocation's
        # unit_id, which needs DB access -- that is performed inside
        # f_reject_bill_unit_details, the only layer with a DB cursor.
        if str_caller_role != "Unit Accountant":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_reject_bill_unit_details(
            uuid_caller_user_id, str_caller_role, uuid_allocation_id, str_reason
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. No audit_log entry: approve/reject/resolve actions are intentionally excluded
        # here since they're already captured in approval_history_log.
        return response

    except Exception as ex:
        # 7. Unhandled exception
        return handle_system_exception(ex)
