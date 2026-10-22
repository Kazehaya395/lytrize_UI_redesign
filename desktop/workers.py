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
    polars_ready = Signal(object)     # emits optional PolarsDataset snapshot
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
                # Build the native query snapshot from the original CSV. This
                # avoids making the first Polars chart slice depend on a
                # pandas conversion. The pandas frame remains temporarily for
                # legacy table/cleaning pages during the staged migration.
                try:
                    from services.polars_engine import open_csv
                    polars_data = open_csv(path)
                except Exception as exc:
                    log.info("Polars CSV path unavailable; using compatibility boundary: %s", exc)
                    polars_data = None
            else:
                df = pd.read_excel(path)
                try:
                    from services.polars_engine import PolarsDataset
                    polars_data = PolarsDataset.from_pandas(
                        df, source=str(path))
                except Exception as exc:
                    log.info("Polars Excel compatibility conversion unavailable: %s", exc)
                    polars_data = None
            self.finished_ok.emit(df)
            self.polars_ready.emit(polars_data)
        except Exception as exc:
            log.error("ImportWorker failed: %s", exc)
            self.failed.emit(str(exc))


class ChartGenerationWorker(QThread):
    """Generate charts or native ChartModels in a background thread.

    ``layout_opts`` / ``typography_opts`` (from the Layout Options and
    Typography Options settings tabs) are applied to every produced
    figure right after generation so a chart is born already matching
    the user's display settings -- see ``chart_display.apply_display_options``.
    """

    finished_ok = Signal(object)      # list of (uid, title, legacy figure/model)
    failed = Signal(str)              # emits error message

    def __init__(self, aid: str, df: pd.DataFrame, widget_values: dict,
                 parent=None, *, layout_opts: Optional[dict] = None,
                 typography_opts: Optional[dict] = None,
                 roles: Optional[dict] = None, polars_dataset=None) -> None:
        super().__init__(parent)
        self._aid = aid
        self._df = df
        self._widget_values = widget_values
        self._layout_opts = dict(layout_opts) if layout_opts else None
        self._typo_opts = dict(typography_opts) if typography_opts else None
        if self._typo_opts:
            try:
                from font_bundle import resolve
                for key in ("family", "header_family"):
                    if self._typo_opts.get(key):
                        self._typo_opts[key] = resolve(self._typo_opts[key])
            except Exception:
                log.debug("font family resolution unavailable", exc_info=True)
        # Resolved column families from the page. Passing them keeps the
        # service-side defaults (e.g. "first four metrics") consistent with
        # the drop-downs the user actually saw, so the user's Column
        # Classification governs generation as well as the form.
        self._roles = roles
        self._polars_dataset = polars_dataset

    def _with_display_options(self, models):
        """Apply the selected ECharts display contract to every built model."""
        from services.polars_chart_service import apply_chart_model_display_options
        result = []
        for model in models or []:
            if not model:
                continue
            result.append(apply_chart_model_display_options(
                model, self._layout_opts or {}, self._typo_opts or {}))
        return result

    def run(self) -> None:
        try:
            if self._aid == "descriptive":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_descriptive_table_chart
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                model = build_descriptive_table_chart(
                    dataset, roles=self._roles, layout=self._layout_opts,
                    typography=self._typo_opts,
                )
                self.finished_ok.emit(
                    [(item["uid"], item["title"], item)
                     for item in self._with_display_options([model])])
                return

            if self._aid == "pie_chart":
                # First native vertical slice: Polars performs the group and
                # aggregation, ChartModel carries the result, and the Analysis
                # page displays it with ECharts. This path does not construct
                # a Plotly figure or pass through the legacy chart runner.
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_pie_chart
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                model = build_pie_chart(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit(
                    [(item["uid"], item["title"], item)
                     for item in self._with_display_options([model])])
                return

            if self._aid == "categorical":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_categorical_charts
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                models = build_categorical_charts(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit([
                    (model["uid"], model["title"], model)
                    for model in self._with_display_options(models)
                ])
                return

            if self._aid == "statistical":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_statistical_charts
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                models = build_statistical_charts(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit([
                    (model["uid"], model["title"], model)
                    for model in self._with_display_options(models)
                ])
                return

            if self._aid == "correlation":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_correlation_chart
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                model = build_correlation_chart(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit(
                    [(item["uid"], item["title"], item)
                     for item in self._with_display_options([model])])
                return

            if self._aid == "matrix_heatmap":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_matrix_heatmap_chart
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                model = build_matrix_heatmap_chart(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit(
                    [(item["uid"], item["title"], item)
                     for item in self._with_display_options([model])])
                return

            if self._aid == "matrix_table":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_matrix_table_chart
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                model = build_matrix_table_chart(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit(
                    [(item["uid"], item["title"], item)
                     for item in self._with_display_options([model])])
                return

            if self._aid == "time_series":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_time_series_chart
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                model = build_time_series_chart(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit(
                    [(item["uid"], item["title"], item)
                     for item in self._with_display_options([model])])
                return

            if self._aid == "distribution":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_distribution_charts
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                models = build_distribution_charts(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit([
                    (model["uid"], model["title"], model)
                    for model in self._with_display_options(models)
                ])
                return

            if self._aid == "scatter_plot":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_scatter_chart
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                model = build_scatter_chart(
                    dataset, self._widget_values, roles=self._roles,
                    layout=self._layout_opts, typography=self._typo_opts,
                )
                self.finished_ok.emit(
                    [(item["uid"], item["title"], item)
                     for item in self._with_display_options([model])])
                return

            if self._aid == "outlier":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_outlier_charts
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                columns = (self._widget_values.get("x_cols")
                           or self._widget_values.get("columns")
                           or (self._roles or {}).get("numeric")
                           or dataset.roles().get("numeric", [])[:6])
                models = build_outlier_charts(
                    dataset, columns,
                    multiplier=self._widget_values.get("multiplier", 1.5),
                    typography=self._typo_opts,
                )
                self.finished_ok.emit([
                    (model["uid"], model["title"], model)
                    for model in self._with_display_options(models)
                ])
                return

            if self._aid == "data_quality":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_data_quality_charts
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                models = build_data_quality_charts(dataset)
                self.finished_ok.emit([
                    (model["uid"], model["title"], model)
                    for model in self._with_display_options(models)
                ])
                return

            if self._aid == "map_plot":
                from services.polars_engine import PolarsDataset
                from services.polars_chart_service import build_map_plot_chart
                dataset = (self._polars_dataset or
                           PolarsDataset.from_pandas(self._df))
                model = build_map_plot_chart(
                    dataset, self._widget_values, layout=self._layout_opts,
                    typography=self._typo_opts,
                )
                # Map Plot is an offline native route. Unsupported or empty
                # spatial input reports no chart instead of silently calling
                # the old network-backed Plotly basemap runner.
                self.finished_ok.emit(
                    [(item["uid"], item["title"], item)
                     for item in self._with_display_options([model])])
                return

            from services.analysis_service import run_analysis_with_config

            results = run_analysis_with_config(self._aid, self._df,
                                                self._widget_values,
                                                roles=self._roles)
            apply_display = (self._layout_opts is not None
                             or self._typo_opts is not None)
            out = []
            for uid, title, fig in results:
                if apply_display:
                    from services.polars_chart_service import apply_chart_model_display_options
                    fig = apply_chart_model_display_options(
                        fig, self._layout_opts or {}, self._typo_opts or {})
                    fig["uid"] = uid
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
                 extra_css: str = "", report_payload: Optional[dict] = None,
                 report_specs: Optional[dict] = None, parent=None) -> None:
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
        self._report_payload = dict(report_payload or {})
        self._report_specs = dict(report_specs or {})

    def run(self) -> None:
        try:
            # A newer regen may have superseded this build while it sat in the
            # queue. Bail before the multi-second build_export_report rather
            # than running it for a result that gets discarded -- the canvas
            # freeze under rapid edits.
            if self.isInterruptionRequested():
                return
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
                # The report payload is column-pruned and built in the input
                # worker. Embed it in the preview so slicers filter this
                # already-loaded ECharts board in place; the UI does not
                # rebuild and reload the document on each selection.
                extra_css=self._extra_css,
                editor=True,
                report_payload=self._report_payload,
                report_specs=self._report_specs,
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
                active_slicers, affected_charts, filter_frames_by_chart,
            )
            from services.export_service import build_export_charts
            from services.filter_service import distinct_values

            charts = list(self._snapshot.get("charts") or [])
            slicers = list(self._snapshot.get("slicers") or [])
            df = self._snapshot.get("df")
            dataset = self._snapshot.get("polars_dataset")
            recipes = self._snapshot.get("recipes") or {}
            cols = int(self._snapshot.get("grid_cols_n", 2))
            kpis = list(self._snapshot.get("kpis") or [])

            # Build the browser's column-pruned payload first. With this
            # payload present, the already-loaded ECharts document can filter
            # and update itself; Python only needs to rebuild chart snapshots
            # when interactive data is unavailable.
            report_payload = None
            if dataset is not None or (df is not None and not getattr(df, "empty", True)):
                try:
                    from modules.report_data import (
                        build_report_payload, build_report_payload_polars,
                    )
                    if dataset is not None:
                        report_payload = build_report_payload_polars(
                            dataset, recipes, slicers, kpis)
                    if (not report_payload or not report_payload.get("columns")) and \
                            df is not None and not getattr(df, "empty", True):
                        report_payload = build_report_payload(
                            df, recipes, slicers, kpis)
                except Exception:
                    log.debug("report payload not built; static export",
                              exc_info=True)
                    report_payload = None

            has_live_data = bool(report_payload and report_payload.get("columns"))

            # Geometry stays on the page (_current_geometry reads bridge
            # state + does a grid-blob migration; duplicating it here would
            # drift). The worker returns filtered charts + payload; the page
            # merges its cheap geometry maps afterwards.
            if (not has_live_data and charts and active_slicers(slicers)
                    and df is not None and not df.empty and recipes):
                try:
                    hit = set(affected_charts(recipes, slicers))
                    # Each chart re-runs against only the slicers that
                    # constrain it (report-scope = all; chart-scope = target),
                    # with each distinct filter set computed once. A report
                    # slicer is universal, so this re-runs every chart, but a
                    # report-only board still filters the data a single time.
                    frames = filter_frames_by_chart(
                        df, slicers,
                        [c[0] for c in charts if c[0] in hit])
                except Exception:
                    log.warning("ReportInputsWorker: filter failed; "
                                "charts left unfiltered", exc_info=True)
                    frames = {}
                if frames:
                    out = []
                    for chart in charts:
                        # A newer regen superseded this one: stop re-running
                        # recipes mid-way rather than finishing work whose
                        # result will be thrown away. This is what stops a
                        # burst of drags from piling up concurrent full
                        # re-runs over millions of rows -- the "canvas freeze"
                        # report.
                        if self.isInterruptionRequested():
                            return
                        uid = chart[0]
                        if uid not in frames:
                            out.append(chart)
                            continue
                        rebuilt = run_chart_recipe(recipes[uid], frames[uid])
                        out.append((uid, chart[1], rebuilt[2], *chart[3:])
                                   if rebuilt else chart)
                    charts = out

            meta_map = self._snapshot.get("meta_map") or {}
            notes = self._snapshot.get("notes") or {}
            export_charts = build_export_charts(
                charts, meta_map=meta_map, notes=notes)

            slicer_values: dict = {}
            if dataset is not None or (df is not None and not getattr(df, "empty", True)):
                for i, s in enumerate(slicers):
                    if not isinstance(s, dict):
                        continue
                    if str(s.get("widget") or "").lower()                             not in ("list", "dropdown"):
                        continue
                    try:
                        column = str(s.get("column") or "")
                        if dataset is not None and column in dataset.schema:
                            import polars as pl
                            candidates = (dataset.frame.select(
                                pl.col(column).drop_nulls().unique(
                                    maintain_order=True).head(500))
                                .collect()[column].to_list())
                            slicer_values[str(i)] = [str(value) for value in candidates]
                        elif df is not None:
                            slicer_values[str(i)] = distinct_values(df, column)
                    except Exception:
                        continue

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
