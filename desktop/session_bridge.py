"""desktop/session_bridge.py -- Qt signal bridge for the headless SessionManager.

Wraps ``services.session_service.SessionManager`` in a ``QObject`` so the Qt
UI can subscribe to state changes via signals instead of manually polling
``st.session_state``.

Threading rule: the SessionManager is owned by the main (GUI) thread. Worker
threads must NOT touch it directly — they should compute results and emit
signals to the main thread, which then applies the result.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import pandas as pd
from PySide6.QtCore import QObject, Signal

from services.session_service import SessionManager

log = logging.getLogger(__name__)


class SessionBridge(QObject):
    """QObject wrapper around the headless SessionManager."""

    # -- Signals emitted when session state changes --
    df_changed = Signal(object)        # emits pd.DataFrame
    charts_changed = Signal(object)    # emits list of (uid, title, fig)
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
        self._last_df: Optional[pd.DataFrame] = None
        self._polars_dataset = None

        # Async draft-save queue (latest-wins). save_draft() serialises the
        # whole session (Plotly to_json + SQLite write) which stalls the UI
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

    # -- Python callbacks → Qt signals --
    def _on_df_changed(self, df: pd.DataFrame) -> None:
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
        if df is not None:
            self.charts_changed.emit(self._manager.get("charts", []))

    def _on_state_changed(self, key: str, value: Any) -> None:
        self.state_changed.emit(key, value)
        if key == "user_id":
            self.user_changed.emit(value)
        elif key == "charts":
            self.charts_changed.emit(value)

    # -- Convenience proxies --
    def get(self, key: str, default: Any = None) -> Any:
        return self._manager.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._manager.set(key, value)

    def pop(self, key: str, default: Any = None) -> Any:
        return self._manager.pop(key, default)

    def clear_session(self) -> None:
        """Reset all session state to start a fresh analysis session.

        Clears the DataFrame, charts, KPIs, slicers, tile geometry, and
        file metadata so that a new upload/analysis starts from a clean slate.
        """
        self._manager._state.clear()

    @property
    def df(self) -> Optional[pd.DataFrame]:
        return self._manager.df

    @df.setter
    def df(self, value: pd.DataFrame) -> None:
        self._manager.df = value

    def set_df(self, df: pd.DataFrame) -> None:
        self._manager.df = df

    def set_polars_dataset(self, dataset) -> None:
        """Store the optional native query snapshot associated with this data."""
        self._polars_dataset = dataset

    def polars_dataset(self, *, create_if_missing: bool = True):
        """Return the current Polars snapshot, creating a compatibility copy.

        CSV import supplies a direct ``scan_csv`` snapshot. After legacy data
        edits, this creates a fresh Polars snapshot from the current pandas
        frame at the boundary instead of reusing stale rows.
        """
        if self._polars_dataset is None and create_if_missing and self.df is not None:
            try:
                from services.polars_engine import PolarsDataset
                self._polars_dataset = PolarsDataset.from_pandas(self.df)
            except Exception as exc:
                log.debug("Polars compatibility snapshot unavailable: %s", exc)
        return self._polars_dataset

    def add_chart(self, uid: str, title: str, fig: Any, chart_type: str = "",
                  meta: Optional[dict] = None, note: str = "") -> None:
        """Add a chart to the session and emit charts_changed."""
        charts = list(self._manager.get("charts", []))
        charts.append((uid, title, fig))
        self._manager.set("charts", charts)
        if chart_type:
            self._manager.set(f"chart_type_{uid}", chart_type)
        if meta:
            self._manager.set(f"chart_meta_{uid}", meta)
        if note:
            self._manager.set_note(uid, note)

    def replace_chart(self, uid: str, new_fig: Any, new_title: Optional[str] = None) -> None:
        """Replace a chart's figure (and optionally title) in place."""
        charts = list(self._manager.get("charts", []))
        for i, (c_uid, c_title, c_fig) in enumerate(charts):
            if c_uid == uid:
                charts[i] = (c_uid, new_title or c_title, new_fig)
                break
        self._manager.set("charts", charts)

    def remove_chart(self, uid: str) -> None:
        """Remove a chart by uid and clean up its associated state."""
        charts = list(self._manager.get("charts", []))
        charts = [c for c in charts if c[0] != uid]
        self._manager.set("charts", charts)
        self._manager.pop_note(uid)
        self._manager.pop(f"chart_type_{uid}", None)
        self._manager.pop(f"chart_meta_{uid}", None)
        self._manager.pop(f"desc_{uid}", None)

    def clear_charts(self) -> None:
        """Remove all charts and chart-related state."""
        self._manager.set("charts", [])
        self._manager.set("selected_analyses", [])
        for key in list(self._manager.keys()):
            if key.startswith("chart_type_") or key.startswith("chart_meta_") \
                    or key.startswith("desc_") or key.startswith("_edit_"):
                self._manager.pop(key, None)
        self._manager.invalidate_cache()

    def set_note(self, uid: str, text: str) -> None:
        self._manager.set_note(uid, text)

    def get_note(self, uid: str) -> str:
        return self._manager.get_note(uid)

    def get_chart_meta(self, uid: str) -> dict:
        return self._manager.get(f"chart_meta_{uid}", {})

    def set_chart_meta(self, uid: str, **kwargs) -> None:
        meta = dict(self._manager.get(f"chart_meta_{uid}", {}))
        meta.update(kwargs)
        self._manager.set(f"chart_meta_{uid}", meta)

    # -- Persistence helpers --
    def record_dataset_analysed(self) -> bool:
        """Record that the loaded dataset has reached the analysis stage.

        Called from the two entry points that genuinely mean "the user started
        analysing this data": proceeding to the Analysis page from Upload, and
        successfully generating a chart when they jumped straight there from
        the sidebar. Both call this one method so the identity used for
        de-duplication is identical no matter which door the user came in by.

        De-duplication is by ``file_signature`` ("<name>:<size>"), so the same
        file analysed again in the same or a later session does not
        double-count, while an updated file that happens to share the name does.

        Returns True when the count actually went up.
        """
        df = self._manager.df
        user_id = self._manager.get("user_id")
        if df is None or user_id is None:
            return False
        file_name = str(self._manager.get("file_name") or "")
        # file_signature is set at import time from the real on-disk size.
        # Datasets that arrive by another route (an Excel sheet selection, a
        # restored session) have no size, so fall back to name+shape, which
        # still distinguishes genuinely different data.
        signature = str(self._manager.get("file_signature") or "")
        if not signature:
            if not file_name:
                return False
            signature = f"{file_name}:{len(df)}x{df.shape[1]}"
        try:
            from services.database_service import record_dataset_analysed
            return record_dataset_analysed(
                user_id, file_name, signature, len(df), df.shape[1])
        except Exception as exc:
            # Counting is telemetry; never let it break navigation or chart
            # generation.
            log.warning("record_dataset_analysed failed: %s", exc)
            return False

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
        # re-rendering falls back to guessing.
        serialisable = []
        for uid, title, fig in charts:
            serialisable.append((
                uid, title, fig,
                notes.get(uid, ""),
                self._manager.get(f"chart_type_{uid}", ""),
                chart_meta.get(uid, {}),
            ))

        snapshot = dict(
            user_id=user_id,
            page=self._manager.get("page", "home"),
            charts_json=charts_to_json(serialisable, notes=notes, meta_map=chart_meta),
            file_name=self._manager.get("file_name", ""),
            editing_session_id=self._manager.get("editing_session_id"),
            editing_session_name=self._manager.get("editing_session_name"),
            editing_file_name=self._manager.get("editing_file_name", ""),
            dashboard_title=self._manager.get("dashboard_title", ""),
            kpis_json=json.dumps(self._manager.get("kpis", [])),
            chart_meta_json=json.dumps(
                {f"chart_meta_{k}": v for k, v in chart_meta.items()},
                default=str,
            ),
            layout_mode=self._manager.get("layout_mode", "portrait"),
            tile_geometry_json=json.dumps(tile_geometry, default=str),
        )
        if self._save_sync:
            # Tests only: deterministic, same-thread write.
            from services.database_service import save_draft as db_save_draft
            db_save_draft(**snapshot)
            return
        with self._save_lock:
            if self._save_in_flight:
                # A save is already running: remember only the newest
                # snapshot. Rapid generate/delete/edit bursts collapse to
                # two writes (in-flight + latest) instead of N.
                self._save_pending = snapshot
                return
            self._save_in_flight = True
        threading.Thread(target=self._save_loop,
                         args=(snapshot,), daemon=True).start()

    def _write_draft_snapshot(self, snapshot: dict) -> None:
        """Perform one draft write. Runs on the saver thread, never the UI."""
        try:
            from services.database_service import save_draft as db_save_draft
            db_save_draft(**snapshot)
        except Exception as exc:
            log.warning("save_draft: write failed: %s", exc)

    def _save_loop(self, first: dict) -> None:
        """Drain the save queue: first snapshot, then the latest pending."""
        snapshot = first
        while snapshot is not None:
            self._write_draft_snapshot(snapshot)
            with self._save_lock:
                snapshot = self._save_pending
                self._save_pending = None
                if snapshot is None:
                    self._save_in_flight = False

    def flush_draft_save(self, timeout: float = 10.0) -> bool:
        """Block until the saver thread is idle. Tests only.

        Returns True when the queue drained within *timeout* seconds.
        """
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._save_lock:
                idle = (not self._save_in_flight
                        and self._save_pending is None)
            if idle:
                return True
            time.sleep(0.02)
        return False

    def restore_draft(self) -> bool:
        """Reload the last auto-saved draft into the live session.

        Counterpart to :meth:`save_draft`. Autosave runs every 2 minutes and
        on every Generate, but nothing read the row back, so a close/reopen
        silently lost the session even though the draft was sitting in
        ``draft_sessions``.

        Restores the DataFrame from the parquet snapshot plus every chart
        (figure, title, notes, chart_type, meta) and the assorted page
        bookkeeping fields. Returns True when at least one chart came back.

        Never raises: a missing or corrupt draft is a normal empty-start
        condition, and a bad launch must not be blocked by it.
        """
        import json

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

        # -- Charts: one bulk write so charts_changed fires once and the
        #    analysis page rebuilds its tabs a single time.
        try:
            restored = charts_from_json(draft.get("charts_json") or "")
        except Exception as exc:
            log.warning("restore_draft: charts_from_json failed: %s", exc)
            return False
        if not restored:
            return False

        charts: list = []
        for entry in restored:
            uid, title, fig = entry[0], entry[1], entry[2]
            desc = entry[3] if len(entry) > 3 else ""
            chart_type = entry[4] if len(entry) > 4 else ""
            meta = entry[5] if len(entry) > 5 else {}
            charts.append((uid, title, fig))
            if desc:
                self._manager.set_note(uid, desc)
            if chart_type:
                self._manager.set(f"chart_type_{uid}", chart_type)
            if meta:
                self._manager.set(f"chart_meta_{uid}", dict(meta))

        self._manager.set("charts", charts)

        # -- Scalar page bookkeeping, persisted alongside the charts.
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

        # The active page is restored by the caller (it owns navigation);
        # keep only the page that actually carries a draft.
        if draft.get("page"):
            self._manager.set("draft_page", draft["page"])

        try:
            kpis = json.loads(draft.get("kpis_json") or "[]")
        except (ValueError, TypeError):
            kpis = []
        if kpis:
            self._manager.set("kpis", kpis)

        # -- Free-canvas placement. Restored AFTER the charts so the geometry
        #    the user dragged is put back verbatim instead of the dashboard
        #    re-auto-placing every card on the restored list.
        try:
            tile_geometry = json.loads(draft.get("tile_geometry_json") or "{}")
        except (ValueError, TypeError):
            tile_geometry = {}
        if isinstance(tile_geometry, dict) and tile_geometry:
            self._manager.set("tile_geometry", tile_geometry)

        log.info("restore_draft: restored %d chart(s) for user %s",
                 len(charts), user_id)
        return True
