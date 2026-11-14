"""services/transform_service.py -- Headless data transformation & cleaning.

Pure functions only: every function takes a DataFrame (and plain
arguments) and returns either a result dict/DataFrame or a NEW
DataFrame. Nothing here touches global state, Streamlit, or the Qt
event loop -- callers (desktop/pages/upload_page.py) are responsible
for pushing any resulting DataFrame back through
``SessionBridge.set_df()``.

The whole module is **Polars-native**: every function takes and returns
``pl.DataFrame`` / ``pl.Series``. pandas is not used here at all -- the
only place pandas survives in this application is reading Excel files,
which Polars cannot parse.

Ported from the original Streamlit modules:
  modules/ui/column_manager.py -> add/remove/rename column
  modules/ui/column_tools.py   -> dtype preview/convert, column classify
  modules/ui/data_cleaner.py   -> text clean, find/replace, validate,
                                    string ops, numeric clean
"""

from __future__ import annotations

import ast
import datetime
import logging
import operator
import re
import unicodedata
from typing import Any, Optional

import polars as pl

log = logging.getLogger(__name__)

# =========================================================================
# Column Manager -- add / remove / rename
# =========================================================================

_SAFE_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_SAFE_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_formula_eval(df: pl.DataFrame, formula: str) -> pl.Series:
    """Evaluate a derived-column arithmetic formula, e.g. ``` `Sales` / `Units` ```.

    The grammar is the app's safe subset: numeric columns, numeric constants
    and the four arithmetic operators. Function calls, attributes, indexing
    and every other Python syntax are rejected -- a formula is typed by the
    user into a text box, and it must never be able to call anything.
    """
    if df is None:
        raise ValueError("No data to evaluate the formula against.")
    numeric_cols = {c for c in df.columns
                    if df[c].dtype.is_numeric()}
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
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return pl.lit(node.value)
        if isinstance(node, ast.Name):
            col = column_aliases.get(node.id, node.id)
            if col not in df.columns:
                raise ValueError(f"Unknown column: {col}")
            if col not in numeric_cols:
                raise ValueError(f"Column '{col}' is not numeric.")
            return pl.col(col).cast(pl.Float64, strict=False)
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BIN_OPS:
            return _SAFE_BIN_OPS[type(node.op)](eval_node(node.left),
                                                eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_UNARY_OPS:
            return _SAFE_UNARY_OPS[type(node.op)](eval_node(node.operand))
        raise ValueError("Only arithmetic with numeric columns and constants is allowed.")

    result = df.select(eval_node(tree).alias("__formula_result"))
    return result.get_column("__formula_result").rename("")


_SLASH_DATE_RE = r"^\s*(\d{1,2})[/\-](\d{1,2})[/\-]\d{2,4}(?:\s.*)?$"


def _detect_dayfirst(s: pl.Series) -> Optional[bool]:
    """Infer day-first vs month-first layout from slash/dash date strings.

    If any first component exceeds 12 the column must be day-first
    (e.g. '13/01/2023' present); if only the second component ever exceeds
    12 it must be month-first ('01/13/2023'); otherwise ambiguous (None) and
    the month-first default is kept.
    """
    try:
        first = s.str.extract(_SLASH_DATE_RE, 1).cast(pl.Float64, strict=False)
        second = s.str.extract(_SLASH_DATE_RE, 2).cast(pl.Float64, strict=False)
    except Exception as exc:
        log.debug("_detect_dayfirst failed: %s", exc)
        return None
    if first.is_empty() or first.null_count() == len(first):
        return None
    a_gt12 = bool((first.drop_nulls() > 12).any())
    b_gt12 = bool((second.drop_nulls() > 12).any())
    if a_gt12 and not b_gt12:
        return True   # day-first, e.g. transaction_date '01/01/2023'..'30/06/2023'
    if b_gt12 and not a_gt12:
        return False  # month-first
    return None       # ambiguous -> month-first default


def _parse_datetime_robust(series: Any) -> pl.Series:
    """Parse a column as datetimes, trying every layout the app has seen.

    A bare month-first parse turns every DD/MM/YYYY value with day>12 into
    null and silently month/day-swaps every day<=12 value -- the "date
    conversion not working" bug on a transaction_date column where 39% of the
    values came out null. So the layout is detected first, and the explicit
    formats are retried for whatever is still unparsed.
    """
    if series is None:
        return pl.Series([], dtype=pl.Datetime)
    if not isinstance(series, pl.Series):
        series = pl.Series(series)
    if series.dtype in (pl.Date, pl.Datetime):
        return series.cast(pl.Datetime)
    # Numeric-only columns (e.g. transaction_id) can never be dates; skip
    # parsing entirely so no epoch interpretation is ever attempted.
    if series.dtype.is_numeric() or series.dtype == pl.Boolean:
        return pl.Series([None] * len(series), dtype=pl.Datetime)

    s = series.cast(pl.Utf8).str.strip_chars()
    dayfirst = _detect_dayfirst(s)

    result = pl.Series([None] * len(s), dtype=pl.Datetime)
    # First pass: an explicit format, in the detected order. Explicit formats
    # are unambiguous, so they are tried before the loose parser.
    date_fmts = (("%d/%m/%Y", "%m/%d/%Y") if dayfirst
                 else ("%m/%d/%Y", "%d/%m/%Y"))
    for fmt in date_fmts:
        result = _fill_datetimes(result, s.str.to_datetime(fmt, strict=False))
        if result.null_count() == 0:
            return result

    # Second pass: the loose parser (handles "2019-04-22 16:02:01" and
    # ISO-8601 with a T).
    result = _fill_datetimes(result, s.str.to_datetime(strict=False))
    if result.null_count() == 0:
        return result

    # Third pass: the time-oriented formats.
    normalised = (s.str.to_uppercase()
                  .str.replace(r"([AP]M)$", r" \1")
                  .str.replace(r"\s{2,}", " "))
    for fmt in ("%I:%M %p", "%I:%M:%S %p", "%I:%M%p", "%I:%M:%S%p", "%I %p",
                "%m/%d/%Y %I:%M %p", "%d/%m/%Y %I:%M %p",
                "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            parsed = normalised.str.to_datetime(fmt, strict=False)
        except Exception:
            continue
        result = _fill_datetimes(result, parsed)
        if result.null_count() == 0:
            return result
    return result


def _fill_datetimes(current: pl.Series, parsed: pl.Series) -> pl.Series:
    """Fill the still-null entries of *current* from *parsed*."""
    if parsed is None or len(parsed) != len(current):
        return current
    if parsed.dtype not in (pl.Date, pl.Datetime):
        return current
    return current.fill_null(parsed.cast(pl.Datetime))


def date_diff_column(df: Any, col_a: str, col_b: Optional[str],
                      unit: str) -> Any:
    """Compute a date-difference derived column. *col_b* None means "today"."""
    is_pandas = not isinstance(df, pl.DataFrame)
    pldf = pl.from_pandas(df) if is_pandas else df
    # Use the robust parser (multi-format, coerce) -- a naive parse tries to
    # interpret numeric columns as epoch timestamps and nulls the lot.
    series_a = _parse_datetime_robust(pldf[col_a])
    series_b = (
        pl.Series([datetime.datetime.now()] * pldf.height, dtype=pl.Datetime)
        if col_b is None else _parse_datetime_robust(pldf[col_b])
    )

    for name, series in ((col_a, series_a), (col_b, series_b)):
        if col_b is None and name == col_b:
            continue  # "today" sentinel -- nothing to validate
        if series.null_count() == len(series):
            raise ValueError(
                f"Column '{name}' doesn't look like a date column. "
                f"Pick a column containing dates/times."
            )

    if unit == "Days":
        res = (series_b - series_a).dt.total_days().cast(pl.Int64)
    elif unit == "Months":
        result = ((series_b.dt.year() - series_a.dt.year()) * 12
                  + (series_b.dt.month() - series_a.dt.month()))
        adj = (series_b.dt.day() < series_a.dt.day()).cast(pl.Int64)
        res = (result - adj).cast(pl.Int64)
    elif unit in ("Years", "Age (Years from DOB vs Today)"):
        res = (
            (series_b.dt.year() - series_a.dt.year()).cast(pl.Float64)
            + (series_b.dt.month() - series_a.dt.month()).cast(pl.Float64) / 12.0
            + (series_b.dt.day() - series_a.dt.day()).cast(pl.Float64) / 365.0
        )
    elif unit == "Previous Year (subtract 1 year)":
        res = series_a.dt.offset_by("-1y")
    elif unit == "Previous Month (subtract 1 month)":
        res = series_a.dt.offset_by("-1mo")
    else:
        res = (series_b - series_a).dt.total_days()
    return res.to_pandas() if is_pandas else res


DATE_PART_OPTIONS = [
    "Date (YYYY-MM-DD)", "Year", "Quarter", "Month (Number)", "Month Name",
    "Week Number", "Day", "Weekday Name", "Hour (12h AM/PM)", "Hour (24h)",
]


def extract_date_part(df: pl.DataFrame, col: str, part: str) -> pl.Series:
    """Extract a date/time component (year, month name, etc.) from *col*."""
    dt = _parse_datetime_robust(df[col])
    mapping = {
        "Date (YYYY-MM-DD)": dt.dt.date(),
        "Year": dt.dt.year().cast(pl.Int64),
        "Quarter": dt.dt.quarter().cast(pl.Int64),
        "Month (Number)": dt.dt.month().cast(pl.Int64),
        "Month Name": dt.dt.strftime("%B"),
        "Week Number": dt.dt.week().cast(pl.Int64),
        "Day": dt.dt.day().cast(pl.Int64),
        "Weekday Name": dt.dt.strftime("%A"),
        "Hour (12h AM/PM)": dt.dt.strftime("%I %p"),
        "Hour (24h)": dt.dt.hour().cast(pl.Int64),
    }
    if part not in mapping:
        raise ValueError(f"Unknown date part: {part}")
    return mapping[part]


def add_column_op(df: pl.DataFrame, col_a: str, col_b: str, op: str) -> pl.Series:
    """Compute Column [op] Column (op is one of '*','/','+','-')."""
    return safe_formula_eval(df, f"`{col_a}` {op} `{col_b}`")


def moving_average_column(df: pl.DataFrame, col: str, window: int) -> pl.Series:
    """Rolling moving average over *window* rows ending at the current row.

    ``min_periods=1`` so the first rows get a partial-window average instead
    of null; non-numeric values are coerced to null.
    """
    window = max(int(window), 1)
    numeric = df[col].cast(pl.Float64, strict=False)
    return numeric.rolling_mean(window_size=window, min_periods=1)


def add_derived_column(df: pl.DataFrame, new_col_name: str,
                       series: pl.Series) -> pl.DataFrame:
    """Return a copy of *df* with a new column added."""
    return df.with_columns(series.rename(new_col_name).alias(new_col_name))


def remove_column(df: pl.DataFrame, col: str) -> pl.DataFrame:
    """Return a copy of *df* with *col* dropped."""
    return df.drop(col)


def rename_column(df: pl.DataFrame, col: str, new_name: str) -> pl.DataFrame:
    """Return a copy of *df* with *col* renamed to *new_name*."""
    if new_name in df.columns:
        raise ValueError(f"A column named '{new_name}' already exists.")
    return df.rename({col: new_name})


# =========================================================================
# Dtype Transformer
# =========================================================================

DTYPE_OPTIONS = [
    "int64", "float64", "string", "category", "bool",
    "datetime64[ns]", "date", "time", "timedelta64[ns]",
]


def preview_dtype_conversion(series: pl.Series, new_dtype: str) -> dict:
    """Preview the effect of converting *series* to *new_dtype*. Pure, no mutation."""
    total = len(series)
    n_null_before = int(series.null_count())

    try:
        if new_dtype == "datetime64[ns]":
            # Robust parser (day-first detection + explicit-format retries):
            # a bare month-first parse swaps day<=12 values and nulls day>12
            # values for DD/MM/YYYY columns like transaction_date.
            converted = _parse_datetime_robust(series)
        elif new_dtype == "date":
            converted = _parse_datetime_robust(series).dt.date()
        elif new_dtype == "time":
            # A real TIME, not a strftime string: 'time' must be a usable
            # time type downstream -- the Analysis time filters and
            # classify_columns() both key off real time values. A strftime
            # result is just text and silently demotes the column back to the
            # text family.
            converted = _parse_time_of_day(series)
        elif new_dtype == "timedelta64[ns]":
            converted = _parse_timedelta(series)
        elif new_dtype in ("string", "object"):
            converted = series.cast(pl.Utf8)
        elif new_dtype == "category":
            converted = series.cast(pl.Categorical)
        elif new_dtype == "bool":
            converted = _parse_bool(series)
        elif new_dtype in ("int64", "float64"):
            numeric = series.cast(pl.Float64, strict=False)
            converted = (numeric.cast(pl.Int64, strict=False)
                         if new_dtype == "int64" else numeric)
        else:
            converted = series
    except Exception as exc:
        return {"error": str(exc)}

    n_null_after = int(converted.null_count())
    new_nulls = max(0, n_null_after - n_null_before)
    success = total - new_nulls
    pct = round((success / total) * 100, 1) if total else 0.0

    # The rows that were fine before and became null after -- these are the
    # rows the user needs to see to understand the conversion.
    failed_mask = (converted.is_null() & series.is_not_null())
    fail_positions = [i for i, bad in enumerate(failed_mask.to_list()) if bad][:3]
    sample_positions = list(range(min(6, total)))
    sample_positions = list(dict.fromkeys(sample_positions + fail_positions))[:8]

    before_vals = [str(v) for v in series[sample_positions].to_list()]
    after_vals = [_display_value(v) for v in converted[sample_positions].to_list()]
    sample_df = pl.DataFrame({"Before": before_vals, "After": after_vals})

    return {
        "total": total, "success": success, "new_nulls": new_nulls,
        "pct": pct, "sample_df": sample_df,
        "dtype_after": str(converted.dtype),
        "converted": converted,
    }


def _display_value(value) -> str:
    """Render one value for the preview table, flagging nulls."""
    if value is None:
        return "⚠️ null"
    try:
        if isinstance(value, float) and value != value:   # NaN
            return "⚠️ null"
    except Exception:
        pass
    return str(value)


def _merge_series(current: pl.Series, parsed: pl.Series) -> pl.Series:
    """Fill the still-null entries of *current* from *parsed*.

    Done at the frame level: a ``Series.when`` does not exist, and the
    frame-level ``when/then/otherwise`` handles the null-preserving merge
    that a dtype like ``Time`` needs.
    """
    if parsed is None or len(parsed) != len(current):
        return current
    frame = pl.DataFrame({"__current": current, "__parsed": parsed})
    return frame.select(
        pl.when(pl.col("__current").is_null() & pl.col("__parsed").is_not_null())
        .then(pl.col("__parsed"))
        .otherwise(pl.col("__current"))
        .alias("value")
    ).to_series()


def _parse_time_of_day(series: pl.Series) -> pl.Series:
    """Parse a column of times ("16:02:01", "4:02 PM") into a TIME."""
    if series.dtype == pl.Time:
        return series
    src = series.cast(pl.Utf8).str.strip_chars()
    result = pl.Series([None] * len(src), dtype=pl.Time)
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p",
                "%I %p", "%I:%M%p", "%I:%M:%S%p"):
        try:
            parsed = src.str.to_time(fmt, strict=False)
        except Exception:
            continue
        # Each format only matches its own shape, so the results are merged:
        # the first format that parses a value wins.
        result = _merge_series(result, parsed)
        if result.null_count() == 0:
            break
    return result


def _parse_timedelta(series: pl.Series) -> pl.Series:
    """Parse a column of durations into a DURATION."""
    if series.dtype == pl.Duration:
        return series
    src = series.cast(pl.Utf8).str.strip_chars()
    return src.str.to_duration(strict=False)


def _parse_bool(series: pl.Series) -> pl.Series:
    """Parse a column of truthy/falsy text into a BOOLEAN.

    Anything that is not a recognised truthy or falsy word becomes null --
    the same rule the old implementation used, where an unrecognised value
    was simply absent from the truth/false maps.
    """
    if series.dtype == pl.Boolean:
        return series
    src = series.cast(pl.Utf8).str.strip_chars().str.to_lowercase()
    truthy = src.is_in(["true", "1", "yes", "y", "t"])
    falsy = src.is_in(["false", "0", "no", "n", "f"])
    known = truthy | falsy
    return pl.DataFrame({"__known": known, "__truthy": truthy}).select(
        pl.when(pl.col("__known"))
        .then(pl.col("__truthy"))
        .otherwise(None)
        .alias("value")
    ).to_series()


def apply_dtype_conversion(df: pl.DataFrame, col: str, new_dtype: str) -> pl.DataFrame:
    """Apply a dtype conversion to *col* and return a new DataFrame."""
    result = preview_dtype_conversion(df[col], new_dtype)
    if "error" in result:
        raise ValueError(result["error"])
    return df.with_columns(result["converted"].alias(col))


def classify_columns(df: pl.DataFrame) -> dict:
    """Auto-classify columns into numeric / categorical / datetime.

    A text column whose values parse as dates counts as datetime-family: a
    row-0-only probe misses columns whose first value is null or an outlier,
    so up to 20 non-null values per column are probed.
    """
    all_cols = list(df.columns)
    auto_dt: list[str] = []

    for col in df.columns:
        dtype = df[col].dtype
        if dtype in (pl.Date, pl.Datetime, pl.Time, pl.Duration):
            auto_dt.append(col)
            continue
        if dtype != pl.Utf8:
            continue
        if _looks_temporal(df[col]):
            auto_dt.append(col)

    auto_num = [c for c in all_cols
                if df[c].dtype.is_numeric() and c not in auto_dt]
    auto_cat = [c for c in all_cols if c not in auto_num and c not in auto_dt]
    return {"numeric": auto_num, "categorical": auto_cat, "datetime": auto_dt}


def _looks_temporal(series: pl.Series) -> bool:
    """True if most non-null values in *series* are date/time-like."""
    non_null = series.drop_nulls()
    if non_null.is_empty():
        return False
    sample = non_null.head(20)
    hits = sum(1 for v in sample.to_list()
               if isinstance(v, (datetime.date, datetime.time, datetime.datetime)))
    if sample.len() and hits / sample.len() >= 0.5:
        return True
    # Fall back to a strict parse probe on strings: only trust it when
    # EVERY probed value parses (avoids false-positives on free text).
    if sample.dtype != pl.Utf8:
        return False
    parsed = sample.str.to_date("%Y-%m-%d", strict=False)
    return parsed.null_count() == 0


# =========================================================================
# Data Cleaner -- text clean, find/replace, validate, string ops, numeric
# =========================================================================

def text_clean_preview(series: Any, ops: dict) -> Any:
    """Apply the selected text-cleaning ops (see keys below) to *series*."""
    is_pandas = not isinstance(series, pl.Series)
    s = (pl.Series(series) if is_pandas else series).cast(pl.Utf8)
    if ops.get("strip"):    s = s.str.strip_chars()
    if ops.get("lower"):    s = s.str.to_lowercase()
    if ops.get("upper"):    s = s.str.to_uppercase()
    if ops.get("title"):    s = s.str.to_titlecase()
    if ops.get("rm_html"):  s = s.str.replace_all(r"<[^>]+>", "")
    if ops.get("unicode"):
        s = pl.Series([_strip_accents(v) for v in s.to_list()],
                      dtype=pl.Utf8)
    if ops.get("rm_newlines"): s = s.str.replace_all(r"[\r\n\t]", " ")
    if ops.get("rm_special"):  s = s.str.replace_all(r"[^a-zA-Z0-9\s]", "")
    if ops.get("alphanumeric"): s = s.str.replace_all(r"[^a-zA-Z0-9]", "")
    if ops.get("rm_digits"):  s = s.str.replace_all(r"\d", "")
    if ops.get("collapse_spaces"):
        s = s.str.replace_all(r"\s{2,}", " ").str.strip_chars()
    if ops.get("empty_to_na"):
        s = pl.DataFrame({"__value": s}).select(
            pl.when(pl.col("__value").is_in(["", "nan", "None"]))
            .then(None).otherwise(pl.col("__value")).alias("__value")
        ).to_series()
    return s.to_pandas() if is_pandas else s


def _strip_accents(value: str) -> str:
    """NFKD-normalise *value* and drop the combining marks."""
    try:
        return (unicodedata.normalize("NFKD", str(value))
                .encode("ascii", "ignore").decode("ascii"))
    except Exception:
        return str(value)


def find_replace_preview(series: Any, find: str, replace: str,
                         use_regex: bool = False,
                         case_insensitive: bool = False) -> Any:
    """Replace *find* with *replace* in *series*.

    Guards against the empty-find footgun (replacing "" inserts between
    every character) and handles the case-insensitive + literal combo, which
    a literal replace cannot express on its own.
    """
    find = "" if find is None else str(find)
    replace = "" if replace is None else str(replace)
    is_pandas = not isinstance(series, pl.Series)
    s = (pl.Series(series) if is_pandas else series).cast(pl.String)
    if not find:
        return s.to_pandas() if is_pandas else s
    if use_regex:
        pat = ("(?i)" + find) if case_insensitive else find
        res = s.str.replace_all(pat, replace)
    elif case_insensitive:
        res = s.str.replace_all("(?i)" + re.escape(find), replace)
    else:
        res = s.str.replace_all(find, replace, literal=True)
    return res.to_pandas() if is_pandas else res


VALIDATION_PATTERNS = {
    "Email address": r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
    "Phone (intl. +XX)": r"^\+?[0-9\s\-\(\)]{7,20}$",
    "UK postcode": r"^[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}$",
    "US ZIP code": r"^\d{5}(-\d{4})?$",
    "Integer number": r"^-?\d+$",
    "Decimal number": r"^-?\d+(\.\d+)?$",
    "Date (YYYY-MM-DD)": r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$",
    "Date (MM/DD/YYYY) — US": r"^(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/\d{4}$",
    "Date (DD/MM/YYYY) — Europe": r"^(0[1-9]|[12]\d|3[01])/(0[1-9]|1[0-2])/\d{4}$",
    "Date (YYYY/MM/DD) — Asian": r"^\d{4}/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])$",
    "Date (DD-MM-YYYY)": r"^(0[1-9]|[12]\d|3[01])-(0[1-9]|1[0-2])-\d{4}$",
    "Date (MM-DD-YYYY)": r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-\d{4}$",
    "Date (DD.MM.YYYY)": r"^(0[1-9]|[12]\d|3[01])\.(0[1-9]|1[0-2])\.\d{4}$",
    "Date (any common format)": r"^(?:\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])|(?:0[1-9]|1[0-2])/(?:0[1-9]|[12]\d|3[01])/\d{4}|(?:0[1-9]|[12]\d|3[01])/(?:0[1-9]|1[0-2])/\d{4}|\d{4}/(?:0[1-9]|1[0-2])/(?:0[1-9]|[12]\d|3[01])|(?:0[1-9]|[12]\d|3[01])-(?:0[1-9]|1[0-2])-\d{4}|(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])-\d{4}|(?:0[1-9]|[12]\d|3[01])\.(?:0[1-9]|1[0-2])\.\d{4})$",
    "URL (http/https)": r"^https?://[^\s]+$",
    "UUID": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
}


def validate_column(df: Any, col: str, pattern: str) -> dict:
    """Validate *col* against a regex *pattern*. Returns pass/fail counts + failing rows."""
    is_pandas = not isinstance(df, pl.DataFrame)
    pldf = pl.from_pandas(df) if is_pandas else df
    series = pldf[col]
    text = series.cast(pl.Utf8)
    # A null is a null, not a failure: the nulls are counted separately.
    mask_pass = (text.str.contains(pattern, literal=False) | text.is_null())
    mask_fail = ~mask_pass
    fail_positions = [i for i, bad in enumerate(mask_fail.to_list()) if bad]

    fail_rows_pl = pldf.select(pl.col(col))[[i for i in fail_positions[:20]]]
    return {
        "n_pass": int(mask_pass.sum()) - int(text.is_null().sum()),
        "n_fail": len(fail_positions),
        "n_null": int(text.is_null().sum()),
        "total": pldf.height,
        "fail_rows": fail_rows_pl.to_pandas() if is_pandas else fail_rows_pl,
        "fail_index": fail_positions,
    }


def validate_replace_failures_with_nan(df: Any, col: str,
                                      fail_index: list) -> Any:
    """Blank out the rows that failed validation (they become null)."""
    is_pandas = not isinstance(df, pl.DataFrame)
    pldf = pl.from_pandas(df) if is_pandas else df
    positions = [int(i) for i in (fail_index or [])
                 if 0 <= int(i) < pldf.height]
    if not positions:
        return df
    series = pldf[col]
    # A null for the failing rows, the original value everywhere else. Done
    # at the frame level so the column keeps its own dtype.
    blanks = pl.DataFrame({"__value": series}).select(
        pl.when(pl.Series([i in set(positions) for i in range(pldf.height)]))
        .then(None).otherwise(pl.col("__value")).alias("__value")
    ).to_series()
    res = pldf.with_columns(blanks.alias(col))
    return res.to_pandas() if is_pandas else res


def validate_flag_column(df: Any, col: str, pattern: str) -> Any:
    """Add a boolean ``_valid_<col>`` column marking the passing rows."""
    is_pandas = not isinstance(df, pl.DataFrame)
    pldf = pl.from_pandas(df) if is_pandas else df
    text = pldf[col].cast(pl.Utf8)
    flag = (text.str.contains(pattern, literal=False)
            & text.is_not_null())
    res = pldf.with_columns(flag.alias(f"_valid_{col}"))
    return res.to_pandas() if is_pandas else res


def numeric_clean(series: Any, op: str, **kwargs) -> Any:
    """Apply a numeric-cleaning operation. See *op* options below."""
    is_pandas = not isinstance(series, pl.Series)
    s = (pl.Series(series) if is_pandas else series).cast(pl.Float64, strict=False)
    if op == "clamp":
        low = kwargs.get("min")
        high = kwargs.get("max")
        out = s
        if low is not None:
            out = out.clip(lower_bound=float(low))
        if high is not None:
            out = out.clip(upper_bound=float(high))
        return out.to_pandas() if is_pandas else out
    if op in ("iqr_median", "iqr_mean"):
        mult = kwargs.get("iqr_multiplier", 1.5)
        q1 = s.quantile(0.25)
        q3 = s.quantile(0.75)
        if q1 is None or q3 is None:
            return series
        iqr = q3 - q1
        lo, hi = q1 - mult * iqr, q3 + mult * iqr
        fill = s.median() if op == "iqr_median" else s.mean()
        outliers = (s < lo) | (s > hi)
        res = pl.DataFrame({"__value": s, "__outlier": outliers}).select(
            pl.when(pl.col("__outlier"))
            .then(pl.lit(fill, dtype=pl.Float64))
            .otherwise(pl.col("__value"))
            .alias("value")
        ).to_series()
        return res.to_pandas() if is_pandas else res
    if op == "round":
        res = s.round(int(kwargs.get("decimals", 2)))
        return res.to_pandas() if is_pandas else res
    if op == "minmax":
        mn, mx = s.min(), s.max()
        if mx is None or mn is None or mx <= mn:
            res = s * 0
        else:
            res = (s - mn) / (mx - mn)
        return res.to_pandas() if is_pandas else res
    if op == "zscore":
        std = s.std()
        if not std:
            res = s * 0
        else:
            res = (s - s.mean()) / std
        return res.to_pandas() if is_pandas else res
    if op == "fillna_median":
        res = s.fill_null(s.median())
        return res.to_pandas() if is_pandas else res
    if op == "fillna_mean":
        res = s.fill_null(s.mean())
        return res.to_pandas() if is_pandas else res
    if op == "fillna_value":
        res = s.fill_null(kwargs.get("value", 0.0))
        return res.to_pandas() if is_pandas else res
    raise ValueError(f"Unknown numeric_clean op: {op}")


def apply_column_update(df: pl.DataFrame, col: str,
                         new_series: pl.Series) -> pl.DataFrame:
    """Return a copy of *df* with *col* replaced by *new_series* (generic apply helper)."""
    return df.with_columns(new_series.rename(col).alias(col))
