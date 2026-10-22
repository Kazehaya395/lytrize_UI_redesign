"""Virtualized Qt table renderer for native table ChartModels."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHeaderView, QLabel, QTableView, QVBoxLayout, QWidget


def _number_text(value: Any, fmt: str) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return "—"
        if isinstance(value, int) and not isinstance(value, bool):
            return format(value, ",")
        patterns = {",.4f": ",.4f", ",.3f": ",.3f", ",.2f": ",.2f",
                    ",.1f": ",.1f", ",.0f": ",.0f",
                    ".2f": ".2f", ".1f": ".1f", ".0f": ".0f"}
        try:
            return format(value, patterns.get(fmt, ",.2f"))
        except (ValueError, TypeError):
            return str(value)
    return str(value)


class _TableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.headers: list[str] = []
        self.rows: list[list[Any]] = []
        self.display: dict[str, Any] = {}
        self.ranges: dict[int, tuple[float, float]] = {}

    def replace(self, model: dict) -> None:
        self.beginResetModel()
        self.headers = [str(value) for value in (model.get("data", {}).get("dimensions") or [])]
        series = model.get("data", {}).get("series") or []
        self.rows = [list(row) for row in (series[0].get("values", []) if series else [])]
        self.display = dict(model.get("display") or {})
        self.ranges = {}
        for column in range(1, max(1, len(self.headers) - 1)):
            values = [row[column] for row in self.rows
                      if column < len(row) and isinstance(row[column], (int, float))
                      and not isinstance(row[column], bool)
                      and math.isfinite(float(row[column]))]
            if values:
                self.ranges[column] = (float(min(values)), float(max(values)))
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section] if 0 <= section < len(self.headers) else ""
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row, column = index.row(), index.column()
        value = self.rows[row][column] if column < len(self.rows[row]) else None
        even = str(self.display.get("table_stripe_even_color", "#1e293b"))
        odd = str(self.display.get("table_stripe_odd_color", "#0f172a"))
        base = even if row % 2 == 0 else odd
        if role == Qt.ItemDataRole.DisplayRole:
            return _number_text(value, str(self.display.get("table_number_format", ",.2f")))
        if role == Qt.ItemDataRole.BackgroundRole:
            bounds = self.ranges.get(column)
            if bounds and isinstance(value, (int, float)) and not isinstance(value, bool):
                low, high = bounds
                if high > low:
                    ratio = max(0.0, min(1.0, (float(value) - low) / (high - low)))
                    color, accent = QColor(base), QColor("#1e40af")
                    return QColor(
                        round(color.red() + ratio * (accent.red() - color.red())),
                        round(color.green() + ratio * (accent.green() - color.green())),
                        round(color.blue() + ratio * (accent.blue() - color.blue())),
                    )
            return QColor(base)
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(str(self.display.get("table_font_color", "#f1f5f9")))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            alignment = (self.display.get("table_index_align", "left")
                         if column == 0 else self.display.get("table_data_align", "right"))
            alignments = {"left": Qt.AlignmentFlag.AlignLeft,
                          "center": Qt.AlignmentFlag.AlignHCenter,
                          "right": Qt.AlignmentFlag.AlignRight}
            return int(alignments.get(str(alignment), Qt.AlignmentFlag.AlignRight) |
                       Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.FontRole and column == len(self.headers) - 1:
            font = QFont()
            font.setFamily(str(self.display.get("table_font_family", "Inter")))
            font.setBold(True)
            font.setPointSize(int(self.display.get("table_font_size", 11)))
            return font
        return None


class NativeTableView(QWidget):
    """Scrollable table widget backed by a virtualized QAbstractTableModel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = _TableModel(self)
        self._title = QLabel()
        self._title.setWordWrap(False)
        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(False)
        self._table.setSortingEnabled(False)
        self._table.setWordWrap(False)
        self._table.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self._table.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._title)
        layout.addWidget(self._table, 1)

    def set_chart_model(self, model: dict) -> None:
        display = dict(model.get("display") or {})
        self._title.setText(str(model.get("title") or "Table"))
        title_font = self._title.font()
        title_font.setFamily(str(display.get("table_header_font_family", display.get("table_font_family", "Inter"))))
        title_font.setPointSize(max(8, int(display.get("title_size", 14))))
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setStyleSheet(
            f"color: {display.get('text_color', '#e2e8f0')}; background: transparent;")
        self._model.replace(model)
        header = self._table.horizontalHeader()
        header.setFixedHeight(int(display.get("table_header_height", 28)))
        font = QFont()
        font.setFamily(str(display.get("table_font_family", "Inter")))
        font.setPointSize(int(display.get("table_font_size", 11)))
        self._table.setFont(font)
        font = QFont()
        font.setFamily(str(display.get("table_header_font_family", display.get("table_font_family", "Inter"))))
        font.setPointSize(int(display.get("table_header_font_size", 12)))
        header.setFont(font)
        self._table.verticalHeader().setDefaultSectionSize(
            int(display.get("table_row_height", 26)))
        header_color = str(display.get("table_header_color", "#6163df"))
        header.setStyleSheet(
            f"QHeaderView::section {{ background: {header_color}; color: white; "
            "padding-left: 6px; border: 1px solid rgba(255,255,255,0.12); }}")
        self._table.setStyleSheet("QTableView { background: transparent; gridline-color: rgba(255,255,255,0.08); }")
        for column, label in enumerate(self._model.headers):
            longest = len(label)
            for row in self._model.rows[:100]:
                if column < len(row):
                    longest = max(longest, len(_number_text(
                        row[column], str(display.get("table_number_format", ",.2f")))))
            self._table.setColumnWidth(column, max(72, min(320, longest * 8 + 22)))

