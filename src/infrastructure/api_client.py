"""
src/infrastructure/api_client.py
==================================
Generic async HTTP client for any external API data source.
Driven entirely by ApiConfig — no hardcoded URLs, headers, or payload shapes.

To support a new API: define an ApiConfig in src/config/api_configs/ and register
it in src/config/loader.py. No changes needed here.
"""

import json
import httpx
from typing import Any, Optional

from src.config.base import ApiConfig
from src.core.config import settings
from src.core.exceptions import APIFetchError
from src.core.logger import logger

# Shared timeout for all API calls
_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=10.0, pool=5.0)


def _resolve_setting(token: str) -> str:
    """
    Resolves a '$SETTING_NAME' token to its value from settings.
    e.g. '$USER_ID' → settings.USER_ID
    """
    key = token.lstrip("$")
    val = getattr(settings, key, None)
    if val is None:
        raise APIFetchError(f"Settings key '{key}' not found. Add it to .env and src/core/config.py.")
    return val


def _get_nested(data: dict, dot_path: str, default: Any = None) -> Any:
    """
    Traverses a nested dict using a dot-separated path.
    e.g. _get_nested(resp, 'response_body.authorize_token')
    """
    parts = dot_path.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict):
            return default
        current = current.get(part, default)
    return current


def _resolve_payload(template: dict, fetch_params: dict) -> dict:
    """
    Resolves a payload_template dict using fetch_params values.

    Token resolution rules:
      '$filters'        → fetch_params['filters']  (the full filters dict)
      '$filters.field'  → fetch_params['filters']['field']
      '$group_by'       → fetch_params.get('group_by', [])
      '$SETTING_NAME'   → getattr(settings, SETTING_NAME)
      any other value   → kept as-is
    """
    resolved: dict = {}
    for key, value in template.items():
        if not isinstance(value, str):
            resolved[key] = value
            continue

        if value == "$filters":
            resolved[key] = fetch_params.get("filters", {})

        elif value == "$group_by":
            resolved[key] = fetch_params.get("group_by", [])

        elif value.startswith("$filters."):
            field = value[len("$filters."):]
            resolved[key] = fetch_params.get("filters", {}).get(field, "")

        elif value.startswith("$") and value[1:].isupper():
            # e.g. '$USER_ID', '$PASSWORD' — resolve from settings
            resolved[key] = _resolve_setting(value)

        else:
            resolved[key] = value

    return resolved


class GenericApiClient:
    """
    Config-driven async HTTP client.
    Reads auth + fetch configuration from an ApiConfig instance.
    """

    def __init__(self, api_config: ApiConfig) -> None:
        self.config = api_config

    async def authenticate(self) -> Optional[str]:
        """
        Performs authentication as described in self.config.auth.
        Returns a token string, or None if auth type is 'none'.

        Raises:
            APIFetchError — on HTTP error, network failure, or missing token.
        """
        auth = self.config.auth

        if auth.type == "none":
            return None

        if auth.type == "api_key_header":
            # The key lives directly in settings — just return it
            if not auth.api_key_env:
                raise APIFetchError("AuthConfig.api_key_env must be set for api_key_header auth type.")
            token = _resolve_setting(f"${auth.api_key_env}")
            if not token:
                raise APIFetchError(f"API key env var '{auth.api_key_env}' is empty in .env.")
            return token

        if auth.type == "bearer_token":
            endpoint_val = getattr(settings, auth.endpoint_env, "") if auth.endpoint_env else ""
            if not endpoint_val:
                raise APIFetchError(
                    f"Auth endpoint env var '{auth.endpoint_env}' is not set in .env."
                )

            payload = {
                k: _resolve_setting(v) if (isinstance(v, str) and v.startswith("$")) else v
                for k, v in auth.payload_template.items()
            }
            headers = {
                "Content-Type": "application/json",
                "request_type": "application/json",
            }
            logger.info(f"[{self.config.display_name}] Authenticating at {endpoint_val}")

            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    response = await client.post(endpoint_val, json=payload, headers=headers)
                    response.raise_for_status()
            except httpx.TimeoutException as e:
                raise APIFetchError(f"Authentication request timed out: {e}") from e
            except httpx.HTTPStatusError as e:
                raise APIFetchError(
                    f"Authentication HTTP error {e.response.status_code}: {e.response.text}"
                ) from e
            except httpx.RequestError as e:
                raise APIFetchError(f"Authentication network error: {e}") from e

            data = response.json()
            # Check API-level success (inside the JSON body, not just HTTP status)
            if data.get(self.config.response_success_field) != self.config.response_success_value:
                error_msg = _get_nested(data, self.config.response_error_path) or data
                raise APIFetchError(f"Authentication failed — API returned: {error_msg}")

            token = _get_nested(data, auth.token_path, "")
            if not token:
                raise APIFetchError(
                    f"Authentication succeeded but token not found at path '{auth.token_path}'."
                )
            logger.info(f"[{self.config.display_name}] Authentication successful.")
            return token

        raise APIFetchError(f"Unsupported auth type: '{auth.type}'.")

    async def fetch_data(self, token: Optional[str], fetch_params: dict) -> dict:
        """
        Fetches data from the configured endpoint using fetch_params.

        Args:
            token       — auth token from authenticate() (None if auth.type == 'none')
            fetch_params — dict with at minimum 'filters' and 'group_by' keys

        Returns:
            Full parsed JSON response dict.

        Raises:
            APIFetchError — on HTTP error, network failure, or missing config.
        """
        endpoint_val = getattr(settings, self.config.fetch_endpoint_env, "")
        if not endpoint_val:
            raise APIFetchError(
                f"Fetch endpoint env var '{self.config.fetch_endpoint_env}' is not set in .env."
            )

        payload = _resolve_payload(self.config.payload_template, fetch_params)

        headers = dict(self.config.fetch_headers)  # copy
        if token and self.config.auth.inject_as_header:
            headers[self.config.auth.inject_as_header] = token

        logger.info(
            f"[{self.config.display_name}] Fetching data from {endpoint_val} | "
            f"group_by={fetch_params.get('group_by')}"
        )
        logger.info(f"[{self.config.display_name}] Payload: {json.dumps(payload)}")
        print(f"[{self.config.name}] POST {endpoint_val}\nPayload: {json.dumps(payload, indent=2)}")

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(endpoint_val, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.TimeoutException as e:
            raise APIFetchError(f"Fetch request timed out: {e}") from e
        except httpx.HTTPStatusError as e:
            raise APIFetchError(
                f"Fetch HTTP error {e.response.status_code}: {e.response.text}"
            ) from e
        except httpx.RequestError as e:
            raise APIFetchError(f"Fetch network error: {e}") from e

        data = response.json()
        logger.info(
            f"[{self.config.display_name}] Fetch response — "
            f"{self.config.response_success_field}={data.get(self.config.response_success_field)}"
        )
        return data


# ---------------------------------------------------------------------------
# Backward-compat alias — code that imports TransactionLogAPIClient won't
# break immediately, though it should be migrated to GenericApiClient.
# ---------------------------------------------------------------------------
TransactionLogAPIClient = GenericApiClient

