"""modules/utils/session_cache.py

DEPRECATED / LEGACY: DataFrame parquet snapshot helpers.

This module used to also hold a Streamlit ``st.session_state``-backed
``set_df()`` / ``session_cached`` decorator. Those are now fully
superseded by ``services/session_service.py``'s ``SessionManager``
(headless, no Streamlit dependency) and its own
``save_df_snapshot`` / ``load_df_snapshot`` / ``df_cache_path`` methods,
which the Qt app actually uses via ``SessionBridge``.

The functions below are kept only as standalone, dependency-free
utilities in case something outside the Qt app wants file-based
snapshotting without instantiating a full SessionManager. Nothing in
the desktop app imports this module.
"""


import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

_MAX_SNAPSHOT_BYTES = 512 * 1_048_576


def make_json_safe(value):
    """Return a JSON-serializable version of *value* by recursively converting
    unsupported objects to strings."""
    if isinstance(value, dict):
        return {k: make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        import json as _json
        _json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        return str(value)


def df_cache_path(user_id: int) -> Path:
    """Return the path for the per-user DataFrame parquet snapshot."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        base = Path(runtime) / "lytrize"
    else:
        cache_home = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
        base = Path(cache_home) / "lytrize"

    base.mkdir(parents=True, exist_ok=True)
    try:
        base.chmod(0o700)
    except Exception as exc:
        log.debug("df_cache_path: chmod failed: %s", exc)

    return base / f"df_{user_id}.parquet"


def save_df_snapshot(user_id: int, df: pd.DataFrame) -> None:
    """Persist a DataFrame to a parquet snapshot."""
    if df is None:
        return

    path = df_cache_path(user_id)
    try:
        df.to_parquet(str(path), index=False, engine="pyarrow", compression="zstd")
        try:
            path.chmod(0o600)
        except Exception as exc:
            log.debug("save_df_snapshot: chmod failed: %s", exc)
    except Exception as exc:
        log.warning(
            "save_df_snapshot: failed to write parquet for user %s "
            "(shape=%s, path=%s): %s",
            user_id, getattr(df, "shape", "?"), path, exc,
        )


def load_df_snapshot(user_id: int) -> Optional[pd.DataFrame]:
    """Restore the DataFrame from the parquet snapshot written by save_df_snapshot."""
    path = df_cache_path(user_id)
    if not path.exists():
        return None

    try:
        file_bytes = path.stat().st_size
        if file_bytes > _MAX_SNAPSHOT_BYTES:
            log.warning(
                "load_df_snapshot: snapshot for user %s is %.0f MB — "
                "exceeds %.0f MB limit; skipping to avoid OOM.",
                user_id, file_bytes / 1_048_576, _MAX_SNAPSHOT_BYTES / 1_048_576,
            )
            return None
    except Exception as exc:
        log.debug("load_df_snapshot: stat failed: %s", exc)

    try:
        return pd.read_parquet(str(path), engine="pyarrow")
    except Exception as exc:
        log.warning(
            "load_df_snapshot: failed to read parquet for user %s (path=%s): %s",
            user_id, path, exc,
        )
        return None
