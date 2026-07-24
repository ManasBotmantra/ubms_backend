"""
src/api/dependencies.py
========================
Dependency injection factories — stateless design for Azure Functions readiness.

Rules:
- DuckDBEngine can be cached (lru_cache) — it's a path wrapper with no mutable state.
- LLM client can be cached — stateless HTTP client (no per-request state stored).
- ApiConfig can be cached — immutable config object.
- All SERVICE factories create fresh instances per request — stateless, no in-memory session.
"""
from functools import lru_cache

from src.config.base import ApiConfig
from src.config.loader import get_active_api_config
from src.infrastructure.duckdb_engine import DuckDBEngine
from src.infrastructure.llm_client import create_llm_client
from src.infrastructure.api_client import GenericApiClient
from src.services.upload_service import UploadService
from src.services.schema_profiler import SchemaProfiler
from src.services.statistics_engine import StatisticsEngine
from src.services.dataset_service import DatasetService
from src.services.sql_validator import SQLValidator
from src.services.sql_generator import SQLGenerator
from src.services.sql_executor import SQLExecutor
from src.services.insight_generator import InsightGenerator
from src.services.planner_agent import PlannerAgent
from src.services.response_builder import ResponseBuilder
from src.services.data_decision_agent import DataDecisionAgent
from src.domain.interfaces import ILLMClient


# ── Cached singletons (safe — no mutable state) ──────────────────────────────

@lru_cache()
def get_db_engine() -> DuckDBEngine:
    """Cached — just a path wrapper, safe to reuse across requests."""
    return DuckDBEngine()


@lru_cache()
def get_llm_client() -> ILLMClient:
    """
    Cached — reads LLM_PROVIDER from settings and creates the matching provider.
    Switch providers by changing LLM_PROVIDER in .env:
      azure_openai | openai | claude | gemini
    """
    return create_llm_client()


@lru_cache()
def get_api_config() -> ApiConfig:
    """
    Cached — loads the active ApiConfig from the registry.
    Switch configs by changing ACTIVE_API_CONFIG in .env.
    """
    return get_active_api_config()


# ── Per-request factories (stateless) ────────────────────────────────────────

def get_api_client() -> GenericApiClient:
    """Fresh per request — no token stored; every call authenticates fresh."""
    return GenericApiClient(get_api_config())


def get_upload_service() -> UploadService:
    return UploadService()


def get_schema_profiler() -> SchemaProfiler:
    return SchemaProfiler()


def get_stats_engine() -> StatisticsEngine:
    return StatisticsEngine()


def get_dataset_service() -> DatasetService:
    return DatasetService(
        db_engine=get_db_engine(),
        upload_service=get_upload_service(),
        schema_profiler=get_schema_profiler(),
        stats_engine=get_stats_engine(),
        api_client=get_api_client(),
        api_config=get_api_config(),
    )


def get_sql_validator() -> SQLValidator:
    return SQLValidator()


def get_data_decision_agent() -> DataDecisionAgent:
    return DataDecisionAgent(
        llm_client=get_llm_client(),
        api_config=get_api_config(),
    )


def get_response_builder() -> ResponseBuilder:
    llm_client = get_llm_client()
    db_engine = get_db_engine()
    api_config = get_api_config()
    return ResponseBuilder(
        planner=PlannerAgent(llm_client, api_config),
        sql_generator=SQLGenerator(llm_client, api_config),
        sql_executor=SQLExecutor(db_engine, get_sql_validator()),
        insight_generator=InsightGenerator(llm_client, api_config),
        data_decision_agent=get_data_decision_agent(),
        dataset_service=get_dataset_service(),
        db_engine=db_engine,
    )
