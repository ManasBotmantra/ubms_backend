import uuid
from datetime import datetime, timedelta, timezone

import bcrypt

from common_functions.auth import (
    ACCESS_TOKEN_EXPIRY_SECONDS,
    REFRESH_TOKEN_EXPIRY_SECONDS,
    generate_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from common_functions.db import get_db_cursor
from common_functions import config

# Used to run a dummy bcrypt check on the "no such user" path so that path takes
# roughly as long as a real password check -- otherwise an unknown email returns
# noticeably faster than a wrong password, letting response time reveal which
# email addresses have accounts even though the error message is identical.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"timing-attack-mitigation", bcrypt.gensalt()).decode()

# Note: unlike every other f_* mapping function, these three don't take
# (uuid_caller_user_id, str_caller_role) -- there's no authenticated caller
# yet when logging in, and refresh/logout are authenticated by the refresh
# token itself rather than an authorize_token/RBAC check.


def _token_response(access_token, refresh_token):
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_EXPIRY_SECONDS,
    }


def f_create_auth_session(str_email, str_password):
    try:
        # 1. Validate input
        if not str_email or not str_email.strip() or "@" not in str_email:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        str_email = str_email.strip().lower()
        if not str_password:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        with get_db_cursor(commit=True) as cursor:
            # 2. Resolve dependent lookup: find the user by email. Case-insensitive --
            # emails are industry-standard treated as case-insensitive identifiers, and this
            # must work regardless of the case a row was originally created with.
            cursor.execute(
                """
                SELECT user_id, password_hash, user_status
                FROM user_master
                WHERE LOWER(email) = %s
                """,
                (str_email,),
            )
            user_row = cursor.fetchone()

            # 3. Validate credentials
            if not user_row or user_row["user_status"] != "Active":
                bcrypt.checkpw(str_password.encode(), _DUMMY_PASSWORD_HASH.encode())
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_INVALID_CREDENTIALS, None
            if not bcrypt.checkpw(str_password.encode(), user_row["password_hash"].encode()):
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_INVALID_CREDENTIALS, None

            user_id = user_row["user_id"]

            # 4. Core DB operation: issue and persist a new session
            access_token = generate_access_token(user_id)
            refresh_token, refresh_token_hash = generate_refresh_token()
            issued_on = datetime.now(timezone.utc)
            expires_on = issued_on + timedelta(seconds=REFRESH_TOKEN_EXPIRY_SECONDS)

            cursor.execute(
                """
                INSERT INTO auth_session (session_id, user_id, refresh_token_hash, issued_on, expires_on)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), user_id, refresh_token_hash, issued_on, expires_on),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", _token_response(access_token, refresh_token)

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_refresh_auth_session(str_refresh_token):
    try:
        # 1. Validate input
        if not str_refresh_token:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        refresh_token_hash = hash_refresh_token(str_refresh_token)
        now = datetime.now(timezone.utc)

        with get_db_cursor(commit=True) as cursor:
            # 2. Resolve dependent lookup: find the session by refresh token hash
            cursor.execute(
                """
                SELECT session_id, user_id, expires_on, revoked_on
                FROM auth_session
                WHERE refresh_token_hash = %s
                """,
                (refresh_token_hash,),
            )
            session_row = cursor.fetchone()

            # 3. Validate session
            if not session_row or session_row["revoked_on"] is not None or session_row["expires_on"] <= now:
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_REFRESH_TOKEN_INVALID_OR_EXPIRED, None

            cursor.execute(
                "SELECT user_status FROM user_master WHERE user_id = %s",
                (session_row["user_id"],),
            )
            user_row = cursor.fetchone()
            if not user_row or user_row["user_status"] != "Active":
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_USER_NOT_ACTIVE, None

            # 4. Core DB operation: rotate -- revoke the old session, issue a new one.
            # Guard with "AND revoked_on IS NULL" and check rowcount so a concurrent
            # request that already rotated/revoked this exact session loses the race
            # cleanly instead of both requests minting a new session pair.
            cursor.execute(
                "UPDATE auth_session SET revoked_on = %s WHERE session_id = %s AND revoked_on IS NULL",
                (now, session_row["session_id"]),
            )
            if cursor.rowcount == 0:
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_REFRESH_TOKEN_INVALID_OR_EXPIRED, None

            user_id = session_row["user_id"]
            access_token = generate_access_token(user_id)
            new_refresh_token, new_refresh_token_hash = generate_refresh_token()
            expires_on = now + timedelta(seconds=REFRESH_TOKEN_EXPIRY_SECONDS)

            cursor.execute(
                """
                INSERT INTO auth_session (session_id, user_id, refresh_token_hash, issued_on, expires_on)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), user_id, new_refresh_token_hash, now, expires_on),
            )

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", _token_response(access_token, new_refresh_token)

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_delete_auth_session(str_refresh_token):
    try:
        # 1. Validate input
        if not str_refresh_token:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        refresh_token_hash = hash_refresh_token(str_refresh_token)

        with get_db_cursor(commit=True) as cursor:
            # 2. Core DB operation: revoke the matching active session
            cursor.execute(
                """
                UPDATE auth_session
                SET revoked_on = %s
                WHERE refresh_token_hash = %s AND revoked_on IS NULL
                """,
                (datetime.now(timezone.utc), refresh_token_hash),
            )
            revoked_count = cursor.rowcount

        # 3. No data found
        if not revoked_count:
            return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_ACTIVE_SESSION_FOUND, None

        # 4. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", {"status": "logged_out"}

    except Exception as ex:
        # 5. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
