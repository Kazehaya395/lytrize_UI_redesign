"""desktop/pages/home_page.py -- Home page (Qt).

Replaces the Streamlit ``page_home()`` with a native Qt page that shows
saved sessions and provides navigation to start a new analysis.
"""

from __future__ import annotations

import getpass
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox, QInputDialog,
    QMenu, QToolButton, QFrame, QSizePolicy,
)

from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from desktop.widgets.banner_label import BannerLabel
from desktop.widgets.stat_card import StatCard
from services.database_service import (
    get_user_sessions, invalidate_sessions_cache,
    delete_session_db, rename_session_db, get_user_stats,
)
from services.analysis_service import ANALYSIS_OPTIONS

log = logging.getLogger(__name__)

#: Where the bundled artwork lives -- the same directory the app icon and the
#: sidebar logo are resolved from (``desktop/app.py``,
#: ``desktop/main_window.py``), so the app has one assets location, not three.
_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "assets"


def _asset_path(name: str) -> Path:
    """Resolve a bundled asset to a path.

    Returns the first candidate that exists, else the preferred one. It must
    NOT raise for a missing file: a packaging mistake should show as a banner
    that quietly does not appear, not crash the page during construction.
    """
    for base in (_ASSETS_DIR,
                 Path(__file__).resolve().parent.parent / "assets"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return _ASSETS_DIR / name


class HomePage(QWidget):
    """Home page with saved sessions and navigation."""

    navigate_requested = Signal(str)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._sessions: list = []
        self._sid_name: dict = {}

        self._build_ui()
        self._connect_signals()
        self._refresh_sessions()
        self._refresh_stats()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)

        # -- Header -------------------------------------------------------
        # ONE row, three parts: the greeting on the left, the official banner in
        # the middle, the Start button hard against the top-right.
        #
        # The banner used to sit in a row of its own above this one, which put
        # the page title and the welcome card on a lower line and left a band of
        # empty space beside the artwork -- the title appeared to float in the
        # middle of the header while the banner towered above it. Sharing one
        # row means the banner, the "Home" title and the welcome message are all
        # on the same line, and the only vertical space is whatever the tallest
        # of the three genuinely needs.
        header = QHBoxLayout()
        header.setSpacing(16)

        # Left: the page title stacked over the welcome card, so they read as one
        # greeting block rather than two unrelated rows.
        greeting = QVBoxLayout()
        greeting.setSpacing(8)
        title = QLabel("🏠 Home")
        title.setProperty("class", "pageTitle")
        greeting.addWidget(title, 0, Qt.AlignLeft)

        try:
            username = getpass.getuser().strip() or "user"
        except Exception:
            username = "user"
        welcome = QFrame()
        welcome.setObjectName("welcome_banner")
        welcome_lay = QVBoxLayout(welcome)
        welcome_lay.setContentsMargins(16, 16, 16, 16)
        welcome_lay.setSpacing(4)
        lbl_welcome_title = QLabel(f"Welcome “{username}” to Lytrize — your local-first analytics workspace.")
        lbl_welcome_title.setProperty("class", "sectionTitle")
        welcome_lay.addWidget(lbl_welcome_title)
        lbl_welcome_desc = QLabel("Import a dataset, generate charts, and compose dashboards — all offline.")
        lbl_welcome_desc.setProperty("class", "muted")
        welcome_lay.addWidget(lbl_welcome_desc)
        greeting.addWidget(welcome)
        # The greeting column takes only the width its text needs; the banner
        # claims the rest. It is wrapped in a plain widget rather than added
        # with an alignment: QBoxLayout.addLayout takes no alignment argument
        # (only addWidget does), and the nested column already hugs its content
        # because both of its widgets are sized to their own text.
        greeting_holder = QWidget()
        greeting_holder.setLayout(greeting)
        header.addWidget(greeting_holder, 0, Qt.AlignTop)

        # Middle: the official banner artwork, scaling with the window
        # (BannerLabel). fill="adapt" so the banner auto-adjusts to the window
        # while keeping the artwork's own proportions and staying WHOLE -- an
        # earlier "cover" render filled the band by cropping the top and bottom
        # off the artwork, which lost the shields, the axis labels and both ends
        # of the chart on a full screen, and a plain "fit" render left a wide
        # empty strip beside a small image. Adapt makes the WIDGET take the
        # artwork's shape instead, so it grows and shrinks with the window,
        # never crops, and never letterboxes.
        # Keep the header compact so the stat cards and the session list stay
        # above the fold at 100% zoom: the banner is 1080x462 (aspect ~2.34),
        # so at a ~390px slot it stands ~167px -- the old 180px floor forced
        # it TALLER than its own aspect wanted, which is exactly what pushed
        # the Sessions section below the scroll. min 90 / max 150 keeps the
        # artwork whole (adapt never crops) but lets it sit at its natural
        # short height.
        self.banner = BannerLabel(str(_asset_path("welcome-banner.png")),
                                  fill="adapt", min_height=90, max_height=150)
        # Stretch 1: the banner is what absorbs the leftover width, and in adapt
        # mode that width is what drives the artwork's size. AlignVCentre keeps
        # the band aligned with the greeting.
        header.addWidget(self.banner, 1, Qt.AlignVCenter)

        # Right: the Start button, pinned to the top-right corner. AlignTop keeps
        # it on the title's line instead of drifting to the vertical middle of
        # however tall the banner turns out to be.
        self.btn_new = QPushButton("🚀 Start New Analysis")
        self.btn_new.setProperty("kind", "primary")
        header.addWidget(self.btn_new, 0, Qt.AlignTop | Qt.AlignRight)

        # Action buttons for selected session are built here (styles only);
        # they are placed next to the "Saved Sessions" section header below,
        # not in this page-level header, since they act on a selected session.
        self.btn_rename = QPushButton("✏️ Rename")
        self.btn_rename.setEnabled(False)
        self.btn_rename.setProperty("kind", "ghost")

        self.btn_delete = QPushButton("🗑 Delete")
        self.btn_delete.setEnabled(False)
        self.btn_delete.setProperty("kind", "danger")

        root.addLayout(header)

        # -- Stat cards: sessions saved, datasets analyzed, analyses run --
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_sessions = StatCard("💾", 0, "Sessions Saved")
        self.stat_datasets = StatCard("📂", 0, "Datasets Analyzed")
        self.stat_analyses = StatCard("📊", 0, "Analyses Available")
        stats_row.addWidget(self.stat_sessions, 0, Qt.AlignLeft)
        stats_row.addWidget(self.stat_datasets, 0, Qt.AlignLeft)
        stats_row.addWidget(self.stat_analyses, 0, Qt.AlignLeft)
        stats_row.addStretch()
        root.addLayout(stats_row)

        # -- Sessions section --
        section_row = QHBoxLayout()
        section = QLabel("💾 Saved Sessions")
        section.setProperty("class", "sectionTitle")
        section_row.addWidget(section)
        section_row.addStretch()
        section_row.addWidget(self.btn_rename)
        section_row.addWidget(self.btn_delete)
        root.addLayout(section_row)

        self.session_list = QListWidget()
        self.session_list.setStyleSheet("font-size: 13px;")
        root.addWidget(self.session_list, stretch=1)

        # -- Empty state --
        self.lbl_empty = QLabel("No saved sessions yet. Start a new analysis to begin.")
        self.lbl_empty.setProperty("class", "faint"); self.lbl_empty.setStyleSheet("font-size: 12px; padding: 8px;")
        self.lbl_empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.lbl_empty)

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.btn_new.clicked.connect(lambda: [self.bridge.clear_session(), self.navigate_requested.emit("upload")])
        self.session_list.itemDoubleClicked.connect(self._on_session_open)
        self.session_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.btn_rename.clicked.connect(self._on_session_rename)
        self.btn_delete.clicked.connect(self._on_session_delete)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._on_context_menu)

    # -- Session management -------------------------------------------------

    def _refresh_sessions(self) -> None:
        """Reload the saved sessions list from the database."""
        user_id = self.bridge.get("user_id")
        if not user_id:
            self._sessions = []
        else:
            self._sessions = get_user_sessions(user_id)

        self.session_list.clear()
        self._sid_name = {}
        for row in self._sessions:
            # row: (id, session_name, file_name, rows, cols, analysis_types, created_at, dataset_uri, dataset_hash, slicers_json)
            sid = row[0]
            name = row[1] or "Untitled Session"
            fname = row[2] or ""
            rows = row[3] or 0
            cols = row[4] or 0
            created = row[6] or ""
            dataset_uri = row[7] if len(row) > 7 else ""
            slicers_json = row[9] if len(row) > 9 else "[]"
            try:
                import json as _json
                n_slicers = len(_json.loads(slicers_json or "[]") or [])
            except Exception:
                n_slicers = 0
            has_bundle = bool(dataset_uri)

            self._sid_name[sid] = name

            # Two-line row instead of one concatenated string. The old code
            # built a single "name  ·  file  ·  rows  ·  date" QLabel and let
            # the row action buttons squeeze it to a few characters wide (the
            # "saved-session rows are squeezed & unreadable" report). A row
            # widget now carries the name on its own line (elided in the
            # MIDDLE so the start and end stay legible) and the file/rows/date
            # meta on a quieter second line.
            item = QListWidgetItem()
            item.setData(Qt.UserRole, sid)
            self.session_list.addItem(item)
            # Give the row a fixed, generous height so the two lines are never
            # crushed to one by the list's default item sizing.
            item.setSizeHint(QSize(0, 56))
            self.session_list.setItemWidget(
                item, self._build_session_row(sid, name, fname, rows, cols, created, has_bundle, n_slicers))

        self.lbl_empty.setVisible(len(self._sessions) == 0)

    def _build_session_row(self, sid, name: str, fname: str,
                           rows: int, cols: int, created: str,
                           has_data: bool = True, n_slicers: int = 0) -> QWidget:
        """A single session row: name + meta on two lines, then the actions.

        The action buttons live in their OWN auto-sized column on the right
        (``setSizePolicy(Maximum, ...)`` plus a parent maximum) so they can
        never steal width from the labels -- that squeezing was the whole
        reason the old row was unreadable.
        """
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(8)

        # -- Text: two stacked lines -----------------------------------------
        text_col = QWidget()
        text_lay = QVBoxLayout(text_col)
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(2)

        name_lbl = QLabel()
        name_lbl_font = QFont(name_lbl.font())
        name_lbl_font.setBold(True)
        name_lbl_font.setPointSize(12)
        name_lbl.setFont(name_lbl_font)
        # Elide in the MIDDLE: a long session name keeps both its start (what
        # the user recognises) and its tail (often a timestamp/size), instead
        # of the default right-elide that hides exactly the distinguishing end.
        name_lbl.setMaximumWidth(420)
        self._set_elided(name_lbl, name)
        text_lay.addWidget(name_lbl)

        status = "🟢 live" if has_data else "🟠 data missing — view-only"
        slicer_bit = (f"{n_slicers} slicer" + ("s" if n_slicers != 1 else "")) if n_slicers else ""
        meta_bits = [b for b in (fname,
                                 (f"{rows:,} rows" if rows else ""),
                                 (f"{cols} cols" if cols else ""),
                                 slicer_bit, status, created) if b]
        meta_lbl = QLabel("  ·  ".join(meta_bits) or "No dataset info")
        meta_lbl.setProperty("class", "faint")
        meta_lbl.setStyleSheet("font-size: 11px;")
        meta_lbl.setMaximumWidth(420)
        self._set_elided(meta_lbl, meta_lbl.text(), right=True)
        text_lay.addWidget(meta_lbl)

        lay.addWidget(text_col, 1)

        # -- Actions: their own column, never squeezing the text -------------
        actions = QWidget()
        actions_lay = QHBoxLayout(actions)
        actions_lay.setContentsMargins(0, 0, 0, 0)
        actions_lay.setSpacing(2)
        actions.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

        def _btn(glyph: str, tip: str, slot) -> QToolButton:
            b = QToolButton()
            b.setText(glyph)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(30, 30)
            b.setStyleSheet("font-size: 15px;")
            b.clicked.connect(slot)
            return b

        actions_lay.addWidget(_btn("📂", "Open",
                                   lambda: self._open_session(sid, name)))
        actions_lay.addWidget(_btn("✏️", "Rename",
                                   lambda: self._rename_session(sid, name)))
        actions_lay.addWidget(_btn("🗑", "Delete",
                                   lambda: self._delete_session(sid, name)))
        lay.addWidget(actions)
        return row

    @staticmethod
    def _set_elided(label: QLabel, text: str, *, right: bool = False) -> None:
        """Set *text* on *label*, elided to its own max width.

        The row is built before it has a real width, so the elision is done
        against ``label.maximumWidth()`` and re-elided lazily on the next
        resize via the label's own ``resizeEvent`` -- but a simple middle/right
        elide at build time already keeps the row readable, which is the fix.
        """
        fm_metrics = label.fontMetrics()
        avail = label.maximumWidth() or 360
        elide = Qt.ElideRight if right else Qt.ElideMiddle
        label.setText(fm_metrics.elidedText(str(text or ""), elide, avail))
        label.setToolTip(str(text or ""))


    def _refresh_stats(self) -> None:
        """Refresh the three summary stat cards from the database."""
        user_id = self.bridge.get("user_id")
        # "Analyses Available" is a property of the build, not of this user's
        # history, so it comes straight from the analysis registry. Reading it
        # from ANALYSIS_OPTIONS rather than hardcoding a number means adding a
        # chart type updates the card with no second place to forget.
        analyses_available = len(ANALYSIS_OPTIONS)
        stats = (
            get_user_stats(user_id, analyses_available=analyses_available)
            if user_id
            else {"sessions": 0, "datasets": 0, "analyses": analyses_available}
        )
        self.stat_sessions.set_value(stats.get("sessions", 0))
        self.stat_datasets.set_value(stats.get("datasets", 0))
        self.stat_analyses.set_value(stats.get("analyses", 0))

    def _on_selection_changed(self) -> None:
        """Enable/disable action buttons based on selection."""
        has_selection = self.session_list.currentItem() is not None
        self.btn_rename.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)

    def _on_session_open(self, item: QListWidgetItem) -> None:
        """Open a saved session in the dashboard (double-click / header path)."""
        sid = item.data(Qt.UserRole)
        if sid is None:
            return
        self._open_session(sid, self._sid_name.get(sid) or "Untitled Session")

    def _on_context_menu(self, position) -> None:
        """Show context menu for session list."""
        item = self.session_list.itemAt(position)
        if not item:
            return

        menu = QMenu()

        action_open = menu.addAction("📂 Open")
        action_rename = menu.addAction("✏️ Rename")
        action_delete = menu.addAction("🗑 Delete")

        action = menu.exec_(self.session_list.viewport().mapToGlobal(position))
        if action == action_open:
            self._on_session_open(item)
        elif action == action_rename:
            self._on_session_rename()
        elif action == action_delete:
            self._on_session_delete()

    def _on_session_rename(self) -> None:
        """Rename the selected session (header button / context menu)."""
        item = self.session_list.currentItem()
        if item is None:
            return
        sid = item.data(Qt.UserRole)
        if sid is None:
            return
        self._rename_session(sid, self._sid_name.get(sid) or "Untitled Session")

    def _on_session_delete(self) -> None:
        """Delete the selected session (header button / context menu)."""
        item = self.session_list.currentItem()
        if item is None:
            return
        sid = item.data(Qt.UserRole)
        if sid is None:
            return
        self._delete_session(sid, self._sid_name.get(sid) or "Untitled Session")

    # -- Action cores (shared by per-row buttons and header/context paths) --

    def _open_session(self, sid, name: str) -> None:
        self.bridge.set("view_session_id", sid)
        self.bridge.set("view_session_name", name)
        self.navigate_requested.emit("dashboard")

    def _rename_session(self, sid, name: str) -> None:
        if sid is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Session", "New name:", text=name,
        )
        if ok and new_name.strip():
            user_id = self.bridge.get("user_id")
            rename_session_db(sid, new_name.strip(), user_id)
            invalidate_sessions_cache()
            self._refresh_sessions()
            self._refresh_stats()

    def _delete_session(self, sid, name: str) -> None:
        if sid is None:
            return
        reply = QMessageBox.question(
            self, "Delete Session",
            f"Delete '{name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            user_id = self.bridge.get("user_id")
            delete_session_db(sid, user_id)
            invalidate_sessions_cache()
            self._refresh_sessions()
            self._refresh_stats()

    # -- Public API ----------------------------------------------------------

    def refresh(self) -> None:
        """Refresh the session list (called when navigating to this page)."""
        self._refresh_sessions()
        self._refresh_stats()