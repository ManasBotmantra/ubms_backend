import duckdb
import pandas as pd
import os
from pathlib import Path

from src.domain.interfaces import IDatabaseEngine
from src.core.config import settings
from src.core.logger import logger
from src.core.exceptions import SQLExecutionError

class DuckDBEngine(IDatabaseEngine):
    def __init__(self, db_dir: str = settings.DB_DIR):
        import tempfile
        import os
        # If the path is relative (like 'data/db'), forcefully put it in the temp directory
        # This prevents 'Read-only file system' errors in Azure if the setting was overridden.
        if not os.path.isabs(db_dir):
            self.db_dir = Path(tempfile.gettempdir()) / db_dir
        else:
            self.db_dir = Path(db_dir)
            
        self.db_dir.mkdir(parents=True, exist_ok=True)

    def _get_db_path(self, dataset_id: str) -> Path:
        return self.db_dir / f"{dataset_id}.duckdb"

    def dataset_exists(self, dataset_id: str) -> bool:
        """Returns True if the DuckDB file for this dataset_id is already on disk."""
        return self._get_db_path(dataset_id).exists()

    def register_dataset(self, dataset_id: str, dfs: dict[str, pd.DataFrame]) -> None:
        db_path = self._get_db_path(dataset_id)
        logger.info(f"Registering dataset {dataset_id} into DuckDB at {db_path} with tables: {list(dfs.keys())}")
        try:
            with duckdb.connect(str(db_path)) as conn:
                for table_name, df in dfs.items():
                    conn.register(f"temp_{table_name}", df)
                    conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM temp_{table_name}")
            logger.info(f"Dataset {dataset_id} registered successfully.")
        except Exception as e:
            logger.error(f"Failed to register dataset in DuckDB: {str(e)}")
            raise SQLExecutionError(f"Failed to register dataset: {str(e)}")

    def execute_query(self, dataset_id: str, query: str) -> pd.DataFrame:
        db_path = self._get_db_path(dataset_id)
        if not db_path.exists():
            raise SQLExecutionError(f"Database for dataset_id {dataset_id} not found.")

        logger.info(f"Executing query on dataset {dataset_id}:\n{query}")
        try:
            with duckdb.connect(str(db_path)) as conn:
                conn.execute("SET enable_external_access=false")
                result_df = conn.execute(query).df()
            return result_df
        except Exception as e:
            logger.error(f"Error executing query: {str(e)}")
            raise SQLExecutionError(f"Query execution failed: {str(e)}")


