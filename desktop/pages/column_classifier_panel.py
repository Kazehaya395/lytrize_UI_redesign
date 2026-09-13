"""desktop/pages/column_classifier_panel.py -- Column classification widget.

Replaces the Streamlit ``show_column_classifier()`` with a native Qt panel that:
  - Auto-detects numeric / categorical / datetime columns
  - Allows manual override via multi-select lists
  - Real drag & drop between the three lists (buttons kept as fallback)
  - Detects overlap warnings
  - Emits a signal when user confirms classification
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal, QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox, QFrame,
)

from services.transform_service import classify_columns

log = logging.getLogger(__name__)


class _ClassifyList(QListWidget):
    """A list that can both originate and accept drag & drop moves.

    Dragging a selection from any of the three category lists onto another
    list moves those items into the target category. Multi-selected items
    move together. The move is visually identical to the fallback buttons:
    items leave the source list and are appended to the target list.
    """

    moved = Signal()

    def __init__(self, owner, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._owner = owner
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.MultiSelection)
        self.setDefaultDropAction(Qt.MoveAction)
        # Keep the widget from collapsing to nothing when empty.
        self.setMinimumHeight(120)

    # -- drag: package all selected item texts --
    def mimeTypes(self):
        return ["application/x-lytrize-columns"]

    def mimeData(self, items) -> QMimeData:
        mime = QMimeData()
        payload = "\x1f".join(it.text() for it in items)
        mime.setData("application/x-lytrize-columns", payload.encode("utf-8"))
        return mime

    # -- drop: move the dragged columns into THIS list --
    def dropEvent(self, event):
        if not event.mimeData().hasFormat("application/x-lytrize-columns"):
            event.ignore()
            return

        # Decode the dragged column names (multi-item).
        payload = bytes(event.mimeData().data("application/x-lytrize-columns"))
        dragged = payload.decode("utf-8").split("\x1f") if payload else []

        if not dragged:
            event.ignore()
            return

        target_names = {self.item(i).text() for i in range(self.count())}
        moved_any = False

        # Remove the dragged columns from whichever source list holds them.
        for source in (self._owner.list_num, self._owner.list_cat, self._owner.list_dt):
            for i in range(source.count() - 1, -1, -1):
                item = source.item(i)
                if item.text() in dragged:
                    source.takeItem(i)
                    if item.text() not in target_names:
                        self.addItem(item)
                        moved_any = True

        if moved_any:
            event.accept()
            self.moved.emit()
        else:
            event.ignore()


class ColumnClassifierPanel(QWidget):
    """Column type classification panel.

    Emits ``classification_confirmed`` when the user clicks the confirm button,
    passing the three column-type lists as a dict.
    """

    classification_confirmed = Signal(dict)  # emits {"numeric": [...], "categorical": [...], "datetime": [...]}

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._df: Optional[pd.DataFrame] = None
        self._auto_num: list[str] = []
        self._auto_cat: list[str] = []
        self._auto_dt: list[str] = []

        self._build_ui()
        self._connect_signals()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # -- Header --
        header = QLabel("🏷️ Column Classification")
        header.setProperty("class", "sectionTitle")
        root.addWidget(header)

        desc = QLabel(
            "Review the auto-detected column types below. "
            "Drag columns between lists to reclassify them, or select one or "
            "more columns and use the buttons below to move them."
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "muted")
        root.addWidget(desc)

        # -- Move-to-category buttons (fallback to drag & drop) --
        move_row = QHBoxLayout()
        move_row.setSpacing(8)
        move_row.addWidget(QLabel("Move selected to:"))
        # Button styling handled by global QSS kind=ghost
        self.btn_move_num = QPushButton("📊 Numeric")
        self.btn_move_num.setProperty("kind", "ghost")
        self.btn_move_num.clicked.connect(lambda: self._move_selected_to("numeric"))
        move_row.addWidget(self.btn_move_num)

        self.btn_move_cat = QPushButton("🏷️ Categorical")
        self.btn_move_cat.setProperty("kind", "ghost")
        self.btn_move_cat.clicked.connect(lambda: self._move_selected_to("categorical"))
        move_row.addWidget(self.btn_move_cat)

        self.btn_move_dt = QPushButton("📅 Date/Time")
        self.btn_move_dt.setProperty("kind", "ghost")
        self.btn_move_dt.clicked.connect(lambda: self._move_selected_to("datetime"))
        move_row.addWidget(self.btn_move_dt)
        move_row.addStretch()
        root.addLayout(move_row)

        # -- Columns: three lists side by side --
        lists_row = QHBoxLayout()
        lists_row.setSpacing(12)

        # Numeric
        num_group = QFrame()
        num_group.setObjectName("config_panel")
        num_layout = QVBoxLayout(num_group)
        num_layout.setContentsMargins(8, 8, 8, 8)
        num_layout.setSpacing(4)
        num_header = QLabel("📊 Metrics Columns (Numeric)")
        num_header.setProperty("class", "cardSub")
        num_layout.addWidget(num_header)
        self.list_num = _ClassifyList(self)
        num_layout.addWidget(self.list_num)
        lists_row.addWidget(num_group, stretch=1)

        # Categorical
        cat_group = QFrame()
        cat_group.setObjectName("config_panel")
        cat_layout = QVBoxLayout(cat_group)
        cat_layout.setContentsMargins(8, 8, 8, 8)
        cat_layout.setSpacing(4)
        cat_header = QLabel("🏷️ Dimension Columns (Categorical)")
        cat_header.setProperty("class", "cardSub")
        cat_layout.addWidget(cat_header)
        self.list_cat = _ClassifyList(self)
        cat_layout.addWidget(self.list_cat)
        lists_row.addWidget(cat_group, stretch=1)

        # Datetime
        dt_group = QFrame()
        dt_group.setObjectName("config_panel")
        dt_layout = QVBoxLayout(dt_group)
        dt_layout.setContentsMargins(8, 8, 8, 8)
        dt_layout.setSpacing(4)
        dt_header = QLabel("📅 Date/Time Columns")
        dt_header.setProperty("class", "cardSub")
        dt_layout.addWidget(dt_header)
        self.list_dt = _ClassifyList(self)
        dt_layout.addWidget(self.list_dt)
        lists_row.addWidget(dt_group, stretch=1)

        root.addLayout(lists_row, stretch=1)

        # -- Overlap warning --
        self.lbl_warning = QLabel("")
        self.lbl_warning.setProperty("class", "warning")
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.setVisible(False)
        root.addWidget(self.lbl_warning)

        # -- Confirm button --
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_confirm = QPushButton("✅ Confirm & Proceed to Analysis →")
        self.btn_confirm.setProperty("kind", "success")
        self.btn_confirm.setEnabled(False)
        btn_row.addWidget(self.btn_confirm)
        root.addLayout(btn_row)

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.list_num.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_cat.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_dt.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_num.moved.connect(self._on_move_done)
        self.list_cat.moved.connect(self._on_move_done)
        self.list_dt.moved.connect(self._on_move_done)
        self.btn_confirm.clicked.connect(self._on_confirm)

    # -- Public API ---------------------------------------------------------

    def set_dataframe(self, df: pd.DataFrame) -> None:
        """Load a DataFrame and auto-detect column types."""
        self._df = df
        if df is None or df.empty:
            self._clear_lists()
            return

        result = classify_columns(df)
        self._auto_num = result.get("numeric", [])
        self._auto_cat = result.get("categorical", [])
        self._auto_dt = result.get("datetime", [])

        self._populate_lists()
        self._check_overlap()

    def get_classification(self) -> dict[str, list[str]]:
        """Return the current column classification from the UI."""
        return {
            "numeric": self._get_selected_items(self.list_num),
            "categorical": self._get_selected_items(self.list_cat),
            "datetime": self._get_selected_items(self.list_dt),
        }

    # -- Slots --------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        self._check_overlap()

    def _on_move_done(self) -> None:
        """After a drag-drop move, refresh overlap + confirm state."""
        self._check_overlap()
        self._update_confirm_state()

    def _on_confirm(self) -> None:
        if not self._df_is_valid():
            return
        classification = self.get_classification()
        self.classification_confirmed.emit(classification)

    # -- Helpers ------------------------------------------------------------

    def _populate_lists(self) -> None:
        """Fill the three list widgets with column names."""
        self.list_num.clear()
        self.list_cat.clear()
        self.list_dt.clear()

        all_cols = self._df.columns.tolist() if self._df is not None else []
        assigned = set()

        for col in self._auto_num:
            if col in all_cols:
                item = QListWidgetItem(col)
                item.setData(Qt.UserRole, col)
                self.list_num.addItem(item)
                assigned.add(col)

        for col in self._auto_cat:
            if col in all_cols:
                item = QListWidgetItem(col)
                item.setData(Qt.UserRole, col)
                self.list_cat.addItem(item)
                assigned.add(col)

        for col in self._auto_dt:
            if col in all_cols:
                item = QListWidgetItem(col)
                item.setData(Qt.UserRole, col)
                self.list_dt.addItem(item)
                assigned.add(col)

        # Any columns not classified go into categorical by default
        for col in all_cols:
            if col not in assigned:
                item = QListWidgetItem(col)
                item.setData(Qt.UserRole, col)
                self.list_cat.addItem(item)

        self._update_confirm_state()

    def _get_selected_items(self, list_widget: QListWidget) -> list[str]:
        """Return all item texts in a list widget."""
        return [list_widget.item(i).text() for i in range(list_widget.count())]

    def _move_selected_to(self, target: str) -> None:
        """Move whichever items are currently multi-selected -- across
        any/all of the three lists -- into the target category list.
        Buttons are a keyboard-accessible fallback to the drag & drop."""
        target_list = {"numeric": self.list_num, "categorical": self.list_cat,
                        "datetime": self.list_dt}[target]
        moved_any = False
        for source_list in (self.list_num, self.list_cat, self.list_dt):
            if source_list is target_list:
                continue
            # Iterate in reverse so takeItem() index shifts don't skip items.
            for i in range(source_list.count() - 1, -1, -1):
                item = source_list.item(i)
                if item.isSelected():
                    source_list.takeItem(i)
                    target_list.addItem(item)
                    moved_any = True
        if moved_any:
            self._check_overlap()
            self._update_confirm_state()

    def _check_overlap(self) -> None:
        """Check for overlapping selections and show/hide warning."""
        num = set(self._get_selected_items(self.list_num))
        cat = set(self._get_selected_items(self.list_cat))
        dt = set(self._get_selected_items(self.list_dt))

        overlaps = []
        if num & cat:
            overlaps.append("Metrics & Dimension")
        if num & dt:
            overlaps.append("Metrics & Date/Time")
        if cat & dt:
            overlaps.append("Dimension & Date/Time")

        if overlaps:
            self.lbl_warning.setText(
                f"⚠️ Overlap detected between: {', '.join(overlaps)}. "
                "Please remove duplicates before confirming."
            )
            self.lbl_warning.setVisible(True)
            self.btn_confirm.setEnabled(False)
        else:
            self.lbl_warning.setVisible(False)
            self._update_confirm_state()

    def _update_confirm_state(self) -> None:
        """Enable confirm button only if no overlap and at least one column in each list."""
        if self.lbl_warning.isVisible():
            self.btn_confirm.setEnabled(False)
            return

        num = self._get_selected_items(self.list_num)
        cat = self._get_selected_items(self.list_cat)
        dt = self._get_selected_items(self.list_dt)

        # Allow empty numeric/datetime lists, but require at least one column overall
        has_any = bool(num or cat or dt)
        self.btn_confirm.setEnabled(has_any)

    def _df_is_valid(self) -> bool:
        """Basic sanity check before confirming."""
        if self._df is None or self._df.empty:
            QMessageBox.warning(self, "No Data", "Please upload a dataset first.")
            return False
        return True

    def _clear_lists(self) -> None:
        self.list_num.clear()
        self.list_cat.clear()
        self.list_dt.clear()
        self._auto_num = []
        self._auto_cat = []
        self._auto_dt = []