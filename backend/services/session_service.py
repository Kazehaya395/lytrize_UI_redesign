"""services/session_service.py — Headless session state manager (Polars-native).

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

import polars as pl

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
        sm.set_df(my_polars_df)
        chart = sm.get("charts", [])
    """

    def __init__(self) -> None:
        # -- Core state dictionary (replaces st.session_state) --
        self._state: dict[str, Any] = {}

        # -- Polars DataFrame version counter --
        self._df_version: int = 0

        # -- Derived-cache invalidation --
        self._cache: dict[str, Any] = {}

        # -- Shadow for notes sync --
        self._notes_shadow: dict[str, str] = {}

        # -- Callbacks (Qt signals equivalent) --
        self._df_change_callbacks: list[Callable[[pl.DataFrame], None]] = []
        self._state_change_callbacks: list[Callable[[str, Any], None]] = []

    # ── Callback registration (the Qt-signal equivalent) ──────────────

    def on_df_changed(self, callback: Callable[[pl.DataFrame], None]) -> None:
        """Register a callback fired whenever the DataFrame is replaced.

        The callback receives the new Polars frame. A callback that raises is
        logged and skipped: one broken subscriber must not stop the others
        from being told about the new data.
        """
        if callback not in self._df_change_callbacks:
            self._df_change_callbacks.append(callback)

    def on_state_changed(self, callback: Callable[[str, Any], None]) -> None:
        """Register a callback fired whenever a state key is written."""
        if callback not in self._state_change_callbacks:
            self._state_change_callbacks.append(callback)

    def off_df_changed(self, callback: Callable[[pl.DataFrame], None]) -> None:
        """Remove a previously registered DataFrame callback."""
        if callback in self._df_change_callbacks:
            self._df_change_callbacks.remove(callback)

    def off_state_changed(self, callback: Callable[[str, Any], None]) -> None:
        """Remove a previously registered state callback."""
        if callback in self._state_change_callbacks:
            self._state_change_callbacks.remove(callback)

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

    def setdefault(self, key: str, default: Any) -> Any:
        """Return existing value or set and return *default*."""
        if key not in self._state:
            self._state[key] = default
        return self._state[key]

    # ── Chart metadata & notes access ─────────────────────────────────

    def get_chart_meta(self, uid: str) -> dict:
        """Return chart recipe/metadata for *uid*."""
        meta = self.get(f"chart_meta_{uid}")
        if meta is None:
            meta_map = self.get("chart_meta", {})
            meta = meta_map.get(uid, {}) if isinstance(meta_map, dict) else {}
        return dict(meta or {})

    def set_chart_meta(self, uid: str, **kwargs) -> None:
        """Update chart metadata for *uid*."""
        meta = dict(self.get_chart_meta(uid))
        meta.update(kwargs)
        self.set(f"chart_meta_{uid}", meta)
        meta_map = dict(self.get("chart_meta", {}) or {})
        meta_map[uid] = meta
        self.set("chart_meta", meta_map)

    def set_note(self, uid: str, text: str) -> None:
        """Store user note for chart *uid*."""
        self.set(f"chart_note_{uid}", str(text or ""))
        notes = dict(self.get("chart_notes", {}) or {})
        notes[uid] = str(text or "")
        self.set("chart_notes", notes)

    def get_note(self, uid: str) -> str:
        """Retrieve user note for chart *uid*."""
        note = self.get(f"chart_note_{uid}")
        if note is None:
            notes = self.get("chart_notes", {}) or {}
            note = notes.get(uid, "") if isinstance(notes, dict) else ""
        return str(note or "")

    # ── DataFrame access (Polars-native) ──────────────────────────────

    @property
    def df(self) -> Optional[pl.DataFrame]:
        """Return the current Polars DataFrame, or None."""
        return self._state.get("df")

    @df.setter
    def df(self, value: Optional[pl.DataFrame]) -> None:
        self.set_df(value)

    def set_df(self, df: Optional[pl.DataFrame]) -> None:
        """Store a Polars DataFrame and notify callbacks."""
        self._df_version += 1
        self._state["df"] = df
        for cb in self._df_change_callbacks:
            try:
                cb(df)
            except Exception as exc:
                log.warning("SessionManager df callback failed: %s", exc)
        for cb in self._state_change_callbacks:
            try:
                cb("df", df)
            except Exception as exc:
                log.warning("SessionManager state callback failed: %s", exc)

    # ── Cache management ──────────────────────────────────────────────

    def get_cache(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def set_cache(self, key: str, value: Any) -> None:
        self._cache[key] = value

    def pop_cache(self, key: str, default: Any = None) -> Any:
        return self._cache.pop(key, default)

    def clear_cache(self, key: Optional[str] = None) -> None:
        """Clear a single cache entry, or all caches if *key* is None."""
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)
            self._cache.pop(f"{key}_sig", None)

    # ── DataFrame snapshot persistence (Parquet via Polars) ───────────

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

    def save_df_snapshot(self, user_id: int, df: Optional[pl.DataFrame] = None) -> None:
        """Persist a Polars DataFrame to a parquet snapshot (zstd compressed)."""
        if df is None:
            df = self._state.get("df")
        if df is None:
            return

        path = self.df_cache_path(user_id)
        try:
            df.write_parquet(str(path), compression="zstd")
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

    def load_df_snapshot(self, user_id: int) -> Optional[pl.DataFrame]:
        """Restore the DataFrame from the parquet snapshot (Polars)."""
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
            return pl.read_parquet(str(path))
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

    def save_df_snapshot_async(self, user_id: int, df: Optional[pl.DataFrame] = None) -> None:
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
