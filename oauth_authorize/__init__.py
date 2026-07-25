"""Azure Function implementing the OAuth 2.0 Authorization Endpoint (/oauth/authorize).

Flow:
1. Validate response_type, client_id, redirect_uri and PKCE parameters.
2. Authenticate the resource owner (login form, or an Authorization: Bearer header).
3. Obtain explicit consent.
4. Redirect to the *registered* redirect_uri with ?code=...&state=...

Security notes -- these are deliberate:

- client_id and redirect_uri are validated BEFORE anything is rendered or redirected,
  and a failure of either is reported on THIS page rather than by redirecting
  (RFC 6749 s4.1.2.1). Redirecting an unvalidated redirect_uri is exactly the bug that
  turns this endpoint into an account-takeover primitive: an attacker sends the victim
  to the genuine login page and receives the resulting code at their own host.
- PKCE is mandatory, S256 only.
- Access tokens are read from the Authorization header ONLY, never from a query
  parameter. Tokens in URLs end up in Application Insights, proxy logs, and the
  Referer header sent to the redirect target.
- Codes are issued only on an explicit POST carrying consent -- never silently on GET.
- Everything interpolated into the HTML is escaped.
"""

import html
import json
import logging
import urllib.parse

import azure.functions as func

from admin_functions.auth_session import f_create_auth_session
from admin_functions.oauth_db import (
    OAuthConfigurationError,
    SUPPORTED_CODE_CHALLENGE_METHODS,
    create_authorization_code,
    get_oauth_client,
    is_valid_code_challenge,
    validate_redirect_uri,
    client_supports_grant,
)
from common_functions.auth import (
    SCOPE_READ,
    extract_auth_token,
    format_scope,
    parse_scope,
    validate_auth_token,
)

logger = logging.getLogger("oauth_authorize")

# No Access-Control-Allow-Origin: this endpoint is a top-level browser navigation
# target, not an XHR endpoint. Advertising CORS on it only helps a script read the
# authorization response cross-origin.
BASE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Frame-Options": "DENY",  # the consent screen must not be clickjackable
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

# Only these are echoed back into the form action. An allowlist stops an attacker
# from smuggling extra parameters (e.g. a pre-filled "password") through the form.
_FORWARDED_PARAMS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "code_challenge",
    "code_challenge_method",
)

_PAGE_CSS = """
* { box-sizing: border-box; }
body {
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0b0e14;
    background-image: radial-gradient(circle at 50% -20%, #1e2638 0%, #0b0e14 75%);
    color: #f8fafc;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    margin: 0;
    padding: 20px;
}
.card {
    background: #161b26;
    padding: 2.25rem 2rem;
    border-radius: 16px;
    box-shadow: 0 20px 50px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06);
    width: 100%;
    max-width: 420px;
    text-align: center;
}
.logo-badge {
    width: 54px;
    height: 54px;
    border-radius: 16px;
    display: grid;
    place-items: center;
    margin: 0 auto 18px;
    color: #ffffff;
    background: linear-gradient(135deg, #f79524, #d96c0a);
    box-shadow: 0 8px 24px rgba(247, 149, 36, 0.35);
}
h2 { margin: 0 0 8px; color: #f8fafc; font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }
p { color: #94a3b8; font-size: 0.9rem; line-height: 1.45; margin: 0 0 20px; }
.form-group { margin-bottom: 16px; text-align: left; }
label { display: block; font-size: 0.825rem; font-weight: 500; color: #cbd5e1; margin-bottom: 6px; }
input {
    width: 100%;
    padding: 11px 14px;
    border-radius: 8px;
    border: 1px solid #283044;
    background: #0f131a;
    color: #f8fafc;
    font-size: 0.95rem;
    transition: all 0.15s ease;
}
input:focus {
    outline: none;
    border-color: #f79524;
    box-shadow: 0 0 0 3px rgba(247, 149, 36, 0.25);
}
button {
    width: 100%;
    padding: 12px 16px;
    background: linear-gradient(135deg, #f79524, #d96c0a);
    color: #ffffff;
    border: none;
    border-radius: 8px;
    font-weight: 600;
    font-size: 0.95rem;
    cursor: pointer;
    margin-top: 10px;
    box-shadow: 0 6px 18px rgba(247, 149, 36, 0.3);
    transition: all 0.15s ease;
}
button:hover {
    background: linear-gradient(135deg, #fb923c, #ea580c);
    box-shadow: 0 8px 22px rgba(247, 149, 36, 0.45);
    transform: translateY(-1px);
}
.err {
    color: #fca5a5;
    background: rgba(239, 68, 68, 0.12);
    border: 1px solid rgba(239, 68, 68, 0.3);
    padding: 10px 14px;
    border-radius: 8px;
    margin-bottom: 18px;
    font-size: 0.875rem;
    text-align: left;
}
.scope {
    text-align: left;
    font-size: 0.85rem;
    color: #cbd5e1;
    background: #0f131a;
    border: 1px solid #232a3b;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 18px;
}
.scope strong { display: block; color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
.scope-item { display: flex; align-items: center; gap: 6px; margin-top: 4px; color: #e2e8f0; font-weight: 500; }
"""


def _forwarded_query(params: dict) -> str:
    """Builds the form-action query string from allowlisted parameters only."""
    return urllib.parse.urlencode(
        {k: params[k] for k in _FORWARDED_PARAMS if params.get(k)}
    )


def _html_response(body: str, status_code: int) -> func.HttpResponse:
    headers = dict(BASE_HEADERS)
    headers["Content-Type"] = "text/html; charset=utf-8"
    return func.HttpResponse(body=body, status_code=status_code, headers=headers)


def _json_error(error: str, description: str, status_code: int = 400) -> func.HttpResponse:
    """An OAuth error that must NOT be redirected (bad client_id / redirect_uri)."""
    headers = dict(BASE_HEADERS)
    headers["Content-Type"] = "application/json;charset=UTF-8"
    return func.HttpResponse(
        body=json.dumps({"error": error, "error_description": description}),
        status_code=status_code,
        headers=headers,
    )


def _error_page(title: str, message: str, status_code: int) -> func.HttpResponse:
    """Human-readable version of a non-redirectable error."""
    return _html_response(
        f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{_PAGE_CSS}</style></head>
<body><div class="card">
<div class="logo-badge">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" width="26" height="26">
        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
    </svg>
</div>
<h2>{html.escape(title)}</h2>
<div class="err">{html.escape(message)}</div>
<p>No authorization was granted. You can safely close this window.</p>
</div></body></html>""",
        status_code,
    )


def _render_consent_html(
    client_name: str,
    params: dict,
    scope: str,
    require_login: bool,
    error_msg: str = "",
) -> str:
    """Renders the login + consent form.

    Every interpolated value is escaped. client_name in particular comes from the
    database and was previously injected raw, giving stored XSS on the authorization
    server's own origin -- the worst place to have it, since script there can read
    the authorization response.
    """
    query_str = _forwarded_query(params)
    safe_name = html.escape(client_name)
    error_html = f'<div class="err">{html.escape(error_msg)}</div>' if error_msg else ""

    scope_items = "".join(
        f'<div class="scope-item"><span>&bull;</span> {html.escape(s)}</div>' for s in scope.split() if s
    )
    scope_html = f'<div class="scope"><strong>Requested Access</strong>{scope_items}</div>' if scope_items else ""

    if require_login:
        credential_fields = """
            <div class="form-group">
                <label>Email Address</label>
                <input type="email" name="email" required placeholder="name@example.com" autocomplete="username" />
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;" autocomplete="current-password" />
            </div>"""
        button_label = "Sign in &amp; Authorize"
    else:
        credential_fields = ""
        button_label = "Authorize"

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize {safe_name}</title>
    <style>{_PAGE_CSS}</style>
</head>
<body>
    <div class="card">
        <div class="logo-badge">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" width="26" height="26">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
            </svg>
        </div>
        <h2>Authorize Application</h2>
        <p><strong>{safe_name}</strong> is requesting access to your account.</p>
        {error_html}
        {scope_html}
        <form method="POST" action="/api/oauth/authorize?{html.escape(query_str, quote=True)}">
            {credential_fields}
            <input type="hidden" name="consent" value="approve" />
            <button type="submit">{button_label}</button>
        </form>
    </div>
</body>
</html>"""


def _parse_params(req: func.HttpRequest) -> dict:
    """Merges query and body parameters.

    Body wins on conflict. The previous order let query-string values override the
    POST body, so an attacker crafting the initial link could pin parameters the
    victim's own form submission was supposed to supply.
    """
    params = dict(req.params.items())

    if req.method == "POST":
        try:
            body_bytes = req.get_body()
            if body_bytes:
                parsed_qs = urllib.parse.parse_qs(
                    body_bytes.decode("utf-8"), keep_blank_values=True
                )
                for k, v in parsed_qs.items():
                    if v:
                        params[k] = v[0]
        except Exception:
            logger.warning("oauth_authorize: unparseable request body", exc_info=False)

    return params


def _build_redirect(redirect_uri: str, code: str, state: str) -> str:
    """Appends code (and state) to an already-validated redirect_uri."""
    delimiter = "&" if "?" in redirect_uri else "?"
    target = f"{redirect_uri}{delimiter}code={urllib.parse.quote(code, safe='')}"
    if state:
        target += f"&state={urllib.parse.quote(state, safe='')}"
    return target


def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse("", status_code=204, headers=BASE_HEADERS)

    params = _parse_params(req)

    client_id = params.get("client_id")
    redirect_uri = params.get("redirect_uri")
    response_type = params.get("response_type")
    scope = params.get("scope") or "read"
    state = params.get("state") or ""
    code_challenge = params.get("code_challenge") or ""
    code_challenge_method = params.get("code_challenge_method") or "S256"

    # ---- Stage 1: client_id and redirect_uri -------------------------------
    # These must be settled before any redirect, because until redirect_uri is
    # known-good there is no safe place to send an error to (RFC 6749 s4.1.2.1).
    if not client_id:
        return _error_page("Invalid request", "client_id is required.", 400)
    if not redirect_uri:
        return _error_page("Invalid request", "redirect_uri is required.", 400)

    try:
        client = get_oauth_client(client_id)
    except OAuthConfigurationError:
        logger.exception("oauth_authorize: client registry unavailable")
        return _error_page(
            "Service unavailable",
            "The authorization service is temporarily unavailable. Please try again later.",
            503,
        )

    if client is None:
        # Unknown or deactivated client. No redirect: we have no validated target.
        logger.warning("oauth_authorize: unknown client_id=%r", client_id)
        return _error_page("Invalid client", "Unknown or inactive client_id.", 400)

    if not validate_redirect_uri(client, redirect_uri):
        # THE critical check. Never redirect here.
        logger.warning(
            "oauth_authorize: unregistered redirect_uri for client_id=%r", client_id
        )
        return _error_page(
            "Invalid redirect URI",
            "The redirect_uri does not exactly match a URI registered for this client.",
            400,
        )

    client_name = client.get("client_name") or "Client Application"

    # ---- Stage 2: remaining parameters ------------------------------------
    # redirect_uri is validated now, so from here errors could be redirected. We
    # still answer directly: it's equally spec-compliant and avoids bouncing
    # attacker-influenced text through a redirect.
    if response_type != "code":
        return _json_error("unsupported_response_type", "response_type must be 'code'")

    if not client_supports_grant(client, "authorization_code"):
        return _json_error(
            "unauthorized_client",
            "This client is not registered for the authorization_code grant.",
        )

    if code_challenge_method not in SUPPORTED_CODE_CHALLENGE_METHODS:
        return _json_error(
            "invalid_request",
            f"code_challenge_method must be one of: {', '.join(SUPPORTED_CODE_CHALLENGE_METHODS)}",
        )

    if not is_valid_code_challenge(code_challenge):
        return _json_error(
            "invalid_request",
            "A valid PKCE code_challenge (base64url-encoded SHA-256, 43 chars) is required.",
        )

    if len(state) > 512 or len(scope) > 512:
        return _json_error("invalid_request", "state or scope parameter is too long.")

    # Normalise the requested scope down to what this server actually supports.
    # Unknown scope values are dropped rather than rejected (RFC 6749 s3.3 -- the
    # server decides the granted scope). If nothing recognisable survives, fall
    # back to read-only rather than silently granting write.
    granted_scopes = parse_scope(scope) or frozenset({SCOPE_READ})
    scope = format_scope(granted_scopes)

    # Normalise for the form action, so the POST carries exactly what we validated
    # and the user sees on the consent screen exactly what gets stored on the code.
    params["scope"] = scope
    params["code_challenge_method"] = code_challenge_method

    # ---- Stage 3: authenticate the resource owner -------------------------
    # Header only. Query-parameter tokens were removed: they leak into logs and
    # into the Referer header sent to the redirect target.
    user_id = None
    if extract_auth_token(req):
        is_valid, token_user_id, _role = validate_auth_token(req)
        if is_valid:
            user_id = token_user_id

    login_error = ""
    if user_id is None and req.method == "POST":
        email = params.get("email")
        password = params.get("password")
        if email and password:
            try:
                int_code, _desc, _err, session_data = f_create_auth_session(email, password)
            except Exception:
                logger.exception("oauth_authorize: login failure")
                return _error_page(
                    "Service unavailable",
                    "Unable to process sign-in right now. Please try again later.",
                    503,
                )
            if session_data and int_code == 200:
                is_valid, token_user_id, _role = validate_auth_token(
                    session_data.get("access_token")
                )
                if is_valid:
                    user_id = token_user_id
            if user_id is None:
                # Fixed message: never reveal whether the email exists.
                login_error = "Invalid email or password."

    if user_id is None:
        status = 401 if login_error else 200
        return _html_response(
            _render_consent_html(client_name, params, scope, require_login=True, error_msg=login_error),
            status,
        )

    # ---- Stage 4: explicit consent ----------------------------------------
    # A code is only ever issued by a POST that carries consent=approve. Previously
    # a GET with a valid token minted a code silently, with no user interaction.
    if not (req.method == "POST" and params.get("consent") == "approve"):
        return _html_response(
            _render_consent_html(client_name, params, scope, require_login=False),
            200,
        )

    # ---- Stage 5: issue the code and redirect -----------------------------
    try:
        auth_code = create_authorization_code(
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
        )
    except Exception:
        # Failures propagate now instead of returning an unredeemable code.
        logger.exception("oauth_authorize: failed to persist authorization code")
        return _error_page(
            "Service unavailable",
            "Unable to complete authorization right now. Please try again later.",
            503,
        )

    target_redirect = _build_redirect(redirect_uri, auth_code, state)

    headers = dict(BASE_HEADERS)
    headers["Location"] = target_redirect
    # Body intentionally omits the code and the target: a 302 body is visible to
    # anything that renders it, and the code belongs only in the Location header.
    return func.HttpResponse(body="Redirecting...", status_code=302, headers=headers)
