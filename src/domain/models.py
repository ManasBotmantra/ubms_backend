from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class BlockType(str, Enum):
    MARKDOWN = "MARKDOWN"
    KPI_CARD = "KPI_CARD"
    BAR_CHART = "BAR_CHART"
    LINE_CHART = "LINE_CHART"
    PIE_CHART = "PIE_CHART"
    SCATTER_CHART = "SCATTER_CHART"
    AREA_CHART = "AREA_CHART"
    RADAR_CHART = "RADAR_CHART"
    TABLE = "TABLE"


class Block(BaseModel):
    type: BlockType
    title: Optional[str] = None
    colors: Optional[List[str]] = Field(default=None, description="Theme colors as hex strings")
    data: Any = Field(description="The data for the block. Format depends on the BlockType.")


class ChatMessage(BaseModel):
    role: str = Field(description="Role of the sender, typically 'user' or 'assistant'")
    content: str = Field(description="Content of the message")


class ChatRequest(BaseModel):
    message: str
    dataset_id: Optional[str] = None
    history: List[ChatMessage] = Field(default_factory=list, description="Previous messages in the conversation")


class ChatResponse(BaseModel):
    dataset_id: Optional[str] = None
    blocks: List[Block]


class UploadResponse(BaseModel):
    dataset_id: str
    message: str = "Dataset uploaded successfully"


class DatasetMetadata(BaseModel):
    dataset_id: str
    file_path: str
    file_name: str
    source: str = Field(default="file", description="Data source type: 'file' or API source label")
    schema_info: Dict[str, Dict[str, str]] = Field(description="Table name to (Column name → data type) mapping")
    statistics: Dict[str, Dict[str, Any]] = Field(description="Table name to basic stats for each column")
    schema_hints: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Predefined semantic descriptions of tables/columns from the API config. "
            "Injected into SQL generator and planner prompts for richer LLM context."
        ),
    )
    applied_filters: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "The exact 'filters' dict this dataset was fetched with (API source only). "
            "Lets DataDecisionAgent see what's actually loaded instead of guessing from "
            "conversation history alone."
        ),
    )
    applied_group_by: Optional[List[str]] = Field(
        default=None,
        description="The exact 'group_by' list this dataset was fetched with (API source only).",
    )


# ─── Generic Data Decision Models ────────────────────────────────────────────
# NOTE: TransactionLogFilters and DataFetchParams have been removed.
# The DataDecisionAgent now returns a generic dict for fetch_params,
# validated against the active API's filter_schema at runtime.

class DataDecision(BaseModel):
    """
    Output of the DataDecisionAgent — tells ResponseBuilder whether to fetch
    fresh data and, if so, with what parameters.
    fetch_params is a generic dict containing:
      - 'filters': dict  (keys match the active ApiConfig.filter_schema.fields)
      - 'group_by': list[str]
    """
    needs_fetch: bool = Field(description="True if a new API call should be made")
    fetch_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Generic fetch params dict. Keys: 'filters' (dict) and 'group_by' (list[str])."
    )
    reason: Optional[str] = Field(
        default=None,
        description="LLM's brief explanation of the decision (for logging/debugging)",
    )

