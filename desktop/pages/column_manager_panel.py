"""desktop/pages/column_manager_panel.py -- Column management widget.

Replaces the Streamlit ``show_column_manager()`` with a native Qt panel that:
  - Add Column: custom formula, column ops, date diff, date part extraction
  - Remove Column: multi-select with confirmation
  - Rename Column: with duplicate validation

Layout: a 2-column workspace -- compact settings/actions on the left and a
live preview table on the right. All controls, handlers and behaviour are
preserved from the original version; only the visual layout changed.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QLineEdit, QMessageBox,
    QTabWidget, QFormLayout, QSpinBox, QCheckBox, QGroupBox,
    QFileDialog, QInputDialog, QProgressBar, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from services.transform_service import (
    safe_formula_eval, add_derived_column, remove_column, rename_column,
    date_diff_column, extract_date_part, add_column_op, moving_average_column,
)
from desktop.workers import TransformWorker

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Small icon helper -- themed icons (Linux) with a safe text-only fallback so
# the UI never shows a broken/missing-image glyph.
# ---------------------------------------------------------------------------

def _icon(name: str) -> QIcon:
    ic = QIcon.fromTheme(name)
    return ic if not ic.isNull() else QIcon()


# Visual operator symbols shown in the UI versus the real Python operators the
# formula parser understands. The combo label uses the nice glyph (× ÷ −); the
# formula handed to the backend must use the ASCII operator (* / -).
_DISPLAY_OP = {
    "Column × Column": "×", "Column ÷ Column": "÷",
    "Column + Column": "+", "Column − Column": "−",
}
_REAL_OP = {
    "Column × Column": "*", "Column ÷ Column": "/",
    "Column + Column": "+", "Column − Column": "-",
}

# Custom-formula unicode operators a user might type/paste -> ASCII equivalent.
_FORMULA_OP_MAP = {"×": "*", "÷": "/", "−": "-", "∕": "/"}


def _normalize_formula(formula: str) -> str:
    """Translate typographic operators a user may have typed (× ÷ − …) into the
    ASCII operators the formula parser understands, so the UI and parser agree."""
    for pretty, real in _FORMULA_OP_MAP.items():
        formula = formula.replace(pretty, real)
    return formula




class ColumnManagerPanel(QWidget):
    """Column management panel with Add / Remove / Rename tabs.

    Emits ``operation_applied`` when a column operation succeeds, passing
    a description string for status display.
    """

    operation_applied = Signal(str)  # emits success message

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._df: Optional[pd.DataFrame] = None
        self._worker = None

        self._build_ui()
        self._connect_signals()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # -- 2-column workspace: settings (left) + preview (right) ------------
        body = QHBoxLayout()
        body.setSpacing(12)

        # Left: settings panel ------------------------------------------------
        left = QFrame()
        left.setObjectName("cm_settings")
        left.setMaximumWidth(540)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        header = QLabel("Column Manager")
        header.setProperty("class", "sectionTitle")
        left_lay.addWidget(header)

        self.tabs = QTabWidget()
        # Tab styling handled by global _tabs_qss

        self.tab_add = QWidget()
        self._build_add_tab()
        self.tabs.addTab(self.tab_add, _icon("list-add"), "Add Column")

        self.tab_remove = QWidget()
        self._build_remove_tab()
        self.tabs.addTab(self.tab_remove, _icon("edit-delete"), "Remove Column")

        self.tab_rename = QWidget()
        self._build_rename_tab()
        self.tabs.addTab(self.tab_rename, _icon("document-edit"), "Rename Column")

        left_lay.addWidget(self.tabs, stretch=1)
        body.addWidget(left)

        # Right: preview panel ------------------------------------------------
        right = QFrame()
        right.setObjectName("cm_preview")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(12, 12, 12, 12)
        right_lay.setSpacing(8)

        prev_header = QLabel("Preview")
        prev_header.setProperty("class", "cardSub")
        right_lay.addWidget(prev_header)

        self.table_preview = QTableWidget()
        self.table_preview.setColumnCount(0)
        self.table_preview.setRowCount(0)
        self.table_preview.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_preview.setAlternatingRowColors(True)
        self.table_preview.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_preview.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table_preview.verticalHeader().setDefaultSectionSize(28)
        # Table styling handled by global _tables_qss
        self.table_preview.verticalHeader().setFixedWidth(36)
        self.table_preview.verticalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table_preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_preview.setMinimumHeight(220)
        right_lay.addWidget(self.table_preview, stretch=1)

        self.lbl_preview_status = QLabel("Click Preview Result to see the computed column here.")
        self.lbl_preview_status.setWordWrap(True)
        self.lbl_preview_status.setProperty("class", "muted")
        right_lay.addWidget(self.lbl_preview_status)

        body.addWidget(right, stretch=1)
        root.addLayout(body, stretch=1)

        # Progress bar --------------------------------------------------------
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

    def _build_add_tab(self) -> None:
        layout = QVBoxLayout(self.tab_add)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)

        self.txt_new_col = QLineEdit()
        self.txt_new_col.setPlaceholderText("e.g. Total_Sales")
        self.txt_new_col.setFixedWidth(300)
        self.txt_new_col
        form.addRow("New column name", self.txt_new_col)

        self.cmb_calc_type = QComboBox()
        self.cmb_calc_type.addItems([
            "Custom formula (use col names)",
            "Column × Column", "Column ÷ Column",
            "Column + Column", "Column − Column",
            "Date Difference", "Extract Date/Time Part", "Moving Average",
        ])
        self.cmb_calc_type.setFixedWidth(300)
        self.cmb_calc_type
        form.addRow("Calculation type", self.cmb_calc_type)

        layout.addLayout(form)

        # Dynamic options area (rebuilt on calc-type change) -----------------
        self.options_area = QWidget()
        self.options_layout = QVBoxLayout(self.options_area)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(8)
        layout.addWidget(self.options_area)

        # Action buttons -----------------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_preview = QPushButton(_icon("system-search"), "Preview Result")
        self.btn_preview.setProperty("kind", "primary")
        self.btn_apply_add = QPushButton(_icon("list-add"), "Add Column")
        self.btn_apply_add.setProperty("kind", "success")
        btn_row.addWidget(self.btn_preview)
        btn_row.addWidget(self.btn_apply_add)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        layout.addStretch(1)

        self.cmb_calc_type.currentTextChanged.connect(self._on_calc_type_changed)
        self._on_calc_type_changed(self.cmb_calc_type.currentText())

    def _build_remove_tab(self) -> None:
        layout = QVBoxLayout(self.tab_remove)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Select columns to remove:"))
        self.list_remove = QListWidget()
        self.list_remove.setSelectionMode(QListWidget.MultiSelection)
        # List styling handled by global _lists_qss
        layout.addWidget(self.list_remove, stretch=1)

        self.chk_confirm_remove = QCheckBox("I confirm removal of selected columns")
        # Checkbox styling handled by global QSS
        layout.addWidget(self.chk_confirm_remove)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_apply_remove = QPushButton(_icon("edit-delete"), "Remove Selected")
        self.btn_apply_remove.setEnabled(False)
        self.btn_apply_remove.setProperty("kind", "danger")
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_apply_remove)
        layout.addLayout(btn_row)

    def _build_rename_tab(self) -> None:
        layout = QVBoxLayout(self.tab_rename)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)

        self.cmb_rename_col = QComboBox()
        self.cmb_rename_col.setFixedWidth(300)
        self.cmb_rename_col
        form.addRow("Select column", self.cmb_rename_col)

        self.txt_rename = QLineEdit()
        self.txt_rename.setPlaceholderText("Enter new column name")
        self.txt_rename.setFixedWidth(300)
        self.txt_rename
        form.addRow("New name", self.txt_rename)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_apply_rename = QPushButton(_icon("document-edit"), "Rename Column")
        self.btn_apply_rename.setEnabled(False)
        self.btn_apply_rename.setProperty("kind", "success")
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_apply_rename)
        layout.addLayout(btn_row)

        layout.addStretch(1)

        self.txt_rename.textChanged.connect(
            lambda text: self.btn_apply_rename.setEnabled(bool(text.strip()))
        )

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.btn_preview.clicked.connect(self._on_preview_add)
        self.btn_apply_add.clicked.connect(self._on_apply_add)
        self.btn_apply_remove.clicked.connect(self._on_apply_remove)
        self.btn_apply_rename.clicked.connect(self._on_apply_rename)

    # -- Public API ---------------------------------------------------------

    def set_dataframe(self, df: pd.DataFrame) -> None:
        """Load a DataFrame and populate column lists."""
        self._df = df
        if df is None or df.empty:
            self.list_remove.clear()
            self.cmb_rename_col.clear()
            return

        self.list_remove.clear()
        for col in df.columns:
            item = QListWidgetItem(col)
            self.list_remove.addItem(item)

        self.cmb_rename_col.clear()
        self.cmb_rename_col.addItems(df.columns.tolist())

        self._on_calc_type_changed(self.cmb_calc_type.currentText())

    # -- Slots: Add Column --------------------------------------------------

    def _on_insert_formula_column(self) -> None:
        """Insert the picked column name, backtick-wrapped, at the
        current cursor position in the formula field."""
        col = self.cmb_formula_col.currentText()
        if not col:
            return
        self.txt_formula.insert(f"`{col}`")
        self.txt_formula.setFocus()

    def _clear_layout(self, layout) -> None:
        """Recursively delete every widget and sub-layout in ``layout``."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())
                item.layout().deleteLater()

    def _on_calc_type_changed(self, text: str) -> None:
        """Rebuild the options area based on calculation type."""
        self._clear_layout(self.options_layout)

        if self._df is None or self._df.empty:
            return

        num_cols = self._df.select_dtypes(include="number").columns.tolist()
        all_cols = self._df.columns.tolist()

        pass  # combo styling handled by global QSS

        if text == "Custom formula (use col names)":
            form = QFormLayout()
            form.setSpacing(8)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.txt_formula = QLineEdit()
            self.txt_formula.setPlaceholderText("`Sales` / `Units`")
            self.txt_formula.setFixedWidth(300)
            self.txt_formula
            form.addRow("Formula (`col` / `col`)", self.txt_formula)

            self.cmb_formula_col = QComboBox()
            self.cmb_formula_col.addItems(all_cols)
            self.cmb_formula_col.setFixedWidth(200)
            btn_insert = QPushButton(_icon("insert-link"), "Insert")
            btn_insert.setProperty("kind", "primary")
            btn_insert.clicked.connect(self._on_insert_formula_column)
            picker_row = QHBoxLayout()
            picker_row.setSpacing(6)
            picker_row.addWidget(self.cmb_formula_col)
            picker_row.addWidget(btn_insert)
            picker_row.addStretch(1)
            form.addRow("Insert column", picker_row)

            self.options_layout.addLayout(form)

        elif text in ("Column × Column", "Column ÷ Column",
                      "Column + Column", "Column − Column"):
            op = _DISPLAY_OP[text]
            form = QFormLayout()
            form.setSpacing(8)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.cmb_col_a = QComboBox()
            self.cmb_col_a.addItems(num_cols)
            self.cmb_col_a.setFixedWidth(200)
            self.cmb_col_a
            self.cmb_col_b = QComboBox()
            self.cmb_col_b.addItems(num_cols)
            self.cmb_col_b.setFixedWidth(200)
            self.cmb_col_b
            op_lbl = QLabel(op)
            op_lbl.setProperty("class", "cardSub")
            cols_row = QHBoxLayout()
            cols_row.setSpacing(8)
            cols_row.addWidget(self.cmb_col_a)
            cols_row.addWidget(op_lbl)
            cols_row.addWidget(self.cmb_col_b)
            cols_row.addStretch(1)
            form.addRow("Columns", cols_row)

            self.options_layout.addLayout(form)
            self._add_op = _REAL_OP[text]

        elif text == "Date Difference":
            form = QFormLayout()
            form.setSpacing(8)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.cmb_date_a = QComboBox()
            self.cmb_date_a.addItems(all_cols)
            self.cmb_date_a.setFixedWidth(220)
            self.cmb_date_a
            form.addRow("Start date column", self.cmb_date_a)

            row2 = QHBoxLayout()
            row2.setSpacing(8)
            self.rb_today = QPushButton("Today's date")
            self.rb_today.setCheckable(True)
            self.rb_today.setChecked(True)
            self.rb_today.setProperty("kind", "primary")
            self.rb_another_col = QPushButton("Another column")
            self.rb_another_col.setCheckable(True)
            self.rb_another_col.setProperty("kind", "ghost")
            self.rb_today.clicked.connect(lambda: self._set_date_mode("today"))
            self.rb_another_col.clicked.connect(lambda: self._set_date_mode("column"))
            row2.addWidget(self.rb_today)
            row2.addWidget(self.rb_another_col)
            row2.addStretch(1)
            form.addRow("End date", row2)

            self.cmb_date_b = QComboBox()
            self.cmb_date_b.addItems([c for c in all_cols if c != self.cmb_date_a.currentText()])
            self.cmb_date_b.setFixedWidth(220)
            self.cmb_date_b
            form.addRow("End date column", self.cmb_date_b)
            self.cmb_date_a.currentTextChanged.connect(
                lambda t: self.cmb_date_b.clear() or self.cmb_date_b.addItems([c for c in all_cols if c != t])
            )
            self._set_date_mode("today")

            self.cmb_diff_unit = QComboBox()
            self.cmb_diff_unit.addItems([
                "Days", "Months", "Years", "Age (Years from DOB vs Today)",
                "Previous Year (subtract 1 year)", "Previous Month (subtract 1 month)",
            ])
            self.cmb_diff_unit.setFixedWidth(260)
            self.cmb_diff_unit
            form.addRow("Difference unit", self.cmb_diff_unit)

            self.options_layout.addLayout(form)

        elif text == "Extract Date/Time Part":
            form = QFormLayout()
            form.setSpacing(8)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.cmb_date_src = QComboBox()
            self.cmb_date_src.addItems(all_cols)
            self.cmb_date_src.setFixedWidth(200)
            self.cmb_date_src
            self.cmb_date_part = QComboBox()
            self.cmb_date_part.addItems([
                "Date (YYYY-MM-DD)", "Year", "Quarter", "Month (Number)", "Month Name",
                "Week Number", "Day", "Weekday Name", "Hour (12h AM/PM)", "Hour (24h)",
            ])
            self.cmb_date_part.setFixedWidth(220)
            self.cmb_date_part
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(self.cmb_date_src)
            row.addWidget(self.cmb_date_part)
            row.addStretch(1)
            form.addRow("Source / part", row)

            self.options_layout.addLayout(form)

        elif text == "Moving Average":
            form = QFormLayout()
            form.setSpacing(8)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.cmb_ma_col = QComboBox()
            self.cmb_ma_col.addItems(num_cols)
            self.cmb_ma_col.setFixedWidth(200)
            self.cmb_ma_col
            self.spin_ma_window = QSpinBox()
            self.spin_ma_window.setRange(2, 999)
            self.spin_ma_window.setValue(7)
            self.spin_ma_window.setFixedWidth(80)
            
            form.addRow("Column", self.cmb_ma_col)
            form.addRow("Window (rows)", self.spin_ma_window)

            self.options_layout.addLayout(form)

    def _set_date_mode(self, mode: str) -> None:
        """Toggle between today and another column for date diff."""
        if mode == "today":
            self.rb_today.setChecked(True)
            self.rb_today.setProperty("kind", "primary")
            self.rb_another_col.setChecked(False)
            self.rb_another_col.setProperty("kind", "ghost")
        else:
            self.rb_another_col.setChecked(True)
            self.rb_another_col.setProperty("kind", "ghost")
            self.rb_today.setChecked(False)
            self.rb_today.setProperty("kind", "primary")
        if hasattr(self, "cmb_date_b"):
            self.cmb_date_b.setEnabled(mode != "today")

    def _populate_preview_table(self, df: pd.DataFrame, max_rows: int = 8) -> None:
        """Fill the right-hand preview table with ``df``."""
        cols = list(df.columns)
        self.table_preview.clear()
        self.table_preview.setColumnCount(len(cols))
        self.table_preview.setHorizontalHeaderLabels(cols)
        n = min(len(df), max_rows)
        self.table_preview.setRowCount(n)
        for r, (_, row) in enumerate(df.head(n).iterrows()):
            for c, col in enumerate(cols):
                val = row[col]
                text = "" if pd.isna(val) else str(val)
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if len(cols) > 1 and c == len(cols) - 1:
                    from PySide6.QtGui import QColor
                    item.setBackground(QColor(16, 185, 129, 50))
                self.table_preview.setItem(r, c, item)

    def _validate_formula(self, formula: str) -> Optional[str]:
        """Return a user-friendly error message if *formula* is invalid, or
        ``None`` if it parses cleanly. Catches empty input, unknown columns,
        non-numeric columns, syntax errors and unsafe expressions -- so the
        caller can show a clean message instead of a raw parser traceback."""
        import ast as _ast
        import operator as _operator

        _SAFE_BIN = {
            _ast.Add: _operator.add, _ast.Sub: _operator.sub,
            _ast.Mult: _operator.mul, _ast.Div: _operator.truediv,
            _ast.FloorDiv: _operator.floordiv, _ast.Mod: _operator.mod,
            _ast.Pow: _operator.pow,
        }

        if not formula:
            return "Formula is empty."

        # Resolve backtick-quoted columns to aliases the parser can handle.
        numeric_cols = set(self._df.select_dtypes(include="number").columns)
        column_aliases: dict[str, str] = {}

        def _replace(match: re.Match) -> str:
            col = match.group(1)
            if col not in self._df.columns:
                raise ValueError(f"Unknown column: '{col}'")
            alias = f"__col_{len(column_aliases)}"
            column_aliases[alias] = col
            return alias

        try:
            expr = re.sub(r"`([^`]+)`", _replace, formula)
        except ValueError as exc:
            return str(exc)

        try:
            tree = _ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            return f"Invalid formula syntax: {exc.msg}"

        def _check(node):
            if isinstance(node, _ast.Expression):
                return _check(node.body)
            if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)):
                return None
            if isinstance(node, _ast.Name):
                col = column_aliases.get(node.id, node.id)
                if col not in numeric_cols:
                    return f"Unknown or non-numeric column: '{col}'"
                return None
            if isinstance(node, _ast.BinOp) and type(node.op) in _SAFE_BIN:
                return _check(node.left) or _check(node.right)
            if isinstance(node, _ast.UnaryOp) and type(node.op) in (_ast.UAdd, _ast.USub):
                return _check(node.operand)
            return "Formula contains an unsupported operator or expression."

        return _check(tree)

    def _on_preview_add(self) -> None:
        """Preview the result of the add column operation."""
        if self._df is None or self._df.empty:
            QMessageBox.warning(self, "No Data", "Please upload a dataset first.")
            return

        new_col_name = self.txt_new_col.text().strip()
        if not new_col_name:
            QMessageBox.warning(self, "Missing Name", "Please enter a new column name.")
            return

        calc_type = self.cmb_calc_type.currentText()
        try:
            if calc_type == "Custom formula (use col names)":
                raw = self.txt_formula.text().strip()
                if not raw:
                    QMessageBox.warning(self, "Missing Formula", "Please enter a formula.")
                    return
                formula = _normalize_formula(raw)
                error = self._validate_formula(formula)
                if error:
                    self._populate_preview_table(pd.DataFrame())
                    self.lbl_preview_status.setText(f"❌ {error}")
                    QMessageBox.warning(self, "Invalid Formula", error)
                    return
                result = safe_formula_eval(self._df, formula)

            elif calc_type in ("Column × Column", "Column ÷ Column",
                              "Column + Column", "Column − Column"):
                col_a = self.cmb_col_a.currentText()
                col_b = self.cmb_col_b.currentText()
                result = add_column_op(self._df, col_a, col_b, self._add_op)

            elif calc_type == "Date Difference":
                col_a = self.cmb_date_a.currentText()
                use_today = not self.rb_another_col.isChecked()
                col_b = None if use_today else self.cmb_date_b.currentText()
                unit = self.cmb_diff_unit.currentText()
                result = date_diff_column(self._df, col_a, col_b, unit)

            elif calc_type == "Extract Date/Time Part":
                col = self.cmb_date_src.currentText()
                part = self.cmb_date_part.currentText()
                result = extract_date_part(self._df, col, part)

            elif calc_type == "Moving Average":
                col = self.cmb_ma_col.currentText()
                window = self.spin_ma_window.value()
                result = moving_average_column(self._df, col, window)

            else:
                QMessageBox.warning(self, "Not Implemented",
                                    f"Calculation type '{calc_type}' not yet implemented.")
                return

            preview_df = add_derived_column(self._df, new_col_name, result)
            self._populate_preview_table(preview_df, max_rows=8)
            self.lbl_preview_status.setText(
                f"Showing first {min(8, len(preview_df))} rows of '{new_col_name}' "
                f"(highlighted in green on the right)."
            )

        except Exception as exc:
            msg = str(exc)
            self._populate_preview_table(pd.DataFrame())
            self.lbl_preview_status.setText(f"❌ Preview failed: {msg}")
            QMessageBox.critical(self, "Preview Error", msg)
            log.error("Preview failed: %s", exc)

    def _on_apply_add(self) -> None:
        """Apply the add column operation in a background thread."""
        if self._df is None or self._df.empty:
            return

        new_col_name = self.txt_new_col.text().strip()
        if not new_col_name:
            return

        if new_col_name in self._df.columns:
            QMessageBox.warning(self, "Duplicate Column",
                                f"A column named '{new_col_name}' already exists.")
            return

        calc_type = self.cmb_calc_type.currentText()
        self._set_processing(True)
        self._worker = TransformWorker(self._compute_add_column, calc_type, new_col_name)
        self._worker.finished_ok.connect(self._on_add_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.start()

    def _compute_add_column(self, calc_type: str, new_col_name: str):
        """Compute the new column (runs in worker thread)."""
        if calc_type == "Custom formula (use col names)":
            formula = _normalize_formula(self.txt_formula.text().strip())
            error = self._validate_formula(formula)
            if error:
                raise ValueError(error)
            result = safe_formula_eval(self._df, formula)
        elif calc_type in ("Column × Column", "Column ÷ Column",
                          "Column + Column", "Column − Column"):
            col_a = self.cmb_col_a.currentText()
            col_b = self.cmb_col_b.currentText()
            result = add_column_op(self._df, col_a, col_b, self._add_op)
        elif calc_type == "Date Difference":
            col_a = self.cmb_date_a.currentText()
            use_today = not self.rb_another_col.isChecked()
            col_b = None if use_today else self.cmb_date_b.currentText()
            unit = self.cmb_diff_unit.currentText()
            result = date_diff_column(self._df, col_a, col_b, unit)
        elif calc_type == "Extract Date/Time Part":
            col = self.cmb_date_src.currentText()
            part = self.cmb_date_part.currentText()
            result = extract_date_part(self._df, col, part)
        elif calc_type == "Moving Average":
            col = self.cmb_ma_col.currentText()
            window = self.spin_ma_window.value()
            result = moving_average_column(self._df, col, window)
        else:
            raise ValueError(f"Unsupported calculation type: {calc_type}")

        return add_derived_column(self._df, new_col_name, result)

    def _on_add_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful add column operation."""
        self.bridge.set_df(new_df)
        self.operation_applied.emit(f"Added column '{self.txt_new_col.text().strip()}'")
        self._populate_preview_table(pd.DataFrame())
        self.lbl_preview_status.setText(
            "Column added. Click Preview Result again to see a new preview."
        )
        self._set_processing(False)

    # -- Slots: Remove Column ------------------------------------------------

    def _on_apply_remove(self) -> None:
        """Remove selected columns in a background thread."""
        if self._df is None or self._df.empty:
            return

        selected = [item.text() for item in self.list_remove.selectedItems()]
        if not selected:
            return

        reply = QMessageBox.question(
            self, "Confirm Removal",
            f"Remove {len(selected)} column(s)?\n{', '.join(selected)}",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._set_processing(True)
        self._worker = TransformWorker(self._compute_remove_column, selected)
        self._worker.finished_ok.connect(self._on_remove_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.start()

    def _compute_remove_column(self, selected: list[str]) -> pd.DataFrame:
        """Remove selected columns (runs in worker thread)."""
        new_df = self._df.copy()
        for col in selected:
            new_df = remove_column(new_df, col)
        return new_df

    def _on_remove_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful remove column operation."""
        self.bridge.set_df(new_df)
        self.operation_applied.emit("Removed columns")
        self.chk_confirm_remove.setChecked(False)
        self._set_processing(False)

    # -- Slots: Rename Column ------------------------------------------------

    def _on_apply_rename(self) -> None:
        """Rename selected column in a background thread."""
        if self._df is None or self._df.empty:
            return

        old_name = self.cmb_rename_col.currentText()
        new_name = self.txt_rename.text().strip()

        if not new_name:
            return

        if new_name in self._df.columns:
            QMessageBox.warning(self, "Duplicate Column",
                                f"A column named '{new_name}' already exists.")
            return

        self._set_processing(True)
        self._worker = TransformWorker(rename_column, self._df, old_name, new_name)
        self._worker.finished_ok.connect(self._on_rename_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.start()

    def _on_rename_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful rename column operation."""
        self.bridge.set_df(new_df)
        self.operation_applied.emit("Renamed column")
        self.txt_rename.clear()
        self.cmb_rename_col.clear()
        self.cmb_rename_col.addItems(new_df.columns.tolist())
        self._set_processing(False)

    # -- Worker helpers ------------------------------------------------------

    def _set_processing(self, processing: bool) -> None:
        """Enable/disable UI during background processing."""
        self.progress.setVisible(processing)
        self.btn_preview.setEnabled(not processing)
        self.btn_apply_add.setEnabled(not processing)
        self.btn_apply_remove.setEnabled(not processing)
        self.btn_apply_rename.setEnabled(not processing)
        self.tabs.setEnabled(not processing)

    def _on_worker_failed(self, error: str) -> None:
        """Handle worker failure."""
        self._set_processing(False)
        QMessageBox.critical(self, "Operation Failed", error)
        log.error("Worker failed: %s", error)
