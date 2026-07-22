import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from common_functions.db import get_db_cursor

ACCESS_TOKEN_EXPIRY_SECONDS = int(os.environ.get("ACCESS_TOKEN_EXPIRY_SECONDS", "900"))
REFRESH_TOKEN_EXPIRY_SECONDS = int(os.environ.get("REFRESH_TOKEN_EXPIRY_SECONDS", "604800"))


def generate_access_token(uuid_user_id):
    current_time = datetime.now(timezone.utc)
    payload = {
        "user_id": uuid_user_id,
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


def validate_auth_token(authorize_token):
    """Returns (is_valid, uuid_caller_user_id, str_caller_role).

    Role is never embedded in the access token -- it's resolved fresh from
    user_master/role_master on every call, so a role change or account
    deactivation takes effect on the very next request rather than waiting
    out the token's expiry.
    """
    if not authorize_token:
        return False, None, None

    try:
        payload = jwt.decode(
            authorize_token,
            os.environ["JWT_SECRET_KEY"],
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
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
