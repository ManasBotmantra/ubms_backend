"""Azure Function to filter bill records by date range, unit name, and utility category."""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.reports import f_filter_bills_by_unit_category


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Filters bill records based on from_date, to_date, unit_name, and category.
    Returns paginated matching bill records joined with unit and account details.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body:
        {
            "from_date": "YYYY-MM-DD",                 # Optional (also accepts "date_from" / "bill_date_from")
            "to_date": "YYYY-MM-DD",                   # Optional (also accepts "date_to" / "bill_date_to")
            "unit_name": ["string", "string"],         # Optional (string or list of strings)
            "category": ["string", "string"],          # Optional (string or list of strings, e.g. ["Water", "Electricity"])
            "page_no": 1,                              # Optional, default 1
            "page_size": 20,                           # Optional, default 20
            "sort_by": "bill_date",                    # Optional, default bill_date
            "sort_order": "DESC"                       # Optional, default DESC
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
            log_api_audit_trail("filter_bills_by_unit_category", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("filter_bills_by_unit_category", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Extract filter fields from request body
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        date_from_date = (
            request_body.get("from_date")
            or request_body.get("date_from")
            or request_body.get("bill_date_from")
        )
        date_to_date = (
            request_body.get("to_date")
            or request_body.get("date_to")
            or request_body.get("bill_date_to")
        )
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

        int_page_no = request_body.get("page_no", 1)
        int_page_size = request_body.get("page_size", 20)
        str_sort_by = request_body.get("sort_by", "bill_date")
        str_sort_order = request_body.get("sort_order", "DESC")

        # 3. RBAC check: Administrator, Leadership, Unit Accountant allowed
        if str_caller_role not in ("Administrator", "Leadership", "Unit Accountant"):
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("filter_bills_by_unit_category", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_filter_bills_by_unit_category(
            uuid_caller_user_id,
            str_caller_role,
            date_from_date,
            date_to_date,
            str_unit_name,
            str_category,
            int_page_no,
            int_page_size,
            str_sort_by,
            str_sort_order,
        )

        # 5. Build response
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log
        log_api_audit_trail("filter_bills_by_unit_category", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("filter_bills_by_unit_category", uuid_caller_user_id, request_body, None, response.status_code)
        return response
