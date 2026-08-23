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

        # Config panel (hidden until an analysis is selected)
        self.config_panel = QFrame()
        self.config_panel.setObjectName("config_panel")
        self.config_panel.setStyleSheet(
            "QFrame#config_panel {"
            "  background: #0f172a; border: 1px solid #1e293b;"
            "  border-radius: 8px;"
            "}"
        )
        self.config_layout = QVBoxLayout(self.config_panel)
        self.config_layout.setContentsMargins(12, 12, 12, 12)
        self.config_layout.setSpacing(6)
        self.config_panel.setVisible(False)
        right_layout.addWidget(self.config_panel)

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
            "QTabBar::tab:selected { background: #0f172a; color: #e2e8f0; }"
            "QTabBar::tab:hover { color: #e2e8f0; }"
        )
        self.charts_tabs.setTabsClosable(False)  # closing goes through the card's own delete button
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
        self.btn_clear.clicked.connect(self._on_clear_all)
        self.btn_dashboard.clicked.connect(
            lambda: self.navigate_requested.emit("dashboard")
        )
        self.bridge.charts_changed.connect(self._on_charts_changed)

    # -- Analysis selection -------------------------------------------------

    def _on_analysis_selected(self, current: QListWidgetItem, previous) -> None:
        if current is None:
            return
        aid = current.data(Qt.UserRole)
        self._active_analysis = aid
        self._build_config_panel(aid)

    def _build_config_panel(self, aid: str) -> None:
        """Build the config widgets for the selected analysis type."""
        # Clear existing config widgets
        while self.config_layout.count():
            item = self.config_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._config_widgets.clear()

        df = self.bridge.df
        if df is None:
            self.config_panel.setVisible(False)
            return

        num = num_cols(df)
        cat = cat_cols(df)
        dt = dt_cols(df)
        all_cols = df.columns.tolist()
        NONE = "None"

        # Title
        opt = next((o for o in ANALYSIS_OPTIONS if o["id"] == aid), None)
        title = QLabel(f"⚙️ Configure — {opt['name'] if opt else aid}")
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #e2e8f0;")
        self.config_layout.addWidget(title)

        # Build widgets based on WIDGET_SPEC
        spec = WIDGET_SPEC.get(aid, [])
        for widget_key, _kwarg, kind in spec:
            row = QHBoxLayout()
            row.setSpacing(8)

            label = QLabel(widget_key.replace("_", " ").title())
            label.setStyleSheet("font-size: 12px; color: #94a3b8; min-width: 100px;")
            row.addWidget(label)

            if kind == "scalar":
                # Single-select combo
                if widget_key in ("x", "color", "y", "x_col", "y_col", "color_col",
                                  "size_col", "index_col", "columns_col", "values_col",
                                  "lat_col", "lon_col", "location_col", "value_col",
                                  "geo_col", "dual_y", "dual_y_ts"):
                    options = [NONE] + num if widget_key in (
                        "y", "x_col", "y_col", "size_col", "values_col",
                        "lat_col", "lon_col", "value_col",
                    ) else [NONE] + cat if widget_key in (
                        "color", "color_col", "index_col", "columns_col",
                        "location_col", "geo_col",
                    ) else [NONE] + num + cat
                    combo = QComboBox()
                    combo.addItems(options)
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "palette":
                    combo = QComboBox()
                    combo.addItems(list(PALETTES.keys()))
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key in ("agg", "agg_func", "dual_y_agg"):
                    combo = QComboBox()
                    combo.addItems(["Avg", "Sum", "Median", "Count", "Min", "Max"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key in ("sort", "sort_rows"):
                    combo = QComboBox()
                    combo.addItems(["Value ↓", "Value ↑", "Category A→Z", "Category Z→A"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "direction":
                    combo = QComboBox()
                    combo.addItems(["Vertical (Column chart)", "Horizontal (Bar chart)"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "date_part":
                    combo = QComboBox()
                    combo.addItems(["None", "Year", "Quarter", "Month (number)",
                                    "Month Name", "Weekday Name", "Day", "Hour"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "trendline":
                    combo = QComboBox()
                    combo.addItems(["None", "ols", "lowess"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "render_mode":
                    combo = QComboBox()
                    combo.addItems(["svg", "webgl"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "map_mode":
                    combo = QComboBox()
                    combo.addItems(["Scatter (Lat/Lon)", "Choropleth (Location Names)"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "map_style":
                    combo = QComboBox()
                    combo.addItems(["carto-positron", "open-street-map", "carto-darkmatter"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key in ("choropleth_colorscale",):
                    combo = QComboBox()
                    combo.addItems(["Blues", "Reds", "Greens", "Viridis", "Plasma",
                                    "Cividis", "Magma", "Inferno"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "choropleth_projection":
                    combo = QComboBox()
                    combo.addItems(["natural earth", "equirectangular", "mercator",
                                    "orthographic", "azimuthal equal area"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)
                elif widget_key == "choropleth_scope":
                    combo = QComboBox()
                    combo.addItems(["world", "usa", "europe", "asia", "africa",
                                    "north america", "south america"])
                    combo.setStyleSheet(
                        "QComboBox { background: #1e293b; color: #e2e8f0;"
                        "  border: 1px solid #334155; border-radius: 6px;"
                        "  padding: 4px 8px; font-size: 12px; }"
                    )
                    self._config_widgets[widget_key] = combo
                    row.addWidget(combo, stretch=1)

            elif kind == "list":
                # Multi-select list. Unlike combo/spin/checkbox fields, a
                # multi-select list needs real vertical room and its own
                # row -- squeezed next to a 100px label in the same
                # QHBoxLayout as every other field type left barely any
                # width for it, and there was no QListWidget::item:selected
                # style at all, so clicking an item fell back to whatever
                # the OS default highlight happens to be (easy to miss
                # entirely against this dark theme, which read as
                # "selection doesn't work" even though it did).
                list_widget = QListWidget()
                list_widget.setMinimumHeight(120)
                list_widget.setMaximumHeight(160)
                list_widget.setSelectionMode(QListWidget.MultiSelection)
                list_widget.setStyleSheet(
                    "QListWidget { background: #1e293b; color: #e2e8f0;"
                    "  border: 1px solid #334155; border-radius: 6px;"
                    "  font-size: 12px; padding: 2px; }"
                    "QListWidget::item { padding: 4px 6px; border-radius: 4px; }"
                    "QListWidget::item:hover { background: #334155; }"
                    "QListWidget::item:selected { background: #4f46e5; color: white; }"
                )
                options = num if widget_key in ("x", "y", "x_cols", "y_cols") else all_cols
                for col in options:
                    item = QListWidgetItem(str(col))
                    item.setData(Qt.UserRole, col)
                    list_widget.addItem(item)
                self._config_widgets[widget_key] = list_widget

                # Full-width row: label on its own line above the list,
                # instead of beside it -- gives the list its actual width
                # back rather than sharing the row with a fixed label column.
                list_col = QVBoxLayout()
                list_col.setSpacing(4)
                list_col.addWidget(label)
                list_col.addWidget(list_widget)
                self.config_layout.addLayout(list_col)
                continue

            elif kind == "number":
                spin = QSpinBox()
                spin.setRange(0, 500)
                spin.setValue(0)
                spin.setStyleSheet(
                    "QSpinBox { background: #1e293b; color: #e2e8f0;"
                    "  border: 1px solid #334155; border-radius: 6px;"
                    "  padding: 4px 8px; font-size: 12px; }"
                )
                self._config_widgets[widget_key] = spin
                row.addWidget(spin, stretch=1)

            elif kind == "bool":
                check = QCheckBox()
                check.setStyleSheet("color: #e2e8f0; font-size: 12px;")
                self._config_widgets[widget_key] = check
                row.addWidget(check, stretch=1)

            self.config_layout.addLayout(row)

        # Generate button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_generate = QPushButton("▶ Generate Charts")
        self.btn_generate.setStyleSheet(
            "QPushButton { background: #4f46e5; color: white; border: none;"
            "  border-radius: 8px; padding: 8px 20px; font-weight: 600; }"
            "QPushButton:hover { background: #6366f1; }"
        )
        self.btn_generate.clicked.connect(self._on_generate)
        btn_row.addWidget(self.btn_generate)
        self.config_layout.addLayout(btn_row)

        self.config_panel.setVisible(True)

    # -- Chart generation ---------------------------------------------------

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
        """Generate charts for the active analysis type."""
        if self._active_analysis is None:
            return
        df = self.bridge.df
        if df is None:
            QMessageBox.warning(self, "No Data", "Please upload a dataset first.")
            return

        values = self._collect_widget_values()

        # Disable the generate button while working
        self.btn_generate.setEnabled(False)
        self.btn_generate.setText("⏳ Generating…")

        # Use a background worker to avoid blocking the UI
        self._gen_worker = ChartGenerationWorker(
            self._active_analysis, df, values, self
        )
        self._gen_worker.finished_ok.connect(self._on_generate_finished)
        self._gen_worker.failed.connect(self._on_generate_failed)
        self._gen_worker.start()

    def _on_generate_finished(self, new_charts) -> None:
        """Handle successful chart generation from the worker."""
        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("▶ Generate Charts")

        if not new_charts:
            QMessageBox.warning(self, "No Charts",
                                "No charts were generated. Check your configuration.")
            return

        for uid, title, fig in new_charts:
            self.bridge.add_chart(uid, title, fig,
                                  chart_type=self._active_analysis)
            self.bridge.set(f"chart_type_{uid}", self._active_analysis)

        self._refresh_charts()
        self.bridge.save_draft()

    def _on_generate_failed(self, error: str) -> None:
        """Handle chart generation failure from the worker."""
        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("▶ Generate Charts")
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
        """Edit a chart (re-select its analysis type)."""
        chart_type = self.bridge.get(f"chart_type_{uid}", "")
        if chart_type:
            # Find and select the analysis type in the list
            for i in range(self.analysis_list.count()):
                item = self.analysis_list.item(i)
                if item.data(Qt.UserRole) == chart_type:
                    self.analysis_list.setCurrentItem(item)
                    break

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