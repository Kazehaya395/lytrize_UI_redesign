"""desktop/widgets/analysis_config_panel.py -- tabbed top-half settings host.

Top half has three header tabs: Chart Generate/Edit, Layout Options,
Typography Options. Selecting a tab shows ONLY that setting group.

The panel is a *host*: for every analysis type that has a builder
registered in ``desktop.widgets.analysis_settings`` (currently
``statistical``), the builder's three tab bodies are embedded here and
its ``values_changed`` signal is relayed as ``settings_changed(kind,
payload)``. Types without a builder keep the labelled placeholder pages
but still get the same Generate / Re-Generate action row, so chart types
can be migrated one at a time (register a builder -> full settings UI).

The Generate / Re-Generate pair lives here (right-aligned inside the
Chart Generate/Edit tab, matching the design mockup) instead of the
chart-tabs strip below. Re-Generate is only enabled when the host calls
``set_regenerate_enabled(True)`` -- AnalysisPage gates that on
"an existing chart is selected AND the form is dirty".
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QTabBar, QStackedWidget, QScrollArea, QSizePolicy,
)

from chart_style import default_style
from chart_display import default_layout_opts, default_typo_opts

log = logging.getLogger(__name__)

# Dashed "replace the previous chart" look for Re-Generate (the design
# calls it out with a dashed border so it reads as the *edit* action).
_REGENERATE_QSS = """
QPushButton[kind="regenerate"] {
    background: transparent; border: 1px dashed #6163df; color: #6163df;
    border-radius: 8px; padding: 8px 16px; font-weight: 600;
}
QPushButton[kind="regenerate"]:hover { background: rgba(97, 99, 223, 40); }
QPushButton[kind="regenerate"]:disabled { border: 1px dashed #94a3b8; color: #94a3b8; }
"""


class AnalysisConfigPanel(QFrame):
    """Tabbed top-half settings host (builder-driven 3-tab body)."""

    value_changed = Signal(dict)          # legacy raw payload relay
    collapse_toggled = Signal(bool)
    settings_changed = Signal(str, dict)  # kind: values|layout|typography
    generate_requested = Signal()         # user pressed Generate (new chart)
    regenerate_requested = Signal()       # user pressed Re-Generate (in place)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("analysis_config_panel")

        # Injected by parent
        self._spec: list[tuple[str, str, str]] = []
        self._editing = False
        self._active_aid: Optional[str] = None
        # Header state: which analysis type is on screen and, when the form is
        # bound to an existing chart, which one ("Edit — Statistical · Chart 2")
        # -- with several charts of one analysis open, the bind target is not
        # otherwise obvious from the form itself.
        self._active_name: str = ""
        self._chart_no: int = 0
        self._chart_title: str = ""
        self._ctx: dict = {}
        self._builder = None
        self._actions_busy = False
        self._generate_label = "Generate"

        self._style = dict(default_style())

        self._build_ui()

    # -- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # Row0: pinned strip (chart tabs proxy + Layout/Typography)
        self.row0 = QWidget()
        r0 = QHBoxLayout(self.row0)
        r0.setContentsMargins(0, 0, 0, 0)
        r0.setSpacing(6)

        self.lbl_title = QLabel("Configure")
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: 700;")
        r0.addWidget(self.lbl_title)

        # Tab section: only the selected setting group shows below.
        self.tab_bar = QTabBar()
        self.tab_bar.addTab("Chart Generate/Edit")
        self.tab_bar.addTab("Layout Options")
        self.tab_bar.addTab("Typography Options")
        self.tab_bar.setExpanding(False)
        self.tab_bar.currentChanged.connect(self._on_tab_switched)
        r0.addWidget(self.tab_bar)

        r0.addStretch()

        self.btn_collapse = QPushButton("^")
        self.btn_collapse.setFixedSize(26, 26)
        self.btn_collapse.setToolTip("Collapse settings")
        self.btn_collapse.setProperty("kind", "icon")
        self.btn_collapse.clicked.connect(self._on_collapse)
        r0.addWidget(self.btn_collapse)

        root.addWidget(self.row0)

        # Pages: one visible setting group at a time. Each body is wrapped
        # in a scroll area so long settings lists (Layout has 10 rows,
        # Typography ~12) scroll instead of clipping when the splitter's top
        # pane is short -- the chart area below stays prominent per design.
        self.row1 = QStackedWidget()
        self.body_generate = QWidget()
        self.body_layout = QWidget()
        self.body_typo = QWidget()
        for _page in (self.body_generate, self.body_layout, self.body_typo):
            _lay = QVBoxLayout(_page)
            _lay.setContentsMargins(0, 6, 0, 0)
            _lay.setSpacing(6)
        self.scroll_generate = self._make_scroll(self.body_generate)
        self.scroll_layout = self._make_scroll(self.body_layout)
        self.scroll_typo = self._make_scroll(self.body_typo)
        self.row1.addWidget(self.scroll_generate)
        self.row1.addWidget(self.scroll_layout)
        self.row1.addWidget(self.scroll_typo)
        root.addWidget(self.row1)

        self._collapsed = False

    # -- Public API (parent injects logic) -------------------------------

    def set_logic(
        self,
        spec: list[tuple[str, str, str]],
        scalar_options_fn: Callable,
        field_group_fn: Callable,
    ) -> None:
        """Inject the per-type WIDGET_SPEC + helper closures."""
        self._spec = spec
        self._scalar_options_fn = scalar_options_fn
        self._field_group_fn = field_group_fn

    def set_active(self, aid: str, name: str, editing: bool = False) -> None:
        """Rebuild the shell for analysis type *aid* (builder or placeholder)."""
        self._active_aid = aid
        self._active_name = name
        self._editing = editing
        self._chart_no = 0
        self._chart_title = ""
        self._refresh_header()
        if self.tab_bar.currentIndex() != 0:
            self.tab_bar.setCurrentIndex(0)
        self._rebuild()

    def set_context(self, **ctx) -> None:
        """Column/palette pools for the settings builder (num_cols, cat_cols,
        dt_cols, palette_names, all_cols, group_cols)."""
        self._ctx = {k: v for k, v in ctx.items() if v is not None}

    def set_chart_number(self, n: int, title: str = "") -> None:
        """Name the chart this form is bound to in the header, e.g.
        "✏️ Edit — Statistical · Chart 2".

        *n* is the chart's position among the charts of the SAME analysis type
        (the same numbering the compact tab labels use, "Statistical 2"), and
        *title* is the chart's full title, shown as the header's tooltip since
        it is usually too long to print. ``n=0`` means "not bound to an
        existing chart" (a fresh blank form).
        """
        self._chart_no = int(n) if n else 0
        self._chart_title = title or ""
        self._refresh_header()

    def _refresh_header(self) -> None:
        """Rebuild the pinned header text from the current bind state."""
        prefix = "✏️  Edit — " if self._editing else "⚙️  Configure — "
        label = prefix + (self._active_name or "")
        if self._chart_no:
            label += f"   ·   Chart {self._chart_no}"
        self.lbl_title.setText(label)
        self.lbl_title.setToolTip(self._chart_title or "")

    # -- Builder delegation ------------------------------------------------

    def collect_values(self) -> dict:
        if self._builder is not None:
            try:
                return dict(self._builder.collect_values())
            except Exception:
                log.exception("collect_values failed")
        return {}

    def collect_layout(self) -> dict:
        if self._builder is not None:
            try:
                return dict(self._builder.collect_layout())
            except Exception:
                log.exception("collect_layout failed")
        return dict(default_layout_opts())

    def collect_typography(self) -> dict:
        if self._builder is not None:
            try:
                return dict(self._builder.collect_typography())
            except Exception:
                log.exception("collect_typography failed")
        return dict(default_typo_opts())

    def prefill(self, values: dict) -> None:
        if self._builder is not None:
            try:
                self._builder.prefill(values)
            except Exception:
                log.exception("prefill failed")

    def prefill_layout(self, values: dict) -> None:
        if self._builder is not None:
            try:
                self._builder.prefill_layout(values)
            except Exception:
                log.exception("prefill_layout failed")

    def prefill_typography(self, values: dict) -> None:
        if self._builder is not None:
            try:
                self._builder.prefill_typography(values)
            except Exception:
                log.exception("prefill_typography failed")

    def is_dirty(self) -> bool:
        if self._builder is not None:
            try:
                return bool(self._builder.dirty())
            except Exception:
                pass
        return False

    def reset_dirty(self) -> None:
        if self._builder is not None:
            try:
                self._builder.reset_dirty()
            except Exception:
                pass

    def has_builder(self) -> bool:
        return self._builder is not None

    # -- Action row (Generate / Re-Generate) --------------------------------

    def action_button(self) -> Optional[QPushButton]:
        return getattr(self, "btn_generate", None)

    def regenerate_button(self) -> Optional[QPushButton]:
        return getattr(self, "btn_regenerate", None)

    def set_regenerate_enabled(self, enabled: bool) -> None:
        btn = getattr(self, "btn_regenerate", None)
        if btn is None or self._actions_busy:
            return
        btn.setEnabled(bool(enabled))

    def set_actions_busy(self, busy: bool, text: Optional[str] = None) -> None:
        """Disable both action buttons while a generation worker runs.
        On finish (busy=False) the caller re-applies the dirty-gate with
        set_regenerate_enabled()."""
        gen = getattr(self, "btn_generate", None)
        regen = getattr(self, "btn_regenerate", None)
        self._actions_busy = bool(busy)
        if gen is not None:
            if busy:
                self._generate_label = gen.text()
                gen.setEnabled(False)
                gen.setText(text or "⏳ Working…")
            else:
                gen.setEnabled(True)
                gen.setText(self._generate_label or "Generate")
        if regen is not None:
            if busy:
                regen.setEnabled(False)

    def _build_action_row(self) -> QWidget:
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        hl.addStretch()
        col = QVBoxLayout()
        col.setSpacing(6)

        self.btn_regenerate = QPushButton("Re-Generate")
        self.btn_regenerate.setProperty("kind", "regenerate")
        self.btn_regenerate.setStyleSheet(_REGENERATE_QSS)
        self.btn_regenerate.setEnabled(False)
        self.btn_regenerate.setToolTip(
            "Replace the selected chart in place — enabled after you change a setting")
        self.btn_regenerate.clicked.connect(self.regenerate_requested.emit)
        col.addWidget(self.btn_regenerate)

        self.btn_generate = QPushButton("Generate")
        self.btn_generate.setProperty("kind", "primary")
        self.btn_generate.setMinimumHeight(30)
        self.btn_generate.setToolTip("Generate a new chart")
        self.btn_generate.clicked.connect(self.generate_requested.emit)
        col.addWidget(self.btn_generate)

        hl.addLayout(col)
        return row

    # -- Style passthrough ---------------------------------------------------

    def current_style(self) -> dict:
        return dict(self._style)

    def set_style(self, style: dict) -> None:
        self._style = {**default_style(), **(style or {})}

    # -- Internal builders -----------------------------------------------

    @staticmethod
    def _make_scroll(inner: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidget(inner)
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        sc.setObjectName("settings_scroll")
        return sc

    def current_body(self) -> QWidget:
        """The body widget behind the current tab (row1 holds scroll areas
        wrapping the bodies)."""
        w = self.row1.currentWidget()
        inner = w.widget() if isinstance(w, QScrollArea) else None
        return inner if inner is not None else w

    def _on_tab_switched(self, index: int) -> None:
        """Show only the selected setting-group page."""
        self.row1.setCurrentIndex(index)

    def _clear_page(self, page: QWidget) -> None:
        lay = page.layout()
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # Detach NOW so stale controls can't be found via
                # findChildren (e.g. by tests) while deleteLater waits for
                # the event loop. hide() first: setParent(None) on a visible
                # widget would promote it to a top-level window.
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _on_builder_values(self, payload: dict) -> None:
        """Relay a builder's values_changed payload with its kind."""
        if not isinstance(payload, dict):
            return
        if "layout" in payload:
            kind = "layout"
        elif "typography" in payload:
            kind = "typography"
        else:
            kind = "values"
        self.settings_changed.emit(kind, payload)
        self.value_changed.emit(payload)

    def _rebuild(self) -> None:
        for _page in (self.body_generate, self.body_layout, self.body_typo):
            self._clear_page(_page)
        self._builder = None

        builder = None
        if self._active_aid:
            try:
                from desktop.widgets.analysis_settings import get_builder
                builder = get_builder(self._active_aid, self, **self._ctx)
            except Exception:
                log.exception("settings builder lookup failed for %s", self._active_aid)
                builder = None

        gen_lay = self.body_generate.layout()

        if builder is not None:
            try:
                builder.values_changed.connect(self._on_builder_values)
                builder.generate_requested.connect(self.generate_requested.emit)
                gen_lay.addWidget(builder.build_generate())
                self.body_layout.layout().addWidget(builder.build_layout())
                self.body_typo.layout().addWidget(builder.build_typography())
                self._builder = builder
            except Exception:
                log.exception("settings builder build failed for %s", self._active_aid)
                builder = None

        if self._builder is None:
            # Placeholder pages for types whose builder hasn't been ported
            # yet -- generation still works with the type's defaults.
            for _page, _name in (
                (self.body_generate, "Chart generate/edit options go here"),
                (self.body_layout, "Layout options go here"),
                (self.body_typo, "Typography options go here"),
            ):
                _lay = _page.layout()
                _lbl = QLabel(_name)
                _lbl.setStyleSheet("font-size: 11px;")
                _lay.addWidget(_lbl)

        # Generate / Re-Generate always live in the Generate tab (design).
        gen_lay.addWidget(self._build_action_row())
        gen_lay.addStretch()
        # A fresh action row starts from the clean (Re-Generate disabled) state.
        self.set_regenerate_enabled(False)

    # -- Collapse --------------------------------------------------------

    def expand(self) -> None:
        """Re-open the settings body if it is collapsed (no-op otherwise).

        Used by the page's Edit / New-chart actions: both rebuild the form,
        which the user would never see while the pane is collapsed.
        Emits collapse_toggled(False) like the toggle button does, so the
        page's splitter sizing stays in one place.
        """
        if self._collapsed:
            self._on_collapse()

    def _on_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self.row1.setVisible(not self._collapsed)
        self.btn_collapse.setText("v" if self._collapsed else "^")
        self.btn_collapse.setToolTip(
            "Expand settings" if self._collapsed else "Collapse settings"
        )
        self.collapse_toggled.emit(self._collapsed)

