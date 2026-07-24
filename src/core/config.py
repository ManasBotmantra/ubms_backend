from pydantic_settings import BaseSettings, SettingsConfigDict
import os
import tempfile


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Analytics Chatbot Backend"
    API_V1_STR: str = "/api/v1"

    # ── LLM Provider ──────────────────────────────────────────────────────────
    # Set LLM_PROVIDER in .env to switch between providers.
    # Choices: azure_openai | openai | claude | gemini
    LLM_PROVIDER: str = "azure_openai"

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = "https://your-resource-name.openai.azure.com/"
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_API_VERSION: str = "2025-01-01-preview"
    AZURE_OPENAI_DEPLOYMENT_NAME: str = "gpt-4o"

    # Standard OpenAI (api.openai.com)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # Anthropic Claude
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5"

    # Google Gemini
    GOOGLE_API_KEY: str = ""
    GOOGLE_MODEL: str = "gemini-2.0-flash"

    # ── Active API Config ─────────────────────────────────────────────────────
    # Must match a key registered in src/config/loader.py _API_REGISTRY.
    ACTIVE_API_CONFIG: str = "transaction_log"

    # ── Generic API Credentials ───────────────────────────────────────────────
    # These are referenced from ApiConfig.auth.payload_template via '$USER_ID' etc.
    # Keep adding new credential env vars here as new APIs are added.
    ENDPOINT: str = ""                 # fetch API URL (used by transaction_log config)
    VALIDATE_USER: str = ""            # auth API URL (used by transaction_log config)
    USER_ID: str = ""                  # auth username
    PASSWORD: str = ""                 # auth password
    BILL_SUMMARY_ENDPOINT: str = ""    # fetch API URL (used by bill_summary config)

    # ── Storage ───────────────────────────────────────────────────────────────
    # Uses temp directory to avoid read-only filesystem issues in Azure Functions.
    UPLOAD_DIR: str = os.path.join(tempfile.gettempdir(), "data", "uploads")
    DB_DIR: str = os.path.join(tempfile.gettempdir(), "data", "db")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()


