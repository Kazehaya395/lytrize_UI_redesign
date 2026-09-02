"""desktop/pages/data_cleaner_panel.py -- Data cleaning and validation widget.

Replaces the Streamlit ``show_data_cleaner()`` with a native Qt panel that:
  - Text Clean: strip, case, remove HTML/unicode/special chars
  - Find & Replace: with regex support and live preview
  - Validate: regex pattern validation with pass/fail metrics
  - String Ops: trim, pad, substring, split, concatenate, regex extract
  - Numeric Clean: clamp, IQR outlier fill, round, min-max, z-score, fill NaN
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QLineEdit, QMessageBox,
    QTabWidget, QCheckBox, QSpinBox, QDoubleSpinBox, QRadioButton,
    QButtonGroup, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QFrame, QGroupBox,
)

from services.transform_service import (
    text_clean_preview, find_replace_preview,
    validate_column, validate_replace_failures_with_nan, validate_flag_column,
    numeric_clean, apply_column_update, VALIDATION_PATTERNS,
)
from desktop.workers import TransformWorker

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Single source of truth for String Ops display names and their option labels.
# The option-builder / preview / apply all key off these constants so a label
# typo can never desync the dynamic option widgets from the handlers again.
# ---------------------------------------------------------------------------
OP_TRIM = "Trim to fixed length"
OP_PAD = "Pad to fixed length (left / right)"
OP_SUBSTRING = "Extract substring (start, end)"
OP_SPLIT = "Split column into two (by delimiter)"
OP_CONCAT = "Concatenate two columns"
OP_REGEX_EXTRACT = "Extract with regex capture group"

# Numeric Clean display-name → backend op-key. Frontend combos show the
# human label; the backend `numeric_clean()` accepts the compact keys.
NUMERIC_CLEAN_OP_KEYS = {
    "Clamp to min/max range": "clamp",
    "Fill outliers (IQR) with median": "iqr_median",
    "Fill outliers (IQR) with mean": "iqr_mean",
    "Round to N decimal places": "round",
    "Scale to 0-1 (min-max)": "minmax",
    "Z-score standardisation": "zscore",
    "Fill NaN with median": "fillna_median",
    "Fill NaN with mean": "fillna_mean",
    "Fill NaN with custom value": "fillna_value",
}

# Apply-button enable states, one per tab. `_set_processing` consults these
# instead of blindly re-enabling every apply button after a worker finishes.
_APPLY_STATES = {"text": False, "find": False, "validate": False,
                 "string": False, "numeric": False}


def count_changed_values(before: pd.Series, after: pd.Series) -> int:
    """Count values that genuinely differ, treating NaN == NaN as unchanged.

    A naive ``(before != after).sum()`` counts NaN-vs-NaN pairs as changes
    (NaN != NaN is True in pandas), which inflated the "N values will change"
    labels and could enable Apply even when nothing would actually change.
    String/NaN rendering via ``astype(str)`` plus an explicit both-null mask
    keeps the count honest (Issue 14: reliable operation reporting).
    """
    differ = before.astype(str) != after.astype(str)
    both_na = pd.isna(before) & pd.isna(after)
    return int((differ & ~both_na).sum())


# UI issue: input bars stretched across the full window width look bad on
# wide monitors ('too width & long selection bar & text bar') -- cap every
# combo / text field / spin box to a sensible width (left-aligned).
INPUT_MAX_WIDTH = 480


def cap_input_widths(widget) -> None:
    """Cap the width of every combo / text field / spin box under *widget*."""
    # PySide6 findChildren does not accept a tuple of types (PyQt-ism) --
    # query one type at a time.
    for cls in (QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox):
        for w in widget.findChildren(cls):
            w.setMaximumWidth(INPUT_MAX_WIDTH)


class DataCleanerPanel(QWidget):
    """Data cleaning panel with 5 tabs.

    Emits ``operation_applied`` when a cleaning operation succeeds.
    """

    operation_applied = Signal(str)  # emits success message

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._df: Optional[pd.DataFrame] = None
        self._preview_df: Optional[pd.DataFrame] = None

        self._build_ui()
        self._connect_signals()
        self._worker = None

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # -- Header --
        header = QLabel("🧹 Data Cleaning & Validation")
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

        self.tab_text = QWidget()
        self._build_text_tab()
        self.tabs.addTab(self.tab_text, "🔤 Text Clean")

        self.tab_find = QWidget()
        self._build_find_tab()
        self.tabs.addTab(self.tab_find, "🔄 Find & Replace")

        self.tab_validate = QWidget()
        self._build_validate_tab()
        self.tabs.addTab(self.tab_validate, "✅ Validate")

        self.tab_string = QWidget()
        self._build_string_tab()
        self.tabs.addTab(self.tab_string, "✂️ String Ops")

        self.tab_numeric = QWidget()
        self._build_numeric_tab()
        self.tabs.addTab(self.tab_numeric, "🔢 Numeric Clean")

        root.addWidget(self.tabs, stretch=1)

        # Roomier, centered row-index gutters on every preview table (UI
        # issue: index numbers were cramped against the Before column).
        for tbl in (self.table_text_preview, self.table_find_preview,
                    self.table_failures, self.table_str_preview,
                    self.table_num_preview):
            tbl.verticalHeader().setFixedWidth(36)
            tbl.verticalHeader().setDefaultAlignment(Qt.AlignCenter)

        # Cap input-bar widths on every static tab (see INPUT_MAX_WIDTH).
        cap_input_widths(self.tabs)

    def _build_text_tab(self) -> None:
        layout = QVBoxLayout(self.tab_text)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Column selector
        layout.addWidget(QLabel("Column to clean:"))
        self.cmb_text_col = QComboBox()
        layout.addWidget(self.cmb_text_col)

        # Operations
        ops_group = QGroupBox("Select operations to apply (applied in order):")
        ops_layout = QVBoxLayout(ops_group)
        c1 = QHBoxLayout()
        c2 = QHBoxLayout()
        c3 = QHBoxLayout()

        self.chk_strip = QCheckBox("Strip whitespace")
        self.chk_strip.setChecked(True)
        self.chk_lower = QCheckBox("Lowercase")
        self.chk_upper = QCheckBox("Uppercase")
        self.chk_title = QCheckBox("Title Case")
        self.chk_rmspec = QCheckBox("Remove special chars")
        self.chk_alphanum = QCheckBox("Alphanumeric only")
        self.chk_rmdigits = QCheckBox("Remove digits")
        self.chk_rmspaces = QCheckBox("Collapse spaces")
        self.chk_rmhtml = QCheckBox("Strip HTML tags")
        self.chk_unicode = QCheckBox("Normalise unicode")
        self.chk_rmnl = QCheckBox("Remove newlines")
        self.chk_emptyna = QCheckBox("Empty → NaN")

        c1.addWidget(self.chk_strip)
        c1.addWidget(self.chk_lower)
        c1.addWidget(self.chk_upper)
        c2.addWidget(self.chk_title)
        c2.addWidget(self.chk_rmspec)
        c2.addWidget(self.chk_alphanum)
        c3.addWidget(self.chk_rmdigits)
        c3.addWidget(self.chk_rmspaces)
        c3.addWidget(self.chk_rmhtml)
        ops_layout.addLayout(c1)
        ops_layout.addLayout(c2)
        ops_layout.addLayout(c3)
        c1.addWidget(self.chk_unicode)
        c1.addWidget(self.chk_rmnl)
        c1.addWidget(self.chk_emptyna)
        layout.addWidget(ops_group)

        # Preview
        self.lbl_text_preview = QLabel("")
        self.lbl_text_preview.setWordWrap(True)
        self.lbl_text_preview.setStyleSheet("font-size: 11px; color: #94a3b8;")
        layout.addWidget(self.lbl_text_preview)

        self.table_text_preview = QTableWidget()
        self.table_text_preview.setColumnCount(2)
        self.table_text_preview.setHorizontalHeaderLabels(["Before", "After"])
        self.table_text_preview.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_text_preview.setStyleSheet(
            "QTableWidget { background: #1e293b; color: #e2e8f0; gridline-color: #334155; }"
        )
        # A small explicit minimum (not a fixed height) so this table can't
        # inflate the whole Data Cleaner tab's floor beyond what's needed --
        # QTabWidget.minimumSizeHint() is based on the LARGEST of all its
        # sub-tabs, so an oversized table here forces every other sub-tab
        # (and the outer transform-tools splitter pane) to stay just as
        # tall even when a much smaller tab is the one actually showing.
        # The table still scrolls internally past this height, so no data
        # becomes inaccessible -- it just stops forcing extra vertical
        # space when the user is trying to shrink the panel.
        self.table_text_preview.setMinimumHeight(80)
        layout.addWidget(self.table_text_preview)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_text_preview = QPushButton("🔎 Preview")
        self.btn_text_apply = QPushButton("✅ Apply Text Clean")
        self.btn_text_apply.setEnabled(False)
        btn_row.addWidget(self.btn_text_preview)
        btn_row.addWidget(self.btn_text_apply)
        layout.addLayout(btn_row)

        self.btn_text_preview.clicked.connect(self._on_text_preview)
        self.btn_text_apply.clicked.connect(self._on_text_apply)

    def _build_find_tab(self) -> None:
        layout = QVBoxLayout(self.tab_find)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Column:"))
        self.cmb_find_col = QComboBox()
        layout.addWidget(self.cmb_find_col)

        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find:"))
        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("e.g. N/A  or  ^\\d+$")
        find_row.addWidget(self.txt_find)
        layout.addLayout(find_row)

        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Replace with:"))
        self.txt_replace = QLineEdit()
        self.txt_replace.setPlaceholderText("Leave blank to delete")
        replace_row.addWidget(self.txt_replace)
        layout.addLayout(replace_row)

        opts_row = QHBoxLayout()
        self.chk_find_regex = QCheckBox("Use regex")
        self.chk_find_ci = QCheckBox("Case-insensitive")
        opts_row.addWidget(self.chk_find_regex)
        opts_row.addWidget(self.chk_find_ci)
        layout.addLayout(opts_row)

        self.lbl_find_preview = QLabel("")
        self.lbl_find_preview.setWordWrap(True)
        self.lbl_find_preview.setStyleSheet("font-size: 11px; color: #94a3b8;")
        layout.addWidget(self.lbl_find_preview)

        self.table_find_preview = QTableWidget()
        self.table_find_preview.setColumnCount(2)
        self.table_find_preview.setHorizontalHeaderLabels(["Before", "After"])
        self.table_find_preview.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_find_preview.setStyleSheet(
            "QTableWidget { background: #1e293b; color: #e2e8f0; gridline-color: #334155; }"
        )
        # Small explicit minimum, not a fixed height -- see the identical
        # comment on table_text_preview above for why this matters (a
        # QTabWidget's minimumSizeHint is driven by its LARGEST sub-tab).
        self.table_find_preview.setMinimumHeight(80)
        layout.addWidget(self.table_find_preview)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_find_preview = QPushButton("🔎 Preview")
        self.btn_find_apply = QPushButton("✅ Apply Find & Replace")
        self.btn_find_apply.setEnabled(False)
        btn_row.addWidget(self.btn_find_preview)
        btn_row.addWidget(self.btn_find_apply)
        layout.addLayout(btn_row)

        self.txt_find.textChanged.connect(
            lambda text: self.btn_find_apply.setEnabled(bool(text.strip()))
        )
        self.btn_find_preview.clicked.connect(self._on_find_preview)
        self.btn_find_apply.clicked.connect(self._on_find_apply)

    def _build_validate_tab(self) -> None:
        layout = QVBoxLayout(self.tab_validate)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Column to validate:"))
        self.cmb_val_col = QComboBox()
        layout.addWidget(self.cmb_val_col)

        layout.addWidget(QLabel("Validation rule:"))
        self.cmb_val_pattern = QComboBox()
        self.cmb_val_pattern.addItems([
            "Email address", "Phone (intl. +XX)", "UK postcode", "US ZIP code",
            "Integer number", "Decimal number", "Date (YYYY-MM-DD)",
            "URL (http/https)", "UUID", "Custom regex…",
        ])
        layout.addWidget(self.cmb_val_pattern)

        self.txt_val_custom = QLineEdit()
        self.txt_val_custom.setPlaceholderText(r"e.g. ^\d{4}-\d{2}-\d{2}$")
        self.txt_val_custom.setVisible(False)
        layout.addWidget(self.txt_val_custom)

        self.cmb_val_pattern.currentTextChanged.connect(
            lambda text: self.txt_val_custom.setVisible(text == "Custom regex…")
        )

        # Metrics
        metrics_row = QHBoxLayout()
        self.lbl_pass = QLabel("✅ Pass: 0")
        self.lbl_fail = QLabel("❌ Fail: 0")
        self.lbl_null = QLabel("⬜ Null: 0")
        for lbl in (self.lbl_pass, self.lbl_fail, self.lbl_null):
            lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #e2e8f0;")
            metrics_row.addWidget(lbl)
        layout.addLayout(metrics_row)

        # Failing rows table
        self.table_failures = QTableWidget()
        self.table_failures.setColumnCount(1)
        self.table_failures.setHorizontalHeaderLabels(["Failing Values"])
        self.table_failures.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_failures.setStyleSheet(
            "QTableWidget { background: #1e293b; color: #e2e8f0; gridline-color: #334155; }"
        )
        self.table_failures.setMaximumHeight(150)
        self.table_failures.setMinimumHeight(60)
        layout.addWidget(self.table_failures)

        # Action
        layout.addWidget(QLabel("Action on failing rows:"))
        self.rb_val_none = QRadioButton("None — just report")
        self.rb_val_none.setChecked(True)
        self.rb_val_nan = QRadioButton("Replace failing values with NaN")
        self.rb_val_flag = QRadioButton("Flag: add boolean column '_valid_<col>'")
        for rb in (self.rb_val_none, self.rb_val_nan, self.rb_val_flag):
            rb.setStyleSheet("color: #e2e8f0; font-size: 12px;")
            layout.addWidget(rb)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_val_apply = QPushButton("✅ Apply Validation")
        self.btn_val_apply.setEnabled(False)
        btn_row.addWidget(self.btn_val_apply)
        layout.addLayout(btn_row)

        self.btn_val_apply.clicked.connect(self._on_val_apply)
        self.cmb_val_col.currentTextChanged.connect(self._on_validate_col_changed)
        self.cmb_val_pattern.currentTextChanged.connect(self._on_validate_pattern_changed)

    def _build_string_tab(self) -> None:
        layout = QVBoxLayout(self.tab_string)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Operation:"))
        self.cmb_str_op = QComboBox()
        self.cmb_str_op.addItems([
            OP_TRIM,
            OP_PAD,
            OP_SUBSTRING,
            OP_SPLIT,
            OP_CONCAT,
            OP_REGEX_EXTRACT,
        ])
        layout.addWidget(self.cmb_str_op)

        layout.addWidget(QLabel("Source column:"))
        self.cmb_str_col = QComboBox()
        layout.addWidget(self.cmb_str_col)

        # Dynamic options
        self.str_options = QWidget()
        self.str_options_layout = QVBoxLayout(self.str_options)
        layout.addWidget(self.str_options)

        self.lbl_str_preview = QLabel("")
        self.lbl_str_preview.setWordWrap(True)
        self.lbl_str_preview.setStyleSheet("font-size: 11px; color: #94a3b8;")
        layout.addWidget(self.lbl_str_preview)

        self.table_str_preview = QTableWidget()
        self.table_str_preview.setColumnCount(2)
        self.table_str_preview.setHorizontalHeaderLabels(["Before", "After"])
        self.table_str_preview.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_str_preview.setStyleSheet(
            "QTableWidget { background: #1e293b; color: #e2e8f0; gridline-color: #334155; }"
        )
        # Removed fixed height to allow full resizing via splitter
        self.table_str_preview.setMinimumHeight(80)
        layout.addWidget(self.table_str_preview)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_str_preview = QPushButton("🔎 Preview")
        self.btn_str_apply = QPushButton("✅ Apply")
        self.btn_str_apply.setEnabled(False)
        btn_row.addWidget(self.btn_str_preview)
        btn_row.addWidget(self.btn_str_apply)
        layout.addLayout(btn_row)

        self.cmb_str_op.currentTextChanged.connect(self._on_str_op_changed)
        self.btn_str_preview.clicked.connect(self._on_str_preview)
        self.btn_str_apply.clicked.connect(self._on_str_apply)
        self._on_str_op_changed(self.cmb_str_op.currentText())

    def _build_numeric_tab(self) -> None:
        layout = QVBoxLayout(self.tab_numeric)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Column:"))
        self.cmb_num_col = QComboBox()
        layout.addWidget(self.cmb_num_col)

        layout.addWidget(QLabel("Operation:"))
        self.cmb_num_op = QComboBox()
        self.cmb_num_op.addItems([
            "Clamp to min/max range",
            "Fill outliers (IQR) with median",
            "Fill outliers (IQR) with mean",
            "Round to N decimal places",
            "Scale to 0-1 (min-max)",
            "Z-score standardisation",
            "Fill NaN with median",
            "Fill NaN with mean",
            "Fill NaN with custom value",
        ])
        layout.addWidget(self.cmb_num_op)

        # Dynamic options
        self.num_options = QWidget()
        self.num_options_layout = QVBoxLayout(self.num_options)
        layout.addWidget(self.num_options)

        self.lbl_num_preview = QLabel("")
        self.lbl_num_preview.setWordWrap(True)
        self.lbl_num_preview.setStyleSheet("font-size: 11px; color: #94a3b8;")
        layout.addWidget(self.lbl_num_preview)

        self.table_num_preview = QTableWidget()
        self.table_num_preview.setColumnCount(2)
        self.table_num_preview.setHorizontalHeaderLabels(["Before", "After"])
        self.table_num_preview.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_num_preview.setStyleSheet(
            "QTableWidget { background: #1e293b; color: #e2e8f0; gridline-color: #334155; }"
        )
        self.table_num_preview.setMinimumHeight(80)
        layout.addWidget(self.table_num_preview)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_num_preview = QPushButton("🔎 Preview")
        self.btn_num_apply = QPushButton("✅ Apply Numeric Clean")
        self.btn_num_apply.setEnabled(False)
        btn_row.addWidget(self.btn_num_preview)
        btn_row.addWidget(self.btn_num_apply)
        layout.addLayout(btn_row)

        self.cmb_num_op.currentTextChanged.connect(self._on_num_op_changed)
        self.btn_num_preview.clicked.connect(self._on_num_preview)
        self.btn_num_apply.clicked.connect(self._on_num_apply)
        self._on_num_op_changed(self.cmb_num_op.currentText())

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        # Changing the source column invalidates any previous preview in the
        # String Ops / Numeric Clean tabs (Issue 11: a stale "N values will
        # change" label with an enabled Apply button let users apply an
        # operation whose parameters no longer matched the preview).
        self.cmb_str_col.currentTextChanged.connect(self._reset_string_preview)
        self.cmb_num_col.currentTextChanged.connect(self._reset_numeric_preview)

    # -- Public API ---------------------------------------------------------

    def set_dataframe(self, df: pd.DataFrame) -> None:
        """Load a DataFrame and populate column dropdowns."""
        self._df = df
        if df is None or df.empty:
            self.cmb_text_col.clear()
            self.cmb_find_col.clear()
            self.cmb_val_col.clear()
            self.cmb_str_col.clear()
            self.cmb_num_col.clear()
            return

        cols = df.columns.tolist()
        for cmb in (self.cmb_text_col, self.cmb_find_col, self.cmb_val_col,
                    self.cmb_str_col, self.cmb_num_col):
            cmb.clear()
            cmb.addItems(cols)

    # -- Slots: Text Clean --------------------------------------------------

    def _on_text_preview(self) -> None:
        if self._df is None:
            return
        col = self.cmb_text_col.currentText()
        ops = {
            "strip": self.chk_strip.isChecked(),
            "lower": self.chk_lower.isChecked(),
            "upper": self.chk_upper.isChecked(),
            "title": self.chk_title.isChecked(),
            "rm_html": self.chk_rmhtml.isChecked(),
            "unicode": self.chk_unicode.isChecked(),
            "rm_newlines": self.chk_rmnl.isChecked(),
            "rm_special": self.chk_rmspec.isChecked(),
            "alphanumeric": self.chk_alphanum.isChecked(),
            "rm_digits": self.chk_rmdigits.isChecked(),
            "collapse_spaces": self.chk_rmspaces.isChecked(),
            "empty_to_na": self.chk_emptyna.isChecked(),
        }
        try:
            result = text_clean_preview(self._df[col].astype(str), ops)
            n_changed = int((self._df[col].astype(str) != result).sum())
            self.lbl_text_preview.setText(f"{n_changed:,} values will change")
            self._fill_preview_table(self.table_text_preview,
                                     self._df[col].astype(str), result.astype(str))
            self.btn_text_apply.setEnabled(n_changed > 0)
            _APPLY_STATES["text"] = n_changed > 0
        except Exception as exc:
            QMessageBox.critical(self, "Preview Error", str(exc))

    def _on_text_apply(self) -> None:
        if self._df is None:
            return
        col = self.cmb_text_col.currentText()
        ops = {
            "strip": self.chk_strip.isChecked(),
            "lower": self.chk_lower.isChecked(),
            "upper": self.chk_upper.isChecked(),
            "title": self.chk_title.isChecked(),
            "rm_html": self.chk_rmhtml.isChecked(),
            "unicode": self.chk_unicode.isChecked(),
            "rm_newlines": self.chk_rmnl.isChecked(),
            "rm_special": self.chk_rmspec.isChecked(),
            "alphanumeric": self.chk_alphanum.isChecked(),
            "rm_digits": self.chk_rmdigits.isChecked(),
            "collapse_spaces": self.chk_rmspaces.isChecked(),
            "empty_to_na": self.chk_emptyna.isChecked(),
        }
        self._set_processing(True, "text")
        self._worker = TransformWorker(self._compute_text_clean, col, ops)
        self._worker.finished_ok.connect(self._on_text_clean_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _compute_text_clean(self, col: str, ops: dict):
        """Compute text clean (runs in worker thread)."""
        result = text_clean_preview(self._df[col].astype(str), ops)
        return apply_column_update(self._df, col, result)

    def _on_text_clean_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful text clean."""
        self.bridge.set_df(new_df)
        self.operation_applied.emit(f"✅ Cleaned column '{self.cmb_text_col.currentText()}'")
        self.btn_text_apply.setEnabled(False)
        _APPLY_STATES["text"] = False
        self._set_processing(False, "text")

    # -- Slots: Find & Replace ----------------------------------------------

    def _on_find_preview(self) -> None:
        if self._df is None:
            return
        col = self.cmb_find_col.currentText()
        find = self.txt_find.text()
        if not find:
            return
        replace = self.txt_replace.text()
        use_regex = self.chk_find_regex.isChecked()
        ci = self.chk_find_ci.isChecked()
        try:
            result = find_replace_preview(self._df[col].astype(str), find, replace, use_regex, ci)
            n_changed = int((self._df[col].astype(str) != result).sum())
            self.lbl_find_preview.setText(f"{n_changed:,} values will change")
            self._fill_preview_table(self.table_find_preview,
                                     self._df[col].astype(str), result.astype(str))
            self.btn_find_apply.setEnabled(n_changed > 0)
            _APPLY_STATES["find"] = n_changed > 0
        except (re.error, ValueError) as e:
            # re.error: malformed regex; ValueError: e.g. pandas rejecting
            # flags with regex=False (pre-guard backend). Show a friendly
            # message instead of a silent dead preview.
            QMessageBox.warning(self, "Invalid Regex", str(e))

    def _on_find_apply(self) -> None:
        if self._df is None:
            return
        col = self.cmb_find_col.currentText()
        find = self.txt_find.text()
        if not find:
            # Mirrors the preview guard (and the backend guard) -- an empty
            # find is a no-op: str.replace("", X) would insert X between
            # every character and corrupt the whole column.
            return
        replace = self.txt_replace.text()
        use_regex = self.chk_find_regex.isChecked()
        ci = self.chk_find_ci.isChecked()
        self._set_processing(True, "find")
        self._worker = TransformWorker(self._compute_find_replace, col, find, replace, use_regex, ci)
        self._worker.finished_ok.connect(self._on_find_replace_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _compute_find_replace(self, col: str, find: str, replace: str, use_regex: bool, ci: bool):
        """Compute find/replace (runs in worker thread)."""
        result = find_replace_preview(self._df[col].astype(str), find, replace, use_regex, ci)
        return apply_column_update(self._df, col, result)

    def _on_find_replace_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful find/replace."""
        self.bridge.set_df(new_df)
        self.operation_applied.emit(f"✅ Find & replace applied to '{self.cmb_find_col.currentText()}'")
        self.btn_find_apply.setEnabled(False)
        _APPLY_STATES["find"] = False
        self._set_processing(False, "find")

    # -- Slots: Validate ----------------------------------------------------

    def _on_validate_col_changed(self, text: str) -> None:
        self._run_validation()

    def _on_validate_pattern_changed(self, text: str) -> None:
        self.txt_val_custom.setVisible(text == "Custom regex…")
        self._run_validation()

    def _on_val_apply(self) -> None:
        if self._df is None:
            return
        col = self.cmb_val_col.currentText()
        pattern_name = self.cmb_val_pattern.currentText()
        pattern = self.txt_val_custom.text() if pattern_name == "Custom regex…" else VALIDATION_PATTERNS.get(pattern_name, "")

        action = "none"
        if self.rb_val_nan.isChecked():
            action = "nan"
        elif self.rb_val_flag.isChecked():
            action = "flag"

        self._set_processing(True, "validate")
        self._worker = TransformWorker(self._compute_validate, col, pattern, action)
        self._worker.finished_ok.connect(self._on_validate_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _compute_validate(self, col: str, pattern: str, action: str):
        """Compute validation action (runs in worker thread)."""
        result = validate_column(self._df, col, pattern)
        if action == "nan":
            return validate_replace_failures_with_nan(self._df, col, result["fail_index"])
        elif action == "flag":
            return validate_flag_column(self._df, col, pattern)
        return self._df

    def _on_validate_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful validation action."""
        self.bridge.set_df(new_df)
        col = self.cmb_val_col.currentText()
        if self.rb_val_none.isChecked():
            action_text = f"Validated '{col}' — no changes applied"
        elif self.rb_val_nan.isChecked():
            action_text = "Set invalid values to NaN"
        else:
            action_text = f"Added flag column '_valid_{col}'"
        self.operation_applied.emit(f"✅ {action_text}")
        _APPLY_STATES["validate"] = False
        self._set_processing(False, "validate")

    def _run_validation(self) -> None:
        if self._df is None:
            return
        col = self.cmb_val_col.currentText()
        pattern_name = self.cmb_val_pattern.currentText()
        pattern = self.txt_val_custom.text() if pattern_name == "Custom regex…" else VALIDATION_PATTERNS.get(pattern_name, "")
        if not pattern:
            return
        # Guard against the transient empty-string state QComboBox fires
        # from .clear() while set_dataframe() is repopulating the combo
        # (currentTextChanged('') fires before .addItems() runs). Without
        # this, every dataframe refresh -- every rename, remove, or dtype
        # change -- threw an uncaught KeyError here.
        if not col or col not in self._df.columns:
            return
        try:
            result = validate_column(self._df, col, pattern)
            self.lbl_pass.setText(f"✅ Pass: {result['n_pass']:,}")
            self.lbl_fail.setText(f"❌ Fail: {result['n_fail']:,}")
            self.lbl_null.setText(f"⬜ Null: {result['n_null']:,}")
            self.btn_val_apply.setEnabled(result['n_fail'] > 0)
            _APPLY_STATES["validate"] = result['n_fail'] > 0

            # Show failing rows
            fail_rows = result.get("fail_rows", pd.DataFrame())
            self.table_failures.setRowCount(min(len(fail_rows), 20))
            for i, (idx, row) in enumerate(fail_rows.head(20).iterrows()):
                item = QTableWidgetItem(str(row.iloc[0]))
                item.setTextAlignment(Qt.AlignCenter)
                self.table_failures.setItem(i, 0, item)
        except (re.error, KeyError):
            pass

    # -- Slots: String Ops --------------------------------------------------

    def _on_str_op_changed(self, text: str) -> None:
        while self.str_options_layout.count():
            item = self.str_options_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Inputs changed -> any previous preview is stale. Reset the preview
        # label/table and disable Apply so it can never act on parameters
        # different from what the preview showed (the old Streamlit app
        # recomputed the preview live on every widget change).
        self._reset_string_preview()

        if text == OP_TRIM:
            self.str_op_length = QSpinBox()
            self.str_op_length.setRange(1, 1000)
            self.str_op_length.setValue(50)
            self.str_op_length.valueChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(QLabel("Max characters:"))
            self.str_options_layout.addWidget(self.str_op_length)

            self.str_op_side = QComboBox()
            self.str_op_side.addItems(["Left (start)", "Right (end)"])
            self.str_op_side.currentTextChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(self.str_op_side)

        elif text == OP_PAD:
            self.str_op_padlen = QSpinBox()
            self.str_op_padlen.setRange(1, 200)
            self.str_op_padlen.setValue(10)
            self.str_op_padlen.valueChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(QLabel("Target length:"))
            self.str_options_layout.addWidget(self.str_op_padlen)

            self.str_op_padchar = QLineEdit()
            self.str_op_padchar.setText("0")
            self.str_op_padchar.setMaxLength(1)
            self.str_op_padchar.textChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(QLabel("Pad character:"))
            self.str_options_layout.addWidget(self.str_op_padchar)

            self.str_op_padside = QComboBox()
            self.str_op_padside.addItems(["Left", "Right"])
            self.str_op_padside.currentTextChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(self.str_op_padside)

        elif text == OP_SUBSTRING:
            self.str_op_start = QSpinBox()
            self.str_op_start.setRange(0, 999)
            self.str_op_start.setValue(0)
            self.str_op_start.valueChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(QLabel("Start (0-based):"))
            self.str_options_layout.addWidget(self.str_op_start)

            self.str_op_end = QSpinBox()
            self.str_op_end.setRange(0, 999)
            self.str_op_end.setValue(0)
            self.str_op_end.valueChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(QLabel("End (0 = end of string):"))
            self.str_options_layout.addWidget(self.str_op_end)

        elif text == OP_SPLIT:
            self.str_op_delim = QLineEdit()
            self.str_op_delim.setText(",")
            self.str_op_delim.textChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(QLabel("Delimiter:"))
            self.str_options_layout.addWidget(self.str_op_delim)

            self.str_op_part = QComboBox()
            self.str_op_part.addItems(["First part", "Second part"])
            self.str_op_part.currentTextChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(self.str_op_part)

            self.str_op_newcol = QLineEdit()
            self.str_op_newcol.setPlaceholderText("New column name")
            self.str_op_newcol.textChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(self.str_op_newcol)

        elif text == OP_CONCAT:
            self.str_op_col2 = QComboBox()
            if self._df is not None and not self._df.empty:
                self.str_op_col2.addItems(self._df.columns.tolist())
            self.str_op_col2.currentTextChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(QLabel("Second column:"))
            self.str_options_layout.addWidget(self.str_op_col2)

            self.str_op_sep = QLineEdit()
            self.str_op_sep.setText(" ")
            self.str_op_sep.textChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(QLabel("Separator:"))
            self.str_options_layout.addWidget(self.str_op_sep)

            self.str_op_concatcol = QLineEdit()
            self.str_op_concatcol.setPlaceholderText("New column name")
            self.str_op_concatcol.textChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(self.str_op_concatcol)

        elif text == OP_REGEX_EXTRACT:
            self.str_op_re = QLineEdit()
            self.str_op_re.setPlaceholderText(r"e.g. (\d{4}) to extract 4-digit year")
            self.str_op_re.textChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(self.str_op_re)

            self.str_op_recol = QLineEdit()
            self.str_op_recol.setPlaceholderText("New column name")
            self.str_op_recol.textChanged.connect(self._reset_string_preview)
            self.str_options_layout.addWidget(self.str_op_recol)

        # Cap the freshly built dynamic option widgets too.
        cap_input_widths(self.tabs)

    def _on_str_preview(self) -> None:
        if self._df is None:
            return
        col = self.cmb_str_col.currentText()
        op = self.cmb_str_op.currentText()
        series = self._df[col].astype(str)
        new_series = series.copy()
        new_col_name = None

        try:
            if op == OP_TRIM:
                length = self.str_op_length.value()
                side = self.str_op_side.currentText()
                new_series = series.str[:length] if "Left" in side else series.str[-length:]

            elif op == OP_PAD:
                length = self.str_op_padlen.value()
                char = self.str_op_padchar.text()
                side = self.str_op_padside.currentText()
                if side == "Left" and char == "0":
                    new_series = series.str.zfill(length)
                elif side == "Right":
                    new_series = series.str.ljust(length, char)
                else:
                    new_series = series.str.rjust(length, char)

            elif op == OP_SUBSTRING:
                start = self.str_op_start.value()
                end = self.str_op_end.value()
                new_series = series.str[start: end if end > 0 else None]

            elif op == OP_SPLIT:
                delim = self.str_op_delim.text()
                part_idx = 0 if "First" in self.str_op_part.currentText() else 1
                new_series = series.str.split(delim, n=1, expand=False).apply(
                    lambda x: x[part_idx] if isinstance(x, list) and len(x) > part_idx else np.nan
                )
                new_col_name = self.str_op_newcol.text().strip()

            elif op == OP_CONCAT:
                col2 = self.str_op_col2.currentText()
                sep = self.str_op_sep.text()
                new_series = series.str.cat(self._df[col2].astype(str), sep=sep)
                new_col_name = self.str_op_concatcol.text().strip()

            elif op == OP_REGEX_EXTRACT:
                pattern = self.str_op_re.text().strip()
                new_series = series.str.extract(f"({pattern})", expand=False)
                new_col_name = self.str_op_recol.text().strip()

            n_changed = count_changed_values(series, new_series)
            self.lbl_str_preview.setText(f"{n_changed:,} values will change → {new_col_name or col}")
            self._fill_preview_table(self.table_str_preview, series.head(20).astype(str), new_series.head(20).astype(str))
            self.btn_str_apply.setEnabled(n_changed > 0)
            _APPLY_STATES["string"] = n_changed > 0
        except Exception as exc:
            QMessageBox.critical(self, "Preview Error", str(exc))

    def _on_str_apply(self) -> None:
        if self._df is None:
            return
        col = self.cmb_str_col.currentText()
        op = self.cmb_str_op.currentText()
        self._set_processing(True, "string")
        self._worker = TransformWorker(self._compute_str_op, col, op)
        self._worker.finished_ok.connect(self._on_str_op_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _compute_str_op(self, col: str, op: str):
        """Compute string operation (runs in worker thread)."""
        series = self._df[col].astype(str)
        new_series = series.copy()
        new_col_name = None

        if op == OP_TRIM:
            length = self.str_op_length.value()
            side = self.str_op_side.currentText()
            new_series = series.str[:length] if "Left" in side else series.str[-length:]
        elif op == OP_PAD:
            length = self.str_op_padlen.value()
            char = self.str_op_padchar.text()
            side = self.str_op_padside.currentText()
            if side == "Left" and char == "0":
                new_series = series.str.zfill(length)
            elif side == "Right":
                new_series = series.str.ljust(length, char)
            else:
                new_series = series.str.rjust(length, char)
        elif op == OP_SUBSTRING:
            start = self.str_op_start.value()
            end = self.str_op_end.value()
            new_series = series.str[start: end if end > 0 else None]
        elif op == OP_SPLIT:
            delim = self.str_op_delim.text()
            part_idx = 0 if "First" in self.str_op_part.currentText() else 1
            new_series = series.str.split(delim, n=1, expand=False).apply(
                lambda x: x[part_idx] if isinstance(x, list) and len(x) > part_idx else np.nan
            )
            new_col_name = self.str_op_newcol.text().strip()
        elif op == OP_CONCAT:
            col2 = self.str_op_col2.currentText()
            sep = self.str_op_sep.text()
            new_series = series.str.cat(self._df[col2].astype(str), sep=sep)
            new_col_name = self.str_op_concatcol.text().strip()
        elif op == OP_REGEX_EXTRACT:
            pattern = self.str_op_re.text().strip()
            new_series = series.str.extract(f"({pattern})", expand=False)
            new_col_name = self.str_op_recol.text().strip()

        return new_series, new_col_name, col

    def _on_str_op_finished(self, result_tuple) -> None:
        """Handle successful string operation."""
        new_series, new_col_name, col = result_tuple
        if new_col_name:
            new_df = self._df.copy()
            new_df[new_col_name] = new_series
            self.bridge.set_df(new_df)
            self.operation_applied.emit(f"✅ Created column '{new_col_name}'")
        else:
            new_df = apply_column_update(self._df, col, new_series)
            self.bridge.set_df(new_df)
            self.operation_applied.emit(f"✅ Applied to '{col}'")
        self.btn_str_apply.setEnabled(False)
        _APPLY_STATES["string"] = False
        self._set_processing(False, "string")

    # -- Slots: Numeric Clean -----------------------------------------------

    def _numeric_op_key(self, display_name: str) -> str:
        """Map a combo display name to the backend 'numeric_clean' op key."""
        return NUMERIC_CLEAN_OP_KEYS.get(display_name, "")

    def _on_num_op_changed(self, text: str) -> None:
        while self.num_options_layout.count():
            item = self.num_options_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Inputs changed -> any previous preview is stale (Issue 11/14: the
        # Apply button must never act on parameters the user never previewed).
        self._reset_numeric_preview()

        if "Clamp" in text:
            self.num_min = QDoubleSpinBox()
            self.num_min.setRange(-1e9, 1e9)
            self.num_min.valueChanged.connect(self._reset_numeric_preview)
            self.num_options_layout.addWidget(QLabel("Min:"))
            self.num_options_layout.addWidget(self.num_min)
            self.num_max = QDoubleSpinBox()
            self.num_max.setRange(-1e9, 1e9)
            self.num_max.valueChanged.connect(self._reset_numeric_preview)
            self.num_options_layout.addWidget(QLabel("Max:"))
            self.num_options_layout.addWidget(self.num_max)
        elif "outliers" in text:
            self.num_iqr = QDoubleSpinBox()
            self.num_iqr.setRange(1.0, 3.0)
            self.num_iqr.setSingleStep(0.1)
            self.num_iqr.setValue(1.5)
            self.num_iqr.valueChanged.connect(self._reset_numeric_preview)
            self.num_options_layout.addWidget(QLabel("IQR multiplier:"))
            self.num_options_layout.addWidget(self.num_iqr)
        elif "Round" in text:
            self.num_decimals = QSpinBox()
            self.num_decimals.setRange(0, 10)
            self.num_decimals.setValue(2)
            self.num_decimals.valueChanged.connect(self._reset_numeric_preview)
            self.num_options_layout.addWidget(QLabel("Decimal places:"))
            self.num_options_layout.addWidget(self.num_decimals)
        elif "custom value" in text:
            self.num_fill = QDoubleSpinBox()
            self.num_fill.setRange(-1e9, 1e9)
            self.num_fill.setValue(0.0)
            self.num_fill.valueChanged.connect(self._reset_numeric_preview)
            self.num_options_layout.addWidget(QLabel("Fill value:"))
            self.num_options_layout.addWidget(self.num_fill)

        # Cap the freshly built dynamic option widgets too.
        cap_input_widths(self.tabs)

    def _on_num_preview(self) -> None:
        if self._df is None:
            return
        col = self.cmb_num_col.currentText()
        op = self.cmb_num_op.currentText()
        kwargs = {}
        if "Clamp" in op:
            kwargs["min"] = self.num_min.value()
            kwargs["max"] = self.num_max.value()
        elif "outliers" in op:
            kwargs["iqr_multiplier"] = self.num_iqr.value()
        elif "Round" in op:
            kwargs["decimals"] = self.num_decimals.value()
        elif "custom value" in op:
            kwargs["value"] = self.num_fill.value()

        op_key = self._numeric_op_key(op)
        if not op_key:
            QMessageBox.critical(self, "Preview Error",
                                 f"Unsupported numeric operation: {op}")
            return

        try:
            series = pd.to_numeric(self._df[col], errors="coerce")
            result = numeric_clean(series, op_key, **kwargs)
            n_changed = count_changed_values(series.round(8), result.round(8))
            self.lbl_num_preview.setText(f"{n_changed:,} values will change")
            self._fill_preview_table(self.table_num_preview,
                                 series.head(20).astype(str),
                                 result.head(20).round(4).astype(str))
            self.btn_num_apply.setEnabled(n_changed > 0)
            _APPLY_STATES["numeric"] = n_changed > 0
        except Exception as exc:
            QMessageBox.critical(self, "Preview Error", str(exc))

    def _on_num_apply(self) -> None:
        if self._df is None:
            return
        col = self.cmb_num_col.currentText()
        op = self.cmb_num_op.currentText()
        kwargs = {}
        if "Clamp" in op:
            kwargs["min"] = self.num_min.value()
            kwargs["max"] = self.num_max.value()
        elif "outliers" in op:
            kwargs["iqr_multiplier"] = self.num_iqr.value()
        elif "Round" in op:
            kwargs["decimals"] = self.num_decimals.value()
        elif "custom value" in op:
            kwargs["value"] = self.num_fill.value()

        op_key = self._numeric_op_key(op)
        if not op_key:
            return
        self._set_processing(True, "numeric")
        self._worker = TransformWorker(self._compute_numeric_clean, col, op_key, kwargs)
        self._worker.finished_ok.connect(self._on_numeric_clean_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _compute_numeric_clean(self, col: str, op_key: str, kwargs: dict):
        """Compute numeric clean (runs in worker thread)."""
        series = pd.to_numeric(self._df[col], errors="coerce")
        result = numeric_clean(series, op_key, **kwargs)
        return apply_column_update(self._df, col, result)

    def _on_numeric_clean_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful numeric clean."""
        self.bridge.set_df(new_df)
        self.operation_applied.emit(f"✅ Numeric clean applied to '{self.cmb_num_col.currentText()}'")
        self.btn_num_apply.setEnabled(False)
        _APPLY_STATES["numeric"] = False
        self._set_processing(False, "numeric")

    # -- Helpers ------------------------------------------------------------

    def _reset_string_preview(self, *_args) -> None:
        """Clear the String-Ops preview so Apply can never run on stale inputs.

        Connected to the operation combo, the source-column combo and every
        dynamic option widget. After any input change the user must run a
        fresh Preview, mirroring the old Streamlit live-preview behaviour
        where the shown preview always reflected the current parameters.
        """
        self.lbl_str_preview.setText("")
        self.table_str_preview.setRowCount(0)
        self.btn_str_apply.setEnabled(False)
        _APPLY_STATES["string"] = False

    def _reset_numeric_preview(self, *_args) -> None:
        """Clear the Numeric Clean preview (same rationale as String Ops)."""
        self.lbl_num_preview.setText("")
        self.table_num_preview.setRowCount(0)
        self.btn_num_apply.setEnabled(False)
        _APPLY_STATES["numeric"] = False

    def _fill_preview_table(self, table: QTableWidget, before: pd.Series, after: pd.Series) -> None:
        """Fill a preview table with before/after values (centered cells)."""
        table.setRowCount(min(len(before), 20))
        for i in range(min(len(before), 20)):
            for j, val in enumerate((str(before.iloc[i]), str(after.iloc[i]))):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(i, j, item)

    # -- Worker helpers ------------------------------------------------------

    def _set_processing(self, processing: bool, tab: str = "") -> None:
        """Enable/disable UI during background processing.

        During processing all apply buttons and the tabs are disabled.
        When re-enabling (processing=False) each apply button is restored
        to its individual "preview just ran" state rather than being
        blindly re-enabled, so a tab the user never previewed stays
        disabled instead of becoming unexpectedly enabled.
        """
        if processing:
            self.tabs.setEnabled(False)
            self.btn_text_apply.setEnabled(False)
            self.btn_find_apply.setEnabled(False)
            self.btn_val_apply.setEnabled(False)
            self.btn_str_apply.setEnabled(False)
            self.btn_num_apply.setEnabled(False)
            return

        self.btn_text_apply.setEnabled(_APPLY_STATES["text"])
        self.btn_find_apply.setEnabled(_APPLY_STATES["find"])
        self.btn_val_apply.setEnabled(_APPLY_STATES["validate"])
        self.btn_str_apply.setEnabled(_APPLY_STATES["string"])
        self.btn_num_apply.setEnabled(_APPLY_STATES["numeric"])
        self.tabs.setEnabled(True)

    def _on_worker_failed(self, error: str) -> None:
        """Handle worker failure."""
        self._set_processing(False)
        QMessageBox.critical(self, "Operation Failed", error)
        log.error("Worker failed: %s", error)


# Validation patterns live in services.transform_service.VALIDATION_PATTERNS
# (single source of truth, imported at the top of this module). The panel no
# longer keeps its own copy so a label/pattern can never drift between the UI
# and the backend.