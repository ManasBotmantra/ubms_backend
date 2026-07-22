"""Azure Function: Unit Accountant dashboard summary.

Server-computed counterpart to get_admin_dashboard_summary, scoped to the caller's own active
unit assignments. Follows the standard pattern (auth -> RBAC -> mapping fn -> response -> audit);
there is no request body since the summary is entirely determined by the caller's identity.
"""

import azure.functions as func

from common_functions.auth import validate_auth_token
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.dashboard import f_get_unit_accountant_dashboard_summary


def main(req: func.HttpRequest) -> func.HttpResponse:
    authorize_token = req.headers.get("authorize_token")
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(authorize_token)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail("get_unit_accountant_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. RBAC check: Unit Accountant only (it's the accountant's own dashboard)
        if str_caller_role != "Unit Accountant":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("get_unit_accountant_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # (No request body is needed; parse defensively for audit consistency.)
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        # 3. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_get_unit_accountant_dashboard_summary(
            uuid_caller_user_id, str_caller_role
        )

        # 4. Build response (internal 400 -> client 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 5. Audit log
        log_api_audit_trail("get_unit_accountant_dashboard_summary", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        response = handle_system_exception(ex)
        log_api_audit_trail("get_unit_accountant_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code)
        return response
