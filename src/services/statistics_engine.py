import pandas as pd
from typing import Dict, Any
from src.core.logger import logger

class StatisticsEngine:
    def generate_statistics(self, dfs: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, Any]]:
        """
        Generates basic statistics for each column in each DataFrame.
        Includes min, max, distinct count, null count.
        Returns a dictionary mapping table names to their statistics.
        """
        logger.info(f"Generating dataset statistics for: {list(dfs.keys())}")
        multi_stats = {}
        
        for table_name, df in dfs.items():
            stats = {}
            for column in df.columns:
                col_data = df[column]
                
                try:
                    distinct_count = int(col_data.nunique())
                except TypeError:
                    # Fallback for unhashable types like dict or list
                    distinct_count = int(col_data.astype(str).nunique())
                    
                col_stats = {
                    "null_count": int(col_data.isna().sum()),
                    "distinct_count": distinct_count,
                }
                
                # Numeric stats
                if pd.api.types.is_numeric_dtype(col_data):
                    col_stats["min"] = float(col_data.min()) if not pd.isna(col_data.min()) else None
                    col_stats["max"] = float(col_data.max()) if not pd.isna(col_data.max()) else None
                    
                stats[column] = col_stats
            multi_stats[table_name] = stats
            
        return multi_stats
