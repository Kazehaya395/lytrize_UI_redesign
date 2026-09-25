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
                 typography_opts: Optional[dict] = None) -> None:
        super().__init__(parent)
        self._aid = aid
        self._df = df
        self._widget_values = widget_values
        self._layout_opts = dict(layout_opts) if layout_opts else None
        self._typo_opts = dict(typography_opts) if typography_opts else None

    def run(self) -> None:
        try:
            from services.analysis_service import run_analysis_with_config
            from services.chart_service import apply_hover_format
            from chart_display import apply_display_options

            results = run_analysis_with_config(self._aid, self._df, self._widget_values)
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

    def __init__(self, charts, session_name: str, state: dict,
                 orientation: str = "portrait", kpis=None,
                 dashboard_title: str = "", grid_cols_n: int = 2,
                 parent=None) -> None:
        super().__init__(parent)
        self._charts = charts
        self._session_name = session_name
        self._state = state
        self._orientation = orientation
        self._kpis = kpis
        self._dashboard_title = dashboard_title
        self._grid_cols_n = grid_cols_n

    def run(self) -> None:
        try:
            from services.export_service import build_export_report, build_export_charts

            export_charts = build_export_charts(self._charts)
            html, safe_file = build_export_report(
                export_charts, self._session_name, self._state,
                orientation=self._orientation,
                kpis=self._kpis,
                dashboard_title=self._dashboard_title,
                grid_cols_n=self._grid_cols_n,
            )
            self.finished_ok.emit(html, safe_file)
        except Exception as exc:
            log.error("ExportWorker failed: %s", exc)
            self.failed.emit(str(exc))


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
