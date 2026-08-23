"""services/chart_service.py -- Headless chart utilities.

Replaces the Streamlit-coupled parts of ``modules/charts.py`` with pure
functions that accept a DataFrame (rather than reading ``st.session_state``)
for column-type detection and chart serialisation.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import pandas as pd
import plotly.io as pio

from modules.charts import (
    PALETTES,
    COLORS,
    DANGER,
    chart_layout,
    apply_hover_format,
    _json_safe,
    _fmt_num,
    _fmt_pct,
)

log = logging.getLogger(__name__)

# Re-export the static constants so the Qt UI can use them directly
__all__ = [
    "PALETTES",
    "COLORS",
    "DANGER",
    "chart_layout",
    "apply_hover_format",
    "charts_to_json",
    "charts_from_json",
    "get_chart_meta",
    "set_chart_meta",
    "serialize_chart",
    "deserialize_chart",
    "num_cols",
    "cat_cols",
    "dt_cols",
]


# -- Column-type helpers (pure, no session state) -----------------------

def num_cols(df: pd.DataFrame) -> list:
    """Return the list of numeric column names in *df*."""
    return df.select_dtypes(include="number").columns.tolist()


def cat_cols(df: pd.DataFrame) -> list:
    """Return the list of categorical (object) column names in *df*."""
    return df.select_dtypes(include="object").columns.tolist()


def dt_cols(df: pd.DataFrame) -> list:
    """Return the list of datetime column names in *df*."""
    return df.select_dtypes(include="datetime").columns.tolist()


# -- Chart serialisation (no session state) -----------------------------

def serialize_chart(uid: str, title: str, fig: Any, *,
                    desc: str = "", chart_type: str = "",
                    meta: Optional[dict] = None) -> dict:
    """Serialize a single chart to a JSON-safe dict."""
    return {
        "uid":        uid,
        "title":      title,
        "fig_json":   pio.to_json(fig),
        "desc":       desc,
        "chart_type": chart_type,
        "meta":       _json_safe(meta or {}),
    }


def deserialize_chart(item: dict) -> Optional[tuple]:
    """Deserialize a chart dict into ``(uid, title, fig, desc, chart_type, meta)``."""
    try:
        uid        = item.get("uid", "")
        title      = item.get("title", "")
        desc       = item.get("desc", "")
        chart_type = item.get("chart_type", "")
        meta       = item.get("meta", {})
        fig        = pio.from_json(item["fig_json"])
        return (uid, title, fig, desc, chart_type, meta)
    except Exception as exc:
        log.debug("deserialize_chart: %s", exc)
        return None


def charts_to_json(charts: list, notes: Optional[dict] = None,
                   meta_map: Optional[dict] = None) -> str:
    """Serialize a list of chart tuples to a JSON string.

    Each chart may be a 3-tuple ``(uid, title, fig)`` or a 6-tuple
    ``(uid, title, fig, desc, chart_type, meta)``.
    """
    out = []
    for chart in charts:
        uid, title, fig = chart[:3]
        desc = ""
        chart_type = ""
        meta = {}
        if len(chart) > 3 and chart[3]:
            desc = chart[3]
        elif notes is not None:
            desc = notes.get(uid, "")
        if len(chart) > 4 and chart[4]:
            chart_type = chart[4]
        if len(chart) > 5 and chart[5]:
            meta = chart[5]
        elif meta_map is not None:
            meta = meta_map.get(uid, {})
        try:
            out.append(serialize_chart(uid, title, fig, desc=desc,
                                       chart_type=chart_type, meta=meta))
        except Exception as exc:
            log.debug("charts_to_json: skipping chart %s: %s", uid, exc)
    return json.dumps(out)


def charts_from_json(json_str: str) -> list:
    """Deserialize a ``charts_json`` string into a list of chart tuples."""
    if not json_str:
        return []
    try:
        raw_items = json.loads(json_str)
    except (ValueError, TypeError) as exc:
        log.warning("charts_from_json: failed to parse: %s", exc)
        return []

    charts = []
    for item in raw_items:
        parsed = deserialize_chart(item)
        if parsed is not None:
            charts.append(parsed)
    return charts


# -- Chart metadata helpers (pure, dict-based) --------------------------

def get_chart_meta(meta_map: dict, uid: str) -> dict:
    """Return the metadata dict for a chart uid from a meta map."""
    return meta_map.setdefault(uid, {})


def set_chart_meta(meta_map: dict, uid: str, **kwargs) -> None:
    """Update the metadata dict for a chart uid in a meta map."""
    entry = meta_map.setdefault(uid, {})
    entry.update(kwargs)


# -- Re-export formatting helpers from modules.charts -------------------

def format_number(value: Any) -> str:
    """Format a number with K/M/B suffixes (re-export of _fmt_num)."""
    return _fmt_num(value)


def format_percent(value: Any) -> str:
    """Format a value as a signed percentage (re-export of _fmt_pct)."""
    return _fmt_pct(value)


def safe_filename(name: str, fallback: str = "lytrize_report") -> str:
    """Return a filesystem-safe version of *name*."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)).strip("._")
    return safe or fallback