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
    QMessageBox, QFrame, QSizePolicy,
)

from services.transform_service import preview_dtype_conversion, apply_dtype_conversion
from desktop.workers import TransformWorker
from desktop.widgets.table_index_header import auto_fit_index_header
from desktop.widgets.resizable_table import apply_table_defaults, fit_columns_to_content

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
        header.setProperty("class", "sectionTitle")
        root.addWidget(header)

        # -- Current dtypes table --
        self.table_dtypes = QTableWidget()
        self.table_dtypes.setColumnCount(3)
        self.table_dtypes.setHorizontalHeaderLabels(["Column", "Current Dtype", "Sample Value"])
        self.table_dtypes.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # Table styling handled by global _tables_qss
        # The dtypes table is this panel's main content: NO max-height cap.
        # It sits in the body row (stretch=3, below) so it covers ALL of the
        # panel's leftover space instead of leaving dead gaps (UI issues:
        # "table should cover the whole area" / "taking unnecessary space").
        # The panel lives inside a resizable QScrollArea (upload_page), so a
        # tall table can never inflate the splitter pane -- it simply scrolls.
        self.table_dtypes.setMinimumHeight(120)
        # Roomier, centered row-index gutter (UI issue: index numbers were
        # cramped against the first column) and centered cell values.
        # Sized to the rows actually displayed rather than a hard-coded 36px
        # (too wide for a 10-row preview, too narrow -- clipping the numbers
        # -- for a 4+ digit index). See desktop/widgets/table_index_header.py.
        auto_fit_index_header(self.table_dtypes)

        # -- Body: wide dtypes table (left) + transform controls (right) --
        # UI issue: the full-width stacked layout wasted horizontal space
        # ("organise this section with left table & right side drop-down
        # selections"). The table keeps most of the width; the column/
        # target drop-downs live in a compact side panel on the right.
        body = QHBoxLayout()
        body.setSpacing(12)
        self._body_layout = body
        body.addWidget(self.table_dtypes, 3)

        # -- Transform controls (right-hand side panel) --
        side_frame = QFrame()
        side_frame.setObjectName("config_panel")
        side_layout = QVBoxLayout(side_frame)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(8)
        side_frame.setMaximumWidth(460)

        side_layout.addWidget(QLabel("Select column to transform:"))
        self.cmb_col = QComboBox()
        side_layout.addWidget(self.cmb_col)

        side_layout.addWidget(QLabel("Convert to:"))
        self.cmb_target_dtype = QComboBox()
        self.cmb_target_dtype.addItems([
            "object", "string", "int64", "float64", "bool", "category",
            "datetime64[ns]", "date", "time", "timedelta64[ns]",
        ])
        # Combo styling handled by global QSS
        side_layout.addWidget(self.cmb_target_dtype)
        side_layout.addStretch(1)
        body.addWidget(side_frame)
        root.addLayout(body, 1)

        # -- Preview result area (spans the full width below the body) --
        controls = QFrame()
        controls.setObjectName("config_panel")
        ctrl_layout = QVBoxLayout(controls)
        ctrl_layout.setContentsMargins(12, 12, 12, 12)
        ctrl_layout.setSpacing(8)

        # Keep the preview area compact: the body row above takes stretch=1,
        # so leftover vertical space never gets smeared into inflated gaps
        # between these rows.
        controls.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        # Preview result area
        preview_header_row = QHBoxLayout()
        self.lbl_preview_title = QLabel("")
        self.lbl_preview_title.setProperty("class", "cardSub")
        self.lbl_preview_title.setVisible(False)
        preview_header_row.addWidget(self.lbl_preview_title)
        preview_header_row.addStretch()
        # A close control so a large preview doesn't permanently squeeze the
        # panel -- the user can dismiss the result without losing anything.
        self.btn_clear_preview = QPushButton("✖ Clear Preview")
        self.btn_clear_preview.setVisible(False)
        # Highlighted accent style so the user can spot the dismiss control
        # at a glance (UI issue: the old dark-grey style was nearly invisible
        # against the panel background).
        self.btn_clear_preview.setProperty("kind", "primary")
        preview_header_row.addWidget(self.btn_clear_preview)
        ctrl_layout.addLayout(preview_header_row)

        self.lbl_preview_stats = QLabel("")
        self.lbl_preview_stats.setWordWrap(True)
        self.lbl_preview_stats.setProperty("class", "faint")
        self.lbl_preview_stats.setStyleSheet("font-size: 11px;")
        self.lbl_preview_stats.setVisible(False)
        ctrl_layout.addWidget(self.lbl_preview_stats)

        self.table_preview = QTableWidget()
        self.table_preview.setColumnCount(2)
        self.table_preview.setHorizontalHeaderLabels(["Before", "After"])
        # Readable, scrollable columns instead of Stretch (see resizable_table.py).
        apply_table_defaults(self.table_preview)
        # Explicit min AND max so the preview can never balloon the whole
        # pane -- it scrolls internally instead of forcing the transform
        # controls to shrink out of usability.
        self.table_preview.setMinimumHeight(80)
        self.table_preview.setMaximumHeight(180)
        # Row-index gutter sized to the rows actually shown (the old fixed
        # 36px clipped a 4+ digit index). See table_index_header.py.
        auto_fit_index_header(self.table_preview)
        self.table_preview.setVisible(False)
        ctrl_layout.addWidget(self.table_preview)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_preview = QPushButton("🔎 Preview Conversion")
        self.btn_preview.setProperty("kind", "primary")
        self.btn_apply = QPushButton("🔄 Apply Transformation")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setProperty("kind", "success")
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
        # Changing the target dtype invalidates any shown preview (it was
        # computed for the previous target), so dismiss it.
        self.cmb_target_dtype.currentTextChanged.connect(self._on_target_changed)

    def _on_target_changed(self, _text: str) -> None:
        """Stale-preview guard for the 'Convert to' combo."""
        self._clear_preview()

    # -- Public API ---------------------------------------------------------

    def set_dataframe(self, df: pd.DataFrame) -> None:
        """Load a DataFrame and populate dtype table."""
        self._df = df
        if df is None or df.empty:
            self.table_dtypes.setRowCount(0)
            self.cmb_col.clear()
            return

        # Populate dtype table (values centered per UI feedback)
        self.table_dtypes.setRowCount(len(df.columns))
        for i, col in enumerate(df.columns):
            values = (col, str(df[col].dtype),
                      str(df[col].iloc[0]) if len(df) > 0 else "")
            for j, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self.table_dtypes.setItem(i, j, item)

        # Populate column dropdown
        self.cmb_col.clear()
        self.cmb_col.addItems(df.columns.tolist())

    # -- Slots --------------------------------------------------------------

    def _on_col_changed(self, text: str) -> None:
        """Update target dtype default when column changes."""
        # Stale preview guard: the old banner/before-after table described a
        # different column -- drop it so it can't be mistaken for the
        # current selection's result.
        self._clear_preview()
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
                for j, val in enumerate((str(before_vals.iloc[i]),
                                         str(after_vals.iloc[i]))):
                    item = QTableWidgetItem(val)
                    item.setTextAlignment(Qt.AlignCenter)
                    self.table_preview.setItem(i, j, item)
            self.table_preview.setVisible(True)
            self.btn_clear_preview.setVisible(True)
            # Preview content changed -> fit the columns to their (capped)
            # content instead of squeezing them. See resizable_table.py.
            fit_columns_to_content(self.table_preview)

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
        # Capture the selection NOW: when the worker finishes, set_df() makes
        # the page re-sync this panel (repopulating the combos), so reading
        # them in _on_apply_finished would report the wrong column/target.
        self._pending_col, self._pending_target = col, target
        self._worker = TransformWorker(apply_dtype_conversion, self._df, col, target)
        self._worker.finished_ok.connect(self._on_apply_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _on_apply_finished(self, new_df: pd.DataFrame) -> None:
        """Handle successful dtype conversion."""
        self.bridge.set_df(new_df)
        self.operation_applied.emit(
            f"✅ Converted '{getattr(self, '_pending_col', '?')}'"
            f" to {getattr(self, '_pending_target', '?')}")
        # The applied result is now the live data (dtype table re-syncs via
        # df_changed); the old preview described pre-conversion values, so
        # dismiss it rather than leaving a stale before/after on screen.
        self._clear_preview()
        self.btn_apply.setEnabled(False)
        self.btn_preview.setEnabled(True)
        self.set_dataframe(new_df)

    def _on_worker_failed(self, error: str) -> None:
        """Handle worker failure."""
        self.btn_apply.setEnabled(True)
        self.btn_preview.setEnabled(True)
        QMessageBox.critical(self, "Conversion Failed", error)
        log.error("Worker failed: %s", error)