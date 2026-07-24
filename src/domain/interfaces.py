from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from pandas import DataFrame

from src.domain.models import Block, DatasetMetadata

class ILLMClient(ABC):
    @abstractmethod
    async def generate_completion(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        pass

class IDatabaseEngine(ABC):
    @abstractmethod
    def register_dataset(self, dataset_id: str, dfs: Dict[str, DataFrame]) -> None:
        pass
        
    @abstractmethod
    def execute_query(self, dataset_id: str, query: str) -> DataFrame:
        pass

    @abstractmethod
    def dataset_exists(self, dataset_id: str) -> bool:
        pass

class IDatasetService(ABC):
    @abstractmethod
    async def process_upload(self, file_path: str, file_name: str, dataset_id: str) -> str:
        pass
        
    @abstractmethod
    async def get_metadata(self, dataset_id: str) -> DatasetMetadata:
        pass

    @abstractmethod
    async def fetch_from_api(self, filters: dict, group_by: list) -> Tuple[str, DatasetMetadata]:
        """
        Fetches data from the transaction log API, registers it in DuckDB,
        and persists metadata. Returns (dataset_id, DatasetMetadata).
        """
        pass

