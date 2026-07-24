import pandas as pd
from typing import Dict
from src.core.logger import logger

class SchemaProfiler:
    def detect_schema(self, dfs: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, str]]:
        """
        Detects the data type of each column in each DataFrame.
        Maps Pandas dtypes to string representations.
        Returns a dictionary mapping table names to their schema.
        """
        logger.info(f"Profiling schema for DataFrames: {list(dfs.keys())}")
        multi_schema_info = {}
        
        for table_name, df in dfs.items():
            schema_info = {}
            for column in df.columns:
                dtype = str(df[column].dtype)
                
                # Simplify dtypes for easier LLM context
                if "int" in dtype:
                    schema_info[column] = "integer"
                elif "float" in dtype:
                    schema_info[column] = "float"
                elif "datetime" in dtype:
                    schema_info[column] = "datetime"
                elif "bool" in dtype:
                    schema_info[column] = "boolean"
                else:
                    schema_info[column] = "string"
            multi_schema_info[table_name] = schema_info
                
        return multi_schema_info
