"""Centralized configuration: environment settings, HTTP-aligned status codes, their
descriptions, and reusable error messages -- following the response envelope and
status-code convention documented in CLAUDE.md."""

import os

from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------------------
# Database
# ------------------------------------------------------------------------------
SCHEMANAME = os.environ.get("SCHEMANAME", "public")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "")
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# ------------------------------------------------------------------------------
# Authentication (OAuth2-style access/refresh tokens -- see common_functions/auth.py)
# ------------------------------------------------------------------------------
# No default: a secret this sensitive must fail loudly if unset, not silently sign/verify
# tokens with an empty/guessable key. Matches common_functions/auth.py's own bracket access.
JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRY_SECONDS = int(os.environ.get("ACCESS_TOKEN_EXPIRY_SECONDS", "900"))
REFRESH_TOKEN_EXPIRY_SECONDS = int(os.environ.get("REFRESH_TOKEN_EXPIRY_SECONDS", "604800"))

# ------------------------------------------------------------------------------
# Domain defaults
# ------------------------------------------------------------------------------
DEFAULT_CURRENCY = "AED"  # OQ-005 resolved: stakeholder confirmed AED as the operating currency
DEFAULT_PAGE_NO = 1
DEFAULT_PAGE_SIZE = 20

# ------------------------------------------------------------------------------
# HTTP status codes actually returned by this API -- client-facing (API layer),
# matching standard HTTP semantics: 401 authentication required/failed, 403
# forbidden, 404 not found, 409 conflict, 500 internal error. 402 is repurposed
# per this project's own convention to mean request-validation failure
# (documented in CLAUDE.md), not its rarely-used HTTP meaning ("Payment Required").
#
# These are the ONLY codes any endpoint in this API should return. Every return
# statement and build_api_response()/f_* call should reference these constants
# (and their DESC_* pair below) rather than the bare integer/string literal.
# ------------------------------------------------------------------------------
STATUS_SUCCESS = 200
STATUS_UNAUTHORIZED = 401
STATUS_INVALID_INPUT = 402
STATUS_AUTHORIZATION_FAILURE = 403
STATUS_NO_DATA_FOUND = 404
STATUS_DUPLICATE_CONFLICT = 409
STATUS_SYSTEM_EXCEPTION = 500

# Internal-only signal returned by f_* mapping functions for their own unhandled
# exception case (see CLAUDE.md "Status code convention -- note the layer mismatch").
# The API layer must translate this into STATUS_SYSTEM_EXCEPTION (500) via
# handle_system_exception() before responding -- never return 400 to a caller.
STATUS_INTERNAL_SYSTEM_EXCEPTION = 400

# ------------------------------------------------------------------------------
# Full industry-standard HTTP status code reference (IANA registry, commonly-used
# subset). None of these beyond the ones defined above are currently returned by
# any endpoint -- they're kept here so the full vocabulary is centralized and
# available without guessing a number if/when a new case needs one, instead of a
# new magic number being introduced ad hoc at a call site.
# ------------------------------------------------------------------------------
# 2xx Success
HTTP_OK = STATUS_SUCCESS           # alias: 200
HTTP_CREATED = 201
HTTP_ACCEPTED = 202
HTTP_NO_CONTENT = 204

# 3xx Redirection
HTTP_NOT_MODIFIED = 304

# 4xx Client Error
HTTP_BAD_REQUEST = STATUS_INTERNAL_SYSTEM_EXCEPTION  # alias: 400 (internal f_* signal)
HTTP_UNAUTHORIZED = STATUS_UNAUTHORIZED               # alias: 401
HTTP_FORBIDDEN = STATUS_AUTHORIZATION_FAILURE         # alias: 403
HTTP_NOT_FOUND = STATUS_NO_DATA_FOUND                 # alias: 404
HTTP_METHOD_NOT_ALLOWED = 405
HTTP_NOT_ACCEPTABLE = 406
HTTP_REQUEST_TIMEOUT = 408
HTTP_CONFLICT = STATUS_DUPLICATE_CONFLICT             # alias: 409
HTTP_GONE = 410
HTTP_LENGTH_REQUIRED = 411
HTTP_PRECONDITION_FAILED = 412
HTTP_PAYLOAD_TOO_LARGE = 413
HTTP_UNSUPPORTED_MEDIA_TYPE = 415
HTTP_UNPROCESSABLE_ENTITY = 422
HTTP_LOCKED = 423
HTTP_TOO_MANY_REQUESTS = 429

# 5xx Server Error
HTTP_INTERNAL_SERVER_ERROR = STATUS_SYSTEM_EXCEPTION  # alias: 500
HTTP_NOT_IMPLEMENTED = 501
HTTP_BAD_GATEWAY = 502
HTTP_SERVICE_UNAVAILABLE = 503
HTTP_GATEWAY_TIMEOUT = 504

# ------------------------------------------------------------------------------
# Status descriptions -- the human-readable status_description paired with each
# code above in every build_api_response()/f_* return tuple.
# ------------------------------------------------------------------------------
DESC_SUCCESS = "Success"
DESC_UNAUTHORIZED = "Unauthorized"
DESC_INVALID_INPUT = "Invalid Input"
DESC_AUTHORIZATION_FAILURE = "Authorization Failure"
DESC_NO_DATA_FOUND = "No Data Found"
DESC_DUPLICATE_CONFLICT = "Duplicate/Conflict"
DESC_SYSTEM_EXCEPTION = "System Exception"

# ------------------------------------------------------------------------------
# Reusable error messages -- only for messages that recur across multiple
# entities/functions. Entity-specific messages (e.g. "duplicate role_name",
# "duplicate account_number") stay as literals at their call site, since the
# field/entity name is part of the message and isn't reusable.
# ------------------------------------------------------------------------------
ERR_TOKEN_INVALID_OR_EXPIRED = "invalid or expired token"
ERR_REFRESH_TOKEN_INVALID_OR_EXPIRED = "invalid or expired refresh token"
ERR_INVALID_CREDENTIALS = "invalid credentials"
ERR_USER_NOT_ACTIVE = "user not active"
ERR_ROLE_NOT_PERMITTED = "role not permitted"
ERR_NO_RECORD_FOUND = "no record found"
ERR_NO_DATA_FOUND = "no data found"
ERR_NO_ACTIVE_SESSION_FOUND = "no active session found"
ERR_UNIT_NOT_ASSIGNED_TO_CALLER = "unit not assigned to caller"
ERR_ALLOCATION_TERMINAL_STATE = "allocation already in a terminal state"
ERR_NOTHING_TO_RESOLVE = "nothing on hold to resolve"
ERR_MULTIPLE_ROWS_MATCHED = "search_fields matches multiple rows, narrow the search"
ERR_ALLOCATION_SUM_NOT_100 = "allocation percentages must sum to 100.00"
