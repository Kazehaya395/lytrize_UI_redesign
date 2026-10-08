"""modules/report_data.py -- Column-pruned dataset payload for the exported file.

A saved HTML report is self-contained, so live in-file slicers need the
rows to filter. Shipping the whole session frame as base64 JSON does not
scale (measured: ~15 MB at 200k rows, ~150 MB at 2M) and most of those
columns are never plotted nor filtered.

This module answers "which columns, how many rows" once, on the Python
side, so the exporter, the PNG path and the tests all agree.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# Rows shipped in the interactive payload. Above this the frame is sampled
# with a fixed seed so repeated exports of the same board are identical.
MAX_PAYLOAD_ROWS = 200_000

# Columns shipped, hard stop. A board referencing more distinct columns
# than this is almost certainly a recipe/spec disagreement, not a real
# dashboard -- fail closed rather than emit a huge file.
MAX_PAYLOAD_COLS = 64

# Payloads above this many bytes fall back to a static file (no dataset
# embedded). The saved file stays emailable; the banner explains why the
# slicers are static in that copy.
MAX_INLINE_BYTES = 50 * 1024 * 1024

_PAYLOAD_SEED = 42


def payload_columns(recipes: dict, slicers: Optional[list]) -> list:
    """Sorted column names the payload must carry.

    ``recipes`` maps chart uid -> recipe dict. ``slicers`` is the slicer
    definition list. Order is sorted for determinism: the exporter, the
    sidecar writer and the tests must all build the same column list
    from the same board.
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
                         max_rows: int = MAX_PAYLOAD_ROWS) -> dict:
    """Build the ``LYTRIZE_DATA`` payload for *df*.

    Returns ``{"columns", "rows", "dtypes", "truncated", "total_rows"}``.
    ``rows`` is column-major (one list per column) so the browser can
    filter without transposing. Never raises: a payload that cannot be
    built degrades to empty and the file renders static charts.
    """
    empty = {"columns": [], "rows": [], "dtypes": {},
             "truncated": False, "total_rows": 0}
    if df is None or getattr(df, "empty", True):
        return empty
    try:
        cols = payload_columns(recipes, slicers)
        cols = [c for c in cols if c in list(df.columns)]
        if not cols:
            return dict(empty, total_rows=int(len(df)))
        total = int(len(df))
        frame = df[cols]
        truncated = False
        if total > max_rows:
            try:
                frame = frame.sample(n=max_rows, random_state=_PAYLOAD_SEED)
                frame = frame.sort_index()
            except Exception:
                frame = frame.head(max_rows)
            truncated = True
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
                "truncated": truncated, "total_rows": total}
    except Exception:
        log.warning("build_report_payload failed; static export", exc_info=True)
        return empty


def payload_json_size(payload: dict) -> int:
    """Approximate serialised byte size of *payload* without building it."""
    import json
    try:
        return len(json.dumps(payload, separators=(",", ":"),
                              ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
