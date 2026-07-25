"""Azure Function implementing the OAuth 2.0 Token Revocation Endpoint (RFC 7009).

POST /api/oauth/revoke
    token=<refresh_token>&token_type_hint=refresh_token
    + client authentication (Basic or client_secret_post)

Lets a client — an MCP agent, a Copilot connector, or a signing-out frontend —
hand back a token it no longer needs, instead of leaving a valid session alive
until it expires on its own.

Two behaviours are required by the spec and are easy to get wrong:

- RFC 7009 s2.2: the endpoint returns 200 for an invalid, expired, already-revoked
  or simply unknown token. Only client authentication failure is an error. This is
  deliberate: a revocation endpoint that answered 404 for unknown tokens would be a
  free oracle for testing whether a stolen token is live.
- Revocation is scoped to the authenticated client. A client may only revoke a
  token that was issued to it.

Access tokens are self-contained JWTs and cannot be revoked before they expire.
Revoking the refresh token stops the session from being renewed, which is the
meaningful action; the short access-token lifetime bounds the rest.
"""

import base64
import binascii
import json
import logging
import urllib.parse

import azure.functions as func

from admin_functions.auth_session import f_revoke_session_for_client
from admin_functions.oauth_db import (
    OAuthConfigurationError,
    get_oauth_client,
    verify_client_secret,
)
from common_functions.audit import log_api_audit_trail

logger = logging.getLogger("oauth_revoke")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _parse_params(req: func.HttpRequest) -> dict:
    """Reads the form-urlencoded (or JSON) body. Never the query string — a token
    in a URL ends up in access logs."""
    params: dict = {}
    try:
        body_bytes = req.get_body()
    except Exception:
        return params
    if not body_bytes:
        return params

    content_type = (req.headers.get("Content-Type") or "").lower()
    if "json" in content_type:
        try:
            data = json.loads(body_bytes.decode("utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if v is not None and not isinstance(v, (dict, list)):
                        params[k] = str(v)
        except Exception:
            logger.warning("oauth_revoke: unparseable JSON body")
        return params

    try:
        parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8"), keep_blank_values=True)
        for k, v in parsed.items():
            if v:
                params[k] = v[0]
    except Exception:
        logger.warning("oauth_revoke: unparseable form body")
    return params


def _client_credentials(req: func.HttpRequest, params: dict):
    """HTTP Basic takes precedence over client_secret_post (RFC 6749 s2.3.1)."""
    auth_header = req.headers.get("Authorization") or ""
    if auth_header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
            if ":" in decoded:
                cid, secret = decoded.split(":", 1)
                return urllib.parse.unquote(cid), urllib.parse.unquote(secret)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            logger.warning("oauth_revoke: malformed Basic authorization header")
    return params.get("client_id"), params.get("client_secret")


def _response(status_code: int, body: dict | None = None) -> func.HttpResponse:
    headers = dict(CORS_HEADERS)
    headers["Content-Type"] = "application/json;charset=UTF-8"
    if status_code == 401:
        headers["WWW-Authenticate"] = 'Basic realm="oauth"'
    return func.HttpResponse(
        body=json.dumps(body) if body is not None else "",
        status_code=status_code,
        headers=headers,
    )


def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse("", status_code=204, headers=CORS_HEADERS)

    params = _parse_params(req)

    # ---- Client authentication -----------------------------------------
    client_id, client_secret = _client_credentials(req, params)
    if not client_id:
        log_api_audit_trail("oauth_revoke", None, params, {"error": "invalid_client"}, 401)
        return _response(401, {"error": "invalid_client",
                               "error_description": "client_id is required."})

    try:
        client = get_oauth_client(client_id)
    except OAuthConfigurationError:
        logger.exception("oauth_revoke: client registry unavailable")
        return _response(503, {"error": "temporarily_unavailable",
                               "error_description": "Service temporarily unavailable."})

    if client is None or not verify_client_secret(client, client_secret):
        logger.warning("oauth_revoke: client authentication failed for client_id=%r", client_id)
        log_api_audit_trail("oauth_revoke", None, params, {"error": "invalid_client"}, 401)
        return _response(401, {"error": "invalid_client",
                               "error_description": "Client authentication failed."})

    # ---- Revoke ---------------------------------------------------------
    token = params.get("token")
    if not token:
        # A missing token parameter is a malformed request, unlike an unknown one.
        return _response(400, {"error": "invalid_request",
                               "error_description": "token parameter is required."})

    try:
        revoked = f_revoke_session_for_client(token, client_id)
    except Exception:
        logger.exception("oauth_revoke: revocation failed")
        return _response(503, {"error": "temporarily_unavailable",
                               "error_description": "Service temporarily unavailable."})

    # 200 either way, per RFC 7009 s2.2. `revoked` only shapes the audit entry.
    log_api_audit_trail("oauth_revoke", None, params, {"revoked": revoked}, 200)
    return _response(200)
