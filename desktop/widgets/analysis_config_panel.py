"""desktop/widgets/analysis_config_panel.py -- tabbed top-half shell.

Top half has three header tabs: Chart Generate/Edit, Layout Options,
Typography Options. Selecting a tab shows ONLY that setting group.
Option fields intentionally arrive later; this phase only builds the
correct tab shell. Chart add/delete lives on the Analysis page next
to Generate, not in these tabs.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QTabBar, QStackedWidget,
)

from chart_style import default_style


class AnalysisConfigPanel(QFrame):
    """Tabbed top-half settings shell (structure only in this phase)."""

    value_changed = Signal(dict)
    collapse_toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("analysis_config_panel")

        # Injected by parent
        self._spec: list[tuple[str, str, str]] = []
        self._editing = False
        self._active_aid: Optional[str] = None

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

        # Pages: one visible setting group at a time (no options work yet).
        self.row1 = QStackedWidget()
        self.body_generate = QWidget()
        self.body_layout = QWidget()
        self.body_typo = QWidget()
        for _page in (self.body_generate, self.body_layout, self.body_typo):
            _lay = QVBoxLayout(_page)
            _lay.setContentsMargins(0, 6, 0, 0)
            _lay.setSpacing(6)
        self.row1.addWidget(self.body_generate)
        self.row1.addWidget(self.body_layout)
        self.row1.addWidget(self.body_typo)
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
        """Rebuild the shell for analysis type *aid* (tab/page skeleton only)."""
        self._active_aid = aid
        self._editing = editing
        self.lbl_title.setText(("✏️  Edit — " if editing else "⚙️  Configure — ") + name)
        if self.tab_bar.currentIndex() != 0:
            self.tab_bar.setCurrentIndex(0)
        self._rebuild()

    def set_chart_number(self, n: int) -> None:
        """Compatibility stub: the "Chart N" label no longer exists.
        Kept so AnalysisPage does not need changes in this phase."""

    def prefill(self, values: dict) -> None:
        """No option fields exist in this phase; nothing to pre-populate."""

    def collect_values(self) -> dict:
        """No option fields exist in this phase; always empty."""
        return {}

    def current_style(self) -> dict:
        return dict(self._style)

    def set_style(self, style: dict) -> None:
        self._style = {**default_style(), **(style or {})}

    # -- Internal builders -----------------------------------------------

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
                w.deleteLater()

    def _rebuild(self) -> None:
        # Clear skeleton pages only (no options fields in this phase).
        for _page in (self.body_generate, self.body_layout, self.body_typo):
            self._clear_page(_page)

        # Option fields are out of scope for this phase: keep all three
        # skeleton pages empty and clearly labelled so the tab shell can be
        # reviewed before any inputs are added.
        for _page, _name in (
            (self.body_generate, "Chart generate/edit options go here"),
            (self.body_layout, "Layout options go here"),
            (self.body_typo, "Typography options go here"),
        ):
            _lay = _page.layout()
            _lbl = QLabel(_name)
            _lbl.setStyleSheet("font-size: 11px;")
            _lay.addWidget(_lbl)

    # -- Collapse --------------------------------------------------------

    def _on_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self.row1.setVisible(not self._collapsed)
        self.btn_collapse.setText("v" if self._collapsed else "^")
        self.btn_collapse.setToolTip(
            "Expand settings" if self._collapsed else "Collapse settings"
        )
        self.collapse_toggled.emit(self._collapsed)

