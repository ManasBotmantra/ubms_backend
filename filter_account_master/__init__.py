"""Azure Function to filter account_master records by optional criteria."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.account_master import f_filter_account_master

RECOGNIZED_FILTER_FIELDS = {
    "account_id",
    "account_number",
    "account_name",
    "utility_category_name",
    "provider_name",
    "account_status",
    "page_no",
    "page_size",
}


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Filters account_master records based on optional criteria; returns all records
    (paginated) if no filter values are supplied.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body:
        {
            "account_id": "string",              # Optional, exact match
            "account_number": "string",          # Optional, partial match
            "account_name": "string",            # Optional, partial match
            "utility_category_name": "string",   # Optional
            "provider_name": "string",           # Optional
            "account_status": "string",          # Optional: Active|Inactive
            "page_no": 1,                         # Optional, default 1
            "page_size": 20                       # Optional, default 20
        }

    Returns:
        JSON response with status_code, status_description, error_message, and data
        (data.records is the list of matching rows, plus page_no/page_size/total_count).
    """
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(req)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail("filter_account_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("filter_account_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Extract optional filter fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}
        if not isinstance(request_body, dict):
            request_body = {}

        if not set(request_body.keys()) <= RECOGNIZED_FILTER_FIELDS:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("filter_account_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        uuid_account_id = request_body.get("account_id")
        str_account_number = request_body.get("account_number")
        str_account_name = request_body.get("account_name")
        str_utility_category_name = request_body.get("utility_category_name")
        str_provider_name = request_body.get("provider_name")
        str_account_status = request_body.get("account_status")
        int_page_no = request_body.get("page_no", 1)
        int_page_size = request_body.get("page_size", 20)

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("filter_account_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_filter_account_master(
            uuid_caller_user_id,
            str_caller_role,
            uuid_account_id,
            str_account_number,
            str_account_name,
            str_utility_category_name,
            str_provider_name,
            str_account_status,
            int_page_no,
            int_page_size,
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail("filter_account_master", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("filter_account_master", uuid_caller_user_id, request_body, None, response.status_code)
        return response
