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

import numpy as np
import pandas as pd

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
    "run_chart_recipe",
    "recipe_columns",
    "num_cols",
    "cat_cols",
    "dt_cols",
    "dim_cols",
]


# -- Column-type helpers (pure, no session state) -----------------------
#
# Re-exported from analysis_service, which in turn delegates to
# services.column_roles -- the single source of truth for what counts as a
# metric vs a dimension. These used to be independent select_dtypes calls,
# which is how the two services drifted apart and how category/string/bool
# columns ended up in no pool at all.

from services.analysis_service import (  # noqa: E402  (kept next to re-exports)
    num_cols, cat_cols, dt_cols, dim_cols,
)


# -- Persistence downsampling -------------------------------------------------
#
# Charts are stored as rendered figures, so the session row's size is set by how
# many points the chart drew. Measured with the current serialiser, a
# 500k-point line serialises to 12.4 MB -- so a 20-chart dashboard is ~249 MB
# in ONE `sessions.charts_json` row, written on every save and read back whole
# on every load. The Streamlit-era code capped this at 10,000 points per trace
# for exactly this reason; that cap was lost when persistence moved to this
# service layer, and it is restored here.
#
# Only the PERSISTED copy is downsampled. The live figure keeps every point,
# because the on-screen chart is the real one and thinning it would be a lie
# about the data. A session reopened from disk therefore shows the downsampled
# chart -- which is why a chart is always re-derivable from its recipe
# (`run_chart_recipe`) rather than being the only copy of itself.

_MAX_PERSIST_POINTS = 10_000


def _downsample_indices(n: int, max_points: int = _MAX_PERSIST_POINTS) -> list:
    """Indices to keep when reducing *n* items to *max_points*.

    Keeps the first and last so a line's endpoints survive, and samples evenly
    in between. Returns ``range(n)``-equivalent indices unchanged when *n* is
    already small enough.
    """
    if n <= max_points:
        return list(range(n))
    step = (n - 1) / (max_points - 1)
    return sorted({int(round(i * step)) for i in range(max_points)})


def _decode_bdata(value: Any) -> Optional[list]:
    """Decode a Plotly-serialised array into a plain list, or None.

    A deep-copied figure can hold numpy arrays, and ``pio.to_json`` turns them
    into a compact base64 dict. Those have to be decoded BEFORE the length
    check -- ``len({'dtype':..., 'bdata':...})`` is 3, so a 500k-point trace
    would measure as tiny and never be downsampled, which is the exact bug
    this function exists to prevent.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict) and "bdata" in value:
        try:
            import base64
            dtype = np.dtype(str(value.get("dtype", "f8")))
            shape = tuple(int(s) for s in str(value.get("shape", "")).split(",")
                          if s.strip())
            arr = np.frombuffer(base64.b64decode(value["bdata"]), dtype=dtype)
            if shape:
                arr = arr.reshape(shape)
            return arr.tolist()
        except Exception as exc:
            log.debug("_decode_bdata failed: %s", exc)
            return None
    return None


def downsample_fig_for_persist(fig: Any) -> Any:
    """Return a copy of *fig* with large scatter traces thinned for storage.

    Only scatter/scattergl traces in lines/markers mode are touched -- those
    are the ones whose point count tracks the row count. Bar, pie and heatmap
    traces are bounded by their category count and are left exactly as they
    are, so no chart changes meaning.

    Returns the ORIGINAL figure on any error: a slightly large session row is
    a far better outcome than a failed save.
    """
    try:
        import copy
        out = copy.deepcopy(fig)
        for trace in out.data:
            kind = str(getattr(trace, "type", "") or "").lower()
            if kind not in ("scatter", "scattergl"):
                continue
            mode = str(getattr(trace, "mode", "") or "")
            if "lines" not in mode and "markers" not in mode:
                continue

            y = _decode_bdata(getattr(trace, "y", None))
            if not y:
                continue
            n = len(y)
            if n <= _MAX_PERSIST_POINTS:
                continue

            idx = _downsample_indices(n)
            trace.y = [y[i] for i in idx]

            # x/text are thinned with the SAME indices. Doing so independently
            # is how a persisted chart ends up plotting y against the wrong x.
            x = _decode_bdata(getattr(trace, "x", None))
            if x and len(x) == n:
                trace.x = [x[i] for i in idx]
            text = getattr(trace, "text", None)
            if text is not None:
                try:
                    if len(text) == n:
                        trace.text = [text[i] for i in idx]
                except TypeError:
                    pass

            log.info("downsample_fig_for_persist: %s trace %d -> %d points",
                     kind, n, len(idx))
        return out
    except Exception as exc:
        log.warning("downsample_fig_for_persist failed, storing the figure "
                    "as-is: %s", exc)
        return fig


def serialize_chart(uid: str, title: str, fig: Any, *,
                    desc: str = "", chart_type: str = "",
                    meta: Optional[dict] = None) -> dict:
    """Serialize a single chart to a JSON-safe dict.

    The figure is downsampled first (see :func:`downsample_fig_for_persist`)
    so a session row stays a sane size regardless of how many rows the chart
    plotted. This is the ONLY point where that happens: the live figure passed
    in by the caller is never mutated.
    """
    common = {
        "uid":        uid,
        "title":      title,
        "desc":       desc,
        "chart_type": chart_type,
        "meta":       _json_safe(meta or {}),
    }
    if isinstance(fig, dict) and fig.get("schema_version") == 1 \
            and fig.get("kind") == "chart":
        from modules.chart_model import chart_model_to_json
        common["chart_model_json"] = chart_model_to_json(fig)
        return common
    raise TypeError("Chart persistence accepts native ECharts ChartModels only")


def deserialize_chart(item: dict) -> Optional[tuple]:
    """Deserialize a chart dict into ``(uid, title, fig, desc, chart_type, meta)``."""
    try:
        uid        = item.get("uid", "")
        title      = item.get("title", "")
        desc       = item.get("desc", "")
        chart_type = item.get("chart_type", "")
        meta       = item.get("meta", {})
        if item.get("chart_model_json"):
            from modules.chart_model import chart_model_from_json
            fig = chart_model_from_json(item["chart_model_json"])
        else:
            # Read historical Plotly JSON as plain data and convert it at the
            # storage boundary. Plotly is not imported or executed.
            raw = json.loads(item["fig_json"])
            from modules.legacy_plotly_adapter import figure_to_chart_model
            fig = figure_to_chart_model(
                raw, uid=str(uid), chart_type=str(chart_type),
                title=str(title), meta=meta)
            if fig is None:
                log.warning("Skipping unsupported legacy chart %s", uid)
                return None
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


# ---------------------------------------------------------------------------
# Recipe re-execution (the slicer path)
# ---------------------------------------------------------------------------

# Config keys whose values are ENUM/OPTION labels, not column names, even
# though ``WIDGET_SPEC`` types some of them "scalar".
#
# ``WIDGET_SPEC``'s third field is a WIDGET kind (how the form control behaves),
# not a data kind, so "scalar" covers both "pick one column" and "pick one
# option from a combo". There is no way to tell them apart from the spec alone:
# ``("agg", "agg", "scalar")`` and ``("geo_col", "geo_col", "scalar")`` are
# indistinguishable there, yet one holds "Sum" and the other "country".
#
# So the column keys are derived from the spec and then these are subtracted.
# Every entry was read off ``build_analysis_config``, where each of these keys
# is consumed with a literal string default (``g("agg", "Avg")``,
# ``g("direction", "Vertical (Column chart)")``, ...) rather than being checked
# against ``df.columns``. That is the authoritative signal for "this is an
# option", and it is why this list is explicit rather than inferred.
_OPTION_CONFIG_KEYS = frozenset({
    "agg", "agg_func", "dual_y_agg", "hover_val1_agg", "hover_val2_agg",
    "sort", "sort_rows", "direction", "date_part", "trendline",
    "render_mode", "map_mode", "map_style",
    "choropleth_colorscale", "choropleth_projection", "choropleth_scope",
    "choropleth_show_borders", "invert_colorscale", "show_borders",
})


def _column_config_keys(chart_type: str) -> set:
    """Config keys for *chart_type* whose values are COLUMN NAMES.

    Derived from the declarative ``WIDGET_SPEC`` (``(widget_key, runner_param,
    kind)``), restricted to the kinds that can hold a column ("scalar" and
    "list"), minus :data:`_OPTION_CONFIG_KEYS`.

    Anything not declared in the spec is treated as a NON-column and passed
    through untouched. That default is deliberate: deleting a real column and
    letting the runner guess produces a plausible but WRONG chart -- "Sum"
    silently becomes "Mean" -- whereas forwarding an unrecognised option is at
    worst ignored by the runner.
    """
    try:
        from services.analysis_service import WIDGET_SPEC
    except Exception as exc:  # pragma: no cover -- import cycle guard
        log.debug("_column_config_keys: WIDGET_SPEC unavailable: %s", exc)
        return set()

    entries = WIDGET_SPEC.get(chart_type) or []
    return {key for key, _param, kind in entries
            if kind in ("scalar", "list")
            and isinstance(key, str)
            and key not in _OPTION_CONFIG_KEYS}


def recipe_columns(recipe: dict) -> set:
    """The actual DATA columns a saved chart plots.

    ``_column_config_keys`` answers "which config KEYS hold columns", which is
    what the pruner needs. This answers the question the slicer propagation
    needs: "which columns does this chart draw", as a set of NAMES to
    intersect against ``filter_service.active_columns``.

    Only column-bearing keys are read, so enum values like "Sum" and
    "Value ↓" cannot be mistaken for columns -- the same distinction that
    ``_OPTION_CONFIG_KEYS`` exists to enforce.
    """
    if not recipe:
        return set()
    chart_type = str(recipe.get("chart_type") or "")
    config = dict(recipe.get("config") or {})
    out = set()
    for key in _column_config_keys(chart_type):
        value = config.get(key)
        if isinstance(value, str):
            if value and value != "None":
                out.add(str(value))
        elif isinstance(value, (list, tuple)):
            out.update(str(v) for v in value
                       if v and str(v) != "None")
    return out


def run_chart_recipe(recipe: dict, df: pd.DataFrame, *,
                     roles: Optional[dict] = None) -> Optional[tuple]:
    """Re-execute one saved chart against a (possibly filtered) DataFrame.

    This is what a slicer calls when its selection changes. It exists because
    charts are persisted as **rendered figures**, not as live queries: a slicer
    cannot re-aggregate a picture, so the picture has to be rebuilt from the
    recipe it was born from.

    Returns ``(uid, title, fig)`` for the chart the recipe describes, or None
    when it cannot be rebuilt. The uid is the one from the recipe -- see
    :func:`_rebuild_one` for why that matters.

    The recipe is the chart's own ``meta`` dict, i.e. exactly what
    ``analysis_page`` stores on generate::

        {"config": {...}, "layout": {...}, "typography": {...}}

    ``chart_type`` is passed alongside because the analysis page keeps it in a
    separate per-chart bridge key (``chart_type_<uid>``) rather than in meta.

    Degradation follows ``kpi_service.recompute``: a column the new frame does
    not have is dropped and reported through ``stale`` rather than raising, so
    a renamed column reads as "pick a new one" instead of blanking the report.
    """
    if not recipe:
        return None
    chart_type = str(recipe.get("chart_type") or "")
    if not chart_type:
        log.debug("run_chart_recipe: recipe has no chart_type")
        return None
    if chart_type == "descriptive" and recipe.get("engine") == "polars":
        # Native descriptive tables are regenerated from their semantic recipe
        # when a dashboard slicer changes. Keep this model path out of the
        # Plotly runner registry, while retaining the saved canvas uid.
        from services.polars_engine import PolarsDataset
        from services.polars_chart_service import build_descriptive_table_chart
        dataset = PolarsDataset.from_pandas(df, source="slicer-rebuild")
        model = build_descriptive_table_chart(
            dataset, layout=recipe.get("layout"),
            typography=recipe.get("typography"),
        )
        if model is None:
            return None
        model["uid"] = str(recipe.get("uid") or model["uid"])
        return model["uid"], model["title"], model
    if chart_type == "outlier" and recipe.get("engine") == "polars":
        # Outlier cards are semantic scatter models. Re-run the IQR query
        # against the filtered frame so dashboard slicers update both points
        # and fences without entering the legacy Plotly runner/display path.
        from services.polars_engine import PolarsDataset
        from services.polars_chart_service import build_outlier_charts
        model_recipe = dict(recipe.get("chart_model_recipe") or {})
        config = dict(model_recipe.get("config") or recipe.get("config") or {})
        column = config.get("column")
        if not column or column not in df.columns:
            log.info("run_chart_recipe: outlier column is absent from filtered frame")
            return None
        dataset = PolarsDataset.from_pandas(df, source="slicer-rebuild")
        models = build_outlier_charts(
            dataset, [column], multiplier=config.get("multiplier", 1.5),
            typography=recipe.get("typography"),
        )
        if not models:
            return None
        model = models[0]
        model["uid"] = str(recipe.get("uid") or model["uid"])
        return model["uid"], model["title"], model
    if chart_type == "data_quality" and recipe.get("engine") == "polars":
        from services.polars_engine import PolarsDataset
        from services.polars_chart_service import build_data_quality_charts
        model_recipe = dict(recipe.get("chart_model_recipe") or {})
        view = dict(model_recipe.get("config") or {}).get("view")
        dataset = PolarsDataset.from_pandas(df, source="slicer-rebuild")
        models = build_data_quality_charts(dataset)
        model = next((item for item in models
                      if dict((item.get("recipe") or {}).get("config") or {})
                      .get("view") == view), None)
        if model is None:
            return None
        model["uid"] = str(recipe.get("uid") or model["uid"])
        return model["uid"], model["title"], model
    if chart_type == "map_plot":
        from services.polars_engine import PolarsDataset
        from services.polars_chart_service import build_map_plot_chart
        model_recipe = dict(recipe.get("chart_model_recipe") or {})
        config = dict(model_recipe.get("config") or recipe.get("config") or {})
        dataset = PolarsDataset.from_pandas(df, source="slicer-rebuild")
        model = build_map_plot_chart(
            dataset, config, layout=model_recipe.get("layout") or recipe.get("layout"),
            typography=model_recipe.get("typography") or recipe.get("typography"),
        )
        if model is None:
            return None
        model["uid"] = str(recipe.get("uid") or model["uid"])
        return model["uid"], model["title"], model
    return _rebuild_one(recipe, chart_type, df, roles=roles)


def _rebuild_one(recipe: dict, chart_type: str, df: pd.DataFrame, *,
                 roles: Optional[dict] = None) -> Optional[tuple]:
    """Build one semantic model from *recipe*, preserving the chart's uid.

    The uid is the whole point. ``run_analysis`` mints a **fresh random uid**
    for every figure it returns (``analysis_service.run_analysis``), which is
    right for a new chart and wrong for a re-render: a slicer-triggered
    rebuild that changed the uid would orphan the chart's canvas geometry, its
    note and its slicer bindings, and the card would silently vanish from the
    board. So the runner is called directly here rather than through
    ``run_analysis``, and the recipe's own uid is carried through.
    """
    from services.analysis_service import build_analysis_config, run_analysis

    uid = str(recipe.get("uid") or "")
    if not uid:
        log.debug("_rebuild_one: recipe has no uid")
        return None

    if df is None:
        log.debug("run_chart_recipe: no dataset to rebuild against")
        return None

    if getattr(df, "empty", False):
        # A filter that removes every row leaves the chart with nothing to
        # draw. Returning None is the honest answer: the caller shows an
        # empty-state card. Handing back the previous figure instead would
        # leave a chart on screen that no longer matches the slicer above it.
        log.info("run_chart_recipe: %r has no rows under the filter", uid)
        return None

    config = dict(recipe.get("config") or {})
    alive = set(str(c) for c in list(getattr(df, "columns", [])))

    # Which config keys hold COLUMN NAMES, and which hold enum/option values.
    #
    # This distinction is the whole reason this function is not a five-liner.
    # A config mixes both: {"x": ["region"], "agg": "Sum", "sort": "Value ↓",
    # "palette": "\U0001F535 Default Blue-Purple"}. Treating every string value
    # as a column name silently deletes "Sum", "Value ↓" and the palette name,
    # and the runner then falls back to its own default -- so a chart the user
    # explicitly set to Sum re-rendered as a Mean. That is the worst possible
    # failure for a slicer: no error, no warning, just a chart that quietly
    # says something different from the one it replaced.
    #
    # So the column-bearing keys come from the declarative WIDGET_SPEC, and
    # anything not declared there is passed through untouched. Better to hand
    # the runner one option it does not recognise than to delete a real
    # column and leave it guessing.
    column_keys = _column_config_keys(chart_type)

    stale = []

    def _prune(values):
        """Drop column names this frame does not have; return (kept, dropped)."""
        kept, dropped = [], []
        for value in values or []:
            text = str(value)
            if text and text not in alive and text not in ("None",):
                dropped.append(text)
            else:
                kept.append(value)
        return kept, dropped

    for key in column_keys:
        if key not in config:
            continue
        value = config[key]
        if isinstance(value, list):
            kept, dropped = _prune(value)
            config[key] = kept
            stale.extend(dropped)
        elif isinstance(value, str):
            kept, dropped = _prune([value])
            if dropped:
                # Dropped rather than nulled: leaving the key absent lets the
                # runner pick its own default, which is the same degradation
                # ``kpi_service.recompute`` chose for a vanished column.
                config.pop(key, None)
            stale.extend(dropped)

    try:
        runner_config = build_analysis_config(chart_type, df, config, roles=roles)
        raw = run_analysis(
            chart_type, df, roles=roles, **runner_config,
            layout=dict(recipe.get("layout") or {}),
            typography=dict(recipe.get("typography") or {}))
    except Exception as exc:
        log.warning("run_chart_recipe: %s failed for %r: %s",
                    chart_type, uid, exc)
        return None

    if not raw:
        # The honest outcome for a filter that removes every row: the chart has
        # nothing to draw. Callers render an empty-state card rather than
        # keeping the previous figure, which would be a lie.
        log.info("run_chart_recipe: %r produced no figure under the filter", uid)
        return None

    # ``raw`` is [(title, fig), ...]. A multi-select config can yield several;
    # the first is the chart this uid refers to, exactly as
    # ``analysis_page._on_generate_finished`` does when re-generating.
    _new_uid, title, fig = raw[0]
    fig["uid"] = uid
    if recipe.get("layout") is not None or recipe.get("typography") is not None:
        try:
            from services.polars_chart_service import apply_chart_model_display_options
            fig = apply_chart_model_display_options(
                fig, recipe.get("layout") or {}, recipe.get("typography") or {})
        except Exception as exc:
            log.warning("run_chart_recipe: display options failed for %r: %s", uid, exc)

    if stale:
        log.info("run_chart_recipe: %r dropped missing columns: %s",
                 uid, ", ".join(sorted(set(stale))))
    return uid, title, fig
