import pandas as pd
from src.domain.interfaces import IDatabaseEngine
from src.services.sql_validator import SQLValidator
from src.core.logger import logger

class SQLExecutor:
    def __init__(self, db_engine: IDatabaseEngine, validator: SQLValidator):
        self.db_engine = db_engine
        self.validator = validator

    def execute(self, dataset_id: str, query: str) -> pd.DataFrame:
        """
        Validates the SQL query and executes it against the DuckDB instance.
        """
        # 1. Validate Query
        valid_query = self.validator.validate_query(query)
        
        # 2. Execute Query
        df = self.db_engine.execute_query(dataset_id, valid_query)
        
        return df
