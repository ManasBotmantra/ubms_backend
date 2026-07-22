import json

import azure.functions as func

from common_functions import config


def build_api_response(int_status_code, str_status_description, str_error_message, json_response_body):
    body = {
        "status_code": int_status_code,
        "status_description": str_status_description,
        "error_message": str_error_message,
        "data": json_response_body,
    }
    return func.HttpResponse(
        body=json.dumps(body, default=str),
        status_code=int_status_code,
        mimetype="application/json",
    )


def handle_system_exception(exception):
    str_error_message = str(exception)
    return build_api_response(config.STATUS_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str_error_message, None)
