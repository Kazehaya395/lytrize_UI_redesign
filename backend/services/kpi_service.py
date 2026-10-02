"""services/kpi_service.py -- Headless KPI card computation.

The dashboard's KPI cards used to be a five-way Sum/Average/Count/Min/Max
dialog, which is a fraction of what the old Streamlit app offered
(``_KPI_TYPES`` in ``modules/pages/dashboard.py``). This module ports that
list, adds the preset calculations a professional tool needs on top of it
(running total, distinct count, share of grand total, period-over-period
change against a chosen baseline, variance against the column mean) and adds a
custom-expression type.

Two things make this different from the old implementation:

* Every result carries a ``_recipe`` -- the exact parameters used -- so a card
  can be recomputed against a freshly re-uploaded dataset without the caller
  having to remember which drop-downs the user had touched. The old code relied
  on ``modules/utils/regenerate.py`` doing that; here the recipe travels with
  the card, so ``recompute`` below is all that is needed.
* Nothing here touches Qt, Streamlit or session state, so the desktop page and
  the export path can both use it.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

import pandas as pd

log = logging.getLogger(__name__)


# =========================================================================
# The type catalogue
# =========================================================================
# Each entry is (key, label, icon, fields). ``fields`` drives the form the
# dialog builds, so the type list, the form and the calculation can never
# disagree:
#
#   "col"        one numeric column            -> col
#   "col_any"    one column of any dtype       -> col
#   "col_date"   one date-ish column           -> col
#   "formula"    a formula over numeric cols   -> formula
#
# plus the multi-field types, which list every parameter they consume.

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
    {"key": "custom", "label": "Custom Expression", "icon": "\u270F\uFE0F", "fields": ["formula"]},
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


# =========================================================================
# Formatting
# =========================================================================

def fmt_num(v) -> str:
    """Format a number for a KPI tile.

    The old ``_fmt_num`` rounded everything to two decimals, which turned a row
    count into "1,000.00" and a share of a large total into something with four
    meaningless trailing digits. Precision is chosen from the magnitude and from
    whether the value is actually integral, so a count stays a count and a small
    decimal survives.
    """
    try:
        if v is None or pd.isna(v):
            return "--"
    except (TypeError, ValueError):
        pass
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
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
    glyph to be readable -- which leaves the arrow free to mean "versus the prior
    period" instead of restating the sign.
    """
    try:
        f = float(p)
    except (TypeError, ValueError):
        return "--"
    if math.isnan(f) or math.isinf(f):
        return "--"
    return f"{'+' if f >= 0 else ''}{f:.1f}"


# =========================================================================
# Column helpers
# =========================================================================

def numeric_columns(df: pd.DataFrame) -> list[str]:
    try:
        return [str(c) for c in df.select_dtypes(include="number").columns]
    except Exception:
        return []


def category_columns(df: pd.DataFrame) -> list[str]:
    out: list[str] = []
    try:
        for c in df.select_dtypes(include=["object", "category", "bool"]).columns:
            out.append(str(c))
    except Exception:
        pass
    return out


def date_columns(df: pd.DataFrame) -> list[str]:
    """Columns that look like dates: real datetime dtypes first, then a cheap
    name probe, so a date stored as text is still offered."""
    out: list[str] = []
    try:
        for c in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
            out.append(str(c))
    except Exception:
        pass
    for c in df.columns:
        c = str(c)
        if c in out:
            continue
        if any(k in c.lower() for k in ("date", "time", "dt", "day", "month",
                                        "year", "period", "week")):
            out.append(c)
    return out


def distinct_values(df: pd.DataFrame, col: str, limit: int = 2000) -> list:
    """Distinct values of *col*, bounded.

    The old form loaded every distinct value into a combo, which on a
    high-cardinality key column meant materialising a million-row set into the
    GUI thread. Uniques are still computed -- that is the point of the picker --
    but the LIST handed to the UI is capped.
    """
    try:
        s = df[col].dropna()
        if int(s.nunique()) > limit:
            return s.unique()[:limit].tolist()
        return s.unique().tolist()
    except Exception:
        return []


# =========================================================================
# The calculation
# =========================================================================

def calc_kpi(df: pd.DataFrame, kpi_type: str, col: Optional[str] = None,
             group_col: Optional[str] = None, metric_col: Optional[str] = None,
             filter_col: Optional[str] = None, filter_val: Any = None,
             label: Optional[str] = None, formula: str = "",
             baseline_period: str = "") -> dict:
    """Compute one KPI from *df*.

    Returns a dict with ``icon``, ``label``, ``value``, ``prefix``, ``suffix``,
    an optional ``change_pct`` (only for the comparison types, so the card knows
    whether an arrow means anything) and ``_recipe`` -- the exact parameters
    used, so the card can be recomputed later against a different dataset
    without the caller re-deriving the user's choices.

    A failure never raises: the card shows the error text. A dashboard with one
    bad KPI should still render the other nine.
    """
    spec = kpi_spec(kpi_type)
    key = spec["key"]
    out_label = label or spec["label"]
    prefix = suffix = ""
    value = "--"
    change_pct: Optional[float] = None
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
    except Exception as exc:  # noqa: BLE001 - a KPI must never take the page down
        log.debug("KPI %r failed", kpi_type, exc_info=True)
        value = "Err: %s" % (str(exc)[:60] or exc.__class__.__name__)
    return {"icon": spec["icon"], "label": out_label, "value": value,
            "prefix": prefix, "suffix": suffix, "change_pct": change_pct,
            "_recipe": recipe}


def _series(df, col):
    if col not in df.columns:
        raise ValueError("Pick a column")
    return df[col]


def _num(df, col):
    """The column as a float Series, with non-numeric entries dropped.

    ``pd.to_numeric`` is used rather than trusting the dtype: a column read from
    CSV arrives as object with a stray "1,234" or "N/A" in it, and the old code
    raised on the whole column instead of ignoring the bad rows.
    """
    if col not in df.columns:
        raise ValueError("Pick a column")
    return pd.to_numeric(df[col], errors="coerce").dropna()


def _dates(df, col):
    if col not in df.columns:
        raise ValueError("Pick a date column")
    try:
        return pd.to_datetime(df[col], errors="coerce")
    except (OverflowError, ValueError, TypeError):
        # Junk that errors="coerce" still cannot absorb makes pandas raise out
        # of tslib rather than coercing; fall back to "no dates" so the caller
        # reports an empty result instead of taking the page down.
        return pd.Series([pd.NaT] * len(df), index=df.index)


def _filtered(df, col, val):
    if not col or col not in df.columns or val is None or val == "":
        return df
    # Compared as text on purpose: the value came out of a combo populated from
    # the column itself, but it may have round-tripped through JSON, where the
    # int 3 comes back as the string "3". A plain == would then match nothing.
    return df[df[col].astype(str) == str(val)]


def _extreme_label(key, group_col):
    word = "Top" if key == "top_cat" else "Bottom"
    return f"{word} {group_col}" if group_col else word


def _period_label(key, col, baseline_period):
    if key == "yoy":
        return "YoY %s" % col
    if key == "mom_given" and baseline_period:
        return "%s vs %s" % (baseline_period, col)
    return "MoM %s" % col


def _evaluate(df, key, col, group_col, metric_col, filter_col, filter_val,
              label, formula, baseline_period):
    """Dispatch one KPI type. Returns (value, label, prefix, suffix, change)."""
    if df is None or len(df) == 0:
        return "--", label or kpi_spec(key)["label"], "", "", None

    # -- Whole-frame aggregates -------------------------------------------
    if key == "count":
        return fmt_num(len(df)), label or "Total Records", "", "", None
    if key == "count_nonnull":
        n = int(_series(df, col).notna().sum())
        return fmt_num(n), label or f"Count {col}", "", "", None
    if key == "nunique":
        n = int(_series(df, col).nunique())
        return fmt_num(n), label or f"Unique {col}", "", "", None
    if key == "missing":
        s = _series(df, col)
        n = int(s.isna().sum())
        pct = n / max(1, len(s)) * 100
        return (fmt_num(n), label or f"Missing {col}",
                "", f" ({pct:.1f}%)", None)
    if key == "std":
        return (fmt_num(_num(df, col).std()), label or f"Std dev {col}",
                "", "", None)

    # -- Simple numeric aggregates ----------------------------------------
    if key in ("sum", "mean", "median", "min", "max"):
        s = _num(df, col)
        v = {"sum": s.sum, "mean": s.mean, "median": s.median,
             "min": s.min, "max": s.max}[key]()
        short = {"sum": "Total", "mean": "Avg", "median": "Median",
                 "min": "Min", "max": "Max"}[key]
        return fmt_num(v), label or f"{short} {col}", "", "", None

    if key == "date_range":
        d = _dates(df, col).dropna()
        if not len(d):
            return "--", label or f"Range of {col}", "", "", None
        val = "%s \u2192 %s" % (d.min().strftime("%d %b %y"),
                                d.max().strftime("%d %b %y"))
        return val, label or f"Range of {col}", "", "", None

    # -- Share of a total --------------------------------------------------
    if key == "share":
        tot = _num(df, col).sum()
        if not tot:
            return "0.0", label or f"{filter_val} share", "", "%", None
        sub = _num(_filtered(df, group_col, filter_val), col).sum()
        return (fmt_pct(sub / tot * 100), label or f"{filter_val} share of {col}",
                "", "%", None)

    if key == "vs_mean":
        avg = _num(df, col).mean()
        if not avg or pd.isna(avg):
            return "--", label or f"{filter_val} vs avg {col}", "", "%", None
        v = _num(_filtered(df, group_col, filter_val), col).mean()
        if pd.isna(v):
            return "--", label or f"{filter_val} vs avg {col}", "", "%", None
        pct = (v - avg) / abs(avg) * 100
        return (fmt_pct(pct), label or f"{filter_val} vs avg {col}",
                "", "%", float(pct))

    # -- Category extremes -------------------------------------------------
    if key in ("top_cat", "bottom_cat"):
        lbl = label or _extreme_label(key, group_col)
        if group_col not in df.columns or metric_col not in df.columns:
            return "--", lbl, "", "", None
        vals = pd.to_numeric(df[metric_col], errors="coerce")
        agg = pd.DataFrame({"_g": df[group_col], "_v": vals}).dropna()
        if not len(agg):
            return "--", lbl, "", "", None
        sums = agg.groupby("_g", dropna=True)["_v"].sum()
        idx = sums.idxmax() if key == "top_cat" else sums.idxmin()
        return "%s: %s" % (idx, fmt_num(sums.loc[idx])), lbl, "", "", None

    # -- Period over period -------------------------------------------------
    if key in ("mom", "mom_given", "yoy"):
        lbl = label or _period_label(key, col, baseline_period)
        cur, prev = _period_pair(df, key, col, filter_col, baseline_period)
        if cur is None or prev is None or not prev:
            return "--", lbl, "", "%", None
        pct = (cur - prev) / abs(prev) * 100
        return fmt_pct(pct), lbl, "", "%", float(pct)

    # -- Running total -----------------------------------------------------
    if key == "running":
        frame = pd.DataFrame({"_d": _dates(df, filter_col),
                              "_v": pd.to_numeric(df[col], errors="coerce")
                              if col in df.columns else None}).dropna()
        if not len(frame):
            return "--", label or f"Running total {col}", "", "", None
        latest = frame["_d"].max()
        total = frame.loc[frame["_d"] <= latest, "_v"].sum()
        return (fmt_num(total), label or f"Running total {col}",
                "", " to %s" % latest.strftime("%d %b %y"), None)

    # -- Custom expression -------------------------------------------------
    if key == "custom":
        if not str(formula or "").strip():
            raise ValueError("Enter a formula, e.g. `Revenue` / `Units`")
        from services.transform_service import safe_formula_eval
        s = safe_formula_eval(df, formula)
        return fmt_num(s.sum()), label or "Custom", "", "", None

    return "--", label or kpi_spec(key)["label"], "", "", None


def _period_pair(df, key, col, filter_col, baseline_period):
    """Return (current_value, previous_value) for a period-over-period KPI.

    The three period types differ only in how the periods are cut: month, year,
    or an explicit period the user names. Keeping that in one place is what stops
    them from drifting apart.
    """
    # Only rows that survived BOTH the date parse and the numeric parse can take
    # part: aligning two differently-dropped series silently pairs the wrong
    # month with the wrong value, which is worse than showing no answer.
    frame = pd.DataFrame({"_d": _dates(df, filter_col),
                          "_v": pd.to_numeric(df[col], errors="coerce")
                          if col in df.columns else None}).dropna()
    if not len(frame):
        return None, None

    if key == "yoy":
        year = frame["_d"].dt.year
        latest = int(year.max())
        return (frame.loc[year == latest, "_v"].sum(),
                frame.loc[year == latest - 1, "_v"].sum())

    period = frame["_d"].dt.to_period("M")
    if key == "mom_given" and str(baseline_period or "").strip():
        try:
            want = pd.Period(str(baseline_period).strip(), freq="M")
        except Exception:
            return None, None
        return (frame.loc[period == want, "_v"].sum(),
                frame.loc[period == (want + 1), "_v"].sum())
    latest = period.max()
    return (frame.loc[period == latest, "_v"].sum(),
            frame.loc[period == (latest - 1), "_v"].sum())


# =========================================================================
# Recomputation (the _recipe's whole purpose)
# =========================================================================

def recompute(recipe: dict, df: pd.DataFrame) -> dict:
    """Recompute a KPI from its stored ``_recipe`` against *df*.

    This is the "re-open a saved dashboard after re-uploading the file" path.
    A column that no longer exists is dropped from the recipe rather than
    raising, so a renamed column degrades to "pick a new one" instead of
    blanking the card.
    """
    r = dict(recipe or {})
    # NB: ``getattr(df, "columns", ...) or []`` raises on an empty frame --
    # a pandas Index has no __bool__, so it falls through to __len__ and an
    # EMPTY index is ambiguous. ``list(...)`` is the safe accessor.
    cols = set(str(c) for c in list(getattr(df, "columns", [])))

    def _alive(name):
        return name if (name in cols) else None

    # A recipe that names a column the new file does not have would otherwise
    # quietly recompute against None and render "--", or worse, recompute
    # against a DIFFERENT column of the same name in another frame. The card is
    # marked instead, so the page can show it as needing a re-pick.
    stale = [str(r.get(k)) for k in ("col", "group_col", "metric_col",
                                     "filter_col")
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
        # Keep the ORIGINAL recipe, not the nulled-out one that was just
        # evaluated. The user needs to see which column went missing so they can
        # re-point the card; handing back the evaluated recipe would have
        # already forgotten it.
        out["_recipe"] = r
    return out


def recompute_all(kpis: list, df: pd.DataFrame) -> list:
    """Recompute a whole KPI list, preserving anything without a recipe.

    A card stored by an older build (a bare ``{"label", "value"}``) has no
    recipe, so it is passed through untouched rather than blanked -- the user
    still sees the number they saved.
    """
    out = []
    for k in list(kpis or []):
        k = dict(k or {})
        recipe = k.get("_recipe")
        if recipe:
            try:
                fresh = recompute(recipe, df)
                # Keep a user-edited label: re-deriving it would silently rename
                # the card on every re-upload.
                if k.get("label"):
                    fresh["label"] = k["label"]
                out.append(fresh)
                continue
            except Exception:
                log.debug("could not recompute a KPI card", exc_info=True)
        out.append(k)
    return out


