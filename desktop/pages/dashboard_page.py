"""desktop/pages/dashboard_page.py -- Dashboard page (Qt).

Replaces the Streamlit ``page_dashboard()`` with a native Qt page that:
  - Shows charts in a scrollable grid
  - Supports KPI cards
  - Provides HTML export via the headless export service
  - Handles saved-session viewing
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QLineEdit, QComboBox, QFileDialog,
    QMessageBox, QFrame, QSplitter, QTextEdit, QCheckBox, QSpinBox,
    QGroupBox, QFormLayout, QColorDialog, QFontComboBox, QInputDialog,
    QDialog, QDialogButtonBox,
)

from desktop import theme as _theme_mod
from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from desktop.widgets.chart_card import ChartCard
from desktop.widgets.kpi_card import KpiCard
from desktop.workers import ExportWorker
from services.export_service import build_export_report, build_export_charts
from services.chart_service import safe_filename, num_cols
from services.database_service import (
    save_session_db, update_session_db, get_session_charts,
    get_session_meta, clear_draft, invalidate_sessions_cache,
)

log = logging.getLogger(__name__)


# Default values for every export-style key, in one place so the style
# panel, the live-apply path, and the export payload all agree on the
# same defaults instead of repeating them at each call site.
_STYLE_DEFAULTS = {
    "ex_bg": "#121a2e", "ex_card": "#1b2245", "ex_kpi": "#1b2245",
    "ex_accent": "#6163df", "ex_border": "#2c3564", "ex_text": "#f5f7ff",
    "ex_not_bg": "#1a1732", "ex_not_bd": "#8566fc",
    "ex_density": "Comfortable", "ex_radius": 12, "ex_chart_h": 400,
    "ex_width": "Auto", "ex_meta": True,
    "ex_kpi_text_color": "#f5f7ff", "ex_kpi_val_size": 14,
    "ex_title_font": "Inter", "ex_title_style": "Normal",
    "ex_title_size": 28, "ex_title_color": "#6163df",
    "ex_notes_font": "Inter", "ex_notes_style": "Normal",
    "ex_notes_size": 14, "ex_notes_color": "#f5f7ff",
}


class DashboardPage(QWidget):
    """Dashboard page: chart grid, KPIs, and export."""

    navigate_requested = Signal(str)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._chart_cards: dict[str, ChartCard] = {}
        self._kpi_widgets: list[KpiCard] = []
        self._color_swatches: dict[str, QPushButton] = {}
        self._viewing_saved = False

        self._build_ui()
        self._connect_signals()
        self._refresh_charts()
        self._refresh_kpis()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("📈 Dashboard")
        title.setProperty("class", "pageTitle")
        header.addWidget(title)
        header.addStretch()

        self.btn_back = QPushButton("← Back")
        self.btn_back.setProperty("kind", "ghost")
        header.addWidget(self.btn_back)

        self.btn_export = QPushButton("⬇️ Export HTML")
        self.btn_export.setProperty("kind", "primary")
        header.addWidget(self.btn_export)

        self.btn_export_png = QPushButton("📷 Export PNG")
        self.btn_export_png.setProperty("kind", "ghost")
        header.addWidget(self.btn_export_png)

        self.btn_save = QPushButton("💾 Save Session")
        self.btn_save.setProperty("kind", "success")
        header.addWidget(self.btn_save)
        root.addLayout(header)

        # -- Split: left = controls (session/title/layout/KPIs/style),
        #    right = the live dashboard itself (charts + KPI row). The
        #    right panel IS the preview -- every left-panel control applies
        #    to it immediately via _apply_style_to_cards(), there is no
        #    separate "preview vs real" rendering path to keep in sync.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)

        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        # KPI row sits above the chart grid, inside the same live-preview panel
        self.kpi_row_container = QWidget()
        self.kpi_row_layout = QHBoxLayout(self.kpi_row_container)
        self.kpi_row_layout.setContentsMargins(0, 0, 0, 0)
        self.kpi_row_layout.setSpacing(10)
        right_layout.addWidget(self.kpi_row_container)

        self.charts_scroll = QScrollArea()
        self.charts_scroll.setWidgetResizable(True)
        self.charts_container = QWidget()
        self.charts_layout = QGridLayout(self.charts_container)
        self.charts_layout.setContentsMargins(0, 0, 0, 0)
        self.charts_layout.setSpacing(12)
        self.charts_scroll.setWidget(self.charts_container)
        right_layout.addWidget(self.charts_scroll, stretch=1)

        self.lbl_empty = QLabel("No charts yet. Go to Analysis to generate some!")
        self.lbl_empty.setProperty("class", "faint")
        self.lbl_empty.setStyleSheet("font-size: 13px; padding: 20px;")
        self.lbl_empty.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.lbl_empty)

        splitter.addWidget(right_panel)
        splitter.setSizes([300, 900])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

    def _build_left_panel(self) -> QWidget:
        """Build the scrollable control column: session/title/layout,
        KPI management, and the dashboard style controls that were
        previously only consumed at export time with no UI to set them."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(260)
        scroll.setMaximumWidth(420)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(10)

        # -- Session / title / layout --
        meta_group = QGroupBox("📝 Dashboard Info")
        meta_form = QFormLayout(meta_group)
        meta_form.setContentsMargins(12, 14, 12, 12)
        meta_form.setSpacing(8)

        self.session_name = QLineEdit()
        self.session_name.setPlaceholderText("Session name")
        meta_form.addRow(self._form_label("Session"), self.session_name)

        self.dash_title = QLineEdit()
        self.dash_title.setPlaceholderText("e.g. Q1 2025 Sales Dashboard")
        meta_form.addRow(self._form_label("Title"), self.dash_title)

        self.layout_mode = QComboBox()
        self.layout_mode.addItems(["Portrait", "Landscape"])
        meta_form.addRow(self._form_label("Layout"), self.layout_mode)

        # Grid columns -- old codebase's grid_cols_n (2 or 3 per row),
        # previously this app derived column count from Portrait/Landscape
        # instead of exposing it directly, which meant Portrait mode was
        # always stuck at 1 column with no way to get 2 or 3. Decoupled
        # from orientation entirely now.
        self.grid_cols_combo = QComboBox()
        self.grid_cols_combo.addItems(["2 Columns", "3 Columns"])
        current_cols = int(self.bridge.get("grid_cols_n", 2))
        self.grid_cols_combo.setCurrentIndex(0 if current_cols == 2 else 1)
        self.grid_cols_combo.currentIndexChanged.connect(self._on_grid_cols_changed)
        meta_form.addRow(self._form_label("Grid columns"), self.grid_cols_combo)

        layout.addWidget(meta_group)

        # -- KPIs --
        kpi_group = QGroupBox("📊 KPI Cards")
        kpi_layout = QVBoxLayout(kpi_group)
        kpi_layout.setContentsMargins(12, 14, 12, 12)

        self.btn_add_kpi = QPushButton("+ Add KPI")
        self.btn_add_kpi.setProperty("kind", "ghost")
        self.btn_add_kpi.clicked.connect(self._on_add_kpi)
        kpi_layout.addWidget(self.btn_add_kpi)
        layout.addWidget(kpi_group)

        # -- Canvas colors --
        canvas_group = QGroupBox("🎨 Canvas Colors")
        canvas_form = QFormLayout(canvas_group)
        canvas_form.setContentsMargins(12, 14, 12, 12)
        canvas_form.setSpacing(6)
        for key, label in [("ex_bg", "Background"), ("ex_card", "Card"),
                            ("ex_kpi", "KPI card"), ("ex_border", "Border")]:
            canvas_form.addRow(self._form_label(label), self._make_color_field(key))
        layout.addWidget(canvas_group)

        # -- Text & accent --
        text_group = QGroupBox("🔤 Text & Accent")
        text_form = QFormLayout(text_group)
        text_form.setContentsMargins(12, 14, 12, 12)
        text_form.setSpacing(6)
        for key, label in [("ex_text", "Text"), ("ex_accent", "Accent")]:
            text_form.addRow(self._form_label(label), self._make_color_field(key))
        layout.addWidget(text_group)

        # -- Notes styling --
        notes_group = QGroupBox("🗒️ Notes Styling")
        notes_form = QFormLayout(notes_group)
        notes_form.setContentsMargins(12, 14, 12, 12)
        notes_form.setSpacing(6)
        for key, label in [("ex_not_bg", "Background"), ("ex_not_bd", "Border")]:
            notes_form.addRow(self._form_label(label), self._make_color_field(key))
        layout.addWidget(notes_group)

        # -- Layout density / sizing --
        density_group = QGroupBox("📐 Layout")
        density_form = QFormLayout(density_group)
        density_form.setContentsMargins(12, 14, 12, 12)
        density_form.setSpacing(6)

        self.ex_density = QComboBox()
        self.ex_density.addItems(["Comfortable", "Compact"])
        self.ex_density.setCurrentText(self.bridge.get("ex_density", _STYLE_DEFAULTS["ex_density"]))
        self.ex_density.currentTextChanged.connect(lambda v: self._on_field_changed("ex_density", v))
        density_form.addRow(self._form_label("Density"), self.ex_density)

        self.ex_radius = QSpinBox()
        self.ex_radius.setRange(0, 32)
        self.ex_radius.setValue(int(self.bridge.get("ex_radius", _STYLE_DEFAULTS["ex_radius"])))
        self.ex_radius.valueChanged.connect(lambda v: self._on_field_changed("ex_radius", v))
        density_form.addRow(self._form_label("Corner radius"), self.ex_radius)

        self.ex_chart_h = QSpinBox()
        self.ex_chart_h.setRange(200, 900)
        self.ex_chart_h.setSingleStep(20)
        self.ex_chart_h.setValue(int(self.bridge.get("ex_chart_h", _STYLE_DEFAULTS["ex_chart_h"])))
        self.ex_chart_h.valueChanged.connect(lambda v: self._on_field_changed("ex_chart_h", v))
        density_form.addRow(self._form_label("Chart height"), self.ex_chart_h)

        self.ex_width = QComboBox()
        self.ex_width.addItems(["Auto", "Fixed"])
        self.ex_width.setCurrentText(self.bridge.get("ex_width", _STYLE_DEFAULTS["ex_width"]))
        self.ex_width.currentTextChanged.connect(lambda v: self._on_field_changed("ex_width", v))
        density_form.addRow(self._form_label("Width"), self.ex_width)

        self.ex_meta = QCheckBox("Show chart metadata")
        self.ex_meta.setChecked(bool(self.bridge.get("ex_meta", _STYLE_DEFAULTS["ex_meta"])))
        self.ex_meta.setStyleSheet("font-size: 12px;")
        self.ex_meta.toggled.connect(lambda v: self._on_field_changed("ex_meta", v))
        density_form.addRow(self.ex_meta)

        layout.addWidget(density_group)

        # -- KPI text --
        kpi_text_group = QGroupBox("🔢 KPI Text")
        kpi_text_form = QFormLayout(kpi_text_group)
        kpi_text_form.setContentsMargins(12, 14, 12, 12)
        kpi_text_form.setSpacing(6)
        kpi_text_form.addRow(self._form_label("Text color"), self._make_color_field("ex_kpi_text_color"))

        self.ex_kpi_val_size = QSpinBox()
        self.ex_kpi_val_size.setRange(10, 48)
        self.ex_kpi_val_size.setValue(int(self.bridge.get("ex_kpi_val_size", _STYLE_DEFAULTS["ex_kpi_val_size"])))
        self.ex_kpi_val_size.valueChanged.connect(lambda v: self._on_field_changed("ex_kpi_val_size", v))
        kpi_text_form.addRow(self._form_label("Value size"), self.ex_kpi_val_size)
        layout.addWidget(kpi_text_group)

        # -- Title typography --
        layout.addWidget(self._build_typography_group(
            "🅰️ Title Typography", prefix="ex_title"
        ))

        # -- Notes typography --
        layout.addWidget(self._build_typography_group(
            "🅰️ Notes Typography", prefix="ex_notes"
        ))

        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _build_typography_group(self, title: str, prefix: str) -> QGroupBox:
        """Shared 4-field pattern (font, style, size, color) used for both
        title and notes typography -- avoids writing the same block twice."""
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setContentsMargins(12, 14, 12, 12)
        form.setSpacing(6)

        font_key = f"{prefix}_font"
        font_combo = QFontComboBox()
        font_combo.setCurrentFont(QFont(self.bridge.get(font_key, _STYLE_DEFAULTS[font_key])))
        font_combo.currentFontChanged.connect(
            lambda f, k=font_key: self._on_field_changed(k, f.family())
        )
        form.addRow(self._form_label("Font"), font_combo)
        setattr(self, font_key, font_combo)

        style_key = f"{prefix}_style"
        style_combo = QComboBox()
        style_combo.addItems(["Normal", "Italic", "Bold"])
        style_combo.setCurrentText(self.bridge.get(style_key, _STYLE_DEFAULTS[style_key]))
        style_combo.currentTextChanged.connect(
            lambda v, k=style_key: self._on_field_changed(k, v)
        )
        form.addRow(self._form_label("Style"), style_combo)
        setattr(self, style_key, style_combo)

        size_key = f"{prefix}_size"
        size_spin = QSpinBox()
        size_spin.setRange(8, 72)
        size_spin.setValue(int(self.bridge.get(size_key, _STYLE_DEFAULTS[size_key])))
        size_spin.valueChanged.connect(
            lambda v, k=size_key: self._on_field_changed(k, v)
        )
        form.addRow(self._form_label("Size"), size_spin)
        setattr(self, size_key, size_spin)

        color_key = f"{prefix}_color"
        form.addRow(self._form_label("Color"), self._make_color_field(color_key))

        return group

    def _sync_style_widgets_from_bridge(self) -> None:
        """Update every style-panel widget's displayed value to match
        current bridge state. Needed after loading a saved session --
        the left panel's widgets are built once at page construction,
        reading bridge.get(key, default) at THAT moment only; writing
        new values into the bridge later (e.g. from a loaded session)
        doesn't retroactively update an already-constructed QComboBox's
        displayed text or a color swatch's background. Without this,
        loading a session with a saved purple theme would apply that
        theme to the actual chart cards correctly (_apply_style_to_cards
        already does that) while the control panel kept showing
        whatever the previous session's colors happened to be --
        confusing since the panel would visually contradict the
        dashboard it's supposedly controlling.
        """
        style = self._current_style_dict()

        for key, swatch in self._color_swatches.items():
            color = style.get(key, _STYLE_DEFAULTS.get(key, "#000000"))
            swatch.setStyleSheet(
                f"background:{color}; border:1px solid palette(mid); border-radius:4px;"
            )

        self.ex_density.setCurrentText(style.get("ex_density", "Comfortable"))
        self.ex_radius.setValue(int(style.get("ex_radius", 12)))
        self.ex_chart_h.setValue(int(style.get("ex_chart_h", 400)))
        self.ex_width.setCurrentText(style.get("ex_width", "Auto"))
        self.ex_meta.setChecked(bool(style.get("ex_meta", True)))
        self.ex_kpi_val_size.setValue(int(style.get("ex_kpi_val_size", 14)))

        for prefix in ("ex_title", "ex_notes"):
            font_widget = getattr(self, f"{prefix}_font", None)
            if font_widget is not None:
                font_widget.setCurrentFont(QFont(style.get(f"{prefix}_font", "Inter")))
            style_widget = getattr(self, f"{prefix}_style", None)
            if style_widget is not None:
                style_widget.setCurrentText(style.get(f"{prefix}_style", "Normal"))
            size_widget = getattr(self, f"{prefix}_size", None)
            if size_widget is not None:
                size_widget.setValue(int(style.get(f"{prefix}_size", 16 if prefix == "ex_title" else 14)))

    def _make_color_field(self, key: str) -> QWidget:
        """One reusable color-picker row: a clickable swatch that opens
        QColorDialog and writes straight into the bridge, then re-applies
        live styling -- shared by every one of the 8 color keys instead of
        writing near-identical blocks for each."""
        default = _STYLE_DEFAULTS.get(key, "#000000")
        current = self.bridge.get(key, default)

        swatch = QPushButton()
        swatch.setFixedSize(32, 22)
        swatch.setCursor(Qt.PointingHandCursor)
        swatch.setStyleSheet(
            f"background:{current}; border:1px solid palette(mid); border-radius:4px;"
        )

        def pick():
            color = QColorDialog.getColor(QColor(self.bridge.get(key, default)), self)
            if color.isValid():
                hexval = color.name()
                swatch.setStyleSheet(
                    f"background:{hexval}; border:1px solid palette(mid); border-radius:4px;"
                )
                self._on_field_changed(key, hexval)

        swatch.clicked.connect(pick)
        self._color_swatches[key] = swatch
        return swatch

    @staticmethod
    def _form_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("class", "muted")
        lbl.setStyleSheet("font-size: 12px;")
        return lbl

    @staticmethod
    def _group_box_style() -> str:
        return "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"

    @staticmethod
    def _input_style() -> str:
        return _theme_mod.input_style()

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.btn_back.clicked.connect(self._on_back)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_export_png.clicked.connect(self._on_export_png)
        self.btn_save.clicked.connect(self._on_save)
        self.bridge.charts_changed.connect(self._on_charts_changed)
        self.bridge.theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode: str) -> None:
        """Re-colour the chart tiles after a dark<->light switch.

        A chart's canvas is painted by the figure / plot page, not by the app
        stylesheet, so each open view re-themes and repaints in place
        (Plotly.react -- no reload, no rebuild, no regeneration).
        """
        for card in self._chart_cards.values():
            try:
                card.plot_view.apply_theme()
            except Exception:
                log.debug("chart theme repaint failed", exc_info=True)

    # -- Style controls -------------------------------------------------

    def _current_style_dict(self) -> dict:
        """Single source of truth for the 23 export-style keys, read from
        the bridge with defaults -- used by export, by the live-apply
        path, and available for anything else that needs current styling."""
        return {key: self.bridge.get(key, default) for key, default in _STYLE_DEFAULTS.items()}

    def _on_field_changed(self, key: str, value) -> None:
        """Any style control (color, font, size, checkbox...) calls this
        on change. Writes to the bridge, then re-applies styling to the
        already-rendered cards in place -- never rebuilds them, so a
        color tweak stays instant instead of flashing the whole grid."""
        self.bridge.set(key, value)
        self._apply_style_to_cards()

    def _apply_style_to_cards(self) -> None:
        """Re-apply current dashboard style state (colors/typography/
        layout density) to already-rendered cards/KPIs, without
        touching chart data or rebuilding any widget.

        NAMING NOTE: this used to be named _on_style_changed() (no
        args), until Phase 1's per-chart style persistence handler
        (below, _on_style_changed(self, uid, style)) was added with the
        identical name. Python keeps only the last-defined method with
        a given name on a class, so the per-chart handler silently
        replaced this one -- every dashboard-level color/font/layout
        control has been raising a TypeError on every interaction since
        Phase 1 landed (_on_field_changed calling a now-2-argument
        method with zero arguments), caught via a real save/reload
        round-trip test, not by reading the diff. Renamed this one
        rather than the newer per-chart handler, since
        _on_style_changed(uid, style) matches the established
        _on_note_changed(uid, text)-style naming convention already
        used for other per-chart signal handlers.
        """
        style = self._current_style_dict()
        for card in self._chart_cards.values():
            card.apply_style(style)
        for kpi in self._kpi_widgets:
            kpi.apply_style(style)

    # -- KPI management -------------------------------------------------

    def _refresh_kpis(self) -> None:
        """Render the KPI row from bridge state. Previously the export
        path read ``bridge.get('kpis', [])`` but nothing ever rendered
        those KPIs in the live dashboard -- users could export cards they
        never saw while editing. This keeps the on-screen dashboard and
        the exported report showing the same KPIs."""
        for w in self._kpi_widgets:
            w.deleteLater()
        self._kpi_widgets = []

        while self.kpi_row_layout.count():
            item = self.kpi_row_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        kpis = self.bridge.get("kpis", [])
        style = self._current_style_dict()
        for i, kpi in enumerate(kpis):
            card = KpiCard(kpi.get("label", ""), kpi.get("value", ""), kpi.get("delta"), index=i)
            card.apply_style(style)
            card.move_up_requested.connect(self._on_move_kpi_up)
            card.move_down_requested.connect(self._on_move_kpi_down)
            card.remove_requested.connect(self._on_remove_kpi)
            self.kpi_row_layout.addWidget(card)
            self._kpi_widgets.append(card)
        self.kpi_row_layout.addStretch()
        self.kpi_row_container.setVisible(bool(kpis))

    def _on_add_kpi(self) -> None:
        """Open a small dialog to add a KPI card: pick a numeric column
        and an aggregation, compute the value from the current dataset,
        and append it to bridge state."""
        df = self.bridge.df
        if df is None or df.empty:
            QMessageBox.warning(self, "No Data", "Load a dataset before adding KPIs.")

        cols = num_cols(df)
        if not cols:
            QMessageBox.warning(self, "No Numeric Columns", "No numeric columns available to aggregate.")

        dialog = QDialog(self)
        dialog.setWindowTitle("Add KPI")
        form = QFormLayout(dialog)

        label_edit = QLineEdit()
        form.addRow(self._form_label("Label"), label_edit)

        col_combo = QComboBox()
        col_combo.addItems(cols)
        form.addRow(self._form_label("Column"), col_combo)

        agg_combo = QComboBox()
        agg_combo.addItems(["Sum", "Average", "Count", "Min", "Max"])
        form.addRow(self._form_label("Aggregation"), agg_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        col = col_combo.currentText()
        agg = agg_combo.currentText()
        label = label_edit.text().strip() or f"{agg} of {col}"

        try:
            series = df[col].dropna()
            if agg == "Sum":
                value = series.sum()
            elif agg == "Average":
                value = series.mean()
            elif agg == "Count":
                value = series.count()
            elif agg == "Min":
                value = series.min()
            else:
                value = series.max()
            value_str = f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
        except Exception as exc:
            QMessageBox.critical(self, "KPI Error", f"Could not compute KPI: {exc}")

        kpis = list(self.bridge.get("kpis", []))
        kpis.append({"label": label, "value": value_str})
        self.bridge.set("kpis", kpis)
        self._refresh_kpis()

    def _on_remove_kpi(self, index: int) -> None:
        kpis = list(self.bridge.get("kpis", []))
        if 0 <= index < len(kpis):
            kpis.pop(index)
            self.bridge.set("kpis", kpis)
            self._refresh_kpis()

    def _on_move_kpi_up(self, index: int) -> None:
        kpis = list(self.bridge.get("kpis", []))
        if 0 < index < len(kpis):
            kpis[index - 1], kpis[index] = kpis[index], kpis[index - 1]
            self.bridge.set("kpis", kpis)
            self._refresh_kpis()

    def _on_move_kpi_down(self, index: int) -> None:
        kpis = list(self.bridge.get("kpis", []))
        if 0 <= index < len(kpis) - 1:
            kpis[index], kpis[index + 1] = kpis[index + 1], kpis[index]
            self.bridge.set("kpis", kpis)
            self._refresh_kpis()

    # -- Slots ---------------------------------------------------------------

    def _on_back(self) -> None:
        """Navigate back to the previous page."""
        if self._viewing_saved:
            self.bridge.pop("view_session_id", None)
            self.bridge.pop("_view_charts", None)
            self.navigate_requested.emit("home")
        else:
            self.navigate_requested.emit("analysis")

    def _on_export(self) -> None:
        """Export the dashboard as an HTML file."""
        charts = self.bridge.get("charts", [])
        if not charts:
            QMessageBox.warning(self, "No Charts", "No charts to export.")

        session_name = self.session_name.text().strip() or "Lytrize Report"
        dash_title = self.dash_title.text().strip() or session_name
        orientation = "landscape" if self.layout_mode.currentText() == "Landscape" else "portrait"

        # Build export state from session -- same dict the live preview
        # uses (_current_style_dict), so the exported file matches what
        # was shown on screen while editing.
        state = self._current_style_dict()

        # Build export charts with meta and notes
        meta_map = {
            uid: self.bridge.get_chart_meta(uid)
            for uid, _, _ in charts
        }
        notes = {
            uid: self.bridge.get_note(uid)
            for uid, _, _ in charts
        }
        export_charts = build_export_charts(charts, meta_map=meta_map, notes=notes)

        # Disable export button while working
        self.btn_export.setEnabled(False)
        self.btn_export.setText("⏳ Exporting…")

        # Use a background worker to avoid blocking the UI
        self._export_format = "html"
        self._export_worker = ExportWorker(
            export_charts, session_name, state,
            orientation=orientation,
            kpis=self.bridge.get("kpis", []),
            dashboard_title=dash_title,
            grid_cols_n=int(self.bridge.get("grid_cols_n", 2)),
            parent=self,
        )
        self._export_worker.finished_ok.connect(self._on_export_finished)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_png(self) -> None:
        """Export the dashboard as a full-page PNG screenshot.

        Reuses the exact same HTML-generation path as _on_export() (same
        worker, same export_charts/state/kpis) -- the difference is only
        in what happens once the HTML comes back: instead of saving it
        directly, it gets rendered headlessly to an image first.

        Deliberately NOT implemented as a screenshot of the live Qt
        dashboard panel (QWidget.grab() on the page itself). That would
        need to composite native Qt widgets (KPI cards, buttons) together
        with embedded QWebEngineView charts in one grab, which has a
        well-documented history of not reliably capturing GPU-accelerated
        WebEngine content on some platforms/Qt versions -- confirmed
        working in THIS sandbox's software-rendering fallback during
        testing, but that's the specific case where it's known to work;
        real GPU-accelerated user machines are the case where it's known
        to sometimes silently fail. Rendering the already-correct HTML
        export fresh in a dedicated, single-purpose offscreen webview
        sidesteps that risk entirely.
        """
        charts = self.bridge.get("charts", [])
        if not charts:
            QMessageBox.warning(self, "No Charts", "No charts to export.")

        session_name = self.session_name.text().strip() or "Lytrize Report"
        dash_title = self.dash_title.text().strip() or session_name
        orientation = "landscape" if self.layout_mode.currentText() == "Landscape" else "portrait"
        state = self._current_style_dict()

        meta_map = {uid: self.bridge.get_chart_meta(uid) for uid, _, _ in charts}
        notes = {uid: self.bridge.get_note(uid) for uid, _, _ in charts}
        export_charts = build_export_charts(charts, meta_map=meta_map, notes=notes)

        self.btn_export_png.setEnabled(False)
        self.btn_export_png.setText("⏳ Rendering…")

        self._export_format = "png"
        self._export_worker = ExportWorker(
            export_charts, session_name, state,
            orientation=orientation,
            kpis=self.bridge.get("kpis", []),
            dashboard_title=dash_title,
            grid_cols_n=int(self.bridge.get("grid_cols_n", 2)),
            parent=self,
        )
        self._export_worker.finished_ok.connect(self._on_export_finished)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_finished(self, html: str, safe_file: str) -> None:
        """Handle successful HTML generation from the worker -- dispatches
        to either a direct HTML save or a PNG render, based on which
        export button was clicked."""
        if getattr(self, "_export_format", "html") == "png":
            self._render_html_to_png(html, safe_file)

        self.btn_export.setEnabled(True)
        self.btn_export.setText("⬇️ Export HTML")

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Dashboard", str(Path.home() / f"{safe_file}.html"),
            "HTML Files (*.html)",
        )
        if file_path:
            try:
                Path(file_path).write_text(html, encoding="utf-8")
                QMessageBox.information(self, "Export Complete",
                                        f"Dashboard saved to:\n{file_path}")
            except Exception as exc:
                log.error("Export write failed: %s", exc)
                QMessageBox.critical(self, "Export Error", str(exc))

    def _render_html_to_png(self, html: str, safe_file: str) -> None:
        """Render the exported HTML report to a full-page PNG using a
        dedicated, offscreen QWebEngineView.

        Flow: write HTML to a temp file (setHtml() has a real ~2MB limit
        that the CDN/inline-JS chart rewrite already ran into once in
        this codebase -- see item A -- a multi-chart report can easily
        exceed that), load it, measure the page's actual rendered height
        via JS once loaded, resize the view to that full height, give
        Plotly's own async chart rendering a moment to finish (page
        loadFinished fires before Plotly.newPlot() completes its work),
        then grab() the view and save.
        """
        import tempfile
        from PySide6.QtCore import QUrl, QTimer, QSize, Qt
        from PySide6.QtWidgets import QWidget

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            self.btn_export_png.setEnabled(True)
            self.btn_export_png.setText("📷 Export PNG")
            QMessageBox.critical(self, "PNG Export Unavailable",
                                 "QtWebEngine is not available in this build, "
                                 "so PNG rendering can't run. HTML export still works.")

        width_map = {"Auto": 1200, "Narrow": 1000, "Wide": 1500, "Full": 1200}
        render_width = width_map.get(self._current_style_dict().get("ex_width", "Auto"), 1200)

        tmp_dir = Path(tempfile.gettempdir()) / "lytrize_png_export"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        html_path = tmp_dir / f"{safe_file}_render.html"
        html_path.write_text(html, encoding="utf-8")

        # Keep references alive on self -- a local variable view/timer would
        # get garbage-collected before the async load/render finishes.
        self._png_render_view = QWebEngineView()
        # CRITICAL, confirmed by direct testing: a QWebEngineView that is
        # never shown never actually produces rendered frames for grab()
        # to capture, even after loadFinished fires and JS executes
        # successfully -- grab() silently returns a blank/near-blank image.
        # WA_DontShowOnScreen + show() forces the compositor to render
        # without putting a visible window on the user's screen (verified:
        # first attempt without this produced an image with only 1 distinct
        # color bucket across the whole render; with this fix, real chart
        # colors show up correctly).
        self._png_render_view.setAttribute(Qt.WA_DontShowOnScreen, True)
        self._png_render_view.resize(render_width, 800)  # placeholder height, corrected after load
        self._png_render_view.show()

        def on_load_finished(ok: bool) -> None:
            if not ok:
                self._finish_png_export(None, safe_file)

            def on_height(height):
                try:
                    full_height = max(int(height), 400)
                except (TypeError, ValueError):
                    full_height = 1200
                full_height = min(full_height, 20000)  # sanity cap
                self._png_render_view.resize(render_width, full_height)
                # Give Plotly's own async rendering (newPlot/react run after
                # page loadFinished, not before) time to actually paint --
                # a fixed settle delay rather than trying to hook every
                # chart's own render-complete event across N charts.
                QTimer.singleShot(1200, lambda: self._capture_png(safe_file))

            self._png_render_view.page().runJavaScript(
                "document.body.scrollHeight", on_height
            )

        self._png_render_view.loadFinished.connect(on_load_finished)
        self._png_render_view.load(QUrl.fromLocalFile(str(html_path)))

    def _capture_png(self, safe_file: str) -> None:
        """Grab the fully-rendered offscreen view and hand off to save."""
        pixmap = self._png_render_view.grab()
        self._finish_png_export(pixmap, safe_file)

    def _finish_png_export(self, pixmap, safe_file: str) -> None:
        self.btn_export_png.setEnabled(True)
        self.btn_export_png.setText("📷 Export PNG")

        if pixmap is None or pixmap.isNull():
            QMessageBox.critical(self, "PNG Export Failed",
                                 "Could not render the dashboard to an image.")
            self._png_render_view = None

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Dashboard as Image", str(Path.home() / f"{safe_file}.png"),
            "PNG Files (*.png)",
        )
        if file_path:
            if pixmap.save(file_path, "PNG"):
                QMessageBox.information(self, "Export Complete",
                                        f"Dashboard image saved to:\n{file_path}")
            else:
                QMessageBox.critical(self, "Export Error", "Failed to save the PNG file.")

        self._png_render_view = None

    def _on_export_failed(self, error: str) -> None:
        """Handle export failure from the worker."""
        self.btn_export.setEnabled(True)
        self.btn_export.setText("⬇️ Export HTML")
        self.btn_export_png.setEnabled(True)
        self.btn_export_png.setText("📷 Export PNG")
        log.error("Export failed: %s", error)
        QMessageBox.critical(self, "Export Error", error)

    def _on_save(self) -> None:
        """Save the current dashboard as a session."""
        charts = self.bridge.get("charts", [])
        if not charts:
            QMessageBox.warning(self, "No Charts", "No charts to save.")

        user_id = self.bridge.get("user_id")
        if not user_id:
            QMessageBox.warning(self, "No User", "No user session found.")

        session_name = self.session_name.text().strip() or "Lytrize Session"
        dash_title = self.dash_title.text().strip()
        layout_mode = "landscape" if self.layout_mode.currentText() == "Landscape" else "portrait"

        # Check if we're editing an existing session
        editing_sid = self.bridge.get("editing_session_id")
        if editing_sid:
            reply = QMessageBox.question(
                self, "Overwrite Session?",
                f"Session '{session_name}' already exists.\nDo you want to overwrite it?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.No:
                # Prompt for new name
                new_name, ok = QInputDialog.getText(
                    self, "Save As", "Enter a new session name:",
                    text=f"{session_name} (copy)"
                )
                if not ok or not new_name.strip():
                    return
                session_name = new_name.strip()
                editing_sid = None  # Create new session

        # Build charts JSON
        from services.chart_service import charts_to_json
        meta_map = {
            uid: self.bridge.get_chart_meta(uid)
            for uid, _, _ in charts
        }
        notes = {
            uid: self.bridge.get_note(uid)
            for uid, _, _ in charts
        }
        charts_json = charts_to_json(charts, notes=notes, meta_map=meta_map)

        df = self.bridge.df
        grid_cols_n = int(self.bridge.get("grid_cols_n", 2))
        grid_fullwidth_json = json.dumps(self.bridge.get("grid_fullwidth", {}))
        # The 23-key style dict (colors, typography, layout density from
        # the dashboard style panel) was being read/applied correctly
        # during a live session but never persisted at all -- the DB
        # already has export_text_json/export_colours_json columns for
        # this (confirmed against the old codebase), but nothing here
        # ever wrote to them. Storing the whole style dict as one blob
        # in export_colours_json rather than replicating the old
        # codebase's exact text/colours key split -- this is a fresh,
        # internal-only column for this app, not required to match the
        # old JSON shape for any compatibility reason, and one dict
        # matching _current_style_dict()/_STYLE_DEFAULTS exactly is
        # simpler to keep correct than a hand-maintained translation
        # layer between two different key-naming conventions.
        export_colours_json = json.dumps(self._current_style_dict())
        try:
            if editing_sid:
                # Update existing session.
                #
                # BUG FIXED HERE: this call previously passed its arguments
                # positionally, copied from save_session_db's parameter
                # order -- but update_session_db has a genuinely different
                # signature (session_id, session_name, charts_json,
                # analysis_types, user_id, ...), no file_name/rows/cols
                # params at all, and dashboard_title in a different slot.
                # The positional call landed user_id where session_name
                # was expected, session_name where charts_json was
                # expected, and so on, then crashed outright with
                # "got multiple values for argument 'dashboard_title'"
                # since position 6 and the keyword both targeted the same
                # parameter. Confirmed by reproducing the exact call
                # shape in isolation. Every "update existing session" save
                # was broken. Switched to all-keyword arguments so this
                # entire class of bug can't happen again if either
                # function's parameter order ever changes.
                from services.database_service import update_session_db
                update_session_db(
                    session_id=editing_sid,
                    session_name=session_name,
                    charts_json=charts_json,
                    analysis_types=self.bridge.get("selected_analyses", []),
                    user_id=user_id,
                    dashboard_title=dash_title,
                    kpis_json=json.dumps(self.bridge.get("kpis", [])),
                    layout_mode=layout_mode,
                    grid_cols_n=grid_cols_n,
                    grid_fullwidth_json=grid_fullwidth_json,
                    export_colours_json=export_colours_json,
                )
                clear_draft(user_id)
                invalidate_sessions_cache()
                QMessageBox.information(self, "Updated",
                                        f"Session '{session_name}' updated successfully.")
                # Clear editing state
                self.bridge.pop("editing_session_id", None)
                self.bridge.pop("editing_session_name", None)
                self.bridge.pop("editing_file_name", None)
            else:
                # Create new session
                sid = save_session_db(
                    user_id=user_id,
                    session_name=session_name,
                    file_name=self.bridge.get("file_name", ""),
                    rows=df.shape[0] if df is not None else 0,
                    cols=df.shape[1] if df is not None else 0,
                    analysis_types=self.bridge.get("selected_analyses", []),
                    charts_json=charts_json,
                    dashboard_title=dash_title,
                    kpis_json=json.dumps(self.bridge.get("kpis", [])),
                    layout_mode=layout_mode,
                    grid_cols_n=grid_cols_n,
                    grid_fullwidth_json=grid_fullwidth_json,
                    export_colours_json=export_colours_json,
                )
                clear_draft(user_id)
                invalidate_sessions_cache()
                QMessageBox.information(self, "Saved",
                                        f"Session '{session_name}' saved successfully.")
        except Exception as exc:
            log.error("Save failed: %s", exc)
            QMessageBox.critical(self, "Save Error", str(exc))

    def _on_charts_changed(self, charts) -> None:
        """Handle charts_changed signal."""
        self._refresh_charts()
        self._refresh_kpis()

    # -- Chart management ---------------------------------------------------

    def _refresh_charts(self) -> None:
        """Rebuild the chart cards from the session bridge in a grid layout.

        Incremental on purpose: only the cards whose chart disappeared are
        destroyed, and the surviving ones are re-gridded in place.  A
        ChartCard owns a ``QWebEngineView``, so rebuilding every tile reloads
        the plotly.js shell for each chart and flashes the whole grid -- the
        same "the app reopened itself" bug the Analysis page had on Generate.
        """
        charts = self.bridge.get("charts", [])
        wanted = [c[0] for c in charts]

        # Detach every card from the grid (they are re-placed below); drop
        # only the ones whose chart is gone.
        for i in reversed(range(self.charts_layout.count())):
            item = self.charts_layout.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is None:
                self.charts_layout.takeAt(i)
                continue
            if getattr(w, "uid", None) in wanted:
                self.charts_layout.removeWidget(w)
                continue
            self.charts_layout.takeAt(i)
            self._chart_cards.pop(getattr(w, "uid", None), None)
            w.deleteLater()

        self.lbl_empty.setVisible(len(charts) == 0)

        # layout_mode still reflects Portrait/Landscape (used for export
        # page orientation, not grid column count -- see grid_cols_n below).
        view_sid = self.bridge.get("view_session_id")
        sm = None
        if view_sid:
            # Loaded saved session: use its saved layout mode + grid state
            user_id = self.bridge.get("user_id")
            sm = get_session_meta(view_sid, user_id) if user_id else None
            layout_mode = sm.get("layout_mode", "portrait") if sm else "portrait"
        else:
            # Current session: use selected mode
            layout_mode = "landscape" if self.layout_mode.currentText() == "Landscape" else "portrait"

        if sm:
            # Viewing a saved session: grid layout comes from what was
            # actually saved with it, not from whatever the live bridge
            # happens to hold (which could be leftover state from a
            # previous, different session).
            grid_cols = int(sm.get("grid_cols_n", 2))
            try:
                fullwidth_map = json.loads(sm.get("grid_fullwidth_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                fullwidth_map = {}
        else:
            grid_cols = int(self.bridge.get("grid_cols_n", 2))
            fullwidth_map = dict(self.bridge.get("grid_fullwidth", {}))

        # Explicit equal stretch per column -- verified empirically that
        # Qt's default (no stretch factors set) happens to distribute
        # space evenly for this app's current card content, including an
        # asymmetric-content test (one card with a very long note vs a
        # plain one still split 50/50). Setting this explicitly removes
        # the "happens to work out today" reliance so it can't quietly
        # become uneven if a future card's content grows a much larger
        # natural size hint than its neighbors.
        for i in range(max(grid_cols, 1)):
            self.charts_layout.setColumnStretch(i, 1)

        # Add chart cards in grid. A running row/col cursor (not simple
        # idx // grid_cols division) is needed because a full-width chart
        # breaks the regular wrapping pattern -- it flushes whatever row
        # is in progress, takes an entire row by itself, and the next
        # chart starts fresh on the row after it.
        row = 0
        col = 0
        for idx, (uid, title, fig) in enumerate(charts):
            chart_type = self.bridge.get(f"chart_type_{uid}", "")
            meta = self.bridge.get_chart_meta(uid)
            note = self.bridge.get_note(uid)

            card = self._chart_cards.get(uid)
            if card is None:
                card = ChartCard(uid, title, fig, chart_type, meta)
                card.set_note(note)
                card.delete_requested.connect(self._on_delete_chart)
                card.note_changed.connect(self._on_note_changed)
                card.style_changed.connect(self._on_style_changed)

                # Hide edit button (chart config editing is an Analysis-page
                # concept), but show reorder + full-width controls -- these
                # are dashboard-grid-specific, the inverse of Analysis page
                # which shows edit/up/down but has no concept of full-width.
                card.btn_edit.setVisible(False)
                card.btn_up.setVisible(True)
                card.btn_down.setVisible(True)
                card.move_up_requested.connect(self._on_move_up)
                card.move_down_requested.connect(self._on_move_down)

                card.btn_fullwidth.setVisible(True)
                card.fullwidth_toggled.connect(self._on_fullwidth_toggled)
                self._chart_cards[uid] = card
            else:
                # Surviving tile: update it in place (title/notes/figure/
                # full-width) so its web view and loaded page are kept.
                card.set_title(title)
                card.set_note(note)
                if fig is not card._fig:
                    card.set_figure(fig)

            # Style is the dashboard's own palette layer: re-applying it to a
            # retained card is a stylesheet swap on the tile, not a rebuild.
            card.apply_style(self._current_style_dict())
            card.set_fullwidth(fullwidth_map.get(uid, False))

            if fullwidth_map.get(uid, False):
                if col != 0:
                    row += 1
                    col = 0
                self.charts_layout.addWidget(card, row, 0, 1, grid_cols)
                row += 1
                col = 0
            else:
                self.charts_layout.addWidget(card, row, col)
                col += 1
                if col >= grid_cols:
                    col = 0
                    row += 1

            self._chart_cards[uid] = card

    def _on_delete_chart(self, uid: str) -> None:
        """Delete a chart from the dashboard."""
        self.bridge.remove_chart(uid)
        self._refresh_charts()

    def _on_grid_cols_changed(self, index: int) -> None:
        """2-column / 3-column grid selector. Structural change (affects
        every card's row/col position), so a full rebuild is needed --
        unlike color/typography changes, which apply in place."""
        cols = 2 if index == 0 else 3
        self.bridge.set("grid_cols_n", cols)
        self._refresh_charts()

    def _on_move_up(self, uid: str) -> None:
        """Move a chart earlier in the dashboard's chart order. Reuses
        the same 'charts' list Analysis page orders charts by -- one
        shared order rather than a separate dashboard-only arrangement,
        so reordering here and on Analysis always stay in sync instead
        of silently drifting apart into two different orders."""
        charts = list(self.bridge.get("charts", []))
        idx = next((i for i, c in enumerate(charts) if c[0] == uid), -1)
        if idx > 0:
            charts[idx - 1], charts[idx] = charts[idx], charts[idx - 1]
            self.bridge.set("charts", charts)
            self._refresh_charts()

    def _on_move_down(self, uid: str) -> None:
        """Move a chart later in the dashboard's chart order (see _on_move_up)."""
        charts = list(self.bridge.get("charts", []))
        idx = next((i for i, c in enumerate(charts) if c[0] == uid), -1)
        if 0 <= idx < len(charts) - 1:
            charts[idx], charts[idx + 1] = charts[idx + 1], charts[idx]
            self.bridge.set("charts", charts)
            self._refresh_charts()

    def _on_fullwidth_toggled(self, uid: str, is_full_width: bool) -> None:
        """Per-chart full-width toggle -- old codebase's grid_fullwidth.
        A full-width chart takes its own row, spanning every column,
        instead of sharing a row slot with neighboring charts."""
        fullwidth_map = dict(self.bridge.get("grid_fullwidth", {}))
        fullwidth_map[uid] = is_full_width
        self.bridge.set("grid_fullwidth", fullwidth_map)
        self._refresh_charts()

    def _on_note_changed(self, uid: str, text: str) -> None:
        """Handle note changes."""
        self.bridge.set_note(uid, text)

    def _on_style_changed(self, uid: str, style: dict) -> None:
        """Persist a chart's style settings to the bridge -- same pattern
        as _on_note_changed, so styling survives a session save instead
        of only living on the in-memory ChartCard instance."""
        self.bridge.set_chart_meta(uid, display_options=style)

    # -- Public API ----------------------------------------------------------

    def refresh(self) -> None:
        """Refresh the page when navigated to."""
        # Check if we're viewing a saved session
        view_sid = self.bridge.get("view_session_id")
        self._viewing_saved = view_sid is not None

        if view_sid:
            user_id = self.bridge.get("user_id")
            loaded = get_session_charts(view_sid, user_id)
            if loaded:
                # Convert 6-tuples to 3-tuples for the bridge
                charts = [(uid, title, fig) for uid, title, fig, *_ in loaded]
                self.bridge.set("charts", charts)
                for uid, title, fig, desc, ctype, meta in loaded:
                    self.bridge.set(f"chart_type_{uid}", ctype)
                    self.bridge.set(f"chart_meta_{uid}", meta)
                    if desc:
                        self.bridge.set_note(uid, desc)

                sm = get_session_meta(view_sid, user_id)
                if sm:
                    self.dash_title.setText(sm.get("dashboard_title", ""))
                    self.session_name.setText(
                        self.bridge.get("view_session_name", "Saved Session")
                    )
                    layout = sm.get("layout_mode", "portrait")
                    self.layout_mode.setCurrentText(
                        "Landscape" if layout == "landscape" else "Portrait"
                    )
                    try:
                        self.bridge.set("kpis", json.loads(sm.get("kpis_json", "[]")))
                    except Exception:
                        self.bridge.set("kpis", [])

                    # grid_cols_n/grid_fullwidth: _refresh_charts() already
                    # reads these correctly straight from `sm` for laying
                    # out the chart grid itself (see its own view_sid
                    # branch), but that's a local computation inside that
                    # method -- it never wrote the values into the bridge
                    # or updated grid_cols_combo's displayed selection.
                    # Confirmed via a real save/reload test: the chart
                    # grid rendered with the right column count, but
                    # bridge.get("grid_cols_n") came back None and the
                    # combo box still showed "2 Columns" for a session
                    # saved with 3 -- a visible contradiction between the
                    # dashboard and its own control panel, and a latent
                    # bug where re-saving without touching the combo
                    # would silently revert to the wrong column count.
                    grid_cols_n = int(sm.get("grid_cols_n", 2))
                    self.bridge.set("grid_cols_n", grid_cols_n)
                    self.grid_cols_combo.setCurrentIndex(0 if grid_cols_n == 2 else 1)
                    try:
                        self.bridge.set("grid_fullwidth", json.loads(sm.get("grid_fullwidth_json", "{}")))
                    except (json.JSONDecodeError, TypeError):
                        self.bridge.set("grid_fullwidth", {})

                    # Restore the 23-key style dict (colors, typography,
                    # layout density) into the bridge, then push those
                    # values into the actual widgets -- see
                    # _sync_style_widgets_from_bridge for why the second
                    # step is necessary and not implied by the first.
                    try:
                        saved_style = json.loads(sm.get("export_colours_json", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        saved_style = {}
                    for key, default in _STYLE_DEFAULTS.items():
                        self.bridge.set(key, saved_style.get(key, default))
                    self._sync_style_widgets_from_bridge()
        else:
            # Not viewing saved — use current session state
            editing_name = self.bridge.get("editing_session_name")
            if editing_name:
                self.session_name.setText(editing_name)
            else:
                self.session_name.setText(
                    self.bridge.get("file_name", "Analysis Session")
                )
        self._refresh_charts()
        self._refresh_kpis()
