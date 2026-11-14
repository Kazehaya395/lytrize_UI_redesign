"""backend/core/views.py -- Dynamic Filter & Aggregation View Layer.

Provides high-performance, SQL-VIEW style query and aggregation functions
executed lazily inside the Polars engine. Only the final aggregated slice
(< 5,000 - 10,000 rows) is materialized, keeping memory overhead minimal
and UI responsiveness instantaneous.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Union

import pandas as pd
import polars as pl

from services.polars_engine import PolarsDataset, ensure_dataset

log = logging.getLogger(__name__)


def get_aggregated_view(
    dataset: Any,
    dimensions: Union[str, List[str]],
    metric: Optional[str] = None,
    agg: str = "sum",
    filter_specs: Optional[List[dict]] = None,
    sort_by: str = "value_desc",
    top_n: Optional[int] = None,
    *,
    as_pandas: bool = True,
) -> Union[pd.DataFrame, pl.DataFrame]:
    """Execute filtering and aggregation entirely in the Polars engine.

    Materializes only the final compact result as a DataFrame for charting.

    Parameters
    ----------
    dataset : PolarsDataset | pl.DataFrame | pl.LazyFrame | pd.DataFrame
        Source dataset.
    dimensions : str | list[str]
        Categorical dimension column(s) to group by.
    metric : str, optional
        Numeric metric column. If None or agg=='count', row count is calculated.
    agg : str
        Aggregation operation: 'sum', 'mean', 'median', 'min', 'max', 'count'.
    filter_specs : list[dict], optional
        Active slicer filter specifications.
    sort_by : str
        'value_desc', 'value_asc', 'category_asc', 'category_desc', or 'none'.
    top_n : int, optional
        Limit output to top N groups.
    as_pandas : bool
        If True, returns pandas DataFrame; if False, returns pl.DataFrame.
    """
    ds = ensure_dataset(dataset)
    if isinstance(dimensions, str):
        dimensions = [dimensions]
    dimensions = [d for d in dimensions if d and d in ds.schema and d != ds.row_id]

    if not dimensions:
        log.warning("get_aggregated_view: no valid dimensions provided")
        empty_df = pl.DataFrame()
        return empty_df.to_pandas() if as_pandas else empty_df

    # 1. Apply slicers lazily
    if filter_specs:
        ds = ds.apply_filter_specs(filter_specs)

    lf = ds.frame

    # 2. Filter out nulls in dimensions
    valid_dims = pl.all_horizontal([pl.col(d).is_not_null() for d in dimensions])
    lf = lf.filter(valid_dims)

    # 3. Build aggregation expression
    agg_op = (agg or "sum").lower()
    if metric is None or agg_op == "count" or metric not in ds.schema or metric == ds.row_id:
        agg_expr = pl.len().alias("value")
    else:
        m_col = pl.col(metric).cast(pl.Float64, strict=False).fill_nan(None)
        agg_map = {
            "sum": m_col.sum(),
            "mean": m_col.mean(),
            "median": m_col.median(),
            "min": m_col.min(),
            "max": m_col.max(),
            "count": m_col.count(),
        }
        agg_expr = agg_map.get(agg_op, m_col.sum()).alias("value")

    # 4. Group by and aggregate
    grouped = lf.group_by(dimensions).agg(agg_expr)

    # 5. Sort
    if sort_by == "value_desc":
        grouped = grouped.sort("value", descending=True, nulls_last=True)
    elif sort_by == "value_asc":
        grouped = grouped.sort("value", descending=False, nulls_last=True)
    elif sort_by == "category_asc":
        grouped = grouped.sort(dimensions[0], descending=False, nulls_last=True)
    elif sort_by == "category_desc":
        grouped = grouped.sort(dimensions[0], descending=True, nulls_last=True)

    # 6. Apply Top N if requested
    if top_n and top_n > 0:
        grouped = grouped.head(top_n)

    # 7. Materialize only the tiny aggregated view
    result_pl = grouped.collect()
    return result_pl.to_pandas() if as_pandas else result_pl


def get_time_series_view(
    dataset: Any,
    date_col: str,
    metric: Optional[str] = None,
    agg: str = "sum",
    interval: str = "1mo",
    group_col: Optional[str] = None,
    filter_specs: Optional[List[dict]] = None,
    *,
    as_pandas: bool = True,
) -> Union[pd.DataFrame, pl.DataFrame]:
    """Execute time-bucketed aggregation in Polars."""
    ds = ensure_dataset(dataset)
    if date_col not in ds.schema:
        log.warning("get_time_series_view: date column %r not found", date_col)
        empty_df = pl.DataFrame()
        return empty_df.to_pandas() if as_pandas else empty_df

    if filter_specs:
        ds = ds.apply_filter_specs(filter_specs)

    lf = ds.frame.filter(pl.col(date_col).is_not_null())

    # Build aggregation
    agg_op = (agg or "sum").lower()
    if metric is None or agg_op == "count" or metric not in ds.schema:
        agg_expr = pl.len().alias("value")
    else:
        m_col = pl.col(metric).cast(pl.Float64, strict=False).fill_nan(None)
        agg_map = {
            "sum": m_col.sum(),
            "mean": m_col.mean(),
            "median": m_col.median(),
            "min": m_col.min(),
            "max": m_col.max(),
            "count": m_col.count(),
        }
        agg_expr = agg_map.get(agg_op, m_col.sum()).alias("value")

    by_cols = [group_col] if group_col and group_col in ds.schema else []

    try:
        # Group dynamic by date bucket
        ts_lf = lf.sort(date_col).group_by_dynamic(
            date_col,
            every=interval,
            by=by_cols,
        ).agg(agg_expr)
        result = ts_lf.collect()
    except Exception as exc:
        log.debug("group_by_dynamic failed (%s); falling back to standard group_by", exc)
        grouped_dims = [date_col] + by_cols
        result = lf.group_by(grouped_dims).agg(agg_expr).sort(date_col).collect()

    return result.to_pandas() if as_pandas else result

