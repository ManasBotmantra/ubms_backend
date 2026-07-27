import uuid
from datetime import datetime, timedelta, timezone

import bcrypt

from common_functions.auth import (
    ACCESS_TOKEN_EXPIRY_SECONDS,
    DEFAULT_SCOPES,
    REFRESH_TOKEN_EXPIRY_SECONDS,
    format_scope,
    generate_access_token,
    generate_refresh_token,
    hash_refresh_token,
    parse_scope,
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


def _token_response(access_token, refresh_token, granted_scopes=None):
    """Builds the RFC 6749 s5.1 token response.

    `scope` is only included when it differs from what was requested, which for
    our purposes means: always include it, since the frontend ignores the field
    and OAuth clients want it. Omitted entirely when no scope set is supplied so
    the legacy shape is untouched for callers that don't do scopes.
    """
    body = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_EXPIRY_SECONDS,
    }
    if granted_scopes is not None:
        body["scope"] = format_scope(granted_scopes)
    return body


def _persist_session(cursor, user_id, refresh_token_hash, issued_on, expires_on,
                     granted_scopes, client_id):
    """Inserts the auth_session row carrying the grant's scope and client binding.

    scope/client_id were added by prerequisite/oauth_migration.sql. They're what
    let a refresh preserve the ORIGINAL grant's scope: without them a read-only
    agent could refresh its way up to full access, which would make scope
    enforcement pointless.
    """
    cursor.execute(
        """
        INSERT INTO auth_session
            (session_id, user_id, refresh_token_hash, issued_on, expires_on, scope, client_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            refresh_token_hash,
            issued_on,
            expires_on,
            format_scope(granted_scopes),
            client_id,
        ),
    )


def f_create_auth_session(str_email, str_password, requested_scopes=None, client_id=None):
    """Password login.

    `requested_scopes` defaults to full access, which is what the existing
    frontend gets -- its behaviour is unchanged. The OAuth password grant passes
    the client's requested scope through instead.
    """
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
                WHERE LOWER(email) = %s AND user_status = 'Active'
                """,
                (str_email,),
            )
            user_row = cursor.fetchone()

            # 3. Validate credentials
            if not user_row:
                bcrypt.checkpw(str_password.encode(), _DUMMY_PASSWORD_HASH.encode())
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_INVALID_CREDENTIALS, None
            if not bcrypt.checkpw(str_password.encode(), user_row["password_hash"].encode()):
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_INVALID_CREDENTIALS, None

            user_id = user_row["user_id"]

            # 4. Core DB operation: issue and persist a new session
            granted_scopes = DEFAULT_SCOPES if requested_scopes is None else parse_scope(requested_scopes)
            access_token = generate_access_token(user_id, granted_scopes)
            refresh_token, refresh_token_hash = generate_refresh_token()
            issued_on = datetime.now(timezone.utc)
            expires_on = issued_on + timedelta(seconds=REFRESH_TOKEN_EXPIRY_SECONDS)

            _persist_session(cursor, user_id, refresh_token_hash, issued_on,
                             expires_on, granted_scopes, client_id)

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", _token_response(
            access_token, refresh_token, granted_scopes
        )

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_create_session_for_user(uuid_user_id, requested_scopes=None, client_id=None):
    """Issues and persists a session for an ALREADY-AUTHENTICATED user.

    Used by the OAuth authorization_code grant, where the resource owner proved who
    they were at /oauth/authorize and the code exchange must not ask for a password
    again. Re-checks user_status, because a user can be deactivated in the window
    between the code being issued and being redeemed.

    Important: this exists because the code grant previously called
    generate_refresh_token() and handed the plaintext to the client WITHOUT ever
    persisting the hash. The refresh token was therefore dead on arrival, and the
    grant created no auth_session row at all -- so the resulting access token could
    not be revoked and left no session trail.
    """
    try:
        # 1. Validate input
        if not uuid_user_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        with get_db_cursor(commit=True) as cursor:
            # 2. Re-verify the account is still active
            cursor.execute(
                "SELECT user_status FROM user_master WHERE user_id = %s",
                (uuid_user_id,),
            )
            user_row = cursor.fetchone()
            if not user_row or user_row["user_status"] != "Active":
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_USER_NOT_ACTIVE, None

            # 3. Core DB operation: issue and persist the session
            granted_scopes = DEFAULT_SCOPES if requested_scopes is None else parse_scope(requested_scopes)
            access_token = generate_access_token(uuid_user_id, granted_scopes)
            refresh_token, refresh_token_hash = generate_refresh_token()
            issued_on = datetime.now(timezone.utc)
            expires_on = issued_on + timedelta(seconds=REFRESH_TOKEN_EXPIRY_SECONDS)

            _persist_session(cursor, uuid_user_id, refresh_token_hash, issued_on,
                             expires_on, granted_scopes, client_id)

        # 4. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", _token_response(
            access_token, refresh_token, granted_scopes
        )

    except Exception as ex:
        # 5. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_refresh_auth_session(str_refresh_token, client_id=None):
    """Rotates a refresh token.

    `client_id` is the client presenting the token. When the session was issued to
    a specific OAuth client, a different client may not refresh it (RFC 6749
    s10.4) -- otherwise a leaked refresh token is usable by anyone. Passing None
    means "first-party frontend", which only matches sessions that were themselves
    issued without a client.
    """
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
                SELECT session_id, user_id, expires_on, revoked_on, scope, client_id
                FROM auth_session
                WHERE refresh_token_hash = %s
                """,
                (refresh_token_hash,),
            )
            session_row = cursor.fetchone()

            # 3. Validate session
            if not session_row or session_row["revoked_on"] is not None or session_row["expires_on"] <= now:
                return config.STATUS_UNAUTHORIZED, config.DESC_UNAUTHORIZED, config.ERR_REFRESH_TOKEN_INVALID_OR_EXPIRED, None

            # The token must be presented by the client it was issued to. Same
            # generic error as an unknown token, so this can't be used to probe
            # which client owns a given refresh token.
            if (session_row["client_id"] or None) != (client_id or None):
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

            # Carry the ORIGINAL grant's scope forward. A refresh must never widen
            # access -- otherwise a read-only agent token could be refreshed into a
            # full-access one and scope enforcement would mean nothing.
            # A pre-migration row has scope NULL; treat that as the full set, which
            # is what those sessions effectively had when they were created.
            stored_scope = session_row["scope"]
            granted_scopes = DEFAULT_SCOPES if stored_scope is None else parse_scope(stored_scope)

            access_token = generate_access_token(user_id, granted_scopes)
            new_refresh_token, new_refresh_token_hash = generate_refresh_token()
            expires_on = now + timedelta(seconds=REFRESH_TOKEN_EXPIRY_SECONDS)

            _persist_session(cursor, user_id, new_refresh_token_hash, now,
                             expires_on, granted_scopes, session_row["client_id"])

        # 5. Success
        return config.STATUS_SUCCESS, config.DESC_SUCCESS, "", _token_response(
            access_token, new_refresh_token, granted_scopes
        )

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_revoke_session_for_client(str_refresh_token, client_id):
    """Revokes a refresh token on behalf of the client it was issued to (RFC 7009).

    Returns True if a live session was revoked, False otherwise. The caller
    answers 200 either way -- the boolean is only for the audit trail. Reporting
    "no such token" to the client would turn this endpoint into an oracle for
    testing whether a stolen token is still live.

    The client_id match is what stops one client revoking another's sessions.
    """
    if not str_refresh_token or not client_id:
        return False

    refresh_token_hash = hash_refresh_token(str_refresh_token)

    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            """
            UPDATE auth_session
            SET revoked_on = %s
            WHERE refresh_token_hash = %s
              AND client_id = %s
              AND revoked_on IS NULL
            """,
            (datetime.now(timezone.utc), refresh_token_hash, client_id),
        )
        return cursor.rowcount > 0


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
