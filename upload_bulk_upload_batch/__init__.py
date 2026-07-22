"""Azure Function to upload a bulk file and create a bulk_upload_batch record."""

import azure.functions as func

from common_functions.auth import validate_auth_token
from common_functions.audit import log_api_audit_trail
from common_functions.response import build_api_response, handle_system_exception
from common_functions import config

from admin_functions.bulk_upload_batch import f_upload_bulk_upload_batch

REQUIRED_FIELDS = ["file_name", "file_data"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Uploads a bulk file (base64-encoded) and creates a bulk_upload_batch record.

    Request:
        Headers:
            - request_type = application/json
            - authorize_token = str

        Body:
        {
            "file_name": "string",
            "file_data": "string"  # base64-encoded file contents
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
            log_api_audit_trail("upload_bulk_upload_batch", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 2. Validate request body against required fields
        try:
            request_body = req.get_json()
        except ValueError:
            request_body = {}

        # file_data is never logged (raw file contents)
        log_request_body = {key: value for key, value in request_body.items() if key != "file_data"}

        missing_fields = [field for field in REQUIRED_FIELDS if not request_body.get(field)]
        if missing_fields:
            response = build_api_response(config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None)
            log_api_audit_trail("upload_bulk_upload_batch", uuid_caller_user_id, log_request_body, None, response.status_code)
            return response

        str_file_name = request_body.get("file_name")
        blob_file_data = request_body.get("file_data")

        # 3. RBAC check: only Administrator may bulk upload (FR-DAT-004)
        if str_caller_role != "Administrator":
            response = build_api_response(config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None)
            log_api_audit_trail("upload_bulk_upload_batch", uuid_caller_user_id, log_request_body, None, response.status_code)
            return response

        # 4. Call mapping function
        int_status_code, str_status_description, str_error_message, json_response_body = f_upload_bulk_upload_batch(
            uuid_caller_user_id, str_caller_role, str_file_name, blob_file_data
        )

        # 5. Build response (f_* returns 400 for its own System Exception case -> translate to 500)
        if int_status_code == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
            response = handle_system_exception(str_error_message)
        else:
            response = build_api_response(int_status_code, str_status_description, str_error_message, json_response_body)

        # 6. Audit log (authorize_token and file_data excluded)
        log_api_audit_trail(
            "upload_bulk_upload_batch", uuid_caller_user_id, log_request_body, json_response_body, response.status_code
        )

        return response

    except Exception as ex:
        # 7. Unhandled exception
        response = handle_system_exception(ex)
        log_api_audit_trail("upload_bulk_upload_batch", uuid_caller_user_id, request_body, None, response.status_code)
        return response
