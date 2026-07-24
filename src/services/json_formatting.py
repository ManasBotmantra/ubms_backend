"""
src/services/json_formatting.py
================================
Shared helper for formatting DataFrame date/timestamp columns before JSON
serialization. pandas' DataFrame.to_json(date_format=...) only supports 'iso'
or 'epoch' -- neither of which is DD-MM-YYYY -- so datetime columns are
pre-formatted to plain strings here instead.
"""

import pandas as pd


def format_dates_for_json(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns a copy of df with every datetime64 column formatted as 'DD-MM-YYYY'
    strings (NaT -> None) so charts/tables/LLM context all see a consistent,
    human-readable date format instead of full ISO timestamps.
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].apply(lambda v: v.strftime("%d-%m-%Y") if pd.notna(v) else None)
    return df
