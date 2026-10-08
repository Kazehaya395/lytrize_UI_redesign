"""services/column_roles.py -- THE single source of truth for column families.

Why this module exists
----------------------
Every drop-down offering a "metric", a "dimension" or a "group by" column
must answer the same question: *what kind of column is this?* Previously
each caller answered independently with a raw ``df.select_dtypes(...)``
call, which produced three defects:

1. Columns were silently LOST. ``select_dtypes(include="object")`` does not
   match ``category``, ``string`` or ``bool`` columns, so a
   pandas-optimised dataset (the dtype transformer turns low-cardinality
   text into ``category``) lost those columns from every dimension pool --
   they appeared in NO pool at all and so could never be picked.
2. User intent was ignored. The Column Classification panel on the Upload
   page stores the user's confirmed numeric/categorical/datetime choice,
   but the Analysis page re-derived the pools from raw dtypes, so a user
   who deliberately reclassified an id-like numeric as a dimension still
   saw it only as a metric.
3. There was no single definition to fix, so every new chart type would
   have to repeat (and eventually forget) the correction.

The rule implemented here, in priority order:

* **The user's explicit classification always wins.** If the user called a
  column a dimension, every drop-down in the entire app treats it as a
  dimension -- no matter what its pandas dtype says.
* **Auto-detection is the fallback**, used only when the user never
  classified the column (or it is new to the dataset).
* **A column is never dropped.** Every column ends up in exactly one
  family, so no column can become unreachable from a drop-down.
* **Self-healing after dtype conversion.** If a column's *value profile*
  changed since the user confirmed it -- e.g. the Dtype Transformer
  converted a text column to real ``date``/``time`` objects, which leaves
  the pandas dtype as ``object`` -- that one column is re-derived. Without
  this a converted date column would stay a "dimension" forever and no
  time-series chart could find it. Unchanged columns keep the user's
  choice verbatim.

This module is deliberately UI-free and side-effect-free so the Qt pages
and the headless chart services can all share it.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

FAMILIES = ("numeric", "categorical", "datetime")

# Datetime-like pandas dtypes, including the "delta" flavour.
_DT_DTYPES = ["datetime", "datetimetz", "timedelta"]


# Rows read by _probe_non_null before falling back to a full scan. Large
# enough that a column whose first rows are well-populated is answered from
# the head alone, small enough that the probe is microseconds.
_PROBE_WINDOW = 100_000


def _probe_non_null(series: pd.Series, need: int = 20,
                    window: int = _PROBE_WINDOW) -> pd.Series:
    """First *need* non-null values, read from a bounded head of the column.

    ``series.dropna()`` is O(rows): it materialises a full boolean mask and a
    full reduced copy before the caller takes its first 20 values. On a
    multi-million-row frame that runs for EVERY object column on EVERY
    ``resolve_roles`` call -- which is what made selecting an analysis type on
    the sidebar stall the UI. Reading the head keeps the cost flat in the
    column length for the common case where values are densely populated.

    The fallback exists for correctness, not performance: a sparse column
    (leading NaNs) would probe empty and mis-detect, so when the head yields
    fewer than *need* non-nulls the full ``dropna()`` still runs. That is the
    rare shape, and getting the answer wrong would be worse than paying for it.
    """
    if len(series) <= window:
        return series.dropna()
    head = series.iloc[:window].dropna()
    if len(head) < need:
        return series.dropna()
    return head


def value_sig(series: pd.Series) -> tuple:
    """Fingerprint a column's value profile (dtype + sampled value types).

    The pandas dtype string alone cannot detect a date/time conversion
    (text 'object' -> date objects is object -> object), so the sample of
    actual value types is part of the signature.

    The sample is taken through :func:`_probe_non_null`, so this stays cheap
    enough to call per column per resolve (see the cache in
    :func:`resolve_roles` for the layer above).
    """
    non_null = _probe_non_null(series, need=20)
    types = tuple(sorted({type(v).__name__ for v in non_null.iloc[:20]}))
    return (str(series.dtype), types)


def value_sig_map(df: pd.DataFrame) -> Dict[Any, tuple]:
    """Capture the baseline fingerprint for every column (for self-healing)."""
    if df is None:
        return {}
    return {c: value_sig(df[c]) for c in df.columns}


def _looks_temporal(series: pd.Series) -> bool:
    """True when most probed non-null values are real date/time objects.

    Only object columns reach this probe, because a genuine datetime dtype is
    already caught by the pandas dtype checks above; the probe exists purely for
    the object case.
    """
    non_null = _probe_non_null(series, need=20)
    if len(non_null) == 0:
        return False
    sample = non_null.iloc[:20]
    hits = sum(
        1 for v in sample
        if isinstance(v, (datetime.date, datetime.time, pd.Timestamp))
    )
    if hits / len(sample) >= 0.5:
        return True
    # Fall back to a strict parse probe on strings: only trust it when
    # EVERY probed value parses (avoids false-positives on free text).
    if sample.map(type).eq(str).all():
        parsed = pd.to_datetime(sample, errors="coerce", format="%Y-%m-%d")
        return bool(parsed.notna().all())
    return False


def auto_roles(df: pd.DataFrame) -> Dict[str, List[str]]:
    """Detect numeric / categorical / datetime families from the data alone.

    Used when the user has not classified anything. Every column lands in
    exactly one family, so total coverage already holds here.
    """
    cols = df.columns.tolist()
    dt = df.select_dtypes(include=_DT_DTYPES).columns.tolist()
    for col in df.select_dtypes(include=["object"]).columns:
        if col not in dt and _looks_temporal(df[col]):
            dt.append(col)
    numeric = [c for c in df.select_dtypes(include=[np.number]).columns
               if c not in dt]
    categorical = [c for c in cols if c not in numeric and c not in dt]
    return {"numeric": numeric, "categorical": categorical, "datetime": dt}


def _normalise(explicit: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Coerce a user classification into a clean {family: [cols]} mapping."""
    out: Dict[str, List[str]] = {f: [] for f in FAMILIES}
    if not explicit:
        return out
    for fam in FAMILIES:
        for c in (explicit.get(fam) or []):
            if c not in out[fam]:
                out[fam].append(c)
    return out


# -- Roles memo -------------------------------------------------------------
# Resolving the families is pure: same DataFrame + same user classification +
# same value signatures always produce the same answer. The Analysis page
# re-resolves on EVERY sidebar selection (and the Export path again), and the
# old implementation ran a ``dropna`` probe per object column each time -- on a
# wide frame over a million rows that was the visible stall when clicking an
# analysis type. One entry per DataFrame is enough (the session has exactly
# one live frame), and the frame is held STRONGLY so ``id()`` cannot be
# recycled onto a different object while its entry is still cached.
_ROLES_CACHE: Dict[tuple, tuple] = {}   # key -> (df, roles)


def _roles_cache_key(df: pd.DataFrame, explicit, value_sig_map):
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
            sig = tuple(sorted((str(k), v) for k, v in value_sig_map.items()))
        except Exception:
            return None
    else:
        sig = None
    norm = _normalise(explicit)
    # _normalise returns a dict (unhashable); flatten it to a comparable tuple.
    cls = tuple((f, tuple(norm.get(f) or ())) for f in FAMILIES)
    return (id(df), shape, cols, sig, cls)


def clear_roles_cache() -> None:
    """Drop every memoised resolution (called when the DataFrame changes)."""
    _ROLES_CACHE.clear()


def resolve_roles(
    df: pd.DataFrame,
    explicit: Optional[Dict[str, Any]] = None,
    value_sig_map: Optional[Dict[Any, tuple]] = None,
) -> Dict[str, List[str]]:
    """Resolve the column families every drop-down in the app should use.

    Parameters
    ----------
    df :
        The active dataset.
    explicit :
        The user's confirmed classification, shaped
        ``{"numeric": [...], "categorical": [...], "datetime": [...]}``.
        ``None``/empty means the user never classified -- auto-detection
        then decides everything. A column named in ``explicit`` keeps the
        user's family regardless of its pandas dtype.
    value_sig_map :
        The value-profile fingerprint captured when the user confirmed (see
        :func:`value_sig`). Columns whose fingerprint has since changed are
        re-derived, because a dtype conversion (e.g. text -> date objects)
        has invalidated the old answer for them. Columns whose fingerprint
        is unchanged keep the user's choice verbatim.

    Returns
    -------
    dict
        ``{"numeric": [...], "categorical": [...], "datetime": [...]}``
        covering every column of *df* exactly once, in dataframe order.

    The result is memoised per (frame, classification, signatures); the
    returned dict is a fresh copy each time, so a caller that appends to a
    family cannot poison the cache for the next one.
    """
    if df is None:
        return {f: [] for f in FAMILIES}

    key = _roles_cache_key(df, explicit, value_sig_map)
    if key is not None:
        hit = _ROLES_CACHE.get(key)
        if hit is not None and hit[0] is df:
            return {f: list(hit[1].get(f) or []) for f in FAMILIES}

    roles = _resolve_roles_uncached(df, explicit, value_sig_map)
    if key is not None:
        _ROLES_CACHE.clear()             # one live frame; never grow a pile
        # Store a COPY: ``roles`` is also handed to this caller, and a caller
        # that appends to a family must not rewrite what the next one reads.
        _ROLES_CACHE[key] = (df, {f: list(roles.get(f) or []) for f in FAMILIES})
    return roles


def _resolve_roles_uncached(
    df: pd.DataFrame,
    explicit: Optional[Dict[str, Any]] = None,
    value_sig_map: Optional[Dict[Any, tuple]] = None,
) -> Dict[str, List[str]]:
    if df is None:
        return {f: [] for f in FAMILIES}

    cols = df.columns.tolist()
    if not cols:
        return {f: [] for f in FAMILIES}

    user = _normalise(explicit)

    # Which columns may we trust the user's answer for?
    trusted = (set(user["numeric"]) | set(user["categorical"])
               | set(user["datetime"]))
    if value_sig_map:
        # Re-derive only the columns whose value profile actually moved.
        changed = {c for c in cols
                   if c in trusted and value_sig(df[c]) != value_sig_map.get(c)}
        trusted -= changed

    detected = auto_roles(df)
    roles: Dict[str, List[str]] = {f: [] for f in FAMILIES}
    assigned = set()

    # 1. The user's explicit, still-valid classification wins.
    for fam in FAMILIES:
        for c in user[fam]:
            if c in cols and c in trusted and c not in assigned:
                roles[fam].append(c)
                assigned.add(c)

    # 2. Everything else is auto-detected -- for columns the user never
    #    classified, and for any whose value profile changed since.
    for fam in FAMILIES:
        for c in detected[fam]:
            if c not in assigned:
                roles[fam].append(c)
                assigned.add(c)

    # 3. Total-coverage guarantee: a column that neither the user nor the
    #    detector claimed (an exotic dtype such as sparse or arrow-backed)
    #    becomes a dimension rather than vanishing from every drop-down.
    for c in cols:
        if c not in assigned:
            roles["categorical"].append(c)
            assigned.add(c)

    # Preserve dataframe order so drop-downs read top-to-bottom the way the
    # user's table does, not in family-detection order.
    order = {c: i for i, c in enumerate(cols)}
    for fam in FAMILIES:
        roles[fam].sort(key=lambda c: order[c])
    return roles


def metric_columns(roles: Dict[str, List[str]]) -> List[str]:
    """Columns offered as metrics (the values being measured)."""
    return list(roles.get("numeric") or [])


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


def role_pools(df: pd.DataFrame, roles: Optional[Dict[str, List[str]]] = None,
               explicit: Optional[Dict[str, List[str]]] = None,
               value_sig_map: Optional[Dict[Any, tuple]] = None) -> Dict[str, List[str]]:
    """The four column pools every drop-down and every chart builder needs.

    Returns ``{"num": [...], "cat": [...], "dt": [...], "dim": [...]}``.

    This is the ONLY entry point a runner should use to answer "which columns
    are metrics / dimensions". A runner that re-derives the answer with its
    own ``select_dtypes`` call is the exact bug this module was written to
    end: it silently ignores the user's Column Classification, so a column the
    user deliberately reclassified as a dimension reappears as a metric in
    one chart type and not in another. ``tests``/the smoke suite enforce that
    no live analysis module calls ``select_dtypes`` itself.

    Passing an already-resolved ``roles`` short-circuits re-resolution, so a
    runner can be handed the exact pools the UI showed the user without the
    cost (or the risk of drift) of resolving them a second time.
    """
    resolved = roles if roles is not None else resolve_roles(df, explicit,
                                                            value_sig_map)
    return {
        "num": metric_columns(resolved),
        "cat": list(resolved.get("categorical") or []),
        "dt": list(resolved.get("datetime") or []),
        "dim": dimension_columns(resolved),
    }


def is_numeric_column(df: pd.DataFrame, col: Any) -> bool:
    """True when *col* really holds numbers, whatever its role says.

    Roles answer "how should the app treat this column"; this answers "will
    arithmetic work on it". They usually agree, but they do not have to: a
    metric can hold a numeric-looking object column, and an arithmetic
    operation (``.corr()``, IQR quartiles) must not blow up on it. It uses
    the pandas dtype predicate rather than ``select_dtypes`` so it stays a
    per-column question, not a bulk re-derivation of the pools.
    """
    try:
        return bool(pd.api.types.is_numeric_dtype(df[col]))
    except Exception:
        return False


def numeric_subset(df: pd.DataFrame, cols: List[Any]) -> List[Any]:
    """Keep only the entries of *cols* that are genuinely numeric columns.

    Used by arithmetic runners (correlation, outliers) to defend against a
    role/label mismatch without discarding the user's classification
    decision: the role pool decides what is *offered*, this only filters what
    is *safe to compute on*.
    """
    seen = set()
    out: List[Any] = []
    for c in cols or []:
        if c in seen or c not in df.columns:
            continue
        if is_numeric_column(df, c):
            seen.add(c)
            out.append(c)
    return out

