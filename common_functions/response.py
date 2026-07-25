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


def build_insufficient_scope_response(required_scope):
    """403 for a token that authenticated fine but wasn't granted enough scope.

    RFC 6750 s3.1 says a scope failure must carry a WWW-Authenticate challenge
    naming the required scope, so a well-behaved OAuth client can tell this apart
    from "your token is bad" and re-request authorization with the right scope
    instead of pointlessly refreshing.
    """
    response = build_api_response(
        config.STATUS_AUTHORIZATION_FAILURE,
        config.DESC_AUTHORIZATION_FAILURE,
        config.ERR_INSUFFICIENT_SCOPE,
        None,
    )
    response.headers["WWW-Authenticate"] = (
        f'Bearer error="insufficient_scope", scope="{required_scope}"'
    )
    return response


def handle_system_exception(exception):
    str_error_message = str(exception)
    return build_api_response(config.STATUS_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str_error_message, None)
