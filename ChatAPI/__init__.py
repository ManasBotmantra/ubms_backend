"""Azure Function backing the conversational analytics endpoint (/api/chat).

Security notes -- these are deliberate:

- The token is VALIDATED, not merely checked for presence. The previous version only
  tested `if not authorize_token`, so any non-empty header value passed. Because the
  pipeline skips the upstream API call whenever the supplied dataset_id already exists
  on disk, that made the whole LLM -> SQL -> DuckDB path reachable with no credentials
  at all, against another user's cached dataset.
- dataset_id is validated as a UUID before it is used. It is interpolated into a
  filesystem path (`db_dir / f"{dataset_id}.duckdb"`), so an unvalidated value gives
  path traversal via "../".
- Exception text is not returned to the caller.
"""

import asyncio
import json
import logging
import re
import uuid

import azure.functions as func

from common_functions.auth import SCOPE_READ, token_has_scope, validate_auth_token
from common_functions.audit import log_api_audit_trail
from common_functions import config
from src.api.dependencies import get_response_builder, get_dataset_service
from src.core.exceptions import DatasetNotFoundError
from src.core.logger import logger
from src.domain.models import ChatMessage

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, authorize_token",
}

# Roles permitted to use conversational analytics. This endpoint can aggregate across
# the whole dataset, so it is restricted to the two roles that already hold
# organisation-wide read access through the reporting endpoints.
_PERMITTED_ROLES = ("Administrator", "Leadership")

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_MAX_MESSAGE_CHARS = 4000
_MAX_HISTORY_MESSAGES = 40


def _json_response(payload: dict, status_code: int) -> func.HttpResponse:
    headers = dict(CORS_HEADERS)
    headers["Content-Type"] = "application/json;charset=UTF-8"
    return func.HttpResponse(
        body=json.dumps(payload, default=str), status_code=status_code, headers=headers
    )


def _error(message: str, status_code: int) -> func.HttpResponse:
    return _json_response({"error": message}, status_code)


def _is_valid_dataset_id(dataset_id) -> bool:
    """dataset_ids are uuid4 values produced by DatasetService.

    Enforcing that shape stops "../" and any other path metacharacter from reaching
    `db_dir / f"{dataset_id}.duckdb"`.
    """
    return isinstance(dataset_id, str) and bool(_UUID_RE.match(dataset_id))


def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse("", status_code=204, headers=CORS_HEADERS)

    request_body = {}
    uuid_caller_user_id = None

    try:
        # 1. Validate auth token -- accepts Authorization: Bearer <token> or the
        #    legacy authorize_token header (extract_auth_token handles both).
        is_valid, uuid_caller_user_id, str_caller_role = validate_auth_token(req)
        if not is_valid:
            response = _error(config.ERR_TOKEN_INVALID_OR_EXPIRED, config.STATUS_UNAUTHORIZED)
            log_api_audit_trail("chat", None, request_body, None, response.status_code)
            return response

        # 2. Parse body
        try:
            request_body = req.get_json()
        except ValueError:
            return _error("Invalid JSON in request body", 400)
        if not isinstance(request_body, dict):
            return _error("Request body must be a JSON object", 400)

        # 3. Scope check -- chat only ever reads, so `read` is enough.
        if not token_has_scope(req, SCOPE_READ):
            response = _error(config.ERR_INSUFFICIENT_SCOPE, config.STATUS_AUTHORIZATION_FAILURE)
            response.headers["WWW-Authenticate"] = (
                f'Bearer error="insufficient_scope", scope="{SCOPE_READ}"'
            )
            log_api_audit_trail("chat", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. RBAC check
        if str_caller_role not in _PERMITTED_ROLES:
            response = _error(config.ERR_ROLE_NOT_PERMITTED, config.STATUS_AUTHORIZATION_FAILURE)
            log_api_audit_trail("chat", uuid_caller_user_id, request_body, None, response.status_code)
            return response

        # 4. Validate input
        user_message = request_body.get("message")
        if not user_message or not isinstance(user_message, str):
            return _error("Missing 'message' in request body", 400)
        if len(user_message) > _MAX_MESSAGE_CHARS:
            return _error(f"'message' exceeds {_MAX_MESSAGE_CHARS} characters", 400)

        dataset_id = request_body.get("dataset_id")
        if dataset_id is not None and not _is_valid_dataset_id(dataset_id):
            # Rejected rather than sanitised: a non-UUID value is never legitimate.
            logger.warning("chat: rejected malformed dataset_id from user_id=%s", uuid_caller_user_id)
            return _error("Invalid 'dataset_id' — expected a UUID", 400)

        history_raw = request_body.get("history") or []
        if not isinstance(history_raw, list):
            return _error("'history' must be a list", 400)
        if len(history_raw) > _MAX_HISTORY_MESSAGES:
            history_raw = history_raw[-_MAX_HISTORY_MESSAGES:]

        try:
            chat_history = [
                ChatMessage(role=msg["role"], content=msg["content"])
                for msg in history_raw
                if isinstance(msg, dict) and "role" in msg and "content" in msg
            ]
        except Exception:
            return _error("'history' entries must have 'role' and 'content'", 400)

        # 5. Run the pipeline. The caller's own validated token is forwarded so the
        #    upstream data API applies its own authorisation to the fetch.
        authorize_token = req.headers.get("authorize_token") or req.headers.get("Authorization")

        response = asyncio.run(
            process_chat_request(
                user_message, dataset_id, chat_history, authorize_token, uuid_caller_user_id
            )
        )

        log_api_audit_trail("chat", uuid_caller_user_id, request_body, None, response.status_code)
        return response

    except Exception:
        logger.exception("chat: unhandled exception")
        response = _error("Internal server error", config.STATUS_SYSTEM_EXCEPTION)
        log_api_audit_trail("chat", uuid_caller_user_id, request_body, None, response.status_code)
        return response


async def process_chat_request(
    user_message, dataset_id, chat_history, authorize_token, caller_user_id
):
    try:
        dataset_service = get_dataset_service()
        response_builder = get_response_builder()

        metadata = None
        if dataset_id:
            try:
                metadata = await dataset_service.get_metadata(dataset_id)
            except DatasetNotFoundError:
                logger.warning(f"dataset_id={dataset_id} not found on disk — will re-fetch if needed")

        blocks, resolved_dataset_id = await response_builder.build_response(
            user_message=user_message,
            dataset_id=dataset_id,
            metadata=metadata,
            chat_history=chat_history,
            authorize_token=authorize_token,
        )

        return _json_response(
            {
                "dataset_id": resolved_dataset_id,
                "blocks": [b.model_dump() for b in blocks],
            },
            200,
        )

    except Exception:
        # Detail to the server log; the caller gets a generic message. Previously the
        # raw str(e) was returned, leaking SQL, schema and provider errors.
        logger.exception(f"Error processing chat for user_id={caller_user_id}")
        return _error("Internal server error", config.STATUS_SYSTEM_EXCEPTION)
