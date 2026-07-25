import json
import re
import uuid
import datetime
import pandas as pd
from pathlib import Path
from typing import Optional, Tuple

from src.config.base import ApiConfig, TableMappingConfig
from src.domain.interfaces import IDatasetService, IDatabaseEngine
from src.domain.models import DatasetMetadata
from src.services.upload_service import UploadService
from src.services.schema_profiler import SchemaProfiler
from src.services.statistics_engine import StatisticsEngine
from src.core.logger import logger
from src.core.exceptions import DatasetNotFoundError, DatasetUploadError, APIFetchError


def _get_nested(data: dict, dot_path: str):
    """Traverses a nested dict using a dot-separated path."""
    parts = dot_path.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


# Matches an unambiguous ISO 8601 date (optionally with a time component), e.g.
# "2026-04-01" or "2026-04-01T00:00:00". The leading 4-digit year makes the remaining
# month/day fields unambiguous, so these must NEVER be parsed with dayfirst=True --
# doing so swaps day/month whenever both are <=12 (e.g. billing_period, which is always
# YYYY-MM-01, would get corrupted on every single value: "2026-04-01" -> "2026-01-04").
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Mirrors duckdb_engine._SAFE_DATASET_ID_RE. dataset_id is caller-supplied and gets
# interpolated into filesystem paths, so anything with a path separator or a dot is
# rejected outright rather than sanitised.
_SAFE_DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class DatasetService(IDatasetService):
    def __init__(
        self,
        db_engine: IDatabaseEngine,
        upload_service: UploadService,
        schema_profiler: SchemaProfiler,
        stats_engine: StatisticsEngine,
        api_client=None,   # GenericApiClient — optional for backward compat
        api_config: Optional[ApiConfig] = None,
    ):
        self.db_engine = db_engine
        self.upload_service = upload_service
        self.schema_profiler = schema_profiler
        self.stats_engine = stats_engine
        self.api_client = api_client
        self.api_config = api_config

    # ─── Existing: File-based upload ─────────────────────────────────────────

    async def process_upload(self, file_path: str, file_name: str, dataset_id: str) -> str:
        """
        Reads file into pandas, profiles it, registers in DuckDB, and saves metadata.
        """
        logger.info(f"Processing dataset {dataset_id} from {file_path}")

        try:
            if file_path.endswith('.csv'):
                dfs = {"dataset": pd.read_csv(file_path)}
            elif file_path.endswith('.xlsx'):
                dfs = {"dataset": pd.read_excel(file_path)}
            elif file_path.endswith('.json'):
                dfs = {}
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Handle nested API response format
                if isinstance(data, dict) and "response_body" in data:
                    body = data["response_body"]
                    if isinstance(body, dict):
                        if "summary" in body:
                            dfs["summary"] = pd.DataFrame([body["summary"]])
                        for key, val in body.items():
                            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                                dfs[key] = pd.DataFrame(val)
                    if not dfs:
                        dfs["dataset"] = pd.json_normalize(body)
                else:
                    dfs["dataset"] = pd.read_json(file_path)
            else:
                raise DatasetUploadError("Unsupported file type during processing")

            dfs = self._auto_parse_dates(dfs)
            schema_info = self.schema_profiler.detect_schema(dfs)
            statistics = self.stats_engine.generate_statistics(dfs)

            self.db_engine.register_dataset(dataset_id, dfs)

            metadata = DatasetMetadata(
                dataset_id=dataset_id,
                file_path=file_path,
                file_name=file_name,
                source="file",
                schema_info=schema_info,
                statistics=statistics,
            )

            meta_path = self.db_engine.db_dir / f"{dataset_id}.meta.json"
            with open(meta_path, "w") as f:
                f.write(metadata.model_dump_json())

            logger.info(f"Successfully processed dataset {dataset_id}")
            return dataset_id

        except Exception as e:
            logger.error(f"Error processing dataset: {str(e)}")
            raise DatasetUploadError(f"Error processing dataset: {str(e)}")

    # ─── New: Generic API-based fetch ─────────────────────────────────────────

    async def fetch_from_api(self, filters: dict, group_by: list, authorize_token: Optional[str] = None) -> Tuple[str, DatasetMetadata]:
        """
        Fetches data from the configured API, parses the response into DataFrames
        using the active ApiConfig.tables mapping, registers them in DuckDB,
        and persists metadata (including schema_hints from the ApiConfig).

        Args:
            filters  — filter dict (keys match the active ApiConfig.filter_schema.fields)
            group_by — list of grouping dimension strings

        Returns:
            (dataset_id, DatasetMetadata)

        Raises:
            APIFetchError — authentication failure, network error, or API error
        """
        if self.api_client is None or self.api_config is None:
            raise APIFetchError(
                "API client / config is not set. Check ACTIVE_API_CONFIG and endpoint settings in .env."
            )

        logger.info(
            f"fetch_from_api | api={self.api_config.name} | "
            f"group_by={group_by} | filters_keys={list(filters.keys())}"
        )

        # Step 1: Authenticate (or use passed token)
        try:
            if self.api_config.auth.type == "passed_token":
                token = authorize_token
            else:
                token = await self.api_client.authenticate()
        except APIFetchError:
            raise
        except Exception as e:
            raise APIFetchError(f"Unexpected auth error: {e}") from e

        # Step 2: Fetch data
        fetch_params = {"filters": filters, "group_by": group_by}
        try:
            raw_response = await self.api_client.fetch_data(token, fetch_params)
        except APIFetchError:
            raise
        except Exception as e:
            raise APIFetchError(f"Unexpected fetch error: {e}") from e

        # Step 3: Validate API-level response
        api_status = raw_response.get(self.api_config.response_success_field)
        if api_status != self.api_config.response_success_value:
            error_msg = (
                _get_nested(raw_response, self.api_config.response_error_path)
                or raw_response.get("status_description", "Unknown API error")
            )
            raise APIFetchError(
                f"API '{self.api_config.name}' returned status {api_status}: {error_msg}"
            )

        body = raw_response.get(self.api_config.response_data_root, {})

        # Step 4: Build DataFrames from response body using config table mappings
        dfs = self._parse_response_body(body)

        # Step 5: Auto-parse date columns
        dfs = self._auto_parse_dates(dfs)

        # Step 6: Profile schema and generate statistics
        schema_info = self.schema_profiler.detect_schema(dfs)
        statistics = self.stats_engine.generate_statistics(dfs)

        # Step 7: Register in DuckDB
        dataset_id = str(uuid.uuid4())
        self.db_engine.register_dataset(dataset_id, dfs)

        # Step 8: Build schema_hints from ApiConfig.duckdb_schema_hints
        # Convert to plain dict (JSON-serialisable) for storage in metadata
        schema_hints = None
        if self.api_config.duckdb_schema_hints:
            schema_hints = {
                table_name: {
                    "description": hint.description,
                    "columns": {
                        col_name: {"type": col.type, "description": col.description}
                        for col_name, col in hint.columns.items()
                    },
                }
                for table_name, hint in self.api_config.duckdb_schema_hints.items()
            }

        # Step 9: Persist metadata
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        metadata = DatasetMetadata(
            dataset_id=dataset_id,
            file_path=f"api://{self.api_config.name}",
            file_name=f"{self.api_config.name}_{timestamp_str}",
            source=self.api_config.dataset_source_label,
            schema_info=schema_info,
            statistics=statistics,
            schema_hints=schema_hints,
            applied_filters=filters,
            applied_group_by=group_by,
        )
        meta_path = self.db_engine.db_dir / f"{dataset_id}.meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(meta_path, "w") as f:
            f.write(metadata.model_dump_json())

        logger.info(
            f"fetch_from_api complete — dataset_id={dataset_id} | tables={list(dfs.keys())}"
        )
        return dataset_id, metadata

    def _parse_response_body(self, body: dict) -> dict:
        """
        Parses the API response body into named DataFrames using the ApiConfig
        table mappings. This replaces the previous hardcoded summary/grouped_results logic.
        """
        dfs = {}
        for table_cfg in self.api_config.tables:
            raw = _get_nested(body, table_cfg.path) if "." in table_cfg.path else body.get(table_cfg.path)

            if table_cfg.type == "single_row":
                if raw and isinstance(raw, dict):
                    dfs[table_cfg.name] = pd.DataFrame([raw])
                    logger.info(f"Table '{table_cfg.name}': 1 summary row")
                else:
                    logger.warning(f"Table '{table_cfg.name}': expected dict at path '{table_cfg.path}', got {type(raw)}")
                    dfs[table_cfg.name] = pd.DataFrame()

            elif table_cfg.type == "list_of_dicts":
                if raw and isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], dict):
                    dfs[table_cfg.name] = pd.DataFrame(raw)
                    logger.info(f"Table '{table_cfg.name}': {len(raw)} rows")
                else:
                    # Empty results — create empty DataFrame with the known schema
                    # so LLM schema profiling still works and SQL can reference columns.
                    # Cast to pandas' "string" extension dtype (not plain object) — a
                    # zero-row object column carries no type info, and DuckDB's pandas
                    # scanner then guesses a default (often INTEGER) instead of VARCHAR,
                    # causing type-mismatch errors when compared against populated
                    # VARCHAR columns from other tables. "string" dtype is unambiguous
                    # regardless of row count.
                    logger.warning(
                        f"Table '{table_cfg.name}': no rows returned — registering empty table "
                        f"with {len(table_cfg.fallback_columns)} fallback columns"
                    )
                    dfs[table_cfg.name] = pd.DataFrame(columns=table_cfg.fallback_columns).astype("string")

        return dfs

    # ─── Shared helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _auto_parse_dates(dfs: dict) -> dict:
        """
        Attempts to convert string columns that look like datetimes to pandas
        datetime dtype, improving DuckDB compatibility for time-series queries.

        dayfirst is only applied to genuinely ambiguous formats (e.g. a "05/06/2026"
        from an uploaded file). A sample that's already unambiguous ISO 8601
        ("YYYY-MM-DD...", as every API response in this codebase emits via
        datetime.isoformat()) is always parsed WITHOUT dayfirst -- see _ISO_DATE_RE's
        comment for why forcing dayfirst on that format silently corrupts the data.
        """
        for df_name, df in dfs.items():
            for col in df.select_dtypes(include=['object']).columns:
                try:
                    first_val = str(df[col].dropna().iloc[0]) if not df[col].dropna().empty else ""
                    if not (any(c.isdigit() for c in first_val) and ('-' in first_val or '/' in first_val)):
                        continue
                    if _ISO_DATE_RE.match(first_val):
                        df[col] = pd.to_datetime(df[col])
                    else:
                        df[col] = pd.to_datetime(df[col], dayfirst=True)
                except Exception:
                    pass
        return dfs

    async def get_metadata(self, dataset_id: str) -> DatasetMetadata:
        # dataset_id arrives from the request body and is interpolated into a path,
        # so it must be shape-checked before use or "../" escapes db_dir and turns
        # this into an arbitrary-file read.
        if not isinstance(dataset_id, str) or not _SAFE_DATASET_ID_RE.match(dataset_id):
            raise DatasetNotFoundError("Invalid dataset_id.")

        meta_path = self.db_engine.db_dir / f"{dataset_id}.meta.json"
        if not meta_path.exists():
            raise DatasetNotFoundError(f"Metadata for dataset {dataset_id} not found.")

        with open(meta_path, "r") as f:
            data = json.load(f)
            return DatasetMetadata(**data)
