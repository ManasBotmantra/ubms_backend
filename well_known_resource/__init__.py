"""OAuth 2.0 Protected Resource Metadata (RFC 9728).

GET /api/.well-known/oauth-protected-resource

This is the document an MCP client fetches FIRST. It asks the API "who issues your
tokens?", follows `authorization_servers` to the authorization-server metadata, and
only then starts the OAuth flow. Without it, a client that receives a 401 has no way
to discover where to authenticate and simply gives up.

It is unauthenticated by design — it is public discovery data and contains no secrets.
"""

import json
import os

import azure.functions as func
from dotenv import load_dotenv

load_dotenv()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Content-Type": "application/json;charset=UTF-8",
    "Cache-Control": "public, max-age=3600",
}


def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse("", status_code=204, headers=CORS_HEADERS)

    host_url = os.environ.get("BASE_URL")
    if not host_url:
        host_url = req.url.split("/.well-known/")[0].rstrip("/")

    api_base = host_url if host_url.endswith("/api") else f"{host_url}/api"
    issuer = host_url[:-4] if host_url.endswith("/api") else host_url

    metadata = {
        # The resource these tokens are good for.
        "resource": api_base,
        # Where to go to get a token. Points at our own authorization server.
        "authorization_servers": [issuer],
        "scopes_supported": ["read", "write"],
        # We accept the token in the Authorization header. ("header" is the only
        # value listed because query-parameter tokens are deliberately not
        # supported — they leak into logs and Referer headers.)
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{issuer}/README.md",
    }

    return func.HttpResponse(
        body=json.dumps(metadata, indent=2),
        status_code=200,
        headers=CORS_HEADERS,
    )
