"""desktop/pages/dtype_transformer_panel.py -- Data type transformer widget.

Replaces the Streamlit ``show_dtype_transformer()`` with a native Qt panel that:
  - Shows current dtypes for all columns
  - Allows selecting a column and target dtype
  - Preview conversion with success rate and sample before/after
  - Apply transformation
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFrame,
)

from services.transform_service import preview_dtype_conversion, apply_dtype_conversion
from desktop.workers import TransformWorker

log = logging.getLogger(__name__)


class DtypeTransformerPanel(QWidget):
    """Data type transformer panel.

    Emits ``operation_applied`` when a dtype conversion succeeds.
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
        root.setSpacing(12)

        # -- Header --
        header = QLabel("🔍 Data Type Inspector & Transformer")
        header.setStyleSheet("font-size: 16px; font-weight: 700; color: #e2e8f0;")
        root.addWidget(header)

        # -- Current dtypes table --
        self.table_dtypes = QTableWidget()
        self.table_dtypes.setColumnCount(3)
        self.table_dtypes.setHorizontalHeaderLabels(["Column", "Current Dtype", "Sample Value"])
        self.table_dtypes.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_dtypes.setStyleSheet(
            "QTableWidget { background: #1e293b; color: #e2e8f0; gridline-color: #334155; }"
        )
        self.table_dtypes.setMaximumHeight(200)
        self.table_dtypes.setMinimumHeight(80)
        root.addWidget(self.table_dtypes)

        # -- Transform controls --
        controls = QFrame()
        controls.setStyleSheet(
            "QFrame { background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; }"
        )
        ctrl_layout = QVBoxLayout(controls)
        ctrl_layout.setContentsMargins(12, 12, 12, 12)
        ctrl_layout.setSpacing(8)

        ctrl_layout.addWidget(QLabel("Select column to transform:"))
        self.cmb_col = QComboBox()
        self.cmb_col.setStyleSheet(
            "QComboBox { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; }"
        )
        ctrl_layout.addWidget(self.cmb_col)

        ctrl_layout.addWidget(QLabel("Convert to:"))
        self.cmb_target_dtype = QComboBox()
        self.cmb_target_dtype.addItems([
            "object", "string", "int64", "float64", "bool", "category",
            "datetime64[ns]", "date", "time", "timedelta64[ns]",
        ])
        self.cmb_target_dtype.setStyleSheet(
            "QComboBox { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; }"
        )
        ctrl_layout.addWidget(self.cmb_target_dtype)

        # Preview result area
        preview_header_row = QHBoxLayout()
        self.lbl_preview_title = QLabel("")
        self.lbl_preview_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #e2e8f0;")
        self.lbl_preview_title.setVisible(False)
        preview_header_row.addWidget(self.lbl_preview_title)
        preview_header_row.addStretch()
        # A close control so a large preview doesn't permanently squeeze the
        # panel -- the user can dismiss the result without losing anything.
        self.btn_clear_preview = QPushButton("✖ Clear Preview")
        self.btn_clear_preview.setVisible(False)
        self.btn_clear_preview.setStyleSheet(
            "QPushButton { background: #1e293b; color: #94a3b8; border: 1px solid #334155;"
            "  border-radius: 6px; padding: 4px 12px; font-size: 11px; }"
            "QPushButton:hover { background: #334155; color: #e2e8f0; }"
        )
        preview_header_row.addWidget(self.btn_clear_preview)
        ctrl_layout.addLayout(preview_header_row)

        self.lbl_preview_stats = QLabel("")
        self.lbl_preview_stats.setWordWrap(True)
        self.lbl_preview_stats.setStyleSheet("font-size: 11px; color: #94a3b8;")
        self.lbl_preview_stats.setVisible(False)
        ctrl_layout.addWidget(self.lbl_preview_stats)

        self.table_preview = QTableWidget()
        self.table_preview.setColumnCount(2)
        self.table_preview.setHorizontalHeaderLabels(["Before", "After"])
        self.table_preview.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_preview.setStyleSheet(
            "QTableWidget { background: #1e293b; color: #e2e8f0; gridline-color: #334155; }"
        )
        # Explicit min AND max so the preview can never balloon the whole
        # pane -- it scrolls internally instead of forcing the transform
        # controls to shrink out of usability.
        self.table_preview.setMinimumHeight(80)
        self.table_preview.setMaximumHeight(180)
        self.table_preview.setVisible(False)
        ctrl_layout.addWidget(self.table_preview)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_preview = QPushButton("🔎 Preview Conversion")
        self.btn_preview.setStyleSheet(
            "QPushButton { background: #4f46e5; color: white; border: none;"
            "  border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
            "QPushButton:disabled { background: #334155; color: #64748b; }"
        )
        self.btn_apply = QPushButton("🔄 Apply Transformation")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setStyleSheet(
            "QPushButton { background: #10b981; color: white; border: none;"
            "  border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
            "QPushButton:disabled { background: #334155; color: #64748b; }"
        )
        btn_row.addWidget(self.btn_preview)
        btn_row.addWidget(self.btn_apply)
        ctrl_layout.addLayout(btn_row)

        root.addWidget(controls)

        # -- Signal connections --
        self.btn_preview.clicked.connect(self._on_preview)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_clear_preview.clicked.connect(self._clear_preview)
        self.cmb_col.currentTextChanged.connect(self._on_col_changed)

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        pass

    # -- Public API ---------------------------------------------------------

    def set_dataframe(self, df: pd.DataFrame) -> None:
        """Load a DataFrame and populate dtype table."""
        self._df = df
        if df is None or df.empty:
            self.table_dtypes.setRowCount(0)
            self.cmb_col.clear()
            return

        # Populate dtype table
        self.table_dtypes.setRowCount(len(df.columns))
        for i, col in enumerate(df.columns):
            self.table_dtypes.setItem(i, 0, QTableWidgetItem(col))
            dtype_str = str(df[col].dtype)
            self.table_dtypes.setItem(i, 1, QTableWidgetItem(dtype_str))
            sample = str(df[col].iloc[0]) if len(df) > 0 else ""
            self.table_dtypes.setItem(i, 2, QTableWidgetItem(sample))

        # Populate column dropdown
        self.cmb_col.clear()
        self.cmb_col.addItems(df.columns.tolist())

    # -- Slots --------------------------------------------------------------

    def _on_col_changed(self, text: str) -> None:
        """Update target dtype default when column changes."""
        if self._df is None or not text:
            return
        current_dtype = str(self._df[text].dtype)
        # Try to set a sensible default target
        if "int" in current_dtype:
            idx = self.cmb_target_dtype.findText("int64")
        elif "float" in current_dtype:
            idx = self.cmb_target_dtype.findText("float64")
        elif "datetime" in current_dtype:
            idx = self.cmb_target_dtype.findText("datetime64[ns]")
        elif "bool" in current_dtype:
            idx = self.cmb_target_dtype.findText("bool")
        elif "category" in current_dtype:
            idx = self.cmb_target_dtype.findText("category")
        else:
            idx = self.cmb_target_dtype.findText("string")
        if idx >= 0:
            self.cmb_target_dtype.setCurrentIndex(idx)

    def _clear_preview(self) -> None:
        """Dismiss the preview result, returning the panel to a compact state."""
        for widget in (self.lbl_preview_title, self.lbl_preview_stats, self.table_preview,
                       self.btn_clear_preview):
            widget.setVisible(False)
        self.btn_apply.setEnabled(False)

    def _nonsense_conversion(self, current_dtype: str, target: str) -> str:
        """Return a warning string if the requested conversion is nonsensical.

        Converting time/datetime/timedelta values into numeric-targets
        produces garbage silently (e.g. a naive ``astype(float)`` of a
        datetime object), so we pre-screen and refuse with a clear message.
        """
        source_is_temporal = any(k in current_dtype
                                 for k in ("datetime", "timedelta", "time"))
        if source_is_temporal and target in ("int64", "float64"):
            return (
                f"Converting {current_dtype} to {target} is not meaningful. "
                "Use 'datetime64[ns]' → 'date'/'time'/'timedelta64[ns]', or "
                "extract a part first."
            )
        return ""

    def _on_preview(self) -> None:
        """Preview the dtype conversion."""
        if self._df is None:
            return
        col = self.cmb_col.currentText()
        target = self.cmb_target_dtype.currentText()
        if not col or not target:
            return

        try:
            # Refuse nonsensical temporal → numeric conversions up front so
            # the preview can't silently suggest garbage values.
            guard = self._nonsense_conversion(str(self._df[col].dtype), target)
            if guard:
                QMessageBox.warning(self, "Unsupported Conversion", guard)
                self._clear_preview()
                return

            result = preview_dtype_conversion(self._df[col], target)
            if "error" in result:
                QMessageBox.warning(self, "Preview Error", result["error"])
                return

            pct = result["pct"]
            new_nulls = result["new_nulls"]
            total = result["total"]
            success = result["success"]

            # Update preview labels
            self.lbl_preview_title.setVisible(True)
            if pct == 100:
                color = "#10b981"
                icon = "✅"
            elif pct >= 80:
                color = "#f59e0b"
                icon = "⚠️"
            else:
                color = "#ef4444"
                icon = "❌"

            self.lbl_preview_title.setText(
                f"{icon} {pct}% success rate — {success:,} of {total:,} values will convert cleanly"
            )
            self.lbl_preview_title.setStyleSheet(
                f"font-size: 13px; font-weight: 600; color: {color};"
            )

            null_msg = (
                f"⚠️ {new_nulls:,} value(s) will become null (unconvertible → NaN)"
                if new_nulls
                else f"No new null values will be introduced."
            )
            self.lbl_preview_stats.setText(
                f"{null_msg}\nResult dtype: {result['dtype_after']}"
            )
            self.lbl_preview_stats.setVisible(True)

            # Fill preview table
            before_vals = result["sample_df"]["Before"].head(10)
            after_vals = result["sample_df"]["After"].head(10)
            self.table_preview.setRowCount(len(before_vals))
            for i in range(len(before_vals)):
                self.table_preview.setItem(i, 0, QTableWidgetItem(str(before_vals.iloc[i])))
                self.table_preview.setItem(i, 1, QTableWidgetItem(str(after_vals.iloc[i])))
            self.table_preview.setVisible(True)
            self.btn_clear_preview.setVisible(True)

            # Allow applying after ANY successful preview -- even a lossy one
            # (pct < 100). Previously Apply was gated on pct > 0, which left
            # it permanently disabled for wholly-lossy-but-valid conversions
            # like time → float64. We warn about data loss instead of
            # blocking the user.
            self.btn_apply.setEnabled(True)
            if new_nulls > 0:
                self.lbl_preview_stats.setText(
                    self.lbl_preview_stats.text()
                    + "\n\nApply will permanently replace the flagged values "
                      "with null."
                )

        except Exception as exc:
            QMessageBox.critical(self, "Preview Error", str(exc))
            log.error("Preview conversion failed: %s", exc)

    def _on_apply(self) -> None:
        """Apply the dtype conversion in a background thread."""
        if self._df is None:
            return
        col = self.cmb_col.currentText()
        target = self.cmb_target_dtype.currentText()
        if not col or not target:
            return

        self.btn_apply.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self._worker = TransformWorker(apply_dtype_conversion, self._df, col, target)
        self._worker.finished_ok.connect(self._on_apply_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _on_apply_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful dtype conversion."""
        self.bridge.set_df(new_df)
        self.operation_applied.emit(f"✅ Converted '{self.cmb_col.currentText()}' to {self.cmb_target_dtype.currentText()}")
        self.btn_apply.setEnabled(False)
        self.btn_preview.setEnabled(True)
        self.set_dataframe(new_df)

    def _on_worker_failed(self, error: str) -> None:
        """Handle worker failure."""
        self.btn_apply.setEnabled(True)
        self.btn_preview.setEnabled(True)
        QMessageBox.critical(self, "Conversion Failed", error)
        log.error("Worker failed: %s", error)