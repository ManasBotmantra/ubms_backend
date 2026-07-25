"""Azure Function to update an existing account in account_master based on search and update fields."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_WRITE
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.account_master import f_update_account_master


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Updates an existing account in account_master based on search and update fields.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body:
        {
            "search_fields": {
                "account_id": "string",       # at least one of account_id/account_number required
                "account_number": "string"
            },
            "update_fields": {
                "account_name": "string",             # at least one update field required
                "utility_category_name": "string",
                "provider_name": "string",
                "provider_contact_email": "string",
                "provider_contact_phone": "string",
                "account_status": "string"
            }
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
            log_api_audit_trail("update_account_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_WRITE):
            response = build_insufficient_scope_response(SCOPE_WRITE)
            log_api_audit_trail("update_account_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Extract search_fields / update_fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        search_fields = request_body.get("search_fields") or {}
        update_fields = request_body.get("update_fields") or {}

        if not search_fields or not update_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("update_account_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("update_account_master", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_update_account_master(
            uuid_caller_user_id, str_caller_role, search_fields, update_fields
        )

        # 5. Build response (f_* reuses 403 for config.ERR_NO_RECORD_FOUND -> translate to 404;
        #    f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        elif int_status_code == 403 and str_error_message == config.ERR_NO_RECORD_FOUND:
            response = build_api_response(config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, "", None)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail("update_account_master", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("update_account_master", uuid_caller_user_id, request_body, None, response.status_code)
        return response
