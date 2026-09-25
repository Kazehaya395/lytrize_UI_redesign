"""desktop/widgets/chart_legend_panel.py -- read-only legend/colorbar panel.

Renders the legend_data() dict produced by ChartCard.legend_data() as a
native Qt panel fixed to ~240-260px on the right of the chart preview.

Kinds:
  "table"      — go.Table output: just shows the title, no palette.
  "palette"    — generic bar/scatter/line: one swatch per trace name.
  "colorscale" — heatmap/choropleth: a vertical gradient + label.
  "categories" — pie: category -> color + value rows.

Read-only in this phase (editable later). Pure display of data already
computed by ChartCard — never touches Plotly figures.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy,
)


class _Swatch(QWidget):
    """Small fixed-size color swatch."""

    def __init__(self, color: str, size: int = 14, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(color)

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawRect(0, 0, self.width(), self.height())


class _ColorScale(QWidget):
    """Vertical gradient bar for heatmap/choropleth colorbars."""

    def __init__(self, colorscale: str = "Viridis", parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(20)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._stops = _colorscale_stops(colorscale)

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        h = self.height()
        w = self.width()
        grad = QLinearGradient(0, 0, 0, h)
        for stop, color in self._stops:
            grad.setColorAt(max(0.0, min(1.0, stop)), QColor(color))
        p.setBrush(grad)
        p.drawRect(0, 0, w, h)


_NAMED_COLORSCALES = {
    "Blues":   [(0,"#f7fbff"),(0.5,"#6baed6"),(1,"#08306b")],
    "Reds":    [(0,"#fff5f0"),(0.5,"#fb6a4a"),(1,"#67000d")],
    "Greens":  [(0,"#f7fcf5"),(0.5,"#74c476"),(1,"#00441b")],
    "Viridis": [(0,"#440154"),(0.5,"#21918c"),(1,"#fde725")],
    "Plasma":  [(0,"#0d0887"),(0.5,"#cc4778"),(1,"#f0f921")],
    "Cividis": [(0,"#00224e"),(0.5,"#7c7b15"),(1,"#fdea45")],
    "Magma":   [(0,"#000004"),(0.5,"#b73779"),(1,"#fcfdbf")],
    "Inferno": [(0,"#000004"),(0.5,"#bc3754"),(1,"#fcffa4")],
}


def _colorscale_stops(name: str):
    return _NAMED_COLORSCALES.get(name, _NAMED_COLORSCALES["Viridis"])


class ChartLegendPanel(QFrame):
    """Read-only legend panel. Call set_legend(dict) to update."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("chart_legend_panel")
        self.setMinimumWidth(220)
        self.setMaximumWidth(280)
        self._build_ui()

    def _build_ui(self) -> None:
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(10, 10, 10, 10)
        self._root.setSpacing(6)
        self._root.addStretch()

    def set_legend(self, data: Optional[dict]) -> None:
        """Rebuild the panel from a legend_data() dict."""
        # Clear
        while self._root.count():
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not data:
            self._root.addStretch()
            return

        kind = data.get("kind", "palette")
        title = data.get("title", "")
        if title:
            lbl = QLabel(title)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
            self._root.addWidget(lbl)

        if kind == "table":
            hint = QLabel("Tabular output — no legend.")
            hint.setStyleSheet("font-size: 11px;")
            self._root.addWidget(hint)
        elif kind == "colorscale":
            self._build_colorscale(data)
        elif kind == "categories":
            self._build_categories(data)
        else:  # palette
            self._build_palette(data)

        self._root.addStretch()

    def _build_palette(self, data: dict) -> None:
        for row in data.get("rows", []):
            wrap = QWidget()
            hl = QHBoxLayout(wrap)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            hl.addWidget(_Swatch(row.get("color", "#888888")))
            lab = QLabel(row.get("label", ""))
            lab.setStyleSheet("font-size: 11px;")
            lab.setWordWrap(True)
            hl.addWidget(lab, stretch=1)
            self._root.addWidget(wrap)

    def _build_categories(self, data: dict) -> None:
        for row in data.get("rows", []):
            wrap = QWidget()
            hl = QHBoxLayout(wrap)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            hl.addWidget(_Swatch(row.get("color", "#888888")))
            lab = QLabel(row.get("label", ""))
            lab.setStyleSheet("font-size: 11px;")
            lab.setWordWrap(True)
            hl.addWidget(lab, stretch=1)
            val = row.get("value")
            if val is not None:
                vlab = QLabel(str(val))
                vlab.setStyleSheet("font-size: 11px; font-weight: 600;")
                hl.addWidget(vlab)
            self._root.addWidget(wrap)

    def _build_colorscale(self, data: dict) -> None:
        wrap = QWidget()
        hl = QHBoxLayout(wrap)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        hl.addWidget(_ColorScale(data.get("colorscale", "Viridis")))
        lab = QLabel(data.get("label", ""))
        lab.setStyleSheet("font-size: 11px;")
        lab.setWordWrap(True)
        hl.addWidget(lab, stretch=1)
        self._root.addWidget(wrap)