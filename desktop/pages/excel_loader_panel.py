"""desktop/pages/excel_loader_panel.py -- Excel multi-sheet browser and table builder.

Replaces the Streamlit ``show_excel_loader()`` with a native Qt panel that:
  - Shows all sheets in an Excel file with shape/dtype summaries
  - Single sheet mode: select one sheet to analyse
  - Unified Table Builder mode: join multiple sheets via fact/dimension keys
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Optional

import pandas as pd
import polars as pl
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QRadioButton, QButtonGroup, QCheckBox,
    QLineEdit, QFormLayout, QProgressBar, QGroupBox,
)

log = logging.getLogger(__name__)


def _to_polars(df) -> pl.DataFrame:
    """Convert a pandas frame to Polars at the Excel boundary.

    Excel is the one format Polars cannot read, so pandas does the parsing
    and this is where the result is handed over to the Polars-native
    application. A frame that is already Polars passes straight through.
    """
    if df is None:
        return pl.DataFrame()
    if isinstance(df, pl.DataFrame):
        return df
    try:
        return pl.from_pandas(df, include_index=False)
    except Exception as exc:
        log.error("could not convert the Excel sheet to Polars: %s", exc)
        return pl.DataFrame()


class _LazySheets(Mapping):
    """``{sheet_name: DataFrame}`` that parses each sheet on first access.

    The panel's ``self._sheets[name]`` call sites are all "I need this sheet's
    rows now" -- selection, preview, merge. Only ONE sheet is normally wanted,
    so the mapping loads on ``__getitem__`` and memoises. ``in`` is answered
    from the known sheet-name list, which is why the merge path can still test
    ``dim_name not in self._sheets`` without triggering a read.
    """

    def __init__(self, names, loader):
        self._names = list(names)
        self._loader = loader
        self._cache: dict = {}

    def __getitem__(self, key):
        if key not in self._names:
            raise KeyError(key)
        if key not in self._cache:
            self._cache[key] = self._loader(key)
        return self._cache[key]

    def __iter__(self):
        return iter(self._names)

    def __len__(self):
        return len(self._names)

    def __contains__(self, key):
        return key in self._names

    def get(self, key, default=None):
        if key not in self._names:
            return default
        try:
            return self[key]
        except Exception as exc:
            log.debug("_LazySheets.get failed for %r: %s", key, exc)
            return default

    def loaded(self) -> list:
        """Names actually parsed so far -- used by tests and diagnostics."""
        return list(self._cache)


def _xlsx_row_count(file_path: Optional[str], sheet_name: str) -> Optional[int]:
    """Row count for *sheet_name* read straight from the xlsx XML.

    An .xlsx is a zip of XML and the worksheet part declares its extent in
    ``<dimension ref="A1:D5000"/>``, so the row count is available without
    parsing a single cell value. That is what makes the sheet list cheap to
    populate.

    Returns None for any workbook this cannot introspect (.xls, a missing or
    malformed dimension, a zip that is not a spreadsheet); the caller then
    omits the count rather than falling back to a full read. Every failure is
    non-fatal by design: a missing row count is not worth refusing to open
    the file over.
    """
    if not file_path or not sheet_name:
        return None
    try:
        import re
        import zipfile
        from xml.etree import ElementTree

        with zipfile.ZipFile(file_path) as zf:
            names = zf.namelist()
            index = _xlsx_sheet_part(zf, sheet_name, ElementTree)
            if index is None or index not in names:
                parts = [n for n in names
                         if n.startswith("xl/worksheets/sheet")
                         and n.endswith(".xml")]
                # Only unambiguous when the workbook has a single data sheet.
                if len(parts) != 1:
                    return None
                index = parts[0]
            with zf.open(index) as fh:
                head = fh.read(8192).decode("utf-8", "ignore")
        match = re.search(r'<dimension[^>]*ref="[A-Z]+\d+:([A-Z]+)(\d+)"', head)
        if not match:
            return None
        return int(match.group(2)) - 1          # dimension ref is 1-based
    except Exception as exc:
        log.debug("_xlsx_row_count failed for %r: %s", sheet_name, exc)
        return None


def _xlsx_sheet_part(zf, sheet_name: str, ElementTree) -> Optional[str]:
    """Zip path of *sheet_name*'s worksheet part, via workbook.xml + rels.

    ``ElementTree`` is passed in rather than imported so the caller keeps a
    single import of it inside its own ``try`` -- a local ``import`` here
    would be a fresh name binding that the two helpers do not share.
    """
    ns_r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    try:
        wb = ElementTree.fromstring(zf.read("xl/workbook.xml"))
        rel_id = None
        for sheet in wb.iter():
            if sheet.tag.endswith("}sheet") and sheet.get("name") == sheet_name:
                rel_id = sheet.get(f"{ns_r}id")
                break
        if not rel_id:
            return None
        rels = ElementTree.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for rel in rels:
            if rel.get("Id") == rel_id:
                target = (rel.get("Target") or "").lstrip("/")
                return target if target.startswith("xl/") else "xl/" + target
    except Exception as exc:
        log.debug("_xlsx_sheet_part failed for %r: %s", sheet_name, exc)
    return None


class ExcelLoaderPanel(QWidget):
    """Excel multi-sheet loader panel.

    Emits ``sheet_selected`` when a single sheet is chosen.
    Emits ``merged_table_ready`` when a unified table is built.

    pandas is used INTERNALLY here, and only here, because it is the only
    reader that can parse ``.xlsx`` (Polars cannot). Everything that leaves
    this panel is converted to Polars at the boundary, so no pandas frame
    ever reaches the session.
    """

    sheet_selected = Signal(object, str)      # emits pl.DataFrame, sheet_name
    merged_table_ready = Signal(object, str)   # emits pl.DataFrame, description

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._file_path: Optional[str] = None
        self._excel = None
        # Swapped for a _LazySheets view in load_file(). A plain empty dict here
        # keeps every `in` / `[]` call safe before a file has been opened.
        self._sheets: Mapping[str, pd.DataFrame] = {}
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
        """Load an Excel file and populate the sheet list.

        Sheets are read LAZILY. This used to be::

            self._sheets = {name: pd.read_excel(excel, sheet_name=name)
                            for name in self._sheet_names}

        which parsed every sheet in the workbook into memory before the user
        had chosen one -- and then kept them all alive for the rest of the
        session. A 20-sheet workbook meant 20 DataFrames resident to look at
        a list of names, and the row counts shown in that list could only be
        produced by parsing every sheet first.

        ``_sheet_cache`` reads a sheet the first time something asks for its
        contents and memoises it, so the list is populated from cheap header
        reads and only the sheets actually opened are ever parsed in full.
        Every existing ``self._sheets[name]`` call site keeps working: ``_sheets``
        is now a mapping view that loads on ``__getitem__``.
        """
        self._file_path = file_path
        self._sheet_names = []

        try:
            excel = pd.ExcelFile(file_path)
            self._sheet_names = list(excel.sheet_names)
            self._excel = excel
            # Installed BEFORE the list is populated: every ``self._sheets[name]``
            # below now goes through the lazy view.
            self._sheets = _LazySheets(
                self._sheet_names,
                lambda name: pd.read_excel(excel, sheet_name=name),
            )

            # Populate sheet list. Shape comes from a header-only read, so
            # this is O(columns) per sheet rather than O(rows x columns).
            self.list_sheets.clear()
            for name in self._sheet_names:
                shape = self._peek_shape(name)
                if shape is None:
                    label = f"\U0001F4C4 {name}"
                else:
                    label = (f"\U0001F4C4 {name}\n"
                             f"   {shape[0]:,} rows × {shape[1]} cols")
                item = QListWidgetItem(label)
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
                self.list_dims.addItem(QListWidgetItem(name))

        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to read Excel file:\n{exc}")
            log.error("Failed to load Excel file: %s", exc)

    def _peek_shape(self, name: str) -> Optional[tuple]:
        """``(rows, cols)`` for *name* without parsing its cells.

        Returns None when the shape cannot be determined cheaply, and the
        caller then omits the count rather than triggering a full read.
        """
        try:
            # ``nrows=0`` stops openpyxl after the header row, so this costs
            # one row read per sheet rather than the whole table.
            head = pd.read_excel(self._excel, sheet_name=name, nrows=0)
            width = len(head.columns)
            rows = _xlsx_row_count(self._file_path, name)
            if rows is None or width == 0:
                return None
            return rows, width
        except Exception as exc:
            log.debug("_peek_shape failed for %r: %s", name, exc)
            return None

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
        # Convert at the boundary: the session is Polars-native.
        self.sheet_selected.emit(_to_polars(df), sheet_name)

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
            cmb_fact_key.addItems(list(fact_df.columns))
            layout.addRow("Fact key:", cmb_fact_key)

            # Dim key
            cmb_dim_key = QComboBox()
            cmb_dim_key.addItems(list(dim_df.columns))
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

            self.merged_table_ready.emit(_to_polars(merged), summary)

        except Exception as exc:
            QMessageBox.critical(self, "Merge Failed", str(exc))
            log.error("Failed to build merged table: %s", exc)