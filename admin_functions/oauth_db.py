"""Database helper functions for OAuth 2.0 Authorization Server (clients, auth codes, PKCE).

Security posture -- these are deliberate, load-bearing choices:

- Every failure path fails CLOSED. A missing table, a DB outage, or an unknown
  client_id results in a rejection, never a permissive fallback. An earlier version
  fell back to a hardcoded client with redirect_uris=["*"] whenever the DB errored,
  which turned any transient outage into "every redirect target is allowed".
- redirect_uri is matched EXACTLY against the client's registered list. There is no
  wildcard and no "*" escape hatch: an unvalidated redirect_uri lets an attacker send
  a victim to the real login page and have the resulting authorization code delivered
  to the attacker's own host, which is a complete account takeover. PKCE does not
  mitigate this, because the attacker initiates the flow and therefore owns the
  verifier/challenge pair.
- PKCE is MANDATORY and S256-only. The "plain" method is rejected: it puts the
  verifier in the authorization URL in cleartext, so anything that can read the URL
  (logs, proxies, Referer headers) can replay the code.
- Error strings returned to callers are fixed constants. Raw exception text used to be
  interpolated into the OAuth error_description, leaking table names and connection
  details to unauthenticated callers.
"""

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from common_functions.db import get_db_cursor

# Authorization codes are single-use and short-lived. RFC 6749 s4.1.2 recommends a
# maximum of 10 minutes; 60s is ample for a redirect + immediate token exchange and
# shrinks the window in which a leaked code is worth anything.
AUTH_CODE_TTL_SECONDS = 60

# The only PKCE method we accept. See module docstring for why "plain" is out.
SUPPORTED_CODE_CHALLENGE_METHODS = ("S256",)

# RFC 7636 s4.1: verifier is 43-128 chars from the unreserved set.
_CODE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
# RFC 7636 s4.2: S256 challenge is base64url(sha256(verifier)) without padding = 43 chars.
_CODE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9\-._~]{43}$")


class OAuthConfigurationError(Exception):
    """Raised when the OAuth tables are unreachable or missing.

    Kept distinct from "unknown client" so the caller can answer 503 (a server-side
    problem the operator must fix) rather than 400 (a caller mistake). Both are
    rejections -- neither issues a token.
    """


def verify_pkce_challenge(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """Verifies a PKCE code_verifier against the stored code_challenge.

    Returns False for any unsupported method, any missing value, or any
    malformed verifier -- never raises, so callers get a clean boolean.
    """
    if not code_verifier or not code_challenge:
        return False
    if method not in SUPPORTED_CODE_CHALLENGE_METHODS:
        return False
    if not _CODE_VERIFIER_RE.match(code_verifier):
        return False

    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    calculated = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    # compare_digest rather than == so a wrong verifier can't be recovered a byte
    # at a time from response timing.
    return hmac.compare_digest(calculated, code_challenge)


def is_valid_code_challenge(code_challenge: str) -> bool:
    """True if code_challenge is a well-formed S256 challenge."""
    return bool(code_challenge) and bool(_CODE_CHALLENGE_RE.match(code_challenge))


def _parse_redirect_uris(raw) -> list:
    """Normalises the redirect_uris column into a list of strings.

    Tolerates a Postgres text[]/jsonb column (already a list), a JSON-encoded
    string, or a comma-separated string, since the column type isn't pinned by a
    migration in this repo.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(u).strip() for u in raw if str(u).strip()]
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1].strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(u).strip() for u in parsed if str(u).strip()]
            except ValueError:
                pass
        return [part.strip().strip('"\'') for part in raw.split(",") if part.strip().strip('"\'')]
    return []


def get_oauth_client(client_id: str) -> dict | None:
    """Fetches an active OAuth client, or None if no such active client exists.

    Raises OAuthConfigurationError if the client registry can't be read. This must
    NOT be softened into a default client -- see the module docstring.
    """
    if not client_id or not isinstance(client_id, str) or len(client_id) > 255:
        return None

    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT client_id, client_secret_hash, client_name, redirect_uris,
                       grant_types, is_active
                FROM oauth_clients
                WHERE client_id = %s AND is_active = TRUE
                """,
                (client_id,),
            )
            row = cursor.fetchone()
    except Exception as ex:
        raise OAuthConfigurationError("OAuth client registry is unavailable") from ex

    if not row:
        return None

    client = dict(row)
    client["redirect_uris"] = _parse_redirect_uris(client.get("redirect_uris"))
    client["grant_types"] = _parse_redirect_uris(client.get("grant_types"))
    return client


def validate_redirect_uri(client: dict, redirect_uri: str) -> bool:
    """Exact-match redirect_uri against the client's registered list.

    Exact string comparison only: no wildcards, no prefix matching, no
    normalisation. Prefix matching is a known bypass (an attacker registers
    https://good.example.com and then uses
    https://good.example.com.evil.com or .../../path).
    """
    if not redirect_uri or not isinstance(redirect_uri, str):
        return False
    registered = client.get("redirect_uris") or []
    return any(hmac.compare_digest(redirect_uri, str(candidate)) for candidate in registered)


def client_supports_grant(client: dict, grant_type: str) -> bool:
    """True if this client is registered for the requested grant type."""
    grants = client.get("grant_types") or []
    return grant_type in grants


def verify_client_secret(client: dict, presented_secret: str | None) -> bool:
    """Authenticates a client at the token endpoint.

    A client with a stored client_secret_hash is CONFIDENTIAL and must present a
    matching secret. A client with no stored hash is PUBLIC and is allowed through
    without one -- it is protected by mandatory PKCE plus exact redirect_uri
    matching instead. Previously no secret was ever checked, so every registered
    confidential client was effectively public.
    """
    stored_hash = client.get("client_secret_hash")

    if not stored_hash:
        return True  # public client

    if not presented_secret:
        return False

    presented_hash = hashlib.sha256(presented_secret.encode("utf-8")).hexdigest()
    return hmac.compare_digest(presented_hash, str(stored_hash))


def create_authorization_code(
    client_id: str,
    user_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    scope: str = "read write",
    ttl_seconds: int = AUTH_CODE_TTL_SECONDS,
) -> str:
    """Generates and persists a single-use authorization code in oauth_auth_codes.

    Exceptions propagate. An earlier version swallowed insert failures and still
    returned the code, so a failed insert produced a redirect carrying a code that
    could never be redeemed -- an unexplainable failure for the client.
    """
    if code_challenge_method not in SUPPORTED_CODE_CHALLENGE_METHODS:
        raise ValueError(f"unsupported code_challenge_method: {code_challenge_method}")
    if not is_valid_code_challenge(code_challenge):
        raise ValueError("code_challenge is required and must be a base64url S256 digest")

    # secrets-grade entropy via uuid4 (122 bits); the code is a bearer credential.
    code_id = f"code_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds)

    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO oauth_auth_codes (
                code_id, client_id, user_id, redirect_uri, scope,
                code_challenge, code_challenge_method, expires_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                code_id,
                client_id,
                user_id,
                redirect_uri,
                scope,
                code_challenge,
                code_challenge_method,
                expires_at,
                now,
            ),
        )

    return code_id


def validate_and_consume_authorization_code(
    code_id: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> tuple[bool, str | None, str | None, str | None]:
    """Validates and atomically consumes an authorization code.

    Returns (is_valid, user_id, granted_scope, error_message). The error_message is
    always one of a fixed set of constants -- never raw exception text, which would
    leak schema and connection details to an unauthenticated caller.

    granted_scope is the scope the user actually consented to at /oauth/authorize.
    It is read back from the stored code rather than taken from the token request,
    so a client cannot quietly ask for more at exchange time than it was granted.
    """
    now = datetime.now(timezone.utc)
    generic_error = "invalid or expired authorization code"

    if not code_id or not client_id or not redirect_uri:
        return False, None, None, generic_error

    try:
        with get_db_cursor(commit=True) as cursor:
            # Consume FIRST, conditionally, and let the DB arbitrate the race:
            # only the single transaction that flips used_at from NULL gets a row
            # back. The previous read-then-write ordering let two concurrent
            # requests both pass the "already used?" check and both receive a
            # token from one code.
            cursor.execute(
                """
                UPDATE oauth_auth_codes
                SET used_at = %s
                WHERE code_id = %s AND used_at IS NULL
                RETURNING code_id, client_id, user_id, redirect_uri, scope,
                          code_challenge, code_challenge_method, expires_at
                """,
                (now, code_id),
            )
            row = cursor.fetchone()

            # Unknown code, or already consumed. Same message either way so the
            # response can't be used to enumerate valid codes.
            if not row:
                return False, None, None, generic_error

            # From here the code is spent regardless of outcome. Every remaining
            # check that fails also burns the code, which is what RFC 6749 s4.1.2
            # wants: a code that fails validation must not be retryable.
            if not hmac.compare_digest(str(row["client_id"]), str(client_id)):
                return False, None, None, generic_error

            if not hmac.compare_digest(str(row["redirect_uri"]), str(redirect_uri)):
                return False, None, None, generic_error

            if row["expires_at"] <= now:
                return False, None, None, generic_error

            stored_method = row.get("code_challenge_method") or "S256"
            if not verify_pkce_challenge(code_verifier, row["code_challenge"], stored_method):
                return False, None, None, "PKCE verification failed"

            return True, row["user_id"], row.get("scope"), None

    except Exception:
        # Deliberately opaque. The real cause is on the server's own logger.
        return False, None, None, "authorization code could not be verified"
