"""modules/utils/perf.py -- Performance utilities for large dataset handling."""
import logging


import pandas as pd
import numpy as np
import os
from pathlib import Path
from typing import Union




def mem_mb(df: pd.DataFrame) -> float:
    """Return total DataFrame memory usage in megabytes (deep=True)."""
    return df.memory_usage(deep=True).sum() / 1_048_576




_CAT_THRESHOLD = 0.50
_CAT_MAX_UNIQ  = 1_000




def optimize_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Shrink a DataFrame's memory footprint without losing precision or data."""
    modified = {}


    for col in df.columns:
        series = df[col]
        dtype = series.dtype


        if pd.api.types.is_integer_dtype(dtype):
            opt_series = pd.to_numeric(series, downcast="integer")
            if opt_series.dtype != dtype:
                modified[col] = opt_series


        elif dtype == np.float64:
            opt_series = pd.to_numeric(series, downcast="float")
            if opt_series.dtype != dtype:
                modified[col] = opt_series


        elif dtype == object:
            n_uniq = series.nunique()
            n_rows = len(df)
            ratio  = n_uniq / n_rows if n_rows else 0
            if n_uniq <= _CAT_MAX_UNIQ and ratio < _CAT_THRESHOLD:
                modified[col] = series.astype("category")


    if not modified:
        return df


    return df.assign(**modified)




_SAMPLE_NOTE = (
    "⚠️ Chart rendered from a {n:,}-row sample (dataset has {total:,} rows). "
    "Statistical patterns are preserved."
)




def sample_for_plot(
    df: pd.DataFrame,
    n: int = 50_000,
    random_state: int = 42,
) -> tuple[pd.DataFrame, bool]:
    """Return a representative random sample of at most n rows for Plotly rendering."""
    if len(df) <= n:
        return df, False
    return df.sample(n=n, random_state=random_state).reset_index(drop=True), True




def sample_note(n: int, total: int) -> str:
    """Human-readable note explaining the sample size shown in charts."""
    return _SAMPLE_NOTE.format(n=n, total=total)




def read_csv_fast(file, **kwargs) -> pd.DataFrame:
    """Read a CSV file and return a dtype-optimised DataFrame."""
    kwargs.setdefault("low_memory", False)
    if hasattr(file, "seek"):
        file.seek(0)
    

    file_size = 0
    if hasattr(file, "size"):
        file_size = file.size
    elif hasattr(file, "getvalue"):
        try:
            file_size = len(file.getvalue())
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass
    elif isinstance(file, (str, Path)):
        try:
            file_size = os.path.getsize(file)
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass


    if file_size > 30 * 1024 * 1024:
        return read_csv_chunked(file, **kwargs)


    df = pd.read_csv(file, **kwargs)
    return optimize_dtypes(df)




def read_csv_chunked(
    file,
    chunksize: int = 200_000,
    max_rows: int | None = None,
    **kwargs,
) -> pd.DataFrame:
    """Read a large CSV in chunks and concatenate, applying dtype optimisation"""
    kwargs.setdefault("low_memory", False)
    if hasattr(file, "seek"):
        file.seek(0)


    chunks = []
    rows_read = 0


    for chunk in pd.read_csv(file, chunksize=chunksize, **kwargs):
        chunk = optimize_dtypes(chunk)
        chunks.append(chunk)
        rows_read += len(chunk)
        if max_rows is not None and rows_read >= max_rows:
            break


    if not chunks:
        return pd.DataFrame()


    return pd.concat(chunks, ignore_index=True)




def get_sheet_names(file) -> list[str]:
    """Return the list of sheet names without loading any cell data."""
    if hasattr(file, "seek"):
        file.seek(0)
    with pd.ExcelFile(file) as xl:
        return xl.sheet_names




def read_excel_sheet(file, sheet_name: Union[str, int] = 0) -> pd.DataFrame:
    """Read a SINGLE sheet from an Excel file with dtype optimisation."""
    if hasattr(file, "seek"):
        file.seek(0)
    df = pd.read_excel(file, sheet_name=sheet_name)
    return optimize_dtypes(df)




def _pivot_impl(df: pd.DataFrame, index: str, columns: str,
                values: str, aggfunc: str) -> pd.DataFrame:
    """Inner implementation kept at module level so the cache is shared across calls."""
    return pd.pivot_table(
        df, index=index, columns=columns, values=values,
        aggfunc=aggfunc, observed=True,
    )




try:
    import streamlit as _st
    _pivot_impl = _st.cache_data(show_spinner=False, ttl=300)(_pivot_impl)
except Exception as exc:
    logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
    pass




def cached_pivot(
    df: pd.DataFrame,
    index: str,
    columns: str,
    values: str,
    aggfunc: str,
) -> pd.DataFrame:
    """Cached pd.pivot_table wrapper."""
    return _pivot_impl(df, index, columns, values, aggfunc)




_HIST_SAMPLE = 50_000




def sample_for_histogram(
    df: pd.DataFrame,
    n: int = _HIST_SAMPLE,
    random_state: int = 42,
) -> tuple[pd.DataFrame, bool]:
    """Sample for histogram / distribution charts."""
    return sample_for_plot(df, n=n, random_state=random_state)




_CAT_MAX_BARS = 50




def top_n_with_other(
    series: pd.Series,
    n: int = _CAT_MAX_BARS,
    other_label: str = "Other",
) -> pd.Series:
    """Keep the top-n most frequent categories and replace the rest with 'Other'."""
    top = series.value_counts().nlargest(n).index
    return series.where(series.isin(top), other=other_label)




RENDER_LIMITS = {
    "scatter":     8_000,
    "histogram":  50_000,
    "map":        10_000,
    "line":       50_000,
    "bar":         5_000,
    "heatmap":       500,
}




def enforce_render_limit(
    df: pd.DataFrame,
    chart_type: str,
    random_state: int = 42,
) -> tuple[pd.DataFrame, bool]:
    """Sample df to the render budget for chart_type."""
    limit = RENDER_LIMITS.get(chart_type, 50_000)
    return sample_for_plot(df, n=limit, random_state=random_state)
