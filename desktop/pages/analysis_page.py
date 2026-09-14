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

from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from desktop.widgets.chart_legend_panel import ChartLegendPanel
from desktop.widgets.analysis_config_panel import AnalysisConfigPanel
from desktop.widgets.analysis_notes_bar import AnalysisNotesBar
from desktop.widgets.chart_card import ChartCard
from desktop.workers import ChartGenerationWorker
from services.analysis_service import (
    ANALYSIS_OPTIONS, WIDGET_SPEC, run_analysis_with_config, run_analysis,
    num_cols, cat_cols, dt_cols,
)
from services.chart_service import PALETTES, apply_hover_format

log = logging.getLogger(__name__)

# id -> friendly display name, built from the same list the analysis-type
# picker on the left already uses -- so tab labels ("Pie Chart 1") stay
# consistent with what the user actually selected, not a re-derived guess.
_ANALYSIS_TYPE_NAMES = {opt["id"]: opt["name"] for opt in ANALYSIS_OPTIONS}

# ── Shared design tokens (one source for the compact, modern form) ─────

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

class _TabPane(QWidget):
    """Per-tab container: ChartCard (auto-fit) + LegendPanel (fixed) side by
    side, so clicking a tab shows the full chart AND its legend together
    without any scrolling."""

    def __init__(self, card: ChartCard, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.card = card
        self.legend = ChartLegendPanel()
        lay.addWidget(card, stretch=1)
        lay.addWidget(self.legend)

    def update_legend(self) -> None:
        self.legend.set_legend(self.card.legend_data())

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
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("📊 Analysis")
        title.setProperty("class", "pageTitle")
        header.addWidget(title)
        header.addStretch()

        self.btn_clear = QPushButton("🗑 Clear All")
        self.btn_clear.setProperty("kind", "danger")
        header.addWidget(self.btn_clear)

        self.btn_dashboard = QPushButton("🎯 Proceed to Dashboard →")
        self.btn_dashboard.setProperty("kind", "success")
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

        root.addWidget(splitter, stretch=1)

        # -- Left: analysis type list --
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(4)

        left_title = QLabel("🔬 Select Analysis Type")
        left_title.setProperty("class", "muted"); left_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        left_layout.addWidget(left_title)

        self.analysis_list = QListWidget()
        self.analysis_list.setStyleSheet("font-size: 12px;")
        for opt in ANALYSIS_OPTIONS:
            item = QListWidgetItem(f"{opt['icon']}  {opt['name']}")
            item.setData(Qt.UserRole, opt["id"])
            item.setToolTip(opt["desc"])
            self.analysis_list.addItem(item)
        left_layout.addWidget(self.analysis_list, stretch=1)

        splitter.addWidget(left_panel)

        # -- Right: vertical splitter (TOP compact config | BOTTOM preview) --
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(0)

        self._v_split = QSplitter(Qt.Vertical)
        self._v_split.setHandleWidth(6)
        self._v_split.setChildrenCollapsible(False)
        # Same colored-bar handle styling as the outer splitter / other pages.
        self._v_split.setStyleSheet(
            "QSplitter::handle { background: #1e293b; }"
            "QSplitter::handle:hover { background: #334155; }"
        )

        # TOP pane: tabbed config shell (tabs only in this phase).
        self.config_panel = AnalysisConfigPanel()
        self.config_panel.collapse_toggled.connect(self._on_collapse_toggled)
        self.config_panel.setVisible(False)
        self._v_split.addWidget(self.config_panel)

        # BOTTOM pane: tabs strip + chart preview + notes footer
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # Tabs strip (fixed height) -- one full-view chart per tab.
        self.tabs_row = QWidget()
        self.tabs_row.setFixedHeight(42)
        tr = QHBoxLayout(self.tabs_row)
        tr.setContentsMargins(6, 4, 6, 4)
        tr.setSpacing(6)
        self.charts_tabs = QTabWidget()
        self.charts_tabs.setTabsClosable(True)
        self.charts_tabs.setMovable(True)
        self.charts_tabs.setUsesScrollButtons(True)
        self.charts_tabs.tabCloseRequested.connect(self._on_tab_close)
        self.charts_tabs.currentChanged.connect(self._on_tab_changed)
        tr.addWidget(self.charts_tabs, stretch=1)
        # Chart add/delete live next to Generate, never in the config tabs.
        self.btn_add_chart = QPushButton("+")
        self.btn_add_chart.setFixedSize(28, 28)
        self.btn_add_chart.setToolTip("Add another chart of this type")
        self.btn_add_chart.setProperty("kind", "icon")
        self.btn_add_chart.clicked.connect(self._on_add_chart)
        tr.addWidget(self.btn_add_chart)
        self.btn_remove_chart = QPushButton("x")
        self.btn_remove_chart.setFixedSize(28, 28)
        self.btn_remove_chart.setToolTip("Delete selected chart")
        self.btn_remove_chart.setProperty("kind", "icon")
        self.btn_remove_chart.clicked.connect(self._on_delete_current)
        tr.addWidget(self.btn_remove_chart)
        self.btn_generate = QPushButton("▶ Generate Charts")
        self.btn_generate.setProperty("kind", "primary")
        self.btn_generate.setMinimumHeight(30)
        self.btn_generate.clicked.connect(self._on_generate)
        tr.addWidget(self.btn_generate)
        bl.addWidget(self.tabs_row)

        # Chart preview = the tab widget (each tab pane holds card + legend).
        bl.addWidget(self.charts_tabs, stretch=1)

        # Descriptive-only full-area table view.
        self.descriptive_container = QWidget()
        self.descriptive_container_layout = QVBoxLayout(self.descriptive_container)
        self.descriptive_container_layout.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.descriptive_container, stretch=1)

        # No-charts placeholder.
        self.lbl_no_charts = QLabel("No charts yet. Configure an analysis and click Generate.")
        self.lbl_no_charts.setAlignment(Qt.AlignCenter)
        self.lbl_no_charts.setProperty("class", "faint"); self.lbl_no_charts.setStyleSheet("font-size: 13px; padding: 40px;")
        bl.addWidget(self.lbl_no_charts, stretch=1)

        # Notes footer (fixed height, per-analysis-type notes).
        self.notes_bar = AnalysisNotesBar(self.bridge)
        self.notes_bar.note_changed.connect(self._on_analysis_note_changed)
        bl.addWidget(self.notes_bar)

        self._v_split.addWidget(bottom)
        self._v_split.setStretchFactor(0, 0)
        self._v_split.setStretchFactor(1, 1)
        self._v_split.setSizes([296, 544])
        self.config_panel.setMinimumHeight(48)
        bottom.setMinimumHeight(248)

        right_layout.addWidget(self._v_split)
        splitter.addWidget(right_panel)
        splitter.setSizes([220, 800])

        # Initial view: no charts yet -> hide tabs & descriptive.
        self.tabs_row.setVisible(False)
        self.charts_tabs.setVisible(False)
        self.descriptive_container.setVisible(False)
        self.notes_bar.setVisible(False)

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
        # A fresh manual selection always exits edit mode and starts clean.
        # The edit path explicitly re-builds with edit chrome afterwards.
        if not self._suppress_rebuild:
            self._editing_uid = None
            aid = current.data(Qt.UserRole)
            self._build_config_panel(aid, prefill=self._pending_prefill)
            self._pending_prefill = None

    # -- Config panel construction -----------------------------------------

    def _build_config_panel(self, aid: str, prefill: Optional[dict] = None) -> None:
        """Build the config form for the selected analysis type.

        Descriptive is special-cased: it is a pure data overview that does
        NOT belong on the dashboard, so it shows only a clean full-area
        table (no config form, no Generate, no tabs, no legend, and nothing
        is added to the bridge charts -- so it can never reach a saved
        dashboard). Everything else renders through the compact 3-column
        AnalysisConfigPanel in the splitter's top pane.
        """
        if aid == "descriptive":
            self._show_descriptive()
            return

        self._active_analysis = aid
        df = self.bridge.df
        if df is None:
            self.config_panel.setVisible(False)
            return
        self._config_widgets = {}

        # Refresh column pools for the injected scalar-options closure.
        self._num = num_cols(df)
        self._cat = cat_cols(df)
        self._all_cols = df.columns.tolist()

        opt = next((o for o in ANALYSIS_OPTIONS if o["id"] == aid), None)
        name = opt["name"] if opt else aid
        editing = self._editing_uid is not None

        self.config_panel.set_logic(
            spec=WIDGET_SPEC.get(aid, []),
            scalar_options_fn=self._column_scalar_options,
            field_group_fn=self._field_group_of,
        )
        self.config_panel.set_active(aid, name, editing=editing)
        if prefill:
            self.config_panel.prefill(prefill)
        self.config_panel.setVisible(True)

        self._update_config_chart_no()
        self.notes_bar.set_analysis_type(aid)
        self._show_chart_area(False)

    def _update_config_chart_no(self) -> None:
        n = 1
        if self._editing_uid:
            idx = self.charts_tabs.currentIndex()
            n = idx + 1 if idx >= 0 else 1
        else:
            n = len(self._tab_order) + 1
        self.config_panel.set_chart_number(n)

    def _column_scalar_options(self, key: str) -> list:
        NONE = "None"
        if key in _NUMERIC_ONLY:
            return [NONE] + self._num
        if key in _CAT_ONLY:
            return [NONE] + self._cat
        return [NONE] + self._num + self._cat

    def _field_group_of(self, key: str) -> str:
        return _FIELD_GROUP.get(key, "Options")

    def _show_descriptive(self) -> None:
        self._active_analysis = "descriptive"
        self.config_panel.setVisible(False)
        self._refresh_descriptive_table()
        self._show_chart_area(True)

    def _refresh_descriptive_table(self) -> None:
        df = self.bridge.df
        if df is None:
            return
        while self.descriptive_container_layout.count():
            item = self.descriptive_container_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        try:
            results = run_analysis("descriptive", df)
        except Exception as exc:  # pragma: no cover
            log.error("Descriptive run failed: %s", exc)
            results = []
        if results:
            uid, title, fig = results[0]
            card = ChartCard(uid, title, fig, "descriptive")
            card.set_note("")
            for btn in (card.btn_edit, card.btn_style, card.btn_delete,
                        card.btn_up, card.btn_down):
                btn.setVisible(False)
            self.descriptive_container_layout.addWidget(card, stretch=1)

    def _show_chart_area(self, descriptive: bool) -> None:
        has = len(self.bridge.get("charts", [])) > 0
        self.tabs_row.setVisible(not descriptive and has)
        self.charts_tabs.setVisible(not descriptive and has)
        self.descriptive_container.setVisible(descriptive)
        self.lbl_no_charts.setVisible(not descriptive and not has)
        self.notes_bar.setVisible(not descriptive)

    def _collect_widget_values(self) -> dict:
        return self.config_panel.collect_values()

    # -- Config panel hooks (signals) -------------------------------------

    def _on_add_chart(self) -> None:
        """[+] -> start a fresh blank chart of the current type."""
        aid = self._active_analysis
        if not aid or aid == "descriptive":
            return
        self._editing_uid = None
        self._build_config_panel(aid)

    def _on_delete_current(self) -> None:
        """[x] -> delete the currently-selected chart."""
        idx = self.charts_tabs.currentIndex()
        if 0 <= idx < len(self._tab_order):
            self._on_delete_chart(self._tab_order[idx])

    def _on_collapse_toggled(self, collapsed: bool) -> None:
        if not self.config_panel.isVisible():
            return
        sizes = list(self._v_split.sizes())
        sizes[0] = 40 if collapsed else 296
        self._v_split.setSizes(sizes)

    def _on_analysis_note_changed(self, text: str) -> None:
        aid = self._active_analysis or ""
        self.bridge.set(f"analysis_note__{aid}", text)

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
            pane = _TabPane(card)
            pane.update_legend()
            self.charts_tabs.addTab(pane, label)
            self._tab_order.append(uid)
            self._chart_cards[uid] = card
            if uid == prev_uid:
                restore_idx = i

        # Keep chart-area visibility in sync (descriptive hides them).
        descriptive = self._active_analysis == "descriptive"
        self.tabs_row.setVisible((not descriptive) and len(charts) > 0)
        self.charts_tabs.setVisible((not descriptive) and len(charts) > 0)
        self.lbl_no_charts.setVisible((not descriptive) and len(charts) == 0)

        if charts:
            self.charts_tabs.setCurrentIndex(restore_idx)
        self._update_current_legend()

    def _on_charts_changed(self, charts) -> None:
        """Handle charts_changed signal from the bridge."""
        self._refresh_charts()

    def _on_tab_changed(self) -> None:
        """Refresh the legend when the user switches charts (each tab pane
        holds its own ChartCard + LegendPanel)."""
        self._update_current_legend()

    def _update_current_legend(self) -> None:
        w = self.charts_tabs.currentWidget()
        if isinstance(w, _TabPane):
            w.update_legend()

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