"""services/kpi_service.py -- Headless KPI card computation (Polars-native).

The dashboard's KPI cards compute aggregations over the dataset. Every result
carries a ``_recipe`` -- the exact parameters used -- so a card can be
recomputed against a freshly re-uploaded dataset without the caller having
to remember which drop-downs the user had touched.

Nothing here touches Qt, Streamlit or session state, so the desktop page
and the export path can both use it.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

import polars as pl

log = logging.getLogger(__name__)

# =========================================================================
# The type catalogue
# =========================================================================
# Each entry is (key, label, icon, fields). ``fields`` drives the form the
# dialog builds, so the type list, the form and the calculation can never
# disagree.

KPI_SPECS: list[dict[str, Any]] = [
    {"key": "sum", "label": "Total (Sum)", "icon": "\U0001F4DC", "fields": ["col"]},
    {"key": "mean", "label": "Average (Mean)", "icon": "\U0001F4CA", "fields": ["col"]},
    {"key": "median", "label": "Median", "icon": "\U0001F4D0", "fields": ["col"]},
    {"key": "count", "label": "Count (Rows)", "icon": "\U0001F522", "fields": []},
    {"key": "count_nonnull", "label": "Count (non-empty values)", "icon": "\u2705", "fields": ["col"]},
    {"key": "min", "label": "Minimum Value", "icon": "\u2B07\uFE0F", "fields": ["col"]},
    {"key": "max", "label": "Maximum Value", "icon": "\u2B06\uFE0F", "fields": ["col"]},
    {"key": "std", "label": "Std Deviation", "icon": "\U0001F4CF", "fields": ["col"]},
    {"key": "nunique", "label": "Unique Values Count", "icon": "\U0001F50D", "fields": ["col_any"]},
    {"key": "missing", "label": "Missing Values Count", "icon": "\u26A0\uFE0F", "fields": ["col_any"]},
    {"key": "date_range", "label": "Date Range", "icon": "\U0001F4C5", "fields": ["col_date"]},
    {"key": "share", "label": "% of Total (category share)", "icon": "\U0001F4C8",
     "fields": ["col", "group_col", "filter_val"]},
    {"key": "top_cat", "label": "Top Category \u2192 Value", "icon": "\U0001F3C6",
     "fields": ["group_col", "metric_col"]},
    {"key": "bottom_cat", "label": "Bottom Category \u2192 Value", "icon": "\U0001F4C9",
     "fields": ["group_col", "metric_col"]},
    {"key": "mom", "label": "% Change (Latest Month vs Prev Month)", "icon": "\U0001F4C5",
     "fields": ["filter_col", "col"]},
    {"key": "yoy", "label": "% Change (Latest Year vs Prev Year)", "icon": "\U0001F4C5",
     "fields": ["filter_col", "col"]},
    {"key": "mom_given", "label": "% Change (vs a chosen month)", "icon": "\U0001F4C8",
     "fields": ["filter_col", "col", "baseline_period"]},
    {"key": "running", "label": "Running Total (up to latest date)", "icon": "\U0001F501",
     "fields": ["col", "filter_col"]},
    {"key": "vs_mean", "label": "% vs Column Average", "icon": "\u2696\uFE0F",
     "fields": ["col", "group_col", "filter_val"]},
    {"key": "custom", "label": "Custom Expression", "icon": "\u270F\uFE0F",
     "fields": ["formula"]},
]

KPI_TYPES: list[str] = [k["label"] for k in KPI_SPECS]
KPI_ICONS: dict[str, str] = {k["label"]: k["icon"] for k in KPI_SPECS}
_SPEC_BY_LABEL: dict[str, dict] = {k["label"]: k for k in KPI_SPECS}
_SPEC_BY_KEY: dict[str, dict] = {k["key"]: k for k in KPI_SPECS}


def kpi_spec(kpi_type: str) -> dict:
    """The spec for a KPI type, by label or by key. Never None."""
    if kpi_type in _SPEC_BY_LABEL:
        return _SPEC_BY_LABEL[kpi_type]
    return _SPEC_BY_KEY.get(str(kpi_type or "").strip().lower(), _SPEC_BY_KEY["sum"])


def kpi_icons() -> dict[str, str]:
    """Copy of the label -> icon map (callers must not mutate the original)."""
    return dict(KPI_ICONS)


def _require_col(df: pl.DataFrame, col: Optional[str]) -> Optional[str]:
    if col is None or col not in df.columns:
        return None
    return col


def _numeric_expr(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Float64, strict=False)


def fmt_num(v) -> str:
    """Format a number for a KPI tile.

    Precision is chosen from the magnitude and from whether the value is
    actually integral, so a count stays a count ("1,234") and a small decimal
    survives ("0.0123") instead of being flattened to two decimals.
    """
    if v is None:
        return "--"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(f):
        return "--"
    if math.isinf(f):
        return "\u221e" if f > 0 else "-\u221e"
    if f.is_integer() and abs(f) < 1e15:
        return f"{int(f):,}"
    a = abs(f)
    if a >= 1000:
        return f"{f:,.0f}"
    if a >= 100:
        return f"{f:,.1f}"
    if a >= 1:
        return f"{f:,.2f}"
    if a >= 0.001:
        return f"{f:,.4f}"
    return f"{f:.2e}"


def fmt_pct(p) -> str:
    """A signed percentage, e.g. ``+12.4`` / ``-3.0``.

    The sign carries the direction, so the card does not need a separate arrow
    glyph to be readable -- which leaves the arrow free to mean "versus the
    prior period" instead of restating the sign.
    """
    try:
        f = float(p)
    except (TypeError, ValueError):
        return "--"
    if math.isnan(f) or math.isinf(f):
        return "--"
    return f"{'+' if f >= 0 else ''}{f:.1f}"


# The private aliases the rest of the module (and older callers) used.
_fmt_num = fmt_num
_fmt_pct = fmt_pct


# =========================================================================
# Column helpers
# =========================================================================

def _as_frame(df):
    """Normalize a PolarsDataset / pandas / eager frame to a Polars DataFrame.

    Restored sessions hand back whatever ``load_dataset`` produced -- a pandas
    DataFrame -- and pandas has no ``.height``, which turned every KPI card
    into ``Err: ... has no attribute 'height'``. Convert at this one seam so
    both worlds reach the polars-native computations below.
    """
    if df is None:
        return None
    if hasattr(df, "frame") and getattr(df, "frame", None) is not df:
        try:
            frame = df.frame
            if hasattr(frame, "collect"):
                return frame.collect()
            return frame
        except Exception:
            return None
    if isinstance(df, pl.DataFrame):
        return df
    if isinstance(df, pl.LazyFrame):
        try:
            return df.collect()
        except Exception:
            return None
    try:
        import pandas as pd
        if isinstance(df, pd.DataFrame):
            try:
                return pl.from_pandas(df, include_index=False)
            except Exception:
                # pyarrow missing (or an object column it cannot map): a
                # column-by-column materialization always works.
                try:
                    return pl.DataFrame(
                        {str(c): df[c].tolist() for c in df.columns})
                except Exception:
                    return pl.DataFrame(
                        {str(c): [None if v is None else str(v)
                                  for v in df[c].tolist()]
                         for c in df.columns})
    except Exception:
        pass
    return df


def numeric_columns(df):
    frame = _as_frame(df)
    if frame is None:
        try:
            cols = getattr(df, "columns", None)
            if cols is not None:
                return [str(c) for c in cols]
        except Exception:
            return []
        return []
    out = []
    for c in frame.columns:
        try:
            if frame[c].dtype.is_numeric():
                out.append(str(c))
        except Exception:
            continue
    return out


def category_columns(df):
    frame = _as_frame(df)
    if frame is None:
        try:
            cols = getattr(df, "columns", None)
            if cols is not None:
                return [str(c) for c in cols]
        except Exception:
            return []
        return []
    out = []
    for c in frame.columns:
        try:
            if not frame[c].dtype.is_numeric():
                out.append(str(c))
        except Exception:
            out.append(str(c))
    return out


def date_columns(df):
    frame = _as_frame(df)
    if frame is None:
        try:
            cols = getattr(df, "columns", None)
            if cols is not None:
                return [str(c) for c in cols]
        except Exception:
            return []
        return []
    out = []
    for c in frame.columns:
        try:
            if frame[c].dtype in (pl.Date, pl.Datetime):
                out.append(str(c))
        except Exception:
            continue
    for c in frame.columns:
        c = str(c)
        if c in out:
            continue
        low = c.lower()
        if any(k in low for k in ("date", "time", "dt", "day", "month", "year", "period", "week")):
            out.append(c)
    return out


def distinct_values(df, col, limit=2000):
    frame = _as_frame(df)
    if frame is None:
        return []
    if col is None or col not in getattr(frame, "columns", []):
        return []
    try:
        s = frame[col].drop_nulls()
        if s.n_unique() > limit:
            return s.unique().head(limit).to_list()
        return s.unique().to_list()
    except Exception:
        return []


# =========================================================================
# Individual KPI computations (Polars-native)
# =========================================================================

def _kpi_sum(df: pl.DataFrame, col: str) -> Any:
    return df.select(_numeric_expr(col).sum()).item()


def _kpi_mean(df: pl.DataFrame, col: str) -> Any:
    return df.select(_numeric_expr(col).mean()).item()


def _kpi_median(df: pl.DataFrame, col: str) -> Any:
    return df.select(_numeric_expr(col).median()).item()


def _kpi_count(df: pl.DataFrame) -> Any:
    return df.height


def _kpi_count_nonnull(df: pl.DataFrame, col: str) -> Any:
    return df.select(pl.col(col).drop_nulls().len()).item()


def _kpi_min(df: pl.DataFrame, col: str) -> Any:
    return df.select(_numeric_expr(col).min()).item()


def _kpi_max(df: pl.DataFrame, col: str) -> Any:
    return df.select(_numeric_expr(col).max()).item()


def _kpi_std(df: pl.DataFrame, col: str) -> Any:
    return df.select(_numeric_expr(col).std()).item()


def _kpi_nunique(df: pl.DataFrame, col: str) -> Any:
    return df.select(pl.col(col).n_unique()).item()


def _kpi_missing(df: pl.DataFrame, col: str) -> Any:
    return df.select(pl.col(col).null_count()).item()


def _kpi_date_range(df: pl.DataFrame, col: str) -> Any:
    if df[col].dtype in (pl.Date, pl.Datetime):
        min_v = df.select(pl.col(col).min()).item()
        max_v = df.select(pl.col(col).max()).item()
        return f"{min_v} to {max_v}"
    return "--"


def _filtered(df: pl.DataFrame, col: Optional[str], val: Any) -> pl.DataFrame:
    """The rows whose *col* equals *val*.

    Compared as text on purpose: the value came out of a combo populated from
    the column itself, but it may have round-tripped through JSON, where the
    int 3 comes back as the string "3". A plain ``==`` would then match
    nothing.
    """
    if not col or col not in df.columns or val is None or val == "":
        return df
    return df.filter(pl.col(col).cast(pl.Utf8) == str(val))


def _kpi_share(df: pl.DataFrame, col: str, group_col: str, filter_val: Any) -> Any:
    if group_col and group_col in df.columns:
        total = df.select(_numeric_expr(col).sum()).item()
        if total == 0 or total is None:
            return 0.0
        if filter_val is not None:
            sub = _filtered(df, group_col, filter_val)
            sub_sum = sub.select(_numeric_expr(col).sum()).item()
        else:
            sub_sum = 0.0
        return (sub_sum or 0) / total * 100
    return 0.0


def _kpi_top_cat(df: pl.DataFrame, group_col: str, metric_col: str) -> Any:
    return _kpi_extreme_cat(df, group_col, metric_col, top=True)


def _kpi_bottom_cat(df: pl.DataFrame, group_col: str, metric_col: str) -> Any:
    return _kpi_extreme_cat(df, group_col, metric_col, top=False)


def _kpi_extreme_cat(df: pl.DataFrame, group_col: str, metric_col: str,
                     *, top: bool) -> Any:
    """``"<category>: <value>"`` for the highest (or lowest) category total.

    The metric is SUMMED per category and the category with the largest (or
    smallest) total is reported together with that total. Returning only the
    category name -- which is what the stub did -- loses the number the card
    is supposed to show, so both halves are rendered here.
    """
    if group_col not in df.columns or metric_col not in df.columns:
        return "--"
    try:
        result = (df.group_by(group_col)
                  .agg(_numeric_expr(metric_col).sum().alias("total"))
                  .filter(pl.col("total").is_not_null())
                  .sort("total", descending=top)
                  .head(1))
    except Exception as exc:
        log.debug("_kpi_extreme_cat failed on %r: %s", group_col, exc)
        return "--"
    if result.height == 0:
        return "--"
    category, total = result.row(0)
    return f"{category}: {fmt_num(total)}"


def _kpi_mom(df: pl.DataFrame, filter_col: str, col: str) -> Any:
    """Latest month vs the month before it, as a percentage change."""
    return _kpi_period_change(df, filter_col, col, period="month")


def _kpi_yoy(df: pl.DataFrame, filter_col: str, col: str) -> Any:
    """Latest year vs the year before it, as a percentage change."""
    return _kpi_period_change(df, filter_col, col, period="year")


def _kpi_period_change(df: pl.DataFrame, filter_col: str, col: str, *,
                       period: str = "month") -> Any:
    """Percentage change of the latest period against the one before it.

    The two periods are the immediately preceding calendar month (or year), so
    an intervening period that is absent from the dataset does not shift the
    comparison -- which is what ``polars_engine.period_change`` does too.

    Only rows that survived BOTH the date parse and the numeric parse take
    part: aligning two differently-dropped series silently pairs the wrong
    period with the wrong value, which is worse than showing no answer.
    """
    if filter_col not in df.columns or col not in df.columns:
        return None
    try:
        frame = df.with_columns(_date_expr(df, filter_col).alias("__date")).filter(
            pl.col("__date").is_not_null())
        if frame.height == 0:
            return None
        interval = "1mo" if period == "month" else "1y"
        totals = (frame.with_columns(pl.col("__date").dt.truncate(interval)
                                     .alias("__period"))
                  .group_by("__period")
                  .agg(_numeric_expr(col).sum().alias("__total")))
        if totals.height < 2:
            return None
        ordered = totals.sort("__period")
        periods = ordered.get_column("__period").to_list()
        values = ordered.get_column("__total").to_list()
        latest, previous = values[-1], values[-2]
        # The two periods must be CONSECUTIVE. If the dataset skips a month,
        # the two newest buckets are not a MoM pair, and reporting their
        # ratio as a month-over-month change would be a lie.
        latest_period, previous_period = periods[-1], periods[-2]
        if period == "month":
            expected = (latest_period.year - 1, 12) \
                if latest_period.month == 1 else (latest_period.year,
                                                 latest_period.month - 1)
        else:
            expected = (latest_period.year - 1, latest_period.month)
        if (previous_period.year, previous_period.month) != expected:
            return None
        if previous is None or previous == 0:
            return None
        return (latest - previous) / abs(previous) * 100
    except Exception as exc:
        log.debug("_kpi_period_change failed on %r/%r: %s", filter_col, col, exc)
        return None


def _date_expr(df: pl.DataFrame, column: str) -> pl.Expr:
    """An expression parsing *column* as a datetime (best effort).

    A column that already IS a datetime is used as-is; a text column is
    parsed leniently, so a stray value costs that one row rather than the
    whole calculation.
    """
    dtype = df[column].dtype
    if dtype in (pl.Date, pl.Datetime):
        return pl.col(column).cast(pl.Datetime)
    if dtype == pl.Utf8:
        return pl.coalesce(
            pl.col(column).str.strip_chars().str.to_datetime(strict=False),
            pl.col(column).str.strip_chars().str.to_datetime("%m/%d/%Y", strict=False),
            pl.col(column).str.strip_chars().str.to_datetime("%d/%m/%Y", strict=False),
        )
    return pl.col(column).cast(pl.Datetime, strict=False)


_KPI_CALCS = {
    "sum": _kpi_sum,
    "mean": _kpi_mean,
    "median": _kpi_median,
    "count": _kpi_count,
    "count_nonnull": _kpi_count_nonnull,
    "min": _kpi_min,
    "max": _kpi_max,
    "std": _kpi_std,
    "nunique": _kpi_nunique,
    "missing": _kpi_missing,
    "date_range": _kpi_date_range,
    "share": _kpi_share,
    "top_cat": _kpi_top_cat,
    "bottom_cat": _kpi_bottom_cat,
    "mom": _kpi_mom,
    "yoy": _kpi_yoy,
}


def _extreme_label(key: str, group_col: Optional[str]) -> str:
    word = "Top" if key == "top_cat" else "Bottom"
    return f"{word} {group_col}" if group_col else word


def _period_label(key: str, col: Optional[str], baseline_period: str) -> str:
    if key == "yoy":
        return f"YoY {col}"
    if key == "mom_given" and baseline_period:
        return f"{baseline_period} vs {col}"
    return f"MoM {col}"


def _attach_trend(result: dict, data, kpi_type: str, kwargs: dict) -> dict:
    """Attach a compact monthly trend when a numeric KPI has a date basis."""
    try:
        from services.polars_engine import PolarsDataset
    except Exception:
        try:
            from .polars_engine import PolarsDataset
        except Exception:
            PolarsDataset = ()
    if isinstance(data, PolarsDataset):
        frame = data.frame
        schema = data.schema
    else:
        frame = _as_frame(data)
        schema = {name: frame.schema[name] for name in frame.columns}
    measure = kwargs.get("col") or kwargs.get("metric_col")
    if not measure or measure not in schema:
        return result
    date_col = kwargs.get("trend_date_col") or kwargs.get("filter_col")
    date_cols = [name for name, dtype in schema.items()
                 if dtype in (pl.Date, pl.Datetime)]
    if date_col not in date_cols:
        date_col = date_cols[0] if len(date_cols) == 1 else None
    if not date_col:
        return result
    # Trend aggregation mirrors the KPI type and returns at most 24 points.
    op = "mean" if kpi_spec(kpi_type)["key"] in {"mean", "median"} else "sum"
    # Use the known schema to keep lazy datasets lazy through the bounded
    # aggregation; the inferred trend only needs a normalized month bucket.
    if schema[date_col] == pl.Date:
        date_expr = pl.col(date_col).cast(pl.Datetime)
    else:
        date_expr = pl.col(date_col).cast(pl.Datetime, strict=False)
    metric_expr = _numeric_expr(measure)
    if schema[measure].is_float():
        metric_expr = metric_expr.fill_nan(None)
    grouped = (frame.with_columns(date_expr.alias("__trend_date"))
               .filter(pl.col("__trend_date").is_not_null())
               .group_by(pl.col("__trend_date").dt.truncate("1mo").alias("__period"))
               .agg((metric_expr.mean() if op == "mean" else metric_expr.sum()).alias("__value"))
               .sort("__period").tail(24))
    if isinstance(grouped, pl.LazyFrame):
        grouped = grouped.collect()
    trend = [[str(period), float(value)] for period, value in grouped.iter_rows()
             if period is not None and value is not None and math.isfinite(float(value))]
    if len(trend) >= 2:
        result["trend"] = trend
        first, last = trend[0][1], trend[-1][1]
        up = last >= first
        pct = ((last - first) / abs(first) * 100) if first else None
        result["trend_direction"] = "up" if up else "down"
        result["trend_change_pct"] = pct
        # Ready-made delta text so BOTH render paths (static markup and the
        # live worker) show the +/- indication without re-deriving it.
        if pct is not None:
            result["delta"] = ("\u25b2 " if up else "\u25bc ") + f"{abs(pct):.1f}%"
        else:
            result["delta"] = "\u25b2" if up else "\u25bc"
    return result


def calc_kpi(df, kpi_type: str, **kwargs) -> dict:
    """Compute one KPI from *df*.

    Dispatches ``PolarsDataset`` to ``calc_polars_kpi`` and handles eager
    ``pl.DataFrame`` directly.  A failure never raises: the card shows the
    error text.  A dashboard with one bad KPI should still render the other
    nine.
    """
    # Explicitly dispatch PolarsDataset to the Polars-native implementation.
    # This must come before any access to df.height in _evaluate().
    if hasattr(df, "frame") and getattr(df, "frame", None) is not None and getattr(df, "frame") is not df:
        try:
            from .polars_engine import PolarsDataset, calc_polars_kpi
        except Exception:
            PolarsDataset = None
            calc_polars_kpi = None
            log.error("polars_engine unavailable for KPI %r", kpi_type, exc_info=True)
        if PolarsDataset is not None and isinstance(df, PolarsDataset):
            try:
                return _attach_trend(calc_polars_kpi(df, kpi_type, **kwargs), df,
                                     kpi_type, kwargs)
            except Exception:
                # Never let the real exception collapse into the eager path
                # silently: log it, then answer with the snapshot materialized.
                log.error("Polars-native KPI %r failed; using eager fallback",
                          kpi_type, exc_info=True)
                materialized = _as_frame(df)
                if materialized is not None:
                    df = materialized

    spec = kpi_spec(kpi_type)
    key = spec["key"]
    label = kwargs.get("label")
    out_label = label or spec["label"]
    prefix = suffix = ""
    value: Any = "--"
    change_pct: Optional[float] = None

    col = kwargs.get("col")
    group_col = kwargs.get("group_col")
    metric_col = kwargs.get("metric_col")
    filter_col = kwargs.get("filter_col")
    filter_val = kwargs.get("filter_val")
    formula = kwargs.get("formula", "") or ""
    baseline_period = kwargs.get("baseline_period", "") or ""

    recipe = {
        "kpi_type": kpi_type, "kpi_key": key, "col": col,
        "group_col": group_col, "metric_col": metric_col,
        "filter_col": filter_col, "filter_val": filter_val,
        "label": label, "formula": formula,
        "baseline_period": baseline_period,
    }

    try:
        value, out_label, prefix, suffix, change_pct = _evaluate(
            df, key, col, group_col, metric_col, filter_col, filter_val,
            label, formula, baseline_period)
    except Exception as exc:              # noqa: BLE001 - a KPI never breaks the page
        log.debug("KPI %r failed", kpi_type, exc_info=True)
        value = "Err: %s" % (str(exc)[:60] or exc.__class__.__name__)

    result = {"icon": spec["icon"], "label": out_label, "value": value,
            "prefix": prefix, "suffix": suffix, "change_pct": change_pct,
            "_recipe": recipe}
    try:
        return _attach_trend(result, df, kpi_type, kwargs)
    except Exception:
        # The trend line is decorative; it must never take the card down.
        log.error("Attaching trend to KPI %r failed; returning value without trend",
                  kpi_type, exc_info=True)
        return result


def _evaluate(df: pl.DataFrame, key: str, col, group_col, metric_col,
              filter_col, filter_val, label, formula, baseline_period):
    """Dispatch one KPI type. Returns (value, label, prefix, suffix, change)."""
    # Tolerate a lazy/PolarsDataset snapshot being handed in by mistake:
    # materialize it instead of dying on df.height with a cryptic error.
    df = _as_frame(df)
    if df is None or df.height == 0:
        return "--", label or kpi_spec(key)["label"], "", "", None

    def _require(name, message="Pick a column"):
        if not name or name not in df.columns:
            raise ValueError(message)
        return name

    # -- Whole-frame aggregates -------------------------------------------
    if key == "count":
        return fmt_num(df.height), label or "Total Records", "", "", None
    if key == "count_nonnull":
        name = _require(col)
        n = df.select(pl.col(name).drop_nulls().len()).item()
        return fmt_num(n), label or f"Count {name}", "", "", None
    if key == "nunique":
        name = _require(col)
        n = df.select(pl.col(name).drop_nulls().n_unique()).item()
        return fmt_num(n), label or f"Unique {name}", "", "", None
    if key == "missing":
        name = _require(col)
        expr = pl.col(name).is_null()
        if df[name].dtype.is_float():
            expr = expr | pl.col(name).is_nan()
        n = df.select(expr.sum()).item() or 0
        pct = n / max(1, df.height) * 100
        return (fmt_num(n), label or f"Missing {name}",
                "", f" ({pct:.1f}%)", None)
    if key == "std":
        name = _require(col)
        return (fmt_num(df.select(_numeric_expr(name).std()).item()),
                label or f"Std dev {name}", "", "", None)

    # -- Simple numeric aggregates ----------------------------------------
    if key in ("sum", "mean", "median", "min", "max"):
        name = _require(col)
        aggregate = {"sum": "sum", "mean": "mean", "median": "median",
                     "min": "min", "max": "max"}[key]
        v = df.select(getattr(_numeric_expr(name), aggregate)()).item()
        short = {"sum": "Total", "mean": "Avg", "median": "Median",
                 "min": "Min", "max": "Max"}[key]
        return fmt_num(v), label or f"{short} {name}", "", "", None

    if key == "date_range":
        name = _require(col, "Pick a date column")
        if df[name].dtype not in (pl.Date, pl.Datetime):
            return "--", label or f"Range of {name}", "", "", None
        low, high = df.select(pl.col(name).min().alias("__min"),
                              pl.col(name).max().alias("__max")).row(0)
        if low is None or high is None:
            return "--", label or f"Range of {name}", "", "", None
        return (f"{low} \u2192 {high}", label or f"Range of {name}", "", "", None)

    # -- Share of a total --------------------------------------------------
    if key == "share":
        name = _require(col)
        total = df.select(_numeric_expr(name).sum()).item()
        if not total:
            return "0.0", label or f"{filter_val} share", "", "%", None
        sub = _filtered(df, group_col, filter_val)
        sub_sum = sub.select(_numeric_expr(name).sum()).item() or 0
        return (fmt_pct(sub_sum / total * 100),
                label or f"{filter_val} share of {name}", "", "%", None)

    if key == "vs_mean":
        name = _require(col)
        avg = df.select(_numeric_expr(name).mean()).item()
        if not avg:
            return "--", label or f"{filter_val} vs avg {name}", "", "%", None
        sub = _filtered(df, group_col, filter_val)
        v = sub.select(_numeric_expr(name).mean()).item()
        if v is None:
            return "--", label or f"{filter_val} vs avg {name}", "", "%", None
        pct = (v - avg) / abs(avg) * 100
        return (fmt_pct(pct), label or f"{filter_val} vs avg {name}",
                "", "%", float(pct))

    # -- Category extremes -------------------------------------------------
    if key in ("top_cat", "bottom_cat"):
        lbl = label or _extreme_label(key, group_col)
        _require(group_col, "Pick a category column")
        _require(metric_col, "Pick a metric column")
        text = _kpi_extreme_cat(df, group_col, metric_col,
                                top=(key == "top_cat"))
        return text, lbl, "", "", None

    # -- Period over period -------------------------------------------------
    if key in ("mom", "mom_given", "yoy"):
        lbl = label or _period_label(key, col, baseline_period)
        if key == "mom_given":
            current, previous = _kpi_month_pair(df, filter_col, col,
                                                 baseline_period)
            if current is None or previous is None or not previous:
                return "--", lbl, "", "%", None
            pct = (current - previous) / abs(previous) * 100
            return fmt_pct(pct), lbl, "", "%", float(pct)
        pct = _kpi_period_change(
            df, filter_col, col,
            period="year" if key == "yoy" else "month")
        if pct is None:
            return "--", lbl, "", "%", None
        return fmt_pct(pct), lbl, "", "%", float(pct)

    # -- Running total -----------------------------------------------------
    if key == "running":
        name = _require(col)
        date_name = _require(filter_col, "Pick a date column")
        frame = df.with_columns(_date_expr(df, date_name).alias("__date")).filter(
            pl.col("__date").is_not_null())
        if frame.height == 0:
            return "--", label or f"Running total {name}", "", "", None
        total, latest = frame.select(
            _numeric_expr(name).sum(), pl.col("__date").max()).row(0)
        return (fmt_num(total), label or f"Running total {name}",
                "", "" if latest is None else f" to {latest:%d %b %y}", None)

    # -- Custom expression -------------------------------------------------
    if key == "custom":
        if not str(formula or "").strip():
            raise ValueError("Enter a formula, e.g. `Revenue` / `Units`")
        from services.polars_engine import compile_numeric_formula
        total = df.select(compile_numeric_formula(df, formula).sum()).item()
        return fmt_num(total), label or "Custom", "", "", None

    return "--", label or kpi_spec(key)["label"], "", "", None


def _kpi_month_pair(df: pl.DataFrame, filter_col, col, baseline_period):
    """(value of the baseline month, value of the month after it)."""
    if filter_col not in df.columns or col not in df.columns:
        return None, None
    text = str(baseline_period or "").strip()
    if not text:
        return None, None
    try:
        year_text, month_text = text.split("-", 1)
        year, month = int(year_text), int(month_text)
        if month not in range(1, 13):
            raise ValueError
    except (TypeError, ValueError):
        return None, None
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    try:
        frame = df.with_columns(_date_expr(df, filter_col).alias("__date")).filter(
            pl.col("__date").is_not_null())
        if frame.height == 0:
            return None, None

        def _sum(y, m):
            return frame.filter((pl.col("__date").dt.year() == y)
                                & (pl.col("__date").dt.month() == m)) \
                .select(_numeric_expr(col).sum()).item()

        return _sum(year, month), _sum(next_year, next_month)
    except Exception as exc:
        log.debug("_kpi_month_pair failed on %r: %s", filter_col, exc)
        return None, None


def recompute(recipe: dict, df: pl.DataFrame) -> dict:
    """Recompute a KPI from its stored ``_recipe`` against *df*.

    This is the "re-open a saved dashboard after re-uploading the file" path.
    A column that no longer exists is dropped from the recipe rather than
    raising, so a renamed column degrades to "pick a new one" instead of
    taking the dashboard down.
    """
    r = dict(recipe or {})
    cols = set(str(c) for c in df.columns)

    def _alive(name):
        return name if (name and str(name) in cols) else None

    stale = [str(r.get(k)) for k in ("col", "group_col", "metric_col", "filter_col")
             if r.get(k) and str(r.get(k)) not in cols]

    out = calc_kpi(
        df,
        r.get("kpi_type") or "Total (Sum)",
        col=_alive(r.get("col")),
        group_col=_alive(r.get("group_col")),
        metric_col=_alive(r.get("metric_col")),
        filter_col=_alive(r.get("filter_col")),
        filter_val=r.get("filter_val"),
        label=r.get("label"),
        formula=r.get("formula", ""),
        baseline_period=r.get("baseline_period", ""),
    )
    if stale:
        out["stale"] = ", ".join(stale)
        out["value"] = "--"
        out["_recipe"] = r
    return out


def recompute_all(kpis: list, df: pl.DataFrame) -> list:
    """Recompute a whole KPI list."""
    out = []
    for k in list(kpis or []):
        k = dict(k or {})
        recipe = k.get("_recipe")
        if recipe:
            try:
                fresh = recompute(recipe, df)
                if k.get("label"):
                    fresh["label"] = k["label"]
                out.append(fresh)
                continue
            except Exception:
                log.debug("could not recompute a KPI card", exc_info=True)
        out.append(k)
    return out
