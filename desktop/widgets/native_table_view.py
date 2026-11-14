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


def _theme_color(value: Any, role: str, mode: str) -> str:
    value = str(value or "auto").strip()
    light = mode != "dark"
    defaults = {
        "font": ("#1f2937", "#f1f5f9"),
        "even": ("#ffffff", "#1e293b"),
        "odd": ("#f8fafc", "#0f172a"),
        "grid": ("#cbd5e1", "rgba(255,255,255,0.08)"),
    }
    light_value, dark_value = defaults[role]
    legacy = {"font": {"#f1f5f9", "#e2e8f0"},
              "even": {"#1e293b"}, "odd": {"#0f172a"}}.get(role, set())
    if value.lower() in {"", "auto", "none"} or (light and value.lower() in legacy):
        return light_value if light else dark_value
    return value


class _TableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.headers: list[str] = []
        self.rows: list[list[Any]] = []
        self.display: dict[str, Any] = {}
        self.ranges: dict[int, tuple[float, float]] = {}
        self.hierarchy: list[dict] = []
        self._expanded: set[int] = set()
        self._visible: list[int] = []

    def replace(self, model: dict) -> None:
        self.beginResetModel()
        self.headers = [str(value) for value in (model.get("data", {}).get("dimensions") or [])]
        series = model.get("data", {}).get("series") or []
        self.rows = [list(row) for row in (series[0].get("values", []) if series else [])]
        self.display = dict(model.get("display") or {})
        self.hierarchy = list(self.display.get("table_hierarchy") or [])
        self._expanded.clear()
        self._refresh_visible()
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
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section] if 0 <= section < len(self.headers) else ""
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._visible):
            return None
        row, column = self._visible[index.row()], index.column()
        value = self.rows[row][column] if column < len(self.rows[row]) else None
        mode = str(self.display.get("theme_mode", "light"))
        even = _theme_color(self.display.get("table_stripe_even_color", "auto"), "even", mode)
        odd = _theme_color(self.display.get("table_stripe_odd_color", "auto"), "odd", mode)
        base = even if row % 2 == 0 else odd
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0 and row < len(self.hierarchy):
                meta = self.hierarchy[row]
                depth = max(0, int(meta.get("depth", 0)))
                marker = ("▾ " if row in self._expanded else "▸ ") if meta.get("has_children") else "  "
                return ("  " * depth) + marker + _number_text(
                    value, str(self.display.get("table_number_format", ",.2f")))
            return _number_text(value, str(self.display.get("table_number_format", ",.2f")))
        if role == Qt.ItemDataRole.ToolTipRole and column == 0 and row < len(self.hierarchy):
            return ("Click or press Enter to collapse this group" if row in self._expanded
                    else "Click or press Enter to expand this group") if self.hierarchy[row].get("has_children") else None
        if role == Qt.ItemDataRole.BackgroundRole:
            bounds = self.ranges.get(column)
            heat_enabled = bool(self.display.get("table_heat_enabled", True))
            last_column = (len(self.headers) - 1)
            if (heat_enabled and bounds and column != last_column
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)):
                low, high = bounds
                if high > low:
                    ratio = max(0.0, min(1.0, (float(value) - low) / (high - low)))
                    color, accent = QColor(base), QColor(
                        str(self.display.get("table_heat_color", "#1e40af")))
                    return QColor(
                        round(color.red() + ratio * (accent.red() - color.red())),
                        round(color.green() + ratio * (accent.green() - color.green())),
                        round(color.blue() + ratio * (accent.blue() - color.blue())),
                    )
            return QColor(base)
        if role == Qt.ItemDataRole.ForegroundRole:
            font_ink = QColor(_theme_color(self.display.get("table_font_color", "auto"), "font", mode))
            bounds = self.ranges.get(column)
            heat_enabled = bool(self.display.get("table_heat_enabled", True))
            last_column = len(self.headers) - 1
            if (heat_enabled and bounds and column != last_column
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)):
                low, high = bounds
                if high > low:
                    ratio = max(0.0, min(1.0, (float(value) - low) / (high - low)))
                    even = _theme_color(self.display.get("table_stripe_even_color", "auto"), "even", mode)
                    odd = _theme_color(self.display.get("table_stripe_odd_color", "auto"), "odd", mode)
                    base = QColor(even if row % 2 == 0 else odd)
                    accent = QColor(str(self.display.get("table_heat_color", "#1e40af")))
                    blended = QColor(
                        round(base.red() + ratio * (accent.red() - base.red())),
                        round(base.green() + ratio * (accent.green() - base.green())),
                        round(base.blue() + ratio * (accent.blue() - base.blue())))
                    # Contrast: ink that reads on the blended cell.
                    luminance = (0.2126 * blended.red() + 0.7152 * blended.green()
                                 + 0.0722 * blended.blue()) / 255.0
                    return QColor("#111827" if luminance > 0.55 else "#f8fafc")
            return font_ink
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

    def _refresh_visible(self):
        if not self.hierarchy or len(self.hierarchy) != len(self.rows):
            self._visible = list(range(len(self.rows)))
            return
        visible = []
        hidden_depth = None
        for row, meta in enumerate(self.hierarchy):
            depth = max(0, int(meta.get("depth", 0)))
            if hidden_depth is not None and depth > hidden_depth:
                continue
            hidden_depth = None
            visible.append(row)
            if meta.get("has_children") and row not in self._expanded:
                hidden_depth = depth
        self._visible = visible

    def toggle_hierarchy(self, visible_row: int) -> None:
        if not (0 <= visible_row < len(self._visible)):
            return
        row = self._visible[visible_row]
        if row >= len(self.hierarchy) or not self.hierarchy[row].get("has_children"):
            return
        self.beginResetModel()
        if row in self._expanded:
            self._expanded.remove(row)
        else:
            self._expanded.add(row)
        self._refresh_visible()
        self.endResetModel()


class NativeTableView(QWidget):
    """Scrollable table widget backed by a virtualized QAbstractTableModel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = _TableModel(self)
        self._title = QLabel()
        self._title.setWordWrap(False)
        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.clicked.connect(
            lambda index: self._model.toggle_hierarchy(index.row())
            if index.column() == 0 else None)
        self._table.activated.connect(
            lambda index: self._model.toggle_hierarchy(index.row())
            if index.column() == 0 else None)
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
        self._chart_model = dict(model)
        display = dict(model.get("display") or {})
        self._model.replace(model)
        self._apply_styles(display, model)

    def apply_theme(self) -> None:
        """Re-theme in place after the application palette changes.

        The analysis page calls this on every card when the user toggles
        light/dark; without it the table kept its generation-time colours.
        """
        model = getattr(self, "_chart_model", None)
        if not model:
            return
        try:
            from desktop.theme import current_mode
            mode = str(current_mode())
        except Exception:
            mode = str((model.get("display") or {}).get("theme_mode", "light"))
        model = dict(model)
        display = dict(model.get("display") or {})
        display["theme_mode"] = mode
        model["display"] = display
        self._chart_model = model
        self._model.display = dict(display)
        self._apply_styles(display, model)
        # Repaint cells (Background/Foreground roles read display live).
        self._model.beginResetModel()
        self._model.endResetModel()

    def _apply_styles(self, display: dict, model: dict) -> None:
        try:
            from desktop.theme import current_mode as _cm
            _title_mode = str(display.get("theme_mode") or _cm())
        except Exception:
            _title_mode = str(display.get("theme_mode", "light"))
        self._title.setText(str(model.get("title") or "Table"))
        title_font = self._title.font()
        title_font.setFamily(str(display.get("table_header_font_family",
                                            display.get("table_font_family", "Inter"))))
        title_font.setPointSize(max(8, int(display.get("title_size", 14))))
        style = str(display.get("title_font_style", "Bold")).lower()
        title_font.setBold("bold" in style)
        title_font.setItalic("italic" in style)
        title_font.setUnderline("underline" in style)
        self._title.setFont(title_font)
        title_color = str(display.get("title_color", "auto"))
        if title_color.lower() == "auto":
            title_color = _theme_color(str(display.get("text_color", "auto")), "font", _title_mode)
        align = str(display.get("title_align", "left")).lower()
        if align == "center":
            self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        elif align == "right":
            self._title.setAlignment(Qt.AlignmentFlag.AlignRight)
        else:
            self._title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._title.setStyleSheet(
            f"color: {title_color}; background: transparent;")
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
        try:
            from desktop.theme import current_mode
            mode = str(display.get("theme_mode") or current_mode())
        except Exception:
            mode = str(display.get("theme_mode", "light"))
        display["theme_mode"] = mode
        header_color = str(display.get("table_header_color", "#6163df"))
        header_text = "#1f2937" if mode != "dark" and header_color.lower() in ("#ffffff", "#f1f5f9") else "white"
        header.setStyleSheet(
            f"QHeaderView::section {{ background: {header_color}; color: {header_text}; "
            f"padding-left: 6px; border: 1px solid {_theme_color('auto', 'grid', mode)}; }}")
        self._table.setStyleSheet(f"QTableView {{ background: transparent; gridline-color: {_theme_color('auto', 'grid', mode)}; }}")
        for column, label in enumerate(self._model.headers):
            longest = len(label)
            for row in self._model.rows[:100]:
                if column < len(row):
                    longest = max(longest, len(_number_text(
                        row[column], str(display.get("table_number_format", ",.2f")))))
            self._table.setColumnWidth(column, max(72, min(320, longest * 8 + 22)))

