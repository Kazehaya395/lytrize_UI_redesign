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

from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from desktop.widgets.dataframe_model import DataFrameModel
from desktop.widgets.table_index_header import auto_fit_index_header
from desktop.workers import ImportWorker
from desktop.pages.column_classifier_panel import ColumnClassifierPanel
from desktop.pages.column_manager_panel import ColumnManagerPanel
from desktop.pages.data_cleaner_panel import DataCleanerPanel
from desktop.pages.dtype_transformer_panel import DtypeTransformerPanel
from desktop.pages.excel_loader_panel import ExcelLoaderPanel
from modules.utils.perf import read_csv_fast, mem_mb
from services.transform_service import classify_columns
from services.column_roles import resolve_roles, value_sig_map

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

        # If a DataFrame is already loaded, show it -- through the preview
        # path, not straight into the model. The combo already reads
        # "Top 10", so pushing the raw frame rendered all 149,116 rows
        # behind a "Top 10" label: the very mismatch the compact-index
        # work is about. _on_preview_mode slices the frame AND re-fits the
        # gutter, so the two can never disagree.
        if self.bridge.df is not None:
            self._on_preview_mode()
            self._update_preview_label()
            self._auto_fit_index_width()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("📂 Upload Dataset")
        title.setProperty("class", "pageTitle")
        header.addWidget(title)
        header.addStretch()

        self.btn_import = QPushButton("📁 Import CSV / Excel")
        self.btn_import.setProperty("kind", "primary")
        header.addWidget(self.btn_import)
        root.addLayout(header)

        # -- File info --
        self.lbl_file = QLabel("No file loaded")
        self.lbl_file.setProperty("class", "muted")
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

        controls.addWidget(self.search_box, stretch=1)

        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["Top 10", "Bottom 10", "Random 10", "All rows"])

        controls.addWidget(self.preview_mode)

        self.btn_go_analysis = QPushButton("📊 Go to Analysis →")
        self.btn_go_analysis.setProperty("kind", "success")
        self.btn_go_analysis.setEnabled(False)
        controls.addWidget(self.btn_go_analysis)

        self.btn_save_csv = QPushButton("💾 Save CSV")
        self.btn_save_csv.setProperty("kind", "primary")
        self.btn_save_csv.setEnabled(False)
        self.btn_save_csv.clicked.connect(self._on_save_csv)
        controls.addWidget(self.btn_save_csv)

        root.addLayout(controls)

        # -- Stats label --
        self.lbl_stats = QLabel("")
        self.lbl_stats.setProperty("class", "faint")
        self.lbl_stats.setStyleSheet("font-size: 12px;")
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
        # Compact index gutter: a narrow, digit-driven fixed width. The
        # 149,116-row screenshot overflows a roomy gutter, but a fully
        # hidden header would also remove the only positional row cue.
        # Fixed + minimumSectionSize avoids section-sizeHint churn on the
        # large-table resize path, and _auto_fit_index_width() widens it
        # only as far as the loaded row count's digit width requires.
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table.verticalHeader().setMinimumSectionSize(22)
        self.table.verticalHeader().setFixedWidth(36)
        self._auto_fit_index_width()

        # -- Transform Tools Tabs (hidden until data loaded) --
        self._transform_tabs = QTabWidget()


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
        self.bridge.theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode: str) -> None:
        """Re-resolve model header colors + repolish the view after a
        dark/light toggle (the global QSS is palette-driven and updates
        itself; only model-provided colors need an explicit reset)."""
        self._model.on_theme_changed()

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

        # Update model -- through the preview path so the grid always shows
        # what the combo says (and the gutter is sized to those rows), not
        # the raw frame.
        self._on_preview_mode()
        self._update_preview_label()
        self._auto_fit_index_width()
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

    def _on_preview_mode(self, index: int = -1) -> None:
        """Handle preview mode change (Top/Bottom/Random/All).

        *index* is the combo's currentIndexChanged argument; it is accepted
        for the signal connection but deliberately unused (the current text
        is the source of truth). It defaults so the same method can be
        called directly to (re)apply the preview after a load/import.
        """
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
        finally:
            # The displayed row count just changed (Top/Bottom/Random 10 vs
            # All rows), so the index gutter has to be re-measured -- this
            # is what stops a 6-digit-wide gutter on a 10-row preview.
            self._auto_fit_index_width()

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
            self._auto_fit_index_width()
            return

        try:
            mask = df.astype(str).apply(
                lambda col: col.str.contains(text, case=False, na=False)
            ).any(axis=1)
            self._model.set_dataframe(df[mask])
        except Exception as exc:
            log.warning("Search failed: %s", exc)
        finally:
            # A search can shrink the result to a handful of rows (or empty
            # it), so the index gutter has to follow the new row count.
            self._auto_fit_index_width()

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _value_sig(series: pd.Series) -> tuple:
        """Fingerprint a column's value profile (dtype + sampled value types).

        pandas dtype strings alone can't detect date/time conversions
        (string 'object' -> date/time objects is object -> object), so the
        sample of actual value types is part of the signature.
        """
        non_null = series.dropna()
        types = tuple(sorted({type(v).__name__ for v in non_null.iloc[:20]}))
        return (str(series.dtype), types)

    def _effective_classification(self, df: pd.DataFrame) -> tuple:
        """Resolve the active column families for the current df.

        Delegates to ``services.column_roles.resolve_roles`` -- the single
        source of truth shared with the Analysis page, so the counts shown
        here and the drop-downs offered there can never disagree. The
        user's confirmed Column Classification wins; columns whose value
        profile changed since (a Dtype Transformer conversion) are
        re-derived; nothing is ever dropped.
        """
        roles = resolve_roles(
            df,
            explicit=self._confirmed_classification(),
            value_sig_map=self.bridge.get("cls_value_sig") or None,
        )
        num, cat, dt = (roles["numeric"], roles["categorical"],
                        roles["datetime"])

        # Persist so Analysis / other pages agree with what the label shows.
        self.bridge.set("num_cols", num)
        self.bridge.set("cat_cols", cat)
        self.bridge.set("dt_cols", dt)
        self.bridge.set("cls_value_sig", value_sig_map(df))
        return num, cat, dt

    def _confirmed_classification(self) -> Optional[dict]:
        """The user's Column Classification in resolver shape, or None."""
        num = self.bridge.get("num_cols")
        cat = self.bridge.get("cat_cols")
        dt = self.bridge.get("dt_cols")
        if not (num or cat or dt):
            return None
        return {"numeric": list(num or []),
                "categorical": list(cat or []),
                "datetime": list(dt or [])}

    def _update_preview_label(self) -> None:
        """Update the stats label with column type counts.

        Counts mirror the app's pipeline truth via
        ``_effective_classification`` -> ``services.column_roles``: the
        user-confirmed Column Classification (bridge keys
        num_cols/cat_cols/dt_cols), self-healed after dtype conversions --
        NOT raw pandas dtypes, which disagree with the pipeline (e.g. a
        column converted to 'date'/'time' keeps pandas dtype ``object``
        but is datetime-family for analysis).
        """
        df = self.bridge.df
        if df is None:
            self.lbl_stats.setText("")
            return

        num_cols, cat_cols, dt_cols = self._effective_classification(df)
        self.lbl_stats.setText(
            f"🔢 {len(num_cols)} numeric  ·  🔤 {len(cat_cols)} text"
            f"  ·  📅 {len(dt_cols)} datetime"
        )

    def _auto_fit_index_width(self) -> None:
        """Size the row-index gutter to the rows actually on screen.

        Delegates to the shared helper so every table preview in the app
        sizes its gutter the same way. The important part is *when* this
        runs: it must follow the DISPLAYED row count, not the loaded
        dataset size, otherwise a 149k-row file keeps a 6-digit-wide
        gutter even in "Top 10" mode (where only "1".."10" are drawn).
        Every path that swaps the model -- import, preview-mode change,
        search, per-column filters, transforms -- calls this.
        """
        auto_fit_index_header(self.table)

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
            inp.setStyleSheet("QLineEdit { font-size: 11px; min-width: 80px; padding: 4px 6px; }")
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
        # Filtered row count changed -> re-measure the index gutter.
        self._auto_fit_index_width()

    # -- Operation feedback -------------------------------------------------

    def _on_operation_applied(self, message: str) -> None:
        """Show a status message when a transform operation succeeds."""
        self.lbl_file.setText(message)
        # Refresh everything that reflects dtype/shape: the main grid
        # (filter-aware), the stats label, and all transform panels. Some
        # panels mutate the df and only emit `operation_applied` (without
        # set_df), so this is the single guaranteed sync point.
        df = self.bridge.df
        self._update_preview_label()
        self._refresh_table_preserving_filter()
        if df is not None:
            self._column_manager_panel.set_dataframe(df)
            self._data_cleaner_panel.set_dataframe(df)
            self._dtype_transformer_panel.set_dataframe(df)
            self.btn_save_csv.setEnabled(True)

    def _refresh_table_preserving_filter(self) -> None:
        """Re-render the preview grid from the current bridge df, keeping
        the active search filter / preview mode so an operation does not
        silently reset the user's view."""
        df = self.bridge.df
        if df is None:
            self._model.set_dataframe(pd.DataFrame())
            return
        text = getattr(self, "_pending_search_text", "") or ""
        if text.strip():
            try:
                mask = df.astype(str).apply(
                    lambda col: col.str.contains(text, case=False, na=False)
                ).any(axis=1)
                df = df[mask]
            except Exception as exc:
                log.warning("Filter re-apply failed: %s", exc)
        self._model.set_dataframe(df)

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
        # Baseline the value-profile fingerprint NOW so later Dtype
        # Transformer conversions (string -> date/time objects) are detected
        # as changes and the stats label re-derives those columns.
        self._update_preview_label()
        # Emit signal to navigate to analysis
        self.classification_ready.emit(classification)

    def _on_classification_ready(self, classification: dict) -> None:
        """Navigate to analysis page when classification is confirmed."""
        self.navigate_requested.emit("analysis")
