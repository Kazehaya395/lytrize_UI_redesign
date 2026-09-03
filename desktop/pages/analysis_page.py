"""desktop/pages/analysis_page.py -- Analysis page (Qt).

Replaces the Streamlit ``page_analysis()`` with a native Qt page that:
  - Shows analysis type cards (Descriptive, Statistical, Distribution, etc.)
  - Builds a config panel from the WIDGET_SPEC schema
  - Generates charts via the headless analysis service
  - Displays charts in a scrollable grid with per-chart cards
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QComboBox, QListWidget, QListWidgetItem,
    QLineEdit, QSpinBox, QCheckBox, QFrame, QMessageBox, QSplitter,
    QStackedWidget, QSizePolicy, QTabWidget,
)

from desktop.session_bridge import SessionBridge
from desktop.widgets.chart_card import ChartCard
from desktop.workers import ChartGenerationWorker
from services.analysis_service import (
    ANALYSIS_OPTIONS, WIDGET_SPEC, run_analysis_with_config,
    num_cols, cat_cols, dt_cols,
)
from services.chart_service import PALETTES, apply_hover_format

log = logging.getLogger(__name__)

# id -> friendly display name, built from the same list the analysis-type
# picker on the left already uses -- so tab labels ("Pie Chart 1") stay
# consistent with what the user actually selected, not a re-derived guess.
_ANALYSIS_TYPE_NAMES = {opt["id"]: opt["name"] for opt in ANALYSIS_OPTIONS}

# ── Shared design tokens (one source for the compact, modern form) ─────
_STYLE_INPUT = (
    "QComboBox, QSpinBox, QLineEdit {"
    "  background: #1e293b; color: #e2e8f0;"
    "  border: 1px solid #334155; border-radius: 6px;"
    "  padding: 4px 10px; font-size: 12px;"
    "}"
    "QComboBox:hover, QSpinBox:hover, QLineEdit:hover { border-color: #4f46e5; }"
    "QComboBox::drop-down { border: none; width: 22px; }"
    "QComboBox QAbstractItemView {"
    "  background: #1e293b; color: #e2e8f0;"
    "  border: 1px solid #334155;"
    "  selection-background-color: #4f46e5; selection-color: #fff;"
    "}"
)
_STYLE_LABEL = "font-size: 12px; font-weight: 500; color: #cbd5e1;"
_STYLE_GROUP = "font-size: 11px; font-weight: 700; color: #818cf8; letter-spacing: 0.6px;"
_STYLE_HR = "color: #1e293b;"

# Field grouping for a consistent element order across every chart type:
# Data → Options → Appearance.
_GROUP_DATA = {
    "x", "y", "color", "x_col", "y_col", "color_col", "size_col",
    "index_col", "columns_col", "values_col", "lat_col", "lon_col",
    "location_col", "value_col", "geo_col", "dual_y", "dual_y_ts",
}
_GROUP_APPEARANCE = {
    "palette", "map_style", "choropleth_colorscale", "choropleth_projection",
    "choropleth_scope", "render_mode", "invert_colorscale", "show_borders",
    "choropleth_show_borders", "marker_opacity",
}
_FIELD_GROUP = {k: "Data" for k in _GROUP_DATA}
_FIELD_GROUP.update({k: "Appearance" for k in _GROUP_APPEARANCE})

# Field -> which option pool a data picker draws from.
_NUMERIC_ONLY = {"y", "x_col", "y_col", "size_col", "values_col",
                 "lat_col", "lon_col", "value_col"}
_CAT_ONLY = {"color", "color_col", "index_col", "columns_col",
             "location_col", "geo_col"}


def _field_label(key: str) -> str:
    """Human label for a widget key, e.g. 'color_col' -> 'Color Col'."""
    return key.replace("_", " ").title()


class AnalysisPage(QWidget):
    """Analysis page: chart generation and management."""

    navigate_requested = Signal(str)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._active_analysis: Optional[str] = None
        self._chart_cards: dict[str, ChartCard] = {}
        self._tab_order: list[str] = []  # uid per tab index, for restore-selection-by-uid on refresh
        self._config_widgets: dict[str, Any] = {}
        self._editing_uid: Optional[str] = None       # uid being edited, or None for new-chart mode
        self._pending_prefill: Optional[dict] = None   # config snapshot to prefill on next rebuild
        self._suppress_rebuild = False                 # guard recursion when selecting the chart's own type
        self._collect_at_generate: dict = {}           # widget values captured when Generate/Update fired

        self._build_ui()
        self._connect_signals()
        self._refresh_charts()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("📊 Analysis")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #e2e8f0;")
        header.addWidget(title)
        header.addStretch()

        self.btn_clear = QPushButton("🗑 Clear All")
        self.btn_clear.setStyleSheet(
            "QPushButton { background: rgba(239,68,68,0.1); color: #f87171;"
            "  border: 1px solid rgba(239,68,68,0.3); border-radius: 8px;"
            "  padding: 8px 16px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(239,68,68,0.2); }"
        )
        header.addWidget(self.btn_clear)

        self.btn_dashboard = QPushButton("🎯 Proceed to Dashboard →")
        self.btn_dashboard.setStyleSheet(
            "QPushButton { background: #10b981; color: white; border: none;"
            "  border-radius: 8px; padding: 8px 16px; font-weight: 600; }"
            "QPushButton:hover { background: #34d399; }"
        )
        header.addWidget(self.btn_dashboard)
        root.addLayout(header)

        # -- Splitter: left = analysis types, right = content --
        # Same handle styling as Upload/Dashboard splitters -- previously
        # this one had no setHandleWidth/setStyleSheet at all, so it fell
        # back to the native Qt default handle (a thin bar with a small
        # dot-grip icon), which looked inconsistent next to the other two
        # pages' clean colored-bar handles.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #1e293b; }"
            "QSplitter::handle:hover { background: #334155; }"
        )
        root.addWidget(splitter, stretch=1)

        # -- Left: analysis type list --
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(4)

        left_title = QLabel("🔬 Select Analysis Type")
        left_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #cbd5e1;")
        left_layout.addWidget(left_title)

        self.analysis_list = QListWidget()
        self.analysis_list.setStyleSheet(
            "QListWidget {"
            "  background: #0f172a; color: #e2e8f0;"
            "  border: 1px solid #1e293b; border-radius: 8px;"
            "  font-size: 12px;"
            "}"
            "QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #1e293b; }"
            "QListWidget::item:hover { background: rgba(99,102,241,0.1); }"
            "QListWidget::item:selected { background: rgba(99,102,241,0.2); }"
        )
        for opt in ANALYSIS_OPTIONS:
            item = QListWidgetItem(f"{opt['icon']}  {opt['name']}")
            item.setData(Qt.UserRole, opt["id"])
            item.setToolTip(opt["desc"])
            self.analysis_list.addItem(item)
        left_layout.addWidget(self.analysis_list, stretch=1)

        splitter.addWidget(left_panel)

        # -- Right: config + charts --
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        # Config panel (hidden until an analysis is selected). The outer
        # layout holds a rebuildable content area plus a *persistent* footer
        # button bar. The footer is created once here and never rebuilt, so
        # the Generate button can never duplicate no matter how many times the
        # fields above it are swapped out for a different analysis type.
        self.config_panel = QFrame()
        self.config_panel.setObjectName("config_panel")
        self.config_panel.setStyleSheet(
            "QFrame#config_panel {"
            "  background: #0f172a; border: 1px solid #1e293b;"
            "  border-radius: 8px;"
            "}"
        )
        config_outer = QVBoxLayout(self.config_panel)
        config_outer.setContentsMargins(12, 12, 12, 12)
        config_outer.setSpacing(8)

        # Rebuildable content area (moved field widgets live here).
        self.config_layout = QVBoxLayout()
        self.config_layout.setSpacing(6)
        config_outer.addLayout(self.config_layout)

        # Persistent footer: Cancel (edit mode) + Generate/Update button.
        self.config_footer = QWidget()
        footer_lay = QHBoxLayout(self.config_footer)
        footer_lay.setContentsMargins(0, 0, 0, 0)
        footer_lay.setSpacing(8)
        self.btn_cancel_edit = QPushButton("✕ Cancel")
        self.btn_cancel_edit.setVisible(False)
        self.btn_cancel_edit.setStyleSheet(
            "QPushButton { background: rgba(148,163,184,0.12); color: #cbd5e1;"
            "  border: 1px solid #334155; border-radius: 8px; padding: 8px 16px;"
            "  font-weight: 600; }"
            "QPushButton:hover { background: rgba(148,163,184,0.25); }"
        )
        footer_lay.addWidget(self.btn_cancel_edit)
        footer_lay.addStretch()
        self.btn_generate = QPushButton("▶ Generate Charts")
        self.btn_generate.setStyleSheet(
            "QPushButton { background: #4f46e5; color: white; border: none;"
            "  border-radius: 8px; padding: 8px 20px; font-weight: 600; }"
            "QPushButton:hover { background: #6366f1; }"
        )
        footer_lay.addWidget(self.btn_generate)
        config_outer.addWidget(self.config_footer)

        self.config_panel.setVisible(False)
        right_layout.addWidget(self.config_panel)

        # -- Generated charts section header (visual separation from config) --
        charts_header = QHBoxLayout()
        charts_header.setSpacing(8)
        self.lbl_charts_header = QLabel("📈 Generated Charts (0)")
        self.lbl_charts_header.setStyleSheet(
            "font-size: 14px; font-weight: 700; color: #e2e8f0;"
        )
        charts_header.addWidget(self.lbl_charts_header)
        header_hr = QFrame()
        header_hr.setFrameShape(QFrame.HLine)
        header_hr.setStyleSheet(_STYLE_HR)
        charts_header.addWidget(header_hr, stretch=1)
        right_layout.addLayout(charts_header)

        # Charts tab widget -- one tab per generated chart, titled with
        # the chart type + a running count ("Pie Chart 1", "Pie Chart 2"),
        # or the exact custom title if the user renamed a chart. Was a
        # simple vertical stack of cards before; a stack of 5+ full-size
        # charts meant a lot of scrolling to compare or find one.
        self.charts_tabs = QTabWidget()
        self.charts_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #1e293b; border-radius: 6px;"
            "  background: #0f172a; top: -1px; }"
            "QTabBar::tab {"
            "  background: #1e293b; color: #94a3b8;"
            "  border: 1px solid #334155; border-bottom: none;"
            "  border-top-left-radius: 6px; border-top-right-radius: 6px;"
            "  padding: 6px 14px; font-size: 12px; margin-right: 2px;"
            "}"
            "QTabBar::tab:selected { background: #0f172a; color: #e2e8f0;"
            "  border-top: 2px solid #4f46e5; }"
            "QTabBar::tab:hover { color: #e2e8f0; }"
            "QTabBar::close-button { subcontrol-position: right;"
            "  image: none; width: 14px; height: 14px; margin: 0 2px 0 8px;"
            "  border-radius: 3px; }"
            "QTabBar::close-button:hover { background: #334155; }"
        )
        # Many charts -> a clean tab/selector system that scrolls instead of
        # overcrowding. Tabs are closable (wired to delete below) and movable
        # so users can order them without opening each chart's card controls.
        self.charts_tabs.setTabsClosable(True)
        self.charts_tabs.setMovable(True)
        self.charts_tabs.setUsesScrollButtons(True)
        right_layout.addWidget(self.charts_tabs, stretch=1)

        self.lbl_no_charts = QLabel("No charts yet. Configure an analysis and click Generate.")
        self.lbl_no_charts.setAlignment(Qt.AlignCenter)
        self.lbl_no_charts.setStyleSheet("font-size: 13px; color: #64748b; padding: 40px;")
        right_layout.addWidget(self.lbl_no_charts)

        splitter.addWidget(right_panel)
        splitter.setSizes([220, 800])

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.analysis_list.currentItemChanged.connect(self._on_analysis_selected)
        self.btn_generate.clicked.connect(self._on_generate)
        self.btn_clear.clicked.connect(self._on_clear_all)
        self.btn_dashboard.clicked.connect(
            lambda: self.navigate_requested.emit("dashboard")
        )
        self.bridge.charts_changed.connect(self._on_charts_changed)
        self.btn_cancel_edit.clicked.connect(self._on_cancel_edit)
        self.charts_tabs.tabCloseRequested.connect(self._on_tab_close)

    # -- Analysis selection -------------------------------------------------

    def _on_analysis_selected(self, current: QListWidgetItem, previous) -> None:
        if current is None:
            return
        # A fresh manual selection always exits edit mode and starts clean.
        # The edit path explicitly re-builds with edit chrome afterwards.
        if not self._suppress_rebuild:
            self._editing_uid = None
            aid = current.data(Qt.UserRole)
            self._build_config_panel(aid, prefill=self._pending_prefill)
            self._pending_prefill = None

    # -- Config panel construction -----------------------------------------

    @staticmethod
    def _clear_layout(layout) -> None:
        """Recursively delete everything in a QLayout -- widgets AND nested
        layouts. The old teardown only took top-level widgets via takeAt(), so
        every nested sub-layout (multi-select column, button row) survived a
        rebuild and the config panel accumulated orphaned fields + extra
        Generate buttons each time you switched analysis type."""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            child = item.layout()
            if child is not None:
                AnalysisPage._clear_layout(child)

    def _styled_combo(self, items) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setStyleSheet(_STYLE_INPUT)
        combo.setMinimumHeight(30)
        return combo

    def _scalar_options(self, key: str, num, cat, all_cols) -> list:
        """Option list for a single-select field, mirroring the backend's
        per-key column pools (numeric-only / cat-only / both)."""
        NONE = "None"
        if key in ("x", "color", "y", "x_col", "y_col", "color_col",
                   "size_col", "index_col", "columns_col", "values_col",
                   "lat_col", "lon_col", "location_col", "value_col",
                   "geo_col", "dual_y", "dual_y_ts"):
            if key in _NUMERIC_ONLY:
                return [NONE] + num
            if key in _CAT_ONLY:
                return [NONE] + cat
            return [NONE] + num + cat
        if key == "palette":
            return list(PALETTES.keys())
        if key in ("agg", "agg_func", "dual_y_agg"):
            return ["Avg", "Sum", "Median", "Count", "Min", "Max"]
        if key in ("sort", "sort_rows"):
            return ["Value \u2193", "Value \u2191", "Category A\u2192Z", "Category Z\u2192A"]
        if key == "direction":
            return ["Vertical (Column chart)", "Horizontal (Bar chart)"]
        if key == "date_part":
            return ["None", "Year", "Quarter", "Month (number)", "Month Name",
                    "Weekday Name", "Day", "Hour"]
        if key == "trendline":
            return ["None", "ols", "lowess"]
        if key == "render_mode":
            return ["svg", "webgl"]
        if key == "map_mode":
            return ["Scatter (Lat/Lon)", "Choropleth (Location Names)"]
        if key == "map_style":
            return ["carto-positron", "open-street-map", "carto-darkmatter"]
        if key == "choropleth_colorscale":
            return ["Blues", "Reds", "Greens", "Viridis", "Plasma",
                    "Cividis", "Magma", "Inferno"]
        if key == "choropleth_projection":
            return ["natural earth", "equirectangular", "mercator",
                    "orthographic", "azimuthal equal area"]
        if key == "choropleth_scope":
            return ["world", "usa", "europe", "asia", "africa",
                    "north america", "south america"]
        return [NONE] + num + cat

    def _add_group_header(self, name: str) -> None:
        hr = QFrame()
        hr.setFrameShape(QFrame.HLine)
        hr.setStyleSheet(_STYLE_HR)
        lab = QLabel(name.upper())
        lab.setStyleSheet(_STYLE_GROUP)
        lay = QHBoxLayout()
        lay.setSpacing(8)
        lay.addWidget(lab)
        lay.addWidget(hr, stretch=1)
        self.config_layout.addLayout(lay)

    def _add_field_row(self, label_text: str, widget) -> None:
        """Compact single row: fixed-width label column + control."""
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(_STYLE_LABEL)
        lbl.setMinimumWidth(130)
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        self.config_layout.addLayout(row)

    def _prefill_widgets(self, values: dict) -> None:
        """Pre-populate every widget from a previously saved config snapshot
        so editing a chart only requires changing what the user wants."""
        for key, widget in self._config_widgets.items():
            val = values.get(key)
            if val is None:
                continue
            if isinstance(widget, QComboBox):
                idx = widget.findText(str(val))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            elif isinstance(widget, QListWidget):
                valset = set(val) if not isinstance(val, str) else {val}
                for i in range(widget.count()):
                    item = widget.item(i)
                    if item.data(Qt.UserRole) in valset:
                        item.setSelected(True)
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(val))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(val))

    def _build_config_panel(self, aid: str, prefill: Optional[dict] = None) -> None:
        """Build the config form for the selected analysis type.

        Fields are rendered in a consistent Data → Options → Appearance
        order with shared styling so every chart type looks like the same
        product. *prefill* (an optional saved config snapshot) pre-selects
        the widgets so editing an existing chart starts where it left off.
        """
        self._clear_layout(self.config_layout)
        self._config_widgets.clear()
        self._active_analysis = aid

        df = self.bridge.df
        if df is None:
            self.config_panel.setVisible(False)
            return
        self.config_panel.setVisible(True)

        num = num_cols(df)
        cat = cat_cols(df)
        dt = dt_cols(df)
        all_cols = df.columns.tolist()
        NONE = "None"

        # Title (edit mode swaps chrome: label, accent colour, button text)
        opt = next((o for o in ANALYSIS_OPTIONS if o["id"] == aid), None)
        name = opt["name"] if opt else aid
        if self._editing_uid:
            title = QLabel("\u270f\ufe0f  Edit \u2014 " + name)
            title.setStyleSheet("font-size: 14px; font-weight: 700; color: #818cf8;")
            self.btn_generate.setText("\U0001f4be Update Chart")
            self.btn_cancel_edit.setVisible(True)
        else:
            title = QLabel("\u2699\ufe0f  Configure \u2014 " + name)
            title.setStyleSheet("font-size: 14px; font-weight: 700; color: #e2e8f0;")
            self.btn_generate.setText("\u25b6 Generate Charts")
            self.btn_cancel_edit.setVisible(False)
        self.config_layout.addWidget(title)

        spec = WIDGET_SPEC.get(aid, [])
        current_group: Optional[str] = None
        for widget_key, _kwarg, kind in spec:
            group = _FIELD_GROUP.get(widget_key, "Options")
            if group != current_group:
                current_group = group
                self._add_group_header(group)
            label_text = _field_label(widget_key)

            if kind in ("scalar", "scalar_map", "palette"):
                combo = self._styled_combo(
                    self._scalar_options(widget_key, num, cat, all_cols)
                )
                self._config_widgets[widget_key] = combo
                self._add_field_row(label_text, combo)

            elif kind == "list":
                # Multi-select column bar on its own row so it gets real width.
                pool = num if widget_key in ("x", "y", "x_cols", "y_cols") else all_cols
                lw = QListWidget()
                lw.setSelectionMode(QListWidget.MultiSelection)
                lw.setMinimumHeight(110)
                lw.setMaximumHeight(150)
                lw.setStyleSheet(
                    "QListWidget { background: #1e293b; color: #e2e8f0;"
                    "  border: 1px solid #334155; border-radius: 6px;"
                    "  font-size: 12px; padding: 2px; }"
                    "QListWidget::item { padding: 4px 8px; border-radius: 4px; }"
                    "QListWidget::item:hover { background: #334155; }"
                    "QListWidget::item:selected { background: #4f46e5; color: white; }"
                )
                for col in pool:
                    item = QListWidgetItem(str(col))
                    item.setData(Qt.UserRole, col)
                    lw.addItem(item)
                self._config_widgets[widget_key] = lw

                list_col = QVBoxLayout()
                list_col.setSpacing(4)
                lab = QLabel(label_text)
                lab.setStyleSheet(_STYLE_LABEL)
                list_col.addWidget(lab)
                list_col.addWidget(lw)
                self.config_layout.addLayout(list_col)

            elif kind == "number":
                spin = QSpinBox()
                spin.setRange(0, 500)
                spin.setValue(0)
                spin.setStyleSheet(_STYLE_INPUT)
                spin.setMinimumHeight(30)
                self._config_widgets[widget_key] = spin
                self._add_field_row(label_text, spin)

            elif kind == "bool":
                chk = QCheckBox()
                chk.setStyleSheet("color: #e2e8f0; font-size: 12px;")
                self._config_widgets[widget_key] = chk
                self._add_field_row(label_text, chk)

        if prefill:
            self._prefill_widgets(prefill)
    def _collect_widget_values(self) -> dict:
        """Collect current widget values into a dict for the analysis service."""
        values = {}
        for key, widget in self._config_widgets.items():
            if isinstance(widget, QComboBox):
                values[key] = widget.currentText()
            elif isinstance(widget, QListWidget):
                selected = [item.data(Qt.UserRole) for item in widget.selectedItems()]
                values[key] = selected
            elif isinstance(widget, QSpinBox):
                values[key] = widget.value()
            elif isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
        return values

    def _on_generate(self) -> None:
        """Generate (or update) charts for the active analysis type."""
        if self._active_analysis is None:
            return
        df = self.bridge.df
        if df is None:
            QMessageBox.warning(self, "No Data", "Please upload a dataset first.")
            return

        values = self._collect_widget_values()
        # Snapshot the exact values so the finished-handler can persist them
        # as the chart's config (needed to prefill its form if edited later).
        self._collect_at_generate = dict(values)

        # Disable the generate button while working
        self.btn_generate.setEnabled(False)
        self.btn_generate.setText("⏳ Working…")

        # Use a background worker to avoid blocking the UI
        self._gen_worker = ChartGenerationWorker(
            self._active_analysis, df, values, self
        )
        self._gen_worker.finished_ok.connect(self._on_generate_finished)
        self._gen_worker.failed.connect(self._on_generate_failed)
        self._gen_worker.start()

    def _on_generate_finished(self, new_charts) -> None:
        """Handle successful chart generation/update from the worker."""
        editing = self._editing_uid is not None
        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("\ud83d\udcbe Update Chart" if editing
                                  else "\u25b6 Generate Charts")

        if not new_charts:
            QMessageBox.warning(self, "No Charts",
                                "No charts were generated. Check your configuration.")
            return

        values = self._collect_at_generate
        if editing:
            # Replace the edited chart in place with the first regenerated
            # figure (a multi-select config may produce more than one; those
            # extras become brand-new charts rather than being lost).
            edit_uid = self._editing_uid
            first_uid, first_title, first_fig = new_charts[0]
            self.bridge.set_chart_meta(edit_uid, config=values)
            self.bridge.replace_chart(edit_uid, first_fig, first_title)
            for uid2, title2, fig2 in new_charts[1:]:
                self.bridge.add_chart(uid2, title2, fig2,
                                      chart_type=self._active_analysis,
                                      meta={"config": dict(values)})
            self._editing_uid = None
            self._refresh_charts()
            self._select_chart_tab(edit_uid)
        else:
            # Always append -- generating the same type again with different
            # values creates a new chart instead of overwriting work.
            for uid, title, fig in new_charts:
                self.bridge.add_chart(uid, title, fig,
                                      chart_type=self._active_analysis,
                                      meta={"config": dict(values)})
                self.bridge.set(f"chart_type_{uid}", self._active_analysis)
            self._refresh_charts()

        self.bridge.save_draft()

    def _on_generate_failed(self, error: str) -> None:
        """Handle chart generation failure from the worker."""
        editing = self._editing_uid is not None
        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("\ud83d\udcbe Update Chart" if editing
                                  else "\u25b6 Generate Charts")
        log.error("Generate failed: %s", error)
        QMessageBox.critical(self, "Generation Error", error)

    # -- Chart management ---------------------------------------------------

    def _chart_tab_label(self, title: str, chart_type: str, type_counter: dict) -> str:
        """Tab label for a chart: its own title if it has one (chart
        titles are already descriptive by default, e.g. "Mean Revenue by
        Region" -- not bare type names), else "{Friendly Type} {n}" as a
        fallback, numbered per chart type so multiple untitled charts of
        the same type stay distinguishable ("Pie Chart 1", "Pie Chart 2")."""
        if title and title.strip():
            label = title.strip()
            return label if len(label) <= 28 else label[:26] + "…"
        type_counter[chart_type] = type_counter.get(chart_type, 0) + 1
        friendly = _ANALYSIS_TYPE_NAMES.get(chart_type, chart_type.replace("_", " ").title() or "Chart")
        return f"{friendly} {type_counter[chart_type]}"

    def _refresh_charts(self) -> None:
        """Rebuild the chart tabs from the session bridge."""
        # Remember which chart was selected so regenerating/adding a
        # chart doesn't always bounce the view back to the first tab --
        # tracked by uid, not index, since index alone doesn't survive
        # charts being added/removed/reordered.
        prev_uid = None
        cur_idx = self.charts_tabs.currentIndex()
        if 0 <= cur_idx < len(self._tab_order):
            prev_uid = self._tab_order[cur_idx]

        for card in self._chart_cards.values():
            card.deleteLater()
        self._chart_cards.clear()

        while self.charts_tabs.count():
            w = self.charts_tabs.widget(0)
            self.charts_tabs.removeTab(0)
            if w:
                w.deleteLater()

        charts = self.bridge.get("charts", [])
        self.lbl_no_charts.setVisible(len(charts) == 0)
        self.charts_tabs.setVisible(len(charts) > 0)
        # Keep the section header + count in sync (hidden when nothing yet).
        self.lbl_charts_header.setText(f"📈 Generated Charts ({len(charts)})")
        self.lbl_charts_header.setVisible(len(charts) > 0)

        self._tab_order = []
        type_counter: dict = {}
        restore_idx = 0
        for i, (uid, title, fig) in enumerate(charts):
            chart_type = self.bridge.get(f"chart_type_{uid}", "")
            meta = self.bridge.get_chart_meta(uid)
            note = self.bridge.get_note(uid)

            card = ChartCard(uid, title, fig, chart_type, meta)
            card.set_note(note)
            card.delete_requested.connect(self._on_delete_chart)
            card.edit_requested.connect(self._on_edit_chart)
            card.move_up_requested.connect(self._on_move_up)
            card.move_down_requested.connect(self._on_move_down)
            card.note_changed.connect(self._on_note_changed)
            card.style_changed.connect(self._on_style_changed)

            label = self._chart_tab_label(title, chart_type, type_counter)
            self.charts_tabs.addTab(card, label)
            self._chart_cards[uid] = card
            self._tab_order.append(uid)
            if uid == prev_uid:
                restore_idx = i

        if charts:
            self.charts_tabs.setCurrentIndex(restore_idx)

    def _on_charts_changed(self, charts) -> None:
        """Handle charts_changed signal from the bridge."""
        self._refresh_charts()

    def _on_delete_chart(self, uid: str) -> None:
        """Delete a chart."""
        self.bridge.remove_chart(uid)
        self._refresh_charts()
        self.bridge.save_draft()

    def _on_edit_chart(self, uid: str) -> None:
        """Edit a chart: reopen its chart-generation settings with the
        chart's previous selections pre-filled, so the user only changes
        what they actually want."""
        chart_type = self.bridge.get(f"chart_type_{uid}", "")
        if not chart_type:
            return

        # Enter edit mode and prefill from the stored config snapshot.
        self._editing_uid = uid
        meta = self.bridge.get_chart_meta(uid) or {}
        config = meta.get("config") or {}

        # Select the matching analysis type purely for visual highlight (the
        # guard flag stops its handler from wiping our edit state).
        self._suppress_rebuild = True
        try:
            for i in range(self.analysis_list.count()):
                item = self.analysis_list.item(i)
                if item.data(Qt.UserRole) == chart_type:
                    self.analysis_list.setCurrentItem(item)
                    break
        finally:
            self._suppress_rebuild = False

        self._build_config_panel(chart_type, prefill=config)
        # Drop the focus back to the config card so the user sees the form.
        self.config_panel.setFocus()

    def _on_cancel_edit(self) -> None:
        """Leave edit mode and reset the form to a blank new-chart form."""
        self._editing_uid = None
        if self._active_analysis:
            self._build_config_panel(self._active_analysis)

    def _on_tab_close(self, index: int) -> None:
        """Close a chart via its tab's ✕ button."""
        if 0 <= index < len(self._tab_order):
            self._on_delete_chart(self._tab_order[index])

    def _select_chart_tab(self, uid: str) -> None:
        """Select the tab whose chart uid matches (after a refresh)."""
        try:
            self.charts_tabs.setCurrentIndex(self._tab_order.index(uid))
        except ValueError:
            pass

    def _on_move_up(self, uid: str) -> None:
        """Move a chart up in the list."""
        charts = list(self.bridge.get("charts", []))
        idx = next((i for i, c in enumerate(charts) if c[0] == uid), -1)
        if idx > 0:
            charts[idx - 1], charts[idx] = charts[idx], charts[idx - 1]
            self.bridge.set("charts", charts)
            self._refresh_charts()

    def _on_move_down(self, uid: str) -> None:
        """Move a chart down in the list."""
        charts = list(self.bridge.get("charts", []))
        idx = next((i for i, c in enumerate(charts) if c[0] == uid), -1)
        if 0 <= idx < len(charts) - 1:
            charts[idx], charts[idx + 1] = charts[idx + 1], charts[idx]
            self.bridge.set("charts", charts)
            self._refresh_charts()

    def _on_note_changed(self, uid: str, text: str) -> None:
        """Handle note changes from a chart card."""
        self.bridge.set_note(uid, text)

    def _on_style_changed(self, uid: str, style: dict) -> None:
        """Persist a chart's style settings to the bridge -- same pattern
        as _on_note_changed, so styling survives a session save instead
        of only living on the in-memory ChartCard instance."""
        self.bridge.set_chart_meta(uid, display_options=style)

    def _on_clear_all(self) -> None:
        """Clear all charts."""
        reply = QMessageBox.question(
            self, "Clear All Charts",
            "Remove all generated charts?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.bridge.clear_charts()
            self._refresh_charts()
            self.bridge.save_draft()

    # -- Public API ----------------------------------------------------------

    def refresh(self) -> None:
        """Refresh the page when navigated to."""
        self._refresh_charts()