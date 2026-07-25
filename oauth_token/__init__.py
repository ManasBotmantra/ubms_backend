"""Azure Function implementing the OAuth 2.0 Token Endpoint (RFC 6749).

Supported grants:
- authorization_code : code + code_verifier (PKCE, mandatory) -> access + refresh token
- password (ROPC)    : username/email + password             -> access + refresh token
- refresh_token      : refresh_token                          -> rotated token pair

Security notes -- these are deliberate:

- The client is AUTHENTICATED on every grant. A client registered with a
  client_secret_hash must present its secret (via HTTP Basic per RFC 6749 s2.3.1, or
  client_secret_post). Previously no secret was ever checked, so every confidential
  client was effectively public and anyone holding a code could redeem it.
- redirect_uri has no default and must be supplied for the code grant, so it can be
  compared against the value bound to the code.
- Exception text is never returned to the caller. A raw str(ex) here leaked psycopg2
  messages -- table names, hostnames, connection details -- to an unauthenticated
  caller. Details go to the server logger instead.
- The audit payload is filtered before logging: see common_functions/audit.py, which
  now also redacts code, code_verifier and client_secret.
- ROPC (grant_type=password) is retained for the existing first-party login flow, but
  is restricted to clients explicitly registered for it. It is a deprecated grant
  (OAuth 2.1 removes it) -- new integrations should use authorization_code + PKCE.
"""

import base64
import binascii
import json
import logging
import urllib.parse

import azure.functions as func

from admin_functions.auth_session import (
    f_create_auth_session,
    f_create_session_for_user,
    f_refresh_auth_session,
)
from admin_functions.oauth_db import (
    OAuthConfigurationError,
    client_supports_grant,
    get_oauth_client,
    validate_and_consume_authorization_code,
    verify_client_secret,
)
from common_functions.audit import log_api_audit_trail
from common_functions.auth import SCOPE_READ, parse_scope
from common_functions import config

logger = logging.getLogger("oauth_token")

CORS_HEADERS = {
    # The token endpoint is called server-to-server or by a public client's own
    # origin. It never relies on cookies, so "*" cannot be paired with credentials.
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}

_GENERIC_INVALID_GRANT = "invalid or expired grant"


def _parse_request_params(req: func.HttpRequest) -> dict:
    """Parses a form-urlencoded or JSON body into a flat dict.

    Query-string parameters are deliberately NOT read: credentials and codes must
    travel in the request body, not the URL, so they stay out of access logs.
    """
    params: dict = {}
    content_type = (req.headers.get("Content-Type") or "").lower()

    try:
        body_bytes = req.get_body()
    except Exception:
        body_bytes = b""

    if not body_bytes:
        return params

    if "json" in content_type:
        try:
            json_data = json.loads(body_bytes.decode("utf-8"))
            if isinstance(json_data, dict):
                for k, v in json_data.items():
                    if v is not None and not isinstance(v, (dict, list)):
                        params[k] = str(v)
        except Exception:
            logger.warning("oauth_token: unparseable JSON body")
        return params

    try:
        parsed_qs = urllib.parse.parse_qs(body_bytes.decode("utf-8"), keep_blank_values=True)
        for k, v in parsed_qs.items():
            if v:
                params[k] = v[0]
    except Exception:
        logger.warning("oauth_token: unparseable form body")

    return params


def _client_credentials(req: func.HttpRequest, params: dict) -> tuple[str | None, str | None]:
    """Extracts client_id / client_secret.

    HTTP Basic (RFC 6749 s2.3.1) takes precedence over client_secret_post, which is
    the order the spec requires when both are present.
    """
    auth_header = req.headers.get("Authorization") or ""
    if auth_header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
            if ":" in decoded:
                basic_id, basic_secret = decoded.split(":", 1)
                # These arrive percent-encoded per RFC 6749 Appendix B.
                return urllib.parse.unquote(basic_id), urllib.parse.unquote(basic_secret)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            logger.warning("oauth_token: malformed Basic authorization header")

    return params.get("client_id"), params.get("client_secret")


def _build_oauth_response(status_code: int, data: dict) -> func.HttpResponse:
    """Returns a standard RFC 6749 JSON response."""
    headers = dict(CORS_HEADERS)
    headers["Content-Type"] = "application/json;charset=UTF-8"
    if status_code == 401:
        # RFC 6749 s5.2: a 401 from the token endpoint must carry a challenge.
        headers["WWW-Authenticate"] = 'Basic realm="oauth"'
    return func.HttpResponse(body=json.dumps(data), status_code=status_code, headers=headers)


def _error(error: str, description: str, status_code: int, params: dict, user_id=None) -> func.HttpResponse:
    """Builds, audits and returns an OAuth error response."""
    response_data = {"error": error, "error_description": description}
    log_api_audit_trail("oauth_token", user_id, params, response_data, status_code)
    return _build_oauth_response(status_code, response_data)


def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse("", status_code=204, headers=CORS_HEADERS)

    params = _parse_request_params(req)
    grant_type = params.get("grant_type")

    if grant_type not in ("authorization_code", "password", "refresh_token"):
        return _error(
            "unsupported_grant_type",
            "grant_type must be one of: authorization_code, password, refresh_token.",
            400,
            params,
        )

    # ---- Client authentication (all grants) --------------------------------
    client_id, client_secret = _client_credentials(req, params)
    if not client_id:
        return _error("invalid_client", "client_id is required.", 401, params)

    try:
        client = get_oauth_client(client_id)
    except OAuthConfigurationError:
        logger.exception("oauth_token: client registry unavailable")
        return _error(
            "temporarily_unavailable",
            "The authorization service is temporarily unavailable.",
            503,
            params,
        )

    if client is None:
        logger.warning("oauth_token: unknown client_id=%r", client_id)
        return _error("invalid_client", "Unknown or inactive client.", 401, params)

    if not verify_client_secret(client, client_secret):
        logger.warning("oauth_token: client authentication failed for client_id=%r", client_id)
        return _error("invalid_client", "Client authentication failed.", 401, params)

    if not client_supports_grant(client, grant_type):
        return _error(
            "unauthorized_client",
            f"This client is not registered for grant_type '{grant_type}'.",
            400,
            params,
        )

    try:
        # ---- Grant 1: authorization_code (PKCE) ----------------------------
        if grant_type == "authorization_code":
            code = params.get("code")
            redirect_uri = params.get("redirect_uri")
            code_verifier = params.get("code_verifier")

            if not code:
                return _error("invalid_request", "code is required.", 400, params)
            if not redirect_uri:
                # No default. It must match the value bound to the code.
                return _error("invalid_request", "redirect_uri is required.", 400, params)
            if not code_verifier:
                return _error("invalid_request", "code_verifier is required (PKCE).", 400, params)

            is_valid, user_id, granted_scope, err_msg = validate_and_consume_authorization_code(
                code_id=code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
            )

            if not (is_valid and user_id):
                return _error("invalid_grant", err_msg or _GENERIC_INVALID_GRANT, 400, params)

            # Persist a real session so the refresh token works and the grant is
            # revocable. This is the fix for the previously-dead refresh token.
            # The scope comes from the stored code -- what the user consented to --
            # not from this request, so the client can't widen it at exchange time.
            int_status, _desc, _err, json_body = f_create_session_for_user(
                user_id, requested_scopes=parse_scope(granted_scope), client_id=client_id
            )
            if int_status != config.STATUS_SUCCESS or not json_body:
                logger.error("oauth_token: session creation failed after valid code exchange")
                return _error(
                    "server_error",
                    "Unable to issue a token at this time.",
                    500,
                    params,
                    user_id,
                )

            log_api_audit_trail("oauth_token", user_id, params, json_body, 200)
            return _build_oauth_response(200, json_body)

        # ---- Grant 2: password (ROPC) --------------------------------------
        elif grant_type == "password":
            email = params.get("username") or params.get("email")
            password = params.get("password")

            if not email or not password:
                return _error(
                    "invalid_request",
                    "username (or email) and password are required.",
                    400,
                    params,
                )

            # Honour the requested scope; absent one, default to read-only. A
            # password-grant client is typically a script or agent, and defaulting
            # those to full write access is not a good failure mode.
            requested = parse_scope(params.get("scope")) or frozenset({SCOPE_READ})

            int_status, _desc, _err, json_body = f_create_auth_session(
                email, password, requested_scopes=requested, client_id=client_id
            )

            if int_status == config.STATUS_SUCCESS and json_body:
                log_api_audit_trail("oauth_token", None, params, json_body, 200)
                return _build_oauth_response(200, json_body)

            if int_status == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
                logger.error("oauth_token: system exception during password grant")
                return _error("server_error", "Unable to process the request.", 500, params)

            # Fixed message -- never distinguish "no such user" from "wrong password".
            return _error("invalid_grant", config.ERR_INVALID_CREDENTIALS, 400, params)

        # ---- Grant 3: refresh_token ----------------------------------------
        else:
            refresh_token = params.get("refresh_token")

            if not refresh_token:
                return _error("invalid_request", "refresh_token is required.", 400, params)

            # Bound to the client that was issued the token: a leaked refresh
            # token is useless to a different client.
            int_status, _desc, _err, json_body = f_refresh_auth_session(
                refresh_token, client_id=client_id
            )

            if int_status == config.STATUS_SUCCESS and json_body:
                log_api_audit_trail("oauth_token", None, params, json_body, 200)
                return _build_oauth_response(200, json_body)

            if int_status == config.STATUS_INTERNAL_SYSTEM_EXCEPTION:
                logger.error("oauth_token: system exception during refresh grant")
                return _error("server_error", "Unable to process the request.", 500, params)

            return _error(
                "invalid_grant",
                config.ERR_REFRESH_TOKEN_INVALID_OR_EXPIRED,
                400,
                params,
            )

    except Exception:
        # Log the detail server-side; tell the caller nothing about it.
        logger.exception("oauth_token: unhandled exception for grant_type=%r", grant_type)
        return _error("server_error", "An internal error occurred.", 500, params)
