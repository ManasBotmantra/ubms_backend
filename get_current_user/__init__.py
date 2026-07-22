"""Azure Function returning the authenticated caller's own profile, role, and unit assignments.

Authenticated but role-agnostic (any valid session), so it deliberately skips the RBAC step of
the standard 7-step pattern -- a session is always allowed to read its own identity. It exists
because role is never in the JWT and the master-data lookups that carry role/unit info are
Administrator-only, so a non-Administrator client has no other way to learn who it is.
"""

import azure.functions as func

from common_functions.auth import validate_auth_token
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.current_user import f_get_current_user


def main(req: func.HttpRequest) -> func.HttpResponse:
    authorize_token = req.headers.get("authorize_token")
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(authorize_token)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail("get_current_user", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. No request body / RBAC to check -- any authenticated role may read its own identity.
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        # 3. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_get_current_user(
            uuid_caller_user_id, str_caller_role
        )

        # 4. Build response
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 5. Audit log (full_name/email are redacted by log_api_audit_trail)
        log_api_audit_trail("get_current_user", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        response = handle_system_exception(ex)
        log_api_audit_trail("get_current_user", uuid_caller_user_id, request_body, None, response.status_code)
        return response
