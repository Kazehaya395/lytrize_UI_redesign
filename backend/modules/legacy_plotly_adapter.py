"""Read-only converter for legacy Plotly JSON saved by older Lytrize builds.

This module accepts plain JSON data only. It never imports Plotly or recreates
legacy figures; new chart generation, rendering, persistence and export use
native ChartModels and ECharts.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

import numpy as np

from modules.chart_model import make_chart_model
from modules.runtime_paths import assets_root


_SUPPORTED_ANALYSES = {"pie_chart", "categorical", "statistical", "time_series",
                       "scatter_plot", "matrix_heatmap", "correlation", "map_plot"}


def _values(value: Any) -> Optional[list]:
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict) and "bdata" in value:
        try:
            dtype = np.dtype(str(value.get("dtype", "f8")))
            raw = np.frombuffer(base64.b64decode(value["bdata"]), dtype=dtype)
            shape = tuple(int(part) for part in str(value.get("shape", "")).split(",")
                          if part.strip())
            return raw.reshape(shape).tolist() if shape else raw.tolist()
        except Exception:
            return None
    try:
        return list(value)
    except TypeError:
        return [value]


def _scalar(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _trace_color(trace: dict) -> Optional[list | str]:
    marker = trace.get("marker") or {}
    color = marker.get("color")
    if color is None:
        line = trace.get("line") or {}
        color = line.get("color")
    if isinstance(color, str):
        return color
    if isinstance(color, np.ndarray):
        return color.tolist()
    if isinstance(color, (list, tuple)):
        return list(color)
    return None


def _legend_display(layout: dict) -> dict:
    legend = layout.get("legend") or {}
    orientation = "vertical" if legend.get("orientation") == "v" else "horizontal"
    try:
        x = float(legend.get("x", 1) or 0)
    except (TypeError, ValueError):
        x = 1
    try:
        y = float(legend.get("y", 0) or 0)
    except (TypeError, ValueError):
        y = 0
    if orientation == "vertical":
        position = "right" if x >= 0.5 else "left"
    else:
        position = ("top center" if y >= 0.5 else "bottom center")
    title = legend.get("title") or {}
    if isinstance(title, str):
        legend_title = title
    else:
        legend_title = title.get("text", "")
    font = legend.get("font") or layout.get("font") or {}
    return {"show": layout.get("showlegend", legend.get("visible", True)),
            "position": position,
            "layout": orientation, "title": legend_title,
            "font_size": font.get("size", 11), "click": True}


def _legacy_choropleth_model(trace: dict, layout: dict, *, uid: str,
                             title: str, meta: Optional[dict]) -> Optional[dict]:
    """Convert old Plotly choropleth JSON onto the bundled Natural Earth map."""
    locations = _values(trace.get("locations")) or []
    values = _values(trace.get("z")) or []
    if not locations or not values:
        return None
    lookup_path = (assets_root() / "echarts" / "maps" / "natural-earth" /
                   "lookup.json")
    try:
        lookup = json.loads(lookup_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw_meta = (meta or {}).get("config") or {}
    state_mode = (str(trace.get("locationmode") or "").lower().startswith("usa")
                  or any(str(value).upper().startswith("US-") for value in locations))
    aliases = lookup.get("us_state_aliases", {}) if state_mode else lookup.get("country_aliases", {})
    names = lookup.get("admin1_names", {}) if state_mode else lookup.get("country_names", {})
    asset = "admin1-50m" if state_mode else "countries-110m"
    map_id = "lytrize_ne_admin1_50m" if state_mode else "lytrize_ne_countries_110m"
    rows = []
    finite_values = []
    for location, value in zip(locations, values):
        normalized = unicodedata.normalize("NFKD", str(location or ""))
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
        key = aliases.get(normalized)
        if not key:
            key = str(location).upper() if str(location).upper() in names else None
        scalar = _scalar(value)
        if key is None or not isinstance(scalar, (int, float)):
            continue
        rows.append([key, scalar, names.get(key, str(location)), {}])
        if math.isfinite(float(scalar)):
            finite_values.append(float(scalar))
    if not rows:
        return None
    minimum = min(finite_values) if finite_values else 0
    maximum = max(finite_values) if finite_values else 1
    if minimum == maximum:
        maximum = minimum + 1
    coloraxis = layout.get("coloraxis") or {}
    display = {
        "title_size": ((layout.get("title") or {}).get("font") or {}).get("size", 20),
        "text_color": (layout.get("font") or {}).get("color"),
        "palette": layout.get("colorway") or [],
        "map_asset": asset, "map_id": map_id, "map_center": [0, 0], "map_zoom": 1,
        "map_projection": "natural earth", "map_borders": True,
        "map_theme": "light", "map_land_color": "#e8edf4",
        "map_border_color": "#9aa8ba", "map_value_title": str(trace.get("colorbar") or "Value"),
        "heatmap_min": minimum, "heatmap_max": maximum,
        "heatmap_colors": coloraxis.get("colorscale") or ["#eff6ff", "#1d4ed8"],
        "show_colorbar": coloraxis.get("showscale", True),
        "colorbar_title": ((coloraxis.get("colorbar") or {}).get("title") or {}).get("text", ""),
        "legend": {"show": False}, "labels": {"show": False},
        "chart_background": layout.get("paper_bgcolor", "transparent"),
    }
    model_title = title or str((layout.get("title") or {}).get("text") or "Choropleth")
    return make_chart_model(
        uid=uid, chart_type="map_plot", title=model_title,
        recipe={"version": 1, "chart_type": "map_plot",
                "config": raw_meta, "layout": {}, "typography": {}},
        data={"dimensions": ["Location", "Value"],
              "map": {"id": map_id, "asset": asset, "mode": "choropleth"},
              "series": [{"id": "choropleth", "name": model_title,
                          "type": "map", "values": rows}]},
        display=display, source={"engine": "legacy_import", "migration": True})


def figure_to_chart_model(fig: Any, *, uid: str, chart_type: str,
                          title: str = "", meta: Optional[dict] = None) -> Optional[dict]:
    """Convert supported legacy figure families into semantic model v1."""
    if chart_type not in _SUPPORTED_ANALYSES or fig is None:
        return None
    try:
        raw = fig if isinstance(fig, dict) else None
        if raw is None:
            return None
        traces = raw.get("data") or []
        layout = raw.get("layout") or {}
    except Exception:
        return None
    if not traces:
        return None

    map_traces = [trace for trace in traces
                  if str(trace.get("type") or "").lower() in
                  {"choropleth", "choroplethmap", "choroplethmapbox"}]
    if chart_type == "map_plot" and map_traces:
        return _legacy_choropleth_model(
            map_traces[0], layout, uid=str(uid), title=title, meta=meta)

    layout_title = layout.get("title") or {}
    if isinstance(layout_title, dict):
        title = title or str(layout_title.get("text") or "")
    title = title or str(layout_title or "")
    series, palette = [], []
    dimensions = []
    labels_display = {"show": True, "position": "outside",
                      "font_size": 11, "color": None}
    model_family = chart_type
    display_donut = False
    display_donut_ratio = 38
    pie_rotation = 0
    pie_direction = "clockwise"
    heatmap_rows, heatmap_columns, heatmap_values = [], [], []

    for index, trace in enumerate(traces):
        kind = str(trace.get("type") or "")
        name = str(trace.get("name") or f"Series {index + 1}")
        color = _trace_color(trace)
        if color is not None:
            if isinstance(color, list):
                palette.extend(c for c in color if isinstance(c, str))
            else:
                palette.append(color)

        if kind == "pie":
            labels = _values(trace.get("labels")) or []
            values = _values(trace.get("values")) or []
            pairs = [[_scalar(label), _scalar(value)]
                     for label, value in zip(labels, values)]
            series.append({"id": str(trace.get("uid") or f"series-{index}"),
                           "name": name, "type": "pie", "values": pairs})
            model_family = "pie_chart"
            info = str(trace.get("textinfo") or "label+percent")
            labels_display["show"] = info != "none"
            labels_display["formatter"] = (
                "label+percent" if "percent" in info else
                "label+value" if "value" in info else "label")
            labels_display["position"] = str(trace.get("textposition") or "outside")
            text_font = trace.get("textfont") or {}
            labels_display["font_size"] = text_font.get("size", 11)
            labels_display["color"] = text_font.get("color")
            hole = float(trace.get("hole") or 0)
            display_donut = hole > 0
            display_donut_ratio = round(max(0, min(0.8, hole)) * 100)
            pie_rotation = int(trace.get("rotation") or 0)
            pie_direction = str(trace.get("direction") or "clockwise")
        elif kind in {"bar", "scatter", "scattergl"}:
            x = _values(trace.get("x")) or []
            y = _values(trace.get("y")) or []
            count = min(len(x), len(y))
            pairs = [[_scalar(x[i]), _scalar(y[i])] for i in range(count)]
            mode = str(trace.get("mode") or "")
            if kind in {"scatter", "scattergl"}:
                # ECharts scatter uses numeric value axes and preserves raw
                # coordinates. Marker-only, line-only and combined traces are
                # all represented as points at this migration boundary.
                series_type = "scatter"
            else:
                series_type = "bar"
            series.append({"id": str(trace.get("uid") or f"series-{index}"),
                           "name": name, "type": series_type, "values": pairs})
            dimensions.extend(str(pair[0]) for pair in pairs if pair[0] is not None)
            if series_type == "scatter":
                model_family = "scatter_plot"
        elif kind == "heatmap":
            x = _values(trace.get("x")) or []
            y = _values(trace.get("y")) or []
            z = _values(trace.get("z")) or []
            if z and isinstance(z[0], list):
                for row_index, row in enumerate(z):
                    for column_index, value in enumerate(row):
                        if row_index < len(y) and column_index < len(x):
                            heatmap_values.append([
                                _scalar(x[column_index]), _scalar(y[row_index]),
                                _scalar(value)])
            heatmap_rows.extend(_scalar(v) for v in y)
            heatmap_columns.extend(_scalar(v) for v in x)
            model_family = "matrix_heatmap"
        else:
            return None

    if model_family == "matrix_heatmap" and heatmap_values:
        series = [{"id": "heatmap", "name": str(traces[0].get("name") or "Value"),
                   "type": "heatmap", "values": heatmap_values}]
        dimensions = list(dict.fromkeys(heatmap_columns + heatmap_rows))
    if not series:
        return None
    first_axis = layout.get("xaxis") or {}
    second_axis = layout.get("yaxis") or {}
    x_title = first_axis.get("title") or {}
    y_title = second_axis.get("title") or {}
    x_title = x_title.get("text", "") if isinstance(x_title, dict) else str(x_title)
    y_title = y_title.get("text", "") if isinstance(y_title, dict) else str(y_title)
    font = layout.get("font") or {}
    colors = layout.get("colorway") or palette
    config = (meta or {}).get("config") or {}
    try:
        title_x = float(layout_title.get("x", 0) or 0) if isinstance(layout_title, dict) else 0
    except (TypeError, ValueError):
        title_x = 0
    title_align = "center" if 0.4 <= title_x <= 0.6 else "right" if title_x > 0.6 else "left"
    model_display = {
        "title_size": (layout_title.get("font") or {}).get("size", 20)
        if isinstance(layout_title, dict) else 20,
        "title_align": title_align,
        "text_color": font.get("color"),
        "legend": _legend_display(layout),
        "labels": labels_display,
        "palette": colors,
        "orientation": "horizontal" if any(t.get("orientation") == "h"
                                             for t in traces) else "vertical",
        "x_title": x_title,
        "y_title": y_title,
        "donut": bool(display_donut),
        "donut_ratio": display_donut_ratio,
        "rotation": pie_rotation,
        "direction": pie_direction,
        "chart_background": layout.get("paper_bgcolor", "transparent"),
        "heatmap_rows": heatmap_rows,
        "heatmap_columns": heatmap_columns,
    }
    if model_family == "pie_chart":
        model_display["orientation"] = "vertical"
    elif any(t.get("orientation") == "h" for t in traces):
        model_display["orientation"] = "horizontal"
    return make_chart_model(
        uid=str(uid), chart_type=model_family, title=title,
        recipe={"version": 1, "config": config},
        data={"dimensions": list(dict.fromkeys(dimensions)), "series": series},
        display=model_display,
        source={"engine": "legacy_import", "migration": True},
    )
