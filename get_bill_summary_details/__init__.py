"""Azure Function that returns a consolidated bill-domain summary for the LLM chatbot.

The chatbot backend calls this on behalf of a logged-in Administrator and hands the response
to the LLM as grounding context, so it can answer open-ended questions about bills (totals,
pending amounts, spend by category/unit, monthly trend, largest bills) without the LLM having
to choose a specific report cut.
"""

import azure.functions as func

from common_functions.auth import validate_auth_token, token_has_scope, SCOPE_READ
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, build_insufficient_scope_response, handle_system_exception
from common_functions import config

from admin_functions.bill import f_get_bill_summary_details


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns a consolidated, read-only summary of the bill domain for LLM grounding.

    Request:
        Headers:
            - request_type = application/json
            - Authorization = Bearer <access_token>   (standard; use this for OAuth clients)
            - authorize_token = str                   (legacy header, still supported)

        Body (all optional; AND-combined; billing_period scoped via day_flag):
        {
            "filters": {
                "account_id": "uuid",
                "bill_status": "Pending Approval|Approved|On Hold|Resolved",
                "utility_category_name": "str",
                "unit_id": "uuid or unit_code",
                "day_flag":   "Daily|Monthly|Quarterly|Yearly",
                "day_value":  "DD-MM-YYYY | MM-YYYY | Q1-YYYY | YYYY",
                "from_date":  "arbitrary range start, filters bill_date -- mutually exclusive with day_flag",
                "to_date":    "arbitrary range end, filters bill_date -- mutually exclusive with day_flag"
            },
            "group_by": ["bill_status", "utility_category", "account_id",
                         "billing_period", "month", "quarter", "year",
                         "bill_date", "unit_id"]
        }
        group_by is optional; when supplied, the response additionally includes a
        "grouped_results" array aggregated over those dimensions (see f_get_bill_summary_details).

    Returns:
        JSON response with status_code, status_description, error_message, and data
        (data = {metadata, kpis, by_status, by_category, by_unit, monthly_trend, top_bills}).
    """
    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(req)
        if not is_valid:
            response = build_api_response(config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_TOKEN_INVALID_OR_EXPIRED, None)
            response.headers["WWW-Authenticate"] = 'Bearer realm="UBMS", scope="read"'
            log_api_audit_trail("get_bill_summary_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 1b. Scope check -- role says WHO may act, scope says how much this
        #     particular token was granted. A read-only agent token fails here.
        if not token_has_scope(req, SCOPE_READ):
            response = build_insufficient_scope_response(SCOPE_READ)
            log_api_audit_trail("get_bill_summary_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Parse request body -- every filter is optional, so a missing/empty body is valid
        #    (an unfiltered, org-wide summary).
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}
        if not isinstance(request_body, dict):
            request_body = {}

        filters = request_body.get("filters") or {}

        uuid_account_id = filters.get("account_id")
        str_bill_status = filters.get("bill_status")
        str_utility_category_name = filters.get("utility_category_name")
        uuid_unit_id  = filters.get("unit_id")
        str_day_flag  = filters.get("day_flag")
        str_day_value = filters.get("day_value")
        str_date_from = filters.get("from_date")
        str_date_to   = filters.get("to_date")
        list_group_by = request_body.get("group_by") or []

        # 3. RBAC check: Administrator only
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("get_bill_summary_details", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_get_bill_summary_details(
            uuid_caller_user_id,
            str_caller_role,
            uuid_account_id,
            str_bill_status,
            str_utility_category_name,
            uuid_unit_id,
            str_day_flag,
            str_day_value,
            str_date_from,
            str_date_to,
            list_group_by,
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token excluded)
        log_api_audit_trail("get_bill_summary_details", uuid_caller_user_id, request_body, json_response_body, response.status_code)

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("get_bill_summary_details", uuid_caller_user_id, request_body, None, response.status_code)
        return response
