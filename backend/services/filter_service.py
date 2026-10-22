"""services/filter_service.py -- Pure filter algebra for dashboard slicers.

Why this module exists
----------------------
A slicer is a *filter state*, and a filter state has to be applied in three
places that must never disagree: the live Qt canvas, the regenerated report,
and (later) the in-browser export. So the filter maths lives here, on its own,
with no Qt, no session state and no Plotly, and is written once.

It is deliberately small. It builds **boolean masks**; it never mutates the
DataFrame and never re-runs an analysis. Deciding *which* charts to re-run is
``services.slicer_service``'s job, and re-running them is
``services.chart_service.run_chart_recipe``'s.

Two design rules worth stating, because both were learned the hard way in this
codebase:

1. **Columns are sourced from ``services.column_roles``, never from
   ``modules.charts.cat_cols``.** The latter is ``select_dtypes("object")``,
   which silently omits ``category`` dtype -- and ``perf.optimize_dtypes``
   converts exactly the low-cardinality text columns users filter on. A slicer
   column picker built on it would show an empty list for a perfectly good
   column.

2. **A missing column degrades, it does not raise.** ``kpi_service.recompute``
   established the precedent: a renamed column should read as "pick a new one",
   never as a crash that blanks the report. Every public function here follows
   the same rule and says so in the result rather than blowing up.

Filter kinds
------------
``in``            keep rows whose value is in ``values`` (empty == no-op)
``not_in``        the inverse of ``in``
``range``         inclusive numeric/temporal range, ``low``/``high`` optional
``search``        case-insensitive substring, over the stringified column
``top_n``         keep the N most (or ``least`` fewest) rows by ``column``

Multiple specs combine with AND, which is what a dashboard with two slicers
does. Exclusions (``not_in``) are applied last so they always win -- otherwise
"show NA except Europe" is not expressible.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Kinds this module understands. Anything else is reported as ``unknown``
# rather than raised, so a slicer saved by a newer build degrades to a no-op
# in an older one instead of taking the report down with it.
KINDS = ("in", "not_in", "range", "search", "top_n")


def _normalise_values(values: Any) -> list:
    """Coerce a stored filter's ``values`` into a real list.

    A single-value filter round-trips through JSON as a scalar rather than a
    one-element list, which is the sort of thing that turns "Region = EU" into
    a silent no-op several months later.
    """
    if values is None:
        return []
    if isinstance(values, (list, tuple, set, pd.Index, np.ndarray)):
        return list(values)
    return [values]


def _as_series(df: pd.DataFrame, column: str) -> Optional[pd.Series]:
    """Return *column* as a Series, or None when it is not in *df*.

    ``list(...)`` rather than a truthiness test on the Index: an empty pandas
    Index has no usable ``__bool__``, so ``df[column] or default`` raises on an
    empty frame. ``kpi_service`` hit this exact bug.
    """
    if column is None:
        return None
    if column not in list(df.columns):
        return None
    return df[column]


def _to_datetime_safe(value: Any) -> Any:
    """Parse *value* as a timestamp, returning it unchanged on failure.

    Range filters on a datetime column arrive as ISO strings from JSON, so the
    bound has to be coerced before it can be compared to real timestamps. A
    bound we cannot parse is returned as-is; the comparison then yields all
    False and the filter reports itself as ineffective rather than raising.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return value
    try:
        return pd.to_datetime(value)
    except (ValueError, TypeError) as exc:
        log.debug("filter bound %r is not a date: %s", value, exc)
        return value


def _series_is_datetime_text(s: pd.Series) -> bool:
    """True when *s* is a TEXT column that still parses as datetimes.

    A ``trans_date_trans_time`` column imported from CSV is often ``object``
    dtype (``"2019-04-22 16:02:01"``) rather than ``datetime64`` -- the Dtype
    Transformer has not run yet. ``numeric_bounds`` only recognises true
    ``datetime64``, so such a column reports no range and the Add-Slicer dialog
    never offers the slider the user asked for (the "slicer missing slider for
    continuous & date" report). This samples the column and tries a strict
    datetime parse so a genuinely date-shaped text column is treated as ranged.

    Deliberately conservative: a numeric-looking or arbitrary-text column must
    NOT be classified as temporal, so a plain ``to_datetime`` (which happily
    parses bare integers as epochs) is rejected -- the sample must contain a
    date separator to qualify.
    """
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if s.empty:
        return False
    try:
        sample = s.dropna().astype(str).head(200)
        if sample.empty:
            return False
        # Require a date separator in most samples, so "1234" or "abc" is out.
        sep = sample.str.contains(r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|T\d{2}:", regex=True)
        if sep.mean() < 0.8:
            return False
        parsed = pd.to_datetime(sample, errors="coerce")
        return bool(parsed.notna().mean() >= 0.8)
    except Exception:
        return False


def _numeric_or_datetime_series(s: pd.Series) -> pd.Series:
    """Coerce *s* to something comparable for a range test.

    Datetimes are left alone; everything else goes through ``to_numeric`` with
    errors coerced, so a stray text cell cannot abort the whole filter.
    """
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    return pd.to_numeric(s, errors="coerce")


# ---------------------------------------------------------------------------
# Individual predicates -- one per kind, each returning a mask
# ---------------------------------------------------------------------------

def _mask_in(s: pd.Series, spec: dict, *, negate: bool = False) -> pd.Series:
    """Membership test. An empty selection is a no-op, not "match nothing"."""
    values = _normalise_values(spec.get("values"))
    if not values:
        return pd.Series(True, index=s.index)
    mask = s.isin(values)
    return ~mask if negate else mask


def _mask_range(s: pd.Series, spec: dict) -> pd.Series:
    """Inclusive range test; an absent bound leaves that side unconstrained."""
    low = spec.get("low")
    high = spec.get("high")
    if low in (None, "") and high in (None, ""):
        return pd.Series(True, index=s.index)

    is_dt = pd.api.types.is_datetime64_any_dtype(s)
    if is_dt:
        low, high = _to_datetime_safe(low), _to_datetime_safe(high)
        comparable = s
    elif _series_is_datetime_text(s):
        # Date-as-TEXT column: coerce both the series and the bounds to
        # timestamps so a range slicer over it actually filters (rather than
        # falling through to to_numeric, which would coerce every date string
        # to NaN and keep every row).
        comparable = pd.to_datetime(s, errors="coerce")
        low, high = _to_datetime_safe(low), _to_datetime_safe(high)
    else:
        comparable = _numeric_or_datetime_series(s)

    mask = pd.Series(True, index=s.index)
    if low not in (None, ""):
        # An unparseable bound would make the comparison raise for the whole
        # column, so it is tested per-side and skipped on failure -- the
        # caller then reports the filter as ineffective.
        try:
            mask &= comparable >= low
        except (TypeError, ValueError) as exc:
            log.debug("range low bound %r unusable: %s", low, exc)
            return pd.Series(True, index=s.index)
    if high not in (None, ""):
        try:
            mask &= comparable <= high
        except (TypeError, ValueError) as exc:
            log.debug("range high bound %r unusable: %s", high, exc)
            return pd.Series(True, index=s.index)
    return mask.fillna(False)


def _mask_search(s: pd.Series, spec: dict) -> pd.Series:
    """Case-insensitive substring match over the stringified column.

    Stringification is per-column and unavoidable for a cross-type search box.
    It is deliberately *not* done across the whole frame at once -- that was
    measured at 286 MB of temporary object dtype for a 500k x 12 frame, on
    every keystroke, in the upload-page search path.
    """
    needle = spec.get("value")
    if needle in (None, ""):
        needle = spec.get("values")
        if isinstance(needle, (list, tuple)):
            needle = needle[0] if needle else None
    if needle in (None, ""):
        return pd.Series(True, index=s.index)
    text = s.astype(str)
    return text.str.contains(str(needle), case=False, na=False, regex=False)


def _mask_top_n(df: pd.DataFrame, spec: dict) -> Optional[pd.Series]:
    """Keep the N largest (default) or smallest rows of ``spec['column']``.

    Unlike the other kinds this ranks *rows*, so it needs the frame and not
    just one column. Returns None when the ranking column is absent.
    """
    column = spec.get("column")
    s = _as_series(df, column)
    if s is None:
        return None
    try:
        n = int(spec.get("n", 10) or 10)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return pd.Series(True, index=df.index)

    comparable = _numeric_or_datetime_series(s)
    try:
        order = comparable.sort_values(ascending=bool(spec.get("least", False)),
                                       na_position="last")
        keep = order.index[:n]
    except TypeError as exc:
        log.debug("top_n on %r is not orderable: %s", column, exc)
        return None
    return df.index.isin(keep)

def apply_spec(df: pd.DataFrame, spec: dict) -> pd.Series:
    """Return the boolean mask *spec* selects, or an all-True mask.

    An unusable spec (unknown kind, missing column) is a **no-op**, not an
    error. A slicer pointing at a column the current dataset does not have must
    not empty the whole dashboard; the caller reports it via
    :func:`describe_spec` and shows "column missing" instead.
    """
    if df is None or df.empty or not spec:
        return pd.Series(True, index=getattr(df, "index", None))

    kind = str(spec.get("kind") or "").strip().lower()
    column = spec.get("column")

    if kind == "top_n":
        mask = _mask_top_n(df, spec)
        return mask if mask is not None else pd.Series(True, index=df.index)

    s = _as_series(df, column)
    if s is None:
        return pd.Series(True, index=df.index)

    if kind == "in":
        return _mask_in(s, spec)
    if kind == "not_in":
        return _mask_in(s, spec, negate=True)
    if kind == "range":
        return _mask_range(s, spec)
    if kind == "search":
        return _mask_search(s, spec)

    log.debug("apply_spec: unknown filter kind %r", kind)
    return pd.Series(True, index=df.index)


def apply_specs(df: pd.DataFrame, specs: Optional[Sequence[dict]]) -> pd.DataFrame:
    """Apply every spec (AND) and return the filtered rows.

    Exclusions run after inclusions. With a single left-to-right pass an
    exclusion that precedes its inclusion is computed against rows the
    inclusion was about to drop, and "include NA, exclude Europe" quietly
    keeps Europe back.
    """
    if df is None:
        return df
    # Keep the product-level slicer algebra shared while allowing the staged
    # Polars path to remain lazy. Import locally to avoid making the pandas
    # application require Polars before its dependency migration is complete.
    try:
        from services.polars_engine import PolarsDataset
    except Exception:  # pragma: no cover - optional migration dependency
        PolarsDataset = ()
    if isinstance(df, PolarsDataset):
        return df.apply_filter_specs(specs)
    if df.empty:
        return df
    specs = [s for s in (specs or []) if s]
    if not specs:
        return df

    # Memoise the filtered frame per (frame, spec-signature). The universal
    # whole-report slicer re-aggregates EVERY chart, and a geometry drag or a
    # preview+export pair re-runs the SAME filter set again -- recomputing the
    # boolean masks, the ``df[mask]`` copy and the category prune each time is
    # pure repetition. Bounded + frame-held like the distinct cache below.
    sig = _specs_signature(specs)
    if sig is not None:
        cached = _mask_cache_get(df, sig)
        if cached is not None:
            return cached

    inclusions = [s for s in specs
                  if str(s.get("kind") or "").lower() != "not_in"]
    exclusions = [s for s in specs
                  if str(s.get("kind") or "").lower() == "not_in"]

    mask = pd.Series(True, index=df.index)
    for spec in inclusions:
        mask &= apply_spec(df, spec)
    for spec in exclusions:
        # ``apply_spec`` already negates for a ``not_in`` kind, so the mask it
        # returns is the rows to KEEP. Negating that again -- which this
        # originally did -- inverted the exclusion into an inclusion and
        # "exclude EU, include EU/APAC" returned only the EU rows.
        mask &= apply_spec(df, spec)

    out = df[mask]
    out = _drop_unused_categories(out, df)
    if sig is not None:
        _mask_cache_put(df, sig, out)
    return out


def _drop_unused_categories(out: pd.DataFrame, full: pd.DataFrame) -> pd.DataFrame:
    """Remove categories the filtered rows no longer contain.

    ``df.groupby(col)`` defaults to ``observed=False``, so a ``category``
    column's UNUSED categories still produce a group -- and ``perf.
    optimize_dtypes`` makes exactly the columns a slicer filters on into
    ``category``. Filtering a dataset to one region and re-running a
    categorical chart therefore plotted the other regions as zero-height bars
    rather than dropping them: correct arithmetic, misleading chart.

    The categories are dropped on the RESULT, never on the source frame, and
    only for columns whose category set actually shrank -- so an untouched
    ``category`` column keeps its dtype and the chart's ordering is unaffected
    when nothing was filtered.
    """
    if out is full or out.empty:
        return out
    try:
        categoricals = [
            c for c in out.columns
            if isinstance(out[c].dtype, pd.CategoricalDtype)
        ]
    except Exception as exc:
        log.debug("_drop_unused_categories: dtype scan failed: %s", exc)
        return out

    if not categoricals:
        return out

    out = out.copy()
    for column in categoricals:
        remaining = set(full[column].dropna().unique())
        seen = set(out[column].dropna().unique())
        if len(seen) < len(remaining):
            try:
                out[column] = out[column].cat.remove_categories(
                    [c for c in out[column].cat.categories
                     if c not in remaining or c not in seen])
            except (TypeError, ValueError) as exc:
                log.debug("could not trim categories on %r: %s",
                          column, exc)
    return out


def describe_spec(spec: dict, df: Optional[pd.DataFrame] = None) -> dict:
    """Explain what happened to *spec* -- used to show "column missing".

    Returns ``{ok, kind, column, reason}``. Never raises, so the UI can render
    the result of every slicer unconditionally.
    """
    kind = str((spec or {}).get("kind") or "").strip().lower()
    column = (spec or {}).get("column")
    out = {"ok": True, "kind": kind, "column": column, "reason": ""}

    if not kind:
        out.update(ok=False, reason="no filter kind")
        return out
    if kind not in KINDS:
        out.update(ok=False, reason=f"unknown filter kind '{kind}'")
        return out
    if df is not None and column not in list(df.columns):
        out.update(ok=False, reason=f"column '{column}' is missing")
    return out


def active_columns(specs) -> set:
    """Every column the given specs touch.

    This is the propagation primitive: a chart needs re-running only when one
    of the columns it plots is in here, which is what keeps a slicer
    interaction from re-executing all 13 analysis types on every click.
    """
    out = set()
    for spec in specs or []:
        column = (spec or {}).get("column")
        if column:
            out.add(str(column))
    return out


def distinct_values(df: pd.DataFrame, column: str, *,
                    limit: int = 5000) -> list:
    """Distinct values of *column*, capped at *limit*.

    Ordering is by kind, and deliberately not alphabetical: text columns come
    back most-frequent-first (via ``value_counts``), because that is what a
    slicer should show -- the common values are the ones worth clicking.
    Numeric and datetime columns come back ascending, since their order is
    meaningful. Callers that need a specific order sort at the presentation
    layer; the order here is a UX default, not a contract.

    This is the slicer's hot path and the one place a SQL engine would earn
    its keep -- ``SELECT DISTINCT`` with an index beats a pandas ``unique()``
    over millions of rows. It is isolated in a single function precisely so a
    DuckDB/SQLite adapter can replace it without touching the rest of the
    filter path.

    NaT/NaN are dropped: they are not a selectable value, and every slicer UI
    shows an explicit "missing" affordance instead.

    Memoised per (frame, column, limit) -- see the cache block below. The
    common call pattern is the same question asked again on every report
    rebuild, which is exactly what a cache is for.
    """
    cached = _distinct_cache_get(df, column, limit)
    if cached is not None:
        return cached
    s = _as_series(df, column)
    if s is None:
        return []
    try:
        if pd.api.types.is_bool_dtype(s.dtype):
            values = [True, False]
        elif (pd.api.types.is_numeric_dtype(s.dtype)
              or pd.api.types.is_datetime64_any_dtype(s.dtype)):
            values = s.dropna().sort_values().unique().tolist()
        else:
            values = s.astype(str).value_counts().index.tolist()
    except (TypeError, ValueError) as exc:
        log.debug("distinct_values failed on %r: %s", column, exc)
        return []

    if limit and len(values) > limit:
        log.info("distinct_values: %r has %d values, capping at %d",
                 column, len(values), limit)
        values = values[:limit]
    _distinct_cache_put(df, column, limit, values)
    return values


# -- Distinct-value memo ----------------------------------------------------
# The slicer's hot path, called for every list/dropdown slicer on EVERY
# report rebuild (canvas preview, PNG export, HTML export) and again from the
# slicer and KPI dialogs. ``value_counts`` over a multi-million-row column is
# tens of milliseconds each, so a rebuild of a board with three list slicers
# was hundreds of ms of pure repetition on the UI thread.
#
# One entry per (frame, column): the session holds one live frame, and the
# key includes the column + limit so a dialog asking a different question
# still gets its own answer. The frame is held STRONGLY so ``id()`` cannot be
# recycled onto a different object while the entry is live.
_DISTINCT_CACHE: Dict[tuple, tuple] = {}   # key -> (df, values)


def clear_distinct_cache() -> None:
    """Drop every memoised distinct list (called when the DataFrame changes)."""
    _DISTINCT_CACHE.clear()


def _distinct_cache_key(df: pd.DataFrame, column: str, limit):
    try:
        shape = df.shape
    except Exception:
        return None
    if shape[1] > 10_000:                  # absurd width: don't hold a frame
        return None
    return (id(df), shape, str(column), int(limit or 0))


def _distinct_cache_get(df, column, limit):
    key = _distinct_cache_key(df, column, limit)
    if key is None:
        return None
    hit = _DISTINCT_CACHE.get(key)
    if hit is not None and hit[0] is df:
        # A copy: callers hand this straight to a QListWidget and may sort or
        # filter it in place without poisoning the next reader.
        return list(hit[1])
    return None


def _distinct_cache_put(df, column, limit, values) -> None:
    key = _distinct_cache_key(df, column, limit)
    if key is None:
        return
    if len(_DISTINCT_CACHE) >= 8:          # bounded; the live frame dominates
        _DISTINCT_CACHE.clear()
    _DISTINCT_CACHE[key] = (df, list(values))


# -- Filtered-frame memo -----------------------------------------------------
# The universal whole-report slicer re-aggregates every chart, and the SAME
# filter set is recomputed on every report rebuild (canvas preview, geometry
# drag, PNG export, HTML export). Each recompute redoes the boolean masks, the
# ``df[mask]`` materialisation and the category prune. This memo returns the
# already-filtered frame for an identical (frame, spec-signature) so a board
# with many charts filters its data ONCE, not once per render.
#
# The returned frame is shared and MUST be treated read-only by callers (the
# chart runners only group/aggregate). The frame is held STRONGLY so ``id()``
# cannot be recycled onto a different DataFrame while an entry is live.
_MASK_CACHE: Dict[tuple, tuple] = {}       # key -> (df, filtered_df)


def clear_mask_cache() -> None:
    """Drop every memoised filtered frame (called when the DataFrame changes)."""
    _MASK_CACHE.clear()


def _specs_signature(specs) -> Optional[str]:
    """A stable, order-independent signature for a list of filter specs.

    ``in``/``not_in`` value lists are sorted so the same selection asked in a
    different order hits the same entry; every other field is taken verbatim.
    Returns None when the specs cannot be serialised, which disables the cache
    for that call rather than risking a wrong hit.
    """
    import json

    def _norm(s: dict) -> dict:
        kind = str(s.get("kind") or "").lower()
        out = {"kind": kind, "column": s.get("column")}
        if kind in ("in", "not_in"):
            out["values"] = sorted(str(v) for v in (s.get("values") or []))
        elif kind == "range":
            out["low"] = s.get("low")
            out["high"] = s.get("high")
        elif kind == "search":
            out["value"] = s.get("value")
        elif kind == "top_n":
            out["n"] = s.get("n")
        return out

    try:
        return json.dumps([_norm(s) for s in specs],
                          sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None


def _mask_cache_key(df: pd.DataFrame, sig: str):
    try:
        shape = df.shape
    except Exception:
        return None
    if shape[1] > 10_000:
        return None
    return (id(df), shape, sig)


def _mask_cache_get(df, sig):
    key = _mask_cache_key(df, sig)
    if key is None:
        return None
    hit = _MASK_CACHE.get(key)
    if hit is not None and hit[0] is df:
        return hit[1]
    return None


def _mask_cache_put(df, sig, out) -> None:
    key = _mask_cache_key(df, sig)
    if key is None:
        return
    if len(_MASK_CACHE) >= 8:               # bounded; the live frame dominates
        _MASK_CACHE.clear()
    _MASK_CACHE[key] = (df, out)


def filterable_columns(df: pd.DataFrame, roles: Optional[dict] = None) -> list:
    """Columns a slicer may be built on, dimensions first.

    Sourced from ``services.column_roles`` (never ``modules.charts.cat_cols``,
    which is ``object``-only and misses every ``category`` column), so a
    dataset that has been through ``optimize_dtypes`` offers its real
    dimensions instead of an empty list.
    """
    if df is None:
        return []
    from services.analysis_service import dim_cols, num_cols
    try:
        dims = dim_cols(df, roles=roles)
        nums = num_cols(df, roles=roles)
    except Exception as exc:
        log.warning("filterable_columns: role resolution failed (%s); "
                    "falling back to raw dtypes", exc)
        dims = [c for c in df.columns
                if str(df[c].dtype) in ("object", "string", "category")]
        nums = list(df.select_dtypes(include="number").columns)

    # De-duplicated, order preserved: a range slider is offered on the numeric
    # tail, a list/dropdown on the dimensions above it.
    out, seen = [], set()
    for name in list(dims) + list(nums):
        if name and name not in seen and name in list(df.columns):
            seen.add(name)
            out.append(name)
    return out


def numeric_bounds(df: pd.DataFrame, column: str) -> dict:
    """``{"min", "max", "kind"}`` for a range slider, or ``{}`` if unusable.

    Datetime columns report ISO strings, because the bound travels through the
    slicer's JSON and a ``pd.Timestamp`` does not survive that round-trip.
    """
    s = _as_series(df, column)
    if s is None:
        return {}
    try:
        if pd.api.types.is_datetime64_any_dtype(s.dtype):
            clean = s.dropna()
            if clean.empty:
                return {}
            return {"min": clean.min().isoformat(), "max": clean.max().isoformat(),
                    "kind": "datetime"}
        if not pd.api.types.is_numeric_dtype(s.dtype):
            # A date-as-TEXT column (e.g. a CSV's "2019-04-22 16:02:01") is not
            # numeric, but it still has a real temporal range a slider can
            # travel. Without this the Add-Slicer dialog offered no slider for
            # exactly the columns the user most wanted one on. Parse it and
            # report ISO bounds with kind=datetime so the range filter and the
            # slider bounds agree downstream.
            if _series_is_datetime_text(s):
                parsed = pd.to_datetime(s, errors="coerce").dropna()
                if not parsed.empty:
                    return {"min": parsed.min().isoformat(),
                            "max": parsed.max().isoformat(),
                            "kind": "datetime"}
            return {}
        clean = pd.to_numeric(s, errors="coerce").dropna()
        if clean.empty:
            return {}
        return {"min": float(clean.min()), "max": float(clean.max()),
                "kind": "numeric"}
    except (TypeError, ValueError) as exc:
        log.debug("numeric_bounds failed on %r: %s", column, exc)
        return {}
