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
    QStackedWidget, QSizePolicy, QTabWidget, QDialog,
)

from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from desktop.widgets.analysis_config_panel import AnalysisConfigPanel
from desktop.widgets.chart_card import ChartCard
from desktop.workers import ChartGenerationWorker
from services.analysis_service import (
    ANALYSIS_OPTIONS, WIDGET_SPEC, run_analysis_with_config, run_analysis,
    num_cols, cat_cols, dt_cols, dim_cols,
)
from services.column_roles import resolve_roles
from services.chart_service import PALETTES, apply_hover_format

log = logging.getLogger(__name__)

# Live-preview debounce. Sliders/spinboxes stream values while dragging, so
# they stay coalesced; a combo box is one deliberate pick and must feel
# instant. Both apply the same ~40ms Plotly layout rebuild on the GUI thread,
# so the short path is safe -- there is no flood behind it to coalesce.
_PREVIEW_DEBOUNCE_MS = 300
_PREVIEW_FAST_MS = 40

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
        self._bulk_bridge_write = False                # suppress charts_changed rebuild during Generate/Re-Generate (one refresh at the end)
        self._pending_select_uid: str | None = None      # chart to select on the next refresh (Generate/Re-Generate target)

        # Debounced live preview for Layout/Typography edits (design:
        # "changes appear instantly on the chart").
        #
        # Two intervals, because the two kinds of edit feel nothing alike.
        # Dragging a slider fires a flood of changes and each re-render is
        # ~40ms of Plotly layout rebuilding on the GUI thread, so those stay
        # debounced. A combo box (font family, legend position, bar mode) is a
        # single deliberate pick, and making the user wait 300ms to see it was
        # the "font style change is laggy" report -- so discrete choices apply
        # almost immediately instead.
        self._preview_fast_timer = QTimer(self)
        self._preview_fast_timer.setSingleShot(True)
        self._preview_fast_timer.setInterval(_PREVIEW_FAST_MS)
        self._preview_fast_timer.timeout.connect(self._apply_live_preview)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_DEBOUNCE_MS)
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
        #
        # The three mutually-exclusive views (empty state / chart tabs /
        # descriptive table) live in a QStackedWidget, NOT stacked in one
        # QVBoxLayout. Toggling setVisible() on siblings in a box layout was
        # the root cause of the Generate "reopen" flash: hiding one child
        # re-runs the layout, and because the pages were rebuilt while
        # repaints were disabled, charts_tabs was left at HALF the pane
        # height -- and the ChartCard's fixed-height header + notes ate the
        # rest, leaving the QWebEngineView with height == 0. Chromium
        # suspends painting at zero height, so loadFinished never fired and
        # the freshly generated chart stayed blank until the user resized
        # something. A QStackedWidget gives the active page the full pane
        # unconditionally and switches it with a single atomic call.
        bottom = QWidget()
        self._chart_stack = QStackedWidget()
        self._chart_stack.setContentsMargins(0, 0, 0, 0)
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self._chart_stack)

        # Stack page order is fixed; _show_chart_area() picks the index.
        self._STACK_EMPTY = 0
        self._STACK_CHARTS = 1
        self._STACK_DESCRIPTIVE = 2

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

        # No-charts placeholder. Added FIRST so the stack's initial page
        # (and the _STACK_EMPTY == 0 constant) agree -- addWidget order IS
        # the page order, so the blocks below must be added in index order.
        self.lbl_no_charts = QLabel("No charts yet. Configure an analysis and click Generate.")
        self.lbl_no_charts.setAlignment(Qt.AlignCenter)
        self.lbl_no_charts.setProperty("class", "faint"); self.lbl_no_charts.setStyleSheet("font-size: 13px; padding: 40px;")
        self._chart_stack.addWidget(self.lbl_no_charts)  # _STACK_EMPTY = 0

        self._chart_stack.addWidget(self.charts_tabs)  # _STACK_CHARTS = 1

        # Descriptive-only full-area table view.
        self.descriptive_container = QWidget()
        self.descriptive_container_layout = QVBoxLayout(self.descriptive_container)
        self.descriptive_container_layout.setContentsMargins(0, 0, 0, 0)
        self._chart_stack.addWidget(self.descriptive_container)  # _STACK_DESCRIPTIVE = 2

        self._v_split.addWidget(bottom)
        self._v_split.setStretchFactor(0, 0)
        self._v_split.setStretchFactor(1, 1)
        self._v_split.setSizes([360, 480])
        self.config_panel.setMinimumHeight(48)
        bottom.setMinimumHeight(248)

        right_layout.addWidget(self._v_split)
        splitter.addWidget(right_panel)
        splitter.setSizes([220, 800])

        # Initial view: nothing selected yet -> show the empty state.
        # (A QStackedWidget shows its first page by default, and _STACK_EMPTY
        # is index 0, so the empty hint is already the visible page.)
        self._chart_stack.setCurrentIndex(self._STACK_EMPTY)

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
        self.bridge.theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode: str) -> None:
        """Re-apply the per-widget token styles on a dark<->light switch.

        Section/hint labels inside the chart-settings form are styled with
        widget-level setStyleSheet() (group_label_style/label_style), which
        freezes the colours captured at build time -- the global QSS
        re-polish does NOT reach them. Without this, toggling to light mode
        left the settings text painted in the old dark palette (near-white
        on white = the "transparent/unreadable tiles" report). restyle_tree
        re-resolves those roles against the current mode; colour swatches
        are re-tinted the same way.
        """
        try:
            from desktop.widgets.analysis_settings.base import restyle_tree
            restyle_tree(self.config_panel)
        except Exception:
            log.debug("theme restyle of settings panel failed", exc_info=True)
        # The charts themselves have to follow the mode too: their canvas is
        # painted by the figure/plot page, not by the app's QSS, so each open
        # view re-themes and repaints in place (Plotly.react -- no reload, no
        # flash, no regeneration needed).
        for card in self._chart_cards.values():
            try:
                card.plot_view.apply_theme()
            except Exception:
                log.debug("chart theme repaint failed", exc_info=True)

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
            # The chart tab strip is scoped to the selected analysis type, so
            # it has to be rebuilt on every type change -- otherwise the tabs
            # keep showing the previous type's charts.
            self._refresh_charts()

    # -- Config panel construction -----------------------------------------

    def _user_classification(self) -> Optional[dict]:
        """The user's confirmed Column Classification, in resolver shape.

        Returns ``None`` when the user never confirmed one, which tells
        ``column_roles.resolve_roles`` to auto-detect every column. Any
        column named here keeps the user's family regardless of its dtype.
        """
        b = self.bridge
        num = b.get("num_cols")
        cat = b.get("cat_cols")
        dt = b.get("dt_cols")
        if not (num or cat or dt):
            return None
        return {"numeric": list(num or []),
                "categorical": list(cat or []),
                "datetime": list(dt or [])}

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
        #
        # These MUST come from the user's Column Classification (confirmed on
        # the Upload page), not from raw pandas dtypes: a user who
        # deliberately reclassified an id-like numeric as a dimension has to
        # see it in "Group by" everywhere, or the app silently ignores them.
        # ``column_roles`` owns the precedence rules (user wins, auto-detect
        # is the fallback, no column is ever dropped) so every chart type
        # behaves identically without each one re-deriving anything.
        roles = resolve_roles(
            df,
            explicit=self._user_classification(),
            value_sig_map=self.bridge.get("cls_value_sig") or None,
        )
        self._roles = roles
        self._num = num_cols(df, roles=roles)
        self._cat = cat_cols(df, roles=roles)
        self._dt = dt_cols(df, roles=roles)
        self._dims = dim_cols(df, roles=roles)
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
            # "Group by" offers every dimension (text AND datetime), because a
            # date is a legitimate grouping key. Passing all_cols here -- as
            # this used to -- instead advertised raw metrics as grouping
            # choices, while the *default* pool (cat_cols alone) hid the
            # category/string/bool columns entirely.
            dim_cols=self._dims,
        )
        self.config_panel.set_active(aid, name, editing=(target_uid is not None))

        if target_uid is not None:
            self._settings_target_uid = target_uid
            meta = self._chart_settings_meta(target_uid)
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
                self._update_config_chart_no()
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
        meta = self._chart_settings_meta(uid)
        self._settings_target_uid = uid
        self.config_panel.prefill(meta.get("config") or {})
        self.config_panel.prefill_layout(meta.get("layout") or {})
        self.config_panel.prefill_typography(meta.get("typography") or {})
        self.config_panel.reset_dirty()
        self._update_config_chart_no()
        self._update_regen_state()

    def _chart_settings_meta(self, uid: str) -> dict:
        """Return saved per-chart settings, including older native sessions.

        The bridge metadata is authoritative when present. A few sessions
        created during the renderer migration have the settings only inside
        the ChartModel recipe, so use that recipe to fill any missing blocks.
        """
        meta = dict(self.bridge.get_chart_meta(uid) or {})
        chart = next((item for item in self.bridge.get("charts", [])
                      if item[0] == uid), None)
        model = chart[2] if chart and isinstance(chart[2], dict) else None
        recipe = (model or {}).get("recipe") if (model or {}).get("schema_version") == 1 else None
        if isinstance(recipe, dict):
            for key in ("config", "layout", "typography"):
                if isinstance(recipe.get(key), dict):
                    meta[key] = dict(recipe[key])
        return meta

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
        """Build the descriptive overview off the UI thread.

        ``run_analysis("descriptive", df)`` runs a full ``describe()`` over
        every column on the CALLER's thread, so selecting Descriptive from
        the sidebar used to stall the whole app on a large frame. The work
        now runs in a ChartGenerationWorker (its default config path already
        handles "descriptive"); a generation token discards a stale result
        when the user switches type mid-run. Widget creation stays on the UI
        thread in the completion slot -- Qt forbids it anywhere else.
        """
        df = self.bridge.df
        if df is None:
            return
        while self.descriptive_container_layout.count():
            item = self.descriptive_container_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        placeholder = QLabel("Computing overview…")
        placeholder.setProperty("class", "faint")
        self.descriptive_container_layout.addWidget(placeholder)
        token = getattr(self, "_descriptive_token", 0) + 1
        self._descriptive_token = token
        worker = ChartGenerationWorker("descriptive", df, {}, self)
        worker.finished_ok.connect(
            lambda results: self._on_descriptive_finished(token, results))
        worker.failed.connect(
            lambda err: self._on_descriptive_failed(token, err))
        self._descriptive_worker = worker
        worker.start()

    def _take_descriptive_placeholder(self):
        while self.descriptive_container_layout.count():
            item = self.descriptive_container_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _on_descriptive_finished(self, token: int, results) -> None:
        if token != getattr(self, "_descriptive_token", 0):
            return  # stale: the user already moved to another type
        self._take_descriptive_placeholder()
        if results:
            uid, title, fig = results[0]
            chart_model = (fig if isinstance(fig, dict)
                           and fig.get("schema_version") == 1
                           and fig.get("kind") == "chart" else None)
            card = ChartCard(uid, title, fig, "descriptive",
                             chart_model=chart_model)
            card.set_note("")
            for btn in (card.btn_edit, card.btn_style, card.btn_delete,
                        card.btn_up, card.btn_down, card.btn_fullwidth):
                btn.setVisible(False)
            self.descriptive_container_layout.addWidget(card, stretch=1)

    def _on_descriptive_failed(self, token: int, error: str) -> None:
        if token != getattr(self, "_descriptive_token", 0):
            return
        self._take_descriptive_placeholder()
        log.error("Descriptive run failed: %s", error)

    def _show_chart_area(self, descriptive: bool) -> None:
        """Switch the bottom pane between the chart tabs, the descriptive
        table, and the empty-state hint. New charts come from the
        Generate button in the settings panel.

        One atomic ``setCurrentIndex`` on the QStackedWidget -- never a
        set of setVisible() calls on box-layout siblings. The active page
        is laid out at the full pane size the moment it is selected, which
        is what gives a freshly generated QWebEngineView a real height (and
        therefore a real first paint) without the user touching a splitter.
        """
        has = len(self.bridge.get("charts", [])) > 0
        if descriptive:
            target = self._STACK_DESCRIPTIVE
        elif has:
            target = self._STACK_CHARTS
        else:
            target = self._STACK_EMPTY
        self._sync_stack(target)

    def _sync_stack(self, target: int) -> None:
        """Point the stacked chart area at *target* and force a layout pass.

        ``QStackedWidget.setCurrentIndex`` only marks the layout dirty; the
        new page keeps its previous (often zero) size until Qt runs the
        next layout pass. Since chart generation can complete while the
        event loop is busy, activate the stack's layout explicitly so the
        selected page -- and every QWebEngineView inside it -- has its real
        geometry before the pending figure is pushed to Chromium.
        """
        if self._chart_stack.currentIndex() != target:
            self._chart_stack.setCurrentIndex(target)
        try:
            self._chart_stack.layout().activate()
        except Exception:
            log.debug("chart stack layout activation failed", exc_info=True)

    def _collect_widget_values(self) -> dict:
        return self.config_panel.collect_values()

    # -- Config panel hooks (signals) -------------------------------------

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

        ``discrete`` picks the short debounce: a combo box changed once by a
        deliberate click, so there is no flood to coalesce and no reason to
        make the user wait out the slider debounce before the chart updates.
        """
        if kind in ("layout", "typography") and self._settings_target_uid:
            if payload.get("discrete"):
                self._preview_timer.stop()      # cancel a pending slow pass
                self._preview_fast_timer.start()
            else:
                self._preview_fast_timer.stop()
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
        if card._chart_model is not None and card._fig is None:
            # Native chart families update their renderer-neutral display
            # contract directly; no Plotly figure is created for live preview.
            try:
                from services.polars_chart_service import apply_chart_model_display_options
                new_model = apply_chart_model_display_options(
                    card._chart_model, layout, typo)
            except Exception:
                log.exception("native chart preview failed for %s", uid)
                return
            new_title = str(layout.get("title") or "").strip() or new_model["title"]
            new_model["title"] = new_title
            self._bridge_write_guard = True
            try:
                self.bridge.replace_chart(uid, new_model, new_title)
                recipe = dict(new_model.get("recipe") or {})
                self.bridge.set_chart_meta(
                    uid, config=dict(recipe.get("config") or {}),
                    layout=dict(recipe.get("layout") or layout),
                    typography=dict(recipe.get("typography") or typo))
            finally:
                self._bridge_write_guard = False
            card.set_chart_model(new_model)
            try:
                idx = self._tab_order.index(uid)
            except ValueError:
                return
            self.charts_tabs.setTabToolTip(idx, new_title)
            self._update_config_chart_no()
            return
        log.error("Chart %s has no native ECharts model; cannot apply preview", uid)
        return

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
            roles=self._roles,
            polars_dataset=self.bridge.polars_dataset(create_if_missing=False),
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
        if new_charts and isinstance(new_charts[0][2], dict):
            engine = (new_charts[0][2].get("source") or {}).get("engine")
            if engine:
                meta["engine"] = engine

        def _meta_for_chart(chart):
            chart_meta = dict(meta)
            if isinstance(chart, dict) and chart.get("schema_version") == 1:
                source = chart.get("source") or {}
                if source.get("engine"):
                    chart_meta["engine"] = source["engine"]
                if chart.get("recipe"):
                    chart_meta["chart_model_recipe"] = dict(chart["recipe"])
            return chart_meta

        # Second "counted" trigger: the user jumped straight to Analysis from
        # the sidebar and produced a real chart, without ever pressing
        # "Go to Analysis". Reaching a successful chart is what makes this
        # dataset analysed under that route. Placed after the
        # `if not new_charts` guard so a failed/empty run does not count.
        self.bridge.record_dataset_analysed()
        # Batch the bridge writes below: each add/replace emits charts_changed,
        # and without this guard every emit rebuilds all tabs + rebinds the
        # settings form (Generate looked like the app "reopened itself";
        # Re-Generate could rebuild mid-batch and blank the chart). One
        # explicit _refresh_charts() at the end is the only rebuild.
        self._pending_select_uid = self._gen_target if self._gen_mode == "regen" and self._gen_target else (new_charts[0][0] if new_charts else None)
        self._bulk_bridge_write = True
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
            self.bridge.set_chart_meta(target, **_meta_for_chart(new_charts[0][2]))
            self.bridge.replace_chart(target, first_fig,
                                      display_title or first_title)
            for uid, title, fig in new_charts[1:]:
                self.bridge.add_chart(uid, display_title or title, fig,
                                      chart_type=self._active_analysis,
                                      meta=_meta_for_chart(fig))
            select_uid = target
        else:
            # Always append -- generating the same type again with different
            # values creates a new chart instead of overwriting work.
            for uid, title, fig in new_charts:
                self.bridge.add_chart(uid, display_title or title, fig,
                                      chart_type=self._active_analysis,
                                      meta=_meta_for_chart(fig))
            select_uid = new_charts[0][0]

        self._bulk_bridge_write = False
        self._refresh_charts()
        if select_uid:
            # The refresh above already restored selection to the pending
            # target and bound the form to it (trailing bind); selecting
            # again is a no-op in that case. No forced rebuild here: the
            # form already shows the values just used, and a full panel
            # teardown/rebuild is exactly the "app reopened itself" flash.
            self._select_chart_tab(select_uid)
        # The chart now matches the form, so the form is no longer dirty and
        # Re-Generate goes back to disabled. This used to happen as a side
        # effect of the settings form being torn down and re-bound with every
        # refresh; the chart tiles are reused now, the bind is a no-op, and
        # the gate has to be cleared explicitly or the button would stay
        # enabled (looking like unsaved changes) right after a Re-Generate.
        self.config_panel.reset_dirty()
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
        if self._bridge_write_guard or self._bulk_bridge_write:
            return
        # Freeze repaints/resize propagation while tabs are torn down and
        # re-added: without this the vertical splitter collapses to zero for
        # one frame and springs back -- the visible "reopen" bounce.
        self.setUpdatesEnabled(False)
        self._refreshing_charts = True
        try:
            self._refresh_charts_locked()
        finally:
            self._refreshing_charts = False
            self.setUpdatesEnabled(True)
        if self._active_analysis and self._active_analysis != "descriptive":
            self._bind_selected_chart_settings()

    def _refresh_charts_locked(self) -> None:
        # Remember which chart was selected so regenerating/adding a
        # chart doesn't always bounce the view back to the first tab --
        # tracked by uid, not index, since index alone doesn't survive
        # charts being added/removed/reordered.
        pending = self._pending_select_uid
        self._pending_select_uid = None
        prev_uid = pending
        cur_idx = self.charts_tabs.currentIndex()
        if prev_uid is None and 0 <= cur_idx < len(self._tab_order):
            prev_uid = self._tab_order[cur_idx]

        all_charts = self.bridge.get("charts", [])
        # Stale-card removal is keyed off EVERY chart in the bridge, never the
        # filtered view: cards for other analysis types are merely not shown,
        # and deleting them here would destroy (and on return reload) their
        # QWebEngineView.
        wanted = [c[0] for c in all_charts]

        # The tab strip shows the SELECTED analysis type only. A flat strip
        # mixed every type together, so with Statistical selected you could
        # still see (and land on) a Distribution chart, and the numbered tab
        # labels had to carry the type name to disambiguate. Cards of the
        # other types stay alive in _chart_cards and are re-attached when the
        # user switches back, so nothing is regenerated.
        aid = self._active_analysis
        if aid and aid != "descriptive":
            charts = [c for c in all_charts
                      if self.bridge.get(f"chart_type_{c[0]}", "") == aid]
        else:
            charts = list(all_charts)

        # Drop the cards whose chart is gone -- and ONLY those.  A ChartCard
        # owns a QWebEngineView, and destroying/recreating one reloads the
        # whole plotly.js shell and repaints the chart from scratch; doing
        # that to every card on the page (which is what this used to do) is
        # the "the app closed and reopened itself" flash on Generate.
        # Reusing the surviving tiles turns every later update into an
        # in-place Plotly.react().
        for uid in [u for u in list(self._chart_cards) if u not in wanted]:
            stale = self._chart_cards.pop(uid, None)
            if stale is None:
                continue
            stale_idx = self.charts_tabs.indexOf(stale)
            if stale_idx >= 0:
                self.charts_tabs.removeTab(stale_idx)
            stale.deleteLater()

        # Detach the cards of the other analysis types from the tab strip.
        # removeTab only unparents -- the card and its loaded QWebEngineView
        # stay alive in _chart_cards, so switching back re-attaches the very
        # same widget (no plotly.js reload, no re-generation, no flash).
        # This must run for every non-visible card, otherwise a type switch
        # would APPEND the new type's charts and leave the old ones in place.
        visible_uids = {c[0] for c in charts}
        for uid, card in list(self._chart_cards.items()):
            if uid in visible_uids or card is None:
                continue
            idx = self.charts_tabs.indexOf(card)
            if idx >= 0:
                self.charts_tabs.removeTab(idx)

        self._tab_order = []
        type_counter: dict = {}
        restore_idx = 0
        for i, (uid, title, fig) in enumerate(charts):
            chart_type = self.bridge.get(f"chart_type_{uid}", "")
            meta = self.bridge.get_chart_meta(uid)
            note = self.bridge.get_note(uid)
            card = self._chart_cards.get(uid)
            # Native ChartModels bypass Plotly entirely. Legacy saved figures
            # continue through the temporary conversion path while each chart
            # family is migrated.
            if (card is not None and card._chart_model is not None
                    and getattr(card, "_bridge_figure_ref", None) is fig):
                # A type switch rebuilds the tab strip. Reuse the validated
                # model when its bridge source is unchanged so large charts
                # are not repeatedly converted and reloaded.
                chart_model = card._chart_model
            elif (isinstance(fig, dict) and fig.get("schema_version") == 1
                    and fig.get("kind") == "chart"):
                try:
                    from modules.chart_model import validate_chart_model
                    chart_model = validate_chart_model(fig)
                except Exception as exc:
                    log.debug("stored ChartModel validation failed: %s", exc)
                    chart_model = None
            else:
                try:
                    from modules.legacy_plotly_adapter import figure_to_chart_model
                    chart_model = figure_to_chart_model(
                        fig, uid=uid, chart_type=chart_type, title=title, meta=meta)
                except Exception as exc:
                    log.debug("legacy figure to ChartModel conversion failed: %s", exc)
                    chart_model = None

            if card is None:
                card = ChartCard(uid, title, fig, chart_type, meta,
                                 chart_model=chart_model)
                card.set_note(note)
                card.delete_requested.connect(self._on_delete_chart)
                card.edit_requested.connect(self._on_edit_chart)
                # The per-chart slicer entry point: the card is where the
                # user is looking when they decide this ONE chart needs a
                # filter, so the form opens from here, pre-scoped to it.
                card.slicer_requested.connect(self._on_add_chart_slicer)
                # Analysis cards keep only edit + delete on the header: style
                # lives in the Layout/Typography tabs, reorder via tab drag.
                for _btn in (card.btn_up, card.btn_down, card.btn_style):
                    _btn.setVisible(False)
                card.note_changed.connect(self._on_note_changed)
                self._chart_cards[uid] = card
            else:
                # Existing tile: refresh it in place.  The tab keeps the
                # label/number it was created with (stable under the user's
                # cursor), so the counter is advanced without renaming it.
                if getattr(card, "_title", None) != title:
                    card.set_title(title)
                card.set_note(note)
                card._legacy_source_fig = (
                    None if isinstance(fig, dict)
                    and fig.get("schema_version") == 1 else fig)
                if chart_model is not None:
                    if chart_model is not card._chart_model:
                        old_model = dict(card._chart_model or {})
                        old_display = dict(old_model.get("display") or {})
                        old_display.pop("theme_mode", None)
                        old_model["display"] = old_display
                        new_model = dict(chart_model)
                        new_display = dict(new_model.get("display") or {})
                        new_display.pop("theme_mode", None)
                        new_model["display"] = new_display
                        if old_model == new_model:
                            chart_model = card._chart_model
                        else:
                            card.set_chart_model(chart_model, source_figure=fig)
                elif card._chart_model is not None or fig is not card._fig:
                    # This chart family is not yet supported by the ECharts
                    # adapter; return its existing card to the legacy path.
                    card.set_figure(fig)
            card._bridge_figure_ref = fig

            # Compact per-chart tab: keep short stable labels (see
            # _chart_tab_label) so N charts of one analysis can't widen
            # the strip into the chart area.
            label = self._chart_tab_label(title, chart_type, type_counter)
            tab_idx = self.charts_tabs.indexOf(card)
            if tab_idx < 0:
                # First time this card belongs to the visible strip (it was
                # built while another analysis type was selected). Re-attach
                # the existing widget -- do NOT build a new one, or its
                # web view would have to reload plotly.js from scratch.
                self.charts_tabs.addTab(card, label)
                tab_idx = self.charts_tabs.indexOf(card)

            tab_idx = self.charts_tabs.indexOf(card)
            if tab_idx >= 0:
                self.charts_tabs.setTabToolTip(
                    tab_idx, title or self.charts_tabs.tabText(tab_idx))
            self._tab_order.append(uid)
            if prev_uid is not None and uid == prev_uid:
                restore_idx = i

        # Re-order the tabs to match the bridge order by MOVING them: a tab
        # move keeps its page (and its already-loaded web view) alive, while
        # removeTab+addTab would drop the page and force a reload.
        for pos, uid in enumerate(self._tab_order):
            card = self._chart_cards.get(uid)
            if card is None:
                continue
            cur = self.charts_tabs.indexOf(card)
            if cur >= 0 and cur != pos:
                self.charts_tabs.tabBar().moveTab(cur, pos)

        descriptive = self._active_analysis == "descriptive"
        # Keep chart-area visibility in sync (descriptive shows the table).
        # Uses the same single stacked-page switch as _show_chart_area so
        # the newly created/selected tab is laid out at the full pane size
        # right away -- the tab's QWebEngineView must have real geometry
        # before its pending figure is pushed, or Chromium holds the render
        # until the user happens to resize something.
        self._show_chart_area(descriptive)

        if charts:
            self.charts_tabs.setCurrentIndex(restore_idx)
            # The selected page is now current: re-run the stack layout so
            # the QTabWidget and its ChartCards pick up their real sizes in
            # this same call stack, before the event loop gets to paint.
            try:
                self._chart_stack.layout().activate()
            except Exception:
                log.debug("chart stack layout activation failed", exc_info=True)

    def _on_charts_changed(self, charts) -> None:
        """Handle charts_changed signal from the bridge."""
        if self._bridge_write_guard or self._bulk_bridge_write:
            # Our own write-through (live preview) or an in-flight
            # Generate/Re-Generate batch: the card was/will be updated by the
            # page itself -- a rebuild here would tear down the new tab and
            # rebind the settings form mid-flight (the Generate 'reopen'
            # flicker / Re-Generate blank-chart bugs).
            return
        self._refresh_charts()

    def _on_tab_changed(self, *args) -> None:
        """Bind the settings form to the newly selected chart (per-chart
        settings: prefill + dirty-gate reset) and repaint its view.

        A hidden tab's web view may never have run its pending page load
        while off-screen ("only the last chart shows, other tabs blank") --
        now that the tab is visible, replay its already-computed figure so
        it paints immediately. showEvent on PlotlyWebView covers the same
        case from the widget side; this is the page-side belt-and-braces.
        Runs unconditionally (even when the settings bind is a no-op) so
        every tab switch repaints. Plotly.react is in-place: flicker-free.
        """
        if self._refreshing_charts:
            return
        if self._active_analysis:
            self._bind_selected_chart_settings()
        try:
            idx = self.charts_tabs.currentIndex()
            if 0 <= idx < len(self._tab_order):
                card = self._chart_cards.get(self._tab_order[idx])
                if card is not None:
                    card.refresh_view()
        except Exception:
            log.debug("tab-change view refresh failed", exc_info=True)

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

    def _on_add_chart_slicer(self, uid: str) -> None:
        """Open the slicer form pre-scoped to *uid*.

        The per-chart entry point: the same dialog the dashboard's
        "Add Slicer" opens, but born on "One chart" with this chart already
        chosen, so the scope decision is made right where the user is
        looking at the chart. The result is stored through the bridge --
        slicers belong to the report, not to the analysis page, and the
        dashboard rebuilds itself when it sees ``state_changed("slicers")``.
        """
        df = self.bridge.df
        if df is None or df.empty:
            QMessageBox.warning(self, "No Data",
                                "Load a dataset before adding slicers.")
            return
        from desktop.widgets.slicer_dialog import SlicerDialog

        charts = self.bridge.get("charts", [])
        # Recipes (chart metas) let the dialog report which charts the draft
        # will actually reach -- the same affected_charts() the recompute
        # path uses, so the hint cannot drift from behaviour.
        recipes = {c[0]: self.bridge.get_chart_meta(c[0]) for c in charts}
        dialog = SlicerDialog(df, parent=self, charts=charts,
                              recipes=recipes,
                              preset_scope="One chart", preset_target=uid)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            slicer = dialog.result_slicer()
        except ValueError as exc:
            # make_slicer refuses a chart-scoped slicer with no target; show
            # it as the form error it is rather than swallowing it.
            QMessageBox.warning(self, "Slicer", str(exc))
            return
        slicers = list(self.bridge.get("slicers", []) or [])
        slicers.append(slicer)
        self.bridge.set("slicers", slicers)

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
        finally:
            self._suppress_rebuild = False

        self._build_config_panel(chart_type, target_uid=uid)
        # The visible tab list is filtered by the previously active analysis
        # type. Refresh it only after activating this chart's type, and carry
        # the exact uid through the refresh so the first chart of that type
        # cannot accidentally remain selected.
        self._pending_select_uid = uid
        self._refresh_charts()
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
            idx = self._tab_order.index(uid)
        except ValueError:
            return
        if idx == self.charts_tabs.currentIndex():
            return
        # Programmatic switch: suppress the currentChanged -> full settings
        # rebuild (the caller binds explicitly once afterwards). Without
        # this, every Generate/Re-Generate rebound the form twice -- the
        # visible "reopen" flicker of the whole top panel.
        self._refreshing_charts = True
        try:
            self.charts_tabs.setCurrentIndex(idx)
        finally:
            self._refreshing_charts = False
        self._bind_selected_chart_settings()

    def _on_note_changed(self, uid: str, text: str) -> None:
        """Handle note changes from a chart card."""
        self.bridge.set_note(uid, text)

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
        # A dashboard canvas pencil lands here via navigate_requested with
        # the target uid on the bridge. Consumed once (popped first) so a
        # later plain navigation does not re-open a stale chart.
        try:
            pending = self.bridge.pop("_pending_edit_chart", None)
        except Exception:
            pending = None
        if pending and self.bridge.get(f"chart_type_{pending}", ""):
            self._on_edit_chart(pending)
