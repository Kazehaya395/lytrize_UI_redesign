"""services/session_bundle.py -- Persist a session as a whole, self-contained bundle.

The bug this replaces
---------------------
"Saving a session" only wrote ``charts_json`` (Plotly figures) + styling +
geometry. It never wrote the DataFrame the charts came from, and never the
slicer definitions. The consequence, exactly as reported: a session saved and
reopened (after the app is closed) shows figures that can no longer be
filtered or re-derived -- the slicers are dead because there is no data behind
them, and the board reads as broken/empty. The rows survive in the DB, but the
*package* the user built does not.

The design
----------
A session now references three extra columns on its row:

* ``dataset_hash`` / ``dataset_uri`` -- the DataFrame, stored ONCE as a
  content-addressed Parquet file beside the database. Content-addressed means
  two sessions over the same dataset share one file, and a re-save with
  unchanged data is a no-op rather than a second copy. Parquet (not inline
  JSON) because a 1.3M-row frame is ~100 MB as JSON and a few MB as Parquet,
  and it must never bloat the SQLite row.
* ``slicers_json`` -- the slicer definitions, so they come back live.

The charts keep their stored figures as a fast first paint; on open the bundle
also reloads the DataFrame and re-derives the figures from it, so a reopened
board behaves like the live one (filters work, charts regenerate).

Every function here is best-effort and never raises into a caller: a bundle
that cannot be written still saves the session (the old behaviour), and one
that cannot be read still opens the stored figures. Losing the bundle degrades
a feature; losing the save would lose the user's work.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# Parquet files live in a ``datasets`` directory next to the database file, so
# the whole Lytrize store stays under one root the user can back up as a unit.
_SUBDIR = "datasets"


def _bundle_dir() -> Path:
    from modules.database import DB_PATH
    root = Path(DB_PATH).resolve().parent / _SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _frame_hash(df: pd.DataFrame) -> str:
    """A stable content hash for *df*.

    Uses shape + dtypes + a hash of a bounded sample of values, NOT the whole
    frame: hashing 1.3M rows on every save is the exact UI-thread stall this
    bundle must avoid. Two edits to the same dataset will almost always differ
    in shape, columns or an early sample, so this is a reliable change-detector
    without being a cryptographic digest of every cell.
    """
    try:
        sample = df.head(2000)
        payload = repr(sample.to_dict("list")).encode("utf-8", "replace")
        h = hashlib.sha1(payload).hexdigest()
        return f"{len(df)}x{df.shape[1]}:{h[:16]}"
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("session_bundle: hash failed, falling back to shape: %s", exc)
        return f"{len(df)}x{df.shape[1]}"


def save_dataset(df: Optional[pd.DataFrame]) -> tuple[str, str]:
    """Persist *df* as content-addressed Parquet; return ``(hash, uri)``.

    ``uri`` is the file name relative to the bundle dir (portable if the store
    moves), or ``""`` on any failure -- the caller stores an empty uri and the
    session simply reopens without a live dataset, which is the pre-bundle
    behaviour, not a crash.
    """
    if df is None:
        return "", ""
    # A Polars dataset can be persisted without converting the complete frame
    # through pandas. Keep the established pandas route as the default for
    # existing sessions while the upload/session cutover is staged.
    try:
        from services.polars_engine import PolarsDataset
    except Exception:  # pragma: no cover - optional migration dependency
        PolarsDataset = ()
    if isinstance(df, PolarsDataset):
        return save_polars_dataset(df)
    try:
        if len(df) == 0:
            return "", ""
        frame_hash = _frame_hash(df)
        uri = f"{frame_hash}.parquet"
        path = _bundle_dir() / uri
        if not path.exists():
            # Write to a temp file then atomically replace, so an interrupted
            # save cannot leave a half-written parquet that a later open reads
            # as corrupt.
            tmp = path.with_suffix(".parquet.tmp")
            df.to_parquet(tmp, index=False)
            tmp.replace(path)
        return frame_hash, uri
    except Exception as exc:
        log.warning("session_bundle: could not persist dataset: %s", exc)
        return "", ""


def save_polars_dataset(dataset) -> tuple[str, str]:
    """Persist a lazy Polars dataset as content-addressed Parquet.

    The lazy query writes directly to a temporary Parquet file. Hashing the
    resulting artifact gives the stored snapshot an identity based on the
    actual rows (including transformed/filter state), rather than only on the
    original CSV source revision.
    """
    if dataset is None:
        return "", ""
    temporary = None
    try:
        from services.polars_engine import PolarsDataset
        if not isinstance(dataset, PolarsDataset):
            return "", ""
        root = _bundle_dir()
        temporary = root / (".polars-" + hashlib.sha256(
            (dataset.source + dataset.revision).encode("utf-8")).hexdigest()[:16]
            + ".parquet.tmp")
        dataset.frame.sink_parquet(temporary)
        digest = hashlib.sha256()
        with temporary.open("rb") as saved:
            for chunk in iter(lambda: saved.read(1024 * 1024), b""):
                digest.update(chunk)
        frame_hash = digest.hexdigest()
        uri = f"{frame_hash}.parquet"
        target = root / uri
        if target.exists():
            temporary.unlink(missing_ok=True)
        else:
            temporary.replace(target)
        return frame_hash, uri
    except Exception as exc:
        log.warning("session_bundle: could not persist Polars dataset: %s", exc)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return "", ""


def load_dataset(uri: str) -> Optional[pd.DataFrame]:
    """Load the Parquet dataset *uri* refers to, or None if unavailable."""
    if not uri:
        return None
    try:
        path = _bundle_dir() / Path(uri).name  # name only: no path escape
        if not path.exists():
            return None
        return pd.read_parquet(path)
    except Exception as exc:
        log.warning("session_bundle: could not load dataset %r: %s", uri, exc)
        return None


def load_polars_dataset(uri: str):
    """Load a portable Parquet snapshot as a lazy Polars dataset.

    This is an opt-in reader for the migration path. Existing session callers
    continue to receive pandas frames from :func:`load_dataset` until the UI
    and table-model boundaries are converted.
    """
    if not uri:
        return None
    try:
        import polars as pl
        from services.polars_engine import PolarsDataset, ROW_ID
        path = _bundle_dir() / Path(uri).name
        if not path.is_file():
            return None
        lazy = pl.scan_parquet(path)
        schema = lazy.collect_schema()
        row_id = ROW_ID
        names = set(schema.names())
        if ROW_ID not in names:
            lazy = lazy.with_row_index(row_id, offset=0)
        digest = hashlib.sha256()
        with path.open("rb") as saved:
            for chunk in iter(lambda: saved.read(1024 * 1024), b""):
                digest.update(chunk)
        return PolarsDataset(lazy, str(path), digest.hexdigest(), row_id)
    except Exception as exc:
        log.warning("session_bundle: could not load Polars dataset %r: %s", uri, exc)
        return None
