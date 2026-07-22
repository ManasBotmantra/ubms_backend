"""Azure Function to resolve a rejected bill_unit_details allocation via approval_history."""

import azure.functions as func

from common_functions.auth import validate_auth_token
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.approval_history import f_resolve_approval_history

REQUIRED_FIELDS = ["allocation_id", "resolution_action"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Resolves a rejected bill_unit_details allocation by recording the resolution
    action in approval_history. Administrator only (FR-APR-006).

    Request:
        Headers:
            - request_type = application/json
            - authorize_token = str

        Body:
        {
            "allocation_id": "string",
            "resolution_action": "string",
            "reason": "string"  # Optional
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
        str_resolution_action = request_body.get("resolution_action")
        str_reason = request_body.get("reason")

        # 3. RBAC check: Administrator only (FR-APR-006)
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_resolve_approval_history(
            uuid_caller_user_id, str_caller_role, uuid_allocation_id, str_resolution_action, str_reason
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
