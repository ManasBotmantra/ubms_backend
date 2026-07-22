"""Azure Function to log out by revoking an auth session's refresh token."""

import azure.functions as func

from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.auth_session import f_delete_auth_session

REQUIRED_FIELDS = ["refresh_token"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Logs out by revoking the auth session associated with a refresh token.

    Request:
        Headers:
            - request_type = application/json

        Body:
        {
            "refresh_token": "string"
        }

    Returns:
        JSON response with status_code, status_description, error_message, and data.
    """
    # Logout: authenticated by the refresh_token in the body, not an
    # authorize_token header -- no validate_auth_token/RBAC step here either.
    request_body = {}

    try:
        # 1. Validate request body against required fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        missing_fields = [field for field in REQUIRED_FIELDS if not request_body.get(field)]
        if missing_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("delete_auth_session", None, request_body, None, response.status_code)
            return response

        str_refresh_token = request_body.get("refresh_token")

        # 2. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_delete_auth_session(
            str_refresh_token
        )

        # 3. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 4. Audit log (refresh_token excluded via redaction)
        log_api_audit_trail("delete_auth_session", None, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        # 5. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("delete_auth_session", None, request_body, None, response.status_code)
        return response
