"""services/session_service.py — Headless session state manager.

Replaces ``st.session_state``, ``set_df()``, ``update_df()``, and the
``session_cached`` decorator with a plain Python ``SessionManager`` class
that can be used from scripts, tests, and the Qt UI.

The manager is designed as a plain class (not a singleton), so callers
can instantiate their own or share one across the application.  The Qt
integration layer will wrap it in a ``QObject`` that emits signals when
state changes.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

log = logging.getLogger(__name__)


# ── Cache keys that must be invalidated when the DataFrame changes ──
_CACHES_TO_CLEAR: list[str] = [
    "_ul_preview_cache",
    "_ul_preview_cache_key",
    "_dq_charts",
    "_dq_sig",
    "_df_snapshot_sig",
    "_ul_df_stats",
    "_ul_df_stats_ver",
    "_cls_auto_cache",
    "_cls_auto_cache_ver",
    "_csv_export_cache_key",
    "_csv_export_bytes",
    "_csv_export_size",
    "_preview_stats_cache",
    "_preview_stats_cache_ver",
    "_df_snapshot_version",
    "_desc_stats_cache",
    "_desc_stats_cache_ver",
]

_MAX_SNAPSHOT_BYTES = 512 * 1_048_576


class SessionManager:
    """Headless session state manager.

    Replaces ``st.session_state`` dictionary access and the ``set_df`` /
    ``update_df`` / ``session_cached`` helpers.

    Usage::

        sm = SessionManager()
        sm.set("user_id", 42)
        sm.set_df(my_dataframe)
        chart = sm.get("charts", [])
    """

    def __init__(self) -> None:
        # -- Core state dictionary (replaces st.session_state) --
        self._state: dict[str, Any] = {}

        # -- DataFrame version counter --
        self._df_version: int = 0

        # -- Derived-cache invalidation --
        self._cache: dict[str, Any] = {}

        # -- Shadow for notes sync --
        self._notes_shadow: dict[str, str] = {}

        # -- Callbacks (Qt signals equivalent) --
        self._df_change_callbacks: list[Callable[[pd.DataFrame], None]] = []
        self._state_change_callbacks: list[Callable[[str, Any], None]] = []

    # ── Generic state access (replaces st.session_state[key]) ──────────

    def get(self, key: str, default: Any = None) -> Any:
        """Return a state value, falling back to *default*."""
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store a state value and notify callbacks."""
        self._state[key] = value
        for cb in self._state_change_callbacks:
            try:
                cb(key, value)
            except Exception as exc:
                log.warning("SessionManager state callback failed: %s", exc)

    def pop(self, key: str, default: Any = None) -> Any:
        """Remove and return a state value."""
        return self._state.pop(key, default)

    def has(self, key: str) -> bool:
        """Return True if *key* exists in state."""
        return key in self._state

    def keys(self):
        """Return an iterator over state keys."""
        return self._state.keys()

    def items(self):
        """Return an iterator over state (key, value) pairs."""
        return self._state.items()

    def setdefault(self, key: str, default: Any = None) -> Any:
        """Return the value for *key* if it exists, else set to *default*."""
        if key not in self._state:
            self._state[key] = default
        return self._state[key]

    # ── DataFrame management (replaces set_df / update_df) ────────────

    @property
    def df(self) -> Optional[pd.DataFrame]:
        """Return the current DataFrame, or None."""
        return self._state.get("df")

    @df.setter
    def df(self, value: pd.DataFrame) -> None:
        """Set the DataFrame and bump version (replaces ``set_df``)."""
        self._state["df"] = value
        self._df_version += 1
        self._state["_df_version"] = self._df_version

        # Invalidate derived caches
        for key in _CACHES_TO_CLEAR:
            self._cache.pop(key, None)
            self._state.pop(key, None)

        # Also wipe preview and dtype keys
        stale_keys = [
            k for k in self._state.keys()
            if k.startswith("_preview_") or k.startswith("_dtype_target_")
        ]
        for k in stale_keys:
            self._state.pop(k, None)

        # Notify callbacks
        for cb in self._df_change_callbacks:
            try:
                cb(value)
            except Exception as exc:
                log.warning("SessionManager DF callback failed: %s", exc)

    def update_df(self, df: pd.DataFrame) -> None:
        """Shallow-copy *df* then store (replaces ``update_df``).

        Use this when you have mutated *df* in place so that consumers
        see a **new object identity** and refresh.
        """
        self.df = df.copy(deep=False)

    def on_df_changed(self, callback: Callable[[pd.DataFrame], None]) -> None:
        """Register a callback invoked when the DataFrame is replaced."""
        self._df_change_callbacks.append(callback)

    def on_state_changed(self, callback: Callable[[str, Any], None]) -> None:
        """Register a callback invoked when any state key is set."""
        self._state_change_callbacks.append(callback)

    # ── Notes shadow (replaces _notes_shadow pattern) ────────────────

    def get_note(self, uid: str) -> str:
        """Return the current note text for a chart uid."""
        return self._notes_shadow.get(uid, self._state.get(f"desc_{uid}", ""))

    def set_note(self, uid: str, text: str) -> None:
        """Store a note for a chart uid in both shadow and state."""
        self._notes_shadow[uid] = text
        self._state[f"desc_{uid}"] = text

    def pop_note(self, uid: str) -> None:
        """Remove the note for a chart uid."""
        self._notes_shadow.pop(uid, None)
        self._state.pop(f"desc_{uid}", None)

    def sync_notes(self) -> None:
        """Bidirectional sync: desc_{uid} ↔ _notes_shadow."""
        for k, v in list(self._state.items()):
            if k.startswith("desc_") and k not in ("desc_add", "desc_close") and isinstance(v, str):
                if v:
                    self._notes_shadow[k[5:]] = v
                else:
                    self._notes_shadow.pop(k[5:], None)
        for uid, note in list(self._notes_shadow.items()):
            key = f"desc_{uid}"
            if note and (key not in self._state or not self._state.get(key)):
                self._state[key] = note

    # ── Cached computation (replaces session_cached decorator) ────────

    def cached(self, key: str, compute: Callable[[], Any], *,
               invalidate_on_df: bool = False) -> Any:
        """Return a cached value, computing it if stale.

        Parameters
        ----------
        key : str
            Cache key.
        compute : callable
            Function that returns the value to cache.
        invalidate_on_df : bool
            If True, the cache is invalidated whenever the DataFrame changes
            (checked via ``_df_version``).
        """
        cache_key = key
        sig_key = f"{key}_sig"

        current_sig = self._df_version if invalidate_on_df else None
        cached_sig = self._cache.get(sig_key)

        if cached_sig == current_sig and cache_key in self._cache:
            return self._cache[cache_key]

        val = compute()
        self._cache[cache_key] = val
        if current_sig is not None:
            self._cache[sig_key] = current_sig
        return val

    def invalidate_cache(self, key: Optional[str] = None) -> None:
        """Clear a specific cache entry, or all caches if *key* is None."""
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)
            self._cache.pop(f"{key}_sig", None)

    # ── DataFrame snapshot persistence (replaces save_df_snapshot) ────

    def df_cache_path(self, user_id: int) -> Path:
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

    def save_df_snapshot(self, user_id: int, df: Optional[pd.DataFrame] = None) -> None:
        """Persist a DataFrame to a parquet snapshot."""
        if df is None:
            df = self._state.get("df")
        if df is None:
            return

        path = self.df_cache_path(user_id)
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
                user_id,
                getattr(df, "shape", "?"),
                path,
                exc,
            )

    def load_df_snapshot(self, user_id: int) -> Optional[pd.DataFrame]:
        """Restore the DataFrame from the parquet snapshot."""
        path = self.df_cache_path(user_id)
        if not path.exists():
            return None

        try:
            file_bytes = path.stat().st_size
            if file_bytes > _MAX_SNAPSHOT_BYTES:
                log.warning(
                    "load_df_snapshot: snapshot for user %s is %.0f MB — "
                    "exceeds %.0f MB limit; skipping.",
                    user_id,
                    file_bytes / 1_048_576,
                    _MAX_SNAPSHOT_BYTES / 1_048_576,
                )
                return None
        except Exception as exc:
            log.debug("load_df_snapshot: stat failed: %s", exc)

        try:
            return pd.read_parquet(str(path), engine="pyarrow")
        except Exception as exc:
            log.warning(
                "load_df_snapshot: failed to read parquet for user %s "
                "(path=%s): %s",
                user_id,
                path,
                exc,
            )
            return None

    # ── Thread-safe background snapshot ───────────────────────────────

    def save_df_snapshot_async(self, user_id: int, df: Optional[pd.DataFrame] = None) -> None:
        """Save DataFrame snapshot in a background thread."""
        if df is None:
            df = self._state.get("df")
        if df is None:
            return

        df_ref = df  # capture on calling thread
        threading.Thread(
            target=self.save_df_snapshot,
            args=(user_id, df_ref),
            daemon=True,
        ).start()