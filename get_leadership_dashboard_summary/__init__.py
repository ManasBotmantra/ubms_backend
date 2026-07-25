"""Azure Function: Leadership dashboard summary.

Server-computed org-wide analytics (read-only oversight), Leadership-only. Standard pattern
(auth -> RBAC -> mapping fn -> response -> audit). Optional filters in the body:
utility_category_name, date_from_date, date_to_date.
"""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.dashboard import f_get_leadership_dashboard_summary


def main(req: func.HttpRequest) -> func.HttpResponse:
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(req)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail("get_leadership_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("get_leadership_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. RBAC check: Leadership only
        if str_caller_role != "Leadership":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("get_leadership_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 3. Extract optional filters
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}
        str_utility_category_name = request_body.get("utility_category_name")
        date_from_date = request_body.get("date_from_date")
        date_to_date = request_body.get("date_to_date")

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_get_leadership_dashboard_summary(
            uuid_caller_user_id, str_caller_role, str_utility_category_name, date_from_date, date_to_date
        )

        # 5. Build response (internal 400 -> client 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log
        log_api_audit_trail("get_leadership_dashboard_summary", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        response = handle_system_exception(ex)
        log_api_audit_trail("get_leadership_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code)
        return response
