"""desktop/workers.py -- QThread workers for long-running operations.

Prevents UI blocking during heavy dataframe, chart generation, and export
operations.  Each worker runs in its own QThread and communicates results
back to the main thread via Qt signals.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pandas as pd
from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


class ImportWorker(QThread):
    """Load a CSV/XLSX file in a background thread."""

    finished_ok = Signal(object)      # emits pd.DataFrame
    failed = Signal(str)              # emits error message

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self._file_path = file_path

    def run(self) -> None:
        try:
            from pathlib import Path
            from modules.utils.perf import read_csv_fast

            path = Path(self._file_path)
            if path.suffix.lower() == ".csv":
                df = read_csv_fast(path)
            else:
                df = pd.read_excel(path)
            self.finished_ok.emit(df)
        except Exception as exc:
            log.error("ImportWorker failed: %s", exc)
            self.failed.emit(str(exc))


class ChartGenerationWorker(QThread):
    """Generate charts in a background thread.

    ``layout_opts`` / ``typography_opts`` (from the Layout Options and
    Typography Options settings tabs) are applied to every produced
    figure right after generation so a chart is born already matching
    the user's display settings -- see ``chart_display.apply_display_options``.
    """

    finished_ok = Signal(object)      # emits list of (uid, title, fig)
    failed = Signal(str)              # emits error message

    def __init__(self, aid: str, df: pd.DataFrame, widget_values: dict,
                 parent=None, *, layout_opts: Optional[dict] = None,
                 typography_opts: Optional[dict] = None,
                 roles: Optional[dict] = None) -> None:
        super().__init__(parent)
        self._aid = aid
        self._df = df
        self._widget_values = widget_values
        self._layout_opts = dict(layout_opts) if layout_opts else None
        self._typo_opts = dict(typography_opts) if typography_opts else None
        # Resolved column families from the page. Passing them keeps the
        # service-side defaults (e.g. "first four metrics") consistent with
        # the drop-downs the user actually saw, so the user's Column
        # Classification governs generation as well as the form.
        self._roles = roles

    def run(self) -> None:
        try:
            from services.analysis_service import run_analysis_with_config
            from services.chart_service import apply_hover_format
            from chart_display import apply_display_options

            results = run_analysis_with_config(self._aid, self._df,
                                                self._widget_values,
                                                roles=self._roles)
            apply_display = (self._layout_opts is not None
                             or self._typo_opts is not None)
            out = []
            for uid, title, fig in results:
                if apply_display:
                    # apply_display_options returns a NEW figure.
                    fig = apply_display_options(fig, self._layout_opts,
                                                self._typo_opts)
                apply_hover_format(fig)
                out.append((uid, title, fig))
            self.finished_ok.emit(out)
        except Exception as exc:
            log.error("ChartGenerationWorker failed: %s", exc)
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    """Generate an HTML export in a background thread."""

    finished_ok = Signal(str, str)    # emits html, safe_file
    failed = Signal(str)              # emits error message

    def __init__(self, export_charts, session_name: str, state: dict,
                 orientation: str = "portrait", kpis=None,
                 dashboard_title: str = "", grid_cols_n: int = 2,
                 tile_geometry: Optional[dict] = None,
                 kpi_geometry: Optional[dict] = None,
                 slicers: Optional[list] = None,
                 slicer_geometry: Optional[dict] = None,
                 slicer_values: Optional[dict] = None,
                 report_payload: Optional[dict] = None,
                 report_specs: Optional[dict] = None,
                 geometry_mode: str = "free",
                 parent=None) -> None:
        super().__init__(parent)
        # ALREADY-BUILT export tuples, not the raw session charts. The session
        # holds bare (uid, title, fig), which carries neither notes nor
        # chart_type, so rebuilding them here would quietly drop both.
        self._charts = list(export_charts or [])
        self._session_name = session_name
        self._state = state
        self._orientation = orientation
        self._kpis = kpis
        self._dashboard_title = dashboard_title
        self._grid_cols_n = grid_cols_n
        # Snapshot the arrangement now: the worker runs on its own thread, so
        # reading it lazily later would race with further user drags.
        self._tile_geometry = dict(tile_geometry or {})
        # KPI placement too: the caller supplies both maps, and accepting only
        # one made EVERY export fail with a TypeError before a byte was written.
        self._kpi_geometry = dict(kpi_geometry or {})
        # Slicers likewise. These three are read on the calling thread and
        # copied, for the same reason as the geometry maps: a slicer click on
        # the canvas must not change what an in-flight export is building.
        self._slicers = list(slicers or [])
        self._slicer_geometry = dict(slicer_geometry or {})
        self._slicer_values = dict(slicer_values or {})
        # The column-pruned dataset + per-chart specs for the in-browser
        # slicer engine. Built on the CALLING thread (only the dashboard has
        # the DataFrame; the export path itself is deliberately headless) and
        # snapshotted here for the same reason as the geometry maps. None is
        # a valid value: without slicers there is no engine to feed, and
        # generate_html_report then emits a purely static document.
        self._report_payload = dict(report_payload) if report_payload else None
        self._report_specs = dict(report_specs) if report_specs else None
        self._geometry_mode = str(geometry_mode or "free")

    def run(self) -> None:
        try:
            from services.export_service import build_export_report

            html, safe_file = build_export_report(
                self._charts, self._session_name, self._state,
                orientation=self._orientation,
                kpis=self._kpis,
                dashboard_title=self._dashboard_title,
                grid_cols_n=self._grid_cols_n,
                tile_geometry=self._tile_geometry,
                kpi_geometry=self._kpi_geometry,
                slicers=self._slicers,
                slicer_geometry=self._slicer_geometry,
                slicer_values=self._slicer_values,
                report_payload=self._report_payload,
                report_specs=self._report_specs,
                geometry_mode=self._geometry_mode,
            )
            self.finished_ok.emit(html, safe_file)
        except Exception as exc:
            log.error("ExportWorker failed: %s", exc)
            self.failed.emit(str(exc))


class ReportPreviewWorker(QThread):
    """Build the live dashboard document in a background thread.

    Separate from ExportWorker on purpose. That one owns a file dialog and a
    PNG render; this one fires every time a setting changes, so it does nothing
    but produce an HTML string and carries no dialogs, no prompts and no
    save-file responsibility that a 250 ms debounce would have to wait through.

    Emits the request's ``token`` alongside the HTML so the page can discard a
    render that finished after a newer one already replaced it.
    """

    ready = Signal(int, str)      # token, html
    failed = Signal(int, str)     # token, error message

    def __init__(self, export_charts, session_name: str, state: dict,
                 token: int,
                 orientation: str = "portrait", kpis=None,
                 dashboard_title: str = "", grid_cols_n: int = 2,
                 tile_geometry: Optional[dict] = None,
                 kpi_geometry: Optional[dict] = None,
                 slicers: Optional[list] = None,
                 slicer_geometry: Optional[dict] = None,
                 slicer_values: Optional[dict] = None,
                 geometry_mode: str = "free",
                 extra_css: str = "", parent=None) -> None:
        super().__init__(parent)
        # ALREADY-BUILT export tuples, exactly as ExportWorker takes them, so
        # the canvas and the saved file are built from the same data. Handing
        # this the raw session charts instead is what made notes appear in the
        # exported file and not on the canvas.
        self._charts = list(export_charts or [])
        self._session_name = session_name
        self._state = dict(state or {})
        self._token = int(token)
        self._orientation = orientation
        self._kpis = list(kpis or [])
        self._dashboard_title = dashboard_title
        self._grid_cols_n = grid_cols_n
        # Snapshot on the calling thread: this runs on its own, so reading
        # these lazily would race with further user gestures.
        self._tile_geometry = dict(tile_geometry or {})
        self._kpi_geometry = dict(kpi_geometry or {})
        # Slicers are part of the SAME document the saved file is built from,
        # so they are snapshotted here on the calling thread rather than read
        # back from the session when the worker runs.
        self._slicers = list(slicers or [])
        self._slicer_geometry = dict(slicer_geometry or {})
        self._slicer_values = dict(slicer_values or {})
        self._geometry_mode = str(geometry_mode or "free")
        self._extra_css = str(extra_css or "")

    def run(self) -> None:
        try:
            from services.export_service import build_export_report

            html, _safe = build_export_report(
                self._charts, self._session_name, self._state,
                orientation=self._orientation,
                kpis=self._kpis,
                dashboard_title=self._dashboard_title,
                grid_cols_n=self._grid_cols_n,
                tile_geometry=self._tile_geometry,
                kpi_geometry=self._kpi_geometry,
                slicers=self._slicers,
                slicer_geometry=self._slicer_geometry,
                slicer_values=self._slicer_values,
                geometry_mode=self._geometry_mode,
                # NO report_payload/report_specs here, deliberately: the live
                # canvas drives its slicers PYTHON-side (a click re-runs the
                # report and pushes fresh figures), and embedding the browser
                # engine + dataset would make every click apply twice through
                # two renderers. The SAVED file (ExportWorker) gets them.
                # The bundle is served as a sibling file by ReportCanvas;
                # embedding 4.5 MB on every settings tweak is not viable.
                inline_plotly=False,
                extra_css=self._extra_css,
                editor=True,
            )
            self.ready.emit(self._token, html)
        except Exception as exc:
            log.error("ReportPreviewWorker failed: %s", exc)
            self.failed.emit(self._token, str(exc))


class ReportInputsWorker(QThread):
    """Assemble preview/export inputs off the UI thread.

    ``_export_inputs()`` re-runs slicer-affected chart recipes over the full
    DataFrame, downsamples figures, and JSON-serialises the report payload --
    all synchronous on the caller. Called after every drag release, every
    slicer click and every export, it was the "whole app freezes for a few
    seconds" report. The page snapshots cheap bridge state (plain dicts +
    the df reference) and this worker does the assembly; the result feeds
    ReportPreviewWorker/ExportWorker exactly as before. ``token`` lets the
    page discard a stale assembly when a newer one was requested.
    """

    ready = Signal(int, object)     # emits token, inputs dict
    failed = Signal(int, str)       # emits token, error message

    def __init__(self, token: int, snapshot: dict, parent=None) -> None:
        super().__init__(parent)
        self._token = token
        self._snapshot = snapshot

    def run(self) -> None:
        try:
            from services.chart_service import run_chart_recipe
            from services.slicer_service import (
                active_slicers, affected_charts, filter_frame,
            )
            from services.export_service import build_export_charts
            from modules.report_data import build_report_payload
            from services.filter_service import distinct_values

            charts = list(self._snapshot.get("charts") or [])
            slicers = list(self._snapshot.get("slicers") or [])
            df = self._snapshot.get("df")
            recipes = self._snapshot.get("recipes") or {}
            cols = int(self._snapshot.get("grid_cols_n", 2))

            # Geometry stays on the page (_current_geometry reads bridge
            # state + does a grid-blob migration; duplicating it here would
            # drift). The worker returns filtered charts + payload; the page
            # merges its cheap geometry maps afterwards.
            if charts and active_slicers(slicers) and df is not None                     and not df.empty:
                try:
                    filtered = filter_frame(df, slicers)
                except Exception:
                    filtered = None
                if filtered is not None:
                    hit = set(affected_charts(recipes, slicers)) if recipes                         else set()
                    out = []
                    for chart in charts:
                        uid = chart[0]
                        if uid not in hit or uid not in recipes:
                            out.append(chart)
                            continue
                        rebuilt = run_chart_recipe(recipes[uid], filtered)
                        out.append((uid, chart[1], rebuilt[2], *chart[3:])
                                   if rebuilt else chart)
                    charts = out

            meta_map = self._snapshot.get("meta_map") or {}
            notes = self._snapshot.get("notes") or {}
            export_charts = build_export_charts(
                charts, meta_map=meta_map, notes=notes)

            slicer_values: dict = {}
            if df is not None and not df.empty:
                for i, s in enumerate(slicers):
                    if not isinstance(s, dict):
                        continue
                    if str(s.get("widget") or "").lower()                             not in ("list", "dropdown"):
                        continue
                    try:
                        slicer_values[str(i)] = distinct_values(
                            df, str(s.get("column") or ""))
                    except Exception:
                        continue

            report_payload = None
            if slicers and df is not None:
                try:
                    report_payload = build_report_payload(
                        df, meta_map, slicers)
                except Exception:
                    report_payload = None

            self.ready.emit(self._token, {
                "charts": charts,
                "export_charts": export_charts,
                "slicer_values": slicer_values,
                "report_payload": report_payload,
            })
        except Exception as exc:
            log.error("ReportInputsWorker failed: %s", exc)
            self.failed.emit(self._token, str(exc))


class SnapshotWorker(QThread):
    """Save a DataFrame snapshot in a background thread."""

    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, user_id: int, df: pd.DataFrame, parent=None) -> None:
        super().__init__(parent)
        self._user_id = user_id
        self._df = df

    def run(self) -> None:
        try:
            from services.session_service import SessionManager
            sm = SessionManager()
            sm.save_df_snapshot(self._user_id, self._df)
            self.finished_ok.emit()
        except Exception as exc:
            log.error("SnapshotWorker failed: %s", exc)
            self.failed.emit(str(exc))


class TransformWorker(QThread):
    """Run a data transform operation in a background thread.

    Generic worker that takes a callable and its arguments, runs it,
    and emits the result. Used for text clean, find/replace, column ops, etc.
    """

    finished_ok = Signal(object)   # emits result (DataFrame or Series)
    failed = Signal(str)           # emits error message
    progress = Signal(int)         # emits percentage (0-100)

    def __init__(self, func: Callable, *args, parent=None) -> None:
        super().__init__(parent)
        self._func = func
        self._args = args

    def run(self) -> None:
        try:
            self.progress.emit(10)
            result = self._func(*self._args)
            self.progress.emit(100)
            self.finished_ok.emit(result)
        except Exception as exc:
            log.error("TransformWorker failed: %s", exc)
            self.failed.emit(str(exc))


class ExcelMergeWorker(QThread):
    """Merge multiple Excel sheets in a background thread."""

    finished_ok = Signal(object)   # emits merged DataFrame
    failed = Signal(str)           # emits error message

    def __init__(self, fact_df: pd.DataFrame, join_configs: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._fact_df = fact_df
        self._join_configs = join_configs

    def run(self) -> None:
        try:
            merged = self._fact_df.copy()
            merge_log = []

            for cfg in self._join_configs:
                dim_subset = cfg["dim_df"][[cfg["dim_key"]] + cfg["cols_to_bring"]].copy()
                overlap = [c for c in cfg["cols_to_bring"] if c in merged.columns]
                if overlap:
                    rename_map = {c: f"{c}_{cfg['dim_name']}" for c in overlap}
                    dim_subset = dim_subset.rename(columns=rename_map)

                before = len(merged)
                merged = merged.merge(
                    dim_subset,
                    left_on=cfg["fact_key"],
                    right_on=cfg["dim_key"],
                    how=cfg["join_type"],
                    suffixes=("", f"_{cfg['dim_name']}"),
                )
                after = len(merged)
                merge_log.append(f"✅ Joined {cfg['dim_name']} ({cfg['join_type']}): {before:,} → {after:,} rows")

                if cfg["dim_key"] in merged.columns and cfg["dim_key"] != cfg["fact_key"]:
                    merged = merged.drop(columns=[cfg["dim_key"]], errors="ignore")

            self.finished_ok.emit((merged, merge_log))
        except Exception as exc:
            log.error("ExcelMergeWorker failed: %s", exc)
            self.failed.emit(str(exc))
