"""services/transform_service.py -- Headless data transformation & cleaning.

Pure functions only: every function takes a DataFrame (and plain
arguments) and returns either a result dict/DataFrame or a NEW
DataFrame. Nothing here touches global state, Streamlit, or the Qt
event loop -- callers (desktop/pages/upload_page.py) are responsible
for pushing any resulting DataFrame back through
``SessionBridge.set_df()``.

Ported from the original Streamlit modules:
  modules/ui/column_manager.py -> add/remove/rename column
  modules/ui/column_tools.py   -> dtype preview/convert, column classify
  modules/ui/data_cleaner.py   -> text clean, find/replace, validate,
                                    string ops, numeric clean
"""

from __future__ import annotations

import ast
import datetime
import operator
import re
import unicodedata
import warnings
from typing import Any, Optional

import numpy as np
import pandas as pd


# =========================================================================
# Column Manager -- add / remove / rename
# =========================================================================

_SAFE_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_SAFE_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_formula_eval(df: pd.DataFrame, formula: str) -> pd.Series:
    """Evaluate a derived-column arithmetic formula, e.g. ``` `Sales` / `Units` ```."""
    numeric_cols = set(df.select_dtypes(include=[np.number]).columns)
    column_aliases: dict[str, str] = {}

    def replace_backtick(match: re.Match) -> str:
        col = match.group(1)
        if col not in df.columns:
            raise ValueError(f"Unknown column: {col}")
        alias = f"__col_{len(column_aliases)}"
        column_aliases[alias] = col
        return alias

    expr = re.sub(r"`([^`]+)`", replace_backtick, formula.strip())
    if not expr:
        raise ValueError("Formula is empty.")

    tree = ast.parse(expr, mode="eval")

    def eval_node(node):
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            col = column_aliases.get(node.id, node.id)
            if col not in df.columns:
                raise ValueError(f"Unknown column: {col}")
            if col not in numeric_cols:
                raise ValueError(f"Column '{col}' is not numeric.")
            return df[col]
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BIN_OPS:
            return _SAFE_BIN_OPS[type(node.op)](eval_node(node.left), eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_UNARY_OPS:
            return _SAFE_UNARY_OPS[type(node.op)](eval_node(node.operand))
        raise ValueError("Only arithmetic with numeric columns and constants is allowed.")

    result = eval_node(tree)
    if np.isscalar(result):
        return pd.Series([result] * len(df), index=df.index)
    return result


def _safe_to_datetime(values, **kwargs) -> pd.Series:
    """pd.to_datetime wrapper that never leaks raw pandas errors.

    ``errors="coerce"`` does not protect against every internal failure:
    on several pandas builds, array parsing of mixed/numeric-looking
    strings can raise ``OverflowError: Overflow in int64 addition`` from
    tslibs' overflow-safe add. Convert any such failure into all-NaT so
    the caller's "doesn't look like a date column" validation handles it
    with a friendly message instead.
    """
    try:
        return pd.to_datetime(values, **kwargs)
    except (OverflowError, ValueError, TypeError):
        return pd.to_datetime(pd.Series([None] * len(values), index=getattr(values, "index", None)))


_SLASH_DATE_RE = r"^\s*(\d{1,2})[/\-](\d{1,2})[/\-]\d{2,4}(?:\s.*)?$"


def _detect_dayfirst(s: pd.Series) -> Optional[bool]:
    """Infer day-first vs month-first layout from slash/dash date strings.

    If any first component exceeds 12 the column must be day-first
    (e.g. '13/01/2023' present); if only the second component ever exceeds
    12 it must be month-first ('01/13/2023'); otherwise ambiguous (None)
    and pandas' month-first default is kept.
    """
    parts = s.str.extract(_SLASH_DATE_RE, expand=True)
    if parts.empty or parts[0].isna().all():
        return None
    a = pd.to_numeric(parts[0], errors="coerce")
    b = pd.to_numeric(parts[1], errors="coerce")
    a_gt12 = bool((a > 12).any())
    b_gt12 = bool((b > 12).any())
    if a_gt12 and not b_gt12:
        return True   # day-first, e.g. transaction_date '01/01/2023'..'30/06/2023'
    if b_gt12 and not a_gt12:
        return False  # month-first
    return None       # ambiguous -> pandas default (month-first)


def _parse_datetime_robust(series: pd.Series) -> pd.Series:
    # Numeric-only columns (e.g. transaction_id) can never be dates; skip
    # parsing entirely so to_datetime never tries epoch interpretation.
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    s = series.astype(str).str.strip()
    # Day-first detection: a bare month-first to_datetime turns every
    # DD/MM/YYYY value with day>12 into NaT and silently month/day-swaps
    # every day<=12 value (the "date conversion not working" bug on
    # transaction_date: 90,914 of 149,116 values null, 39% "success").
    dayfirst = _detect_dayfirst(s)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        result = _safe_to_datetime(s, errors="coerce", dayfirst=bool(dayfirst))
    remaining = result.isna() & series.notna()
    if not remaining.any():
        return result
    normalised = (
        s[remaining].str.upper()
        .str.replace(r"([AP]M)$", r" \1", regex=True)
        .str.replace(r"\s{2,}", " ", regex=True)
    )
    result[remaining] = _safe_to_datetime("1970-01-01 " + normalised, errors="coerce")
    remaining = result.isna() & series.notna()
    if not remaining.any():
        return result
    # Explicit-format retries: plain date formats first (order follows the
    # detected layout), then the time-oriented formats.
    date_fmts = ("%d/%m/%Y", "%m/%d/%Y") if dayfirst else ("%m/%d/%Y", "%d/%m/%Y")
    for fmt in (*date_fmts,
                "%I:%M %p", "%I:%M:%S %p", "%I:%M%p", "%I:%M:%S%p", "%I %p",
                "%m/%d/%Y %I:%M %p", "%d/%m/%Y %I:%M %p",
                "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"):
        still = result.isna() & series.notna()
        if not still.any():
            break
        try:
            result[still] = pd.to_datetime(s[still], format=fmt, errors="coerce")
        except Exception:
            pass
    return result


def date_diff_column(df: pd.DataFrame, col_a: str, col_b: Optional[str], unit: str) -> pd.Series:
    """Compute a date-difference derived column. *col_b* None means "today"."""
    # Use the robust parser (multi-format, coerce) -- the previous naive
    # ``pd.to_datetime(series.astype(str))`` tried to interpret numeric
    # columns as nanosecond epoch timestamps and blew up with
    # "Overflow in int64 addition" on columns like transaction_id.
    series_a = _parse_datetime_robust(df[col_a])
    series_b = (
        pd.Series([pd.Timestamp.today()] * len(df), index=df.index)
        if col_b is None else _parse_datetime_robust(df[col_b])
    )

    for name, series in ((col_a, series_a), (col_b, series_b)):
        if col_b is None and name == col_b:
            continue  # "today" sentinel -- nothing to validate
        if series.notna().sum() == 0:
            raise ValueError(
                f"Column '{name}' doesn't look like a date column. "
                f"Pick a column containing dates/times."
            )

    if unit == "Days":
        return (series_b - series_a).dt.days.astype("Int64")
    if unit == "Months":
        result = (series_b.dt.year - series_a.dt.year) * 12 + (series_b.dt.month - series_a.dt.month)
        adj = (series_b.dt.day < series_a.dt.day).astype(int)
        return (result - adj).astype("Int64")
    if unit in ("Years", "Age (Years from DOB vs Today)"):
        return (
            (series_b.dt.year - series_a.dt.year)
            + (series_b.dt.month - series_a.dt.month) / 12.0
            + (series_b.dt.day - series_a.dt.day) / 365.0
        )
    if unit == "Previous Year (subtract 1 year)":
        return series_a - pd.DateOffset(years=1)
    if unit == "Previous Month (subtract 1 month)":
        return series_a - pd.DateOffset(months=1)
    return (series_b - series_a).dt.days


DATE_PART_OPTIONS = [
    "Date (YYYY-MM-DD)", "Year", "Quarter", "Month (Number)", "Month Name",
    "Week Number", "Day", "Weekday Name", "Hour (12h AM/PM)", "Hour (24h)",
]


def extract_date_part(df: pd.DataFrame, col: str, part: str) -> pd.Series:
    """Extract a date/time component (year, month name, etc.) from *col*."""
    dt = _parse_datetime_robust(df[col])
    mapping = {
        "Date (YYYY-MM-DD)": dt.dt.date,
        "Year": dt.dt.year,
        "Quarter": dt.dt.quarter,
        "Month (Number)": dt.dt.month,
        "Month Name": dt.dt.month_name(),
        "Week Number": dt.dt.isocalendar().week.astype("Int64"),
        "Day": dt.dt.day,
        "Weekday Name": dt.dt.day_name(),
        "Hour (12h AM/PM)": dt.dt.strftime("%-I %p"),
        "Hour (24h)": dt.dt.hour,
    }
    return mapping[part]


def add_column_op(df: pd.DataFrame, col_a: str, col_b: str, op: str) -> pd.Series:
    """Compute Column [op] Column (op is one of '*','/','+','-')."""
    return safe_formula_eval(df, f"`{col_a}` {op} `{col_b}`")


def moving_average_column(df: pd.DataFrame, col: str, window: int) -> pd.Series:
    """Rolling moving average over *window* rows ending at the current row.

    ``min_periods=1`` so the first rows get a partial-window average instead
    of NaN; non-numeric values are coerced to NaN.
    """
    window = max(int(window), 1)
    numeric = pd.to_numeric(df[col], errors="coerce")
    return numeric.rolling(window=window, min_periods=1).mean()


def add_derived_column(df: pd.DataFrame, new_col_name: str, series: pd.Series) -> pd.DataFrame:
    """Return a copy of *df* with a new column added."""
    out = df.copy()
    out[new_col_name] = series
    return out


def remove_column(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Return a copy of *df* with *col* dropped."""
    return df.drop(columns=[col])


def rename_column(df: pd.DataFrame, col: str, new_name: str) -> pd.DataFrame:
    """Return a copy of *df* with *col* renamed to *new_name*."""
    if new_name in df.columns:
        raise ValueError(f"A column named '{new_name}' already exists.")
    return df.rename(columns={col: new_name})


# =========================================================================
# Dtype Transformer
# =========================================================================

DTYPE_OPTIONS = [
    "int64", "float64", "string", "category", "bool",
    "datetime64[ns]", "date", "time", "timedelta64[ns]",
]


def preview_dtype_conversion(series: pd.Series, new_dtype: str) -> dict:
    """Preview the effect of converting *series* to *new_dtype*. Pure, no mutation."""
    total = len(series)
    n_null_before = int(series.isna().sum())

    try:
        if new_dtype == "datetime64[ns]":
            # Robust parser (day-first detection + explicit-format retries):
            # a bare pd.to_datetime month/day-swaps day<=12 values and nulls
            # day>12 values for DD/MM/YYYY columns like transaction_date.
            converted = _parse_datetime_robust(series)
        elif new_dtype == "date":
            converted = _parse_datetime_robust(series).dt.date
        elif new_dtype == "time":
            src = series.astype(str).str.strip()
            parsed = pd.to_datetime("1970-01-01 " + src, errors="coerce")
            mask_failed = parsed.isna()
            if mask_failed.any():
                parsed[mask_failed] = pd.to_datetime(src[mask_failed], errors="coerce")
            still_failed = parsed.isna()
            if still_failed.any():
                for fmt in ("%I:%M %p", "%I:%M:%S %p", "%I %p"):
                    remaining = still_failed & parsed.isna()
                    if not remaining.any():
                        break
                    parsed[remaining] = pd.to_datetime(
                        "1970-01-01 " + src[remaining], format=f"1970-01-01 {fmt}", errors="coerce")
            converted = parsed.dt.strftime("%H:%M:%S").where(parsed.notna(), other=None)
        elif new_dtype == "timedelta64[ns]":
            src = series
            if total > 0 and isinstance(series.iloc[0], datetime.time):
                src = series.apply(
                    lambda v: f"{v.hour}:{v.minute:02d}:{v.second:02d}"
                    if isinstance(v, datetime.time) else str(v))
            converted = pd.to_timedelta(src.astype(str), errors="coerce")
        elif new_dtype in ("string", "object"):
            converted = series.astype(str)
        elif new_dtype == "category":
            converted = series.astype("category")
        elif new_dtype == "bool":
            src = series.astype(str).str.strip().str.lower()
            converted = src.map({"true": True, "1": True, "yes": True,
                                  "false": False, "0": False, "no": False})
        elif new_dtype in ("int64", "float64"):
            converted = pd.to_numeric(series, errors="coerce").astype(new_dtype)
        else:
            converted = series.astype(new_dtype)
    except Exception as exc:
        return {"error": str(exc)}

    n_null_after = int(pd.Series(converted).isna().sum())
    new_nulls = max(0, n_null_after - n_null_before)
    success = total - new_nulls
    pct = round((success / total) * 100, 1) if total else 0.0

    idx_fail = pd.Series(converted).isna() & series.notna()
    sample_idx = list(range(min(6, total)))
    fail_idx = [i for i in idx_fail[idx_fail].index[:3] if i not in sample_idx]
    sample_idx = list(dict.fromkeys(sample_idx + fail_idx))[:8]

    before_vals = series.iloc[sample_idx].reset_index(drop=True)
    after_vals = pd.Series(converted).iloc[sample_idx].reset_index(drop=True)
    sample_df = pd.DataFrame({
        "Before": before_vals.astype(str),
        "After": after_vals.astype(str).replace({"None": "⚠️ null", "NaT": "⚠️ null", "nan": "⚠️ null"}),
    })

    try:
        dtype_after = str(pd.Series(converted).dtype)
    except Exception:
        dtype_after = new_dtype

    return {
        "total": total, "success": success, "new_nulls": new_nulls,
        "pct": pct, "sample_df": sample_df, "dtype_after": dtype_after,
        "converted": converted,
    }


def apply_dtype_conversion(df: pd.DataFrame, col: str, new_dtype: str) -> pd.DataFrame:
    """Apply a dtype conversion to *col* and return a new DataFrame."""
    result = preview_dtype_conversion(df[col], new_dtype)
    if "error" in result:
        raise ValueError(result["error"])
    out = df.copy()
    out[col] = result["converted"]
    return out


def classify_columns(df: pd.DataFrame) -> dict:
    """Auto-classify columns into numeric / categorical / datetime."""
    all_cols = df.columns.tolist()
    auto_dt = df.select_dtypes(include=["datetime", "datetimetz", "timedelta"]).columns.tolist()
    for col in df.select_dtypes(include=["object"]):
        if len(df) > 0 and isinstance(df[col].iloc[0], (datetime.date, datetime.time)):
            if col not in auto_dt:
                auto_dt.append(col)
    auto_num = [c for c in df.select_dtypes(include=[np.number]).columns if c not in auto_dt]
    auto_cat = [c for c in all_cols if c not in auto_num and c not in auto_dt]
    return {"numeric": auto_num, "categorical": auto_cat, "datetime": auto_dt}


# =========================================================================
# Data Cleaner -- text clean, find/replace, validate, string ops, numeric
# =========================================================================

def text_clean_preview(series: pd.Series, ops: dict) -> pd.Series:
    """Apply the selected text-cleaning ops (see keys below) to *series*."""
    s = series.astype(str).copy()
    if ops.get("strip"):    s = s.str.strip()
    if ops.get("lower"):    s = s.str.lower()
    if ops.get("upper"):    s = s.str.upper()
    if ops.get("title"):    s = s.str.title()
    if ops.get("rm_html"):  s = s.str.replace(r"<[^>]+>", "", regex=True)
    if ops.get("unicode"):
        s = s.apply(lambda v: unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode("ascii"))
    if ops.get("rm_newlines"): s = s.str.replace(r"[\r\n\t]", " ", regex=True)
    if ops.get("rm_special"):  s = s.str.replace(r"[^a-zA-Z0-9\s]", "", regex=True)
    if ops.get("alphanumeric"): s = s.str.replace(r"[^a-zA-Z0-9]", "", regex=True)
    if ops.get("rm_digits"):  s = s.str.replace(r"\d", "", regex=True)
    if ops.get("collapse_spaces"): s = s.str.replace(r"\s{2,}", " ", regex=True).str.strip()
    if ops.get("empty_to_na"): s = s.replace({"": np.nan, "nan": np.nan, "None": np.nan})
    return s


def find_replace_preview(series: pd.Series, find: str, replace: str,
                          use_regex: bool = False, case_insensitive: bool = False) -> pd.Series:
    """Replace *find* with *replace* in *series*.

    Guards against the empty-find footgun (replacing "" inserts between
    every character) and handles the case-insensitive + literal combo,
    which pandas's ``str.replace(regex=False, flags=IGNORECASE)`` rejects.
    """
    find = "" if find is None else str(find)
    replace = "" if replace is None else str(replace)
    if not find:
        return series.astype(str)
    s = series.astype(str)
    if use_regex:
        return s.str.replace(find, replace, regex=True,
                             flags=re.IGNORECASE if case_insensitive else 0)
    if case_insensitive:
        # Literal replace + case-insensitive: pandas rejects flags with
        # regex=False, so escape the literal and take the regex path.
        return s.str.replace(re.escape(find), replace, regex=True, flags=re.IGNORECASE)
    return s.str.replace(find, replace, regex=False)


VALIDATION_PATTERNS = {
    "Email address": r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
    "Phone (intl. +XX)": r"^\+?[0-9\s\-\(\)]{7,20}$",
    "UK postcode": r"^[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}$",
    "US ZIP code": r"^\d{5}(-\d{4})?$",
    "Integer number": r"^-?\d+$",
    "Decimal number": r"^-?\d+(\.\d+)?$",
    "Date (YYYY-MM-DD)": r"^\d{4}-\d{2}-\d{2}$",
    "URL (http/https)": r"^https?://[^\s]+$",
    "UUID": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
}


def validate_column(df: pd.DataFrame, col: str, pattern: str) -> dict:
    """Validate *col* against a regex *pattern*. Returns pass/fail counts + failing rows."""
    series = df[col].dropna().astype(str)
    mask_pass = series.str.match(pattern, case=True, na=False)
    mask_fail = ~mask_pass
    fail_idx = series[mask_fail].index
    return {
        "n_pass": int(mask_pass.sum()),
        "n_fail": int(mask_fail.sum()),
        "n_null": int(df[col].isna().sum()),
        "total": len(df),
        "fail_rows": df.loc[fail_idx[:20], [col]],
        "fail_index": list(fail_idx),
    }


def validate_replace_failures_with_nan(df: pd.DataFrame, col: str, fail_index: list) -> pd.DataFrame:
    out = df.copy()
    new_series = out[col].astype(object)
    new_series[fail_index] = np.nan
    out[col] = new_series
    return out


def validate_flag_column(df: pd.DataFrame, col: str, pattern: str) -> pd.DataFrame:
    out = df.copy()
    out[f"_valid_{col}"] = out[col].astype(str).str.match(pattern, case=True, na=False)
    return out


def numeric_clean(series: pd.Series, op: str, **kwargs) -> pd.Series:
    """Apply a numeric-cleaning operation. See *op* options below."""
    s = pd.to_numeric(series, errors="coerce")
    if op == "clamp":
        return s.clip(lower=kwargs.get("min"), upper=kwargs.get("max"))
    if op in ("iqr_median", "iqr_mean"):
        mult = kwargs.get("iqr_multiplier", 1.5)
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        lo, hi = q1 - mult * iqr, q3 + mult * iqr
        fill = s.median() if op == "iqr_median" else s.mean()
        out = s.copy()
        out[(s < lo) | (s > hi)] = fill
        return out
    if op == "round":
        return s.round(int(kwargs.get("decimals", 2)))
    if op == "minmax":
        mn, mx = s.min(), s.max()
        return (s - mn) / (mx - mn) if mx > mn else s * 0
    if op == "zscore":
        return (s - s.mean()) / s.std()
    if op == "fillna_median":
        return s.fillna(s.median())
    if op == "fillna_mean":
        return s.fillna(s.mean())
    if op == "fillna_value":
        return s.fillna(kwargs.get("value", 0.0))
    raise ValueError(f"Unknown numeric_clean op: {op}")


def apply_column_update(df: pd.DataFrame, col: str, new_series: pd.Series) -> pd.DataFrame:
    """Return a copy of *df* with *col* replaced by *new_series* (generic apply helper)."""
    out = df.copy()
    out[col] = new_series
    return out
