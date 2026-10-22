"""desktop/widgets/data_tools_panel.py -- Data transformation & cleaning panel.

Replaces the Streamlit upload-page expanders (Column Manager, Data-type
Transformer, Data Cleaner, Data Quality Summary, Outlier Detection) with
a native Qt tabbed panel. Every action calls a pure function in
``services/transform_service.py`` or the analysis runners in
``modules/analysis/*`` and emits ``df_updated`` with the resulting
DataFrame -- the panel itself never touches SessionBridge directly, so
it can be reused/tested standalone.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QCheckBox, QSpinBox, QDoubleSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QGroupBox, QMessageBox, QListWidget,
    QListWidgetItem, QScrollArea, QGridLayout, QRadioButton, QButtonGroup,
)

from services import transform_service as ts
from modules.analysis.data_quality import run_data_quality
from modules.analysis.outlier import remove_outlier_rows

log = logging.getLogger(__name__)

# Styling handled by global QSS (theme.py) via kind=primary and objectName selectors


def _fill_preview_table(table: QTableWidget, df: pd.DataFrame) -> None:
    table.clear()
    table.setColumnCount(len(df.columns))
    table.setRowCount(len(df))
    table.setHorizontalHeaderLabels([str(c) for c in df.columns])
    for r in range(len(df)):
        for c, col in enumerate(df.columns):
            table.setItem(r, c, QTableWidgetItem(str(df.iloc[r, c])))
    table.resizeColumnsToContents()


class _BaseToolTab(QWidget):
    """Shared helpers for tool tabs: current df access + apply signal."""

    apply_requested = Signal(object, str)  # new_df, message

    def __init__(self, get_df, parent=None) -> None:
        super().__init__(parent)
        self._get_df = get_df

    @property
    def df(self) -> Optional[pd.DataFrame]:
        return self._get_df()

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "Error", msg)


# =========================================================================
# Column Manager
# =========================================================================

class ColumnManagerTab(_BaseToolTab):
    def __init__(self, get_df, parent=None) -> None:
        super().__init__(get_df, parent)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        self.action = QComboBox()
        self.action.addItems(["Add derived column", "Remove column", "Rename column"])
        self.action
        root.addWidget(self.action)

        self.form_area = QWidget()
        self.form_layout = QFormLayout(self.form_area)
        root.addWidget(self.form_area)

        self.btn_apply = QPushButton("✅ Apply")
        self.btn_apply.setProperty("kind", "primary")
        root.addWidget(self.btn_apply, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addStretch()

        self.action.currentIndexChanged.connect(self._rebuild_form)
        self.btn_apply.clicked.connect(self._apply)
        self._rebuild_form()

    def _clear_form(self) -> None:
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)

    def _rebuild_form(self) -> None:
        self._clear_form()
        df = self.df
        cols = list(df.columns) if df is not None else []
        num_cols = list(df.select_dtypes("number").columns) if df is not None else []
        mode = self.action.currentText()

        if mode == "Add derived column":
            self.new_col_name = QLineEdit()
            self.new_col_name
            self.form_layout.addRow("New column name", self.new_col_name)

            self.calc_type = QComboBox()
            self.calc_type.addItems([
                "Custom formula", "Column × Column", "Column ÷ Column",
                "Column + Column", "Column − Column",
                "Extract Date/Time Part", "Date Difference",
            ])
            self.calc_type
            self.form_layout.addRow("Calculation type", self.calc_type)

            self.formula_input = QLineEdit()
            self.formula_input.setPlaceholderText("e.g. `Sales` / `Units`")
            self.formula_input
            self.form_layout.addRow("Formula (use `Column Name`)", self.formula_input)

            self.col_a = QComboBox(); self.col_a.addItems(num_cols); self.col_a
            self.col_b = QComboBox(); self.col_b.addItems(num_cols); self.col_b
            self.form_layout.addRow("First column", self.col_a)
            self.form_layout.addRow("Second column", self.col_b)

            self.date_col = QComboBox(); self.date_col.addItems(cols); self.date_col
            self.date_part = QComboBox(); self.date_part.addItems(ts.DATE_PART_OPTIONS); self.date_part
            self.form_layout.addRow("Source date column", self.date_col)
            self.form_layout.addRow("Part to extract", self.date_part)

            self.date_col_a = QComboBox(); self.date_col_a.addItems(cols); self.date_col_a
            self.date_col_b = QComboBox(); self.date_col_b.addItems(["(today's date)"] + cols); self.date_col_b
            self.date_unit = QComboBox()
            self.date_unit.addItems([
                "Days", "Months", "Years", "Age (Years from DOB vs Today)",
                "Previous Year (subtract 1 year)", "Previous Month (subtract 1 month)",
            ])
            self.date_unit
            self.form_layout.addRow("Start date column", self.date_col_a)
            self.form_layout.addRow("End date column", self.date_col_b)
            self.form_layout.addRow("Difference unit", self.date_unit)

            def sync_visibility():
                mode2 = self.calc_type.currentText()
                self.formula_input.setVisible(mode2 == "Custom formula")
                self.col_a.setVisible(mode2 in ("Column × Column", "Column ÷ Column", "Column + Column", "Column − Column"))
                self.col_b.setVisible(mode2 in ("Column × Column", "Column ÷ Column", "Column + Column", "Column − Column"))
                self.date_col.setVisible(mode2 == "Extract Date/Time Part")
                self.date_part.setVisible(mode2 == "Extract Date/Time Part")
                self.date_col_a.setVisible(mode2 == "Date Difference")
                self.date_col_b.setVisible(mode2 == "Date Difference")
                self.date_unit.setVisible(mode2 == "Date Difference")
            self.calc_type.currentIndexChanged.connect(sync_visibility)
            sync_visibility()

        elif mode == "Remove column":
            self.col_remove = QComboBox(); self.col_remove.addItems(cols); self.col_remove
            self.form_layout.addRow("Column to remove", self.col_remove)

        elif mode == "Rename column":
            self.col_rename = QComboBox(); self.col_rename.addItems(cols); self.col_rename
            self.new_name = QLineEdit(); self.new_name
            self.form_layout.addRow("Column to rename", self.col_rename)
            self.form_layout.addRow("New name", self.new_name)

    def _apply(self) -> None:
        df = self.df
        if df is None:
            self._error("No dataset loaded.")
            return
        mode = self.action.currentText()
        try:
            if mode == "Add derived column":
                name = self.new_col_name.text().strip()
                if not name:
                    self._error("Enter a name for the new column.")
                    return
                calc = self.calc_type.currentText()
                if calc == "Custom formula":
                    series = ts.safe_formula_eval(df, self.formula_input.text())
                elif calc in ("Column × Column", "Column ÷ Column", "Column + Column", "Column − Column"):
                    op_map = {"Column × Column": "*", "Column ÷ Column": "/",
                              "Column + Column": "+", "Column − Column": "-"}
                    series = ts.add_column_op(df, self.col_a.currentText(), self.col_b.currentText(), op_map[calc])
                elif calc == "Extract Date/Time Part":
                    series = ts.extract_date_part(df, self.date_col.currentText(), self.date_part.currentText())
                else:  # Date Difference
                    col_b = self.date_col_b.currentText()
                    col_b = None if col_b == "(today's date)" else col_b
                    series = ts.date_diff_column(df, self.date_col_a.currentText(), col_b, self.date_unit.currentText())
                new_df = ts.add_derived_column(df, name, series)
                self.apply_requested.emit(new_df, f"Added column '{name}'.")
            elif mode == "Remove column":
                col = self.col_remove.currentText()
                new_df = ts.remove_column(df, col)
                self.apply_requested.emit(new_df, f"Removed column '{col}'.")
            else:
                col = self.col_rename.currentText()
                new_name = self.new_name.text().strip()
                if not new_name:
                    self._error("Enter a new name.")
                    return
                new_df = ts.rename_column(df, col, new_name)
                self.apply_requested.emit(new_df, f"Renamed '{col}' to '{new_name}'.")
        except Exception as e:
            self._error(str(e))

    def refresh(self) -> None:
        self._rebuild_form()


# =========================================================================
# Dtype Transformer
# =========================================================================

class DtypeTransformerTab(_BaseToolTab):
    def __init__(self, get_df, parent=None) -> None:
        super().__init__(get_df, parent)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        row = QHBoxLayout()
        self.col_select = QComboBox(); self.col_select
        self.dtype_select = QComboBox(); self.dtype_select.addItems(ts.DTYPE_OPTIONS); self.dtype_select
        row.addWidget(QLabel("Column:")); row.addWidget(self.col_select, stretch=1)
        row.addWidget(QLabel("Convert to:")); row.addWidget(self.dtype_select, stretch=1)
        root.addLayout(row)

        self.btn_preview = QPushButton("👁 Preview conversion")
        self.btn_preview.setProperty("kind", "primary")
        root.addWidget(self.btn_preview, alignment=Qt.AlignmentFlag.AlignLeft)

        self.lbl_result = QLabel("")
        self.lbl_result.setProperty("class", "muted")
        root.addWidget(self.lbl_result)

        self.preview_table = QTableWidget()
        self.preview_table
        self.preview_table.setMaximumHeight(220)
        root.addWidget(self.preview_table)

        self.btn_apply = QPushButton("✅ Apply conversion")
        self.btn_apply.setProperty("kind", "primary")
        self.btn_apply.setEnabled(False)
        root.addWidget(self.btn_apply, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addStretch()

        self.btn_preview.clicked.connect(self._preview)
        self.btn_apply.clicked.connect(self._apply)

    def refresh(self) -> None:
        df = self.df
        self.col_select.clear()
        if df is not None:
            self.col_select.addItems([str(c) for c in df.columns])
        self.btn_apply.setEnabled(False)
        self.preview_table.clear()
        self.lbl_result.setText("")

    def _preview(self) -> None:
        df = self.df
        col = self.col_select.currentText()
        if df is None or not col:
            return
        result = ts.preview_dtype_conversion(df[col], self.dtype_select.currentText())
        if "error" in result:
            self._error(result["error"])
            self.btn_apply.setEnabled(False)
            return
        self.lbl_result.setText(
            f"{result['success']}/{result['total']} values convert successfully "
            f"({result['pct']}%) — {result['new_nulls']} new null(s) — new dtype: {result['dtype_after']}"
        )
        _fill_preview_table(self.preview_table, result["sample_df"])
        self.btn_apply.setEnabled(True)

    def _apply(self) -> None:
        df = self.df
        col = self.col_select.currentText()
        try:
            new_df = ts.apply_dtype_conversion(df, col, self.dtype_select.currentText())
            self.apply_requested.emit(new_df, f"Converted '{col}' to {self.dtype_select.currentText()}.")
        except Exception as e:
            self._error(str(e))


# =========================================================================
# Data Cleaner
# =========================================================================

class DataCleanerTab(_BaseToolTab):
    def __init__(self, get_df, parent=None) -> None:
        super().__init__(get_df, parent)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        self.op_select = QComboBox()
        self.op_select.addItems([
            "Text clean", "Find & replace", "Validate column",
            "String operations", "Numeric clean",
        ])
        self.op_select
        root.addWidget(self.op_select)

        self.form_area = QWidget()
        self.form_layout = QFormLayout(self.form_area)
        root.addWidget(self.form_area)

        self.btn_preview = QPushButton("👁 Preview")
        self.btn_preview.setProperty("kind", "primary")
        root.addWidget(self.btn_preview, alignment=Qt.AlignmentFlag.AlignLeft)

        self.lbl_result = QLabel("")
        self.lbl_result.setProperty("class", "muted")
        self.lbl_result.setWordWrap(True)
        root.addWidget(self.lbl_result)

        self.preview_table = QTableWidget()
        self.preview_table
        self.preview_table.setMaximumHeight(220)
        root.addWidget(self.preview_table)

        self.btn_apply = QPushButton("✅ Apply")
        self.btn_apply.setProperty("kind", "primary")
        self.btn_apply.setEnabled(False)
        root.addWidget(self.btn_apply, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addStretch()

        self.op_select.currentIndexChanged.connect(self._rebuild_form)
        self.btn_preview.clicked.connect(self._preview)
        self.btn_apply.clicked.connect(self._apply)
        self._pending: Optional[dict] = None
        self._rebuild_form()

    def refresh(self) -> None:
        self._rebuild_form()

    def _clear_form(self) -> None:
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        self.preview_table.clear()
        self.lbl_result.setText("")
        self.btn_apply.setEnabled(False)
        self._pending = None

    def _str_cols(self):
        df = self.df
        return list(df.select_dtypes(include=["object", "string", "category"]).columns) if df is not None else []

    def _num_cols(self):
        df = self.df
        return list(df.select_dtypes("number").columns) if df is not None else []

    def _rebuild_form(self) -> None:
        self._clear_form()
        op = self.op_select.currentText()

        if op == "Text clean":
            self.tc_col = QComboBox(); self.tc_col.addItems(self._str_cols()); self.tc_col
            self.form_layout.addRow("Column", self.tc_col)
            self.tc_checks = {}
            grid_w = QWidget()
            grid = QGridLayout(grid_w)
            labels = [
                ("strip", "Strip whitespace", True), ("lower", "Lowercase", False),
                ("upper", "Uppercase", False), ("title", "Title Case", False),
                ("rm_special", "Remove special chars", False), ("alphanumeric", "Alphanumeric only", False),
                ("rm_digits", "Remove digits", False), ("collapse_spaces", "Collapse spaces", False),
                ("rm_html", "Strip HTML tags", False), ("unicode", "Normalise unicode", False),
                ("rm_newlines", "Remove newlines", False), ("empty_to_na", "Empty string → NaN", False),
            ]
            for i, (key, label, default) in enumerate(labels):
                cb = QCheckBox(label); cb.setChecked(default)
                self.tc_checks[key] = cb
                grid.addWidget(cb, i // 3, i % 3)
            self.form_layout.addRow(grid_w)

        elif op == "Find & replace":
            self.fr_col = QComboBox(); self.fr_col.addItems(self._str_cols()); self.fr_col
            self.fr_find = QLineEdit(); self.fr_find
            self.fr_replace = QLineEdit(); self.fr_replace
            self.fr_regex = QCheckBox("Use regular expression")
            self.fr_ci = QCheckBox("Case-insensitive")
            self.form_layout.addRow("Column", self.fr_col)
            self.form_layout.addRow("Find", self.fr_find)
            self.form_layout.addRow("Replace with", self.fr_replace)
            self.form_layout.addRow(self.fr_regex)
            self.form_layout.addRow(self.fr_ci)

        elif op == "Validate column":
            self.val_col = QComboBox(); self.val_col.addItems(list(self.df.columns) if self.df is not None else []); self.val_col
            self.val_pattern = QComboBox()
            self.val_pattern.addItems(list(ts.VALIDATION_PATTERNS.keys()) + ["Custom regex…"])
            self.val_pattern
            self.val_custom = QLineEdit(); self.val_custom.setPlaceholderText(r"e.g. ^\d{4}-\d{2}-\d{2}$")
            self.val_custom
            self.val_action = QComboBox()
            self.val_action.addItems(["Just report", "Replace failing values with NaN", "Add validity flag column"])
            self.val_action
            self.form_layout.addRow("Column", self.val_col)
            self.form_layout.addRow("Validation rule", self.val_pattern)
            self.form_layout.addRow("Custom pattern", self.val_custom)
            self.form_layout.addRow("Action on failures", self.val_action)

        elif op == "String operations":
            self.so_op = QComboBox()
            self.so_op.addItems([
                "Trim to fixed length", "Extract substring (start, end)",
                "Split column by delimiter", "Concatenate two columns",
                "Extract with regex capture group",
            ])
            self.so_op
            self.so_col = QComboBox(); self.so_col.addItems(self._str_cols()); self.so_col
            self.so_col2 = QComboBox(); self.so_col2.addItems(list(self.df.columns) if self.df is not None else []); self.so_col2
            self.so_len = QSpinBox(); self.so_len.setRange(1, 1000); self.so_len.setValue(50); self.so_len
            self.so_start = QSpinBox(); self.so_start.setRange(0, 999); self.so_start
            self.so_end = QSpinBox(); self.so_end.setRange(0, 999); self.so_end
            self.so_delim = QLineEdit(","); self.so_delim
            self.so_sep = QLineEdit(" "); self.so_sep
            self.so_regex = QLineEdit(); self.so_regex.setPlaceholderText(r"e.g. (\d{4})"); self.so_regex
            self.so_new_col = QLineEdit(); self.so_new_col
            self.form_layout.addRow("Operation", self.so_op)
            self.form_layout.addRow("Source column", self.so_col)
            self.form_layout.addRow("Second column", self.so_col2)
            self.form_layout.addRow("Length", self.so_len)
            self.form_layout.addRow("Start", self.so_start)
            self.form_layout.addRow("End (0 = to end)", self.so_end)
            self.form_layout.addRow("Delimiter", self.so_delim)
            self.form_layout.addRow("Separator", self.so_sep)
            self.form_layout.addRow("Regex pattern", self.so_regex)
            self.form_layout.addRow("New column name", self.so_new_col)

            def sync():
                m = self.so_op.currentText()
                self.so_col2.setVisible(m == "Concatenate two columns")
                self.so_len.setVisible(m == "Trim to fixed length")
                self.so_start.setVisible(m == "Extract substring (start, end)")
                self.so_end.setVisible(m == "Extract substring (start, end)")
                self.so_delim.setVisible(m == "Split column by delimiter")
                self.so_sep.setVisible(m == "Concatenate two columns")
                self.so_regex.setVisible(m == "Extract with regex capture group")
                self.so_new_col.setVisible(m != "Trim to fixed length")
            self.so_op.currentIndexChanged.connect(sync)
            sync()

        else:  # Numeric clean
            self.nc_col = QComboBox(); self.nc_col.addItems(self._num_cols()); self.nc_col
            self.nc_op = QComboBox()
            self.nc_op.addItems([
                "Clamp to min/max range", "Fill outliers (IQR) with median",
                "Fill outliers (IQR) with mean", "Round to N decimal places",
                "Scale to 0-1 (min-max)", "Z-score standardisation",
                "Fill NaN with median", "Fill NaN with mean", "Fill NaN with custom value",
            ])
            self.nc_op
            self.nc_min = QDoubleSpinBox(); self.nc_min.setRange(-1e12, 1e12); self.nc_min
            self.nc_max = QDoubleSpinBox(); self.nc_max.setRange(-1e12, 1e12); self.nc_max
            self.nc_iqr = QDoubleSpinBox(); self.nc_iqr.setRange(1.0, 3.0); self.nc_iqr.setValue(1.5); self.nc_iqr.setSingleStep(0.1); self.nc_iqr
            self.nc_decimals = QSpinBox(); self.nc_decimals.setRange(0, 10); self.nc_decimals.setValue(2); self.nc_decimals
            self.nc_fill = QDoubleSpinBox(); self.nc_fill.setRange(-1e12, 1e12); self.nc_fill
            self.form_layout.addRow("Column", self.nc_col)
            self.form_layout.addRow("Operation", self.nc_op)
            self.form_layout.addRow("Min", self.nc_min)
            self.form_layout.addRow("Max", self.nc_max)
            self.form_layout.addRow("IQR multiplier", self.nc_iqr)
            self.form_layout.addRow("Decimal places", self.nc_decimals)
            self.form_layout.addRow("Fill value", self.nc_fill)

            def sync():
                m = self.nc_op.currentText()
                self.nc_min.setVisible(m == "Clamp to min/max range")
                self.nc_max.setVisible(m == "Clamp to min/max range")
                self.nc_iqr.setVisible("IQR" in m)
                self.nc_decimals.setVisible(m == "Round to N decimal places")
                self.nc_fill.setVisible(m == "Fill NaN with custom value")
            self.nc_op.currentIndexChanged.connect(sync)
            sync()

    def _preview(self) -> None:
        df = self.df
        if df is None:
            return
        op = self.op_select.currentText()
        try:
            if op == "Text clean":
                col = self.tc_col.currentText()
                if not col:
                    return
                ops = {k: cb.isChecked() for k, cb in self.tc_checks.items()}
                new_series = ts.text_clean_preview(df[col], ops)
                n_changed = int((df[col].astype(str) != new_series.astype(str)).sum())
                self.lbl_result.setText(f"{n_changed:,} of {len(df):,} value(s) will change in '{col}'.")
                _fill_preview_table(self.preview_table, pd.DataFrame({
                    "Before": df[col].astype(str).head(50), "After": new_series.astype(str).head(50)}))
                self._pending = {"kind": "column", "col": col, "series": new_series}

            elif op == "Find & replace":
                col = self.fr_col.currentText()
                if not col or not self.fr_find.text():
                    self._error("Enter a search term.")
                    return
                new_series = ts.find_replace_preview(
                    df[col], self.fr_find.text(), self.fr_replace.text(),
                    use_regex=self.fr_regex.isChecked(), case_insensitive=self.fr_ci.isChecked())
                n_changed = int((df[col].astype(str) != new_series.astype(str)).sum())
                self.lbl_result.setText(f"{n_changed:,} of {len(df):,} value(s) will change in '{col}'.")
                _fill_preview_table(self.preview_table, pd.DataFrame({
                    "Before": df[col].astype(str).head(50), "After": new_series.astype(str).head(50)}))
                self._pending = {"kind": "column", "col": col, "series": new_series}

            elif op == "Validate column":
                col = self.val_col.currentText()
                pat_name = self.val_pattern.currentText()
                pattern = self.val_custom.text() if pat_name == "Custom regex…" else ts.VALIDATION_PATTERNS[pat_name]
                if not pattern:
                    self._error("Enter a pattern.")
                    return
                result = ts.validate_column(df, col, pattern)
                self.lbl_result.setText(
                    f"✅ Pass: {result['n_pass']:,}  ❌ Fail: {result['n_fail']:,}  ⬜ Null: {result['n_null']:,}"
                )
                _fill_preview_table(self.preview_table, result["fail_rows"])
                self._pending = {"kind": "validate", "col": col, "pattern": pattern,
                                  "fail_index": result["fail_index"], "action": self.val_action.currentText()}

            elif op == "String operations":
                m = self.so_op.currentText()
                col = self.so_col.currentText()
                if not col:
                    return
                series = df[col].astype(str)
                new_col_name = self.so_new_col.text().strip() or None
                if m == "Trim to fixed length":
                    new_series = series.str[: self.so_len.value()]
                    new_col_name = None
                elif m == "Extract substring (start, end)":
                    end = self.so_end.value()
                    new_series = series.str[self.so_start.value(): end if end > 0 else None]
                elif m == "Split column by delimiter":
                    new_series = series.str.split(self.so_delim.text(), n=1, expand=False).apply(
                        lambda x: x[0] if isinstance(x, list) and x else None)
                elif m == "Concatenate two columns":
                    new_series = series.str.cat(df[self.so_col2.currentText()].astype(str), sep=self.so_sep.text())
                else:  # regex extract
                    pattern = self.so_regex.text().strip()
                    if not pattern:
                        self._error("Enter a regex pattern.")
                        return
                    new_series = series.str.extract(f"({pattern.strip('()')})", expand=False)

                target = new_col_name or col
                self.lbl_result.setText(f"Preview -> {'new column ' + target if new_col_name else 'column ' + target}")
                _fill_preview_table(self.preview_table, pd.DataFrame({
                    "Before": series.head(50), "After": new_series.astype(str).head(50)}))
                self._pending = {"kind": "new_col" if new_col_name else "column",
                                  "col": new_col_name or col, "series": new_series}

            else:  # Numeric clean
                col = self.nc_col.currentText()
                if not col:
                    return
                op_map = {
                    "Clamp to min/max range": "clamp", "Fill outliers (IQR) with median": "iqr_median",
                    "Fill outliers (IQR) with mean": "iqr_mean", "Round to N decimal places": "round",
                    "Scale to 0-1 (min-max)": "minmax", "Z-score standardisation": "zscore",
                    "Fill NaN with median": "fillna_median", "Fill NaN with mean": "fillna_mean",
                    "Fill NaN with custom value": "fillna_value",
                }
                kwargs = {
                    "min": self.nc_min.value(), "max": self.nc_max.value(),
                    "iqr_multiplier": self.nc_iqr.value(), "decimals": self.nc_decimals.value(),
                    "value": self.nc_fill.value(),
                }
                new_series = ts.numeric_clean(df[col], op_map[self.nc_op.currentText()], **kwargs)
                n_changed = int((df[col].round(8) != new_series.round(8)).sum())
                self.lbl_result.setText(f"{n_changed:,} of {len(df):,} value(s) will change in '{col}'.")
                _fill_preview_table(self.preview_table, pd.DataFrame({
                    "Before": df[col].head(50), "After": new_series.round(4).head(50)}))
                self._pending = {"kind": "column", "col": col, "series": new_series}

            self.btn_apply.setEnabled(True)
        except Exception as e:
            self._error(str(e))
            self.btn_apply.setEnabled(False)

    def _apply(self) -> None:
        df = self.df
        if df is None or not self._pending:
            return
        try:
            pending = self._pending
            if pending["kind"] in ("column", "new_col"):
                new_df = ts.apply_column_update(df, pending["col"], pending["series"])
                msg = f"Updated '{pending['col']}'." if pending["kind"] == "column" else f"Created '{pending['col']}'."
            else:  # validate
                if pending["action"] == "Replace failing values with NaN":
                    new_df = ts.validate_replace_failures_with_nan(df, pending["col"], pending["fail_index"])
                    msg = f"Set {len(pending['fail_index']):,} invalid value(s) to NaN in '{pending['col']}'."
                elif pending["action"] == "Add validity flag column":
                    new_df = ts.validate_flag_column(df, pending["col"], pending["pattern"])
                    msg = f"Added validity flag column for '{pending['col']}'."
                else:
                    return
            self.apply_requested.emit(new_df, msg)
        except Exception as e:
            self._error(str(e))


# =========================================================================
# Data Quality
# =========================================================================

class DataQualityTab(_BaseToolTab):
    def __init__(self, get_df, parent=None) -> None:
        super().__init__(get_df, parent)
        root = QVBoxLayout(self)
        self.btn_run = QPushButton("🔍 Run Data Quality Analysis")
        self.btn_run.setProperty("kind", "primary")
        root.addWidget(self.btn_run, alignment=Qt.AlignmentFlag.AlignLeft)

        self.results_area = QVBoxLayout()
        root.addLayout(self.results_area)
        root.addStretch()

        self.btn_run.clicked.connect(self._run)
        self._views = []

    def refresh(self) -> None:
        pass

    def _run(self) -> None:
        for v in self._views:
            v.deleteLater()
        self._views.clear()

        df = self.df
        if df is None:
            self._error("No dataset loaded.")
            return

        charts = run_data_quality(df)
        if not charts:
            lbl = QLabel("✅ No data quality issues detected.")
            lbl.setStyleSheet("color: #10b981;")
            self.results_area.addWidget(lbl)
            self._views.append(lbl)
            return

        for title, fig in charts:
            from desktop.widgets.echarts_web_view import EChartsWebView
            view = EChartsWebView()
            view.setMinimumHeight(260)
            view.setMaximumHeight(380)
            view.set_chart_model(fig)
            self.results_area.addWidget(view)
            self._views.append(view)


# =========================================================================
# Outlier Detection
# =========================================================================

class OutlierTab(_BaseToolTab):
    def __init__(self, get_df, parent=None) -> None:
        super().__init__(get_df, parent)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        row = QHBoxLayout()
        self.mult = QDoubleSpinBox(); self.mult.setRange(0.5, 5.0); self.mult.setValue(1.5); self.mult.setSingleStep(0.5)
        self.mult
        row.addWidget(QLabel("IQR multiplier:")); row.addWidget(self.mult)
        row.addStretch()
        root.addLayout(row)

        self.col_list = QListWidget()
        self.col_list
        self.col_list.setMaximumHeight(120)
        root.addWidget(self.col_list)

        self.btn_detect = QPushButton("🔍 Detect Outliers")
        self.btn_detect.setProperty("kind", "primary")
        root.addWidget(self.btn_detect, alignment=Qt.AlignmentFlag.AlignLeft)

        self.lbl_result = QLabel("")
        self.lbl_result.setProperty("class", "muted")
        root.addWidget(self.lbl_result)

        self.summary_table = QTableWidget()
        self.summary_table
        self.summary_table.setMaximumHeight(180)
        root.addWidget(self.summary_table)

        self.btn_delete_all = QPushButton("🗑️ Delete all flagged outlier rows")
        self.btn_delete_all.setProperty("kind", "danger")
        self.btn_delete_all.setEnabled(False)
        root.addWidget(self.btn_delete_all, alignment=Qt.AlignmentFlag.AlignLeft)
        self.chart_area = QVBoxLayout()
        self.chart_area.setSpacing(8)
        root.addLayout(self.chart_area)
        root.addStretch()

        self.btn_detect.clicked.connect(self._detect)
        self.btn_delete_all.clicked.connect(self._delete_all)
        self._results = None
        self._chart_views = []

    def refresh(self) -> None:
        self._clear_outlier_charts()
        df = self.df
        self.col_list.clear()
        self.summary_table.clear()
        self.lbl_result.setText("")
        self.btn_delete_all.setEnabled(False)
        self._results = None
        if df is None:
            return
        num_cols = list(df.select_dtypes("number").columns)
        for c in num_cols[:12]:
            item = QListWidgetItem(c)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.col_list.addItem(item)

    def _selected_cols(self) -> list:
        cols = []
        for i in range(self.col_list.count()):
            item = self.col_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                cols.append(item.text())
        return cols

    def _detect(self) -> None:
        df = self.df
        cols = self._selected_cols()
        if df is None or not cols:
            self._error("Select at least one numeric column.")
            return
        self._clear_outlier_charts()
        from services.polars_engine import PolarsDataset
        from services.polars_chart_service import (
            build_outlier_charts, detect_outliers_polars,
        )
        dataset = PolarsDataset.from_pandas(df, source="outlier-tool")
        results = detect_outliers_polars(dataset, cols, self.mult.value())
        results = {c: v for c, v in results.items() if v["out_count"] > 0}
        self._results = results
        if not results:
            self.lbl_result.setText("✅ No outliers detected in the selected columns.")
            self.summary_table.clear()
            self.btn_delete_all.setEnabled(False)
            return

        total = sum(v["out_count"] for v in results.values())
        self.lbl_result.setText(f"{total:,} outlier value(s) across {len(results)} column(s).")

        summary_df = pd.DataFrame([
            {"Column": c, "Outliers": v["out_count"], "% of rows": f"{v['pct']}%",
             "Lower fence": f"{v['lo']:.4g}", "Upper fence": f"{v['hi']:.4g}"}
            for c, v in results.items()
        ])
        _fill_preview_table(self.summary_table, summary_df)
        models = build_outlier_charts(dataset, list(results),
                                      multiplier=self.mult.value())
        from desktop.widgets.echarts_web_view import EChartsWebView
        for model in models:
            view = EChartsWebView()
            view.setMinimumHeight(260)
            view.setMaximumHeight(340)
            view.set_chart_model(model)
            self.chart_area.addWidget(view)
            self._chart_views.append(view)
        self.btn_delete_all.setEnabled(True)

    def _clear_outlier_charts(self) -> None:
        while self.chart_area.count():
            item = self.chart_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._chart_views.clear()

    def _delete_all(self) -> None:
        df = self.df
        if df is None or not self._results:
            return
        row_ids = set()
        for info in self._results.values():
            row_ids.update(info.get("row_ids", []))
        new_df = remove_outlier_rows(df, row_ids=list(row_ids))
        self.apply_requested.emit(new_df, f"Removed {len(row_ids):,} outlier row(s).")


# =========================================================================
# Container panel
# =========================================================================

class DataToolsPanel(QWidget):
    """Tabbed panel combining Column Manager, Dtype Transformer, Data
    Cleaner, Data Quality, and Outlier Detection. Emits ``df_updated``
    with the new DataFrame whenever any tool applies a change."""

    df_updated = Signal(object, str)  # new_df, message

    def __init__(self, get_df, parent=None) -> None:
        super().__init__(parent)
        self._get_df = get_df

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #1e293b; border-radius: 8px; }"
            "QTabBar::tab { background: #1e293b; color: #94a3b8; padding: 8px 14px; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #4f46e5; color: white; }"
        )
        root.addWidget(self.tabs)

        self.column_manager = ColumnManagerTab(self._get_df)
        self.dtype_transformer = DtypeTransformerTab(self._get_df)
        self.data_cleaner = DataCleanerTab(self._get_df)
        self.data_quality = DataQualityTab(self._get_df)
        self.outliers = OutlierTab(self._get_df)

        self.tabs.addTab(self._wrap_scroll(self.column_manager), "🧩 Column Manager")
        self.tabs.addTab(self._wrap_scroll(self.dtype_transformer), "🔢 Dtype Transformer")
        self.tabs.addTab(self._wrap_scroll(self.data_cleaner), "🧼 Data Cleaner")
        self.tabs.addTab(self._wrap_scroll(self.data_quality), "🧹 Data Quality")
        self.tabs.addTab(self._wrap_scroll(self.outliers), "🚨 Outliers")

        for tab in (self.column_manager, self.dtype_transformer, self.data_cleaner, self.outliers):
            tab.apply_requested.connect(self._on_tool_applied)

    def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        return scroll

    def _on_tool_applied(self, new_df, message: str) -> None:
        self.df_updated.emit(new_df, message)

    def refresh(self) -> None:
        """Call after the underlying DataFrame changes to refresh all tabs."""
        self.column_manager.refresh()
        self.dtype_transformer.refresh()
        self.data_cleaner.refresh()
        self.data_quality.refresh()
        self.outliers.refresh()
