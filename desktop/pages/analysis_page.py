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
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QComboBox, QListWidget, QListWidgetItem,
    QLineEdit, QSpinBox, QCheckBox, QFrame, QMessageBox, QSplitter,
    QStackedWidget, QSizePolicy, QTabWidget,
)

from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from desktop.widgets.analysis_config_panel import AnalysisConfigPanel
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


# Chart deletes have no undo, so every delete entry point asks first. The
# automated suites flip this to False -- QMessageBox.question() blocks on
# user input, which never arrives in an offscreen test run.
CONFIRM_CHART_DELETE = True


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
        self._pending_prefill: Optional[dict] = None   # config snapshot to prefill on next rebuild
        self._suppress_rebuild = False                 # guard recursion when selecting the chart's own type
        self._collect_at_generate: dict = {}           # widget values captured when Generate/Re-Generate fired
        self._collect_layout: dict = {}                # Layout Options snapshot at generate time
        self._collect_typo: dict = {}                  # Typography Options snapshot at generate time
        self._gen_mode = "new"                         # "new" (append) | "regen" (replace in place)
        self._gen_target: Optional[str] = None         # uid to replace when _gen_mode == "regen"
        self._settings_target_uid: Optional[str] = None  # chart the settings form is bound to
        self._refreshing_charts = False                # suppress tab-change binding during rebuilds
        self._bridge_write_guard = False               # suppress charts_changed rebuild during live preview

        # Debounced live preview for Layout/Typography edits (design:
        # "changes appear instantly on the chart").
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._apply_live_preview)

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

        # BOTTOM pane: chart-tab strip + chart preview (one full-view chart
        # per tab). The strip IS the tab bar -- tab labels on the left, the
        # "New chart" action as the bar's top-right corner widget -- so the
        # chart keeps every pixel the old separate 42px icon row used to
        # take (chart_designs.pdf: one compact strip above the tile).
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        self.charts_tabs = QTabWidget()
        self.charts_tabs.setTabsClosable(True)
        self.charts_tabs.setMovable(True)
        self.charts_tabs.setUsesScrollButtons(True)
        # Compact strip: tabs size to their own label instead of stretching
        # across the bar, and long labels elide (never wrap) so adding charts
        # of the same analysis can't push the bar into a second row.
        _tab_bar = self.charts_tabs.tabBar()
        _tab_bar.setExpanding(False)
        _tab_bar.setElideMode(Qt.ElideRight)
        _tab_bar.tabMoved.connect(self._on_tab_moved)
        self.charts_tabs.tabCloseRequested.connect(self._on_tab_close)
        self.charts_tabs.currentChanged.connect(self._on_tab_changed)

        # Tab-strip action row: edit / delete / new live beside each other
        # (test_analysis_redesign asserts the three share one parent).
        # The Generate / Re-Generate pair itself is owned by the
        # AnalysisConfigPanel; this strip stays a slim icon row so the
        # chart keeps the full pane below it.
        self.tabs_row = QWidget()
        self.tabs_row.setFixedHeight(32)
        _tr = QHBoxLayout(self.tabs_row)
        _tr.setContentsMargins(0, 4, 0, 4)
        _tr.setSpacing(6)

        self.btn_edit_chart = QPushButton("✏️")
        self.btn_edit_chart.setFixedSize(24, 24)
        self.btn_edit_chart.setToolTip("Edit selected chart")
        self.btn_edit_chart.setProperty("kind", "icon")
        self.btn_edit_chart.clicked.connect(self._on_edit_current)
        _tr.addWidget(self.btn_edit_chart)

        self.btn_remove_chart = QPushButton("🗑")
        self.btn_remove_chart.setFixedSize(24, 24)
        self.btn_remove_chart.setToolTip("Delete selected chart")
        self.btn_remove_chart.setProperty("kind", "icon")
        self.btn_remove_chart.clicked.connect(self._on_delete_current)
        _tr.addWidget(self.btn_remove_chart)

        self.btn_add_chart = QPushButton("➕")
        self.btn_add_chart.setFixedSize(24, 24)
        self.btn_add_chart.setToolTip("New chart (blank settings)")
        self.btn_add_chart.setProperty("kind", "icon")
        self.btn_add_chart.clicked.connect(self._on_add_chart)
        _tr.addWidget(self.btn_add_chart)
        _tr.addStretch()

        bl.addWidget(self.tabs_row)
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

        self._v_split.addWidget(bottom)
        self._v_split.setStretchFactor(0, 0)
        self._v_split.setStretchFactor(1, 1)
        self._v_split.setSizes([360, 480])
        self.config_panel.setMinimumHeight(48)
        bottom.setMinimumHeight(248)

        right_layout.addWidget(self._v_split)
        splitter.addWidget(right_panel)
        splitter.setSizes([220, 800])

        # Initial view: nothing selected yet -> nothing in the bottom pane.
        self.tabs_row.setVisible(False)
        self.charts_tabs.setVisible(False)
        self.descriptive_container.setVisible(False)
        self.lbl_no_charts.setVisible(False)

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.analysis_list.currentItemChanged.connect(self._on_analysis_selected)
        self.btn_clear.clicked.connect(self._on_clear_all)
        self.btn_dashboard.clicked.connect(
            lambda: self.navigate_requested.emit("dashboard")
        )
        # Settings panel -> page (Generate / Re-Generate / live changes).
        self.config_panel.generate_requested.connect(self._on_generate)
        self.config_panel.regenerate_requested.connect(self._on_regenerate)
        self.config_panel.settings_changed.connect(self._on_settings_changed)
        self.bridge.charts_changed.connect(self._on_charts_changed)

    # -- Analysis selection -------------------------------------------------

    def _on_analysis_selected(self, current: QListWidgetItem, previous) -> None:
        if current is None:
            return
        # A fresh manual selection always starts clean (the edit path
        # explicitly re-builds with its target bound afterwards).
        if not self._suppress_rebuild:
            aid = current.data(Qt.UserRole)
            self._build_config_panel(aid, prefill=self._pending_prefill)
            self._pending_prefill = None

    # -- Config panel construction -----------------------------------------

    def _build_config_panel(self, aid: str, prefill: Optional[dict] = None,
                            target_uid: Optional[str] = None,
                            bind: bool = True) -> None:
        """Build the config form for the selected analysis type.

        Descriptive is special-cased: it is a pure data overview that does
        NOT belong on the dashboard, so it shows only a clean full-area
        table (no config form, no Generate, no tabs, no legend, and nothing
        is added to the bridge charts -- so it can never reach a saved
        dashboard). Everything else renders through the 3-tab
        AnalysisConfigPanel in the splitter's top pane.

        ``target_uid`` binds the form to that chart (prefill all three tabs
        from its stored meta; it becomes the Re-Generate target). With no
        target/prefill, ``bind=True`` follows the selected chart tab when it
        holds a chart of this type; the "New chart" action passes
        ``bind=False`` to start unbound/blank.
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

        # Refresh column pools for the settings builder context.
        self._num = num_cols(df)
        self._cat = cat_cols(df)
        self._dt = dt_cols(df)
        self._all_cols = df.columns.tolist()

        opt = next((o for o in ANALYSIS_OPTIONS if o["id"] == aid), None)
        name = opt["name"] if opt else aid

        self.config_panel.set_logic(
            spec=WIDGET_SPEC.get(aid, []),
            scalar_options_fn=self._column_scalar_options,
            field_group_fn=self._field_group_of,
        )
        self.config_panel.set_context(
            num_cols=self._num, cat_cols=self._cat, dt_cols=self._dt,
            palette_names=list(PALETTES.keys()), all_cols=self._all_cols,
            group_cols=self._all_cols,
        )
        self.config_panel.set_active(aid, name, editing=(target_uid is not None))

        if target_uid is not None:
            self._settings_target_uid = target_uid
            meta = self.bridge.get_chart_meta(target_uid)
            self.config_panel.prefill(prefill if prefill is not None
                                      else (meta.get("config") or {}))
            self.config_panel.prefill_layout(meta.get("layout") or {})
            self.config_panel.prefill_typography(meta.get("typography") or {})
        elif prefill:
            self._settings_target_uid = None
            self.config_panel.prefill(prefill)
        elif bind:
            self._bind_selected_chart_settings(switch_type=False)
        else:
            self._settings_target_uid = None

        self.config_panel.reset_dirty()
        self.config_panel.setVisible(True)

        self._update_config_chart_no()
        self._show_chart_area(False)
        self._update_regen_state()

    def _bind_selected_chart_settings(self, switch_type: bool = True,
                                      force: bool = False) -> None:
        """Bind the settings form to the currently selected chart tab.

        Prefills Generate/Layout/Typography from the chart's stored meta and
        resets the dirty gate (Re-Generate disabled until something changes).
        Re-binding the SAME chart keeps in-progress edits, so an unrelated
        refresh can't wipe what the user is typing. With ``switch_type`` the
        form is rebuilt first when the selected chart belongs to another
        analysis type (so "selecting a chart shows that chart's settings").
        """
        aid = self._active_analysis
        if self._suppress_rebuild or not aid or aid == "descriptive":
            return
        idx = self.charts_tabs.currentIndex()
        if not (0 <= idx < len(self._tab_order)):
            if self._settings_target_uid is not None:
                self._settings_target_uid = None
                self._update_regen_state()
            return
        uid = self._tab_order[idx]
        ctype = self.bridge.get(f"chart_type_{uid}", "")
        if ctype and ctype != aid:
            if switch_type:
                self._build_config_panel(ctype, target_uid=uid)
            elif self._settings_target_uid is not None:
                # Chose a different analysis type while another type's
                # chart tab is selected: start blank for the chosen type.
                self._settings_target_uid = None
                self._update_regen_state()
            return
        if uid == self._settings_target_uid and not force:
            # Same chart already bound -- keep in-progress edits.
            self._update_regen_state()
            return
        meta = self.bridge.get_chart_meta(uid)
        self._settings_target_uid = uid
        self.config_panel.prefill(meta.get("config") or {})
        self.config_panel.prefill_layout(meta.get("layout") or {})
        self.config_panel.prefill_typography(meta.get("typography") or {})
        self.config_panel.reset_dirty()
        self._update_regen_state()

    def _update_config_chart_no(self) -> None:
        """Tell the settings header WHICH chart the form is bound to.

        Numbered per analysis type, exactly like the tab labels ("Statistical
        2" <-> "Chart 2"), so with several charts of the same analysis on
        screen it is always clear which one Generate/Re-Generate will target.
        """
        uid = self._settings_target_uid
        n = 0
        title = ""
        if uid:
            aid = self._active_analysis
            same_type = [u for u in self._tab_order
                         if self.bridge.get(f"chart_type_{u}", "") == aid]
            if uid in same_type:
                n = same_type.index(uid) + 1
            for c_uid, c_title, _fig in self.bridge.get("charts", []):
                if c_uid == uid:
                    title = c_title
                    break
        self.config_panel.set_chart_number(n, title)

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
        """Switch the bottom pane between the chart strip, the descriptive
        table, and the empty-state hint.

        The tab strip and its slim icon row stay visible for every
        chart-producing type -- even with zero charts -- so the ➕ "New
        chart" action is always reachable from the same place.
        """
        has = len(self.bridge.get("charts", [])) > 0
        self.tabs_row.setVisible(not descriptive)
        self.charts_tabs.setVisible(not descriptive)
        self.descriptive_container.setVisible(descriptive)
        self.lbl_no_charts.setVisible(not descriptive and not has)

    def _collect_widget_values(self) -> dict:
        return self.config_panel.collect_values()

    # -- Config panel hooks (signals) -------------------------------------

    def _on_add_chart(self) -> None:
        """[➕] -> start a fresh blank chart of the current type.

        Deliberately unbinds the settings form from the selected chart tab
        (Re-Generate has no target until a chart tab is selected again)."""
        aid = self._active_analysis
        if not aid or aid == "descriptive":
            return
        self._build_config_panel(aid, bind=False)
        # Re-open the settings pane if the user had collapsed it: the blank
        # form we just prepared would otherwise stay invisible.
        self.config_panel.expand()

    def _current_tab_uid(self) -> Optional[str]:
        """Uid of the currently selected chart tab (None when empty)."""
        idx = self.charts_tabs.currentIndex()
        if 0 <= idx < len(self._tab_order):
            return self._tab_order[idx]
        return None

    def _on_edit_current(self) -> None:
        """[✏️] -> edit the chart on the selected tab."""
        uid = self._current_tab_uid()
        if uid:
            self._on_edit_chart(uid)

    def _on_delete_current(self) -> None:
        """[🗑] -> delete the chart on the selected tab (asks first)."""
        uid = self._current_tab_uid()
        if uid:
            self._on_delete_chart(uid)

    def _on_collapse_toggled(self, collapsed: bool) -> None:
        if not self.config_panel.isVisible():
            return
        sizes = list(self._v_split.sizes())
        sizes[0] = 40 if collapsed else 360
        self._v_split.setSizes(sizes)

    # -- Settings changes (live preview + Re-Generate dirty gate) -----------

    def _on_settings_changed(self, kind: str, payload: dict) -> None:
        """A settings builder reported a change.

        Layout/Typography edits live-apply to the bound chart tile
        (debounced); any change marks the form dirty, which -- together
        with having a chart selected -- is the Re-Generate gate.
        """
        if kind in ("layout", "typography") and self._settings_target_uid:
            self._preview_timer.start()
        self._update_regen_state()

    def _update_regen_state(self) -> None:
        """Re-Generate is enabled only when a chart is selected AND the
        form differs from that chart's stored settings (dirty gate)."""
        enabled = bool(self._settings_target_uid) and self.config_panel.is_dirty()
        self.config_panel.set_regenerate_enabled(enabled)

    def _apply_live_preview(self) -> None:
        """Re-render the bound chart tile from its ORIGINAL figure + the
        current Layout/Typography options ("changes appear instantly on
        the chart"). Always re-derives from the pre-display-options figure
        so repeated edits can't compound."""
        uid = self._settings_target_uid
        if not uid:
            return
        card = self._chart_cards.get(uid)
        if card is None:
            return
        layout = self.config_panel.collect_layout()
        typo = self.config_panel.collect_typography()
        try:
            from chart_display import apply_display_options
            new_fig = apply_display_options(card._base_fig, layout, typo)
        except Exception:
            log.exception("live preview failed for %s", uid)
            return

        # Title: an explicit Layout title replaces the chart's own title;
        # empty falls back to whatever title the chart already has.
        new_title = str(layout.get("title") or "").strip() or None
        cur_title = None
        for c_uid, c_title, _fig in self.bridge.get("charts", []):
            if c_uid == uid:
                cur_title = c_title
                break
        final_title = new_title or cur_title

        # Write through to the bridge (so save/export/dashboard see the
        # same figure) WITHOUT letting charts_changed tear down the tabs --
        # the card is updated in place instead.
        self._bridge_write_guard = True
        try:
            self.bridge.replace_chart(uid, new_fig, final_title)
            self.bridge.set_chart_meta(uid, layout=dict(layout),
                                       typography=dict(typo))
        finally:
            self._bridge_write_guard = False

        card._fig = new_fig
        if final_title is not None:
            card.lbl_title.setText(final_title)
        card.plot_view.set_figure(new_fig)

        # Keep the tab's tooltip (and the settings header) in sync when an
        # explicit title was set. The VISIBLE tab label is the stable
        # "{Type} {n}" numbering, so it never shifts under the user's cursor.
        try:
            idx = self._tab_order.index(uid)
        except ValueError:
            return
        if final_title:
            self.charts_tabs.setTabToolTip(idx, final_title)
            self._update_config_chart_no()

    def _on_generate(self) -> None:
        """Generate a NEW chart (always appends)."""
        self._start_generation("new", None)

    def _on_regenerate(self) -> None:
        """Re-Generate: replace the settings form's bound chart in place."""
        if not self._settings_target_uid:
            return
        self._start_generation("regen", self._settings_target_uid)

    def _start_generation(self, mode: str, target: Optional[str]) -> None:
        """Run the generation worker for the active analysis type.

        Snapshots all three settings blocks (Generate values, Layout,
        Typography) so the finished handler can persist them as the chart's
        config/layout/typography meta -- the same snapshot edit-mode prefill
        reads back later.
        """
        if self._active_analysis is None:
            return
        df = self.bridge.df
        if df is None:
            QMessageBox.warning(self, "No Data", "Please upload a dataset first.")
            return

        self._collect_at_generate = dict(self.config_panel.collect_values())
        self._collect_layout = dict(self.config_panel.collect_layout())
        self._collect_typo = dict(self.config_panel.collect_typography())
        self._gen_mode = mode
        self._gen_target = target

        # Disable both action buttons while working.
        self.config_panel.set_actions_busy(True, "⏳ Working…")

        # Use a background worker to avoid blocking the UI
        self._gen_worker = ChartGenerationWorker(
            self._active_analysis, df, self._collect_at_generate, self,
            layout_opts=self._collect_layout,
            typography_opts=self._collect_typo,
        )
        self._gen_worker.finished_ok.connect(self._on_generate_finished)
        self._gen_worker.failed.connect(self._on_generate_failed)
        self._gen_worker.start()

    def _on_generate_finished(self, new_charts) -> None:
        """Handle successful chart generation from the worker."""
        self.config_panel.set_actions_busy(False)

        if not new_charts:
            QMessageBox.warning(self, "No Charts",
                                "No charts were generated. Check your configuration.")
            self._update_regen_state()
            return

        meta = {
            "config": dict(self._collect_at_generate),
            "layout": dict(self._collect_layout),
            "typography": dict(self._collect_typo),
        }
        # An explicit Layout title is what the figure shows (the worker
        # applied it), so the chart's stored title/tab label must match it
        # instead of the runner's default title.
        display_title = str(self._collect_layout.get("title") or "").strip()
        select_uid = None

        if self._gen_mode == "regen" and self._gen_target:
            # Replace the bound chart in place with the first regenerated
            # figure (a multi-select config may produce more than one; those
            # extras become brand-new charts rather than being lost).
            target = self._gen_target
            _first_uid, first_title, first_fig = new_charts[0]
            self.bridge.set_chart_meta(target, **meta)
            self.bridge.replace_chart(target, first_fig,
                                      display_title or first_title)
            for uid, title, fig in new_charts[1:]:
                self.bridge.add_chart(uid, display_title or title, fig,
                                      chart_type=self._active_analysis,
                                      meta=dict(meta))
            select_uid = target
        else:
            # Always append -- generating the same type again with different
            # values creates a new chart instead of overwriting work.
            for uid, title, fig in new_charts:
                self.bridge.add_chart(uid, display_title or title, fig,
                                      chart_type=self._active_analysis,
                                      meta=dict(meta))
            select_uid = new_charts[0][0]

        self._refresh_charts()
        if select_uid:
            self._select_chart_tab(select_uid)
            # Force re-bind so the form reflects the stored state of the
            # chart now on screen (clears dirty -> Re-Generate disabled).
            self._bind_selected_chart_settings(force=True)
        self._update_regen_state()
        self.bridge.save_draft()

    def _on_generate_failed(self, error: str) -> None:
        """Handle chart generation failure from the worker."""
        self.config_panel.set_actions_busy(False)
        self._update_regen_state()
        log.error("Generate failed: %s", error)
        QMessageBox.critical(self, "Generation Error", error)

    # -- Chart management ---------------------------------------------------

    def _chart_tab_label(self, title: str, chart_type: str, type_counter: dict) -> str:
        """Compact, stable tab label: "{Friendly Type} {n}", numbered per
        chart type so several charts of the SAME analysis stay tellable apart
        ("Statistical 1", "Statistical 2" -- the duplicate-config case that
        used to produce identical truncated-title tabs).

        The chart's own (often long) title lives in the tab's tooltip instead
        of the tab, which keeps every tab the same short width -- so adding
        charts never squeezes the chart area or wraps the strip.
        """
        type_counter[chart_type] = type_counter.get(chart_type, 0) + 1
        friendly = (_ANALYSIS_TYPE_NAMES.get(chart_type)
                    or chart_type.replace("_", " ").title() or "Chart")
        return f"{friendly} {type_counter[chart_type]}"

    def _refresh_charts(self) -> None:
        """Rebuild the chart tabs from the session bridge.

        Wraps the actual rebuild so tab-change binding is suppressed while
        tabs are torn down/re-added, then re-syncs the settings form with
        the surviving selection once done.
        """
        if self._bridge_write_guard:
            return
        self._refreshing_charts = True
        try:
            self._refresh_charts_locked()
        finally:
            self._refreshing_charts = False
        if self._active_analysis and self._active_analysis != "descriptive":
            self._bind_selected_chart_settings()

    def _refresh_charts_locked(self) -> None:
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
            idx = self.charts_tabs.addTab(card, label)
            self.charts_tabs.setTabToolTip(idx, title or label)
            # Compact per-chart tab: keep short stable labels (see
            # _chart_tab_label) so N charts of one analysis can't widen the
            # strip into the chart area.
            self._tab_order.append(uid)
            self._chart_cards[uid] = card
            if uid == prev_uid:
                restore_idx = i

        # Keep chart-area visibility in sync (descriptive shows the table).
        # The strip + its icon row stay for every chart-producing type --
        # the ➕ row is the only "new chart" affordance when none exist yet.
        descriptive = self._active_analysis == "descriptive"
        self.tabs_row.setVisible(not descriptive)
        self.charts_tabs.setVisible(not descriptive)
        self.lbl_no_charts.setVisible((not descriptive) and len(charts) == 0)

        if charts:
            self.charts_tabs.setCurrentIndex(restore_idx)

    def _on_charts_changed(self, charts) -> None:
        """Handle charts_changed signal from the bridge."""
        if self._bridge_write_guard:
            # Our own live-preview write-through: the card was already
            # updated in place, no teardown needed.
            return
        self._refresh_charts()

    def _on_tab_changed(self, *args) -> None:
        """Bind the settings form to the newly selected chart (per-chart
        settings: prefill + dirty-gate reset)."""
        if self._refreshing_charts:
            return
        if self._active_analysis:
            self._bind_selected_chart_settings()

    def _ask_delete(self, uid: str) -> bool:
        """Confirm a chart deletion. There is no undo, and the ✕ on a tab used
        to delete whatever chart the tab indices pointed at rather than the
        one on screen -- so both delete paths ask before dropping a chart."""
        if not CONFIRM_CHART_DELETE:
            return True
        title = next((t for u, t, _f in self.bridge.get("charts", []) if u == uid), "")
        reply = QMessageBox.question(
            self, "Delete Chart",
            f"Delete this chart?\n\n{title}" if title else "Delete this chart?",
            QMessageBox.Yes | QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _on_delete_chart(self, uid: str) -> None:
        """Delete a chart (the card's 🗑 and the tab's ✕ both land here)."""
        if not uid or not self._ask_delete(uid):
            return
        self.bridge.remove_chart(uid)
        self._refresh_charts()
        self.bridge.save_draft()

    def _on_edit_chart(self, uid: str) -> None:
        """Edit a chart: load its analysis type + stored settings (all three
        tabs) into the form and bind it as the Re-Generate target, so the
        user only has to change what they actually want."""
        chart_type = self.bridge.get(f"chart_type_{uid}", "")
        if not chart_type:
            return

        # Select the chart's list entry and tab WITHOUT letting their
        # handlers rebuild -- we rebuild explicitly with the target bound
        # below (the guard flag stops them wiping our target).
        self._suppress_rebuild = True
        try:
            for i in range(self.analysis_list.count()):
                item = self.analysis_list.item(i)
                if item.data(Qt.UserRole) == chart_type:
                    self.analysis_list.setCurrentItem(item)
                    break
            if uid in self._tab_order:
                self.charts_tabs.setCurrentIndex(self._tab_order.index(uid))
        finally:
            self._suppress_rebuild = False

        self._build_config_panel(chart_type, target_uid=uid)
        self.config_panel.tab_bar.setCurrentIndex(0)
        # A collapsed settings pane would swallow the form we just filled in,
        # so edit always re-opens it (the header names the bound chart).
        self.config_panel.expand()
        # Drop the focus back to the config card so the user sees the form.
        self.config_panel.setFocus()

    def _on_tab_close(self, index: int) -> None:
        """Close a chart via its tab's ✕ button (asks before deleting)."""
        if 0 <= index < len(self._tab_order):
            self._on_delete_chart(self._tab_order[index])

    def _on_tab_moved(self, from_index: int, to_index: int) -> None:
        """A user dragged a chart tab into a new position.

        Every uid lookup on this page goes through ``_tab_order[index]``, so
        the tab order and that list have to move together -- Qt re-orders its
        own tab bar, and without this the settings form bound the WRONG chart
        and the ✕ deleted the wrong one (the tab bar and _tab_order drifted
        apart). The bridge list is re-ordered too, so the tab order the user
        sees is also the dashboard/export order.
        """
        if self._refreshing_charts:
            return
        n = len(self._tab_order)
        if not (0 <= from_index < n and 0 <= to_index < n) or from_index == to_index:
            return
        self._tab_order.insert(to_index, self._tab_order.pop(from_index))

        charts = list(self.bridge.get("charts", []))
        order = {u: i for i, (u, _t, _f) in enumerate(charts)}
        if all(u in order for u in self._tab_order):
            by_uid = {u: c for u, c in ((c[0], c) for c in charts)}
            self.bridge.set("charts", [by_uid[u] for u in self._tab_order])
            self.bridge.save_draft()

        # uids follow their new indices: re-point the form at the chart that
        # is still on screen instead of the one that used to sit here.
        self._bind_selected_chart_settings(force=True)

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