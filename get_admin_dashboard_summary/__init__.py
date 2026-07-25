"""Azure Function to get the Administrator dashboard summary."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.dashboard import f_get_admin_dashboard_summary


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns Administrator dashboard summary data: total unit count, total account
    count with a utility-category breakdown, and pending bill approvals (count +
    total allocated amount).

    date_from_date/date_to_date only scope the pending_approvals_summary section --
    unit_summary and account_summary always reflect current totals (optionally
    narrowed by unit_id/account_id/utility_category_name) regardless of date filters,
    since unit_master/account_master have no date dimension.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body:
        {
            "unit_id": "string",                 # Optional
            "account_id": "string",               # Optional
            "utility_category_name": "string",     # Optional
            "date_from_date": "YYYY-MM-DD",         # Optional, scopes pending_approvals_summary only
            "date_to_date": "YYYY-MM-DD"             # Optional, scopes pending_approvals_summary only
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
            log_api_audit_trail(
                "get_admin_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code
            )
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("get_admin_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Extract optional filter fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        uuid_unit_id = request_body.get("unit_id")
        uuid_account_id = request_body.get("account_id")
        str_utility_category_name = request_body.get("utility_category_name")
        date_from_date = request_body.get("date_from_date")
        date_to_date = request_body.get("date_to_date")

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail(
                "get_admin_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code
            )
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_get_admin_dashboard_summary(
            uuid_caller_user_id,
            str_caller_role,
            uuid_unit_id,
            uuid_account_id,
            str_utility_category_name,
            date_from_date,
            date_to_date,
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail(
            "get_admin_dashboard_summary", uuid_caller_user_id, request_body, json_response_body, response.status_code
        )

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail(
            "get_admin_dashboard_summary", uuid_caller_user_id, request_body, None, response.status_code
        )
        return response
