"""Azure Function to get the account bifurcation breakdown report."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.reports import f_get_account_bifurcation_breakdown_report

REQUIRED_FIELDS = ["account_id"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns the account bifurcation breakdown report for an account, optionally
    filtered by billing period.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body:
        {
            "account_id": "string",
            "billing_period": "string",  # Optional
            "page_no": 1,                 # Optional, default 1
            "page_size": 20,              # Optional, default 20
            "sort_by": "string",          # Optional
            "sort_order": "string"        # Optional
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
                "get_account_bifurcation_breakdown_report", uuid_caller_user_id, request_body, None, response.status_code
            )
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("get_account_bifurcation_breakdown_report", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Validate request body against required fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        missing_fields = [field for field in REQUIRED_FIELDS if not request_body.get(field)]
        if missing_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail(
                "get_account_bifurcation_breakdown_report", uuid_caller_user_id, request_body, None, response.status_code
            )
            return response

        uuid_account_id = request_body.get("account_id")
        date_billing_period = request_body.get("billing_period")
        int_page_no = request_body.get("page_no", 1)
        int_page_size = request_body.get("page_size", 20)
        str_sort_by = request_body.get("sort_by")
        str_sort_order = request_body.get("sort_order")

        # 3. RBAC check: Leadership only
        if str_caller_role != "Leadership":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail(
                "get_account_bifurcation_breakdown_report", uuid_caller_user_id, request_body, None, response.status_code
            )
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = (
            f_get_account_bifurcation_breakdown_report(
                uuid_caller_user_id,
                str_caller_role,
                uuid_account_id,
                date_billing_period,
                int_page_no,
                int_page_size,
                str_sort_by,
                str_sort_order,
            )
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail(
            "get_account_bifurcation_breakdown_report", uuid_caller_user_id, request_body, json_response_body, response.status_code
        )

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail(
            "get_account_bifurcation_breakdown_report", uuid_caller_user_id, request_body, None, response.status_code
        )
        return response
