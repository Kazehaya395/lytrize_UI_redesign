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

        db_save_draft(
            user_id=user_id,
            page=self._manager.get("page", "home"),
            charts_json=charts_to_json(charts, notes=notes, meta_map=chart_meta),
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