"""desktop/pages/upload_page.py -- Upload & Transform page (Qt).

Replaces the Streamlit ``page_upload()`` with a native Qt page that:
  - Uses QFileDialog for CSV/XLSX import
  - Shows a QTableView with a model-backed preview
  - Supports search, sorting, and row preview modes
  - Persists the DataFrame via the SessionBridge
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QTableView, QLineEdit, QComboBox, QGroupBox,
    QMessageBox, QProgressBar, QSplitter, QFrame, QTabWidget, QHeaderView,
    QScrollArea,
)

from desktop.session_bridge import SessionBridge
from desktop.widgets.dataframe_model import DataFrameModel
from desktop.workers import ImportWorker
from desktop.pages.column_classifier_panel import ColumnClassifierPanel
from desktop.pages.column_manager_panel import ColumnManagerPanel
from desktop.pages.data_cleaner_panel import DataCleanerPanel
from desktop.pages.dtype_transformer_panel import DtypeTransformerPanel
from desktop.pages.excel_loader_panel import ExcelLoaderPanel
from modules.utils.perf import read_csv_fast, mem_mb

log = logging.getLogger(__name__)


class UploadPage(QWidget):
    """Upload & Transform page."""

    # Emitted when the user wants to navigate to the analysis page
    navigate_requested = Signal(str)

    # Emitted when column classification is confirmed, passes the classification dict
    classification_ready = Signal(dict)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._model = DataFrameModel()
        self._current_file: Optional[str] = None

        # Debounce timers: search-across-all-columns and per-column filters
        # both scan the full DataFrame (149k+ rows is common), so running
        # that on every keystroke is what makes typing feel laggy. A short
        # delay after the user stops typing avoids a full scan per
        # character while still feeling immediate once they pause.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._run_search)
        self._pending_search_text = ""

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(self._run_filters)

        self._build_ui()
        self._connect_signals()

        # If a DataFrame is already loaded, show it
        if self.bridge.df is not None:
            self._model.set_dataframe(self.bridge.df)
            self._update_preview_label()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("📂 Upload Dataset")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #e2e8f0;")
        header.addWidget(title)
        header.addStretch()

        self.btn_import = QPushButton("📁 Import CSV / Excel")
        self.btn_import.setStyleSheet(
            "QPushButton {"
            "  background: #4f46e5; color: white; border: none;"
            "  border-radius: 8px; padding: 8px 16px; font-weight: 600;"
            "}"
            "QPushButton:hover { background: #6366f1; }"
        )
        header.addWidget(self.btn_import)
        root.addLayout(header)

        # -- File info --
        self.lbl_file = QLabel("No file loaded")
        self.lbl_file.setStyleSheet("font-size: 13px; color: #94a3b8;")
        root.addWidget(self.lbl_file)

        # -- Progress bar (hidden until import) --
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # -- Preview controls --
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Search rows…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setStyleSheet(
            "QLineEdit {"
            "  background: #1e293b; color: #e2e8f0; border: 1px solid #334155;"
            "  border-radius: 6px; padding: 6px 10px;"
            "}"
        )
        controls.addWidget(self.search_box, stretch=1)

        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["Top 10", "Bottom 10", "Random 10", "All rows"])
        self.preview_mode.setStyleSheet(
            "QComboBox {"
            "  background: #1e293b; color: #e2e8f0; border: 1px solid #334155;"
            "  border-radius: 6px; padding: 6px 10px;"
            "}"
        )
        controls.addWidget(self.preview_mode)

        self.btn_go_analysis = QPushButton("📊 Go to Analysis →")
        self.btn_go_analysis.setStyleSheet(
            "QPushButton {"
            "  background: #10b981; color: white; border: none;"
            "  border-radius: 8px; padding: 8px 16px; font-weight: 600;"
            "}"
            "QPushButton:hover { background: #34d399; }"
            "QPushButton:disabled { background: #1e293b; color: #475569; }"
        )
        self.btn_go_analysis.setEnabled(False)
        controls.addWidget(self.btn_go_analysis)

        self.btn_save_csv = QPushButton("💾 Save CSV")
        self.btn_save_csv.setStyleSheet(
            "QPushButton {"
            "  background: #6366f1; color: white; border: none;"
            "  border-radius: 8px; padding: 8px 16px; font-weight: 600;"
            "}"
            "QPushButton:hover { background: #818cf8; }"
            "QPushButton:disabled { background: #1e293b; color: #475569; }"
        )
        self.btn_save_csv.setEnabled(False)
        self.btn_save_csv.clicked.connect(self._on_save_csv)
        controls.addWidget(self.btn_save_csv)

        root.addLayout(controls)

        # -- Stats label --
        self.lbl_stats = QLabel("")
        self.lbl_stats.setStyleSheet("font-size: 12px; color: #64748b;")
        root.addWidget(self.lbl_stats)

        # -- Filter row --
        self.filter_row = QWidget()
        filter_layout = QHBoxLayout(self.filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(4)
        self._filter_inputs = []
        root.addWidget(self.filter_row)

        # -- Table view --
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        # Fixed row height avoids per-row sizeHint recalculation on every
        # geometry pass (viewport resize, splitter drag, model reset).
        # With 100k+ rows the default Interactive/ContentBased sizing makes
        # any resize or drag feel stepped/laggy since Qt has to reconsult
        # row sizing on each pass; a fixed height sidesteps that entirely.
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setStyleSheet(
            "QTableView {"
            "  background: #0f172a; color: #e2e8f0;"
            "  border: 1px solid #1e293b; border-radius: 8px;"
            "  gridline-color: #1e293b;"
            "}"
            "QTableView::item:selected { background: rgba(99,102,241,0.3); }"
            "QHeaderView::section {"
            "  background: #1e293b; color: #94a3b8;"
            "  border: none; padding: 6px; font-weight: 600;"
            "}"
        )
        # -- Transform Tools Tabs (hidden until data loaded) --
        self._transform_tabs = QTabWidget()
        self._transform_tabs.setStyleSheet(
            "QTabWidget { background: transparent; }"
            "QTabBar::tab {"
            "  background: #1e293b; color: #94a3b8;"
            "  border: 1px solid #334155; border-bottom: none;"
            "  border-top-left-radius: 6px; border-top-right-radius: 6px;"
            "  padding: 8px 16px; font-size: 12px;"
            "}"
            "QTabBar::tab:selected { background: #0f172a; color: #e2e8f0; }"
        )

        # Column Classification tab -- goes first since classifying column
        # types is the natural first step after import, before cleaning
        # or managing columns. Previously this panel was added directly to
        # the page's root layout below the splitter, as its own full-width
        # container -- that's why it always took a large fixed block of
        # space regardless of which other tab was active. It now lives
        # inside the same tab bar as the other transform tools, matching
        # how Column Manager/Data Cleaner/etc. already behave.
        self._classifier_panel = ColumnClassifierPanel(self.bridge)
        self._classifier_panel.classification_confirmed.connect(self._on_classification_confirmed)
        self._transform_tabs.addTab(self._classifier_panel, "🏷️ Column Classification")

        # Column Manager tab
        self._column_manager_panel = ColumnManagerPanel(self.bridge)
        self._column_manager_panel.operation_applied.connect(self._on_operation_applied)
        self._transform_tabs.addTab(self._column_manager_panel, "🛠️ Column Manager")

        # Data Cleaner tab
        self._data_cleaner_panel = DataCleanerPanel(self.bridge)
        self._data_cleaner_panel.operation_applied.connect(self._on_operation_applied)
        self._transform_tabs.addTab(self._data_cleaner_panel, "🧹 Data Cleaner")

        # Dtype Transformer tab
        self._dtype_transformer_panel = DtypeTransformerPanel(self.bridge)
        self._dtype_transformer_panel.operation_applied.connect(self._on_operation_applied)
        self._transform_tabs.addTab(self._dtype_transformer_panel, "🔍 Dtype Transformer")

        # Excel Loader tab (hidden for CSV files)
        self._excel_loader_panel = ExcelLoaderPanel(self.bridge)
        self._excel_loader_panel.sheet_selected.connect(self._on_excel_sheet_selected)
        self._excel_loader_panel.merged_table_ready.connect(self._on_excel_merged_ready)
        self._transform_tabs.addTab(self._excel_loader_panel, "📑 Excel Sheets")

        # -- Splitter for resizable table / transform area --
        # Both panes live in the SAME splitter so a real drag handle appears
        # between them (a splitter with only one child renders no usable handle).
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #1e293b; }"
            "QSplitter::handle:hover { background: #334155; }"
        )
        splitter.addWidget(self.table)

        # Wrap the transform-tools tab widget in a QScrollArea rather than
        # adding it to the splitter directly. A bare QTabWidget's
        # minimumSizeHint is the SUM of every stacked control in whichever
        # sub-tab is tallest (Data Cleaner's Validate tab alone easily
        # reaches 400+px once its label/combo/pattern-field/checkbox/
        # preview-table are all stacked) -- and that floor applies to the
        # whole splitter pane even when a much smaller tab like "Add
        # Column" is the one actually visible. A resizable QScrollArea
        # reports a small, mostly-fixed minimum (just enough for its own
        # chrome) independent of its content's natural size, and scrolls
        # the content instead of forcing the pane to stay tall. This is
        # the same technique already used for the Dashboard page's left
        # control panel.
        self._transform_scroll = QScrollArea()
        self._transform_scroll.setWidgetResizable(True)
        self._transform_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self._transform_scroll.setWidget(self._transform_tabs)
        self._transform_scroll.setVisible(False)
        splitter.addWidget(self._transform_scroll)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        # childrenCollapsible defaults to True in Qt, which let either pane
        # be dragged all the way to 0px and vanish completely (reported:
        # dragging toward the table made the whole transform-tools section,
        # tab bar included, disappear with no way to tell it was still
        # there). False + explicit small floors means dragging always
        # leaves at least the tab bar / a few table rows visible and
        # grabbable, instead of a full disappearing act.
        splitter.setChildrenCollapsible(False)
        self.table.setMinimumHeight(100)
        self._transform_scroll.setMinimumHeight(48)
        splitter.setSizes([400, 300])
        root.addWidget(splitter, stretch=1)

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.btn_import.clicked.connect(self._on_import)
        self.btn_go_analysis.clicked.connect(
            lambda: self.navigate_requested.emit("analysis")
        )
        self.preview_mode.currentIndexChanged.connect(self._on_preview_mode)
        self.search_box.textChanged.connect(self._on_search)
        self.bridge.df_changed.connect(self._on_df_changed)
        self.classification_ready.connect(self._on_classification_ready)

    # -- Slots ---------------------------------------------------------------

    def _on_import(self) -> None:
        """Open a file dialog and import the selected CSV/XLSX file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Dataset",
            str(Path.home()),
            "Data Files (*.csv *.xlsx *.xls);;CSV Files (*.csv);;Excel Files (*.xlsx *.xls)",
        )
        if not file_path:
            return

        self._current_file = file_path
        self.progress.setVisible(True)
        self.progress.setValue(10)
        self.btn_import.setEnabled(False)
        self.lbl_file.setText(f"⏳ Importing {Path(file_path).name}…")

        # Use a background worker to avoid blocking the UI
        self._import_worker = ImportWorker(file_path, self)
        self._import_worker.finished_ok.connect(self._on_import_finished)
        self._import_worker.failed.connect(self._on_import_failed)
        self._import_worker.start()

    def _on_import_finished(self, df: pd.DataFrame) -> None:
        """Handle successful import from the worker thread."""
        path = Path(self._current_file or "")
        self.progress.setValue(60)

        # Store in session
        self.bridge.set_df(df)
        self.bridge.set("file_name", path.name)
        self.bridge.set("file_signature", f"{path.name}:{path.stat().st_size}")

        # Update model
        self._model.set_dataframe(df)
        self._update_preview_label()
        self.btn_go_analysis.setEnabled(True)
        self.btn_save_csv.setEnabled(True)

        # Excel Sheets tab is inserted/removed based on file type, not just
        # enabled/disabled. For a .csv there's no sheet to pick, so the tab
        # is removed entirely instead of sitting there greyed out. For an
        # .xlsx it's moved to the FRONT and made the active tab, since
        # picking a sheet is a prerequisite step before the rest of the
        # transform tools make sense to touch.
        is_excel = path.suffix.lower() in (".xlsx", ".xls")
        excel_tab_index = self._transform_tabs.indexOf(self._excel_loader_panel)
        if is_excel:
            # BUG FOUND HERE: the Excel Loader panel's own load_file()
            # method already correctly populates the sheet list, the
            # single-sheet dropdown, and the fact/dimension table combos
            # for the multi-sheet join workflow -- but nothing anywhere
            # in the real import flow ever called it. The panel's
            # internal state (_sheet_names, _sheets) just stayed at its
            # empty initial values forever, which is why the sheet
            # browser rendered as a big empty box and the Step 1/2 join
            # controls looked completely dead -- they were dead, not
            # broken selection logic, just never given any data to
            # select from in the first place.
            self._excel_loader_panel.load_file(str(path))
            if excel_tab_index == -1:
                self._transform_tabs.insertTab(0, self._excel_loader_panel, "📑 Excel Sheets")
            elif excel_tab_index != 0:
                self._transform_tabs.removeTab(excel_tab_index)
                self._transform_tabs.insertTab(0, self._excel_loader_panel, "📑 Excel Sheets")
            self._transform_tabs.setCurrentIndex(0)
        else:
            if excel_tab_index != -1:
                self._transform_tabs.removeTab(excel_tab_index)
            self._transform_tabs.setCurrentIndex(0)

        # Save snapshot in background
        user_id = self.bridge.get("user_id")
        if user_id:
            self.bridge.save_df_snapshot_async(user_id)

        self.progress.setValue(100)
        self.lbl_file.setText(f"✅ {path.name} — {df.shape[0]:,} rows × {df.shape[1]} columns")

        self.progress.setVisible(False)
        self.btn_import.setEnabled(True)

    def _on_import_failed(self, error: str) -> None:
        """Handle import failure from the worker thread."""
        log.error("Import failed: %s", error)
        QMessageBox.critical(self, "Import Error", f"Failed to import file:\n{error}")
        self.lbl_file.setText("Import failed")
        self.progress.setVisible(False)
        self.btn_import.setEnabled(True)

    def _on_df_changed(self, df: pd.DataFrame) -> None:
        """Handle a DataFrame change from the session bridge."""
        self._model.set_dataframe(df)
        self._update_preview_label()
        self._build_filter_row(df)
        has_data = df is not None and len(df) > 0
        if has_data:
            self.btn_go_analysis.setEnabled(True)
            self.btn_save_csv.setEnabled(True)
            # Show transform tabs (now includes Column Classification as
            # its first tab, alongside Column Manager/Data Cleaner/etc.)
            self._transform_scroll.setVisible(True)
            if self._classifier_panel is not None:
                self._classifier_panel.set_dataframe(df)
            # Load data into all transform panels
            if self._column_manager_panel is not None:
                self._column_manager_panel.set_dataframe(df)
            if self._data_cleaner_panel is not None:
                self._data_cleaner_panel.set_dataframe(df)
            if self._dtype_transformer_panel is not None:
                self._dtype_transformer_panel.set_dataframe(df)
        else:
            self._transform_scroll.setVisible(False)

    def _on_preview_mode(self, index: int) -> None:
        """Handle preview mode change (Top/Bottom/Random/All)."""
        df = self.bridge.df
        if df is None:
            return

        mode = self.preview_mode.currentText()
        try:
            if mode == "Top 10":
                preview = df.head(10)
            elif mode == "Bottom 10":
                preview = df.tail(10)
            elif mode == "Random 10":
                preview = df.sample(min(10, len(df)), random_state=None)
            else:
                preview = df
            self._model.set_dataframe(preview)
        except Exception as exc:
            log.warning("Preview mode failed: %s", exc)

    def _on_search(self, text: str) -> None:
        """Debounce entry point: restart the timer on every keystroke
        instead of scanning the whole DataFrame immediately."""
        self._pending_search_text = text
        self._search_timer.start()

    def _run_search(self) -> None:
        """Filter the table by searching across all columns. Runs only
        after the user pauses typing for the debounce interval."""
        text = self._pending_search_text
        df = self.bridge.df
        if df is None or not text.strip():
            self._model.set_dataframe(df if df is not None else pd.DataFrame())
            return

        try:
            mask = df.astype(str).apply(
                lambda col: col.str.contains(text, case=False, na=False)
            ).any(axis=1)
            self._model.set_dataframe(df[mask])
        except Exception as exc:
            log.warning("Search failed: %s", exc)

    # -- Helpers -------------------------------------------------------------

    def _update_preview_label(self) -> None:
        """Update the stats label with column type counts."""
        df = self.bridge.df
        if df is None:
            self.lbl_stats.setText("")
            return

        num_c = len(df.select_dtypes("number").columns)
        cat_c = len(df.select_dtypes("object").columns)
        dt_c = len(df.select_dtypes("datetime").columns)
        self.lbl_stats.setText(
            f"🔢 {num_c} numeric  ·  🔤 {cat_c} text  ·  📅 {dt_c} datetime"
        )

    def _build_filter_row(self, df: pd.DataFrame) -> None:
        """Build per-column filter inputs based on DataFrame columns."""
        # Clear existing filters
        for inp in self._filter_inputs:
            inp.deleteLater()
        self._filter_inputs.clear()

        if df is None or df.empty:
            return

        # Create a filter input for each column
        for col in df.columns:
            inp = QLineEdit()
            inp.setPlaceholderText(f"Filter {col}")
            inp.setClearButtonEnabled(True)
            inp.setStyleSheet(
                "QLineEdit {"
                "  background: #1e293b; color: #e2e8f0; border: 1px solid #334155;"
                "  border-radius: 4px; padding: 4px 6px; font-size: 11px;"
                "  min-width: 80px;"
                "}"
                "QLineEdit:focus { border-color: #6366f1; }"
            )
            inp.textChanged.connect(lambda text, c=col: self._filter_timer.start())
            self._filter_inputs.append(inp)
            self.filter_row.layout().addWidget(inp)

    def _run_filters(self) -> None:
        """Apply all active per-column filters. Runs only after the user
        pauses typing for the debounce interval (see _filter_timer)."""
        df = self.bridge.df
        if df is None or df.empty:
            return

        filtered = df.copy()
        for inp, col in zip(self._filter_inputs, df.columns):
            text = inp.text().strip()
            if text:
                try:
                    mask = filtered[col].astype(str).str.contains(text, case=False, na=False)
                    filtered = filtered[mask]
                except Exception:
                    pass

        self._model.set_dataframe(filtered)

    # -- Operation feedback -------------------------------------------------

    def _on_operation_applied(self, message: str) -> None:
        """Show a status message when a transform operation succeeds."""
        self.lbl_file.setText(message)
        # Refresh all panels with current data
        df = self.bridge.df
        if df is not None:
            self._column_manager_panel.set_dataframe(df)
            self._data_cleaner_panel.set_dataframe(df)
            self._dtype_transformer_panel.set_dataframe(df)
            self.btn_save_csv.setEnabled(True)

    def _on_save_csv(self) -> None:
        """Save the current DataFrame to a CSV file."""
        df = self.bridge.df
        if df is None:
            QMessageBox.warning(self, "No Data", "No dataset loaded to save.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV",
            str(Path.home()),
            "CSV Files (*.csv)",
        )
        if not file_path:
            return

        if not file_path.endswith(".csv"):
            file_path += ".csv"

        try:
            df.to_csv(file_path, index=False)
            QMessageBox.information(
                self,
                "Success",
                f"Dataset saved successfully:\n{file_path}\n\n{df.shape[0]:,} rows × {df.shape[1]} columns",
            )
            self.lbl_file.setText(f"💾 Saved to {Path(file_path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Failed to save CSV:\n{exc}")

    # -- Excel handling -----------------------------------------------------

    def _on_excel_sheet_selected(self, df: pd.DataFrame, sheet_name: str) -> None:
        """Handle single sheet selection from Excel loader."""
        self.bridge.set_df(df)
        self.bridge.set("file_name", f"{sheet_name}.xlsx")
        self._update_preview_label()
        self.btn_go_analysis.setEnabled(True)

    def _on_excel_merged_ready(self, df: pd.DataFrame, summary: str) -> None:
        """Handle merged table from Excel loader."""
        self.bridge.set_df(df)
        self.bridge.set("file_name", "merged_table.xlsx")
        self._update_preview_label()
        self.btn_go_analysis.setEnabled(True)
        self.lbl_file.setText(f"✅ Merged table — {df.shape[0]:,} rows × {df.shape[1]} columns")

    # -- Column classification -----------------------------------------------

    def _on_classification_confirmed(self, classification: dict) -> None:
        """Handle classification confirmation from the panel."""
        # Store classification in session bridge
        self.bridge.set("num_cols", classification.get("numeric", []))
        self.bridge.set("cat_cols", classification.get("categorical", []))
        self.bridge.set("dt_cols", classification.get("datetime", []))
        # Emit signal to navigate to analysis
        self.classification_ready.emit(classification)

    def _on_classification_ready(self, classification: dict) -> None:
        """Navigate to analysis page when classification is confirmed."""
        self.navigate_requested.emit("analysis")
