"""services/analysis_service.py — Headless analysis API.

Replaces the analysis registry and configuration layer in
``modules/analysis/__init__.py`` with pure-Python functions that accept
a DataFrame and a config dict, and return ``(uid, title, fig)`` tuples.

The ``_WIDGET_SPEC`` schema is extracted here so that the Qt UI can
build its form widgets from the same declarative specification as the
Streamlit UI.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import pandas as pd

from modules.analysis.descriptive import run_descriptive
from modules.analysis.statistical import run_statistical
from modules.analysis.distribution import run_distribution
from modules.analysis.correlation import run_correlation
from modules.analysis.categorical import run_categorical
from modules.analysis.pie_chart import run_pie_chart
from modules.analysis.time_series import run_time_series
from modules.analysis.outlier import run_outlier, OUTLIER_HELP
from modules.analysis.scatter_plot import run_scatter_plot
from modules.analysis.matrix_table import run_matrix_heatmap, run_matrix_table
from modules.analysis.map_plot import run_map_plot
from modules.analysis.data_quality import run_data_quality

from modules.charts import PALETTES, chart_layout


# ── Analysis options (same as ANALYSIS_OPTIONS in modules/analysis/__init__) ──

ANALYSIS_OPTIONS = [
    {"id": "descriptive",  "icon": "🗂️", "name": "Descriptive",      "desc": "Summary statistics"},
    {"id": "statistical",  "icon": "📐", "name": "Statistical",       "desc": "Mean, std, min, max"},
    {"id": "distribution", "icon": "📊", "name": "Distribution",      "desc": "Histograms & boxplots"},
    {"id": "correlation",  "icon": "🔗", "name": "Correlation",       "desc": "Correlation matrix"},
    {"id": "categorical",  "icon": "🏷️", "name": "Categorical Bar",   "desc": "Bar charts"},
    {"id": "pie_chart",    "icon": "🍩", "name": "Pie & Donut",       "desc": "Share of total"},
    {"id": "time_series",  "icon": "⏱️", "name": "Time Series",       "desc": "Trends over time"},
    {"id": "scatter_plot", "icon": "📉",  "name": "Scatter Plot",      "desc": "Variable relationships"},
    {"id": "matrix_heatmap", "icon": "🔲", "name": "Matrix Heatmap",   "desc": "Cross-tab heatmap"},
    {"id": "matrix_table",   "icon": "📋", "name": "Pivot Table",      "desc": "Cross-tab table"},
    {"id": "map_plot",     "icon": "🗺️", "name": "Map Plot",          "desc": "Geographic scatter"},
]

_RUNNERS = {
    "descriptive":  run_descriptive,
    "statistical":  run_statistical,
    "distribution": run_distribution,
    "correlation":  run_correlation,
    "categorical":  run_categorical,
    "pie_chart":    run_pie_chart,
    "time_series":  run_time_series,
    "outlier":      run_outlier,
    "scatter_plot": run_scatter_plot,
    "matrix_heatmap": run_matrix_heatmap,
    "matrix_table":   run_matrix_table,
    "map_plot":     run_map_plot,
    "data_quality": run_data_quality,
}

_NEEDS_AXES = {"statistical", "distribution", "correlation", "categorical",
               "pie_chart", "time_series", "scatter_plot", "matrix_heatmap",
               "matrix_table", "map_plot"}

AGG_FUNCS = {
    "Avg": "mean",
    "Sum":        "sum",
    "Median":     "median",
    "Count":      "count",
    "Min":        "min",
    "Max":        "max",
}

DATE_PARTS = {
    "None":           None,
    "Year":           "Y",
    "Quarter":        "Q",
    "Month (number)": "M",
    "Month Name":     "month_name",
    "Weekday Name":   "weekday_name",
    "Day":            "D",
    "Hour":           "H",
}


# ── Declarative widget spec (same as _WIDGET_SPEC in modules/analysis/__init__) ──

WIDGET_SPEC = {
    "statistical": [
        ("x", "x_cols", "scalar"),
        ("y", "y_cols", "list"),
        ("palette", "palette", "palette"),
    ],
    "distribution": [
        ("x", "x_cols", "list"),
        ("color", "y_cols", "scalar"),
        ("palette", "palette", "palette"),
    ],
    "correlation": [
        ("x", "x_cols", "list"),
        ("palette", "palette", "palette"),
    ],
    "categorical": [
        ("x", "x_cols", "list"),
        ("y", "y_cols", "list"),
        ("agg", "agg", "scalar"),
        ("sort", "sort_by", "scalar_map"),
        ("top_n", "top_n", "number"),
        ("direction", "direction", "scalar"),
        ("dual_y", "dual_y_col", "scalar"),
        ("dual_y_agg", "dual_y_agg", "scalar"),
        ("palette", "palette", "palette"),
    ],
    "pie_chart": [
        ("x", "x_cols", "scalar"),
        ("y", "y_cols", "scalar"),
        ("agg", "agg", "scalar"),
        ("sort", "sort_by", "scalar_map"),
        ("top_n", "top_n", "number"),
        ("palette", "palette", "palette"),
    ],
    "time_series": [
        ("x", "x_cols", "scalar"),
        ("y", "y_cols", "list"),
        ("date_part", "date_part", "scalar"),
        ("agg", "agg", "scalar"),
        ("dual_y_ts", "dual_y_col", "scalar"),
        ("dual_y_agg", "dual_y_agg", "scalar"),
        ("palette", "palette", "palette"),
    ],
    "scatter_plot": [
        ("x_col", "x_col", "scalar"),
        ("y_col", "y_col", "scalar"),
        ("color_col", "color_col", "scalar"),
        ("size_col", "size_col", "scalar"),
        ("trendline", "trendline", "scalar"),
        ("palette", "palette", "palette"),
        ("render_mode", "render_mode", "scalar"),
    ],
    "matrix_heatmap": [
        ("index_col", "index_col", "scalar"),
        ("columns_col", "columns_col", "scalar"),
        ("values_col", "values_col", "scalar"),
        ("agg", "agg", "scalar"),
        ("sort_rows", "sort_rows", "scalar_map"),
        ("top_n_rows", "top_n_rows", "number"),
    ],
    "matrix_table": [
        ("index_col", "index_col", "scalar"),
        ("columns_col", "columns_col", "scalar"),
        ("values_col", "values_col", "scalar"),
        ("agg", "agg", "scalar"),
        ("sort_rows", "sort_rows", "scalar_map"),
        ("top_n_rows", "top_n_rows", "number"),
    ],
    "map_plot": [
        ("map_mode", "map_mode", "scalar"),
        ("lat_col", "lat_col", "scalar"),
        ("lon_col", "lon_col", "scalar"),
        ("location_col", "location_col", "scalar"),
        ("color_col", "color_col", "scalar"),
        ("value_col", "value_col", "scalar"),
        ("agg_func", "agg_func", "scalar"),
        ("map_style", "map_style", "scalar"),
        ("marker_opacity", "marker_opacity", "number"),
        ("invert_colorscale", "invert_colorscale", "bool"),
        ("show_borders", "show_borders", "bool"),
        ("geo_col", "geo_col", "scalar"),
        ("choropleth_colorscale", "choropleth_colorscale", "scalar"),
        ("choropleth_projection", "choropleth_projection", "scalar"),
        ("choropleth_scope", "choropleth_scope", "scalar"),
        ("choropleth_show_borders", "choropleth_show_borders", "bool"),
    ],
}


# ── Pure column-type helpers (no st.session_state dependency) ──────────

def num_cols(df: pd.DataFrame) -> list:
    """Return the list of numeric column names in *df*."""
    return df.select_dtypes(include="number").columns.tolist()


def cat_cols(df: pd.DataFrame) -> list:
    """Return the list of categorical (object) column names in *df*."""
    return df.select_dtypes(include="object").columns.tolist()


def dt_cols(df: pd.DataFrame) -> list:
    """Return the list of datetime column names in *df*."""
    return df.select_dtypes(include="datetime").columns.tolist()


# ── Config dict builder (replaces _collect_kwargs) ─────────────────────

def build_analysis_config(aid: str, df: pd.DataFrame,
                          widget_values: dict[str, Any]) -> dict[str, Any]:
    """Build a keyword-argument dict for a runner from raw widget values.

    Parameters
    ----------
    aid : str
        Analysis type ID (e.g. ``"statistical"``, ``"pie_chart"``).
    df : pd.DataFrame
        The dataset.
    widget_values : dict
        Raw widget values keyed by widget key (as defined in WIDGET_SPEC).
        Missing keys default to sensible defaults.

    Returns
    -------
    dict
        Keyword arguments suitable for passing to ``run_analysis()``.
    """
    num = num_cols(df)
    cat = cat_cols(df)
    dt = dt_cols(df)
    all_cols = df.columns.tolist()
    NONE = "None"

    def g(key: str, default: Any = None) -> Any:
        return widget_values.get(key, default)

    _sort_map = {
        "Value ↓":       "Value (Desc)",
        "Value ↑":       "Value (Asc)",
        "Category A→Z":  "Category (A-Z)",
        "Category Z→A":  "Category (Z-A)",
    }

    pal_label = g("palette", list(PALETTES.keys())[0])
    kwargs: dict[str, Any] = {"palette": PALETTES.get(pal_label, list(PALETTES.values())[0])}

    if aid == "statistical":
        x = _single_choice_value(g("x", NONE), NONE)
        kwargs.update(x_cols=None if x is None else [x], y_cols=g("y", num[:4]) or num)

    elif aid == "distribution":
        color = _single_choice_value(g("color", NONE), NONE)
        kwargs.update(x_cols=g("x", num[:4]) or num[:4], y_cols=None if color is None else [color])

    elif aid == "correlation":
        kwargs.update(x_cols=g("x", num) or num)

    elif aid == "categorical":
        # x/y are declared as multi-select ("list") in WIDGET_SPEC, and
        # run_categorical() genuinely supports multiple dimension columns
        # and multiple metric columns (it loops over both, producing one
        # chart per combination) -- but this branch was collapsing both
        # down to a single scalar via _single_choice_value(), which:
        #   (a) meant the multi-select widgets never actually produced
        #       more than one chart no matter how many columns you picked
        #   (b) crashed outright when the x list-selection was empty
        #       (the normal state before a user manually clicks an item):
        #       _single_choice_value([], cat[:2]) returned the whole
        #       cat[:2] LIST as the "scalar" default, which then got
        #       wrapped in another list ([x]) two lines later, producing
        #       a nested list where a flat list of column names was
        #       expected -- confirmed via direct reproduction, not
        #       guessed: kwargs came out as x_cols=[['region','category']].
        # Fixed to pass the list selections straight through, matching
        # what the widget spec already promises and what the runner
        # already supports.
        x_list = [v for v in (g("x", []) or []) if v not in (None, "", NONE)]
        if not x_list:
            x_list = cat[:2] if cat else []
        y_list_sel = [v for v in (g("y", []) or []) if v not in (None, "", NONE)]

        agg = AGG_FUNCS.get(g("agg", "Avg"), "mean")
        raw_sort = g("sort", "Value ↓")
        sort_by = _sort_map.get(raw_sort, "Value (Desc)")
        top_n_v = int(g("top_n", 0) or 0)
        top_n = top_n_v if top_n_v > 0 else None
        kwargs.update(x_cols=x_list or None,
                      y_cols=y_list_sel or None,
                      agg=agg, sort_by=sort_by, top_n=top_n)
        direction = g("direction", "Vertical (Column chart)")
        raw_dual = g("dual_y", NONE)
        dual_y = None if (not raw_dual or raw_dual == NONE) else raw_dual
        y_list = kwargs.get("y_cols") or []
        if dual_y and y_list and dual_y in y_list:
            dual_y = None
        dual_y_agg = AGG_FUNCS.get(g("dual_y_agg", "Avg"), "mean") if dual_y else None
        kwargs.update(direction=direction, dual_y_col=dual_y, dual_y_agg=dual_y_agg)

    elif aid == "pie_chart":
        x = _single_choice_value(g("x", cat[:2] if cat else "None"), cat[:2] if cat else "None")
        y = _single_choice_value(g("y", NONE), NONE)
        agg = AGG_FUNCS.get(g("agg", "Avg"), "mean")
        raw_sort = g("sort", "Value ↓")
        sort_by = _sort_map.get(raw_sort, "Value (Desc)")
        top_n_v = int(g("top_n", 0) or 0)
        top_n = top_n_v if top_n_v > 0 else None
        kwargs.update(x_cols=None if x in (NONE, None, "") else [x],
                      y_cols=None if y in (NONE, None, "") else [y],
                      agg=agg, sort_by=sort_by, top_n=top_n)

    elif aid == "scatter_plot":
        def _sp_resolve(key):
            v = g(key, NONE)
            return None if v in (NONE, None, "") else v
        kwargs.update(
            x_col=_sp_resolve("x_col"),
            y_col=_sp_resolve("y_col"),
            color_col=_sp_resolve("color_col"),
            size_col=_sp_resolve("size_col"),
            trendline=g("trendline", "None"),
            render_mode=g("render_mode", "svg"),
        )

    elif aid == "matrix_heatmap":
        def _mh_resolve(key):
            v = g(key, NONE)
            return None if v in (NONE, None, "") else v
        _mh_sort_map = {
            "Value ↓": "value_desc", "Value ↑": "value_asc",
            "Category A→Z": "cat_asc", "Category Z→A": "cat_desc",
        }
        _mh_top_n_v = int(g("top_n_rows", 0) or 0)
        kwargs.update(
            index_col=_mh_resolve("index_col"),
            columns_col=_mh_resolve("columns_col"),
            values_col=_mh_resolve("values_col"),
            agg=AGG_FUNCS.get(g("agg", "Avg"), "mean"),
            sort_rows=_mh_sort_map.get(g("sort_rows", "Value ↓"), "value_desc"),
            top_n_rows=_mh_top_n_v if _mh_top_n_v > 0 else None,
        )

    elif aid == "matrix_table":
        def _mt_resolve(key):
            v = g(key, NONE)
            return None if v in (NONE, None, "") else v
        _mt_sort_map = {
            "Value ↓": "value_desc", "Value ↑": "value_asc",
            "Category A→Z": "cat_asc", "Category Z→A": "cat_desc",
        }
        _mt_top_n_v = int(g("top_n_rows", 0) or 0)
        kwargs.update(
            index_col=_mt_resolve("index_col"),
            columns_col=_mt_resolve("columns_col"),
            values_col=_mt_resolve("values_col"),
            agg=AGG_FUNCS.get(g("agg", "Avg"), "mean"),
            sort_rows=_mt_sort_map.get(g("sort_rows", "Value ↓"), "value_desc"),
            top_n_rows=_mt_top_n_v if _mt_top_n_v > 0 else None,
        )

    elif aid == "map_plot":
        def _mp_resolve(key):
            v = g(key, NONE)
            return None if v in (NONE, None, "") else v
        _mp_mode = g("map_mode", "Scatter (Lat/Lon)")
        if _mp_mode == "Choropleth (Location Names)":
            kwargs.update(
                geo_col=_mp_resolve("geo_col"),
                value_col=_mp_resolve("value_col"),
                agg_func=AGG_FUNCS.get(g("agg_func", "Sum"), "sum"),
                invert_colorscale=bool(g("invert_colorscale", False)),
                choropleth_colorscale=g("choropleth_colorscale", "Blues"),
                choropleth_projection=g("choropleth_projection", "natural earth"),
                choropleth_scope=g("choropleth_scope", "world"),
                choropleth_show_borders=bool(g("choropleth_show_borders", True)),
            )
        else:
            kwargs.update(
                lat_col=_mp_resolve("lat_col"),
                lon_col=_mp_resolve("lon_col"),
                location_col=_mp_resolve("location_col"),
                value_col=_mp_resolve("value_col"),
                size_col=_mp_resolve("size_col"),
                color_col=_mp_resolve("color_col"),
                agg_func=AGG_FUNCS.get(g("agg_func", "Avg"), "mean"),
                invert_colorscale=bool(g("invert_colorscale", False)),
                map_style=g("map_style", "carto-positron"),
                marker_opacity=float(g("marker_opacity", 0.82)),
            )

    elif aid == "time_series":
        x = _single_choice_value(g("x", NONE), NONE)
        y = _single_choice_value(g("y", num[0] if num else NONE), num[0] if num else NONE)
        agg = AGG_FUNCS.get(g("agg", "Avg"), "mean")
        date_part = DATE_PARTS.get(g("date_part", "None"))
        raw_dual = g("dual_y_ts", NONE)
        dual_y = None if (not raw_dual or raw_dual == NONE) else raw_dual
        if dual_y and dual_y in ([y] if y else []):
            dual_y = None
        dual_y_agg = AGG_FUNCS.get(g("dual_y_agg", "Avg"), "mean") if dual_y else None
        kwargs.update(x_cols=None if x in (NONE, None, "") else [x], y_cols=[y] if y else [],
                      agg=agg, date_part=date_part, dual_y_col=dual_y, dual_y_agg=dual_y_agg)

    return kwargs


def _single_choice_value(value, default=None):
    """Return a scalar value from a selectbox/multiselect-compatible value.

    Also treats the literal string "None" as absence, not just Python
    None/"" -- this is the sentinel used throughout this file for a
    combo box's placeholder "no selection" item (see the module-level
    NONE constant), and every other call site in build_analysis_config
    already checks against it explicitly. This function was the one
    place that didn't, so a field left at its default "None" combo
    entry silently passed the literal string through as if it were a
    real column name (confirmed: produced titles like "Dist: revenue by
    None" instead of correctly omitting the color grouping).
    """
    if isinstance(value, list):
        value = next((v for v in value if v not in (None, "", "None")), default)
    return None if value in (None, "", "None") else value


# ── Core analysis runner ───────────────────────────────────────────────

def run_analysis(aid: str, df: pd.DataFrame, **kwargs) -> list[tuple[str, str, Any]]:
    """Run an analysis and return a list of ``(uid, title, fig)`` tuples.

    This is the headless equivalent of the ``_run()`` function in
    ``modules/analysis/__init__.py``, but without any Streamlit dependency.

    Parameters
    ----------
    aid : str
        Analysis type ID (e.g. ``"descriptive"``, ``"correlation"``).
    df : pd.DataFrame
        The dataset.
    **kwargs
        Runner-specific keyword arguments (see ``build_analysis_config``).

    Returns
    -------
    list of (uid, title, fig)
        Each tuple contains a unique ID, a display title, and a Plotly figure.
    """
    fn = _RUNNERS.get(aid)
    if not fn:
        return []

    try:
        raw = fn(df) if aid in ("descriptive", "data_quality") else fn(df, **kwargs)

        results = []
        for title, fig in raw:
            uid = str(uuid.uuid4())[:8]
            results.append((uid, title, fig))

        return results

    except Exception as e:
        # Return empty list with error info — caller decides how to surface it
        import logging
        logging.getLogger(__name__).error("Analysis error (%s): %s", aid, e)
        return []


def run_analysis_with_config(aid: str, df: pd.DataFrame,
                              widget_values: dict[str, Any]) -> list[tuple[str, str, Any]]:
    """Convenience: build config from widget values and run analysis in one call."""
    kwargs = build_analysis_config(aid, df, widget_values)
    return run_analysis(aid, df, **kwargs)