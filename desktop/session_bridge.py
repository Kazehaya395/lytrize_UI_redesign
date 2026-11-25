"""desktop/session_bridge.py -- Qt signal bridge for the headless SessionManager (Polars-native).

Wraps ``services.session_service.SessionManager`` in a ``QObject`` so the Qt
UI can subscribe to state changes via signals instead of manually polling
``st.session_state``.

Threading rule: the SessionManager is owned by the main (GUI) thread. Worker
threads must NOT touch it directly -- they should compute results and emit
signals to the main thread, which then applies the result.
"""
from __future__ import annotations

import json
import logging
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

        # save_draft() is synchronous: the SQLite write is small enough
        # that handing it to a background thread was not worth the race
        # (autosave could land after the app closed and lose the draft).
        # Tests can call save_draft() and read the row back immediately.
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
        if not file_signature:
            # Only the file-picker path ever stored one; Excel/DB/clipboard
            # imports, and any other frame that arrived without it, would
            # silently never count. Derive a stable, cheap one from the
            # frame itself so EVERY import route counts.
            file_signature = self._derive_dataset_signature(file_name or "dataset")
        if not file_signature:
            return False
        try:
            self._manager.set("file_signature", file_signature)
        except Exception:
            pass
        return record_dataset_analysed(user_id, file_name or "", file_signature)

    def _derive_dataset_signature(self, file_name: str) -> str:
        """Cheap deterministic fingerprint of the loaded frame.

        |shape + column names + a sample of the first rows| -- same file
        re-imported yields the same signature (counted once); a changed
        CSV/xlsx of the same name flips it (counted again). No data is
        fingerprinted byte-wise, so this stays instant on large frames.
        """
        try:
            df = self.df
        except Exception:
            df = None
        if df is None:
            return ""
        try:
            shape = getattr(df, "shape", None)
            if shape is None:
                cols = list(getattr(df, "columns", []))
                rows = int(df.height) if hasattr(df, "height") else "?"
            else:
                rows, ncols = int(shape[0]), int(shape[1])
                cols = list(df.columns)
            try:
                sample = df.head(3).to_dicts() if hasattr(df, "head") else None
            except Exception:
                sample = None
            try:
                key = (str(file_name), tuple(cols), rows, str(sample))
                import hashlib
                digest = hashlib.sha1(repr(key).encode("utf-8", "backslashreplace")).hexdigest()[:12]
            except Exception:
                digest = f"{rows}x{len(cols)}"
            return f"{file_name}:{digest}"
        except Exception:
            return ""

    def record_report_built(self, title: str = "", export_kind: str = "html") -> bool:
        """Count that a dashboard report was built (exported to a file)."""
        user_id = self._manager.get("user_id")
        if not user_id:
            return False
        try:
            from services.database_service import record_report_built
        except Exception:
            return False
        rows = 0
        try:
            df = self.df
            shape = getattr(df, "shape", None) if df is not None else None
            if shape:
                rows = int(shape[0])
        except Exception:
            rows = 0
        return bool(record_report_built(user_id, title, export_kind, rows))

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
        notes = dict(self._manager.get("chart_notes", {}) or {})
        # Free-canvas placement. Without this the draft restores every chart
        # but re-auto-places it, so a relaunch (autosave fires on every edit)
        # silently undid every drag -- the "canvas position resets" report.
        tile_geometry = self._manager.get("tile_geometry", {}) or {}
        # Multi-page state, assembled fresh from the live bridge keys. A
        # corrupt pages_json is never fatal: saving still persists the rest.
        try:
            from services import page_state
            pages_json = page_state.page_state_json(
                page_state.assemble_from_bridge(self._manager.get))
        except Exception as exc:
            log.warning("save_draft: page_state assembly failed: %s", exc)
            pages_json = "{}"

        # charts_to_json only emits chart_type from tuple slot 5, but charts
        # are stored as bare 3-tuples -- the type lives in a separate
        # "chart_type_<uid>" key. Rebuild 6-tuples so the type survives
        # autosave; without it a restored chart has no analysis binding and
        # is orphaned. (Slot 4 is desc -- notes go in via meta_map/notes.)
        full_charts = []
        for uid, title, model in charts:
            ct = self._manager.get(f"chart_type_{uid}", "")
            full_charts.append((uid, title, model, "", ct,
                                chart_meta.get(uid, {})))

        # Positional misalignment here silently stored the file name in the
        # charts_json column, the charts JSON in editing_session_id and the
        # KPI list in dashboard_title (the DB layer swallows the type
        # errors), so a relaunch found a draft row with no parseable charts
        # and restored an empty session.  Pass arguments by name and use the
        # real serializer so the row round-trips.
        try:
            from services.chart_service import charts_to_json
            charts_json = charts_to_json(full_charts, notes=notes,
                                         meta_map=chart_meta)
            db_save_draft(
                user_id,
                self._manager.get("page") or "analysis",
                charts_json,
                file_name=self._manager.get("file_name") or "",
                editing_session_id=self._manager.get("editing_session_id"),
                editing_session_name=self._manager.get("editing_session_name"),
                editing_file_name=self._manager.get("editing_file_name") or "",
                dashboard_title=self._manager.get("dashboard_title") or "",
                kpis_json=json.dumps(self._manager.get("kpis", [])),
                chart_meta_json=json.dumps(chart_meta),
                layout_mode=self._manager.get("layout_mode") or "portrait",
                tile_geometry_json=json.dumps(tile_geometry),
                pages_json=pages_json,
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

        # -- Multi-page state -- (drafts saved before pages existed fall
        # back to a single Overview page carrying the restored geometry.)
        try:
            from services import page_state
            raw_pages = draft.get("pages_json")
            if raw_pages:
                page_state.apply_to_bridge(self._manager.set, raw_pages)
            elif isinstance(tile_geometry, dict) and tile_geometry:
                # Legacy draft: wrap everything onto one default page so the
                # multi-page machinery finds a coherent state.
                pid = page_state.DEFAULT_PAGE_ID
                page_state.apply_to_bridge(self._manager.set, {
                    "version": 1, "active": pid,
                    "pages": [{"id": pid, "name": page_state.DEFAULT_PAGE_NAME}],
                    "chart_pages": {},
                    "page_geometry": {pid: dict(tile_geometry)},
                    "page_kpis": {pid: list(self._manager.get("kpis", []) or [])},
                    "page_slicers": {pid: [], "__global__": list(self._manager.get("slicers", []) or [])},
                })
            else:
                page_state.apply_to_bridge(self._manager.set, None)
        except Exception as exc:
            log.warning("restore_draft: page state failed: %s", exc)

        log.info("restore_draft: restored %d chart(s) for user %s",
                 len(charts), user_id)
        return True
