"""modules/utils/perf.py -- Performance utilities for large dataset handling.

Polars-native implementation. All data reading, sampling, and dtype
optimisation now runs through Polars (Rust-backed) for best-in-class
performance and memory efficiency. A thin pandas compatibility shim is
kept only for legacy Excel reading (polars has no native Excel reader).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import polars as pl

log = logging.getLogger(__name__)

# ── Polars-native helpers ─────────────────────────────────────────────────

_CAT_THRESHOLD = 0.50
_CAT_MAX_UNIQ = 1_000


def mem_mb(df: Any) -> float:
    """Return total DataFrame memory usage in megabytes.

    Accepts either a Polars DataFrame or a pandas DataFrame (legacy shim).
    """
    if isinstance(df, pl.DataFrame):
        return df.estimated_size() / 1_048_576
    # pandas fallback
    try:
        return df.memory_usage(deep=True).sum() / 1_048_576  # type: ignore[attr-defined]
    except Exception:
        return 0.0


def optimize_dtypes(df: pl.DataFrame) -> pl.DataFrame:
    """Downcast numeric types and cast low-cardinality strings to Categorical.

    Polars already stores strings efficiently; this cast is mainly for
    downstream code that expects Categorical dtype for grouping.
    """
    exprs = []
    for col in df.columns:
        dtype = df[col].dtype
        if dtype.is_numeric():
            if dtype.is_integer():
                try:
                    mn = df[col].min()
                    mx = df[col].max()
                    if mn is not None and mx is not None:
                        if mn >= -128 and mx <= 127:
                            exprs.append(pl.col(col).cast(pl.Int8))
                        elif mn >= -32768 and mx <= 32767:
                            exprs.append(pl.col(col).cast(pl.Int16))
                        elif mn >= -2147483648 and mx <= 2147483647:
                            exprs.append(pl.col(col).cast(pl.Int32))
                except Exception:
                    pass
        elif dtype == pl.String:
            try:
                n_uniq = df[col].n_unique()
                n_rows = df.height
                ratio = n_uniq / n_rows if n_rows else 0
                if n_uniq <= _CAT_MAX_UNIQ and ratio < _CAT_THRESHOLD:
                    exprs.append(pl.col(col).cast(pl.Categorical))
            except Exception:
                pass
    if exprs:
        return df.with_columns(exprs)
    return df


_SAMPLE_NOTE = (
    "⚠️ Chart rendered from a {n:,}-row sample (dataset has {total:,} rows). "
    "Statistical patterns are preserved."
)


def sample_for_plot(
    df: pl.DataFrame,
    n: int = 50_000,
    random_state: int = 42,
) -> tuple[pl.DataFrame, bool]:
    """Return a representative random sample of at most n rows for ECharts rendering."""
    if df.height <= n:
        return df, False
    return df.sample(n=n, seed=random_state), True


def sample_note(n: int, total: int) -> str:
    """Human-readable note explaining the sample size shown in charts."""
    return _SAMPLE_NOTE.format(n=n, total=total)


def read_csv_fast(file, **kwargs) -> pl.DataFrame:
    """Read a CSV file lazily via Polars scan_csv and return a DataFrame.

    Polars scan_csv is lazy and memory-mapped -- no full materialisation
    until .collect() is called. For UI preview we collect only what we need.
    """
    from services.polars_engine import open_csv, PolarsDataset

    path = file
    if hasattr(file, "read"):
        import tempfile
        import shutil
        suffix = Path(file.name).suffix if hasattr(file, "name") else ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file, tmp)
            path = tmp.name

    ds: PolarsDataset = open_csv(path)
    return ds.frame.collect()


def read_csv_chunked(
    file,
    chunksize: int = 200_000,
    max_rows: int | None = None,
    **kwargs,
) -> pl.DataFrame:
    """Read a large CSV via Polars lazy scan (single pass, memory efficient)."""
    from services.polars_engine import open_csv

    path = file
    if hasattr(file, "read"):
        import tempfile
        import shutil
        suffix = Path(file.name).suffix if hasattr(file, "name") else ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file, tmp)
            path = tmp.name

    ds: PolarsDataset = open_csv(path)
    df = ds.frame.collect()
    if max_rows is not None and df.height > max_rows:
        df = df.head(max_rows)
    return df


def get_sheet_names(file) -> list[str]:
    """Return the list of sheet names without loading any cell data.

    Polars has no native Excel reader; pandas is kept as a bare-minimum
    legacy shim for Excel metadata only.
    """
    import pandas as pd  # legacy shim for Excel metadata only
    if hasattr(file, "seek"):
        file.seek(0)
    with pd.ExcelFile(file) as xl:
        return xl.sheet_names


def read_excel_sheet(file, sheet_name: Any = 0) -> pl.DataFrame:
    """Read a SINGLE sheet from an Excel file via Polars.

    Polars has no native Excel reader; pandas is used as a thin shim then
    converted to Polars immediately.
    """
    import pandas as pd  # legacy shim for Excel only
    if hasattr(file, "seek"):
        file.seek(0)
    pdf = pd.read_excel(file, sheet_name=sheet_name)
    return pl.from_pandas(pdf)


def _pivot_impl(df: pl.DataFrame, index: str, columns: str,
                values: str, aggfunc: str) -> pl.DataFrame:
    """Polars-native pivot table."""
    agg_map = {
        "sum": pl.sum,
        "mean": pl.mean,
        "min": pl.min,
        "max": pl.max,
        "count": pl.count,
    }
    agg_fn = agg_map.get(aggfunc, pl.sum)
    return df.pivot(
        index=index,
        columns=columns,
        values=values,
        aggregate_function=agg_fn,
    )


def cached_pivot(
    df: pl.DataFrame,
    index: str,
    columns: str,
    values: str,
    aggfunc: str,
) -> pl.DataFrame:
    """Polars pivot table wrapper (no Streamlit cache; caller manages caching)."""
    return _pivot_impl(df, index, columns, values, aggfunc)


_HIST_SAMPLE = 50_000


def sample_for_histogram(
    df: pl.DataFrame,
    n: int = _HIST_SAMPLE,
    random_state: int = 42,
) -> tuple[pl.DataFrame, bool]:
    """Sample for histogram / distribution charts."""
    return sample_for_plot(df, n=n, random_state=random_state)


_CAT_MAX_BARS = 50


def top_n_with_other(
    series: pl.Series,
    n: int = _CAT_MAX_BARS,
    other_label: str = "Other",
) -> pl.Series:
    """Keep the top-n most frequent categories and replace the rest with 'Other'."""
    top = series.value_counts().sort("count", descending=True).head(n)
    top_col = "series" if "series" in top.columns else top.columns[0]
    top_vals = set(top[top_col].to_list())
    return pl.when(series.is_in(list(top_vals))).then(series).otherwise(pl.lit(other_label))


RENDER_LIMITS = {
    "scatter": 8_000,
    "histogram": 50_000,
    "map": 10_000,
    "line": 50_000,
    "bar": 5_000,
    "heatmap": 500,
}


def enforce_render_limit(
    df: pl.DataFrame,
    chart_type: str,
    random_state: int = 42,
) -> tuple[pl.DataFrame, bool]:
    """Sample df to the render budget for chart_type."""
    limit = RENDER_LIMITS.get(chart_type, 50_000)
    return sample_for_plot(df, n=limit, random_state=random_state)
