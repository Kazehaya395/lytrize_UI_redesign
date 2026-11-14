"""services/filter_service.py -- Pure filter algebra for dashboard slicers (Polars-native).

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
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional, Sequence

import polars as pl

log = logging.getLogger(__name__)

# Kinds this module understands. Anything else is reported as ``unknown``
# rather than raised, so a slicer saved by a newer build degrades to a no-op
# in an older one instead of taking the report down with it.
KINDS = ("in", "not_in", "range", "search", "top_n")

# Date formats tried, in order, when a TEXT column has to be compared as a
# date. The first two cover the CSV shapes this app actually sees ("2019-04-22
# 16:02:01", "2019-04-22"); the rest are the common European variants.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d.%m.%Y",
)


def _normalise_values(values: Any) -> list:
    """Coerce a stored filter's ``values`` into a real list."""
    if values is None:
        return []
    if isinstance(values, (list, tuple, set)):
        return list(values)
    return [values]


def _as_series(df: pl.DataFrame, column: str) -> Optional[pl.Series]:
    """Return *column* as a Series, or None when it is not in *df*."""
    if column is None:
        return None
    if column not in df.columns:
        return None
    return df[column]


def _to_datetime_safe(value):
    """Coerce a value to a datetime for comparison.

    Range filters on a datetime column arrive as ISO strings from JSON, so
    the bound has to be coerced before it can be compared to real timestamps.
    A bound we cannot parse is returned unchanged; the comparison then yields
    no rows and the filter reports itself as ineffective rather than raising.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in _DATE_FORMATS:
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.datetime.fromisoformat(text)
        except ValueError:
            return value
    return value


def _series_is_datetime_text(s: pl.Series) -> bool:
    """True when *s* is a TEXT column that still parses as datetimes.

    A ``trans_date_trans_time`` column imported from CSV is often plain text
    (``"2019-04-22 16:02:01"``) rather than a real datetime -- the Dtype
    Transformer has not run yet. ``numeric_bounds`` only recognises true
    datetime dtypes, so such a column reports no range and the Add-Slicer
    dialog never offers the slider the user asked for. This samples the
    column and tries a strict parse so a genuinely date-shaped text column is
    treated as ranged.

    Deliberately conservative: a numeric-looking or arbitrary-text column must
    NOT be classified as temporal, so a bare-integer parse (which would happily
    read "1234" as an epoch) is rejected -- the sample must contain a date
    separator to qualify.
    """
    if s is None or len(s) == 0:
        return False
    dtype = s.dtype
    if dtype in (pl.Date, pl.Datetime):
        return True
    if dtype != pl.Utf8:
        return False
    try:
        sample = s.drop_nulls().head(200)
        if sample.is_empty():
            return False
        # Require a date separator in most samples, so "1234" or "abc" is out.
        sep = sample.str.contains(
            r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|T\d{2}:", literal=False)
        if float(sep.mean() or 0.0) < 0.8:
            return False
        for fmt in _DATE_FORMATS:
            parsed = sample.str.to_datetime(fmt, strict=False)
            if parsed.null_count() <= len(parsed) * 0.2:
                return True
        return False
    except Exception as exc:
        log.debug("_series_is_datetime_text failed: %s", exc)
        return False


def _datetime_expr(column: str):
    """An expression casting a text *column* to datetime (best effort)."""
    for fmt in _DATE_FORMATS:
        try:
            return pl.col(column).str.to_datetime(fmt, strict=False)
        except Exception:
            continue
    return pl.col(column).cast(pl.Utf8).str.to_datetime(strict=False)


def _specs_signature(specs: Sequence[dict]) -> Optional[str]:
    """A stable fingerprint of a spec list, for the mask cache."""
    try:
        parts = []
        for spec in specs or []:
            if not spec:
                continue
            parts.append(repr(sorted(
                (str(k), repr(v)) for k, v in spec.items())))
        return "|".join(parts)
    except Exception:
        return None


def _mask_in(df: pl.DataFrame, spec: dict, *, negate: bool = False) -> pl.DataFrame:
    """Membership test. An empty selection is a no-op, not "match nothing"."""
    values = _normalise_values(spec.get("values"))
    if not values:
        return df
    column = spec.get("column")
    expr = pl.col(column).is_in(values)
    if negate:
        expr = ~expr
    return df.filter(expr.fill_null(False))


def _mask_range(df: pl.DataFrame, spec: dict) -> pl.DataFrame:
    """Inclusive range test; an absent bound leaves that side unconstrained."""
    low = spec.get("low")
    high = spec.get("high")
    if (low in (None, "")) and (high in (None, "")):
        return df

    column = spec.get("column")
    s = df[column]
    dtype = s.dtype

    if dtype in (pl.Date, pl.Datetime):
        expr = pl.col(column)
        if low not in (None, ""):
            lo = _to_datetime_safe(low)
            if not isinstance(lo, datetime.datetime):
                return df
            expr = expr >= pl.lit(lo)
        if high not in (None, ""):
            hi = _to_datetime_safe(high)
            if not isinstance(hi, datetime.datetime):
                return df
            expr = expr <= pl.lit(hi)
        return df.filter(expr.fill_null(False))

    if dtype == pl.Utf8 and _series_is_datetime_text(s):
        # Date-as-TEXT column: coerce the column AND the bounds to timestamps
        # so a range slicer over it actually filters. Falling through to a
        # numeric cast would coerce every date string to null and keep
        # (or drop) every row.
        col_expr = _datetime_expr(column)
        if low not in (None, ""):
            lo = _to_datetime_safe(low)
            if not isinstance(lo, datetime.datetime):
                return df
            df = df.filter(col_expr.is_not_null() & (col_expr >= pl.lit(lo)))
        if high not in (None, ""):
            hi = _to_datetime_safe(high)
            if not isinstance(hi, datetime.datetime):
                return df
            df = df.filter(col_expr.is_not_null() & (col_expr <= pl.lit(hi)))
        return df

    # Numeric (or coercible-to-numeric) column.
    expr = pl.col(column)
    if dtype != pl.Utf8 and not dtype.is_numeric():
        try:
            expr = expr.cast(pl.Float64, strict=False)
        except Exception:
            return df
    if low not in (None, ""):
        try:
            lo = float(low)
        except (TypeError, ValueError):
            return df
        df = df.filter(expr.is_not_null() & (expr >= pl.lit(lo)))
    if high not in (None, ""):
        try:
            hi = float(high)
        except (TypeError, ValueError):
            return df
        df = df.filter(expr.is_not_null() & (expr <= pl.lit(hi)))
    return df


def _mask_search(df: pl.DataFrame, spec: dict) -> pl.DataFrame:
    """Case-insensitive substring match over the stringified column."""
    needle = spec.get("value")
    if needle in (None, ""):
        needle = spec.get("values")
        if isinstance(needle, (list, tuple)):
            needle = needle[0] if needle else None
    if needle in (None, ""):
        return df
    column = spec.get("column")
    # Case-insensitive substring match, literal (not regex) -- the pandas
    # original used ``case=False, regex=False``.
    needle_l = str(needle).lower()
    return df.filter(
        pl.col(column).cast(pl.Utf8)
        .str.to_lowercase()
        .str.contains(needle_l, literal=True)
        .fill_null(False)
    )

def _mask_top_n(df: pl.DataFrame, spec: dict) -> Optional[pl.DataFrame]:
    """Keep the N largest (default) or smallest rows of ``spec['column']``.

    Unlike the other kinds this ranks *rows*, so it needs the frame and not
    just one column. Returns None when the ranking column is absent.
    """
    column = spec.get("column")
    if column is None or column not in df.columns:
        return None
    try:
        n = int(spec.get("n", 10) or 10)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return df

    s = df[column]
    expr = pl.col(column)
    if not s.dtype.is_numeric() and s.dtype != pl.Utf8:
        expr = expr.cast(pl.Float64, strict=False)
    try:
        ranked = df.sort(expr, descending=not bool(spec.get("least", False)),
                         nulls_last=True)
        return ranked.head(n)
    except Exception as exc:
        log.debug("top_n on %r is not orderable: %s", column, exc)
        return None


def apply_spec(df: pl.DataFrame, spec: dict) -> pl.DataFrame:
    """Apply one spec and return the surviving rows.

    An unusable spec (unknown kind, missing column) is a **no-op**, not an
    error. A slicer pointing at a column the current dataset does not have
    must not empty the whole dashboard; the caller reports it via
    :func:`describe_spec` and shows "column missing" instead.
    """
    if df is None or df.height == 0 or not spec:
        return df

    kind = str(spec.get("kind") or "").strip().lower()
    column = spec.get("column")

    if kind == "top_n":
        out = _mask_top_n(df, spec)
        return df if out is None else out

    if column is None or column not in df.columns:
        return df

    try:
        if kind == "in":
            return _mask_in(df, spec)
        if kind == "not_in":
            return _mask_in(df, spec, negate=True)
        if kind == "range":
            return _mask_range(df, spec)
        if kind == "search":
            return _mask_search(df, spec)
    except Exception as exc:
        log.debug("apply_spec: spec %r failed (%s); skipped", spec, exc)
        return df

    log.debug("apply_spec: unknown filter kind %r", kind)
    return df


def apply_specs(df: pl.DataFrame, specs: Optional[Sequence[dict]]) -> pl.DataFrame:
    """Apply every spec (AND) and return the filtered rows.

    Exclusions run after inclusions. With a single left-to-right pass an
    exclusion that precedes its inclusion is computed against rows the
    inclusion was about to drop, and "include NA, exclude Europe" quietly
    keeps Europe back.

    The result is memoised per (frame, spec-signature): the universal
    whole-report slicer re-aggregates EVERY chart, and a geometry drag or a
    preview+export pair re-runs the SAME filter set again.
    """
    if df is None:
        return df
    if df.height == 0:
        return df

    specs = [s for s in (specs or []) if s]
    if not specs:
        return df

    sig = _specs_signature(specs)
    if sig is not None:
        cached = _mask_cache_get(df, sig)
        if cached is not None:
            return cached

    inclusions = [s for s in specs
                  if str(s.get("kind") or "").strip().lower() != "not_in"]
    exclusions = [s for s in specs
                  if str(s.get("kind") or "").strip().lower() == "not_in"]

    result = df
    for spec in inclusions:
        result = apply_spec(result, spec)
    for spec in exclusions:
        result = apply_spec(result, spec)

    if sig is not None:
        _mask_cache_put(df, sig, result)
    return result


# ── Mask cache for repeated filter applications ────────────────────────────
# The universal whole-report slicer re-aggregates EVERY chart, and a geometry
# drag or a preview+export pair re-runs the SAME filter set again --
# recomputing the masks and the filtered copy each time is pure repetition.
# Bounded + frame-held (STRONGLY, so ``id()`` cannot be recycled onto a
# different object while the entry is live), like the distinct cache below.
_MASK_CACHE: Dict[tuple, tuple] = {}   # key -> (df, filtered)


def _mask_cache_key(df: pl.DataFrame, sig: str) -> Optional[tuple]:
    if sig is None:
        return None
    try:
        shape = df.shape
    except Exception:
        return None
    return (id(df), shape, str(sig))


def _mask_cache_get(df: pl.DataFrame, sig: str) -> Optional[pl.DataFrame]:
    key = _mask_cache_key(df, sig)
    if key is None:
        return None
    hit = _MASK_CACHE.get(key)
    if hit is not None and hit[0] is df:
        return hit[1]
    return None


def _mask_cache_put(df: pl.DataFrame, sig: str, out: pl.DataFrame) -> None:
    key = _mask_cache_key(df, sig)
    if key is None:
        return
    if len(_MASK_CACHE) >= 8:
        _MASK_CACHE.clear()
    _MASK_CACHE[key] = (df, out)


def filterable_columns(df: pl.DataFrame,
                       roles: Optional[dict] = None) -> list:
    """Columns a slicer may be built on, dimensions first.

    Sourced from ``services.column_roles`` (never ``modules.charts.cat_cols``,
    which is text-only and misses every ``Categorical`` column), so a dataset
    that has been through the dtype optimiser offers its real dimensions
    instead of an empty list.

    *roles* may be supplied when the caller already resolved them; otherwise
    the families are derived from the frame itself.
    """
    if df is None:
        return []
    from services.column_roles import (dimension_columns, metric_columns,
                                       resolve_roles)
    try:
        resolved = roles if roles is not None else resolve_roles(df)
        dims = dimension_columns(resolved) if resolved else []
        nums = metric_columns(resolved) if resolved else []
        if not dims and not nums:
            # No role information at all (an empty frame, or a frame whose
            # families resolved to nothing): fall back to raw dtypes so the
            # slicer dialog is never handed an empty column list.
            dims = [c for c in df.columns
                    if str(df[c].dtype) in ("Utf8", "String", "Categorical")]
            nums = [c for c in df.columns if df[c].dtype.is_numeric()]
    except Exception as exc:
        log.warning("filterable_columns: role resolution failed (%s); "
                    "falling back to raw dtypes", exc)
        dims = [c for c in df.columns
                if str(df[c].dtype) in ("Utf8", "String", "Categorical")]
        nums = [c for c in df.columns if df[c].dtype.is_numeric()]

    # De-duplicated, order preserved: a range slider is offered on the numeric
    # tail, a list/dropdown on the dimensions above it.
    out, seen = [], set()
    for name in list(dims) + list(nums):
        if name and name not in seen and name in df.columns:
            seen.add(name)
            out.append(name)
    return out


def numeric_bounds(df: pl.DataFrame, column: str) -> dict:
    """``{"min", "max", "kind"}`` for a range slider, or ``{}`` if unusable.

    Datetime columns report ISO strings, because the bound travels through the
    slicer's JSON and a Python ``datetime`` does not survive that round-trip.
    """
    s = _as_series(df, column)
    if s is None:
        return {}
    try:
        dtype = s.dtype
        if dtype in (pl.Date, pl.Datetime, pl.Time):
            clean = s.drop_nulls()
            if clean.is_empty():
                return {}
            return {"min": str(clean.min()), "max": str(clean.max()),
                    "kind": "datetime"}
        if dtype.is_numeric():
            clean = s.drop_nulls()
            if clean.is_empty():
                return {}
            return {"min": float(clean.min()), "max": float(clean.max()),
                    "kind": "numeric"}
        # Text column that might contain dates.
        if dtype == pl.Utf8:
            for fmt in _DATE_FORMATS:
                try:
                    parsed = pl.col(column).str.to_datetime(fmt, strict=False)
                    clean = df.select(parsed.alias("parsed")).drop_nulls()["parsed"]
                    if not clean.is_empty():
                        return {"min": str(clean.min()), "max": str(clean.max()),
                                "kind": "datetime"}
                except Exception:
                    continue
        return {}
    except Exception as exc:
        log.debug("numeric_bounds failed on %r: %s", column, exc)
        return {}


def describe_spec(spec: dict, df: Optional[pl.DataFrame] = None) -> dict:
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
    if df is not None and column not in df.columns:
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



def distinct_values(df: pl.DataFrame, column: str, *,
                    limit: int = 5000) -> list:
    """Distinct values of *column*, capped at *limit*.

    Ordering is by kind, and deliberately not alphabetical: text columns come
    back most-frequent-first, because that is what a slicer should show --
    the common values are the ones worth clicking. Numeric and datetime
    columns come back ascending, since their order is meaningful. Callers
    that need a specific order sort at the presentation layer; the order here
    is a UX default, not a contract.

    This is the slicer's hot path and the one place a SQL engine would earn
    its keep -- ``SELECT DISTINCT`` with an index beats a full unique() scan
    over millions of rows. It is isolated in a single function precisely so a
    DuckDB/SQLite adapter can replace it without touching the rest of the
    filter path.

    Nulls are dropped: they are not a selectable value, and every slicer UI
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
        if s.dtype == pl.Boolean:
            values = [True, False]
        elif s.dtype.is_numeric():
            values = s.drop_nulls().unique().sort().to_list()
        elif s.dtype in (pl.Date, pl.Datetime):
            values = s.drop_nulls().unique().sort().to_list()
        elif s.dtype == pl.Utf8:
            # Most-frequent-first: the common values are the ones worth
            # clicking. ``value_counts`` is a single hash aggregate.
            counts = s.drop_nulls().value_counts(sort=True)
            values = counts.get_column(s.name).to_list()
        else:
            values = s.drop_nulls().unique().to_list()
    except Exception as exc:
        log.debug("distinct_values failed on %r: %s", column, exc)
        return []

    if limit and len(values) > limit:
        log.info("distinct_values: %r has %d values, capping at %d",
                 column, len(values), limit)
        values = values[:limit]
    _distinct_cache_put(df, column, limit, values)
    return list(values)


# -- Distinct-value memo ----------------------------------------------------
# The slicer's hot path, called for every list/dropdown slicer on EVERY
# report rebuild (canvas preview, PNG export, HTML export) and again from the
# slicer and KPI dialogs. A value_counts over a multi-million-row column is
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


def _distinct_cache_key(df: pl.DataFrame, column: str, limit):
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


def clear_mask_cache() -> None:
    """Drop every memoised filtered frame (called when the DataFrame changes)."""
    _MASK_CACHE.clear()
