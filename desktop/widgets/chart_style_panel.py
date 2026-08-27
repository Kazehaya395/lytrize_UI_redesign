"""desktop/widgets/chart_style_panel.py -- Granular chart styling controls.

Qt UI for backend/chart_style.py's style dict. Emits style_changed(dict)
on every control change; the caller (ChartCard) re-renders the chart
from its original figure with the new style applied -- this panel
never touches Plotly figures directly, it only produces a plain dict.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QComboBox, QCheckBox,
    QSpinBox, QDoubleSpinBox, QPushButton, QColorDialog, QGroupBox,
)

from chart_style import default_style, FONT_FAMILIES, MARKER_CHART_TYPES, VALUE_LABEL_CHART_TYPES
from modules.charts import PALETTES


class ChartStylePanel(QWidget):
    """Granular chart styling controls -- palette, legend, gridlines,
    bar gap, value labels, font, marker size/opacity. Shows/hides
    controls that don't apply to the given chart_type, same intent as
    the old codebase's has_control()/get_chart_type_capabilities()."""

    style_changed = Signal(dict)

    def __init__(self, chart_type: str = "", style: Optional[dict] = None, parent=None) -> None:
        super().__init__(parent)
        self._chart_type = chart_type
        self._style = {**default_style(), **(style or {})}
        self._updating = False  # guards against re-entrant signal emission while setting initial values
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        input_style = (
            "QComboBox, QSpinBox, QDoubleSpinBox {"
            "  background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; border-radius: 6px;"
            "  padding: 4px 8px; font-size: 12px;"
            "}"
        )
        group_style = (
            "QGroupBox {"
            "  color: #cbd5e1; font-size: 12px; font-weight: 600;"
            "  border: 1px solid #1e293b; border-radius: 8px;"
            "  margin-top: 8px; padding-top: 4px;"
            "}"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )

        # -- Colors --
        color_group = QGroupBox("🎨 Colors")
        color_group.setStyleSheet(group_style)
        color_form = QFormLayout(color_group)
        color_form.setContentsMargins(12, 14, 12, 12)

        self.palette_combo = QComboBox()
        self.palette_combo.addItems(list(PALETTES.keys()))
        self.palette_combo.setCurrentText(self._style["palette"])
        self.palette_combo.setStyleSheet(input_style)
        self.palette_combo.currentTextChanged.connect(lambda v: self._on_field("palette", v))
        color_form.addRow(self._label("Palette"), self.palette_combo)
        root.addWidget(color_group)

        # -- Layout --
        layout_group = QGroupBox("📐 Layout")
        layout_group.setStyleSheet(group_style)
        layout_form = QFormLayout(layout_group)
        layout_form.setContentsMargins(12, 14, 12, 12)

        self.legend_check = QCheckBox("Show legend")
        self.legend_check.setChecked(self._style["show_legend"])
        self.legend_check.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        self.legend_check.toggled.connect(lambda v: self._on_field("show_legend", v))
        layout_form.addRow(self.legend_check)

        self.gridlines_check = QCheckBox("Show gridlines")
        self.gridlines_check.setChecked(self._style["show_gridlines"])
        self.gridlines_check.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        self.gridlines_check.toggled.connect(lambda v: self._on_field("show_gridlines", v))
        layout_form.addRow(self.gridlines_check)

        self.bargap_spin = QDoubleSpinBox()
        self.bargap_spin.setRange(0.0, 0.9)
        self.bargap_spin.setSingleStep(0.05)
        self.bargap_spin.setValue(self._style["bar_gap"])
        self.bargap_spin.setStyleSheet(input_style)
        self.bargap_spin.valueChanged.connect(lambda v: self._on_field("bar_gap", v))
        layout_form.addRow(self._label("Bar gap"), self.bargap_spin)
        root.addWidget(layout_group)

        # -- Value labels --
        label_group = QGroupBox("🔢 Value Labels")
        label_group.setStyleSheet(group_style)
        label_form = QFormLayout(label_group)
        label_form.setContentsMargins(12, 14, 12, 12)

        self.value_labels_check = QCheckBox("Show value labels")
        self.value_labels_check.setChecked(self._style["show_value_labels"])
        self.value_labels_check.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        self.value_labels_check.toggled.connect(lambda v: self._on_field("show_value_labels", v))
        label_form.addRow(self.value_labels_check)

        self.label_color_btn = self._make_color_field("value_label_color")
        label_form.addRow(self._label("Label color"), self.label_color_btn)
        root.addWidget(label_group)
        label_group.setVisible(not self._chart_type or self._chart_type in VALUE_LABEL_CHART_TYPES)

        # -- Typography --
        font_group = QGroupBox("🔤 Typography")
        font_group.setStyleSheet(group_style)
        font_form = QFormLayout(font_group)
        font_form.setContentsMargins(12, 14, 12, 12)

        self.font_combo = QComboBox()
        self.font_combo.addItems(FONT_FAMILIES)
        self.font_combo.setCurrentText(self._style["font_family"])
        self.font_combo.setStyleSheet(input_style)
        self.font_combo.currentTextChanged.connect(lambda v: self._on_field("font_family", v))
        font_form.addRow(self._label("Font"), self.font_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 32)
        self.font_size_spin.setValue(self._style["font_size"])
        self.font_size_spin.setStyleSheet(input_style)
        self.font_size_spin.valueChanged.connect(lambda v: self._on_field("font_size", v))
        font_form.addRow(self._label("Body size"), self.font_size_spin)

        self.title_size_spin = QSpinBox()
        self.title_size_spin.setRange(10, 48)
        self.title_size_spin.setValue(self._style["title_font_size"])
        self.title_size_spin.setStyleSheet(input_style)
        self.title_size_spin.valueChanged.connect(lambda v: self._on_field("title_font_size", v))
        font_form.addRow(self._label("Title size"), self.title_size_spin)
        root.addWidget(font_group)

        # -- Markers (scatter/map only) --
        self.marker_group = QGroupBox("⚫ Markers")
        self.marker_group.setStyleSheet(group_style)
        marker_form = QFormLayout(self.marker_group)
        marker_form.setContentsMargins(12, 14, 12, 12)

        self.marker_size_spin = QSpinBox()
        self.marker_size_spin.setRange(2, 30)
        self.marker_size_spin.setValue(self._style["marker_size"])
        self.marker_size_spin.setStyleSheet(input_style)
        self.marker_size_spin.valueChanged.connect(lambda v: self._on_field("marker_size", v))
        marker_form.addRow(self._label("Size"), self.marker_size_spin)

        self.marker_opacity_spin = QDoubleSpinBox()
        self.marker_opacity_spin.setRange(0.1, 1.0)
        self.marker_opacity_spin.setSingleStep(0.05)
        self.marker_opacity_spin.setValue(self._style["marker_opacity"])
        self.marker_opacity_spin.setStyleSheet(input_style)
        self.marker_opacity_spin.valueChanged.connect(lambda v: self._on_field("marker_opacity", v))
        marker_form.addRow(self._label("Opacity"), self.marker_opacity_spin)
        root.addWidget(self.marker_group)
        self.marker_group.setVisible(not self._chart_type or self._chart_type in MARKER_CHART_TYPES)

        root.addStretch()

    def _make_color_field(self, key: str) -> QPushButton:
        default = self._style.get(key, "#ffffff")
        swatch = QPushButton()
        swatch.setFixedSize(32, 22)
        swatch.setCursor(Qt.PointingHandCursor)
        swatch.setStyleSheet(f"background:{default}; border:1px solid #334155; border-radius:4px;")

        def pick():
            color = QColorDialog.getColor(QColor(self._style.get(key, default)), self)
            if color.isValid():
                hexval = color.name()
                swatch.setStyleSheet(f"background:{hexval}; border:1px solid #334155; border-radius:4px;")
                self._on_field(key, hexval)

        swatch.clicked.connect(pick)
        return swatch

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px; color: #94a3b8;")
        return lbl

    def _on_field(self, key: str, value) -> None:
        if self._updating:
            return
        self._style[key] = value
        self.style_changed.emit(dict(self._style))

    def current_style(self) -> dict:
        return dict(self._style)
