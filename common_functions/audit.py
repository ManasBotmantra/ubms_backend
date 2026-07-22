import json
import logging
import uuid
from datetime import datetime, timezone

from common_functions.db import get_db_cursor

logger = logging.getLogger("api_audit_trail")

_SENSITIVE_KEYS = {
    "authorize_token",
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    # PII: not a credential, but audit_log is a second place this data would otherwise sit
    # in plaintext (backups, exports, or a future role change granting broader read access).
    "full_name",
    "email",
}


def _redact(payload):
    """Recurses into nested dicts/lists so a sensitive field is redacted no matter how deep
    it's nested (e.g. update_user_master's "password" sits inside update_fields, not top-level)."""
    if isinstance(payload, dict):
        return {
            key: ("***REDACTED***" if key.lower() in _SENSITIVE_KEYS else _redact(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_redact(item) for item in payload]
    return payload


def log_api_audit_trail(str_api_name, uuid_caller_user_id, json_request_payload, json_response_payload, int_status_code):
    """
    Log an API audit trail entry.

    Sensitive fields (authorize_token, password, etc.) are redacted.
    The audit log is written both to a JSON log file and persisted to the audit_log table.
    Errors during logging are caught and logged via logger.exception() but do not propagate,
    to avoid breaking the main API response.
    """
    redacted_request = _redact(json_request_payload)
    # A 200 response is the expected/common case and its body is usually just the record(s)
    # already visible via the request that created/fetched them -- skip storing it to keep
    # audit_log lean; failure responses (4xx/5xx) still log the response body in full since
    # that's the actual diagnostic value of an audit trail.
    redacted_response = None if int_status_code == 200 else _redact(json_response_payload)

    logger.info(
        json.dumps(
            {
                "api_name": str_api_name,
                "caller_user_id": uuid_caller_user_id,
                "status_code": int_status_code,
                "request": redacted_request,
                "response": redacted_response,
            },
            default=str,
        )
    )

    # Persist to audit_log as well. Best-effort: a logging failure must never
    # break the actual API response, so any exception here is swallowed.
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO audit_log
                    (trail_id, api_name, caller_user_id, request_payload, response_payload, status_code, logged_on)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    str_api_name,
                    uuid_caller_user_id,
                    json.dumps(redacted_request, default=str),
                    None if redacted_response is None else json.dumps(redacted_response, default=str),
                    int_status_code,
                    datetime.now(timezone.utc),
                ),
            )
    except Exception:
        logger.exception("Failed to persist audit_log row for %s", str_api_name)
