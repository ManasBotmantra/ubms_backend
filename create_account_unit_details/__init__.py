"""Azure Function to create a new account-to-unit allocation mapping in account_unit_details."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_WRITE
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.account_unit_details import f_create_account_unit_details

REQUIRED_FIELDS = ["account_id", "unit_allocations"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Creates a new account-to-unit allocation mapping in account_unit_details.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body:
        {
            "account_id": "string",
            "unit_allocations": [
                {
                    "unit_id": "string",
                    "allocation_percentage": 100.00
                }
            ]  # at least 2 entries, allocation_percentage across all entries must sum to exactly 100.00
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
            log_api_audit_trail("create_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_WRITE):
            response = build_insufficient_scope_response(SCOPE_WRITE)
            log_api_audit_trail("create_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Validate request body against required fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        missing_fields = [field for field in REQUIRED_FIELDS if not request_body.get(field)]
        if missing_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("create_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        uuid_account_id = request_body.get("account_id")
        json_unit_allocations = request_body.get("unit_allocations")

        # unit_allocations must be an array of at least 2 entries, each with unit_id + allocation_percentage,
        # and SUM(allocation_percentage) across all entries must equal exactly 100.00 (FR-BIF-002/BR-002)
        is_malformed = not isinstance(json_unit_allocations, list) or len(json_unit_allocations) < 2
        total_allocation = 0.0
        if not is_malformed:
            for entry in json_unit_allocations:
                if (
                    not isinstance(entry, dict)
                    or not entry.get("unit_id")
                    or entry.get("allocation_percentage") is None
                    or not isinstance(entry.get("allocation_percentage"), (int, float))
                ):
                    is_malformed = True
                    break
                total_allocation += float(entry.get("allocation_percentage"))

        if is_malformed or round(total_allocation, 2) != 100.00:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("create_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("create_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_create_account_unit_details(
            uuid_caller_user_id,
            str_caller_role,
            uuid_account_id,
            json_unit_allocations,
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail(
            "create_account_unit_details", uuid_caller_user_id, request_body, json_response_body, response.status_code
        )

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("create_account_unit_details", uuid_caller_user_id, request_body, None, response.status_code)
        return response
