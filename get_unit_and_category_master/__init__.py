"""Azure Function to fetch master lists of active units and utility categories."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.reports import f_get_unit_and_category_master


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns active units and utility categories master data.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body (Optional for POST, or query params for GET):
        {
            "unit_name": ["string", "string"], # Optional (string or list of strings)
            "category": ["string", "string"]   # Optional (string or list of strings)
        }

    Returns:
        JSON response with status_code, status_description, error_message, and data
        containing 'units', 'categories', 'total_units', and 'total_categories'.
    """
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(req)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail("get_unit_and_category_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("get_unit_and_category_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Extract optional filter parameters (from body or query params)
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        str_unit_name = (
            request_body.get("unit_name")
            or request_body.get("unit_id")
            or request_body.get("unit_code")
            or req.params.get("unit_name")
            or req.params.get("unit_id")
            or req.params.get("unit_code")
        )
        str_category = (
            request_body.get("category")
            or request_body.get("utility_category_name")
            or req.params.get("category")
        )

        # 3. RBAC check: Administrator, Leadership, Unit Accountant allowed
        if str_caller_role not in ("Administrator", "Leadership", "Unit Accountant"):
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("get_unit_and_category_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_get_unit_and_category_master(
            uuid_caller_user_id,
            str_caller_role,
            str_unit_name,
            str_category,
        )

        # 5. Build response
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log
        log_api_audit_trail("get_unit_and_category_master", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("get_unit_and_category_master", uuid_caller_user_id, request_body, None, response.status_code)
        return response
