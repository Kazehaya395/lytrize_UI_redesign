"""services/column_roles.py — THE single source of truth for column families (Polars-native).

Every drop-down offering a "metric", a "dimension" or a "group by" column
must answer the same question: *what kind of column is this?*

The rule implemented here, in priority order:

* **The user's explicit classification always wins.** If the user called a
  column a dimension, every drop-down in the entire app treats it as a
  dimension -- no matter what its Polars dtype says.
* **Auto-detection is the fallback**, used only when the user never
  classified the column (or it is new to the dataset).
* **A column is never dropped.** Every column ends up in exactly one
  family, so no column can become unreachable from a drop-down.
* **Self-healing after dtype conversion.** If a column's *value profile*
  changed since the user confirmed it -- e.g. the Dtype Transformer
  converted a text column to real ``date``/``time`` objects -- that one
  column is re-derived. Without this a converted date column would stay a
  "dimension" forever and no time-series chart could find it. Unchanged
  columns keep the user's choice verbatim.

This module is deliberately UI-free and side-effect-free so the Qt pages
and the headless chart services can all share it.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

import polars as pl

log = logging.getLogger(__name__)

FAMILIES = ("numeric", "categorical", "datetime")

# Rows read by _probe_non_null before falling back to a full scan. Large
# enough that a column whose first rows are well-populated is answered from
# the head alone, small enough that the probe is microseconds.
_PROBE_WINDOW = 100_000


def _probe_non_null(series: pl.Series, need: int = 20,
                    window: int = _PROBE_WINDOW) -> pl.Series:
    """First *need* non-null values, read from a bounded head of the column.

    ``series.drop_nulls()`` is O(rows): it materialises a full boolean mask
    and a full reduced copy before the caller takes its first 20 values.
    On a multi-million-row frame that runs for EVERY column on EVERY
    ``resolve_roles`` call -- which is what made selecting an analysis type
    on the sidebar stall the UI. Reading the head keeps the cost flat in
    the column length for the common case where values are densely populated.

    The fallback exists for correctness, not performance: a sparse column
    with values clustered at the tail still gets answered exactly via the
    full scan below.
    """
    head = series.head(window)
    non_null = head.drop_nulls()
    if len(non_null) >= need:
        return non_null
    return series.drop_nulls()


def _looks_like_date(sample: pl.Series) -> bool:
    """Heuristic: does a string column look like dates?"""
    if sample.is_empty():
        return False
    # Try parsing first few non-null values as dates
    try:
        import dateutil.parser
        vals = sample.to_list()
        parsed = 0
        for v in vals[:10]:
            if v is None:
                continue
            try:
                dateutil.parser.parse(str(v))
                parsed += 1
            except (ValueError, TypeError):
                pass
        return parsed >= max(1, len(vals) * 0.5)
    except Exception:
        return False


def value_sig(series: pl.Series) -> tuple:
    """Fingerprint a column's value profile (dtype + sampled value types).

    The Polars dtype alone cannot detect a date/time conversion -- a text
    column converted to real ``date``/``datetime`` objects keeps the same
    *kind* of column but changes what its values ARE -- so the sample of
    actual value types is part of the signature.

    The sample is taken through :func:`_probe_non_null`, so this stays cheap
    enough to call per column per resolve (see the cache in
    :func:`resolve_roles` for the layer above).
    """
    non_null = _probe_non_null(series, need=20)
    types = tuple(sorted({type(v).__name__
                          for v in non_null.head(20).to_list()}))
    return (str(series.dtype), types)


def value_sig_map(df: pl.DataFrame) -> Dict[Any, tuple]:
    """Capture the baseline fingerprint for every column (for self-healing)."""
    if df is None:
        return {}
    try:
        return {c: value_sig(df[c]) for c in df.columns}
    except Exception as exc:
        log.debug("value_sig_map failed: %s", exc)
        return {}


# -- Roles memo -------------------------------------------------------------
# Resolving the families is pure: same DataFrame + same user classification +
# same value signatures always produce the same answer. The Analysis page
# re-resolves on EVERY sidebar selection (and the Export path again), so the
# answer is memoised. One entry per DataFrame is enough (the session has
# exactly one live frame), and the frame is held STRONGLY so ``id()`` cannot
# be recycled onto a different object while its entry is still cached.
_ROLES_CACHE: Dict[tuple, tuple] = {}   # key -> (df, roles)


def _roles_cache_key(df: pl.DataFrame, explicit, value_sig_map):
    """A hashable identity for everything ``resolve_roles`` depends on."""
    try:
        shape = df.shape
        cols = tuple(str(c) for c in df.columns)
    except Exception:                     # exotic frame: just don't cache
        return None
    if value_sig_map is not None:
        # The signature map is an INPUT the caller computed elsewhere, so it
        # belongs in the key: two calls with different maps differ.
        try:
            sig = tuple(sorted((str(k), str(v))
                               for k, v in value_sig_map.items()))
        except Exception:
            return None
    else:
        sig = None
    # Flatten the classification to a comparable tuple (a dict is unhashable).
    cls = tuple((f, tuple(str(c) for c in (explicit or {}).get(f) or ()))
                for f in FAMILIES)
    return (id(df), shape, cols, sig, cls)


def clear_roles_cache() -> None:
    """Drop every memoised resolution (called when the DataFrame changes)."""
    _ROLES_CACHE.clear()


def resolve_roles(df: pl.DataFrame,
                  explicit: Optional[Dict[str, List[str]]] = None,
                  value_sig_map: Optional[Dict[Any, tuple]] = None
                  ) -> Dict[str, List[str]]:
    """Classify every column of *df* into numeric / categorical / datetime.

    Returns ``{"numeric": [...], "categorical": [...], "datetime": [...]}``.

    Priority: explicit user classification > auto-detection.

    *value_sig_map* is the value-profile fingerprint captured when the user
    confirmed the classification (see :func:`value_sig`). A column whose
    fingerprint has since changed is re-derived, because a dtype conversion
    (e.g. text -> real date objects) has invalidated the old answer for it.
    A column whose fingerprint is unchanged keeps the user's choice verbatim.

    The result is memoised per (frame, classification, signatures); the
    returned dict is a fresh copy each time, so a caller that appends to a
    family cannot poison the cache for the next one.
    """
    if df is None:
        return {f: [] for f in FAMILIES}

    if not isinstance(df, pl.DataFrame):
        try:
            df = pl.from_pandas(df)
        except Exception:
            return {f: [] for f in FAMILIES}

    key = _roles_cache_key(df, explicit, value_sig_map)
    if key is not None:
        hit = _ROLES_CACHE.get(key)
        if hit is not None and hit[0] is df:
            return {f: list(hit[1].get(f) or []) for f in FAMILIES}

    roles = _resolve_roles_uncached(df, explicit, value_sig_map)
    if key is not None:
        # One live frame; never grow a pile.
        _ROLES_CACHE.clear()
        # Store a COPY: ``roles`` is also handed to this caller, and a caller
        # that appends to a family must not rewrite what the next one reads.
        _ROLES_CACHE[key] = (df, {f: list(roles.get(f) or []) for f in FAMILIES})
    return roles


def _resolve_roles_uncached(df: pl.DataFrame,
                            explicit: Optional[Dict[str, List[str]]] = None,
                            value_sig_map: Optional[Dict[Any, tuple]] = None,
                            ) -> Dict[str, List[str]]:
    explicit = explicit or {"numeric": [], "categorical": [], "datetime": []}
    classified: set[str] = set()
    out: Dict[str, List[str]] = {"numeric": [], "categorical": [], "datetime": []}

    # Which columns may we trust the user's answer for? Only those whose
    # value profile has NOT moved since they confirmed it.
    trusted = _trusted_columns(df, explicit, value_sig_map)

    # 1. Apply explicit user classification first
    for fam in FAMILIES:
        for col in explicit.get(fam, []) or []:
            if (col in df.columns and col not in classified
                    and col in trusted):
                out[fam].append(col)
                classified.add(col)

    # 2. Auto-detect remaining columns from Polars schema
    schema = df.schema
    for col in df.columns:
        if col in classified:
            continue
        dtype = schema[col]

        if dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                     pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                     pl.Float32, pl.Float64):
            out["numeric"].append(col)
            classified.add(col)
        elif dtype in (pl.Date, pl.Datetime, pl.Time, pl.Duration):
            out["datetime"].append(col)
            classified.add(col)
        elif dtype == pl.Boolean:
            # Booleans are categorical
            out["categorical"].append(col)
            classified.add(col)
        elif dtype in (pl.String, pl.Categorical, pl.Enum):
            # Heuristic: look like dates?
            try:
                sample = _probe_non_null(df[col])
                if _looks_like_date(sample):
                    out["datetime"].append(col)
                else:
                    out["categorical"].append(col)
            except Exception:
                out["categorical"].append(col)
            classified.add(col)
        else:
            # Unknown dtype -> categorical (safe fallback)
            out["categorical"].append(col)
            classified.add(col)

    # 3. Preserve dataframe order so drop-downs read top-to-bottom the way
    #    the user's table does, not in family-detection order.
    order = {c: i for i, c in enumerate(df.columns)}
    for fam in FAMILIES:
        out[fam].sort(key=lambda c: order.get(c, len(order)))

    return out


def _trusted_columns(df: pl.DataFrame,
                     explicit: Optional[Dict[str, List[str]]],
                     value_sig_map: Optional[Dict[Any, tuple]]) -> set:
    """Columns whose user classification may still be honoured.

    A column named in *explicit* is trusted unless its value profile has
    changed since the user confirmed it -- a dtype conversion (e.g. the Dtype
    Transformer turning text into real ``date`` objects) invalidates the old
    answer, so that ONE column is re-derived while every other column keeps
    the user's choice verbatim.
    """
    trusted: set = set()
    for fam in FAMILIES:
        for c in (explicit or {}).get(fam) or []:
            if c in df.columns:
                trusted.add(c)

    if not value_sig_map:
        return trusted

    # Re-derive only the columns whose value profile actually moved.
    changed: set = set()
    for c in list(trusted):
        try:
            if value_sig(df[c]) != value_sig_map.get(c):
                changed.add(c)
        except Exception as exc:
            log.debug("value_sig comparison failed on %r: %s", c, exc)
    return trusted - changed


def metric_columns(roles: Dict[str, List[str]]) -> List[str]:
    """Columns offered as metrics (Y axis / value columns)."""
    seen = set()
    out: List[str] = []
    for col in roles.get("numeric", []):
        if col not in seen:
            seen.add(col)
            out.append(col)
    return out


def dimension_columns(roles: Dict[str, List[str]]) -> List[str]:
    """Columns offered as dimensions (Group by / Colour by / X axis).

    Datetime columns are included: a date is a perfectly valid grouping key,
    and it is exactly the family the "Group by" list needs in order to offer
    time as a dimension rather than forcing the user to pick text.
    """
    seen = set()
    out: List[str] = []
    for fam in ("categorical", "datetime"):
        for c in roles.get(fam) or []:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def role_pools(df: pl.DataFrame,
               roles: Optional[Dict[str, List[str]]] = None,
               explicit: Optional[Dict[str, List[str]]] = None,
               value_sig_map: Optional[Dict[Any, tuple]] = None
               ) -> Dict[str, List[str]]:
    """The four column pools every drop-down and every chart builder needs.

    Returns ``{"num": [...], "cat": [...], "dt": [...], "dim": [...]}``.

    Passing an already-resolved ``roles`` short-circuits re-resolution.
    """
    resolved = roles if roles is not None else resolve_roles(df, explicit,
                                                              value_sig_map)
    return {
        "num": metric_columns(resolved),
        "cat": list(resolved.get("categorical") or []),
        "dt": list(resolved.get("datetime") or []),
        "dim": dimension_columns(resolved),
    }


def is_numeric_column(df: pl.DataFrame, col: Any) -> bool:
    """True when *col* really holds numbers, whatever its role says."""
    try:
        return df[col].dtype.is_numeric()
    except Exception:
        return False


def numeric_subset(df: pl.DataFrame, cols: List[Any]) -> List[Any]:
    """Keep only the entries of *cols* that are genuinely numeric columns."""
    seen = set()
    out: List[Any] = []
    for c in cols or []:
        if c in seen or c not in df.columns:
            continue
        if is_numeric_column(df, c):
            seen.add(c)
            out.append(c)
    return out
