"""modules/chartspec.py -- The declarative contract for one saved chart.

Why this exists
---------------
A chart is persisted as a rendered figure, but a slicer has to re-run it, and
the exported HTML eventually has to re-run it *in the browser*. That means the
chart's meaning has to be expressible as data both sides can read, rather than
as Python objects on one side and a reimplementation of them on the other.

``chart_spec`` is that data. It is deliberately small and deliberately
JSON-only: ``{"version", "chart_type", "config", "columns", "aggregate",
"dimensions", "measures"}``. A JS engine can consume it with no knowledge of
pandas, and it is what stops Python and JavaScript from quietly disagreeing
about what a chart means -- the failure mode this codebase's single-document
design exists to prevent.

It is a *description*, not an executor. ``services.chart_service.
run_chart_recipe`` is what turns a spec back into a figure on this side.

The one thing worth knowing about the split is that a spec records the chart's
AGGREGATION separately from its raw config, because that is the part the
in-browser engine needs and the part most easily lost. A chart explicitly set
to ``Sum`` must not become a ``Mean`` in the browser just because the JS
default differs from the Python one.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# Bumped only for a change an older consumer would misread. A consumer that
# does not recognise the version falls back to the persisted figure rather
# than guessing.
SPEC_VERSION = 1

# Config keys that name a grouping dimension / plotted measure per chart
# family. Kept next to the version constant so adding a chart type means
# touching one obvious place rather than three scattered dicts.
#
# Each entry is cross-checked against ``chart_service.recipe_columns`` in
# tests/test_chartspec.py -- these two must agree, because a slicer decides
# which charts to re-run from one of them and the browser from the other, and
# a disagreement means a chart that silently ignores a filter.
#
# ``date_part`` is deliberately absent: it holds a GRANULARITY ("Month
# (number)"), not a column, and a slicer cannot filter on it.
_DIMENSION_KEYS = ("x", "x_cols", "index_col", "columns_col", "lat_col",
                   "lon_col", "geo_col", "location_col", "color", "x_col",
                   "source_col", "target_col", "path_cols", "date_col")
_MEASURE_KEYS = ("y", "y_cols", "values_col", "value_col", "color_col",
                 "size_col", "y_col")


def _values(config: dict, key: str) -> list:
    """The column names in ``config[key]``, as a flat list.

    Widget specs type most of these as "list", but a bare string is legal
    (a legacy saved config, or a direct API caller) and iterating a string
    would split it into characters -- so both spellings are accepted.
    """
    value = config.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value not in ("", "None") else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v and str(v) not in ("None",)]
    return []


def _aggregate(config: dict) -> Optional[str]:
    """The UI label of the chart's aggregation, e.g. ``"Sum"``.

    Read from ``agg`` (and map_plot's ``agg_func``) WITHOUT mapping it through
    ``analysis_service.AGG_FUNCS``: the browser engine needs the *label* the
    user chose, and converting to "sum"/"mean" here would lose the distinction
    between the UI default and an explicit choice.
    """
    for key in ("agg", "agg_func"):
        value = config.get(key)
        if isinstance(value, str) and value and value != "None":
            return value
    return None


def _collect(config: dict, keys: tuple) -> list:
    """Column names across *keys*, de-duplicated, in first-seen order."""
    out, seen = [], set()
    for key in keys:
        for name in _values(config, key):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def chart_spec(recipe: dict) -> dict:
    """Build the JSON-safe spec for *recipe*.

    *recipe* is a chart's stored ``meta`` plus its ``uid`` and ``chart_type``
    -- the same shape ``chart_service.run_chart_recipe`` accepts. Never raises:
    a report that cannot describe one card should still render the others.
    """
    recipe = dict(recipe or {})
    config = dict(recipe.get("config") or {})

    dimensions = _collect(config, _DIMENSION_KEYS)
    measures = _collect(config, _MEASURE_KEYS)
    return {
        "version":    SPEC_VERSION,
        "uid":        str(recipe.get("uid") or ""),
        "chart_type": str(recipe.get("chart_type") or ""),
        "config":     config,
        "columns":    sorted(set(dimensions) | set(measures)),
        "dimensions": dimensions,
        "measures":   measures,
        "aggregate":  _aggregate(config),
    }


def spec_is_renderable(spec: Any, columns: Optional[set] = None) -> bool:
    """True when a spec can plausibly be re-rendered from *columns*.

    A saved spec whose columns are all absent from the current dataset cannot
    produce a chart, and the caller should show "column missing" rather than
    emitting a card that silently draws nothing.
    """
    if not isinstance(spec, dict) or not spec.get("chart_type"):
        return False
    if columns is None:
        return True
    needed = set(spec.get("columns") or [])
    return bool(needed & set(columns)) if needed else True


def spec_filter_columns(spec: Any) -> set:
    """Columns a spec reads, for intersecting against active slicer filters.

    The mirror image of ``chart_service.recipe_columns``: the same answer,
    reached from the serialised spec so the browser and the canvas agree on
    which charts a given slicer has to touch.
    """
    if not isinstance(spec, dict):
        return set()
    return {str(c) for c in (spec.get("columns") or []) if c}
