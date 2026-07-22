"""Azure Function to soft-delete (deactivate) a user-unit assignment in user_unit_details."""

import azure.functions as func

from common_functions.auth import validate_auth_token
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.user_unit_details import f_delete_user_unit_details

REQUIRED_FIELDS = ["assignment_id"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Soft-deletes (deactivates) a user-unit assignment in user_unit_details.

    Request:
        Headers:
            - request_type = application/json
            - authorize_token = str

        Body:
        {
            "assignment_id": "string"
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
            log_api_audit_trail("delete_user_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Validate request body against required fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        missing_fields = [field for field in REQUIRED_FIELDS if not request_body.get(field)]
        if missing_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("delete_user_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        uuid_assignment_id = request_body.get("assignment_id")

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("delete_user_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_delete_user_unit_details(
            uuid_caller_user_id, str_caller_role, uuid_assignment_id
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail(
            "delete_user_unit_details", uuid_caller_user_id, request_body, json_response_body, response.status_code
        )

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("delete_user_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
        return response
