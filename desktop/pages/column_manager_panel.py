"""desktop/pages/column_manager_panel.py -- Column management widget.

Replaces the Streamlit ``show_column_manager()`` with a native Qt panel that:
  - Add Column: custom formula, column ops, date diff, date part extraction
  - Remove Column: multi-select with confirmation
  - Rename Column: with duplicate validation
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QLineEdit, QMessageBox,
    QTabWidget, QFormLayout, QSpinBox, QCheckBox, QGroupBox,
    QFileDialog, QInputDialog, QProgressBar,
)

from services.transform_service import (
    safe_formula_eval, add_derived_column, remove_column, rename_column,
    date_diff_column, extract_date_part, add_column_op, moving_average_column,
)
from desktop.workers import TransformWorker

log = logging.getLogger(__name__)


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

        # -- Header --
        header = QLabel("🛠️ Column Manager")
        header.setStyleSheet("font-size: 16px; font-weight: 700; color: #e2e8f0;")
        root.addWidget(header)

        # -- Tabs --
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget { background: transparent; }"
            "QTabBar::tab {"
            "  background: #1e293b; color: #94a3b8;"
            "  border: 1px solid #334155; border-bottom: none;"
            "  border-top-left-radius: 6px; border-top-right-radius: 6px;"
            "  padding: 8px 16px; font-size: 12px;"
            "}"
            "QTabBar::tab:selected { background: #0f172a; color: #e2e8f0; }"
        )

        # Tab 1: Add Column
        self.tab_add = QWidget()
        self._build_add_tab()
        self.tabs.addTab(self.tab_add, "➕ Add Column")

        # Tab 2: Remove Column
        self.tab_remove = QWidget()
        self._build_remove_tab()
        self.tabs.addTab(self.tab_remove, "🗑️ Remove Column")

        # Tab 3: Rename Column
        self.tab_rename = QWidget()
        self._build_rename_tab()
        self.tabs.addTab(self.tab_rename, "✏️ Rename Column")

        root.addWidget(self.tabs, stretch=1)

        # Progress bar (hidden by default)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar { background: #1e293b; border: none; height: 4px; }"
            "QProgressBar::chunk { background: #4f46e5; }"
        )
        root.addWidget(self.progress)

    def _build_add_tab(self) -> None:
        layout = QVBoxLayout(self.tab_add)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # New column name -- label and field share one compact row and the
        # field is width-capped so it doesn't stretch across the whole pane.
        name_row = QHBoxLayout()
        name_lbl = QLabel("New column name:")
        name_lbl.setStyleSheet("font-size: 12px; color: #94a3b8;")
        name_row.addWidget(name_lbl)
        self.txt_new_col = QLineEdit()
        self.txt_new_col.setPlaceholderText("e.g. Total_Sales")
        self.txt_new_col.setFixedWidth(280)
        self.txt_new_col.setStyleSheet(
            "QLineEdit { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; }"
        )
        name_row.addWidget(self.txt_new_col)
        name_row.addStretch(1)
        layout.addLayout(name_row)

        # Calculation type -- same compact row pattern.
        calc_row = QHBoxLayout()
        calc_lbl = QLabel("Calculation type:")
        calc_lbl.setStyleSheet("font-size: 12px; color: #94a3b8;")
        calc_row.addWidget(calc_lbl)
        self.cmb_calc_type = QComboBox()
        self.cmb_calc_type.addItems([
            "Custom formula (use col names)",
            "Column × Column", "Column ÷ Column", "Column + Column", "Column − Column",
            "Date Difference", "Extract Date/Time Part", "Moving Average",
        ])
        self.cmb_calc_type.setFixedWidth(280)
        self.cmb_calc_type.setStyleSheet(
            "QComboBox { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; }"
        )
        calc_row.addWidget(self.cmb_calc_type)
        calc_row.addStretch(1)
        layout.addLayout(calc_row)

        # Dynamic options area
        self.options_area = QWidget()
        self.options_layout = QVBoxLayout(self.options_area)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.options_area)

        # Preview button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_preview = QPushButton("🔎 Preview Result")
        self.btn_preview.setStyleSheet(
            "QPushButton { background: #4f46e5; color: white; border: none;"
            "  border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
        )
        btn_row.addWidget(self.btn_preview)
        layout.addLayout(btn_row)

        # Preview result
        self.lbl_preview = QLabel("")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setStyleSheet("font-size: 11px; color: #94a3b8;")
        layout.addWidget(self.lbl_preview)

        # Apply button
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self.btn_apply_add = QPushButton("✅ Add Column")
        self.btn_apply_add.setStyleSheet(
            "QPushButton { background: #10b981; color: white; border: none;"
            "  border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
        )
        apply_row.addWidget(self.btn_apply_add)
        layout.addLayout(apply_row)
        # Push everything to the top; without a trailing stretch Qt spreads
        # these few controls across the whole pane height (Issue 5).
        layout.addStretch(1)

        # Calculation type change rebuilds options
        self.cmb_calc_type.currentTextChanged.connect(self._on_calc_type_changed)
        self._on_calc_type_changed(self.cmb_calc_type.currentText())

    def _build_remove_tab(self) -> None:
        layout = QVBoxLayout(self.tab_remove)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Select columns to remove:"))
        self.list_remove = QListWidget()
        self.list_remove.setSelectionMode(QListWidget.MultiSelection)
        self.list_remove.setStyleSheet(
            "QListWidget { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px; font-size: 12px; }"
        )
        layout.addWidget(self.list_remove, stretch=1)

        self.chk_confirm_remove = QCheckBox("I confirm removal of selected columns")
        self.chk_confirm_remove.setStyleSheet("color: #e2e8f0; font-size: 12px;")
        layout.addWidget(self.chk_confirm_remove)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_apply_remove = QPushButton("🗑️ Remove Selected Columns")
        self.btn_apply_remove.setEnabled(False)
        self.btn_apply_remove.setStyleSheet(
            "QPushButton { background: #ef4444; color: white; border: none;"
            "  border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
        )
        btn_row.addWidget(self.btn_apply_remove)
        layout.addLayout(btn_row)
        # Keep controls compact (Issue 5) -- the remove list already has
        # stretch=1, so this stretch sits below the button row.
        layout.addStretch(1)

        self.chk_confirm_remove.toggled.connect(self.btn_apply_remove.setEnabled)

    def _build_rename_tab(self) -> None:
        layout = QVBoxLayout(self.tab_rename)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Select column to rename:"))
        self.cmb_rename_col = QComboBox()
        self.cmb_rename_col.setStyleSheet(
            "QComboBox { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; }"
        )
        layout.addWidget(self.cmb_rename_col)

        layout.addWidget(QLabel("New name:"))
        self.txt_rename = QLineEdit()
        self.txt_rename.setPlaceholderText("Enter new column name")
        self.txt_rename.setStyleSheet(
            "QLineEdit { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; }"
        )
        layout.addWidget(self.txt_rename)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_apply_rename = QPushButton("✏️ Rename Column")
        self.btn_apply_rename.setEnabled(False)
        self.btn_apply_rename.setStyleSheet(
            "QPushButton { background: #4f46e5; color: white; border: none;"
            "  border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
        )
        btn_row.addWidget(self.btn_apply_rename)
        layout.addLayout(btn_row)
        # Add stretch so rename controls stay top-aligned and compact (Issue 5).
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

        # Populate remove list
        self.list_remove.clear()
        for col in df.columns:
            item = QListWidgetItem(col)
            self.list_remove.addItem(item)

        # Populate rename dropdown
        self.cmb_rename_col.clear()
        self.cmb_rename_col.addItems(df.columns.tolist())

        # Rebuild the Add Column tab's options area for the new data.
        # ROOT CAUSE of the "where is the column selection dropdown"
        # report: _on_calc_type_changed() only ever ran once, during
        # __init__, when self._df was still None -- it hit its own
        # early-return guard and built nothing. set_dataframe() never
        # called it again after real data loaded, so the ENTIRE options
        # area below "Calculation type" (not just a picker -- the
        # formula text field itself, the Column x Column selectors,
        # everything) stayed empty for any real dataset, for every
        # calculation type, not just Custom Formula.
        self._on_calc_type_changed(self.cmb_calc_type.currentText())

    # -- Slots: Add Column --------------------------------------------------

    def _on_insert_formula_column(self) -> None:
        """Insert the picked column name, backtick-wrapped, at the
        current cursor position in the formula field -- replaces
        whatever text is currently selected, same as a normal text
        insert, so picking a column mid-edit doesn't clobber the rest
        of the formula."""
        col = self.cmb_formula_col.currentText()
        if not col:
            return
        self.txt_formula.insert(f"`{col}`")
        self.txt_formula.setFocus()

    def _clear_layout(self, layout) -> None:
        """Recursively delete every widget and sub-layout in ``layout``.

        The old clear loop only removed top-level items -- rows added via
        ``addLayout()`` were detached from this layout but their child
        widgets were never deleted, so they stayed visible on top of the
        newly built options (the "conjured"/overlapping First & Second
        column dropdowns reported on the Custom formula tab).
        """
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
        cat_cols = self._df.select_dtypes(include="object").columns.tolist()
        dt_cols = self._df.select_dtypes(include="datetime").columns.tolist()
        all_cols = self._df.columns.tolist()

        combo_qss = (
            "QComboBox { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px; padding: 5px 8px; font-size: 12px; }"
        )
        lbl_qss = "font-size: 12px; color: #94a3b8;"

        if text == "Custom formula (use col names)":
            # No separate First/Second column dropdowns here -- a formula
            # can reference any number of columns. The Insert picker below
            # is the only column-selection UI needed.
            row = QHBoxLayout()
            lbl = QLabel("Formula (`col` / `col`):")
            lbl.setStyleSheet(lbl_qss)
            row.addWidget(lbl)
            self.txt_formula = QLineEdit()
            self.txt_formula.setPlaceholderText("`Sales` / `Units`")
            self.txt_formula.setStyleSheet(
                "QLineEdit { background: #1e293b; color: #e2e8f0;"
                "  border: 1px solid #334155; border-radius: 6px; padding: 5px 8px; font-size: 12px; }"
            )
            row.addWidget(self.txt_formula, stretch=1)
            self.options_layout.addLayout(row)

            picker_row = QHBoxLayout()
            picker_lbl = QLabel("Insert column:")
            picker_lbl.setStyleSheet(lbl_qss)
            picker_row.addWidget(picker_lbl)
            self.cmb_formula_col = QComboBox()
            self.cmb_formula_col.addItems(all_cols)
            self.cmb_formula_col.setFixedWidth(220)
            self.cmb_formula_col.setStyleSheet(combo_qss)
            picker_row.addWidget(self.cmb_formula_col)
            btn_insert = QPushButton("+ Insert")
            btn_insert.setStyleSheet(
                "QPushButton { background: #1e293b; color: #a5b4fc; border: 1px solid #334155;"
                "  border-radius: 6px; padding: 5px 12px; font-size: 11px; }"
                "QPushButton:hover { background: #334155; }"
            )
            btn_insert.clicked.connect(self._on_insert_formula_column)
            picker_row.addWidget(btn_insert)
            picker_row.addStretch(1)
            self.options_layout.addLayout(picker_row)

        elif text in ("Column × Column", "Column ÷ Column", "Column + Column", "Column − Column"):
            op_map = {
                "Column × Column": "*", "Column ÷ Column": "/",
                "Column + Column": "+", "Column − Column": "-",
            }
            op = op_map[text]
            row = QHBoxLayout()
            lbl_a = QLabel("First column:")
            lbl_a.setStyleSheet(lbl_qss)
            row.addWidget(lbl_a)
            self.cmb_col_a = QComboBox()
            self.cmb_col_a.addItems(num_cols)
            self.cmb_col_a.setFixedWidth(200)
            self.cmb_col_a.setStyleSheet(combo_qss)
            row.addWidget(self.cmb_col_a)
            op_lbl = QLabel(op)
            op_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #a5b4fc;")
            row.addWidget(op_lbl)
            lbl_b = QLabel("Second column:")
            lbl_b.setStyleSheet(lbl_qss)
            row.addWidget(lbl_b)
            self.cmb_col_b = QComboBox()
            self.cmb_col_b.addItems(num_cols)
            self.cmb_col_b.setFixedWidth(200)
            self.cmb_col_b.setStyleSheet(combo_qss)
            row.addWidget(self.cmb_col_b)
            row.addStretch(1)
            self.options_layout.addLayout(row)
            self._add_op = op

        elif text == "Date Difference":
            row = QHBoxLayout()
            lbl_a = QLabel("Start date column:")
            lbl_a.setStyleSheet(lbl_qss)
            row.addWidget(lbl_a)
            self.cmb_date_a = QComboBox()
            self.cmb_date_a.addItems(all_cols)
            self.cmb_date_a.setFixedWidth(200)
            self.cmb_date_a.setStyleSheet(combo_qss)
            row.addWidget(self.cmb_date_a)
            row.addStretch(1)
            self.options_layout.addLayout(row)

            row2 = QHBoxLayout()
            self.rb_today = QPushButton("Today's date")
            self.rb_today.setCheckable(True)
            self.rb_today.setChecked(True)
            self.rb_today.setStyleSheet(
                "QPushButton { background: #4f46e5; color: white; border: none;"
                "  border-radius: 4px; padding: 4px 10px; font-size: 11px; }"
            )
            self.rb_another_col = QPushButton("Another column")
            self.rb_another_col.setCheckable(True)
            self.rb_another_col.setStyleSheet(
                "QPushButton { background: #1e293b; color: #94a3b8; border: 1px solid #334155;"
                "  border-radius: 4px; padding: 4px 10px; font-size: 11px; }"
            )
            self.rb_today.clicked.connect(lambda: self._set_date_mode("today"))
            self.rb_another_col.clicked.connect(lambda: self._set_date_mode("column"))
            row2.addWidget(self.rb_today)
            row2.addWidget(self.rb_another_col)
            row2.addStretch(1)
            self.options_layout.addLayout(row2)

            row3 = QHBoxLayout()
            lbl_b = QLabel("End date column:")
            lbl_b.setStyleSheet(lbl_qss)
            row3.addWidget(lbl_b)
            self.cmb_date_b = QComboBox()
            self.cmb_date_b.addItems([c for c in all_cols if c != self.cmb_date_a.currentText()])
            self.cmb_date_b.setFixedWidth(200)
            self.cmb_date_b.setStyleSheet(combo_qss)
            row3.addWidget(self.cmb_date_b)
            row3.addStretch(1)
            self.options_layout.addLayout(row3)
            self.cmb_date_a.currentTextChanged.connect(
                lambda text: self.cmb_date_b.clear() or self.cmb_date_b.addItems([c for c in all_cols if c != text])
            )
            # Apply the initial (Today's date) mode immediately so the
            # end-date combo's enabled/disabled state matches the active
            # toggle from the very first render.
            self._set_date_mode("today")

            row4 = QHBoxLayout()
            lbl_u = QLabel("Difference unit:")
            lbl_u.setStyleSheet(lbl_qss)
            row4.addWidget(lbl_u)
            self.cmb_diff_unit = QComboBox()
            self.cmb_diff_unit.addItems([
                "Days", "Months", "Years", "Age (Years from DOB vs Today)",
                "Previous Year (subtract 1 year)", "Previous Month (subtract 1 month)",
            ])
            self.cmb_diff_unit.setFixedWidth(260)
            self.cmb_diff_unit.setStyleSheet(combo_qss)
            row4.addWidget(self.cmb_diff_unit)
            row4.addStretch(1)
            self.options_layout.addLayout(row4)

        elif text == "Extract Date/Time Part":
            row = QHBoxLayout()
            lbl_s = QLabel("Source date column:")
            lbl_s.setStyleSheet(lbl_qss)
            row.addWidget(lbl_s)
            self.cmb_date_src = QComboBox()
            self.cmb_date_src.addItems(all_cols)
            self.cmb_date_src.setFixedWidth(200)
            self.cmb_date_src.setStyleSheet(combo_qss)
            row.addWidget(self.cmb_date_src)
            lbl_p = QLabel("Part to extract:")
            lbl_p.setStyleSheet(lbl_qss)
            row.addWidget(lbl_p)
            self.cmb_date_part = QComboBox()
            self.cmb_date_part.addItems([
                "Date (YYYY-MM-DD)", "Year", "Quarter", "Month (Number)", "Month Name",
                "Week Number", "Day", "Weekday Name", "Hour (12h AM/PM)", "Hour (24h)",
            ])
            self.cmb_date_part.setFixedWidth(200)
            self.cmb_date_part.setStyleSheet(combo_qss)
            row.addWidget(self.cmb_date_part)
            row.addStretch(1)
            self.options_layout.addLayout(row)

        elif text == "Moving Average":
            row = QHBoxLayout()
            lbl_c = QLabel("Column:")
            lbl_c.setStyleSheet(lbl_qss)
            row.addWidget(lbl_c)
            self.cmb_ma_col = QComboBox()
            self.cmb_ma_col.addItems(num_cols)
            self.cmb_ma_col.setFixedWidth(200)
            self.cmb_ma_col.setStyleSheet(combo_qss)
            row.addWidget(self.cmb_ma_col)
            lbl_w = QLabel("Window (rows):")
            lbl_w.setStyleSheet(lbl_qss)
            row.addWidget(lbl_w)
            self.spin_ma_window = QSpinBox()
            self.spin_ma_window.setRange(2, 999)
            self.spin_ma_window.setValue(7)
            self.spin_ma_window.setFixedWidth(80)
            self.spin_ma_window.setStyleSheet(
                "QSpinBox { background: #1e293b; color: #e2e8f0;"
                "  border: 1px solid #334155; border-radius: 6px; padding: 5px 8px; font-size: 12px; }"
            )
            row.addWidget(self.spin_ma_window)
            hint = QLabel("average of the last N rows incl. current")
            hint.setStyleSheet("font-size: 11px; color: #64748b;")
            row.addWidget(hint)
            row.addStretch(1)
            self.options_layout.addLayout(row)

    def _set_date_mode(self, mode: str) -> None:
        """Toggle between today and another column for date diff."""
        if mode == "today":
            self.rb_today.setChecked(True)
            self.rb_today.setStyleSheet(
                "QPushButton { background: #4f46e5; color: white; border: none;"
                "  border-radius: 4px; padding: 4px 10px; font-size: 11px; }"
            )
            self.rb_another_col.setChecked(False)
            self.rb_another_col.setStyleSheet(
                "QPushButton { background: #1e293b; color: #94a3b8; border: 1px solid #334155;"
                "  border-radius: 4px; padding: 4px 10px; font-size: 11px; }"
            )
        else:
            self.rb_another_col.setChecked(True)
            self.rb_another_col.setStyleSheet(
                "QPushButton { background: #4f46e5; color: white; border: none;"
                "  border-radius: 4px; padding: 4px 10px; font-size: 11px; }"
            )
            self.rb_today.setChecked(False)
            self.rb_today.setStyleSheet(
                "QPushButton { background: #1e293b; color: #94a3b8; border: 1px solid #334155;"
                "  border-radius: 4px; padding: 4px 10px; font-size: 11px; }"
            )
        # End-date column is only meaningful when the user explicitly
        # picked "Another column" -- keep it visibly disabled otherwise.
        if hasattr(self, "cmb_date_b"):
            self.cmb_date_b.setEnabled(mode != "today")

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
                formula = self.txt_formula.text().strip()
                if not formula:
                    QMessageBox.warning(self, "Missing Formula", "Please enter a formula.")
                    return
                result = safe_formula_eval(self._df, formula)

            elif calc_type in ("Column × Column", "Column ÷ Column", "Column + Column", "Column − Column"):
                col_a = self.cmb_col_a.currentText()
                col_b = self.cmb_col_b.currentText()
                result = add_column_op(self._df, col_a, col_b, self._add_op)

            elif calc_type == "Date Difference":
                col_a = self.cmb_date_a.currentText()
                # Only read the end-date combo when the user explicitly
                # picked "Another column"; default to today if somehow
                # neither toggle is checked.
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
                QMessageBox.warning(self, "Not Implemented", f"Calculation type '{calc_type}' not yet implemented.")
                return

            # Show preview
            preview = result.head(5)
            preview_str = "\n".join([f"{i}: {v}" for i, v in preview.items()])
            self.lbl_preview.setText(f"Preview (first 5 rows):\n{preview_str}")

        except Exception as exc:
            QMessageBox.critical(self, "Preview Error", str(exc))
            log.error("Preview failed: %s", exc)

    def _on_apply_add(self) -> None:
        """Apply the add column operation in a background thread."""
        if self._df is None or self._df.empty:
            return

        new_col_name = self.txt_new_col.text().strip()
        if not new_col_name:
            return

        if new_col_name in self._df.columns:
            QMessageBox.warning(self, "Duplicate Column", f"A column named '{new_col_name}' already exists.")
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
            formula = self.txt_formula.text().strip()
            result = safe_formula_eval(self._df, formula)
        elif calc_type in ("Column × Column", "Column ÷ Column", "Column + Column", "Column − Column"):
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
        self.operation_applied.emit(f"✅ Added column '{self.txt_new_col.text().strip()}'")
        self.lbl_preview.setText("")
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
        self.operation_applied.emit(f"✅ Removed columns")
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
            QMessageBox.warning(self, "Duplicate Column", f"A column named '{new_name}' already exists.")
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
        self.operation_applied.emit(f"✅ Renamed column")
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