"""Azure Function returning OAuth 2.0 Authorization Server Metadata (RFC 8414 / OIDC discovery)."""

import json
import os
import azure.functions as func
from dotenv import load_dotenv

load_dotenv()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, authorize_token",
    "Content-Type": "application/json;charset=UTF-8",
    "Cache-Control": "public, max-age=3600",
}


def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse("", status_code=204, headers=CORS_HEADERS)

    # Determine base host URL dynamically or from environment
    host_url = os.environ.get("BASE_URL")
    if not host_url:
        req_url = req.url
        # Strip path and query to get scheme://host:port
        host_url = req_url.split("/.well-known/")[0].rstrip("/")

    api_base = host_url if host_url.endswith("/api") else f"{host_url}/api"
    issuer = host_url[:-4] if host_url.endswith("/api") else host_url

    # This document must describe what the server ACTUALLY does. Advertising
    # capabilities that aren't implemented (or that are implemented but unsafe)
    # invites clients to negotiate down to them:
    #   - "plain" PKCE removed: the server only accepts S256, and advertising plain
    #     invites a downgrade that puts the verifier in the URL in cleartext.
    #   - id_token_signing_alg_values_supported removed: no id_token is issued, and
    #     listing HS256 alongside RS256 is the classic algorithm-confusion setup.
    #   - "openid"/"profile" removed from scopes: this is not an OIDC provider.
    metadata = {
        "issuer": issuer,
        "authorization_endpoint": f"{api_base}/oauth/authorize",
        "token_endpoint": f"{api_base}/oauth/token",
        "revocation_endpoint": f"{api_base}/oauth/revoke",
        "jwks_uri": f"{api_base}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "password", "refresh_token"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
            "none"
        ],
        "revocation_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post"
        ],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["read", "write"],
        "subject_types_supported": ["public"],
        # Deliberately absent: registration_endpoint. Client registration is
        # manual (a SQL INSERT) by choice, so no RFC 7591 endpoint is advertised.
        # A client that requires Dynamic Client Registration must be added by hand.
    }

    return func.HttpResponse(
        body=json.dumps(metadata, indent=2),
        status_code=200,
        headers=CORS_HEADERS,
    )
