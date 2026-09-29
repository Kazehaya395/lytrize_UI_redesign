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

    # -- Access to the underlying manager --
    @property
    def manager(self) -> SessionManager:
        return self._manager

    # -- Python callbacks → Qt signals --
    def _on_df_changed(self, df: pd.DataFrame) -> None:
        self._last_df = df
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

    @property
    def df(self) -> Optional[pd.DataFrame]:
        return self._manager.df

    @df.setter
    def df(self, value: pd.DataFrame) -> None:
        self._manager.df = value

    def set_df(self, df: pd.DataFrame) -> None:
        self._manager.df = df

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

        db_save_draft(
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
        )

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

        log.info("restore_draft: restored %d chart(s) for user %s",
                 len(charts), user_id)
        return True
