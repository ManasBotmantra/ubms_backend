"""Azure Function to create a new unit in unit_master."""

import azure.functions as func

from common_functions.auth import validate_auth_token
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.unit_master import f_create_unit_master

REQUIRED_FIELDS = ["unit_code", "unit_name"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Creates a new unit in unit_master.

    Request:
        Headers:
            - request_type = application/json
            - authorize_token = str

        Body:
        {
            "unit_code": "string",
            "unit_name": "string",
            "address": "string",       # Optional
            "unit_status": "string"    # Optional: Active|Inactive, defaults to Active
        }

    Returns:
        JSON response with status_code, status_description, error_message, and data.
    """
    authorize_token = req.headers.get("authorize_token")
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(authorize_token)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            log_api_audit_trail("create_unit_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Validate request body against required fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        missing_fields = [field for field in REQUIRED_FIELDS if not request_body.get(field)]
        if missing_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("create_unit_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        str_unit_code = request_body.get("unit_code")
        str_unit_name = request_body.get("unit_name")
        str_address = request_body.get("address")
        str_unit_status = request_body.get("unit_status", "Active")

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("create_unit_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_create_unit_master(
            uuid_caller_user_id, str_caller_role, str_unit_code, str_unit_name, str_address, str_unit_status
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail("create_unit_master", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("create_unit_master", uuid_caller_user_id, request_body, None, response.status_code)
        return response
