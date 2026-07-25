"""Azure Function to filter account_unit_details records by optional criteria."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.account_unit_details import f_filter_account_unit_details

RECOGNIZED_FIELDS = {
    "mapping_id",
    "account_id",
    "unit_id",
    "mapping_status",
    "page_no",
    "page_size",
}


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Filters account_unit_details records based on optional criteria; returns all
    records (paginated) if no filter values are supplied.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body:
        {
            "mapping_id": "string",       # Optional, exact match
            "account_id": "string",       # Optional, exact match
            "unit_id": "string",          # Optional, exact match
            "mapping_status": "string",   # Optional
            "page_no": 1,                  # Optional, default 1
            "page_size": 20                # Optional, default 20
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
            log_api_audit_trail("filter_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("filter_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Extract optional filter fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}
        if not isinstance(request_body, dict):
            request_body = {}

        # Reject any body key that isn't a recognized account_unit_details filter/pagination field
        unrecognized_fields = set(request_body.keys()) - RECOGNIZED_FIELDS
        if unrecognized_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("filter_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        uuid_mapping_id = request_body.get("mapping_id")
        uuid_account_id = request_body.get("account_id")
        uuid_unit_id = request_body.get("unit_id")
        str_mapping_status = request_body.get("mapping_status")
        int_page_no = request_body.get("page_no", 1)
        int_page_size = request_body.get("page_size", 20)

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("filter_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_filter_account_unit_details(
            uuid_caller_user_id,
            str_caller_role,
            uuid_mapping_id,
            uuid_account_id,
            uuid_unit_id,
            str_mapping_status,
            int_page_no,
            int_page_size,
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail(
            "filter_account_unit_details", uuid_caller_user_id, request_body, json_response_body, response.status_code
        )

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("filter_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
        return response
