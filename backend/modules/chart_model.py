"""JSON-only semantic chart model shared by data and renderer adapters.

The model describes chart meaning (data, recipe and display intent), not
Plotly figures or ECharts runtime state. A reader validates the known v1
fields and returns a normalized document before handing it to a renderer.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

CHART_MODEL_VERSION = 1


class ChartModelError(ValueError):
    """Raised when a chart model is malformed or cannot be JSON encoded."""


_isfinite = math.isfinite


def _safe(value: Any, path: str = "model") -> Any:
    # Fast paths use exact type checks: this walks every point of every chart,
    # so per-element isinstance() and per-element string building (the old
    # path embedded f"model[{i}]" into EVERY error path) dominated chart
    # build, live-edit and export times. Paths are still reported, just not
    # per-index.
    if value is None:
        return value
    t = type(value)
    if t is str or t is bool or t is int:
        return value
    if t is float:
        if not _isfinite(value):
            raise ChartModelError(f"{path} contains a non-finite number")
        return value
    if t is list or t is tuple:
        out = []
        append = out.append
        for item in value:
            append(_safe(item, path))
        return out
    if t is dict:
        out = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ChartModelError(f"{path} has a non-string key")
            out[key] = _safe(item, path)
        return out
    raise ChartModelError(f"{path} contains unsupported {t.__name__}")


def make_chart_model(*, uid: str, chart_type: str, title: str,
                     data: dict, recipe: Optional[dict] = None,
                     display: Optional[dict] = None,
                     source: Optional[dict] = None) -> dict:
    """Build a validated semantic chart model v1."""
    if not isinstance(uid, str) or not uid.strip():
        raise ChartModelError("chart uid must be a non-empty string")
    if not isinstance(chart_type, str) or not chart_type.strip():
        raise ChartModelError("chart_type must be a non-empty string")
    if not isinstance(title, str):
        raise ChartModelError("chart title must be a string")
    if not isinstance(data, dict):
        raise ChartModelError("chart data must be an object")
    if not isinstance(data.get("dimensions", []), list):
        raise ChartModelError("data.dimensions must be a list")
    if not isinstance(data.get("series", []), list):
        raise ChartModelError("data.series must be a list")
    for index, series in enumerate(data.get("series", [])):
        if not isinstance(series, dict):
            raise ChartModelError(f"data.series[{index}] must be an object")
        if not isinstance(series.get("id"), str) or not series["id"]:
            raise ChartModelError(f"data.series[{index}].id is required")
        if not isinstance(series.get("values"), list):
            raise ChartModelError(f"data.series[{index}].values must be a list")

    return {
        "schema_version": CHART_MODEL_VERSION,
        "uid": uid,
        "kind": "chart",
        "chart_type": chart_type,
        "title": title,
        "recipe": _safe(recipe or {}, "recipe"),
        "data": _safe(data, "data"),
        "display": _safe(display or {}, "display"),
        "source": _safe(source or {}, "source"),
    }


def validate_chart_model(value: Any) -> dict:
    """Validate a decoded or caller-supplied chart model."""
    if not isinstance(value, dict):
        raise ChartModelError("chart model must be an object")
    version = value.get("schema_version")
    if isinstance(version, bool) or version != CHART_MODEL_VERSION:
        raise ChartModelError(f"unsupported chart model version: {version!r}")
    return make_chart_model(
        uid=value.get("uid"), chart_type=value.get("chart_type"),
        title=value.get("title", ""), data=value.get("data"),
        recipe=value.get("recipe"), display=value.get("display"),
        source=value.get("source"),
    )


def chart_model_to_json(value: dict) -> str:
    """Serialize a validated model in a stable representation."""
    return json.dumps(validate_chart_model(value), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def chart_model_from_json(payload: str) -> dict:
    try:
        raw = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ChartModelError(f"invalid chart model JSON: {exc}") from exc
    return validate_chart_model(raw)
