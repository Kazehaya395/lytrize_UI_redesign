"""Native Polars chart-data builders for the renderer-neutral chart contract.

This service is the replacement path for chart families as they migrate. It
does not import Plotly or create a rendered figure. Migrated Analysis families
aggregate with Polars and render through ECharts in the Analysis card.
"""

from __future__ import annotations

import math
import json
import logging
import re
import unicodedata
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from modules.chart_model import make_chart_model, validate_chart_model
from modules.runtime_paths import assets_root as _assets_root_fn

_ASSETS_ROOT = _assets_root_fn()
from services.polars_engine import (
    PolarsDataset, _aggregate_expression, _json_scalar, _parse_dates, pl,
)


_AGGREGATIONS = {
    "avg": "mean", "average": "mean", "mean": "mean",
    "sum": "sum", "median": "median", "count": "count",
    "min": "min", "max": "max",
}
_SORTS = {
    "value (desc)": "value_desc", "value ↓": "value_desc",
    "value (asc)": "value_asc", "value ↑": "value_asc",
    "category (a-z)": "category_asc", "category a→z": "category_asc",
    "category (z-a)": "category_desc", "category z→a": "category_desc",
}

log = logging.getLogger(__name__)

def _numeric_values_expr(name: str, schema) -> "pl.Expr":
    """A Float64 expression for a column that may hold currency / thousands.

    Real-world exports carry numbers as text: "$1,234.56", "2,000",
    "(500)" (accounting negatives), "30%" and padded values. A bare
    ``cast(pl.Float64, strict=False)`` turns every one of those into null,
    which silently emptied histograms and dropped scatter points for such
    columns. Strip the decoration and parse; numeric columns keep the plain
    cast path.
    """
    dtype = None
    try:
        dtype = schema.get(name) if hasattr(schema, "get") else None
    except Exception:
        dtype = None
    try:
        numeric = dtype is not None and (dtype.is_numeric() or dtype == pl.Boolean)
    except Exception:
        numeric = False
    if numeric:
        return pl.col(name).cast(pl.Float64, strict=False).fill_nan(None)
    return (pl.col(name).cast(pl.String, strict=False).fill_null("")
            .str.replace_all(r"\(\s*([^()]*)\s*\)", "-${1}")
            # Strip ANY currency symbol (₹, $, €, £, ¥, ...) plus grouping
            # commas, percent signs and whitespace. The old [,$%\s] class kept
            # ₹ and friends, which nulled out every value in an INR-formatted
            # column -- the chart then drew an empty histogram and the bins
            # slider appeared to do nothing at all.
            .str.replace_all(r"\p{Sc}|[,\s%]", "")
            .cast(pl.Float64, strict=False).fill_nan(None))


def _palette(name: Any) -> list[str]:
    try:
        from modules.charts import COLORS, PALETTES
        return list(PALETTES.get(str(name), COLORS))
    except Exception:
        return ["#6163df", "#8566fc", "#3390c8", "#f59e0b",
                "#ef4444", "#10b981", "#ec4899", "#f97316"]


def _heatmap_palette(name: Any, diverging: bool) -> list[str]:
    scales = {
        "rdbu": ["#2166ac", "#f7f7f7", "#b2182b"],
        "rdylbu": ["#2c7bb6", "#ffffbf", "#d7191c"],
        "rdylgn": ["#1a9850", "#ffffbf", "#d73027"],
        "prgn": ["#7b3294", "#f7f7f7", "#008837"],
        "piyg": ["#c51b7d", "#f7f7f7", "#4d9221"],
        "brbg": ["#8c510a", "#f5f5f5", "#01665e"],
        "spectral": ["#3288bd", "#ffffbf", "#d53e4f"],
        "blues": ["#f7fbff", "#6baed6", "#08306b"],
        "viridis": ["#440154", "#21918c", "#fde725"],
        "plasma": ["#0d0887", "#cc4778", "#f0f921"],
        "magma": ["#000004", "#b73779", "#fcfdbf"],
        "cividis": ["#00204c", "#7d7c78", "#fee838"],
        "turbo": ["#30123b", "#a4fc3c", "#7a0403"],
        "inferno": ["#000004", "#bc3754", "#fcffa4"],
        "greys": ["#ffffff", "#969696", "#000000"],
        "ylgnbu": ["#ffffd9", "#41b6c4", "#081d58"],
        "ylorrd": ["#ffffb2", "#fd8d3c", "#800026"],
        "oranges": ["#fff5eb", "#fd8d3c", "#7f2704"],
        "greens": ["#f7fcf5", "#74c476", "#00441b"],
        "pubu": ["#fff7fb", "#67a9cf", "#023858"],
        "bupu": ["#f7fcfd", "#8c96c6", "#4d004b"],
        "greens": ["#f7fcf5", "#74c476", "#00441b"],
        "reds": ["#fff5f0", "#fb6a4a", "#67000d"],
    }
    fallback = "rdbu" if diverging else "blues"
    return list(scales.get(str(name or fallback).strip().lower(), scales[fallback]))


def _heatmap_display_options(layout: Optional[dict], typography: Optional[dict],
                             previous: Optional[dict] = None,
                             diverging: bool = True,
                             colorscale: Optional[str] = None,
                             default_title: str = "") -> dict:
    """Heatmap colour-bar / annotation options, shared by build + live edit.

    The GENERATION path and the LIVE-PREVIEW path must agree, otherwise a
    chart looks one way right after Generate and changes appearance the
    moment it is edited. Both used to compute these separately -- the
    generation path hard-coded its own defaults (legend hidden, annotations
    forced on at size 10, colour bar ticks in the dark-theme greys), so the
    colour-bar and annotation controls did nothing until the user touched
    the chart once.

    This helper is the single definition, used by both. ``previous`` is the
    display block already on the model, so an option that is not set keeps
    whatever the model had rather than snapping back to a hard-coded value.
    """
    layout, typography = dict(layout or {}), dict(typography or {})
    previous = dict(previous or {})
    prev_labels = dict(previous.get("labels") or {})

    def pick(key, fallback):
        value = layout.get(key)
        return fallback if value is None else value

    scale = colorscale if colorscale is not None else pick(
        "heatmap_colorscale", "RdBu")

    annotation_size = pick("heatmap_annotation_size", 10)
    # A stored 10 is the untouched default, so the typography value wins --
    # this is the same "10 means unset" rule the live-preview path uses.
    if annotation_size in (None, 10, "10"):
        annotation_size = (typography.get("value_label_size")
                           or annotation_size or 10)

    annotation_color = pick("heatmap_annotation_color", None)
    if not annotation_color:
        annotation_color = prev_labels.get("color") or "auto"

    options = {
        "heatmap_colors": _heatmap_palette(scale, diverging),
        "colorbar_title": str(pick("colorbar_title",
                                    previous.get("colorbar_title",
                                                 default_title))),
        "colorbar_title_size": int(pick("colorbar_title_size",
                                        previous.get("colorbar_title_size", 11))),
        "colorbar_title_color": pick("colorbar_title_color",
                                     previous.get("colorbar_title_color",
                                                  "#cbd5e1")),
        "colorbar_tick_size": int(pick("colorbar_tick_size",
                                       previous.get("colorbar_tick_size", 10))),
        "colorbar_tick_color": pick("colorbar_tick_color",
                                    previous.get("colorbar_tick_color",
                                                 "#94a3b8")),
        "show_colorbar": bool(pick("show_colorbar",
                                   previous.get("show_colorbar", True))),
        "colorbar_position": str(pick("colorbar_position",
                                      previous.get("colorbar_position",
                                                   "right"))).lower(),
        "colorbar_orientation": str(pick("colorbar_orientation",
                                         previous.get("colorbar_orientation",
                                                      "auto"))).lower(),
        "colorbar_border": bool(pick("show_border",
                                     previous.get("colorbar_border", False))),
        "labels": {
            "show": bool(pick("heatmap_show_text", prev_labels.get("show", True))),
            "font_size": int(annotation_size),
            "color": annotation_color,
            "precision": int(pick("heatmap_annotation_precision",
                                  prev_labels.get("precision", 2))),
        },
    }
    return options


def _display(layout: Optional[dict], typography: Optional[dict], palette) -> dict:
    layout = dict(layout or {})
    typography = dict(typography or {})
    legend_position = str(layout.get("legend_position", "Bottom Center"))
    position = legend_position.lower()
    orientation = str(layout.get("legend_orientation", "Auto")).lower()
    if orientation == "auto":
        orientation = "vertical" if position in {"left", "right"} else "horizontal"
    text_info = str(layout.get("pie_textinfo", "label+percent")).lower()
    label_position = str(layout.get("label_position", "outside")).lower()
    if label_position == "auto":
        label_position = "outside"
    formatter = ("label+percent" if text_info == "label+percent" else
                 "label+value" if text_info == "label+value" else
                 "percent" if text_info == "percent" else
                 "value" if text_info == "value" else "label")
    pie_value_size = typography.get("slice_value_size",
                                   layout.get("pie_value_size", 11))
    if pie_value_size in (None, 11, "11"):
        pie_value_size = typography.get("value_label_size", pie_value_size)
    return {
        "title_size": typography.get("header_size", 20),
        "title_align": typography.get("header_align", "left"),
        "title_weight": 700 if "bold" in str(typography.get("header_font_style", "")).lower()
        else 600,
        "text_color": typography.get("axis_tick_color", "auto"),
        "palette": palette,
        "chart_background": "transparent",
        "legend": {
            "show": True,
            "position": position,
            "layout": orientation,
            "title": layout.get("legend_title", ""),
            "font_size": typography.get("legend_item_size", 11),
            "title_font_size": typography.get("legend_title_size", 12),
            "click": layout.get("legend_itemclick", "toggle") != "none",
        },
        "labels": {
            "show": bool(layout.get("show_value_labels", True)),
            "position": label_position,
            "formatter": formatter,
            "font_size": pie_value_size,
            "color": typography.get("slice_value_color",
                                    layout.get("pie_value_color", "auto")),
        },
        "donut": float(layout.get("donut_hole", 0) or 0) > 0,
        "donut_ratio": max(0, min(80, round(float(layout.get("donut_hole", 0) or 0) * 100))),
        "donut_center_total": bool(layout.get("donut_center_total", False)),
        "center_total_precision": max(0, min(6, int(layout.get("center_total_precision", 2) or 0))),
        "center_total_size": int(typography.get("pie_center_total_size", pie_value_size or 12)),
        "center_total_color": typography.get("pie_center_total_color",
                                              layout.get("pie_value_color", "auto")),
        "pull_slices": max(0.0, min(0.5, float(layout.get("pull_slices", 0.0) or 0.0))),
        "rotation": int(layout.get("pie_rotation", 0) or 0),
        "direction": layout.get("pie_direction", "clockwise"),
    }


def build_pie_chart(dataset: PolarsDataset, config: dict, *,
                    roles: Optional[dict] = None,
                    layout: Optional[dict] = None,
                    typography: Optional[dict] = None,
                    uid: Optional[str] = None) -> Optional[dict]:
    """Aggregate one Pie & Donut chart in Polars and return ChartModel v1."""
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_pie_chart requires a PolarsDataset")
    config = dict(config or {})
    schema = dataset.schema
    role_dims = list((roles or {}).get("dimensions") or [])
    if not role_dims:
        role_dims = dataset.roles().get("dimensions", [])
    dimension = config.get("x")
    if isinstance(dimension, (list, tuple)):
        dimension = dimension[0] if dimension else None
    if dimension in (None, "", "None"):
        dimension = next((name for name in role_dims if name in schema), None)
    if dimension not in schema or dimension == dataset.row_id:
        return None

    metric = config.get("y")
    if isinstance(metric, (list, tuple)):
        metric = metric[0] if metric else None
    if metric in (None, "", "None"):
        metric = None
    if metric is not None and (metric not in schema or metric == dataset.row_id):
        return None

    raw_agg = str(config.get("agg", "Avg")).strip().lower()
    operation = _AGGREGATIONS.get(raw_agg, "mean") if metric else "count"
    sort = _SORTS.get(str(config.get("sort", "Value ↓")).strip().lower(),
                      "value_desc")
    try:
        top_n = int(config.get("top_n", 0) or 0) or None
    except (ValueError, TypeError):
        top_n = None
    if top_n is not None and top_n < 1:
        top_n = None

    palette = _palette(config.get("palette"))
    result = dataset.aggregate(
        dimension, metric, operation, sort=sort, top_n=top_n,
        include_other=True, uid=uid or str(uuid.uuid4()),
        recipe={"chart_type": "pie_chart", "config": config},
    )
    values = result["data"]["series"][0]["values"]
    if not values:
        return None
    result["chart_type"] = "pie_chart"
    result["data"]["series"][0]["type"] = "pie"

    if metric is None:
        base_title = f"Distribution of {dimension}"
    else:
        agg_label = {"mean": "Average", "sum": "Sum", "median": "Median",
                     "count": "Count", "min": "Min", "max": "Max"}[operation]
        base_title = f"{agg_label} {metric} Split by {dimension}"
    if top_n and len(values) > top_n:
        base_title += f" (Top {top_n})"
    title = str((layout or {}).get("title") or base_title)
    result["title"] = title
    result["recipe"] = {"version": 1, "chart_type": "pie_chart",
                        "config": config, "layout": dict(layout or {}),
                        "typography": dict(typography or {})}
    result["display"] = _display(layout, typography, palette)
    return validate_chart_model(result)


def apply_pie_display_options(model: dict, layout: dict,
                              typography: dict) -> dict:
    """Apply live Layout/Typography settings directly to a Pie ChartModel."""
    model = validate_chart_model(model)
    result = dict(model)
    result["display"] = _display(layout, typography,
                                 list((model.get("display") or {}).get("palette") or []))
    title = str((layout or {}).get("title") or "").strip()
    if title:
        result["title"] = title
    result["recipe"] = {**dict(model.get("recipe") or {}),
                        "layout": dict(layout or {}),
                        "typography": dict(typography or {})}
    return validate_chart_model(result)


def build_categorical_charts(dataset: PolarsDataset, config: dict, *,
                             roles: Optional[dict] = None,
                             layout: Optional[dict] = None,
                             typography: Optional[dict] = None) -> list[dict]:
    """Build categorical bar charts, including a secondary metric when selected.

    The legacy runner emits one chart for each selected dimension/metric pair.
    Preserve that behavior while expressing the secondary metric as a line
    series on a second ECharts value axis.
    """
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_categorical_charts requires a PolarsDataset")
    config = dict(config or {})
    layout = dict(layout or {})
    layout.setdefault("show_value_labels", False)
    schema = dataset.schema
    role_dims = list((roles or {}).get("dimensions") or [])
    if not role_dims:
        role_dims = (list((roles or {}).get("categorical") or []) +
                     list((roles or {}).get("datetime") or []))
    if not role_dims:
        role_dims = dataset.roles().get("dimensions", [])
    raw_dims = config.get("x_cols", config.get("x"))
    if isinstance(raw_dims, str):
        raw_dims = [raw_dims]
    dimensions = [name for name in (raw_dims or role_dims[:4])
                   if name in schema and name != dataset.row_id]
    raw_metrics = config.get("y_cols", config.get("y"))
    if isinstance(raw_metrics, str):
        raw_metrics = [raw_metrics]
    metrics = [name for name in (raw_metrics or [])
               if name in schema and name != dataset.row_id]
    dual_metric = config.get("dual_y", config.get("dual_y_col"))
    if isinstance(dual_metric, (list, tuple)):
        dual_metric = dual_metric[0] if dual_metric else None
    if (dual_metric in (None, "", "None") or dual_metric not in schema
            or dual_metric == dataset.row_id):
        dual_metric = None
    dual_agg = _AGGREGATIONS.get(
        str(config.get("dual_y_agg") or config.get("agg", "mean")).strip().lower(),
        "mean")
    if not dimensions:
        return []

    operation = _AGGREGATIONS.get(str(config.get("agg", "mean")).strip().lower(), "mean")
    sort = _SORTS.get(str(config.get("sort_by", "Value (Desc)")).lower(),
                      _SORTS.get(str(config.get("sort", "Value ↓")).lower(),
                                 "value_desc"))
    try:
        top_n = int(config.get("top_n", 0) or 0) or None
    except (TypeError, ValueError):
        top_n = None
    if top_n is not None and top_n < 1:
        top_n = None

    direction = str(config.get("direction", "Vertical (Column chart)"))
    horizontal = "horizontal" in direction.lower()
    palette = _palette(config.get("palette"))
    models = []
    for dimension in dimensions:
        for metric in (metrics or [None]):
            agg = operation if metric else "count"
            model = dataset.aggregate(
                dimension, metric, agg, sort=sort, top_n=top_n,
                include_other=False,
                uid=str(uuid.uuid4()),
                recipe={"chart_type": "categorical", "config": config},
            )
            values = model["data"]["series"][0]["values"]
            if not values:
                continue
            series = list(model["data"]["series"])
            if dual_metric and metric and dual_metric != metric:
                secondary = dataset.aggregate(
                    dimension, dual_metric, dual_agg, sort="category_asc",
                    include_other=False,
                    recipe={"chart_type": "categorical", "config": config},
                )["data"]["series"][0]["values"]
                secondary_values = {str(category): value for category, value in secondary}
                secondary_series = {
                    "id": f"{dual_metric}-secondary",
                    "name": f"{dual_agg.title()} {dual_metric}",
                    "type": "line", "axis": 1,
                    "values": [[category, secondary_values.get(str(category))]
                               for category, _value in values],
                    "style": {"color": palette[1 % len(palette)] if palette else "#ef4444",
                              "dash": "dashed"},
                }
                series.append(secondary_series)
            model["chart_type"] = "categorical"
            model["data"]["series"] = series
            model["data"]["series"][0]["type"] = "bar"
            agg_label = {"mean": "Mean", "sum": "Sum", "median": "Median",
                         "count": "Count", "min": "Min", "max": "Max"}[agg]
            if metric is None:
                title = f"{'Bar' if horizontal else 'Column'} Counts: {dimension}"
                x_title, y_title = ("Count", dimension) if horizontal else (dimension, "Count")
            else:
                title = (f"{'Bar' if horizontal else 'Column'}: "
                         f"{agg_label} {metric} by {dimension}")
                x_title, y_title = ((agg_label + " " + metric, dimension)
                                    if horizontal else (dimension, agg_label + " " + metric))
            if top_n:
                title += f" (Top {top_n})"
            display = _categorical_display(layout, typography, palette)
            display.update({"orientation": "horizontal" if horizontal else "vertical",
                            "x_title": (layout or {}).get("x_label") or x_title,
                            "y_title": (layout or {}).get("y_label") or y_title})
            if len(series) > 1:
                title += f" & {dual_agg.title()} {dual_metric}"
                model["data"]["dimensions"] = list(dict.fromkeys(
                    list(model["data"].get("dimensions") or []) + [dual_metric]))
                display.update({
                    "dual_axis": True,
                    "dual_axis_orientation": "x" if horizontal else "y",
                    "x2_title": (layout or {}).get("x2_label") or
                    (f"{dual_agg.title()} {dual_metric}" if horizontal else ""),
                    "y2_title": (layout or {}).get("y2_label") or
                    (f"{dual_agg.title()} {dual_metric}" if not horizontal else ""),
                })
                display["legend"]["show"] = True
            model["title"] = str((layout or {}).get("title") or title)
            model["display"] = display
            model["recipe"] = {"version": 1, "chart_type": "categorical",
                               "config": config, "layout": dict(layout or {}),
                               "typography": dict(typography or {})}
            models.append(validate_chart_model(model))
    return models


def build_statistical_charts(dataset: PolarsDataset, config: dict, *,
                             roles: Optional[dict] = None,
                             layout: Optional[dict] = None,
                             typography: Optional[dict] = None) -> list[dict]:
    """Build the Statistical Analysis grouped summary bars in Polars."""
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_statistical_charts requires a PolarsDataset")
    config, schema = dict(config or {}), dataset.schema
    role_numeric = list((roles or {}).get("numeric") or [])
    if not role_numeric:
        role_numeric = dataset.roles().get("numeric", [])
    raw_metrics = config.get("y_cols", config.get("y"))
    if isinstance(raw_metrics, str):
        raw_metrics = [raw_metrics]
    metrics = [name for name in (raw_metrics or role_numeric)
               if name in schema and name != dataset.row_id
               and schema[name].is_numeric()]
    if not metrics:
        return []
    group = config.get("x_cols", config.get("x"))
    if isinstance(group, (list, tuple)):
        group = group[0] if group else None
    if group in (None, "", "None") or group not in schema or group == dataset.row_id:
        group = None

    statistic_names = ["Mean", "Min", "Max", "Std Dev"]
    palette = _palette(config.get("palette"))
    if group:
        selected_metrics = [
            _numeric_values_expr(metric, schema).alias(f"__m{metric_index}")
            for metric_index, metric in enumerate(metrics)
        ]
        stat_exprs = []
        for metric_index in range(len(metrics)):
            metric_expr = pl.col(f"__m{metric_index}")
            stat_exprs.extend([
                metric_expr.mean().alias(f"m{metric_index}_Mean"),
                metric_expr.min().alias(f"m{metric_index}_Min"),
                metric_expr.max().alias(f"m{metric_index}_Max"),
                metric_expr.std(ddof=1).alias(f"m{metric_index}_Std Dev"),
            ])
        grouped = (dataset.frame.select([pl.col(group), *selected_metrics])
                   .filter(pl.col(group).is_not_null())
                   .group_by(group).agg(stat_exprs).sort(group).collect())
        if grouped.height == 0:
            return []
        categories = [_json_scalar(value) for value in grouped.get_column(group).to_list()]
        models = []
        for metric_index, metric in enumerate(metrics):
            series = []
            for stat in statistic_names:
                values = [[category, _json_scalar(value)]
                          for category, value in zip(
                              categories, grouped.get_column(f"m{metric_index}_{stat}").to_list())]
                series.append({"id": stat.lower().replace(" ", "-"),
                               "name": stat, "type": "bar", "values": values})
            title = f"Statistics of {metric} by {group}"
            x_title, y_title = group, "Value"
            display = _categorical_display(layout, typography, palette)
            display.update({
                "legend": {**display["legend"], "show": True},
                "orientation": "vertical",
                "x_title": (layout or {}).get("x_label") or x_title,
                "y_title": (layout or {}).get("y_label") or y_title,
                "bar_mode": str((layout or {}).get("bar_mode", "group")).lower(),
            })
            models.append(make_chart_model(
                uid=str(uuid.uuid4()), chart_type="categorical",
                title=str((layout or {}).get("title") or title),
                data={"dimensions": [group, metric], "series": series},
                recipe={"version": 1, "chart_type": "statistical", "config": config,
                        "layout": dict(layout or {}), "typography": dict(typography or {})},
                display=display,
                source={"engine": "polars", "revision": dataset.revision,
                        "row_id": dataset.row_id},
            ))
        return models

    # The no-group summary puts metric names on the category axis and keeps
    # the four statistic families as distinct bar series. Collect once so a
    # CSV-backed LazyFrame is not rescanned for every selected metric.
    stat_exprs = []
    for metric_index, metric in enumerate(metrics):
        metric_expr = _numeric_values_expr(metric, schema)
        stat_exprs.extend([
            metric_expr.mean().alias(f"m{metric_index}_Mean"),
            metric_expr.min().alias(f"m{metric_index}_Min"),
            metric_expr.max().alias(f"m{metric_index}_Max"),
            metric_expr.std(ddof=1).alias(f"m{metric_index}_Std Dev"),
        ])
    summary = dataset.frame.select(stat_exprs).collect().row(0, named=True)
    series = []
    for stat in statistic_names:
        series.append({
            "id": stat.lower().replace(" ", "-"), "name": stat,
            "type": "bar",
            "values": [[metric, _json_scalar(summary.get(f"m{metric_index}_{stat}"))]
                       for metric_index, metric in enumerate(metrics)],
        })
    title, x_title, y_title = "Statistical Summary", "Column", "Value"
    display = _categorical_display(layout, typography, palette)
    display.update({
        "legend": {**display["legend"], "show": True},
        "orientation": "vertical",
        "x_title": (layout or {}).get("x_label") or x_title,
        "y_title": (layout or {}).get("y_label") or y_title,
        "bar_mode": str((layout or {}).get("bar_mode", "group")).lower(),
    })
    return [make_chart_model(
        uid=str(uuid.uuid4()), chart_type="categorical",
        title=str((layout or {}).get("title") or title),
        data={"dimensions": list(metrics), "series": series},
        recipe={"version": 1, "chart_type": "statistical", "config": config,
                "layout": dict(layout or {}), "typography": dict(typography or {})},
        display=display,
        source={"engine": "polars", "revision": dataset.revision,
                "row_id": dataset.row_id},
    )]


def build_correlation_chart(dataset: PolarsDataset, config: dict, *,
                            roles: Optional[dict] = None,
                            layout: Optional[dict] = None,
                            typography: Optional[dict] = None) -> Optional[dict]:
    """Build a Pearson correlation matrix directly from Polars expressions."""
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_correlation_chart requires a PolarsDataset")
    config, schema = dict(config or {}), dataset.schema
    role_numeric = list((roles or {}).get("numeric") or [])
    if not role_numeric:
        role_numeric = dataset.roles().get("numeric", [])
    raw_columns = config.get("x_cols", config.get("x"))
    if isinstance(raw_columns, str):
        raw_columns = [raw_columns]
    columns = list(dict.fromkeys(
        name for name in (raw_columns or role_numeric)
        if name in schema and name != dataset.row_id and schema[name].is_numeric()))
    if len(columns) < 2:
        return None
    expressions = [pl.col(name).cast(pl.Float64, strict=False)
                   .fill_nan(None).alias(f"__c{i}")
                   for i, name in enumerate(columns)]
    typed = dataset.frame.select(expressions)
    correlation_expressions = [
        pl.corr(pl.col(f"__c{i}"), pl.col(f"__c{j}")).alias(f"r{i}_{j}")
        for i in range(len(columns)) for j in range(len(columns))
    ]
    correlation_row = typed.select(correlation_expressions).collect().row(0)
    values = []
    value_index = 0
    for row_index, row_name in enumerate(columns):
        for column_index, column_name in enumerate(columns):
            value = correlation_row[value_index]
            value_index += 1
            values.append([column_name, row_name, _json_scalar(value)])

    palette = _palette(config.get("palette"))
    display = _categorical_display(layout, typography, palette)
    # Same helper the live-edit path uses, so the colour bar and the cell
    # annotations are editable from the moment the chart is generated -- not
    # only after the chart is touched once.
    display.update(_heatmap_display_options(
        layout, typography, previous=display, diverging=True,
        colorscale=str((layout or {}).get("heatmap_colorscale", "RdBu"))))
    display.update({
        "heatmap_rows": columns,
        "heatmap_columns": columns,
        "heatmap_min": -1,
        "heatmap_max": 1,
        "legend": {**display["legend"], "show": False},
        "x_title": "", "y_title": "",
    })
    return make_chart_model(
        uid=str(uuid.uuid4()), chart_type="matrix_heatmap",
        title=str((layout or {}).get("title") or "Correlation Heatmap"),
        data={"dimensions": columns,
              "series": [{"id": "correlation", "name": "Correlation",
                          "type": "heatmap", "values": values}]},
        recipe={"version": 1, "chart_type": "correlation", "config": config,
                "layout": dict(layout or {}), "typography": dict(typography or {})},
        display=display,
        source={"engine": "polars", "revision": dataset.revision,
                "row_id": dataset.row_id},
    )


def build_matrix_heatmap_chart(dataset: PolarsDataset, config: dict, *,
                               roles: Optional[dict] = None,
                               layout: Optional[dict] = None,
                               typography: Optional[dict] = None,
                               max_categories: int = 40) -> Optional[dict]:
    """Build the cross-tab Matrix Heatmap from Polars aggregates.

    The output is a sparse semantic heatmap (category, category, value)
    consumed by the same ECharts adapter as Correlation. Only the grouped
    result is collected; source rows remain in the Polars query plan.
    """
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_matrix_heatmap_chart requires a PolarsDataset")
    config, schema = dict(config or {}), dataset.schema
    role_dims = list((roles or {}).get("dimensions") or [])
    if not role_dims:
        role_dims = dataset.roles().get("dimensions", [])
    role_numeric = list((roles or {}).get("numeric") or [])
    if not role_numeric:
        role_numeric = dataset.roles().get("numeric", [])

    index_col = config.get("index_col") or next(
        (column for column in role_dims if column in schema),
        next((column for column in schema if column != dataset.row_id), None))
    columns_col = config.get("columns_col") or next(
        (column for column in role_dims if column in schema and column != index_col),
        next((column for column in schema
              if column not in {dataset.row_id, index_col}), None))
    values_col = config.get("values_col") or next(
        (column for column in role_numeric if column in schema), None)
    if (not index_col or not columns_col or not values_col or
            any(column not in schema for column in
                (index_col, columns_col, values_col))):
        return None

    raw_agg = str(config.get("agg", "Avg")).strip().lower()
    operation = _AGGREGATIONS.get(raw_agg, "mean")
    agg_label = {"mean": "AVG", "sum": "SUM", "min": "MIN",
                 "max": "MAX", "count": "COUNT"}[operation]
    sort_name = str(config.get("sort_rows", "Value ↓")).strip().lower()
    sort_rows = _SORTS.get(sort_name, "value_desc")
    try:
        top_n = int(config.get("top_n_rows", 0) or 0) or None
    except (TypeError, ValueError):
        top_n = None

    matrix_frame = dataset.frame.select(
        pl.col(index_col).alias("__row"),
        pl.col(columns_col).alias("__column"),
        pl.col(values_col).alias("__metric"),
    )
    valid_keys = pl.col("__row").is_not_null() & pl.col("__column").is_not_null()
    for key in (index_col, columns_col):
        if schema[key].is_float():
            alias = "__row" if key == index_col else "__column"
            valid_keys = valid_keys & ~pl.col(alias).is_nan()
    if operation == "count":
        aggregate = pl.col("__metric").count().alias("__value")
    else:
        aggregate = _aggregate_expression(operation, "__metric").alias("__value")
    try:
        grouped = matrix_frame.filter(valid_keys).group_by(
            ["__row", "__column"]
        ).agg(aggregate).collect()
    except Exception:
        return None
    if grouped.height == 0:
        return None

    # Pandas pivot_table sorts its category keys. Keep that stable ordering
    # when ties occur during the same absolute-sum category cap as the legacy
    # view, then apply the selected row/column ordering.
    def key_text(value):
        return str(_json_scalar(value))

    grouped_rows = grouped.to_dicts()
    row_categories = {key_text(row["__row"]): row["__row"] for row in grouped_rows}
    column_categories = {key_text(row["__column"]): row["__column"]
                         for row in grouped_rows}

    def category_order(labels, source_values):
        try:
            return sorted(labels, key=lambda label: source_values[label])
        except TypeError:
            return sorted(labels)

    all_rows = category_order(row_categories, row_categories)
    all_columns = category_order(column_categories, column_categories)
    cells = {}
    for row in grouped_rows:
        row_name, col_name = key_text(row["__row"]), key_text(row["__column"])
        value = _json_scalar(row["__value"])
        cells[(row_name, col_name)] = value

    try:
        row_cap = col_cap = max(1, int(max_categories))
    except (TypeError, ValueError):
        row_cap = col_cap = 40
    row_strength = {
        row: sum(abs(value) for col in all_columns
                 if (value := cells.get((row, col))) is not None)
        for row in all_rows
    }
    col_strength = {
        col: sum(abs(value) for row in all_rows
                 if (value := cells.get((row, col))) is not None)
        for col in all_columns
    }
    if len(all_rows) > row_cap:
        row_order = {row: index for index, row in enumerate(all_rows)}
        all_rows = sorted(all_rows,
                          key=lambda row: (-row_strength[row], row_order[row]))[:row_cap]
    if len(all_columns) > col_cap:
        column_order = {column: index for index, column in enumerate(all_columns)}
        all_columns = sorted(all_columns,
                             key=lambda col: (-col_strength[col], column_order[col]))[:col_cap]

    def mean_or_none(values):
        valid = [value for value in values if value is not None]
        return sum(valid) / len(valid) if valid else None

    if sort_rows in {"value_desc", "value_asc"}:
        row_tie_order = {row: index for index, row in enumerate(
            category_order(all_rows, row_categories))}
        all_rows.sort(key=lambda row: (
            mean_or_none([cells.get((row, col)) for col in all_columns]) is None,
            (mean_or_none([cells.get((row, col)) for col in all_columns]) or 0)
            * (1 if sort_rows == "value_asc" else -1), row_tie_order[row]))
        column_tie_order = {column: index for index, column in enumerate(
            category_order(all_columns, column_categories))}
        all_columns.sort(key=lambda col: (
            mean_or_none([cells.get((row, col)) for row in all_rows]) is None,
            -(mean_or_none([cells.get((row, col)) for row in all_rows]) or 0),
            column_tie_order[col]))
    elif sort_rows == "category_desc":
        all_rows = category_order(all_rows, row_categories)[::-1]
    if top_n and top_n > 0:
        all_rows = all_rows[:top_n]
    if not all_rows or not all_columns:
        return None

    total_rows = len({key_text(row["__row"]) for row in grouped_rows})
    total_columns = len({key_text(row["__column"]) for row in grouped_rows})
    truncated = total_rows > row_cap or total_columns > col_cap
    title = (f"Matrix ({agg_label}): {values_col}  ·  "
             f"{index_col} × {columns_col}")
    if truncated:
        title += (f"  (top {len(all_rows)}×{len(all_columns)} of "
                  f"{total_rows}×{total_columns})")
    title = str((layout or {}).get("title") or title)

    value_list = [value for row in all_rows for col in all_columns
                  if (value := cells.get((row, col))) is not None]
    diverging = operation in {"mean", "median", "std"}
    colorscale = str((layout or {}).get("heatmap_colorscale", "RdBu"))
    heat_colors = _heatmap_palette(colorscale, diverging)
    heat_min = heat_max = None
    if value_list:
        if diverging:
            center = sum(value_list) / len(value_list)
            extent = max(abs(min(value_list) - center), abs(max(value_list) - center))
            if extent == 0:
                extent = 1.0
            heat_min, heat_max = center - extent, center + extent
        else:
            heat_min, heat_max = min(value_list), max(value_list)
            if heat_min == heat_max:
                heat_min, heat_max = heat_min - 0.5, heat_max + 0.5
    palette = _palette(config.get("palette"))
    display = _categorical_display(layout, typography, palette)
    # Colour-bar and annotation options come from the SAME helper the live
    # edit path uses, so a freshly generated chart already honours the
    # colour-bar title, tick sizes/colours, annotation size/colour and
    # precision the user picked -- instead of only honouring them after the
    # chart was touched once.
    display.update(_heatmap_display_options(
        layout, typography, previous=display, diverging=diverging,
        colorscale=colorscale,
        default_title=f"{agg_label}({values_col})"))
    display.update({
        "heatmap_rows": all_rows,
        "heatmap_columns": all_columns,
        "heatmap_min": heat_min,
        "heatmap_max": heat_max,
        "heatmap_colors": heat_colors,
        "x_title": (layout or {}).get("x_label") or columns_col,
        "y_title": (layout or {}).get("y_label") or index_col,
        "legend": {**display["legend"], "show": False},
    })
    points = [[column, row, cells.get((row, column))]
              for row in all_rows for column in all_columns]
    return make_chart_model(
        uid=str(uuid.uuid4()), chart_type="matrix_heatmap", title=title,
        data={"dimensions": [index_col, columns_col, values_col],
              "series": [{"id": "matrix", "name": f"{agg_label}({values_col})",
                          "type": "heatmap", "values": points}]},
        recipe={"version": 1, "chart_type": "matrix_heatmap", "config": config,
                "layout": dict(layout or {}), "typography": dict(typography or {})},
        display=display,
        source={"engine": "polars", "revision": dataset.revision,
                "row_id": dataset.row_id},
    )


def build_matrix_table_chart(dataset: PolarsDataset, config: dict, *,
                             roles: Optional[dict] = None,
                             layout: Optional[dict] = None,
                             typography: Optional[dict] = None) -> Optional[dict]:
    """Build a scrollable Pivot Table model from the Polars cross-tab."""
    config, layout = dict(config or {}), dict(layout or {})
    matrix = build_matrix_heatmap_chart(
        dataset, config, roles=roles, layout=layout, typography=typography,
        max_categories=300)
    if matrix is None:
        return None
    index_col, columns_col, values_col = matrix["data"]["dimensions"]
    rows = list(matrix["display"].get("heatmap_rows") or [])
    columns = list(matrix["display"].get("heatmap_columns") or [])
    values = {(str(point[1]), str(point[0])): point[2]
              for point in matrix["data"]["series"][0]["values"]}

    operation = _AGGREGATIONS.get(str(config.get("agg", "avg")).strip().lower(), "mean")
    total_header = {
        "sum": "Overall Total", "mean": "Overall Average",
        "median": "Overall Median", "min": "Overall Minimum",
        "max": "Overall Maximum", "count": "Overall Count",
    }.get(operation, f"{operation.upper()}({values_col})")

    # Keep the rightmost summary column equivalent to the old table: aggregate
    # each row category over its original records, independent of the capped
    # or sorted cross-tab columns.
    totals = {}
    try:
        total_frame = dataset.frame.select(
            pl.col(index_col).alias("__row"),
            pl.col(values_col).alias("__metric"),
        )
        valid_rows = pl.col("__row").is_not_null()
        if dataset.schema[index_col].is_float():
            valid_rows = valid_rows & ~pl.col("__row").is_nan()
        if operation == "count":
            total_expr = pl.col("__metric").count().alias("__total")
        else:
            total_expr = _aggregate_expression(operation, "__metric").alias("__total")
        total_frame = total_frame.filter(valid_rows).group_by("__row").agg(total_expr).collect()
        totals = {str(_json_scalar(row["__row"])): _json_scalar(row["__total"])
                  for row in total_frame.to_dicts()}
    except Exception:
        totals = {}

    headers = [str(index_col), *columns, total_header]
    table_rows = [
        [row, *[values.get((str(row), str(column))) for column in columns],
         totals.get(str(row))]
        for row in rows
    ]
    hierarchy_meta = []
    hierarchy = [str(name) for name in (config.get("row_hierarchy") or [])
                 if str(name) in dataset.schema and str(name) not in
                 {columns_col, values_col}]
    hierarchy = list(dict.fromkeys(hierarchy))[:3]
    if len(hierarchy) >= 2:
        try:
            source = dataset.frame.select(
                *[pl.col(name).alias(f"__h{i}") for i, name in enumerate(hierarchy)],
                pl.col(columns_col).alias("__column"),
                pl.col(values_col).alias("__metric"),
            )
            valid = pl.col("__column").is_not_null()
            for i, name in enumerate(hierarchy):
                valid = valid & pl.col(f"__h{i}").is_not_null()
                if dataset.schema[name].is_float():
                    valid = valid & ~pl.col(f"__h{i}").is_nan()
            if dataset.schema[columns_col].is_float():
                valid = valid & ~pl.col("__column").is_nan()
            prefix_keys = []
            hierarchy_rows = []
            seen_paths = set()
            for depth in range(1, len(hierarchy) + 1):
                keys = [f"__h{i}" for i in range(depth)]
                agg_expr = (pl.col("__metric").count().alias("__value")
                            if operation == "count" else
                            _aggregate_expression(operation, "__metric").alias("__value"))
                grouped = source.filter(valid).group_by([*keys, "__column"]).agg(agg_expr).collect()
                grouped_map = {}
                for record in grouped.to_dicts():
                    path = tuple(_json_scalar(record[k]) for k in keys)
                    col_key = str(_json_scalar(record["__column"]))
                    grouped_map[(path, col_key)] = _json_scalar(record["__value"])
                    seen_paths.add(path)
                paths = sorted({path for path, _column in grouped_map},
                               key=lambda path: tuple(str(part) for part in path))
                for path in paths:
                    vals = [grouped_map.get((path, str(column))) for column in columns]
                    # Overall total is aggregated from the source rows for this
                    # exact prefix, so parent averages are never averages of averages.
                    prefix_filter = pl.lit(True)
                    for i, value in enumerate(path):
                        prefix_filter = prefix_filter & (pl.col(f"__h{i}") == value)
                    total_expr = (pl.col("__metric").count().alias("__total")
                                  if operation == "count" else
                                  _aggregate_expression(operation, "__metric").alias("__total"))
                    total = source.filter(valid & prefix_filter).select(total_expr).collect().item()
                    hierarchy_rows.append([str(path[-1]), *vals, _json_scalar(total)])
                    hierarchy_meta.append({"depth": depth - 1,
                                           "path": [str(part) for part in path],
                                           "has_children": depth < len(hierarchy) and
                                               any(candidate[:depth] == path for candidate in seen_paths)})
            if hierarchy_rows:
                ordered = sorted(zip(hierarchy_rows, hierarchy_meta),
                                 key=lambda item: tuple(item[1]["path"]))
                table_rows = [item[0] for item in ordered]
                hierarchy_meta = [item[1] for item in ordered]
        except Exception:
            # Fall back to the existing single-index table if a legacy recipe
            # contains an invalid hierarchy column or unsupported grouping.
            hierarchy_meta = []

    if hierarchy_meta:
        headers[0] = " / ".join(hierarchy)
    try:
        from desktop.theme import current_mode
        table_theme = current_mode()
    except Exception:
        table_theme = "light"
    table_display = {
        "table_font_size": int(layout.get("table_font_size", 11)),
        "table_header_font_size": int(layout.get("table_header_font_size", 12)),
        "table_row_height": int(layout.get("table_row_height", 26)),
        "table_header_height": int(layout.get("table_header_height", 28)),
        "table_index_align": str(layout.get("table_index_align", "left")),
        "table_data_align": str(layout.get("table_data_align", "right")),
        "table_number_format": str(layout.get("table_number_format", ",.2f")),
        "table_font_color": str(layout.get("table_font_color", "auto")),
        "table_header_color": str(layout.get("table_header_color", "#6163df")),
        "table_stripe_even_color": str(layout.get("table_stripe_even_color", "auto")),
        "table_stripe_odd_color": str(layout.get("table_stripe_odd_color", "auto")),
        "theme_mode": str((typography or {}).get("theme_mode") or table_theme),
        "table_hierarchy": hierarchy_meta,
        "table_font_family": str((typography or {}).get("family", "Inter")),
        "table_header_font_family": str((typography or {}).get(
            "header_family", (typography or {}).get("family", "Inter"))),
        "title_size": int((typography or {}).get("header_size", 20)),
        "title_color": str((typography or {}).get("header_color", "auto")),
        "title_font_style": str((typography or {}).get("header_font_style", "Bold")),
        "title_align": str((typography or {}).get("header_align", "left")),
        "text_color": str((typography or {}).get("axis_tick_color", "auto")),
        "chart_background": "transparent",
    }
    return make_chart_model(
        uid=str(uuid.uuid4()), chart_type="data_table",
        title=str(layout.get("title") or matrix["title"]),
        data={"dimensions": headers,
              "series": [{"id": "pivot", "name": "Pivot Table",
                          "type": "table", "values": table_rows}]},
        recipe={"version": 1, "chart_type": "matrix_table", "config": config,
                "layout": layout, "typography": dict(typography or {})},
        display=table_display,
        source={"engine": "polars", "revision": dataset.revision,
                "row_id": dataset.row_id},
    )


def build_descriptive_table_chart(dataset: PolarsDataset, *,
                                  roles: Optional[dict] = None,
                                  layout: Optional[dict] = None,
                                  typography: Optional[dict] = None) -> Optional[dict]:
    """Build the Descriptive Statistics table from a Polars snapshot.

    This keeps the established ``describe().T`` contract (count, mean, sample
    standard deviation, extrema and quartiles) while emitting the same native
    table ChartModel used by Pivot Table. Float NaNs are treated as missing,
    matching pandas ``describe`` semantics.
    """
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_descriptive_table_chart requires a PolarsDataset")
    schema = dataset.schema
    role_numeric = list((roles or {}).get("numeric") or [])
    numeric = ([name for name in role_numeric if name in schema
                and name != dataset.row_id]
               if role_numeric else
               [name for name, dtype in schema.items()
                if name != dataset.row_id and dtype.is_numeric()])
    if not numeric:
        return None

    stats = ("Count", "Mean", "Std", "Min", "25%", "50%", "75%", "Max")
    expressions = []
    for index, name in enumerate(numeric):
        value = pl.col(name)
        if schema[name].is_float():
            value = pl.when(value.is_nan()).then(None).otherwise(value)
        expressions.extend((
            value.count().alias(f"{index}_count"),
            value.mean().alias(f"{index}_mean"),
            value.std(ddof=1).alias(f"{index}_std"),
            value.min().alias(f"{index}_min"),
            value.quantile(0.25, interpolation="linear").alias(f"{index}_q25"),
            value.quantile(0.50, interpolation="linear").alias(f"{index}_q50"),
            value.quantile(0.75, interpolation="linear").alias(f"{index}_q75"),
            value.max().alias(f"{index}_max"),
        ))
    result = dataset.frame.select(expressions).collect().row(0, named=True)
    stat_keys = ("count", "mean", "std", "min", "q25", "q50", "q75", "max")
    rows = []
    for index, name in enumerate(numeric):
        row = [name]
        for key in stat_keys:
            value = _json_scalar(result.get(f"{index}_{key}"))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = round(value, 4)
            row.append(value)
        rows.append(row)

    layout, typography = dict(layout or {}), dict(typography or {})
    return make_chart_model(
        uid=str(uuid.uuid4()), chart_type="data_table",
        title=str(layout.get("title") or "Descriptive Statistics"),
        data={"dimensions": ["Column", *stats],
              "series": [{"id": "descriptive", "name": "Descriptive Statistics",
                          "type": "table", "values": rows}]},
        recipe={"version": 1, "chart_type": "descriptive", "config": {},
                "layout": layout, "typography": typography},
        display={
            "table_font_size": int(layout.get("table_font_size", 11)),
            "table_header_font_size": int(layout.get("table_header_font_size", 12)),
            "table_row_height": int(layout.get("table_row_height", 26)),
            "table_header_height": int(layout.get("table_header_height", 28)),
            "table_index_align": "left", "table_data_align": "right",
            "table_number_format": str(layout.get("table_number_format", ",.4f")),
            "table_font_color": str(layout.get("table_font_color", "auto")),
            "table_header_color": str(layout.get("table_header_color", "#6163df")),
            "table_stripe_even_color": str(layout.get("table_stripe_even_color", "auto")),
            "table_stripe_odd_color": str(layout.get("table_stripe_odd_color", "auto")),
            "title_size": int(typography.get("header_size", 20)),
            "text_color": str(typography.get("axis_tick_color", "auto")),
            "chart_background": "transparent",
        },
        source={"engine": "polars", "revision": dataset.revision,
                "row_id": dataset.row_id},
    )


def _outlier_summary(dataset: PolarsDataset, column: str,
                     multiplier: float) -> Optional[dict]:
    """Calculate IQR fences and source row IDs for one numeric column."""
    schema = dataset.schema
    dtype = schema.get(column)
    if dtype is None or column == dataset.row_id or not dtype.is_numeric():
        return None
    value = pl.col(column)
    if dtype.is_float():
        value = pl.when(value.is_finite()).then(value).otherwise(None)
    stats = dataset.frame.select(
        value.count().alias("count"),
        value.quantile(0.25, interpolation="linear").alias("q1"),
        value.quantile(0.75, interpolation="linear").alias("q3"),
        pl.col(dataset.row_id).min().alias("row_id_min"),
        pl.col(dataset.row_id).max().alias("row_id_max"),
    ).collect().row(0, named=True)
    if not stats.get("count"):
        return None
    q1, q3 = stats.get("q1"), stats.get("q3")
    if q1 is None or q3 is None:
        return None
    q1, q3 = float(q1), float(q3)
    iqr = q3 - q1
    lower = q1 - float(multiplier) * iqr
    upper = q3 + float(multiplier) * iqr
    outside = value.is_not_null() & ((value < lower) | (value > upper))
    out_rows = (dataset.frame.filter(outside)
                .select(dataset.row_id, column).sort(dataset.row_id).collect())
    row_count = int(dataset.frame.select(pl.len()).collect().item())
    row_ids = [int(row[dataset.row_id]) for row in out_rows.to_dicts()]
    return {
        "q1": q1, "q3": q3, "iqr": iqr, "lo": lower, "hi": upper,
        "out_count": len(row_ids),
        "pct": round(len(row_ids) / row_count * 100, 2) if row_count else 0.0,
        "row_ids": row_ids,
        "row_count": row_count,
        "row_id_min": int(stats["row_id_min"] or 0),
        "row_id_max": int(stats["row_id_max"] or 0),
    }


def detect_outliers_polars(dataset: PolarsDataset, columns: list,
                           multiplier: float = 1.5) -> dict:
    """Return IQR results and stable snapshot row IDs using Polars."""
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("detect_outliers_polars requires a PolarsDataset")
    multiplier = float(multiplier)
    if not math.isfinite(multiplier) or multiplier < 0:
        raise ValueError("IQR multiplier must be a finite non-negative number")
    results = {}
    for column in dict.fromkeys(str(name) for name in (columns or [])):
        summary = _outlier_summary(dataset, column, multiplier)
        if summary is not None:
            results[column] = summary
    return results


def build_outlier_charts(dataset: PolarsDataset, columns: list, *,
                         multiplier: float = 1.5,
                         typography: Optional[dict] = None,
                         normal_sample_limit: int = 8_000,
                         outlier_sample_limit: int = 20_000) -> list[dict]:
    """Build ECharts scatter models with Polars-computed IQR fences.

    The summary and row-ID list remain exact. Only rendered points are bounded
    for large normal/outlier sets, and the title records when point sampling
    was needed.
    """
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_outlier_charts requires a PolarsDataset")
    typography = dict(typography or {})
    schema = dataset.schema
    models = []
    for column, info in detect_outliers_polars(
            dataset, columns, multiplier).items():
        value = pl.col(column)
        if schema[column].is_float():
            value = pl.when(value.is_finite()).then(value).otherwise(None)
        outside = value.is_not_null() & (
            (value < info["lo"]) | (value > info["hi"]))
        selected = [dataset.row_id, column]
        normal = (dataset.frame.filter(value.is_not_null() & ~outside)
                  .select(selected).sort(dataset.row_id).collect())
        outliers = (dataset.frame.filter(outside).select(selected)
                    .sort(dataset.row_id).collect())
        normal_sampled = normal.height > normal_sample_limit
        outlier_sampled = outliers.height > outlier_sample_limit
        if normal_sampled:
            normal = normal.sample(n=normal_sample_limit, seed=42).sort(dataset.row_id)
        if outlier_sampled:
            outliers = outliers.sample(n=outlier_sample_limit, seed=42).sort(dataset.row_id)
        normal_values = [[int(row[dataset.row_id]), _json_scalar(row[column])]
                         for row in normal.to_dicts()]
        outlier_values = [[int(row[dataset.row_id]), _json_scalar(row[column])]
                          for row in outliers.to_dicts()]
        x_min = info["row_id_min"]
        x_max = max(x_min + 1, info["row_id_max"])
        series = [
            {"id": "normal", "name": "Normal", "type": "scatter",
             "values": normal_values, "style": {"color": "#6163df"}},
            {"id": "outlier", "name": "Outlier", "type": "scatter",
             "values": outlier_values, "style": {"color": "#ef4444"}},
            {"id": "lower-fence", "name": f"Lower fence ({info['lo']:.4g})",
             "type": "line", "values": [[x_min, info["lo"]], [x_max, info["lo"]]],
             "style": {"color": "#f59e0b", "dash": "dashed"}},
            {"id": "upper-fence", "name": f"Upper fence ({info['hi']:.4g})",
             "type": "line", "values": [[x_min, info["hi"]], [x_max, info["hi"]]],
             "style": {"color": "#f59e0b", "dash": "dashed"}},
        ]
        sample_note = ""
        if normal_sampled or outlier_sampled:
            sample_note = (f" · displayed {len(normal_values):,} normal and "
                           f"{len(outlier_values):,} outlier points")
        title = (f"Outliers — {column} ({info['out_count']:,} detected"
                 f"{sample_note})")
        models.append(make_chart_model(
            uid=str(uuid.uuid4()), chart_type="scatter_plot", title=title,
            data={"dimensions": [dataset.row_id, column], "series": series},
            recipe={"version": 1, "chart_type": "outlier",
                    "config": {"column": column, "multiplier": multiplier}},
            display={
                "title_size": int(typography.get("header_size", 18)),
                "text_color": str(typography.get("axis_tick_color", "auto")),
                "x_title": "Source row ID", "y_title": column,
                "symbol_size": 6, "symbol_opacity": 0.6,
                "legend": {"show": True, "position": "top right",
                           "layout": "vertical", "font_size": 10,
                           "click": True},
                "labels": {"show": False}, "chart_background": "transparent",
                "outlier_summary": {k: info[k] for k in
                                     ("q1", "q3", "iqr", "lo", "hi",
                                      "out_count", "pct")},
                "point_sampling": {"normal": normal_sampled,
                                    "outlier": outlier_sampled},
            },
            source={"engine": "polars", "revision": dataset.revision,
                    "row_id": dataset.row_id},
        ))
    return models


def build_data_quality_charts(dataset: PolarsDataset, *,
                              sample_rows: int = 100,
                              max_columns: int = 80) -> list[dict]:
    """Summarize missing values and duplicate rows as native ECharts models.

    Missing counts treat both Polars nulls and floating NaNs as missing, to
    match pandas ``isnull``. Duplicate count preserves pandas' default
    ``duplicated(keep='first')`` meaning: repeated rows after the first.
    """
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_data_quality_charts requires a PolarsDataset")
    schema = dataset.schema
    columns = [name for name in schema if name != dataset.row_id]
    row_count = int(dataset.frame.select(pl.len()).collect().item())
    if not columns:
        return []

    def missing_expr(name):
        expr = pl.col(name).is_null()
        if schema[name].is_float():
            expr = expr | pl.col(name).is_nan()
        return expr

    missing_counts = dataset.frame.select([
        missing_expr(name).sum().alias(name) for name in columns
    ]).collect().row(0, named=True) if row_count else {name: 0 for name in columns}
    missing = [(name, int(missing_counts.get(name) or 0))
               for name in columns if int(missing_counts.get(name) or 0) > 0]
    models = []
    if missing:
        missing.sort(key=lambda item: (-item[1], item[0]))
        shown = missing[:max_columns]
        labels = [name for name, _ in shown]
        percentages = [round(count * 100 / row_count, 2) if row_count else 0
                       for _, count in shown]
        title = "Missing % by Column"
        if len(missing) > len(shown):
            title += f" (top {len(shown)} of {len(missing)})"
        models.append(make_chart_model(
            uid=str(uuid.uuid4()), chart_type="categorical", title=title,
            data={"dimensions": ["Column", "Missing %"], "series": [{
                "id": "missing-percent", "name": "Missing %", "type": "bar",
                "values": [[name, value] for name, value in zip(labels, percentages)],
            }]},
            recipe={"version": 1, "chart_type": "data_quality",
                    "config": {"view": "missing_summary"}},
            display={"title_size": 18, "text_color": "auto",
                     "x_title": "Column", "y_title": "Missing %",
                     "legend": {"show": False}, "labels": {"show": True},
                     "chart_background": "transparent"},
            source={"engine": "polars", "revision": dataset.revision},
        ))

        sample_count = max(0, int(sample_rows))
        heat_columns = columns[:max_columns]
        sample = (dataset.frame.sort(dataset.row_id).head(sample_count)
                  .select([dataset.row_id] + heat_columns).collect())
        row_labels = [str(row[dataset.row_id]) for row in sample.to_dicts()]
        cells = []
        for row_index, row in enumerate(sample.to_dicts()):
            for column_index, name in enumerate(heat_columns):
                cell_missing = row[name] is None
                if schema[name].is_float() and row[name] is not None:
                    try:
                        cell_missing = cell_missing or not math.isfinite(float(row[name]))
                    except (TypeError, ValueError):
                        pass
                cells.append([name, row_labels[row_index], 1 if cell_missing else 0])
        heat_title = "Missing Values Map"
        if row_count > len(row_labels) or len(columns) > len(heat_columns):
            heat_title += " (sampled)"
        models.append(make_chart_model(
            uid=str(uuid.uuid4()), chart_type="matrix_heatmap", title=heat_title,
            data={"dimensions": ["Column", "Source row ID", "Missing"],
                  "series": [{"id": "missing-map", "name": "Missing",
                              "type": "heatmap", "values": cells}]},
            recipe={"version": 1, "chart_type": "data_quality",
                    "config": {"view": "missing_map", "sample_rows": sample_rows}},
            display={"heatmap_rows": row_labels, "heatmap_columns": heat_columns,
                     "heatmap_min": 0, "heatmap_max": 1,
                     "heatmap_colors": ["#1e293b", "#ef4444"],
                     "show_colorbar": False,
                     "x_title": "Column", "y_title": "Source row ID",
                     "legend": {"show": False},
                     "labels": {"show": False},
                     "chart_background": "transparent"},
            source={"engine": "polars", "revision": dataset.revision,
                    "row_id": dataset.row_id},
        ))

    try:
        duplicate_excess = 0
        if row_count and columns:
            groups = dataset.frame.group_by(columns).len().filter(pl.col("len") > 1)
            duplicate_excess = int(groups.select(
                (pl.col("len") - 1).sum().fill_null(0)
            ).collect().item() or 0)
    except Exception:
        # Unsupported nested key types should not prevent missing-value
        # diagnostics from being shown.
        duplicate_excess = 0
    if duplicate_excess:
        unique_rows = max(row_count - duplicate_excess, 0)
        models.append(make_chart_model(
            uid=str(uuid.uuid4()), chart_type="pie_chart",
            title=f"Row Uniqueness — {duplicate_excess:,} duplicates of {row_count:,}",
            data={"dimensions": ["Row status", "Count"], "series": [{
                "id": "row-uniqueness", "name": "Rows", "type": "pie",
                "values": [["Unique", unique_rows], ["Duplicate", duplicate_excess]],
            }]},
            recipe={"version": 1, "chart_type": "data_quality",
                    "config": {"view": "duplicates"}},
            display={"title_size": 18, "text_color": "auto",
                     "palette": ["#6163df", "#ef4444"], "donut": True,
                     "donut_ratio": 48, "legend": {"show": True,
                     "position": "bottom", "layout": "horizontal"},
                     "labels": {"show": True, "formatter": "label+value"},
                     "chart_background": "transparent"},
            source={"engine": "polars", "revision": dataset.revision},
        ))
    return models


@lru_cache(maxsize=1)
def _natural_earth_lookup() -> dict:
    asset = (_ASSETS_ROOT / "echarts" / "maps" / "natural-earth" /
             "lookup.json")
    try:
        return json.loads(asset.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Natural Earth map lookup is unavailable: {exc}") from exc


def _geo_norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _map_agg_expr(column: str, operation: str, schema=None):
    """Aggregation expression that tolerates string-typed numeric columns.

    CSV exports carry numbers as text ("$1,234.56"); a bare pl.col(...).sum()
    on such a column raises "sum operation not supported for dtype str" and
    the whole map chart vanished. When a schema is supplied and the column is
    not natively numeric, parse it the same way the rest of the service does.
    """
    if schema is not None:
        try:
            dtype = schema.get(column)
            native_numeric = dtype is not None and (dtype.is_numeric() or dtype == pl.Boolean)
        except Exception:
            native_numeric = False
        if not native_numeric:
            value = _numeric_values_expr(column, schema)
        else:
            value = pl.col(column)
    else:
        value = pl.col(column)
    if operation == "mean":
        return value.mean()
    if operation == "median":
        return value.median()
    if operation == "min":
        return value.min()
    if operation == "max":
        return value.max()
    if operation == "count":
        return value.count()
    return value.sum()


def build_choropleth_chart(dataset: PolarsDataset, config: dict, *,
                           layout: Optional[dict] = None,
                           typography: Optional[dict] = None) -> Optional[dict]:
    """Build an offline Natural Earth choropleth model from Polars aggregates.

    The bundled maps are generalized cartographic boundaries. This builder
    resolves names/codes to those local features, aggregates values in Polars,
    and records unresolved category names for the map tooltip notice.
    """
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_choropleth_chart requires a PolarsDataset")
    config, layout, typography = (dict(config or {}), dict(layout or {}),
                                  dict(typography or {}))
    schema = dataset.schema
    geo_col = config.get("geo_col") or config.get("location_col")
    if geo_col not in schema or geo_col == dataset.row_id:
        return None

    def empty_model(message: str) -> dict:
        return make_chart_model(
            uid=str(uuid.uuid4()), chart_type="map_plot",
            title=f"Choropleth: {geo_col}",
            data={"dimensions": [str(geo_col)], "map": {}, "series": []},
            recipe={"version": 1, "chart_type": "map_plot", "config": config,
                    "layout": layout, "typography": typography},
            display={"empty_message": message,
                     "text_color": typography.get("axis_tick_color", "auto"),
                     "title_size": typography.get("header_size", 20),
                     "legend": {"show": False}},
            source={"engine": "polars", "revision": dataset.revision})

    def coordinate_fallback() -> Optional[dict]:
        lat_col = config.get("lat_col")
        lon_col = config.get("lon_col")
        if lat_col not in schema or not schema[lat_col].is_numeric():
            lat_col = next((name for name, dtype in schema.items()
                            if "lat" in name.lower() and dtype.is_numeric()), None)
        if lon_col not in schema or not schema[lon_col].is_numeric():
            lon_col = next((name for name, dtype in schema.items()
                            if any(part in name.lower() for part in ("lon", "lng", "long"))
                            and dtype.is_numeric()), None)
        if not lat_col or not lon_col:
            return None
        point_config = dict(config)
        point_config.update({"map_mode": "Scatter (Lat/Lon)", "lat_col": lat_col,
                             "lon_col": lon_col, "location_col": geo_col})
        model = build_map_plot_chart(dataset, point_config, layout=layout,
                                     typography=typography)
        if model:
            model["title"] = f"Locations by {geo_col} (city boundaries unavailable)"
            model["display"]["map_notice"] = (
                f"No bundled boundary matches {geo_col}; showing locations from coordinates.")
            return validate_chart_model(model)
        return None
    lookup = _natural_earth_lookup()
    country_aliases = lookup["country_aliases"]
    state_aliases = lookup["us_state_aliases"]
    admin1_aliases = lookup.get("admin1_aliases", state_aliases)
    unique_values = (dataset.frame.select(
        pl.col(geo_col).cast(pl.String).drop_nulls().unique()
    ).collect().to_series().to_list())
    normalized_values = [_geo_norm(value) for value in unique_values]
    column_name = _geo_norm(geo_col)
    prefer_state = any(word in column_name.split()
                       for word in ("state", "province", "region"))
    prefer_country = any(word in column_name.split()
                         for word in ("country", "nation", "territory", "geo"))

    def country_key(text: str) -> Optional[str]:
        found = country_aliases.get(text)
        if found:
            return found
        words = text.split()
        for size in (3, 2):
            if len(words) >= size and (found := country_aliases.get(" ".join(words[:size]))):
                return found
        return country_aliases.get(words[-1]) if words else None

    def admin1_key(text: str) -> Optional[str]:
        found = admin1_aliases.get(text)
        if found:
            return found
        words = text.split()
        # Accept common qualifiers when the source contains a state/province
        # name plus its country (for example "California, USA"). The bare
        # alias table only includes subdivision names when they are unique.
        for qualifier in ("united states of america", "united states", "usa", "us"):
            q = qualifier.split()
            if words[:len(q)] == q:
                found = admin1_aliases.get(" ".join(words[len(q):]))
            elif len(words) >= len(q) and words[-len(q):] == q:
                found = admin1_aliases.get(" ".join(words[:-len(q)]))
            if found:
                return found
        return None

    state_hits = sum(value in state_aliases for value in normalized_values)
    admin1_hits = sum(admin1_key(value) is not None for value in normalized_values)
    country_hits = sum(country_key(value) is not None for value in normalized_values)
    denominator = max(len(normalized_values), 1)
    if (prefer_state and max(state_hits, admin1_hits) / denominator >= 0.4):
        map_id, map_asset, aliases, names, resolver = (
            "lytrize_ne_admin1_50m", "admin1-50m", admin1_aliases,
            lookup.get("admin1_names", lookup["us_state_names"]),
            admin1_key)
    elif prefer_country and country_hits / denominator >= 0.4:
        map_id, map_asset, aliases, names, resolver = (
            "lytrize_ne_countries_110m", "countries-110m", country_aliases,
            lookup["country_names"], country_key)
    elif country_hits / denominator >= 0.4:
        map_id, map_asset, aliases, names, resolver = (
            "lytrize_ne_countries_110m", "countries-110m", country_aliases,
            lookup["country_names"], country_key)
    elif admin1_hits / denominator >= 0.4:
        map_id, map_asset, aliases, names, resolver = (
            "lytrize_ne_admin1_50m", "admin1-50m", admin1_aliases,
            lookup.get("admin1_names", lookup["us_state_names"]),
            admin1_key)
    else:
        log.warning("No Natural Earth match for map column %s", geo_col)
        return coordinate_fallback() or empty_model(
            f"No bundled boundary matches {geo_col}. Choose a country or state/province column, or switch to coordinate scatter.")

    source_to_key = {str(value): resolver(_geo_norm(value))
                     for value in unique_values}
    resolved_keys = {key for key in source_to_key.values() if key}
    unresolved = sorted(str(value) for value, key in source_to_key.items() if not key)
    if not resolved_keys:
        return coordinate_fallback() or empty_model(
            f"No bundled boundary matches values in {geo_col}. Choose a country or state/province column, or switch to coordinate scatter.")
    value_col = config.get("value_col")
    if value_col not in schema or value_col == dataset.row_id:
        value_col = None
    # Value columns may hold currency / grouped numbers as text; treat a
    # column as numeric when it PARSES as one, not just by declared dtype,
    # so "sum" on an amount column does not silently degrade to "count".
    numeric_value = False
    if value_col:
        try:
            numeric_value = bool(schema[value_col].is_numeric())
        except Exception:
            numeric_value = False
    if value_col and not numeric_value:
        try:
            numeric_value = bool(dataset.frame.select(
                _numeric_values_expr(value_col, schema).is_not_null().sum()
            ).collect().item())
        except Exception:
            numeric_value = False
    operation = str(config.get("agg_func") or "sum").strip().lower()
    if operation not in {"sum", "mean", "median", "count", "min", "max"}:
        operation = "sum"
    if not value_col or not numeric_value:
        operation = "count"

    mapped = dataset.frame.with_columns(
        pl.col(geo_col).cast(pl.String).replace_strict(
            source_to_key, default=None, return_dtype=pl.String
        ).alias("__ly_map_key")
    ).filter(pl.col("__ly_map_key").is_not_null())
    aggregations = []
    if operation == "count" and (not value_col or not numeric_value):
        aggregations.append(pl.len().alias("__ly_value"))
    else:
        mapped = mapped.with_columns(
            _numeric_values_expr(value_col, schema).alias("__ly_value_num"))
        aggregations.append(_map_agg_expr("__ly_value_num", operation)
                            .alias("__ly_value"))

    extra_columns = []
    hover_dims = config.get("hover_dims") or [config.get("hover_dim1"),
                                                config.get("hover_dim2")]
    for index, column in enumerate(hover_dims):
        if column in schema and column not in (dataset.row_id, geo_col, value_col):
            alias = f"__ly_dim_{index}"
            aggregations.append(pl.col(column).drop_nulls().first().alias(alias))
            extra_columns.append((alias, str(column)))
    for index, key in enumerate(("hover_val1", "hover_val2")):
        column = config.get(key)
        if column not in schema or column in (dataset.row_id, geo_col):
            continue
        agg_name = str(config.get(f"{key}_agg") or "sum").lower()
        if agg_name in {"avg", "average"}:
            agg_name = "mean"
        if agg_name not in {"sum", "mean", "median", "count", "min", "max"}:
            agg_name = "sum"
        if not schema[column].is_numeric():
            agg_name = "count"
        alias = f"__ly_val_{index}"
        aggregations.append(_map_agg_expr(column, agg_name, schema).alias(alias))
        extra_columns.append((alias, f"{agg_name.upper()}({column})"))

    if value_col and not numeric_value:
        mapped = mapped.filter(pl.col(value_col).is_not_null())

    try:
        result = mapped.group_by("__ly_map_key").agg(aggregations).collect()
    except Exception as exc:
        log.warning("Polars choropleth aggregation failed: %s", exc)
        return None
    rows = []
    values_for_scale = []
    for row in result.to_dicts():
        value = _json_scalar(row.get("__ly_value"))
        if value is None:
            continue
        value = float(value)
        if not math.isfinite(value):
            continue
        key = str(row["__ly_map_key"])
        values_for_scale.append(value)
        tooltip = {label: _json_scalar(row.get(alias))
                   for alias, label in extra_columns}
        rows.append([key, value, names.get(key, key), tooltip])
    if not rows:
        return None

    scale_name = str(layout.get("heatmap_colorscale") or
                     config.get("choropleth_colorscale") or "Blues").lower()
    colors = _heatmap_palette(scale_name, diverging=scale_name in {"rdbu", "rdylgn"})
    if bool(config.get("invert_colorscale")):
        colors.reverse()
    minimum, maximum = min(values_for_scale), max(values_for_scale)
    if minimum == maximum:
        maximum = minimum + 1
    projection = str(config.get("choropleth_projection") or "natural earth").lower()
    if projection not in {"natural earth", "mercator", "equirectangular"}:
        projection = "natural earth"
    scope = str(config.get("choropleth_scope") or "world").lower()
    if map_id == "lytrize_ne_admin1_50m" and scope == "world":
        scope = "usa"
    scope_view = {
        "world": ([0, 0], 1), "usa": ([-98, 39], 2.5),
        "europe": ([15, 52], 2.5), "asia": ([95, 34], 1.7),
        "africa": ([20, 0], 2), "north america": ([-100, 42], 1.8),
        "south america": ([-60, -15], 2),
    }
    center, zoom = scope_view.get(scope, scope_view["world"])
    title = str(layout.get("title") or
                f"Choropleth: {operation.upper()}({value_col or 'Rows'}) by {geo_col}")
    if unresolved:
        title += f" · {len(unresolved):,} unmatched"
    display = {
        "title_size": int(typography.get("header_size", 20)),
        "text_color": str(typography.get("axis_tick_color", "auto")),
        "map_asset": map_asset, "map_id": map_id,
        "map_center": center, "map_zoom": zoom,
        "map_projection": projection,
        "map_scope": scope,
        "map_borders": bool(config.get("choropleth_show_borders",
                                        config.get("show_borders", True))),
        "map_basemap": "satellite" if "satellite" in str(config.get("map_style", "")).lower() else "boundaries",
        "map_theme": "dark" if "dark" in str(config.get("map_style", "")).lower() else "light",
        "map_land_color": "#1e293b" if "dark" in str(config.get("map_style", "")).lower() else "#e8edf4",
        "map_border_color": "#64748b" if "dark" in str(config.get("map_style", "")).lower() else "#9aa8ba",
        "map_unresolved": unresolved,
        "map_value_title": f"{operation.upper()}({value_col or 'Rows'})",
        "heatmap_min": minimum, "heatmap_max": maximum,
        "heatmap_colors": colors,
        "show_colorbar": bool(layout.get("show_colorbar", True)),
        "colorbar_title": str(layout.get("colorbar_title") or
                               f"{operation.upper()}({value_col or 'Rows'})"),
        "colorbar_title_size": int(layout.get("colorbar_title_size", 11)),
        "colorbar_title_color": str(layout.get("colorbar_title_color", "#cbd5e1")),
        "colorbar_tick_color": str(layout.get("colorbar_tick_color", "#cbd5e1")),
        "colorbar_tick_size": int(layout.get("colorbar_tick_size", 10)),
        "colorbar_position": str(layout.get("colorbar_position", "right")).lower(),
        "colorbar_orientation": str(layout.get("colorbar_orientation", "auto")).lower(),
        "colorbar_border": bool(layout.get("show_border", False)),
        "chart_background": "transparent",
        "legend": {"show": False},
        "labels": {"show": False},
    }
    return make_chart_model(
        uid=str(uuid.uuid4()), chart_type="map_plot", title=title,
        data={"dimensions": [geo_col, value_col or "Rows"],
              "map": {"id": map_id, "asset": map_asset, "mode": "choropleth"},
              "series": [{"id": "choropleth", "name": title,
                          "type": "map", "values": rows}]},
        recipe={"version": 1, "chart_type": "map_plot", "config": config,
                "layout": layout, "typography": typography},
        display=display,
        source={"engine": "polars", "revision": dataset.revision,
                "boundary_dataset": "Natural Earth", "boundary_version": lookup["version"]},
    )


def build_map_plot_chart(dataset: PolarsDataset, config: dict, *,
                        layout: Optional[dict] = None,
                        typography: Optional[dict] = None) -> Optional[dict]:
    """Build an offline ECharts map model for every Map Plot mode.

    Coordinate scatter supports bounded sampled points and a lossless spatial
    density aggregation. When coordinates are absent, known country and US
    state names are placed at coarse local boundary centers. No tile server is
    contacted. Location-name choropleths use the dedicated region aggregator.
    """
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_map_plot_chart requires a PolarsDataset")
    config, layout, typography = (dict(config or {}), dict(layout or {}),
                                  dict(typography or {}))
    map_mode = str(config.get("map_mode", "")).strip().lower()
    is_3d_map = map_mode.startswith("3d globe") or map_mode.startswith("extruded map")
    if (map_mode.startswith("choropleth") or is_3d_map or
            (config.get("geo_col") and not config.get("lat_col") and
             not config.get("lon_col"))):
        model = build_choropleth_chart(dataset, config, layout=layout,
                                       typography=typography)
        if model and is_3d_map:
            model["display"]["map_visualization"] = (
                "globe" if map_mode.startswith("3d globe") else "extruded")
            model["display"]["map_interaction"] = (
                "drag-to-rotate" if map_mode.startswith("3d globe") else "wheel-to-zoom")
        return model
    schema = dataset.schema
    names = list(schema)

    def _ly_numeric_ok(name):
        if name not in schema:
            return False
        try:
            if schema[name].is_numeric() or schema[name] == pl.Boolean:
                return True
        except Exception:
            return False
        try:
            return bool(dataset.frame.select(
                _numeric_values_expr(name, schema).is_not_null().sum()
            ).collect().item())
        except Exception:
            return False

    lat = config.get("lat_col")
    lon = config.get("lon_col")
    if not _ly_numeric_ok(lat) or not _ly_numeric_ok(lon):
        lat = next((c for c in names if "lat" in c.lower() and _ly_numeric_ok(c)), None)
        lon = next((c for c in names if any(k in c.lower() for k in ("lon", "lng", "long")) and _ly_numeric_ok(c)), None)
    location = config.get("location_col")
    location = location if location in schema else None
    value_col = config.get("value_col")
    value_col = value_col if _ly_numeric_ok(value_col) else None
    color_col = config.get("color_col")
    color_col = color_col if color_col in schema else None
    mode = str(config.get("render_mode") or "Density (all points)").lower()
    sampled = mode.startswith("sample")
    density = not sampled
    input_count = None
    map_id, map_asset = "lytrize_ne_countries_110m", "countries-110m"
    lookup = _natural_earth_lookup()

    selected_cols = []
    point_lookup = None
    unresolved_names = []
    if lat in schema and lon in schema:
        selected_cols += [lat, lon]
        # When coordinates and a location are both selected, location-valued
        # measures are aggregated per location below. Keep that grouping
        # column in the projection as well as the coordinates.
        if location:
            selected_cols.append(location)
    elif location:
        aliases = lookup["country_aliases"]
        state_aliases = lookup["us_state_aliases"]
        admin1_aliases = lookup.get("admin1_aliases", state_aliases)
        def resolve_admin1(value):
            normalized = _geo_norm(value)
            found = admin1_aliases.get(normalized)
            if found:
                return found
            words = normalized.split()
            for qualifier in ("united states of america", "united states", "usa", "us"):
                q = qualifier.split()
                if words[:len(q)] == q:
                    found = admin1_aliases.get(" ".join(words[len(q):]))
                elif len(words) >= len(q) and words[-len(q):] == q:
                    found = admin1_aliases.get(" ".join(words[:-len(q)]))
                if found:
                    return found
            return None
        unique = (dataset.frame.select(pl.col(location).cast(pl.String).drop_nulls().unique())
                  .collect().to_series().to_list())
        admin1_hits = sum(resolve_admin1(v) is not None for v in unique)
        country_hits = sum(_geo_norm(v) in aliases for v in unique)
        prefer_admin1 = any(token in _geo_norm(location).split()
                            for token in ("state", "province", "region", "admin1"))
        if admin1_hits and (admin1_hits > country_hits or
                            (prefer_admin1 and admin1_hits >= country_hits)):
            map_id, map_asset = "lytrize_ne_admin1_50m", "admin1-50m"
            centers, resolver = lookup.get("admin1_centers", lookup["us_state_centers"]), resolve_admin1
        elif country_hits:
            centers, resolver = lookup["country_centers"], aliases
        else:
            return None
        point_lookup = {str(v): centers.get(resolver(v) if callable(resolver) else resolver.get(_geo_norm(v))) for v in unique}
        unresolved_names = sorted(str(v) for v, point in point_lookup.items() if point is None)
        selected_cols.append(location)
    else:
        return None

    extras = []
    hover_dims = config.get("hover_dims") or [config.get("hover_dim1"), config.get("hover_dim2")]
    for col in hover_dims:
        if col in schema and col not in selected_cols and col != value_col:
            extras.append((col, "first", str(col)))
    for key in ("hover_val1", "hover_val2"):
        col = config.get(key)
        if col not in schema:
            continue
        agg = str(config.get(f"{key}_agg") or "sum").strip().lower()
        agg = {"avg": "mean", "average": "mean"}.get(agg, agg)
        if agg not in {"sum", "mean", "median", "count", "min", "max"}:
            agg = "sum"
        if not schema[col].is_numeric():
            agg = "count"
        extras.append((col, agg, f"{agg.upper()}({col})"))
    for col in (value_col, color_col, config.get("size_col"), *(x[0] for x in extras)):
        if col in schema and col not in selected_cols:
            selected_cols.append(col)
    if not selected_cols:
        return None

    frame = dataset.frame.select(list(dict.fromkeys(selected_cols)))
    row_index = "__ly_map_row"
    while row_index in selected_cols:
        row_index = "_" + row_index
    if lat in schema and lon in schema:
        frame = frame.with_columns(
            _numeric_values_expr(lat, schema).alias("__ly_lat"),
            _numeric_values_expr(lon, schema).alias("__ly_lon"),
        ).filter(pl.col("__ly_lat").is_finite() & pl.col("__ly_lon").is_finite()
                 & pl.col("__ly_lat").is_between(-90, 90)
                 & pl.col("__ly_lon").is_between(-180, 180)
                 & ~((pl.col("__ly_lat") == 0) & (pl.col("__ly_lon") == 0)))
    else:
        frame = frame.with_columns(
            pl.col(location).cast(pl.String).replace_strict(
                {key: (value[0] if value else None) for key, value in point_lookup.items()},
                default=None, return_dtype=pl.Float64).alias("__ly_lon"),
            pl.col(location).cast(pl.String).replace_strict(
                {key: (value[1] if value else None) for key, value in point_lookup.items()},
                default=None, return_dtype=pl.Float64).alias("__ly_lat"),
        ).filter(pl.col("__ly_lat").is_not_null() & pl.col("__ly_lon").is_not_null())

    # If an explicit location and value are selected, aggregate one marker per
    # location, matching the legacy product behavior while keeping compute in Polars.
    group_location = bool(location and value_col)
    map_render_kind = "density" if density and not group_location else "scatter"
    effective_sampled = sampled and not group_location
    if effective_sampled:
        input_count = int(frame.select(pl.len()).collect().item())
    if group_location:
        agg = str(config.get("agg_func") or "mean").strip().lower()
        agg = {"avg": "mean", "average": "mean"}.get(agg, agg)
        if agg not in {"sum", "mean", "median", "count", "min", "max"}:
            agg = "mean"
        group_aggs = [
            pl.col("__ly_lon").first(), pl.col("__ly_lat").first(),
            _map_agg_expr(value_col, agg, schema).alias("__ly_metric"),
        ]
        if color_col and color_col != value_col:
            group_aggs.append(pl.col(color_col).drop_nulls().first().alias("__ly_color"))
        if config.get("size_col") in schema and config.get("size_col") != value_col:
            group_aggs.append(_map_agg_expr(config["size_col"], agg, schema).alias("__ly_size"))
        for i, (column, fn, _label) in enumerate(extras):
            group_aggs.append(
                pl.col(column).drop_nulls().first().alias(f"__ly_extra_{i}")
                if fn == "first" else
                _map_agg_expr(column, fn, schema).alias(f"__ly_extra_{i}"))
        frame = frame.group_by(location).agg(group_aggs)
        lat, lon = "__ly_lat", "__ly_lon"
    elif sampled:
        stride = max((input_count + 4999) // 5000, 1)
        frame = frame.with_row_index(row_index).filter((pl.col(row_index) % stride) == 0).limit(5000)
    elif density:
        # A bounded 45x45 grid means all source rows contribute while the
        # resulting model remains small enough for interactive Qt WebEngine.
        density_metric = value_col or (
            color_col if color_col and schema[color_col].is_numeric() else None)
        density_aggs = [pl.col("__ly_lat").mean(), pl.col("__ly_lon").mean(),
                        pl.len().alias("__ly_count")]
        if density_metric:
            density_aggs.append(
                _numeric_values_expr(density_metric, schema).mean().alias("__ly_metric"))
        if color_col and not schema[color_col].is_numeric() and color_col not in (location,):
            density_aggs.append(pl.col(color_col).drop_nulls().first().alias("__ly_color"))
        for index, (column, operation, _label) in enumerate(extras):
            density_aggs.append(
                pl.col(column).drop_nulls().first().alias(f"__ly_extra_{index}")
                if operation == "first" else
                _map_agg_expr(column, operation, schema).alias(f"__ly_extra_{index}"))
        frame = frame.with_columns(
            (((pl.col("__ly_lat").clip(-90, 90) + 90) * (45 / 180)).floor().clip(0, 44)).alias("__ly_ybin"),
            (((pl.col("__ly_lon").clip(-180, 180) + 180) * (45 / 360)).floor().clip(0, 44)).alias("__ly_xbin"),
        ).group_by(["__ly_ybin", "__ly_xbin"]).agg(density_aggs)
    rows = frame.collect().to_dicts()
    if not rows:
        return None
    # Normalize model inputs to finite JSON numbers and a compact row schema.
    if density and not group_location:
        data = []
        for row in rows:
            raw_measure = row.get("__ly_metric")
            if raw_measure is None:
                raw_measure = row.get("__ly_count", 0)
            measure = float(raw_measure)
            if math.isfinite(measure):
                details = {"Rows in cell": row.get("__ly_count")}
                if color_col and row.get("__ly_color") is not None:
                    details[color_col] = _json_scalar(row.get("__ly_color"))
                for index, (_column, _operation, label) in enumerate(extras):
                    details[label] = _json_scalar(row.get(f"__ly_extra_{index}"))
                data.append({"name": "Density", "value": [float(row["__ly_lon"]),
                    float(row["__ly_lat"]), measure], "details": details})
        series = [{"id": "density", "name": "Density", "type": "heatmap", "values": data}]
        numeric_values = [x["value"][2] for x in data]
        categorical = False
    else:
        metric_key = "__ly_metric" if group_location else value_col
        requested_size = config.get("size_col")
        if bool(layout.get("size_by_value")) and group_location:
            size_col = "__ly_metric"
        else:
            if not requested_size and bool(layout.get("size_by_value")):
                requested_size = value_col
            size_col = requested_size if requested_size in schema else None
        categories = []
        color_source = "__ly_color" if group_location and color_col and color_col != value_col else color_col
        numeric_color_column = bool(color_col and schema[color_col].is_numeric())
        if numeric_color_column and group_location and color_source != value_col:
            metric_key = color_source
        elif numeric_color_column and not metric_key:
            metric_key = color_source
        for row in rows:
            candidate = row.get(color_source) if color_source else None
            if candidate is not None and not isinstance(candidate, (int, float)):
                label = str(candidate)
                if label not in categories:
                    categories.append(label)
        categorical = bool(color_col and categories and len(categories) <= 25)
        numeric_values, series = [], []
        groups = categories if categorical else [None]
        size_key = ("__ly_size" if group_location and size_col and
                    size_col not in {value_col, "__ly_metric"} else size_col)
        raw_sizes = []
        if size_key:
            for row in rows:
                try:
                    raw_sizes.append(float(row.get(size_key)))
                except (TypeError, ValueError):
                    continue
        raw_sizes = [value for value in raw_sizes if math.isfinite(value)]
        size_low = float(config.get("marker_size_min", 4))
        size_high = float(config.get("marker_size_max", 22))
        size_min_raw, size_max_raw = ((min(raw_sizes), max(raw_sizes))
                                      if raw_sizes else (0.0, 0.0))
        palette_name = str(layout.get("heatmap_colorscale") or config.get("choropleth_colorscale") or "Viridis")
        palette = _heatmap_palette(palette_name, diverging=palette_name.lower() in {"rdbu", "rdylgn"})
        if config.get("invert_colorscale"):
            palette.reverse()
        for group_index, group_name in enumerate(groups):
            values = []
            for row in rows:
                label = str(row.get(color_source)) if color_source and row.get(color_source) is not None else None
                if categorical and label != group_name:
                    continue
                try:
                    longitude, latitude = float(row.get("__ly_lon", row.get(lon) if lon in schema else None)), float(row.get("__ly_lat", row.get(lat) if lat in schema else None))
                except (TypeError, ValueError):
                    continue
                metric = row.get(metric_key) if metric_key else None
                numeric_color = (float(metric) if metric is not None and isinstance(metric, (int, float)) else None)
                if numeric_color is not None and math.isfinite(numeric_color):
                    numeric_values.append(numeric_color)
                size_value = row.get(size_key) if size_key else None
                try:
                    size_value = float(size_value) if size_value is not None else 1.0
                except (TypeError, ValueError):
                    size_value = 1.0
                if raw_sizes and size_max_raw > size_min_raw:
                    size_value = size_low + (size_value - size_min_raw) * (size_high - size_low) / (size_max_raw - size_min_raw)
                else:
                    size_value = max(size_low, 8)
                details = {label: _json_scalar(row.get(f"__ly_extra_{i}") if group_location else row.get(col))
                           for i, (col, fn, label) in enumerate(extras)}
                point_name = row.get(location) if location else None
                if point_name is None:
                    point_name = row.get(row_index)
                if point_name is None:
                    point_name = "Location"
                point = {"name": str(point_name),
                    "displayName": str(point_name),
                    "value": [longitude, latitude, numeric_color, size_value],
                    "details": details}
                if categorical:
                    point["itemColor"] = palette[group_index % len(palette)]
                values.append(point)
            if values:
                series.append({"id": f"locations-{group_index}", "name": str(group_name or (color_col or "Locations")),
                    "type": "scatter", "values": values})
        if not series:
            return None

    if not series:
        return None
    values_for_center = [r.get("__ly_lat") for r in rows if r.get("__ly_lat") is not None]
    lon_for_center = [r.get("__ly_lon") for r in rows if r.get("__ly_lon") is not None]
    center = [sum(lon_for_center) / len(lon_for_center), sum(values_for_center) / len(values_for_center)] if lon_for_center and values_for_center else [0, 0]
    scope = str(config.get("choropleth_scope") or "world").lower()
    zoom = {"world": 1, "usa": 2.5, "europe": 2.5, "asia": 1.7, "africa": 2,
            "north america": 1.8, "south america": 2}.get(scope, 1)
    scope_centers = {"usa": [-98, 39], "europe": [15, 52], "asia": [95, 34],
                     "africa": [20, 0], "north america": [-100, 42],
                     "south america": [-60, -15]}
    if scope != "world":
        center = scope_centers.get(scope, center)
    elif values_for_center and lon_for_center:
        span = max(max(lon_for_center) - min(lon_for_center),
                   max(values_for_center) - min(values_for_center))
        zoom = max(1.0, min(12.0, 90.0 / max(span, 7.5)))
    min_value = min(numeric_values) if numeric_values else 0
    max_value = max(numeric_values) if numeric_values else 1
    if min_value == max_value:
        max_value = min_value + 1
    display = {
        "title_size": int(typography.get("header_size", 20)),
        "text_color": str(typography.get("axis_tick_color", "auto")),
        "chart_background": "transparent", "legend": {"show": categorical, "position": "bottom", "layout": "horizontal", "font_size": 10},
        "map_asset": map_asset, "map_id": map_id, "map_center": center,
        "map_zoom": zoom, "map_projection": str(config.get("choropleth_projection") or "natural earth"),
        "map_scope": scope, "map_borders": bool(config.get("show_borders", True)),
        "map_basemap": "satellite" if "satellite" in str(config.get("map_style", "")).lower() else "boundaries",
        "map_theme": "dark" if "dark" in str(config.get("map_style", "")).lower() else "light",
        "map_land_color": "#1e293b" if "dark" in str(config.get("map_style", "")).lower() else "#e8edf4",
        "map_border_color": "#64748b" if "dark" in str(config.get("map_style", "")).lower() else "#9aa8ba",
        "map_mode": map_render_kind, "map_sampled": effective_sampled,
        "map_unresolved": unresolved_names,
        "map_numeric_color": bool(numeric_values and not density),
        "map_point_count": int(input_count if effective_sampled else
                                (sum(int(r.get("__ly_count") or 0) for r in rows)
                                 if density and not group_location else len(rows))),
        "map_marker_opacity": float(config.get("marker_opacity", 0.82)),
        "map_marker_min": int(config.get("marker_size_min", 4)), "map_marker_max": int(config.get("marker_size_max", 22)),
        "map_marker_size": int(layout.get("marker_size", 6)),
        "map_size_by_value": bool(layout.get("size_by_value", False)),
        "heatmap_min": min_value, "heatmap_max": max_value,
        "heatmap_colors": _heatmap_palette(str(layout.get("heatmap_colorscale") or "Viridis"), diverging=False),
        "show_colorbar": bool(layout.get("show_colorbar", bool(numeric_values))),
        "colorbar_title": str(layout.get("colorbar_title") or color_col or value_col or "Count"),
        "colorbar_title_size": int(layout.get("colorbar_title_size", 11)),
        "colorbar_title_color": str(layout.get("colorbar_title_color", "#cbd5e1")),
        "colorbar_tick_size": int(layout.get("colorbar_tick_size", 10)),
        "colorbar_tick_color": str(layout.get("colorbar_tick_color", "#cbd5e1")),
        "colorbar_position": str(layout.get("colorbar_position", "right")).lower(),
        "colorbar_orientation": str(layout.get("colorbar_orientation", "auto")).lower(),
        "colorbar_border": bool(layout.get("show_border", False)),
    }
    default_title = f"Map: {location or (lat + ' / ' + lon if lat in schema and lon in schema else 'Locations')}"
    default_title += f" · {len(rows):,} {('sampled points' if effective_sampled else 'density cells' if density and not group_location else 'locations' if group_location else 'points')}"
    if unresolved_names:
        default_title += f" · {len(unresolved_names):,} unmatched"
    if effective_sampled:
        default_title += f" of {input_count:,} valid rows"
    title = str(layout.get("title") or default_title)
    return make_chart_model(uid=str(uuid.uuid4()), chart_type="map_plot", title=title,
        data={"dimensions": [lat or location, lon or "location"],
              "map": {"id": map_id, "asset": map_asset, "mode": map_render_kind},
              "series": series},
        recipe={"version": 1, "chart_type": "map_plot", "config": config,
                "layout": layout, "typography": typography}, display=display,
        source={"engine": "polars", "revision": dataset.revision,
                "boundary_dataset": "Natural Earth", "boundary_version": lookup["version"]})


def _categorical_display(layout: Optional[dict], typography: Optional[dict],
                         palette: list[str], *, theme_mode: Optional[str] = None) -> dict:
    layout, typography = dict(layout or {}), dict(typography or {})
    position = str(layout.get("legend_position", "Top Right")).lower()
    orientation = str(layout.get("legend_orientation", "Auto")).lower()
    if orientation == "auto":
        orientation = "vertical" if position.startswith(("left", "right")) else "horizontal"
    if not theme_mode:
        try:
            from desktop.theme import current_mode
            theme_mode = current_mode()
        except Exception:
            theme_mode = "light"
    return {
        "theme_mode": theme_mode,
        "title_size": typography.get("header_size", 20),
        "title_align": typography.get("header_align", "left"),
        "title_weight": 700 if "bold" in str(typography.get("header_font_style", "")).lower() else 600,
        "text_color": typography.get("axis_tick_color", "auto"),
        "palette": palette,
        "chart_background": "transparent",
        # Charts sharing the categorical family (bars, statistical bars) DO
        # draw a legend -- per-category rows for single-series bars, one row
        # per series otherwise. The builders whose legend would be noise (time
        # series, correlation, heatmaps, plain histograms) flip "show" back
        # off explicitly; the live-edit paths keep the model's stored value.
        "legend": {"show": True, "position": position,
                   "layout": orientation, "title": layout.get("legend_title", ""),
                   "font_size": typography.get("legend_item_size", 11),
                   "title_font_size": typography.get("legend_title_size", 12),
                   "item_color": typography.get("legend_item_color", ""),
                   "title_color": typography.get("legend_title_color", ""),
                   "click": layout.get("legend_itemclick", "toggle") != "none"},
        "labels": {"show": bool(layout.get("show_value_labels", False)),
                   "position": str(layout.get("label_position", "outside")).lower(),
                   "font_size": typography.get("value_label_size", 11),
                   "color": layout.get("value_label_color", "auto")},
        "bar_mode": str(layout.get("bar_mode", "group")).lower(),
        # Keep these renderer-neutral controls on the model in both the
        # generation and live-preview paths. The ECharts adapter translates
        # bar_gap to category spacing and line_width to lineStyle.width.
        "bar_gap": max(0.0, min(0.9, float(layout.get("bar_gap", 0.28)))),
        "line_width": max(1, min(10, int(layout.get("line_width", 2)))),
        # Grid on by default and written EXPLICITLY: an undefined display key
        # forced the renderer to fall back to the saved recipe every time.
        "show_grid": bool(layout.get("show_grid", True)),
    }


def build_nightingale_rose_chart(dataset: PolarsDataset, config: dict, *,
                                roles: Optional[dict] = None,
                                layout: Optional[dict] = None,
                                typography: Optional[dict] = None,
                                uid: Optional[str] = None) -> Optional[dict]:
    """Build a polar-area rose chart from the same aggregated data as a pie."""
    model = build_pie_chart(dataset, config, roles=roles, layout=layout,
                            typography=typography, uid=uid)
    if not model:
        return None
    model["chart_type"] = "nightingale_rose"
    model["title"] = str((layout or {}).get("title") or
                         model.get("title", "Nightingale Rose"))
    model["display"] = {**dict(model.get("display") or {}),
                         "donut": False,
                         "rose_type": str((layout or {}).get("rose_type", "area"))}
    model["recipe"] = {**dict(model.get("recipe") or {}),
                       "chart_type": "nightingale_rose",
                       "layout": dict(layout or {}),
                       "typography": dict(typography or {})}
    return validate_chart_model(model)


def build_advanced_chart(dataset: PolarsDataset, config: dict, *,
                         roles: Optional[dict] = None,
                         layout: Optional[dict] = None,
                         typography: Optional[dict] = None,
                         uid: Optional[str] = None) -> Optional[dict]:
    """Build Sankey, Sunburst, Calendar, Waterfall, Funnel, or map models.

    Aggregation stays in Polars. Only the already-grouped rows are collected
    into JSON-safe arrays for the ECharts renderer.
    """
    config, layout, typography = dict(config or {}), dict(layout or {}), dict(typography or {})
    kind = str(config.get("chart_type") or "").strip()
    schema = dataset.schema
    roles = roles or dataset.roles()
    operation = _AGGREGATIONS.get(str(config.get("agg", "sum")).lower(), "sum")
    metric = config.get("value_col") or config.get("y")
    if metric in ("", "None") or metric not in schema:
        metric = None
    palette = _palette(config.get("palette"))
    chart_uid = uid or str(uuid.uuid4())

    def agg_expr(col):
        if col is None:
            return pl.len().alias("__value")
        return _aggregate_expression(operation, col).alias("__value")

    def category_values(dimension):
        if dimension not in schema or dimension == dataset.row_id:
            return None
        result = dataset.frame.filter(pl.col(dimension).is_not_null()).group_by(
            dimension).agg(agg_expr(metric))
        result = result.sort(dimension if kind == "waterfall" else "__value",
                             descending=kind != "waterfall").collect()
        return [[_json_scalar(a), _json_scalar(b)] for a, b in result.iter_rows()]

    if kind == "sankey":
        dimensions = list(roles.get("categorical", []))
        source = config.get("source_col") or (dimensions[0] if dimensions else None)
        target = config.get("target_col") or (dimensions[1] if len(dimensions) > 1 else None)
        if source not in schema or target not in schema or source == target:
            return None
        grouped = (dataset.frame.filter(pl.col(source).is_not_null() & pl.col(target).is_not_null())
                   .group_by([source, target]).agg(agg_expr(metric)).collect())
        links, names = [], set()
        for src, dst, value in grouped.iter_rows():
            src, dst, value = str(_json_scalar(src)), str(_json_scalar(dst)), float(_json_scalar(value) or 0)
            names.update((src, dst))
            links.append({"source": src, "target": dst, "value": value})
        data = {"dimensions": [source, target], "series": [],
                "sankey": {"nodes": [{"name": name} for name in sorted(names)], "links": links}}
        title = f"Flow: {source} → {target}"
    elif kind == "sunburst":
        raw_paths = config.get("path_cols") or roles.get("dimensions", [])[:3]
        if isinstance(raw_paths, str):
            raw_paths = [raw_paths]
        paths = [c for c in raw_paths if c in schema and c != dataset.row_id][:5]
        if not paths:
            return None
        grouped = (dataset.frame.filter(pl.all_horizontal([pl.col(c).is_not_null() for c in paths]))
                   .group_by(paths).agg(agg_expr(metric)).collect())
        root = {"name": "All", "children": []}
        for row in grouped.iter_rows(named=True):
            node = root
            for depth, col in enumerate(paths):
                name = str(_json_scalar(row[col]))
                child = next((item for item in node["children"] if item["name"] == name), None)
                if child is None:
                    child = {"name": name, "children": []}
                    node["children"].append(child)
                node = child
            node["value"] = float(_json_scalar(row["__value"]) or 0)
        def finalize(node):
            if node.get("children"):
                node["children"] = [finalize(child) for child in node["children"]]
                node["value"] = sum(float(child.get("value", 0)) for child in node["children"])
            node.pop("children", None) if not node.get("children") else None
            return node
        data = {"dimensions": paths, "series": [], "tree": [finalize(root)]}
        title = " / ".join(paths)
    elif kind == "calendar_heatmap":
        date_col = config.get("date_col") or next(iter(roles.get("datetime", [])), None)
        if date_col not in schema:
            return None
        date_expr = _parse_dates(dataset, date_col).cast(pl.Date, strict=False).alias("__date")
        grouped = (dataset.frame.with_columns(date_expr).filter(pl.col("__date").is_not_null())
                   .group_by("__date").agg(agg_expr(metric)).sort("__date").collect())
        values = [[str(_json_scalar(day)), _json_scalar(value)] for day, value in grouped.iter_rows()]
        if not values:
            return None
        data = {"dimensions": [date_col], "series": [],
                "calendar": {"values": values, "range": [values[0][0], values[-1][0]]}}
        title = f"Daily {metric or 'row count'}"
    elif kind in {"waterfall", "funnel"}:
        dimension = config.get("x")
        if isinstance(dimension, (list, tuple)):
            dimension = dimension[0] if dimension else None
        if dimension not in schema:
            return None
        values = category_values(dimension)
        if not values:
            return None
        data = {"dimensions": [dimension],
                "series": [{"id": metric or "count", "name": metric or "Count",
                            "type": "bar", "values": values}]}
        if kind == "waterfall":
            data["waterfall_totals"] = [str(pair[0]) for pair in values
                if str(pair[0]).strip().lower() in {"total", "subtotal", "net total", "ending balance"}]
        title = f"{kind.title()}: {metric or 'Count'} by {dimension}"
    else:
        return None

    model = make_chart_model(uid=chart_uid, chart_type=kind,
        title=str(layout.get("title") or title), data=data,
        recipe={"version": 1, "chart_type": kind, "config": config,
                "layout": layout, "typography": typography},
        display={**_display(layout, typography, palette),
                 "palette": palette, "text_color": typography.get("axis_tick_color", "#263247"),
                 "heatmap_colors": _heatmap_palette(layout.get("heatmap_colorscale", "Blues"), False)})
    return apply_chart_model_display_options(model, layout, typography)


def apply_chart_model_display_options(model: dict, layout: dict,
                                      typography: dict) -> dict:
    """Apply live display controls for migrated native chart families."""
    model = validate_chart_model(model)
    typography = dict(typography or {})
    layout = dict(layout or {})
    previous_display = dict(model.get("display") or {})
    theme_mode = previous_display.get("theme_mode")
    if not theme_mode:
        try:
            from desktop.theme import current_mode
            theme_mode = current_mode()
        except Exception:
            theme_mode = "light"
    try:
        from font_bundle import resolve
        for key in ("family", "header_family"):
            if typography.get(key):
                typography[key] = resolve(typography[key])
    except Exception:
        pass
    if model.get("chart_type") in {"pie_chart", "pie", "nightingale_rose"}:
        result = apply_pie_display_options(model, layout, typography)
        if result.get("chart_type") == "nightingale_rose":
            result["display"]["rose_type"] = str((layout or {}).get(
                "rose_type", (result.get("display") or {}).get("rose_type", "area")))
            result["recipe"] = {**dict(result.get("recipe") or {}),
                                "layout": dict(layout or {}),
                                "typography": dict(typography or {})}
        result["display"]["theme_mode"] = theme_mode
        return validate_chart_model(result)
    if model.get("chart_type") in {"sankey", "sunburst", "calendar_heatmap",
                                   "waterfall", "funnel", }:
        result = dict(model)
        previous = dict(model.get("display") or {})
        result["display"] = {
            **previous,
            "sankey_orient": str((layout or {}).get("sankey_orient", previous.get("sankey_orient", "horizontal"))),
            "sankey_align": str((layout or {}).get("sankey_align", previous.get("sankey_align", "justify"))),
            "sankey_node_gap": int((layout or {}).get("sankey_node_gap", previous.get("sankey_node_gap", 10))),
            "sankey_node_width": int((layout or {}).get("sankey_node_width", previous.get("sankey_node_width", 18))),
            "sankey_iterations": max(0, min(64, int((layout or {}).get("sankey_iterations", previous.get("sankey_iterations", 32))))),
            "sunburst_node_click": str((layout or {}).get("sunburst_node_click", previous.get("sunburst_node_click", "rootToNode"))),
            "sunburst_inner_radius": int((layout or {}).get("sunburst_inner_radius", previous.get("sunburst_inner_radius", 0))),
            "sunburst_sort": str((layout or {}).get("sunburst_sort", previous.get("sunburst_sort", "desc"))),
            "sunburst_label_rotate": str((layout or {}).get("sunburst_label_rotate", previous.get("sunburst_label_rotate", "radial"))),
            "calendar_orient": str((layout or {}).get("calendar_orient", previous.get("calendar_orient", "horizontal"))),
            "calendar_cell_size": max(8, min(48, int((layout or {}).get("calendar_cell_height", previous.get("calendar_cell_size", 18))))),
            "waterfall_start": float((layout or {}).get("waterfall_start", previous.get("waterfall_start", 0))),
            "funnel_sort": str((layout or {}).get("funnel_sort", previous.get("funnel_sort", "descending"))),
            "funnel_orient": str((layout or {}).get("funnel_orient", previous.get("funnel_orient", "vertical"))),
            "funnel_label_position": str((layout or {}).get("funnel_label_position", previous.get("funnel_label_position", "inside"))),
            "funnel_gap": max(0, min(40, int((layout or {}).get("funnel_gap", previous.get("funnel_gap", 2))))),
            "funnel_min_size": max(0, min(100, int((layout or {}).get("funnel_min_size", previous.get("funnel_min_size", 0))))),
            "funnel_max_size": max(1, min(100, int((layout or {}).get("funnel_max_size", previous.get("funnel_max_size", 100))))),
            "map_projection": str((layout or {}).get("map_projection", previous.get("map_projection", "natural earth"))),
            "title_size": int((typography or {}).get("header_size", previous.get("title_size", 20))),
            "text_color": str((typography or {}).get("axis_tick_color", previous.get("text_color", "auto"))),
            "palette": previous.get("palette", _palette(None)),
        }
        title = str((layout or {}).get("title") or "").strip()
        if title:
            result["title"] = title
        result["recipe"] = {**dict(model.get("recipe") or {}),
                            "layout": dict(layout or {}),
                            "typography": dict(typography or {})}
        result["display"]["theme_mode"] = theme_mode
        return validate_chart_model(result)
    if model.get("chart_type") == "map_plot":
        result = dict(model)
        previous = dict(model.get("display") or {})
        layout, typography = dict(layout or {}), dict(typography or {})
        recipe = dict(model.get("recipe") or {})
        recipe_config = dict(recipe.get("config") or {})
        palette_name = layout.get("heatmap_colorscale") or recipe_config.get(
            "choropleth_colorscale", "Blues")
        palette = _heatmap_palette(str(palette_name),
                                   diverging=str(palette_name).lower() in {"rdbu", "rdylgn"})
        if bool(recipe_config.get("invert_colorscale")):
            palette.reverse()
        result["display"] = {
            **previous,
            "text_color": str(typography.get("axis_tick_color",
                                               previous.get("text_color", "auto"))),
            "title_size": int(typography.get("header_size", previous.get("title_size", 20))),
            "heatmap_colors": palette,
            "show_colorbar": bool(layout.get("show_colorbar", previous.get("show_colorbar", True))),
            "colorbar_title": str(layout.get("colorbar_title", previous.get("colorbar_title", ""))),
            "colorbar_title_size": int(layout.get("colorbar_title_size", previous.get("colorbar_title_size", 11))),
            "colorbar_title_color": str(layout.get("colorbar_title_color", previous.get("colorbar_title_color", "#cbd5e1"))),
            "colorbar_tick_size": int(layout.get("colorbar_tick_size", previous.get("colorbar_tick_size", 10))),
            "colorbar_tick_color": str(layout.get("colorbar_tick_color", previous.get("colorbar_tick_color", "#cbd5e1"))),
            "colorbar_position": str(layout.get("colorbar_position", previous.get("colorbar_position", "right"))).lower(),
            "colorbar_orientation": str(layout.get("colorbar_orientation", previous.get("colorbar_orientation", "auto"))).lower(),
            "colorbar_border": bool(layout.get("show_border", previous.get("colorbar_border", False))),
            "map_marker_size": int(layout.get("marker_size", previous.get("map_marker_size", 6))),
            "map_size_by_value": bool(layout.get("size_by_value", previous.get("map_size_by_value", False))),
        }
        title = str(layout.get("title") or "").strip()
        if title:
            result["title"] = title
        recipe["layout"] = layout
        recipe["typography"] = typography
        result["recipe"] = recipe
        result["display"]["theme_mode"] = theme_mode
        return validate_chart_model(result)
    if model.get("chart_type") == "categorical":
        result = dict(model)
        previous = model.get("display") or {}
        result["display"] = _categorical_display(
            layout, typography, list(previous.get("palette") or _palette(None)), theme_mode=theme_mode)
        result["display"].update({
            "legend": {
                **result["display"]["legend"],
                "show": bool(layout.get("show_legend", (previous.get("legend") or {}).get("show", True))),
            },
            "orientation": previous.get("orientation", "vertical"),
            "x_title": layout["x_label"] if "x_label" in layout else previous.get("x_title", ""),
            "y_title": layout["y_label"] if "y_label" in layout else previous.get("y_title", ""),
            "dual_axis": bool(previous.get("dual_axis", False)),
            "dual_axis_orientation": previous.get("dual_axis_orientation", "y"),
            "x2_title": layout["x2_label"] if "x2_label" in layout else previous.get("x2_title", ""),
            "y2_title": layout["y2_label"] if "y2_label" in layout else previous.get("y2_title", ""),
            "show_grid": bool(layout.get("show_grid", previous.get("show_grid", True))),
            "primary_show_value_labels": bool(layout.get("primary_show_value_labels",
                layout.get("show_value_labels", (previous.get("labels") or {}).get("show", False)))),
            "secondary_show_value_labels": bool(layout.get("secondary_show_value_labels",
                layout.get("show_value_labels", (previous.get("labels") or {}).get("show", False)))),
            "show_border": bool(layout.get("show_border", previous.get("show_border", False))),
            "bar_gap": float(layout.get("bar_gap", previous.get("bar_gap", 0.28))),
            "bar_mode": str(layout.get("bar_mode", previous.get("bar_mode", "group"))).lower(),
            "line_width": int(layout.get("line_width", previous.get("line_width", 2))),
        })
        title = str((layout or {}).get("title") or "").strip()
        if title:
            result["title"] = title
        result["recipe"] = {**dict(model.get("recipe") or {}),
                            "layout": dict(layout or {}),
                            "typography": dict(typography or {})}
        result["display"]["theme_mode"] = theme_mode
        return validate_chart_model(result)
    if model.get("chart_type") == "data_table":
        result = dict(model)
        previous = model.get("display") or {}
        layout, typography = dict(layout or {}), dict(typography or {})
        result["display"] = {
            **previous,
            "theme_mode": theme_mode,
            "table_font_size": int(layout.get("table_font_size", previous.get("table_font_size", 11))),
            "table_header_font_size": int(layout.get("table_header_font_size", previous.get("table_header_font_size", 12))),
            "table_row_height": int(layout.get("table_row_height", previous.get("table_row_height", 26))),
            "table_header_height": int(layout.get("table_header_height", previous.get("table_header_height", 28))),
            "table_index_align": str(layout.get("table_index_align", previous.get("table_index_align", "left"))),
            "table_data_align": str(layout.get("table_data_align", previous.get("table_data_align", "right"))),
            "table_number_format": str(layout.get("table_number_format", previous.get("table_number_format", ",.2f"))),
            "table_font_color": str(layout.get("table_font_color", previous.get("table_font_color", "auto"))),
            "table_header_color": str(layout.get("table_header_color", previous.get("table_header_color", "#6163df"))),
            "table_stripe_even_color": str(layout.get("table_stripe_even_color", previous.get("table_stripe_even_color", "auto"))),
            "table_stripe_odd_color": str(layout.get("table_stripe_odd_color", previous.get("table_stripe_odd_color", "auto"))),
            "table_heat_enabled": bool(layout.get("table_heat_enabled", previous.get("table_heat_enabled", True))),
            "table_heat_color": str(layout.get("table_heat_color", previous.get("table_heat_color", "#1e40af"))),
            "table_font_family": str(typography.get("family", previous.get("table_font_family", "Inter"))),
            "table_header_font_family": str(typography.get("header_family", typography.get("family", previous.get("table_header_font_family", "Inter")))),
            "title_size": int(typography.get("header_size", previous.get("title_size", 14))),
            "title_font_style": str(typography.get("header_font_style", previous.get("title_font_style", "Bold"))),
            "title_align": str(typography.get("header_align", previous.get("title_align", "left"))),
            "title_color": str(typography.get("header_color", previous.get("title_color", "auto"))),
            "text_color": str(typography.get("axis_tick_color", previous.get("text_color", "auto"))),
        }
        title = str(layout.get("title") or "").strip()
        if title:
            result["title"] = title
        result["recipe"] = {**dict(model.get("recipe") or {}),
                            "layout": layout, "typography": typography}
        return validate_chart_model(result)
    if model.get("chart_type") == "matrix_heatmap":
        result = dict(model)
        previous = model.get("display") or {}
        recipe_config = dict((model.get("recipe") or {}).get("config") or {})
        operation = _AGGREGATIONS.get(
            str(recipe_config.get("agg", "avg")).strip().lower(), "mean")
        diverging = operation in {"mean", "median", "std"}
        result["display"] = dict(previous)
        result["display"].update({
            "text_color": (typography or {}).get(
                "axis_tick_color", previous.get("text_color", "auto")),
        })
        # The SAME helper the generation path uses -- one definition, so the
        # two paths cannot drift apart again.
        result["display"].update(_heatmap_display_options(
            layout, typography, previous=previous, diverging=diverging))
        title = str((layout or {}).get("title") or "").strip()
        if title:
            result["title"] = title
        result["recipe"] = {**dict(model.get("recipe") or {}),
                            "layout": dict(layout or {}),
                            "typography": dict(typography or {})}
        result["display"]["theme_mode"] = theme_mode
        return validate_chart_model(result)
    if model.get("chart_type") == "time_series":
        result = dict(model)
        previous = model.get("display") or {}
        result["display"] = _categorical_display(
            layout, typography, list(previous.get("palette") or _palette(None)), theme_mode=theme_mode)
        result["display"].update({
            # A single time-series line has no legend; only the dual-axis
            # overlay showed one at build time, so live edits keep whatever
            # the model had instead of the categorical family default.
            "legend": {**(result["display"].get("legend") or {}),
                       "show": bool(layout.get("show_legend", (previous.get("legend") or {}).get("show", False)))},
            "orientation": "vertical",
            "x_title": layout["x_label"] if "x_label" in layout else previous.get("x_title", ""),
            "y_title": layout["y_label"] if "y_label" in layout else previous.get("y_title", ""),
            "dual_axis": bool(previous.get("dual_axis", False)),
            "y2_title": layout["y2_label"] if "y2_label" in layout else previous.get("y2_title", ""),
            "show_grid": bool(layout.get("show_grid", previous.get("show_grid", True))),
            "show_border": bool(layout.get("show_border", previous.get("show_border", False))),
            "line_width": int((layout or {}).get("line_width", previous.get("line_width", 2))),
            "markers": bool((layout or {}).get("show_markers", previous.get("markers", True))),
            "marker_size": int((layout or {}).get("marker_size", previous.get("marker_size", 6))),
            "line_shape": str((layout or {}).get(
                "line_shape", previous.get("line_shape", "linear"))),
            "smooth": str((layout or {}).get(
                "line_shape", previous.get("line_shape", "linear"))) == "spline",
            "line_fill": (layout or {}).get("line_fill", previous.get("line_fill", "none")),
            "theme_mode": theme_mode,
        })
        title = str((layout or {}).get("title") or "").strip()
        if title:
            result["title"] = title
        result["recipe"] = {**dict(model.get("recipe") or {}),
                            "layout": dict(layout or {}),
                            "typography": dict(typography or {})}
        result["display"]["theme_mode"] = theme_mode
        return validate_chart_model(result)
    if model.get("chart_type") == "distribution":
        result = dict(model)
        previous = model.get("display") or {}
        display = _categorical_display(
            layout, typography, list(previous.get("palette") or _palette(None)), theme_mode=theme_mode)
        display["legend"]["show"] = bool(
            (previous.get("legend") or {}).get("show", False))
        display["orientation"] = "vertical"
        display["x_title"] = layout["x_label"] if "x_label" in layout else previous.get("x_title", "")
        display["y_title"] = layout["y_label"] if "y_label" in layout else previous.get("y_title", "Count")
        display["show_grid"] = bool(layout.get("show_grid", previous.get("show_grid", True)))
        display["show_border"] = bool(layout.get("show_border", previous.get("show_border", False)))
        display["histogram_bins"] = int((layout or {}).get(
            "histogram_bins", previous.get("histogram_bins", 35)))
        display["histogram_opacity"] = float((layout or {}).get(
            "histogram_opacity", previous.get("histogram_opacity", 0.75)))
        display["theme_mode"] = theme_mode
        result["display"] = display
        title = str((layout or {}).get("title") or "").strip()
        if title:
            result["title"] = title
        result["recipe"] = {**dict(model.get("recipe") or {}),
                            "layout": dict(layout or {}),
                            "typography": dict(typography or {})}
        result["display"]["theme_mode"] = theme_mode
        return validate_chart_model(result)
    if model.get("chart_type") == "scatter_plot":
        result = dict(model)
        previous = model.get("display") or {}
        display = _categorical_display(
            layout, typography, list(previous.get("palette") or _palette(None)), theme_mode=theme_mode)
        display.update({
            "orientation": "vertical",
            "x_title": layout["x_label"] if "x_label" in layout else previous.get("x_title", ""),
            "y_title": layout["y_label"] if "y_label" in layout else previous.get("y_title", ""),
            "show_grid": bool(layout.get("show_grid", previous.get("show_grid", True))),
            "show_border": bool(layout.get("show_border", previous.get("show_border", False))),
            "symbol_size": int((layout or {}).get("marker_size", previous.get("symbol_size", 6))),
            "symbol_opacity": float((layout or {}).get("marker_opacity", previous.get("symbol_opacity", 0.8))),
            "line_width": int((layout or {}).get("line_width", previous.get("line_width", 2))),
            "line_shape": str((layout or {}).get("line_shape", previous.get("line_shape", "linear"))),
            "line_fill": str((layout or {}).get("line_fill", previous.get("line_fill", "none"))),
            "renderer": ("svg" if str((layout or {}).get(
                "render_mode", previous.get("renderer", "canvas"))).lower() == "svg"
                else "canvas"),
            "size_by_value": previous.get("size_by_value", False),
            # Canvas mode is the dense/progressive path; svg is the crisp
            # path for small charts. Live edits have to follow the SAME
            # render_mode the user just picked, not the value the chart was
            # born with -- otherwise the dropdown looks dead on re-render.
            "large": str((layout or {}).get(
                "render_mode", previous.get("renderer", "canvas"))).lower() != "svg",
            "legend": {**display["legend"],
                       "show": bool(layout.get("show_legend", (previous.get("legend") or {}).get("show", False)))},
            "theme_mode": theme_mode,
        })
        title = str((layout or {}).get("title") or "").strip()
        if title:
            result["title"] = title
        result["display"] = display
        result["recipe"] = {**dict(model.get("recipe") or {}),
                            "layout": dict(layout or {}),
                            "typography": dict(typography or {})}
        result["display"]["theme_mode"] = theme_mode
        return validate_chart_model(result)
    result = dict(model)
    previous = dict(model.get("display") or {})
    layout, typography = dict(layout or {}), dict(typography or {})
    result["display"] = {
        **previous,
        "theme_mode": theme_mode,
        "title_size": int(typography.get("header_size", previous.get("title_size", 20))),
        "text_color": str(typography.get("axis_tick_color", previous.get("text_color", "auto"))),
        "show_grid": bool(layout.get("show_grid", previous.get("show_grid", True))),
        "show_border": bool(layout.get("show_border", previous.get("show_border", False))),
    }
    if "x_label" in layout:
        result["display"]["x_title"] = layout["x_label"]
    if "y_label" in layout:
        result["display"]["y_title"] = layout["y_label"]
    title = str(layout.get("title") or "").strip()
    if title:
        result["title"] = title
    result["recipe"] = {**dict(model.get("recipe") or {}),
                        "layout": layout, "typography": typography}
    return validate_chart_model(result)


def build_time_series_chart(dataset: PolarsDataset, config: dict, *,
                            roles: Optional[dict] = None,
                            layout: Optional[dict] = None,
                            typography: Optional[dict] = None) -> Optional[dict]:
    """Build a Polars time series, optionally with a second metric and axis."""
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_time_series_chart requires a PolarsDataset")
    config = dict(config or {})
    schema = dataset.schema
    date_col = config.get("x")
    if isinstance(date_col, (list, tuple)):
        date_col = date_col[0] if date_col else None
    if date_col in (None, "", "None"):
        date_cols = list((roles or {}).get("datetime") or [])
        date_col = next((col for col in date_cols if col in schema), None)
    metric = config.get("y")
    if isinstance(metric, (list, tuple)):
        metric = metric[0] if metric else None
    if metric in (None, "", "None") or metric not in schema or metric == dataset.row_id:
        return None
    if date_col not in schema or date_col == dataset.row_id:
        return None

    dual_col = config.get("dual_y_ts", config.get("dual_y_col"))
    if isinstance(dual_col, (list, tuple)):
        dual_col = dual_col[0] if dual_col else None
    if dual_col in (None, "", "None") or dual_col == metric or dual_col not in schema:
        dual_col = None
    if dual_col is not None:
        primary_config = dict(config)
        primary_config.pop("dual_y_ts", None)
        primary_config.pop("dual_y_col", None)
        secondary_config = dict(primary_config)
        secondary_config["y"] = dual_col
        secondary_config["agg"] = config.get("dual_y_agg") or config.get("agg", "Avg")
        primary = build_time_series_chart(
            dataset, primary_config, roles=roles, layout=layout,
            typography=typography)
        secondary = build_time_series_chart(
            dataset, secondary_config, roles=roles, layout=layout,
            typography=typography)
        if primary is None:
            return secondary
        if secondary is None:
            return primary
        merged = dict(primary)
        merged["data"] = dict(primary["data"])
        primary_series = list(primary["data"].get("series") or [])
        secondary_series = list(secondary["data"].get("series") or [])
        if secondary_series:
            second = dict(secondary_series[0])
            second["axis"] = 1
            merged["data"]["series"] = primary_series + [second]
        merged["title"] = str((layout or {}).get("title") or
                              f"{primary['title']} & {secondary['title']}")
        merged["display"] = dict(primary.get("display") or {})
        merged["display"].update({
            "dual_axis": True,
            "y2_title": (layout or {}).get("y2_label") or dual_col,
        })
        merged["recipe"] = {
            "version": 1, "chart_type": "time_series",
            "config": config, "layout": dict(layout or {}),
            "typography": dict(typography or {}),
        }
        return validate_chart_model(merged)

    operation = _AGGREGATIONS.get(str(config.get("agg", "Avg")).strip().lower(), "mean")
    part_map = {"None": None, "Year": "year", "Quarter": "quarter",
                "Month (number)": "month", "Month Name": "month_name",
                "Weekday Name": "weekday_name", "Day": "day", "Hour": "hour"}
    part = part_map.get(str(config.get("date_part", "None")),
                        config.get("date_part"))
    date_expr = _parse_dates(dataset, date_col)
    metric_expr = _numeric_values_expr(metric, schema)
    base = (dataset.frame.select(pl.col(dataset.row_id), date_expr.alias("__date"),
                                 metric_expr.alias("__metric"))
            .filter(pl.col("__date").is_not_null()))

    if part in (None, "", "None"):
        rows = (base.sort(["__date", dataset.row_id])
                .select("__date", "__metric").collect().iter_rows())
        x_title = date_col
        title = f"Time Series: {metric}"
    else:
        if part == "year":
            keyed = base.with_columns(pl.col("__date").dt.year().alias("__bucket"))
            grouped = keyed.group_by("__bucket").agg(_aggregate_expression(operation, "__metric").alias("__value"))
            result = grouped.sort("__bucket").collect()
            values = [(str(year), value) for year, value in result.iter_rows()]
            x_title = "Year"
        elif part == "quarter":
            keyed = base.with_columns(
                pl.col("__date").dt.year().alias("__year"),
                pl.col("__date").dt.quarter().alias("__period"))
            grouped = keyed.group_by(["__year", "__period"]).agg(
                _aggregate_expression(operation, "__metric").alias("__value"))
            result = grouped.sort(["__year", "__period"]).collect()
            values = [(f"{year} Q{quarter}", value)
                      for year, quarter, value in result.iter_rows()]
            x_title = "Quarter"
        elif part in {"month", "day", "hour"}:
            unit = {"month": "1mo", "day": "1d", "hour": "1h"}[part]
            keyed = base.with_columns(pl.col("__date").dt.truncate(unit).alias("__bucket"))
            grouped = keyed.group_by("__bucket").agg(
                _aggregate_expression(operation, "__metric").alias("__value"))
            result = grouped.sort("__bucket").collect()
            values = [(_json_scalar(bucket), value) for bucket, value in result.iter_rows()]
            x_title = {"month": "Month", "day": date_col, "hour": date_col}[part]
        elif part in {"month_name", "weekday_name"}:
            ordinal = pl.col("__date").dt.month() if part == "month_name" else pl.col("__date").dt.weekday()
            keyed = base.with_columns(ordinal.alias("__bucket"))
            grouped = keyed.group_by("__bucket").agg(
                _aggregate_expression(operation, "__metric").alias("__value"))
            result = grouped.sort("__bucket").collect()
            names = (["January", "February", "March", "April", "May", "June",
                      "July", "August", "September", "October", "November", "December"]
                     if part == "month_name" else
                     ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
            values = [(names[int(index) - 1], value)
                      for index, value in result.iter_rows() if index is not None]
            x_title = "Month" if part == "month_name" else "Weekday"
        else:
            # An unknown saved granularity should behave like raw dates rather
            # than silently grouping on a guessed unit.
            rows = (base.sort(["__date", dataset.row_id])
                    .select("__date", "__metric").collect().iter_rows())
            values = [(_json_scalar(date), value) for date, value in rows]
            x_title = date_col
        if part not in {"year", "quarter", "month", "day", "hour", "month_name", "weekday_name"}:
            title = f"Time Series: {metric}"
        else:
            title = f"{operation.title()} {metric} by {x_title}"
    if part in (None, "", "None") or (isinstance(part, str) and part not in
            {"year", "quarter", "month", "day", "hour", "month_name", "weekday_name"}):
        # Both raw paths hold (datetime, metric) tuples; build the final
        # [x, y] pairs in ONE pass instead of staging an intermediate list.
        values = [[_json_scalar(date), _json_scalar(value)]
                  for date, value in rows if value is not None]
    else:
        values = [[x, _json_scalar(y)] for x, y in values if y is not None]
    if not values:
        return None

    palette = _palette(config.get("palette"))
    display = _categorical_display(layout, typography, palette)
    display.update({"orientation": "vertical", "x_title": (layout or {}).get("x_label") or x_title,
                    "y_title": (layout or {}).get("y_label") or metric,
                    "line_width": int((layout or {}).get("line_width", 2)),
                    "markers": bool((layout or {}).get("show_markers", True)),
                    "marker_size": int((layout or {}).get("marker_size", 6)),
                    "line_shape": str((layout or {}).get("line_shape", "linear")),
                    "smooth": str((layout or {}).get("line_shape", "linear")) == "spline",
                    "line_fill": (layout or {}).get("line_fill", "none"),
                    "legend": {**display["legend"], "show": False}})
    title = str((layout or {}).get("title") or title)
    from modules.chart_model import make_chart_model
    return make_chart_model(
        uid=str(uuid.uuid4()), chart_type="time_series", title=title,
        data={"dimensions": [date_col],
              "series": [{"id": metric, "name": metric, "type": "line",
                          "values": values}]},
        recipe={"version": 1, "chart_type": "time_series", "config": config,
                "layout": dict(layout or {}), "typography": dict(typography or {})},
        display=display,
        source={"engine": "polars", "revision": dataset.revision,
                "row_id": dataset.row_id},
    )


def build_distribution_charts(dataset: PolarsDataset, config: dict, *,
                              roles: Optional[dict] = None,
                              layout: Optional[dict] = None,
                              typography: Optional[dict] = None) -> list[dict]:
    """Build exact Polars histograms for selected metrics, optionally grouped.

    The legacy sampling cap is unnecessary here: Polars computes the bin
    aggregates natively without materializing every source row in Python.
    """
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_distribution_charts requires a PolarsDataset")
    config = dict(config or {})
    schema = dataset.schema
    numeric = list((roles or {}).get("numeric") or [])
    if not numeric:
        numeric = dataset.roles().get("numeric", [])
    raw_metrics = config.get("x_cols", config.get("x"))
    if isinstance(raw_metrics, str):
        raw_metrics = [raw_metrics]
    metrics = [name for name in (raw_metrics or numeric[:6])
               if name in schema and name != dataset.row_id]
    color_col = config.get("color", config.get("color_col"))
    if isinstance(color_col, (list, tuple)):
        color_col = color_col[0] if color_col else None
    if color_col in (None, "", "None") or color_col not in schema or color_col == dataset.row_id:
        color_col = None
    try:
        bins = max(5, min(200, int((layout or {}).get("histogram_bins", 35))))
    except (TypeError, ValueError):
        bins = 35
    try:
        opacity = max(0.1, min(1.0, float((layout or {}).get("histogram_opacity", 0.75))))
    except (TypeError, ValueError):
        opacity = 0.75

    palette = _palette(config.get("palette"))
    models = []
    for metric in metrics:
        values_expr = _numeric_values_expr(metric, schema)
        base = dataset.frame.select(
            values_expr.alias("__value"),
            (pl.col(color_col).cast(pl.String, strict=False)
             .fill_null("(Missing)").alias("__group") if color_col else
             pl.lit("__all__").alias("__group")),
        ).filter(pl.col("__value").is_not_null())
        bounds = base.select(pl.col("__value").min().alias("__min"),
                             pl.col("__value").max().alias("__max")).collect()
        if bounds.height == 0 or bounds.item(0, "__min") is None:
            continue
        minimum = float(bounds.item(0, "__min"))
        maximum = float(bounds.item(0, "__max"))
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            continue
        span = maximum - minimum
        if span == 0:
            bin_expr = pl.lit(0, dtype=pl.Int64)
        else:
            bin_expr = (((pl.col("__value") - minimum) / span * bins)
                        .floor().clip(0, bins - 1).cast(pl.Int64))
        grouped = (base.with_columns(bin_expr.alias("__bin"))
                   .group_by(["__group", "__bin"]).agg(pl.len().alias("__count"))
                   .collect())
        group_names = (sorted(grouped.get_column("__group").unique().to_list())
                       if grouped.height else [])
        if not group_names:
            continue
        counts = {(group, int(bin_index)): int(count)
                  for group, bin_index, count in grouped.iter_rows()}
        width = span / bins if span else 1.0
        labels = []
        for index in range(bins):
            low = minimum + width * index
            high = maximum if index == bins - 1 else minimum + width * (index + 1)
            labels.append(f"{low:,.4g}–{high:,.4g}")
        series = []
        for group in group_names:
            series.append({
                "id": str(group), "name": metric if group == "__all__" else str(group),
                "type": "bar",
                "values": [[labels[index], counts.get((group, index), 0)]
                           for index in range(bins)],
            })
        title = f"Distribution: {metric}" + (f" by {color_col}" if color_col else "")
        display = _categorical_display(layout, typography, palette)
        display.update({"orientation": "vertical",
                        "x_title": (layout or {}).get("x_label") or metric,
                        "y_title": (layout or {}).get("y_label") or "Count",
                        "histogram_bins": bins, "histogram_opacity": opacity,
                        "histogram_overlay": bool(color_col)})
        display["legend"]["show"] = bool(color_col)
        display["labels"]["show"] = False
        from modules.chart_model import make_chart_model
        models.append(make_chart_model(
            uid=str(uuid.uuid4()), chart_type="distribution",
            title=str((layout or {}).get("title") or title),
            data={"dimensions": [metric] + ([color_col] if color_col else []),
                  "series": series},
            recipe={"version": 1, "chart_type": "distribution", "config": config,
                    "layout": dict(layout or {}), "typography": dict(typography or {})},
            display=display,
            source={"engine": "polars", "revision": dataset.revision,
                    "row_id": dataset.row_id},
        ))
    return models


def build_scatter_chart(dataset: PolarsDataset, config: dict, *,
                        roles: Optional[dict] = None,
                        layout: Optional[dict] = None,
                        typography: Optional[dict] = None) -> Optional[dict]:
    """Build a scatter chart and optional fit lines from a bounded Polars sample."""
    if not isinstance(dataset, PolarsDataset):
        raise TypeError("build_scatter_chart requires a PolarsDataset")
    config, schema = dict(config or {}), dataset.schema
    x_col, y_col = config.get("x_col"), config.get("y_col")
    numeric = list((roles or {}).get("numeric") or [])
    if not numeric:
        numeric = dataset.roles().get("numeric", [])
    x_col = x_col if x_col in schema else (numeric[0] if numeric else None)
    y_col = y_col if y_col in schema else (numeric[1] if len(numeric) > 1 else x_col)
    if not x_col or not y_col or x_col == dataset.row_id or y_col == dataset.row_id:
        return None
    color_col = config.get("color_col")
    size_col = config.get("size_col")
    color_col = color_col if color_col in schema and color_col not in (x_col, y_col) else None
    size_col = size_col if size_col in schema and size_col not in (x_col, y_col) else None
    trend = str(config.get("trendline", "None") or "None").strip().lower()

    max_points = 3500
    select = [
        pl.col(dataset.row_id),
        _numeric_values_expr(x_col, schema).alias("__x"),
        _numeric_values_expr(y_col, schema).alias("__y"),
    ]
    if color_col:
        select.append(pl.col(color_col).cast(pl.String, strict=False)
                      .fill_null("(Missing)").alias("__group"))
    else:
        select.append(pl.lit("__all__").alias("__group"))
    if size_col:
        select.append(_numeric_values_expr(size_col, schema).alias("__size"))
    base = (dataset.frame.select(select)
            .filter(pl.col("__x").is_not_null() & pl.col("__y").is_not_null()))
    row_count = int(base.select(pl.len()).collect().item())
    if row_count == 0:
        return None
    sampled = row_count > max_points
    if sampled:
        threshold = int((max_points / row_count) * ((1 << 64) - 1))
        base = (base.with_columns(pl.col(dataset.row_id).hash(seed=17).alias("__sample_hash"))
                .filter(pl.col("__sample_hash") <= threshold)
                .sort("__sample_hash").head(max_points))
    else:
        base = base.sort(dataset.row_id)
    points = base.drop("__sample_hash") if sampled else base
    frame = points.collect()
    if frame.height == 0:
        return None
    raw_sizes = frame.get_column("__size").to_list() if size_col else []
    valid_sizes = [float(value) for value in raw_sizes
                   if value is not None and math.isfinite(float(value))]
    size_min = min(valid_sizes) if valid_sizes else 0.0
    size_max = max(valid_sizes) if valid_sizes else 0.0
    median_size = sorted(valid_sizes)[len(valid_sizes) // 2] if valid_sizes else 0.0
    groups: dict[str, list] = {}
    for row in frame.iter_rows(named=True):
        group = str(row["__group"])
        point = [_json_scalar(row["__x"]), _json_scalar(row["__y"])]
        if size_col and valid_sizes:
            raw_size = row.get("__size")
            raw_size = (float(raw_size) if raw_size is not None
                        and math.isfinite(float(raw_size)) else median_size)
            point.append(4 + ((raw_size - size_min) / (size_max - size_min) * 24
                              if size_max > size_min else 12))
        groups.setdefault(group, []).append(point)
    if not groups:
        return None

    palette = _palette(config.get("palette"))
    series = [{"id": name, "name": y_col if name == "__all__" else name,
               "type": "scatter", "values": values}
              for name, values in sorted(groups.items())]
    if trend not in {"", "none"} and frame.height >= 3:
        # Fit on the same bounded Polars sample used for the visible points.
        # The old runner displayed an OLS line for every non-LOWESS choice;
        # implement the named UI modes explicitly as part of the new model.
        import numpy as np

        x_values = np.asarray(frame.get_column("__x").to_list(), dtype=float)
        y_values = np.asarray(frame.get_column("__y").to_list(), dtype=float)
        trend_x, trend_y = np.asarray([]), np.asarray([])
        trend_name = "Linear trend"
        try:
            if trend == "logarithmic":
                positive = x_values > 0
                fit_x, fit_y = x_values[positive], y_values[positive]
                if len(fit_x) >= 3:
                    coefficients = np.polyfit(np.log(fit_x), fit_y, 1)
                    trend_x = np.sort(fit_x)
                    trend_y = coefficients[0] * np.log(trend_x) + coefficients[1]
                    trend_name = "Logarithmic trend"
            elif trend == "polynomial":
                if len(x_values) >= 3 and np.ptp(x_values) > 0:
                    coefficients = np.polyfit(x_values, y_values, 2)
                    trend_x = np.linspace(float(x_values.min()), float(x_values.max()),
                                          min(250, max(32, len(x_values))))
                    trend_y = np.polyval(coefficients, trend_x)
                    trend_name = "Polynomial trend"
            elif trend == "moving average":
                order = np.argsort(x_values)
                trend_x = x_values[order]
                sorted_y = y_values[order]
                window = max(3, min(51, int(math.ceil(len(sorted_y) * 0.05))))
                trend_y = np.convolve(sorted_y, np.ones(window) / window, mode="same")
                trend_name = "Moving average"
            elif np.ptp(x_values) > 0:
                coefficients = np.polyfit(x_values, y_values, 1)
                trend_x = np.sort(x_values)
                trend_y = np.polyval(coefficients, trend_x)
        except (ValueError, TypeError, FloatingPointError, np.linalg.LinAlgError):
            trend_x, trend_y = np.asarray([]), np.asarray([])
        trend_values = [[float(x), float(y)] for x, y in zip(trend_x, trend_y)
                        if math.isfinite(float(x)) and math.isfinite(float(y))]
        if len(trend_values) >= 2:
            series.append({"id": "trendline", "name": trend_name,
                           "type": "line", "values": trend_values,
                           "style": {"color": "#ef4444", "dash": "dashed"}})
    correlation = None
    if not color_col and frame.height >= 3:
        try:
            correlation = frame.select(pl.corr("__x", "__y")).item()
            if correlation is not None and not math.isfinite(float(correlation)):
                correlation = None
        except Exception:
            correlation = None
    correlation_suffix = (f" · r = {float(correlation):+.3f}"
                          if correlation is not None else "")
    sample_suffix = (f" ({frame.height:,} of {row_count:,} rows)" if sampled else "")
    title = f"Scatter: {x_col} vs {y_col}{correlation_suffix}{sample_suffix}"
    layout, typography = dict(layout or {}), dict(typography or {})
    display = _categorical_display(layout, typography, palette)
    display.update({
        "orientation": "vertical", "x_title": layout.get("x_label") or x_col,
        "y_title": layout.get("y_label") or y_col,
        "legend": {**display["legend"],
                   "show": bool(color_col or trend not in {"", "none"})},
        "symbol_size": int(layout.get("marker_size", 6)),
        "symbol_opacity": float(layout.get("marker_opacity", 0.8)),
        "renderer": ("svg" if str((layout or {}).get("render_mode",
                                      config.get("render_mode", "canvas"))).lower() == "svg" else "canvas"),
        # ECharts-GL is not bundled; canvas progressive rendering is the
        # offline-safe high-volume mode and is exposed as such in the UI.
        "large": str((layout or {}).get("render_mode",
                       config.get("render_mode", "canvas"))).lower() in {"canvas", "webgl"},
        "line_width": int((layout or {}).get("line_width", 2)),
        "line_shape": str((layout or {}).get("line_shape", "linear")),
        "line_fill": str((layout or {}).get("line_fill", "none")),
        "size_by_value": bool(size_col and size_max > size_min),
        "sampled": sampled, "sample_count": frame.height,
    })
    from modules.chart_model import make_chart_model
    return make_chart_model(
        uid=str(uuid.uuid4()), chart_type="scatter_plot",
        title=str(layout.get("title") or title),
        data={"dimensions": [x_col, y_col] +
              ([color_col] if color_col else []) + ([size_col] if size_col else []),
              "series": series},
        recipe={"version": 1, "chart_type": "scatter_plot", "config": config,
                "layout": layout, "typography": typography},
        display=display,
        source={"engine": "polars", "revision": dataset.revision,
                "row_id": dataset.row_id},
    )
