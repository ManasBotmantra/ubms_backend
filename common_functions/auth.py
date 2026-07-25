import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from common_functions.db import get_db_cursor

ACCESS_TOKEN_EXPIRY_SECONDS = int(os.environ.get("ACCESS_TOKEN_EXPIRY_SECONDS", "900"))
REFRESH_TOKEN_EXPIRY_SECONDS = int(os.environ.get("REFRESH_TOKEN_EXPIRY_SECONDS", "604800"))

# ------------------------------------------------------------------------------
# OAuth 2.0 scopes
# ------------------------------------------------------------------------------
# Two coarse scopes. The point of these is to let an AI agent or integration hold
# a token that can READ billing data without being able to CHANGE anything --
# the main thing you'd want to grant an MCP/Copilot client.
#
# Scope can only ever NARROW what the caller's role already permits. It never
# grants anything extra:
#
#       effective access = role permissions  AND  token scopes
#
# So a `write`-scoped token held by a Leadership user still cannot create records,
# because the role check rejects it first.
SCOPE_READ = "read"
SCOPE_WRITE = "write"
ALL_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE})

# What a first-party login (create_auth_session, used by the existing frontend)
# receives. Full access, so the frontend behaves exactly as it did before.
DEFAULT_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE})


def parse_scope(scope_value) -> frozenset:
    """Parses an OAuth scope string ("read write") into a validated set.

    Unknown scope tokens are dropped rather than rejected, per RFC 6749 s3.3
    (the server decides the granted scope, which may be narrower than requested).
    """
    if not scope_value:
        return frozenset()
    if isinstance(scope_value, (set, frozenset, list, tuple)):
        requested = {str(s).strip().lower() for s in scope_value}
    else:
        requested = {part.strip().lower() for part in str(scope_value).split()}
    return frozenset(requested & ALL_SCOPES)


def format_scope(scopes) -> str:
    """Renders a scope set as the space-delimited string OAuth expects.

    Sorted so the value is stable -- easier to compare in logs and tests.
    """
    return " ".join(sorted(scopes))


def generate_access_token(uuid_user_id, scopes=None):
    """Mints a signed access token.

    `scopes` defaults to full access so every existing caller of this function
    (the frontend login path) keeps behaving exactly as before.
    """
    granted = frozenset(scopes) & ALL_SCOPES if scopes is not None else DEFAULT_SCOPES
    current_time = datetime.now(timezone.utc)
    payload = {
        "user_id": uuid_user_id,
        "scope": format_scope(granted),
        "iat": current_time,
        "exp": current_time + timedelta(seconds=ACCESS_TOKEN_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, os.environ["JWT_SECRET_KEY"], algorithm="HS256")


def hash_refresh_token(plaintext_token):
    return hashlib.sha256(plaintext_token.encode()).hexdigest()


def generate_refresh_token():
    """Returns (plaintext_token, token_hash). Only the hash is ever persisted;
    the plaintext is handed to the caller once and never stored."""
    plaintext_token = secrets.token_urlsafe(48)
    return plaintext_token, hash_refresh_token(plaintext_token)


def extract_auth_token(req_or_token):
    """
    Extracts access token from a func.HttpRequest object or token string.
    Supports standard 'Authorization: Bearer <token>' and legacy 'authorize_token' headers.
    Strips 'Bearer ' prefix if present.
    """
    if not req_or_token:
        return None

    if hasattr(req_or_token, "headers"):
        req = req_or_token
        token = (
            req.headers.get("Authorization")
            or req.headers.get("authorization")
            or req.headers.get("authorize_token")
        )
    elif isinstance(req_or_token, str):
        token = req_or_token
    else:
        return None

    if token:
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

    return token


def _decode_token(req_or_token):
    """Decodes and signature-verifies the access token. Returns the payload or None."""
    token = extract_auth_token(req_or_token)
    if not token:
        return None
    try:
        return jwt.decode(token, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def get_token_scopes(req_or_token) -> frozenset:
    """Returns the scopes carried by the token, or an empty set if it's unusable.

    Backward compatibility: a token minted before scopes existed has no "scope"
    claim. Those are treated as full access, so tokens already in the wild (and
    any still held by the frontend) keep working until they expire naturally.
    A token with an explicit but empty scope is NOT upgraded -- only a missing
    claim is.
    """
    payload = _decode_token(req_or_token)
    if payload is None:
        return frozenset()
    if "scope" not in payload:
        return DEFAULT_SCOPES
    return parse_scope(payload.get("scope"))


def token_has_scope(req_or_token, required_scope) -> bool:
    """True if the token carries the scope this operation requires."""
    return required_scope in get_token_scopes(req_or_token)


def validate_auth_token(authorize_token):
    """Returns (is_valid, uuid_caller_user_id, str_caller_role).

    Accepts either a func.HttpRequest (preferred -- picks up both the standard
    'Authorization: Bearer' header and the legacy 'authorize_token' header) or a
    bare token string.

    Role is never embedded in the access token -- it's resolved fresh from
    user_master/role_master on every call, so a role change or account
    deactivation takes effect on the very next request rather than waiting
    out the token's expiry.
    """
    payload = _decode_token(authorize_token)
    if payload is None:
        return False, None, None

    uuid_caller_user_id = payload.get("user_id")
    if not uuid_caller_user_id:
        return False, None, None

    # A DB failure here is a system exception, not an invalid token -- let it propagate
    # to the caller's own catch-all (-> 500) instead of masking an outage as a 401.
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT r.role_name, u.user_status
            FROM user_master u
            JOIN role_master r ON r.role_id = u.role_id
            WHERE u.user_id = %s
            """,
            (uuid_caller_user_id,),
        )
        row = cursor.fetchone()

    if not row or row["user_status"] != "Active":
        return False, None, None

    return True, uuid_caller_user_id, row["role_name"]
