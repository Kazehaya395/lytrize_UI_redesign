"""desktop/pages/excel_loader_panel.py -- Excel multi-sheet browser and table builder.

Replaces the Streamlit ``show_excel_loader()`` with a native Qt panel that:
  - Shows all sheets in an Excel file with shape/dtype summaries
  - Single sheet mode: select one sheet to analyse
  - Unified Table Builder mode: join multiple sheets via fact/dimension keys
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QRadioButton, QButtonGroup, QCheckBox,
    QLineEdit, QFormLayout, QProgressBar, QGroupBox,
)

log = logging.getLogger(__name__)


class ExcelLoaderPanel(QWidget):
    """Excel multi-sheet loader panel.

    Emits ``sheet_selected`` when a single sheet is chosen.
    Emits ``merged_table_ready`` when a unified table is built.
    """

    sheet_selected = Signal(pd.DataFrame, str)  # emits df, sheet_name
    merged_table_ready = Signal(pd.DataFrame, str)  # emits merged_df, description

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._file_path: Optional[str] = None
        self._sheets: dict[str, pd.DataFrame] = {}
        self._sheet_names: list[str] = []

        self._build_ui()
        self._connect_signals()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # -- Header --
        header = QLabel("📑 Excel Workbook Browser")
        header.setProperty("class", "sectionTitle")
        root.addWidget(header)

        # -- Sheet list --
        # Was stretch=1 with no height cap, so it silently expanded to
        # fill all available vertical space in the tab -- appropriate
        # for a widget with unbounded content, but this list only ever
        # shows a handful of sheet names (most real workbooks have 2-6
        # sheets), so it just became a large empty box below the actual
        # sheet entries. Capped height, no stretch, so the join-config
        # controls below (the actual interactive workflow) get the
        # extra room instead.
        self.list_sheets = QListWidget()
        # List styling handled by global _lists_qss
        self.list_sheets.setMaximumHeight(180)
        root.addWidget(self.list_sheets)

        # -- Mode selector --
        mode_group = QWidget()
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setContentsMargins(0, 0, 0, 0)

        self.rb_single = QRadioButton("📋 Use a single sheet for analysis")
        self.rb_single.setChecked(True)
        pass  # Radio styling handled by global QSS
        self.rb_unified = QRadioButton("🔗 Model multiple sheets (Table Join)")
        pass  # Radio styling handled by global QSS

        mode_layout.addWidget(self.rb_single)
        mode_layout.addWidget(self.rb_unified)
        root.addWidget(mode_group)

        # -- Single sheet controls --
        self.single_controls = QWidget()
        single_layout = QVBoxLayout(self.single_controls)
        single_layout.setContentsMargins(0, 0, 0, 0)

        single_layout.addWidget(QLabel("Select a sheet to analyse:"))
        self.cmb_single_sheet = QComboBox()
        single_layout.addWidget(self.cmb_single_sheet)

        self.lbl_sheet_info = QLabel("")
        self.lbl_sheet_info.setWordWrap(True)
        self.lbl_sheet_info.setProperty("class", "faint")
        self.lbl_sheet_info.setStyleSheet("font-size: 11px;")
        single_layout.addWidget(self.lbl_sheet_info)

        self.btn_use_sheet = QPushButton("✅ Use This Sheet →")
        self.btn_use_sheet.setProperty("kind", "success")
        single_layout.addWidget(self.btn_use_sheet)
        root.addWidget(self.single_controls)

        # -- Unified table builder controls --
        self.unified_controls = QWidget()
        self.unified_controls.setVisible(False)
        unified_layout = QVBoxLayout(self.unified_controls)
        unified_layout.setContentsMargins(0, 0, 0, 0)

        # Step 1: Fact table
        unified_layout.addWidget(QLabel("Step 1 — Choose your Fact table:"))
        self.cmb_fact = QComboBox()
        unified_layout.addWidget(self.cmb_fact)

        # Step 2: Dimension tables
        unified_layout.addWidget(QLabel("Step 2 — Choose Dimension tables to join:"))
        self.list_dims = QListWidget()
        self.list_dims.setSelectionMode(QListWidget.MultiSelection)
        self.list_dims.setMaximumHeight(100)
        unified_layout.addWidget(self.list_dims)

        # Step 3: Join configuration
        self.step3_group = QWidget()
        step3_layout = QVBoxLayout(self.step3_group)
        step3_layout.addWidget(QLabel("Step 3 — Map join keys:"))
        self.join_configs_layout = QVBoxLayout()
        step3_layout.addLayout(self.join_configs_layout)
        unified_layout.addWidget(self.step3_group)

        # Build button
        self.btn_build_merged = QPushButton("🔗 Build Merged Table →")
        self.btn_build_merged.setProperty("kind", "primary")
        unified_layout.addWidget(self.btn_build_merged)
        root.addWidget(self.unified_controls)

        # -- Signal connections --
        self.rb_single.toggled.connect(self._on_mode_changed)
        self.rb_unified.toggled.connect(self._on_mode_changed)
        self.list_sheets.itemSelectionChanged.connect(self._on_sheet_selected)
        self.cmb_single_sheet.currentTextChanged.connect(self._on_single_sheet_changed)
        self.btn_use_sheet.clicked.connect(self._on_use_sheet)
        self.cmb_fact.currentTextChanged.connect(self._on_fact_changed)
        self.list_dims.itemSelectionChanged.connect(self._on_dims_changed)
        self.btn_build_merged.clicked.connect(self._on_build_merged)

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        pass

    # -- Public API ---------------------------------------------------------

    def load_file(self, file_path: str) -> None:
        """Load an Excel file and populate the sheet list."""
        self._file_path = file_path
        self._sheets = {}
        self._sheet_names = []

        try:
            # Read all sheets
            excel = pd.ExcelFile(file_path)
            self._sheet_names = excel.sheet_names
            self._sheets = {name: pd.read_excel(excel, sheet_name=name) for name in self._sheet_names}

            # Populate sheet list
            self.list_sheets.clear()
            for name in self._sheet_names:
                df = self._sheets[name]
                item = QListWidgetItem(
                    f"📄 {name}\n   {df.shape[0]:,} rows × {df.shape[1]} cols"
                )
                item.setData(Qt.UserRole, name)
                self.list_sheets.addItem(item)

            # Populate single sheet dropdown
            self.cmb_single_sheet.clear()
            self.cmb_single_sheet.addItems(self._sheet_names)

            # Populate fact/dim dropdowns
            self.cmb_fact.clear()
            self.cmb_fact.addItems(self._sheet_names)
            self.list_dims.clear()
            for name in self._sheet_names:
                item = QListWidgetItem(name)
                self.list_dims.addItem(item)

        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to read Excel file:\n{exc}")
            log.error("Failed to load Excel file: %s", exc)

    # -- Slots --------------------------------------------------------------

    def _on_mode_changed(self, checked: bool) -> None:
        """Toggle between single sheet and unified table modes."""
        if self.rb_single.isChecked():
            self.single_controls.setVisible(True)
            self.unified_controls.setVisible(False)
        else:
            self.single_controls.setVisible(False)
            self.unified_controls.setVisible(True)
            self._on_fact_changed(self.cmb_fact.currentText())
            self._on_dims_changed()

    def _on_sheet_selected(self) -> None:
        """Handle sheet selection in the list."""
        item = self.list_sheets.currentItem()
        if not item:
            return
        sheet_name = item.data(Qt.UserRole)
        self.cmb_single_sheet.setCurrentText(sheet_name)
        self._on_single_sheet_changed(sheet_name)

    def _on_single_sheet_changed(self, text: str) -> None:
        """Update info label when single sheet selection changes."""
        if not text or text not in self._sheets:
            self.lbl_sheet_info.setText("")
            return
        df = self._sheets[text]
        n_num = len(df.select_dtypes(include="number").columns)
        n_cat = len(df.select_dtypes(include="object").columns)
        n_dt = len(df.select_dtypes(include="datetime").columns)
        self.lbl_sheet_info.setText(
            f"{df.shape[0]:,} rows × {df.shape[1]} columns\n"
            f"🔢 {n_num} numeric  ·  🔤 {n_cat} text  ·  📅 {n_dt} datetime"
        )

    def _on_use_sheet(self) -> None:
        """Emit the selected single sheet."""
        sheet_name = self.cmb_single_sheet.currentText()
        if not sheet_name or sheet_name not in self._sheets:
            QMessageBox.warning(self, "No Sheet Selected", "Please select a sheet first.")
            return
        df = self._sheets[sheet_name]
        self.sheet_selected.emit(df.copy(), sheet_name)

    def _on_fact_changed(self, text: str) -> None:
        """Rebuild join configs when the fact table changes."""
        self._on_dims_changed()

    def _on_dims_changed(self) -> None:
        """Rebuild join configuration when dimension selection changes."""
        # Clear existing join configs
        while self.join_configs_layout.count():
            item = self.join_configs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        fact_name = self.cmb_fact.currentText()
        if not fact_name or fact_name not in self._sheets:
            return

        selected_dims = [item.text() for item in self.list_dims.selectedItems()]
        if not selected_dims:
            return

        fact_df = self._sheets[fact_name]

        for dim_name in selected_dims:
            if dim_name not in self._sheets:
                continue
            dim_df = self._sheets[dim_name]

            # Find common columns
            common = sorted(set(fact_df.columns) & set(dim_df.columns))

            group = QGroupBox(f"Join: {fact_name} ←→ {dim_name}")
            # Store which dimension this group belongs to so the build step
            # doesn't have to guess from list position -- the configs are
            # rebuilt on both dim AND fact changes, so position is unreliable.
            group.setProperty("dim_name", dim_name)
            layout = QFormLayout(group)

            # Fact key
            cmb_fact_key = QComboBox()
            cmb_fact_key.addItems(fact_df.columns.tolist())
            layout.addRow("Fact key:", cmb_fact_key)

            # Dim key
            cmb_dim_key = QComboBox()
            cmb_dim_key.addItems(dim_df.columns.tolist())
            if common:
                cmb_dim_key.setCurrentText(common[0])
            layout.addRow("Dim key:", cmb_dim_key)

            # Join type
            cmb_join = QComboBox()
            cmb_join.addItems(["left", "inner"])
            layout.addRow("Join type:", cmb_join)

            # Column selection. NOTE: setSelected() must come AFTER addItem()
            # -- calling it on an item before it's been added to the widget
            # is unreliable across Qt versions and silently leaves every
            # column unselected, which made the merge drop all dimension
            # columns (Issue 1).
            list_cols = QListWidget()
            list_cols.setSelectionMode(QListWidget.MultiSelection)
            for col in dim_df.columns:
                if col != dim_df.columns[0]:  # Skip the join key (will be dropped)
                    item = QListWidgetItem(col)
                    list_cols.addItem(item)
                    item.setSelected(True)
            layout.addRow("Columns to import:", list_cols)

            self.join_configs_layout.addWidget(group)

    def _on_build_merged(self) -> None:
        """Build the merged table from configured joins."""
        fact_name = self.cmb_fact.currentText()
        if not fact_name or fact_name not in self._sheets:
            QMessageBox.warning(self, "No Fact Table", "Please select a fact table.")
            return

        selected_dims = [item.text() for item in self.list_dims.selectedItems()]
        if not selected_dims:
            QMessageBox.warning(self, "No Dimensions", "Please select at least one dimension table.")
            return

        try:
            merged = self._sheets[fact_name].copy()
            merge_log = []

            # Collect join configs from UI
            for i in range(self.join_configs_layout.count()):
                group = self.join_configs_layout.itemAt(i).widget()
                if not isinstance(group, QGroupBox):
                    continue

                layout = group.layout()
                if not layout:
                    continue

                # Extract widgets from form layout. The form has exactly
                # FOUR rows (Fact key / Dim key / Join type / Columns), so
                # the fields live at indices 0..3 -- NOT 1..4 as earlier
                # code assumed (that off-by-one read the adjacent rows and
                # skipped the column picker entirely).
                fact_key_widget = layout.itemAt(0, QFormLayout.FieldRole).widget()
                dim_key_widget = layout.itemAt(1, QFormLayout.FieldRole).widget()
                join_type_widget = layout.itemAt(2, QFormLayout.FieldRole).widget()
                cols_widget = layout.itemAt(3, QFormLayout.FieldRole).widget()

                dim_name = group.property("dim_name")
                if not dim_name or dim_name not in self._sheets:
                    continue

                dim_df = self._sheets[dim_name]
                fact_key = fact_key_widget.currentText() if isinstance(fact_key_widget, QComboBox) else ""
                dim_key = dim_key_widget.currentText() if isinstance(dim_key_widget, QComboBox) else ""
                join_type = join_type_widget.currentText() if isinstance(join_type_widget, QComboBox) else "left"

                # Get selected columns
                selected_cols = []
                if isinstance(cols_widget, QListWidget):
                    selected_cols = [cols_widget.item(j).text() for j in range(cols_widget.count()) if cols_widget.item(j).isSelected()]

                # Build subset
                cols_to_bring = [dim_key] + selected_cols
                dim_subset = dim_df[cols_to_bring].copy()

                # Handle overlapping column names
                overlap = [c for c in selected_cols if c in merged.columns]
                if overlap:
                    rename_map = {c: f"{c}_{dim_name}" for c in overlap}
                    dim_subset = dim_subset.rename(columns=rename_map)

                # Merge
                before = len(merged)
                merged = merged.merge(
                    dim_subset,
                    left_on=fact_key,
                    right_on=dim_key,
                    how=join_type,
                    suffixes=("", f"_{dim_name}"),
                )
                after = len(merged)

                # Drop the dim key if it's not the fact key
                if dim_key in merged.columns and dim_key != fact_key:
                    merged = merged.drop(columns=[dim_key], errors="ignore")

                merge_log.append(
                    f"✅ Joined **{dim_name}** ({join_type}) — "
                    f"{before:,} → {after:,} rows, +{len(selected_cols)} columns"
                )

            # Show summary
            summary = "\n".join(merge_log)
            QMessageBox.information(
                self, "Merge Complete",
                f"Merged table built:\n{summary}\n\n"
                f"Final shape: {merged.shape[0]:,} rows × {merged.shape[1]} columns"
            )

            self.merged_table_ready.emit(merged, summary)

        except Exception as exc:
            QMessageBox.critical(self, "Merge Failed", str(exc))
            log.error("Failed to build merged table: %s", exc)