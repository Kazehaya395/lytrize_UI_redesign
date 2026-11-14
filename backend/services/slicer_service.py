"""services/slicer_service.py -- Slicer definitions, state and propagation.

A slicer is a small, self-describing object::

    {"uid": "s_1a2b3c4d", "column": "region", "kind": "in",
     "widget": "list", "scope": "report", "target": "",
     "label": "Region", "spec": {...}}

Scope
-----
``report``   UNIVERSAL -- once any report-scoped slicer is active, every chart
             is re-run against the filtered rows (Power BI's model-filter
             behaviour), even charts that never plot the slicer's column.
``chart``    only ``target`` (one chart's uid) is filtered

That single field is the whole of the feature request's points 1 and 3 --
per-chart and whole-report slicers -- so there are two behaviours, not two
code paths.

Propagation
-----------
:func:`affected_charts` answers WHICH charts to re-run. A report-scoped slicer
is universal, so it returns every chart; a chart-scoped slicer returns only
its target. Because report slicers filter the whole row set, each chart is
rebuilt against only the slicers that actually constrain it --
:func:`slicers_for_chart` / :func:`filter_frames_by_chart` -- so a
chart-scoped slicer never leaks its filter into a chart it does not target.

Nothing here imports Qt or Plotly, so it is testable without a display.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any, Optional

import polars as pl

from services.filter_service import apply_specs, describe_spec

log = logging.getLogger(__name__)


def _to_datetime(value: Any):
    """Parse *value* as a timestamp, or return None when it is not one.

    Datetime slider bounds arrive as ISO strings, so the two bounds have to be
    parsed before they can be compared. A bound that cannot be parsed yields
    None, which makes the bounds degenerate -- an unmovable handle is not a
    control, so the caller drops the slider option rather than offering one
    that cannot work.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value
    try:
        parsed = pl.Series([str(value)]).str.to_datetime(strict=False)
    except Exception as exc:
        log.debug("could not parse %r as a timestamp: %s", value, exc)
        return None
    if parsed.null_count():
        return None
    return parsed.item()

# How a slicer presents its values. Each maps to a filter kind in
# filter_service, so the widget is a RENDERING choice, not a semantic one: a
# "list" and a "dropdown" over a text column select identical rows and differ
# only in the affordance.
WIDGET_KINDS = {
    "list":     "in",        # checkbox list
    "dropdown": "in",        # single/multi select combo
    "slider":   "range",     # continuous, numeric/date
    "search":   "search",    # free text
    "top_n":    "top_n",     # N largest/smallest
}

# Which widget kinds make sense per column kind. A slider over text has no
# bounds and a checkbox list over a continuous measure is unusable, so the
# dialog offers only the workable ones instead of letting the user build a
# slicer that cannot function.
_WIDGETS_FOR_DIMENSION = ("list", "dropdown", "search")
_WIDGETS_FOR_MEASURE = ("list", "dropdown", "slider", "search", "top_n")


def new_uid() -> str:
    """A short, collision-resistant slicer id.

    Prefixed ``s_`` so it can never be mistaken for a chart uid (a bare 8-char
    hex) and cannot collide with the reserved ``__title__`` key.
    """
    return f"s_{uuid.uuid4().hex[:8]}"


def filter_kind(widget_kind: str) -> Optional[str]:
    """The ``filter_service`` kind a widget kind selects with."""
    return WIDGET_KINDS.get(str(widget_kind or "").strip().lower())


def float_or_none(value: Any) -> Optional[float]:
    """*value* as a float, or None when it is not numeric.

    Datetime bounds arrive as ISO strings rather than numbers, so this cannot
    be the whole comparison -- see :func:`bounds_are_degenerate`, which falls
    back to parsing those as timestamps.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bounds_are_degenerate(bounds: dict) -> bool:
    """True when a slider over these bounds could not be moved.

    A slider needs a RANGE. A numeric column whose min equals its max (a
    constant measure) has none, and neither does a datetime column holding a
    single day -- a handle that cannot travel is not a control, so the caller
    drops the "slider" option rather than offering something inert.

    The two kinds are compared differently on purpose. Datetime bounds arrive
    as ISO strings, so a numeric comparison would find neither bound
    convertible and treat every date column as degenerate.
    """
    if not isinstance(bounds, dict):
        return True
    low = bounds.get("min")
    high = bounds.get("max")
    if low is None or high is None:
        return True

    if bounds.get("kind") == "datetime":
        try:
            same = _to_datetime(low) == _to_datetime(high)
        except (ValueError, TypeError):
            return True
        return bool(same)

    low_f = float_or_none(low)
    high_f = float_or_none(high)
    if low_f is None or high_f is None:
        return True
    return low_f == high_f


def available_widgets(df, column: str, roles: Optional[dict] = None) -> list:
    """Widget kinds that make sense for *column*.

    Uses ``services.column_roles`` rather than a raw dtype test so a
    ``category`` column -- what ``optimize_dtypes`` produces for
    low-cardinality text -- is correctly treated as a dimension. A picker
    built on ``select_dtypes("object")`` would offer no widget kinds at all
    for exactly the columns users most want to filter on.
    """
    if df is None or column not in list(df.columns):
        return []
    try:
        # num_cols / dim_cols are the SEPARATE pools, not filterable_columns:
        # the latter deliberately returns dimensions *and* measures in one
        # list (a slicer may be built on either), so asking it "is this a
        # dimension?" is always True and every measure loses its slider.
        from services.analysis_service import dim_cols, num_cols
        measures = set(num_cols(df, roles=roles))
        dims = set(dim_cols(df, roles=roles))
    except Exception as exc:
        log.warning("available_widgets: role resolution failed: %s", exc)
        return list(_WIDGETS_FOR_DIMENSION)

    # A DATETIME is a dimension as far as column_roles is concerned (it groups
    # like any other dimension) but it is also ranged, so it gets the measure
    # widgets AND the slider. Decided on the dtype rather than on pool
    # membership, because "is this a dimension?" is the wrong question here.
    #
    # ``_series_is_datetime_text`` extends that to a date stored as TEXT (a
    # CSV's "2019-04-22 16:02:01" before the Dtype Transformer runs): it is a
    # dimension by dtype but has a real temporal range, so it must also offer
    # the slider -- the "slicer missing slider for continuous & date" report.
    from services.filter_service import numeric_bounds, _series_is_datetime_text
    series = df[column]
    is_datetime = (series.dtype in (pl.Date, pl.Datetime, pl.Time)
                   or _series_is_datetime_text(series))
    is_measure = str(column) in measures or is_datetime

    kinds = list(_WIDGETS_FOR_MEASURE if is_measure
                 else _WIDGETS_FOR_DIMENSION)
    if "slider" in kinds:
        # A slider needs a RANGE, not just bounds: a constant column reports
        # min == max, and a handle that cannot move is not a control.
        if bounds_are_degenerate(numeric_bounds(df, column)):
            kinds.remove("slider")
    return kinds


def make_slicer(column: str, *, widget_kind: str = "list",
                scope: str = "report", target: str = "",
                label: str = "", values: Optional[list] = None,
                low: Any = None, high: Any = None,
                n: int = 10, least: bool = False) -> dict:
    """Build one slicer definition.

    ``kind`` is stored alongside ``widget`` because the filter algebra needs
    the former and the canvas draws the latter; deriving one from the other
    every time is how the two drift.
    """
    kind = filter_kind(widget_kind)
    if kind is None:
        raise ValueError(f"unknown slicer widget kind: {widget_kind!r}")

    scope = "chart" if str(scope).lower() == "chart" else "report"
    if scope == "chart" and not target:
        raise ValueError("a chart-scoped slicer needs a target chart uid")

    spec: dict = {"kind": kind, "column": column}
    if kind in ("in", "not_in"):
        spec["values"] = list(values or [])
    elif kind == "range":
        spec["low"] = low
        spec["high"] = high
    elif kind == "search":
        spec["value"] = values[0] if values else None
    elif kind == "top_n":
        spec["n"] = int(n or 10)
        spec["least"] = bool(least)

    return {
        "uid":    new_uid(),
        "column": column,
        "kind":   kind,
        "widget": str(widget_kind).strip().lower(),
        "scope":  scope,
        "target": str(target or ""),
        "label":  label or str(column),
        "spec":   spec,
    }


def slicer_spec(slicer: dict) -> dict:
    """The ``filter_service`` spec for *slicer*.

    Tolerates a definition predating the ``spec`` field by rebuilding one from
    the flat fields, so a session saved by an earlier build still filters
    instead of silently doing nothing.
    """
    if not isinstance(slicer, dict):
        return {}
    spec = slicer.get("spec")
    if isinstance(spec, dict) and spec:
        return dict(spec)

    kind = str(slicer.get("kind") or filter_kind(slicer.get("widget")) or "in")
    rebuilt = {"kind": kind, "column": slicer.get("column")}
    if kind in ("in", "not_in"):
        rebuilt["values"] = list(slicer.get("values") or [])
    elif kind == "range":
        rebuilt["low"] = slicer.get("low")
        rebuilt["high"] = slicer.get("high")
    elif kind == "search":
        rebuilt["value"] = slicer.get("value") or slicer.get("label")
    elif kind == "top_n":
        rebuilt["n"] = int(slicer.get("n") or 10)
        rebuilt["least"] = bool(slicer.get("least"))
    return rebuilt


def slicer_is_active(slicer: dict) -> bool:
    """True when the slicer currently constrains anything.

    An empty selection is deliberately INACTIVE rather than "match nothing":
    a freshly added slicer has nothing ticked, and treating that as a filter
    matching no rows would blank the dashboard the moment it is created.
    """
    spec = slicer_spec(slicer)
    kind = str(spec.get("kind") or "")
    if kind in ("in", "not_in"):
        return bool(spec.get("values"))
    if kind == "range":
        return spec.get("low") not in (None, "") or \
            spec.get("high") not in (None, "")
    if kind == "search":
        return bool(spec.get("value"))
    if kind == "top_n":
        try:
            return int(spec.get("n") or 0) > 0
        except (TypeError, ValueError):
            return False
    return False


def active_slicers(slicers: Optional[list]) -> list:
    """Only the slicers that currently constrain something."""
    return [s for s in (slicers or []) if isinstance(s, dict)
            and slicer_is_active(s)]


def affected_charts(recipes: dict, slicers: Optional[list],
                    report_only: bool = False) -> list:
    """Chart uids a set of slicers requires to be re-run.

    *recipes* maps ``{uid: recipe}``. Scope decides reach, and the two are
    mutually exclusive:

    * A ``report``-scoped slicer is UNIVERSAL -- once one is active, every
      chart is re-run against the filtered rows, even charts that never plot
      the slicer's column. This is Power BI's model-filter behaviour: a
      ``store_location`` selection must also narrow a chart of Mean qty by
      product_category, because the rows behind it changed.
    * A ``chart``-scoped slicer reaches only its ``target``.

    ``report_only`` restricts the answer to report-scoped slicers; the
    dashboard's left-panel controls use it to decide what *they* changed.
    """
    recipes = recipes or {}
    slicers = active_slicers(slicers)
    if not slicers:
        return []

    universal = False
    targets: set = set()
    for slicer in slicers:
        scope = str(slicer.get("scope") or "report").lower()
        if report_only and scope != "report":
            continue
        if scope == "report":
            universal = True
            # No column test any more: a report slicer constrains the whole
            # row set, so it reaches every chart regardless of what it plots.
            continue
        # chart scope: the target IS the whole of its reach. A malformed
        # definition (no target) adds nothing -- "some chart, unspecified" is
        # not a filter anyone can reason about.
        if slicer.get("target"):
            targets.add(str(slicer["target"]))

    out = []
    for uid, recipe in recipes.items():
        if not isinstance(recipe, dict):
            continue
        if universal or uid in targets:
            out.append(uid)
    return out


def slicers_for_chart(slicers: Optional[list], uid: str) -> list:
    """The active slicers that constrain one chart.

    Report-scope slicers apply to every chart; a chart-scope slicer applies
    only to the chart named by its ``target``. This is what keeps a
    chart-scoped slicer from leaking its filter into a chart it does not
    target -- now that report slicers re-run the whole board, applying every
    active spec to one shared frame would wrongly constrain untargeted charts.
    """
    out = []
    for s in active_slicers(slicers):
        scope = str(s.get("scope") or "report").lower()
        if scope == "report" or str(s.get("target") or "") == str(uid):
            out.append(s)
    return out


def filter_frames_by_chart(df, slicers: Optional[list], uids) -> dict:
    """``{uid: filtered_df}`` computing each distinct filter set only once.

    Charts whose applicable slicer set is identical share a single filtered
    frame, so the common board (report slicers only, no chart slicers) filters
    the data one time no matter how many charts it has -- the property that
    keeps universal report slicers from multiplying the filtering cost.
    """
    cache: dict = {}
    out: dict = {}
    for uid in uids:
        applicable = slicers_for_chart(slicers, uid)
        key = tuple(sorted(str(s.get("uid") or "") for s in applicable))
        if key not in cache:
            cache[key] = filter_frame(df, applicable)
        out[uid] = cache[key]
    return out


def filter_frame(df, slicers: Optional[list]):
    """Apply every active slicer to *df*.

    The single place the canvas, the report rebuild and (later) the export all
    ask "which rows are under the current selection", so they cannot disagree.
    """
    active = active_slicers(slicers)
    if not active:
        return df
    return apply_specs(df, [slicer_spec(s) for s in active])


def slicer_status(slicers: Optional[list], df=None) -> list:
    """Per-slicer health, for the UI to render warnings.

    A slicer whose column is missing from the current dataset is reported, not
    raised -- the degradation ``kpi_service.recompute`` established.
    """
    out = []
    for slicer in (slicers or []):
        if not isinstance(slicer, dict):
            continue
        info = describe_spec(slicer_spec(slicer), df)
        out.append({
            "uid":    slicer.get("uid", ""),
            "label":  slicer.get("label") or slicer.get("column", ""),
            "column": slicer.get("column", ""),
            "active": slicer_is_active(slicer),
            "ok":     bool(info.get("ok")),
            "reason": info.get("reason", ""),
        })
    return out


def apply_selection(slicer: dict, role: str, raw: Any) -> dict:
    """Return *slicer* with its selection replaced by *raw*.

    *role* is the ``data-ly-role`` of the control that fired (``values``,
    ``value``, ``low``, ``high``, ``n``); *raw* is whatever shape that
    control produces -- a list for a multi-select, a scalar for a
    checkbox/search/number input.

    Pure: returns a NEW dict, never mutates the argument, never touches a
    DataFrame. The dashboard page stores the result and re-renders; the
    exported file never calls this (it has no bridge).

    Unknown roles and unparseable numbers leave the spec untouched rather
    than zeroing it: a dropped keystroke must not silently clear a filter.
    """
    if not isinstance(slicer, dict):
        return slicer
    out = dict(slicer)
    spec = dict(slicer_spec(slicer))
    kind = str(spec.get("kind") or "")
    role = str(role or "").strip().lower()

    if role == "values" or (role == "value" and kind in ("in", "not_in")):
        picked = raw if isinstance(raw, list) else [raw]
        spec["values"] = [v for v in (str(p).strip() if p is not None else ""
                                      for p in picked) if v]
    elif role == "value" and kind == "search":
        text = str(raw or "").strip()
        spec["value"] = text or None
    elif role in ("low", "high") and kind == "range":
        number = _selection_number(raw)
        if number is None and str(raw or "").strip() not in ("", "None"):
            return slicer  # unparseable: keep the old bound, not a guess
        spec[role] = number
    elif role == "n" and kind == "top_n":
        try:
            number = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            return slicer
        spec["n"] = max(1, number)
    elif role == "mode" and kind == "top_n":
        spec["least"] = str(raw or "").strip().lower().startswith("small")
    else:
        return slicer

    out["spec"] = spec
    return out


def _selection_number(raw: Any) -> Optional[float]:
    """*raw* as a float, or None for blank/unparseable input."""
    text = str(raw).strip() if raw is not None else ""
    if text in ("", "None"):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def normalise_all(slicers: Optional[list]) -> list:
    """Validate a whole list, dropping unusable entries.

    A definition with no column is dropped rather than rendered as a slicer
    that can never filter anything -- an inert card on the board is worse
    than no card, because the user cannot tell it from a broken one.
    """
    out = []
    for slicer in (slicers or []):
        if not isinstance(slicer, dict):
            continue
        if not slicer.get("column"):
            log.debug("normalise_all: dropping slicer with no column")
            continue
        if not slicer.get("uid"):
            slicer = dict(slicer, uid=new_uid())
        if not slicer.get("kind"):
            slicer = dict(slicer,
                          kind=filter_kind(slicer.get("widget")) or "in")
        if not slicer.get("label"):
            slicer = dict(slicer, label=str(slicer["column"]))
        out.append(slicer)
    return out
