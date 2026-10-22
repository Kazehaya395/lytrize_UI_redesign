"""Polars IQR outlier detection and renderer-neutral chart entry points.

The dataframe compatibility functions accept the current pandas session frame
at the UI boundary. Calculations and chart materialization run through
``PolarsDataset``; row actions can use the per-snapshot row IDs returned by the
detector.
"""

from __future__ import annotations

import hashlib

import pandas as pd

from services.polars_engine import PolarsDataset, pl
from services.polars_chart_service import (
    build_outlier_charts, detect_outliers_polars,
)


def _dataset(frame) -> PolarsDataset:
    return frame if isinstance(frame, PolarsDataset) else PolarsDataset.from_pandas(frame)


def detect_outliers(frame, cols: list, multiplier: float = 1.5) -> dict:
    """Return IQR statistics, stable row IDs, and pandas-index compatibility.

    ``row_ids`` are the authoritative IDs within the analyzed snapshot.
    ``out_indices`` remains for callers from older builds during migration.
    """
    dataset = _dataset(frame)
    results = detect_outliers_polars(dataset, cols, multiplier)
    if isinstance(frame, pd.DataFrame):
        index_values = list(frame.index)
        for info in results.values():
            info["out_indices"] = [index_values[row_id] for row_id in info["row_ids"]
                                   if 0 <= row_id < len(index_values)]
    else:
        for info in results.values():
            info["out_indices"] = list(info["row_ids"])
    return results


def remove_outlier_rows(frame, indices: list = None, *, row_ids: list = None):
    """Return a new frame with selected outliers removed.

    New callers pass snapshot ``row_ids``. The older ``indices`` argument
    retains its pandas-index behavior for saved/in-flight legacy callers.
    """
    if isinstance(frame, PolarsDataset):
        selected = row_ids if row_ids is not None else indices
        selected = list(selected or [])
        if not selected:
            return frame
        filtered = frame.frame.filter(~pl.col(frame.row_id).is_in(selected))
        revision = hashlib.sha256(
            f"{frame.revision}:removed:{sorted(map(str, selected))}".encode()
        ).hexdigest()
        return PolarsDataset(filtered, frame.source, revision, frame.row_id)

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("remove_outlier_rows requires a pandas DataFrame or PolarsDataset")
    if row_ids is None:
        return frame.drop(index=list(indices or [])).reset_index(drop=True)
    positions = {int(value) for value in row_ids
                 if isinstance(value, int) and 0 <= value < len(frame)}
    keep = [position for position in range(len(frame)) if position not in positions]
    return frame.iloc[keep].reset_index(drop=True)


def run_outlier(df, x_cols=None, y_cols=None, palette=None, roles=None,
                **kwargs):
    """Build native ChartModels for configured columns, with Polars IQRs."""
    dataset = kwargs.get("polars_dataset")
    if not isinstance(dataset, PolarsDataset):
        dataset = _dataset(df)
    role_columns = list((roles or {}).get("numeric") or [])
    selected = x_cols or y_cols or role_columns or dataset.roles().get("numeric", [])[:6]
    models = build_outlier_charts(
        dataset, selected, multiplier=kwargs.get("multiplier", 1.5),
        typography=kwargs.get("typography"),
    )
    return [(model["title"], model) for model in models]


OUTLIER_HELP = (
    "**📊 How to read Outlier charts:**  "
    "Each dot is one row in your dataset. "
    "**🔴 Red × marks** are outliers — values that fall outside the IQR boundaries (dashed lines). "
    "**Hover** over any point to see its source row ID and value. "
    "The source row ID identifies the row in the analyzed dataset snapshot.\n\n"
    "**Business use:** Outliers often signal data-entry errors, fraud, returns, or exceptional events. "
    "Investigate red points before running predictive models — they can skew results significantly."
)
