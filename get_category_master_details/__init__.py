"""Azure Function to fetch utility category master details by name, or all active categories."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.reports import f_get_category_master_details


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns active utility category master records.

    Sending no query parameters returns all active utility categories.
    Sending a category name filters to matching categories.

    Request:
        Method: GET
        Headers:
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Query Parameters (all optional):
            - category               : str  — filter by utility category name (partial match)
            - utility_category_name  : str  — alias for category

    Returns:
        JSON response with status_code, status_description, error_message, and data
        containing 'categories' and 'total_categories'.
    """
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(req)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail("get_category_master_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("get_category_master_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Extract optional filter from query params only (GET — no body)
        str_category = (
            req.params.get("category")
            or req.params.get("utility_category_name")
        )

        # 3. RBAC check: Administrator, Leadership, Unit Accountant allowed
        if str_caller_role not in ("Administrator", "Leadership", "Unit Accountant"):
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("get_category_master_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_get_category_master_details(
            uuid_caller_user_id,
            str_caller_role,
            str_category,
        )

        # 5. Build response
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log
        log_api_audit_trail("get_category_master_details", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("get_category_master_details", uuid_caller_user_id, request_body, None, response.status_code)
        return response
