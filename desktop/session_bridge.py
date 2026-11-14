"""desktop/session_bridge.py -- Qt signal bridge for the headless SessionManager (Polars-native).

Wraps ``services.session_service.SessionManager`` in a ``QObject`` so the Qt
UI can subscribe to state changes via signals instead of manually polling
``st.session_state``.

Threading rule: the SessionManager is owned by the main (GUI) thread. Worker
threads must NOT touch it directly -- they should compute results and emit
signals to the main thread, which then applies the result.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import polars as pl
from PySide6.QtCore import QObject, Signal

from services.session_service import SessionManager

log = logging.getLogger(__name__)


def ensure_dataset(data: Any, *, source: str = "session-bridge"):
    """Polymorphically convert input to PolarsDataset."""
    from services.polars_engine import ensure_dataset as _ensure
    return _ensure(data, source=source)


class SessionBridge(QObject):
    """QObject wrapper around the headless SessionManager (Polars-native)."""

    # -- Signals emitted when session state changes --
    df_changed = Signal(object)        # emits pl.DataFrame
    charts_changed = Signal(object)    # emits list of (uid, title, chart_model)
    state_changed = Signal(str, object)  # emits key, value
    user_changed = Signal(object)      # emits user_id int
    theme_changed = Signal(str)        # emits theme_mode ("dark" | "light")

    def __init__(self, manager: Optional[SessionManager] = None, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager or SessionManager()

        # Bridge manager callbacks to Qt signals
        self._manager.on_df_changed(self._on_df_changed)
        self._manager.on_state_changed(self._on_state_changed)

        # Internal mirror to avoid emitting the same df twice
        self._last_df: Optional[pl.DataFrame] = None
        self._polars_dataset = None

        # Async draft-save queue (latest-wins). save_draft() serialises the
        # whole session (charts JSON + SQLite write) which stalls the UI
        # on a large session, so the write runs on ONE background thread:
        # at most one save in flight plus one pending carrying the newest
        # snapshot. Tests that need determinism set _save_sync or call
        # flush_draft_save().
        self._save_lock = threading.Lock()
        self._save_in_flight = False
        self._save_pending = None
        self._save_sync = False   # flipped by tests only

    # -- Access to the underlying manager --
    @property
    def manager(self) -> SessionManager:
        return self._manager

    # -- Python callbacks -> Qt signals --
    def _on_df_changed(self, df: pl.DataFrame) -> None:
        self._last_df = df
        # DataFrame edits still originate in legacy pages. Invalidate the
        # separate Polars snapshot so the next native operation rebuilds it
        # from the edited frame instead of silently using stale CSV rows.
        self._polars_dataset = None
        # A new frame invalidates every cache keyed on the old one's identity:
        # column roles and the slicer's distinct-value lists are memoised per
        # frame (see services.column_roles / services.filter_service), and
        # leaving them alive would answer from the dataset the user just left.
        try:
            from services.column_roles import clear_roles_cache
            from services.filter_service import clear_distinct_cache, clear_mask_cache
            clear_roles_cache()
            clear_distinct_cache()
            clear_mask_cache()
        except Exception:                 # caching must never break a load
            pass
        self.df_changed.emit(df)

    def _on_state_changed(self, key: str, value: Any) -> None:
        self.state_changed.emit(key, value)

    # -- Polars DataFrame access --
    @property
    def df(self) -> Optional[pl.DataFrame]:
        return self._manager.df

    @df.setter
    def df(self, value: Optional[pl.DataFrame]) -> None:
        self._manager.df = value

    def set_df(self, df: Optional[pl.DataFrame]) -> None:
        """Store a Polars DataFrame and emit df_changed."""
        self._manager.set_df(df)

    def set_polars_dataset(self, dataset) -> None:
        """Store the optional native query snapshot associated with this data."""
        self._polars_dataset = dataset

    def polars_dataset(self, *, create_if_missing: bool = True):
        """Return the current Polars snapshot, creating a compatibility copy.

        CSV import supplies a direct ``scan_csv`` snapshot. After data edits,
        this creates a fresh Polars snapshot from the current frame at the
        boundary instead of reusing stale rows.
        """
        if self._polars_dataset is None and create_if_missing and self.df is not None:
            try:
                self._polars_dataset = ensure_dataset(self.df)
            except Exception as exc:
                log.debug("Polars compatibility snapshot unavailable: %s", exc)
        return self._polars_dataset

    # -- Chart management --
    def add_chart(self, uid: str, title: str, chart_model: dict,
                  chart_type: str = "", meta: Optional[dict] = None,
                  note: str = "") -> None:
        """Add a chart to the session and emit charts_changed."""
        charts = list(self._manager.get("charts", []))
        charts.append((uid, title, chart_model))
        self._manager.set("charts", charts)
        if chart_type:
            self._manager.set(f"chart_type_{uid}", chart_type)
        if meta:
            self._manager.set(f"chart_meta_{uid}", meta)
        if note:
            self._manager.set_note(uid, note)
        self.charts_changed.emit(charts)

    def replace_chart(self, uid: str, new_fig: Any, new_title: Optional[str] = None, emit_signal: bool = True) -> None:
        """Replace a chart's figure (and optionally title) in place."""
        charts = list(self._manager.get("charts", []))
        for i, (c_uid, c_title, c_fig) in enumerate(charts):
            if c_uid == uid:
                charts[i] = (uid, new_title or c_title, new_fig)
                self._manager.set("charts", charts)
                if emit_signal:
                    self.charts_changed.emit(charts)
                return

    def remove_chart(self, uid: str) -> None:
        """Remove a chart from the session."""
        charts = list(self._manager.get("charts", []))
        charts = [c for c in charts if c[0] != uid]
        self._manager.set("charts", charts)
        self.charts_changed.emit(charts)

    def clear_charts(self) -> None:
        """Remove all charts."""
        self._manager.set("charts", [])
        self.charts_changed.emit([])

    def set_note(self, uid: str, text: str) -> None:
        if hasattr(self._manager, "set_note"):
            self._manager.set_note(uid, text)
        else:
            self._manager.set(f"chart_note_{uid}", str(text or ""))

    def get_note(self, uid: str) -> str:
        if hasattr(self._manager, "get_note"):
            return self._manager.get_note(uid)
        return str(self._manager.get(f"chart_note_{uid}", "") or "")

    def get_chart_meta(self, uid: str) -> dict:
        if hasattr(self._manager, "get_chart_meta"):
            return self._manager.get_chart_meta(uid)
        return dict(self._manager.get(f"chart_meta_{uid}", {}) or {})

    def set_chart_meta(self, uid: str, **kwargs) -> None:
        if hasattr(self._manager, "set_chart_meta"):
            self._manager.set_chart_meta(uid, **kwargs)
        else:
            meta = dict(self.get_chart_meta(uid))
            meta.update(kwargs)
            self._manager.set(f"chart_meta_{uid}", meta)

    def record_dataset_analysed(self) -> bool:
        user_id = self._manager.get("user_id")
        if not user_id:
            return False
        from services.database_service import record_dataset_analysed
        file_name = self._manager.get("file_name")
        file_signature = self._manager.get("file_signature")
        return record_dataset_analysed(user_id, file_name or "", file_signature or "")

    # -- Generic state access --
    def get(self, key: str, default: Any = None) -> Any:
        return self._manager.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._manager.set(key, value)

    def pop(self, key: str, default: Any = None) -> Any:
        return self._manager.pop(key, default)

    def clear_session(self) -> None:
        """Reset all session state to start a fresh analysis session."""
        self._manager._state.clear()
        self._last_df = None
        self._polars_dataset = None

    # -- Persistence --
    def save_df_snapshot_async(self, user_id: int) -> None:
        df = self._manager.df
        if df is not None:
            self._manager.save_df_snapshot_async(user_id, df)

    def save_draft(self) -> None:
        """Persist the current draft state via the database service."""
        from services.database_service import save_draft as db_save_draft
        from services.chart_service import charts_to_json
        import json

        user_id = self._manager.get("user_id")
        if not user_id:
            return

        charts = self._manager.get("charts", [])
        chart_meta = {
            k[len("chart_meta_"):]: v
            for k, v in self._manager.items()
            if k.startswith("chart_meta_") and isinstance(v, dict)
        }
        notes = dict(self._manager._notes_shadow)
        # Free-canvas placement. Without this the draft restores every chart
        # but re-auto-places it, so a relaunch (autosave fires on every edit)
        # silently undid every drag -- the "canvas position resets" report.
        tile_geometry = self._manager.get("tile_geometry", {}) or {}

        # charts_to_json only emits chart_type from tuple slot 4, but charts
        # are stored as bare 3-tuples -- the type lives in a separate
        # "chart_type_<uid>" key. Rebuild 6-tuples so the type survives
        # autosave; without it a restored chart has no analysis binding and
        # is orphaned.
        full_charts = []
        for uid, title, model in charts:
            ct = self._manager.get(f"chart_type_{uid}", "")
            full_charts.append((uid, title, model, ct, {}, {}))

        try:
            db_save_draft(
                user_id,
                self._manager.get("session_name") or "Untitled",
                self._manager.get("file_name"),
                self._manager.get("file_signature"),
                json.dumps(full_charts, default=str),
                self._manager.get("slicers", []),
                tile_geometry,
                self._manager.get("kpis", []),
            )
        except Exception as exc:
            log.error("save_draft: %s", exc)

    def restore_draft(self) -> bool:
        """Restore the most recent draft for the current user."""
        from services.database_service import get_draft as db_get_draft
        from services.chart_service import charts_from_json

        user_id = self._manager.get("user_id")
        if not user_id:
            return False

        try:
            draft = db_get_draft(user_id)
        except Exception as exc:
            log.warning("restore_draft: get_draft failed: %s", exc)
            return False
        if not draft:
            return False

        # -- DataFrame (separate parquet snapshot, not in the draft row) --
        if self._manager.df is None:
            try:
                df = self._manager.load_df_snapshot(user_id)
            except Exception as exc:
                log.warning("restore_draft: load_df_snapshot failed: %s", exc)
                df = None
            if df is not None:
                self._manager.df = df

        # -- Charts: one bulk write so charts_changed fires once --
        try:
            restored = charts_from_json(draft.get("charts_json") or "")
        except Exception as exc:
            log.warning("restore_draft: charts_from_json failed: %s", exc)
            return False
        if not restored:
            return False

        charts: list = []
        for entry in restored:
            uid, title, model = entry[0], entry[1], entry[2]
            desc = entry[3] if len(entry) > 3 else ""
            chart_type = entry[4] if len(entry) > 4 else ""
            meta = entry[5] if len(entry) > 5 else {}
            charts.append((uid, title, model))
            if desc:
                self._manager.set_note(uid, desc)
            if chart_type:
                self._manager.set(f"chart_type_{uid}", chart_type)
            if meta:
                self._manager.set(f"chart_meta_{uid}", dict(meta))

        self._manager.set("charts", charts)

        # -- Scalar page bookkeeping --
        for key, col in (
            ("file_name", "file_name"),
            ("editing_session_id", "editing_session_id"),
            ("editing_session_name", "editing_session_name"),
            ("editing_file_name", "editing_file_name"),
            ("dashboard_title", "dashboard_title"),
            ("layout_mode", "layout_mode"),
        ):
            value = draft.get(col)
            if value not in (None, ""):
                self._manager.set(key, value)

        if draft.get("page"):
            self._manager.set("draft_page", draft["page"])

        try:
            kpis = json.loads(draft.get("kpis_json") or "[]")
        except (ValueError, TypeError):
            kpis = []
        if kpis:
            self._manager.set("kpis", kpis)

        # -- Free-canvas placement --
        try:
            tile_geometry = json.loads(draft.get("tile_geometry_json") or "{}")
        except (ValueError, TypeError):
            tile_geometry = {}
        if isinstance(tile_geometry, dict) and tile_geometry:
            self._manager.set("tile_geometry", tile_geometry)

        log.info("restore_draft: restored %d chart(s) for user %s",
                 len(charts), user_id)
        return True
