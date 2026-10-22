"""modules/report_data.py -- Column-pruned dataset payload for the exported file.

A saved HTML report is self-contained, so live in-file slicers need the
rows to filter. Shipping only the columns the board actually reads keeps
even large boards portable: ``payload_columns`` intersects chart specs,
slicer columns and KPI recipe columns.

Per user decision (2026-10-19): the downloadable report embeds the FULL
pruned dataset with NO row cap and NO byte cap. Size warnings are the
caller's responsibility; this module never samples.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# Maximum distinct columns shipped. This is a disagreement guard, not a
# data cap: a board referencing more distinct columns than this is almost
# certainly a recipe/spec disagreement, not a real dashboard.
MAX_PAYLOAD_COLS = 256


def _kpi_columns(kpis: Optional[list]) -> set:
    """Column names named by KPI recipes (``_recipe`` or flat keys)."""
    cols: set = set()
    for kpi in (kpis or []):
        if not isinstance(kpi, dict):
            continue
        recipe = kpi.get("_recipe") if isinstance(kpi.get("_recipe"), dict) else kpi
        for key in ("col", "group_col", "metric_col", "filter_col"):
            name = recipe.get(key)
            if name not in (None, ""):
                cols.add(str(name))
        formula = str(recipe.get("formula") or "")
        # Custom formulas name columns directly; pick up bare-word tokens
        # that match real columns at filter time (matched in payload_columns).
        if recipe.get("kpi_type") == "custom" and formula:
            import re
            cols.update(re.findall(r"[A-Za-z_][A-Za-z0-9_ ]*", formula))
    return {c.strip() for c in cols if c and c.strip()}


def payload_columns(recipes: dict, slicers: Optional[list],
                    kpis: Optional[list] = None) -> list:
    """Sorted column names the payload must carry.

    ``recipes`` maps chart uid -> recipe dict. ``slicers`` is the slicer
    definition list. ``kpis`` is the KPI definition list (recipes included
    so in-file KPI recompute never misses a column). Order is sorted for
    determinism: the exporter and the tests must build the same column
    list from the same board.
    """
    from modules.chartspec import chart_spec, spec_filter_columns

    cols: set = set()
    for recipe in (recipes or {}).values():
        if not isinstance(recipe, dict):
            continue
        try:
            cols |= set(spec_filter_columns(chart_spec(recipe)))
        except Exception:
            log.debug("payload_columns: bad recipe skipped", exc_info=True)
    for s in (slicers or []):
        if isinstance(s, dict) and s.get("column"):
            cols.add(str(s["column"]))
    cols |= _kpi_columns(kpis)
    # Date/group helper columns some recipes read implicitly (period cols).
    for recipe in (recipes or {}).values():
        if not isinstance(recipe, dict):
            continue
        config = recipe.get("config") if isinstance(recipe.get("config"), dict) else {}
        for key in ("date_col", "time_col", "period_col", "group_col",
                    "filter_col", "x_col", "y_col"):
            name = config.get(key)
            if name not in (None, ""):
                cols.add(str(name))
    return sorted(c for c in cols if c)[:MAX_PAYLOAD_COLS]
def _json_cell(value: Any) -> Any:
    """One cell value, JSON-safe."""
    if value is None:
        return None
    try:
        import pandas as pd  # local: module must import without pandas
        if value is pd.NaT or (isinstance(value, float) and value != value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
    except ImportError:
        pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, float) and (value != value):
        return None
    return value


def build_report_payload(df, recipes: dict, slicers: Optional[list],
                         kpis: Optional[list] = None,
                         max_rows: Optional[int] = None) -> dict:
    """Build the ``LYTRIZE_DATA`` payload for *df*.

    Returns ``{"columns", "rows", "dtypes", "truncated", "total_rows"}``.
    ``rows`` is column-major (one list per column) so the browser can
    filter without transposing. Never raises: a payload that cannot be
    built degrades to empty and the file renders static charts.

    NO row cap: the full pruned frame is always embedded (user decision
    2026-10-19). ``max_rows`` is accepted for backward compatibility and
    ignored. ``truncated`` is always False.
    """
    empty = {"columns": [], "rows": [], "dtypes": {},
             "truncated": False, "total_rows": 0}
    if df is None or getattr(df, "empty", True):
        return empty
    try:
        cols = payload_columns(recipes, slicers, kpis)
        frame_cols = list(df.columns)
        cols = [c for c in cols if c in frame_cols]
        total = int(len(df))
        if not cols:
            return dict(empty, total_rows=total)
        frame = df[cols]
        rows = []
        for col in cols:
            try:
                series = frame[col]
                if str(series.dtype) == "category":
                    series = series.astype(str)
                rows.append([_json_cell(v) for v in series.tolist()])
            except Exception:
                log.debug("build_report_payload: column %r skipped", col,
                          exc_info=True)
                rows.append([None] * len(frame))
        dtypes = {}
        for col in cols:
            try:
                import pandas as pd
                dt = df[col].dtype
                if pd.api.types.is_numeric_dtype(dt):
                    dtypes[col] = "number"
                elif pd.api.types.is_datetime64_any_dtype(dt):
                    dtypes[col] = "datetime"
                elif pd.api.types.is_bool_dtype(dt):
                    dtypes[col] = "boolean"
                else:
                    dtypes[col] = "string"
            except Exception:
                dtypes[col] = "string"
        return {"columns": cols, "rows": rows, "dtypes": dtypes,
                "truncated": False, "total_rows": total}
    except Exception:
        log.warning("build_report_payload failed; static export", exc_info=True)
        return empty


def build_report_payload_polars(dataset, recipes: dict,
                                slicers: Optional[list],
                                kpis: Optional[list] = None) -> dict:
    """Build the same payload from a lazy Polars dataset (no pandas copy).

    Collects only the pruned columns. Falls back to empty (static file)
    on any failure.
    """
    empty = {"columns": [], "rows": [], "dtypes": {},
             "truncated": False, "total_rows": 0}
    if dataset is None:
        return empty
    try:
        cols = payload_columns(recipes, slicers, kpis)
        schema = dataset.schema
        cols = [c for c in cols if c in schema]
        if not cols:
            try:
                total = int(dataset.frame.select("__row_id__").count()
                            if "__row_id__" in schema else dataset.frame.count())
            except Exception:
                total = 0
            return dict(empty, total_rows=total)
        import polars as pl
        frame = dataset.frame.select([pl.col(c) for c in cols]).collect()
        total = int(frame.height)
        rows = []
        for col in cols:
            try:
                rows.append([_json_cell(v) for v in frame[col].to_list()])
            except Exception:
                rows.append([None] * total)
        dtypes = {}
        for col in cols:
            try:
                dt = schema[col]
                if dt.is_numeric():
                    dtypes[col] = "number"
                elif dt.is_temporal():
                    dtypes[col] = "datetime"
                elif dt == pl.Boolean:
                    dtypes[col] = "boolean"
                else:
                    dtypes[col] = "string"
            except Exception:
                dtypes[col] = "string"
        return {"columns": cols, "rows": rows, "dtypes": dtypes,
                "truncated": False, "total_rows": total}
    except Exception:
        log.warning("build_report_payload_polars failed; static export",
                    exc_info=True)
        return empty


def payload_json_size(payload: dict) -> int:
    """Approximate serialised byte size of *payload* without building it."""
    import json
    try:
        return len(json.dumps(payload, separators=(",", ":"),
                              ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
