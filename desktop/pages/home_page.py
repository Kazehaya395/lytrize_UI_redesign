"""desktop/pages/home_page.py -- Home page (Qt).

Replaces the Streamlit ``page_home()`` with a native Qt page that shows
saved sessions and provides navigation to start a new analysis.
"""

from __future__ import annotations

import getpass
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox, QInputDialog,
    QMenu, QToolButton,
)

from desktop.session_bridge import SessionBridge
from desktop.widgets.stat_card import StatCard
from services.database_service import (
    get_user_sessions, invalidate_sessions_cache,
    delete_session_db, rename_session_db, get_user_stats,
)

log = logging.getLogger(__name__)


class HomePage(QWidget):
    """Home page with saved sessions and navigation."""

    navigate_requested = Signal(str)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._sessions: list = []

        self._build_ui()
        self._connect_signals()
        self._refresh_sessions()
        self._refresh_stats()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("🏠 Home")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #e2e8f0;")
        header.addWidget(title)
        header.addStretch()

        self.btn_new = QPushButton("🚀 Start New Analysis")
        self.btn_new.setStyleSheet(
            "QPushButton {"
            "  background: #4f46e5; color: white; border: none;"
            "  border-radius: 8px; padding: 8px 16px; font-weight: 600;"
            "}"
            "QPushButton:hover { background: #6366f1; }"
        )
        header.addWidget(self.btn_new)

        # Action buttons for selected session are built here (styles only);
        # they are placed next to the "Saved Sessions" section header below,
        # not in this page-level header, since they act on a selected session.
        self.btn_rename = QPushButton("✏️ Rename")
        self.btn_rename.setEnabled(False)
        self.btn_rename.setStyleSheet(
            "QPushButton { background: #1e293b; color: #94a3b8; border: 1px solid #334155;"
            "  border-radius: 8px; padding: 6px 14px; font-weight: 600; }"
            "QPushButton:hover { background: #334155; color: #e2e8f0; }"
            "QPushButton:disabled { color: #475569; border-color: #1e293b; }"
        )

        self.btn_delete = QPushButton("🗑 Delete")
        self.btn_delete.setEnabled(False)
        self.btn_delete.setStyleSheet(
            "QPushButton { background: rgba(239,68,68,0.1); color: #f87171;"
            "  border: 1px solid rgba(239,68,68,0.3); border-radius: 8px;"
            "  padding: 6px 14px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(239,68,68,0.2); }"
            "QPushButton:disabled { background: transparent; color: #475569; border-color: #1e293b; }"
        )

        root.addLayout(header)

        # -- Welcome banner --
        try:
            username = getpass.getuser().strip() or "user"
        except Exception:
            username = "user"
        welcome = QLabel(
            f"<span style='font-size:16px; font-weight:700; color:#e2e8f0;'>"
            f"Welcome “{username}” to Lytrize — your local-first analytics workspace.</span>"
            f"<br>"
            f"<span style='font-size:13px; color:#94a3b8;'>"
            f"Import a dataset, generate charts, and compose dashboards — all offline.</span>"
        )
        welcome.setStyleSheet(
            "font-size: 13px; color: #94a3b8;"
            "background: #0f172a; border: 1px solid #1e293b;"
            "border-radius: 8px; padding: 16px;"
        )
        welcome.setWordWrap(True)
        root.addWidget(welcome)

        # -- Stat cards: sessions saved, datasets analyzed, analyses run --
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_sessions = StatCard("💾", 0, "Sessions Saved")
        self.stat_datasets = StatCard("📂", 0, "Datasets Analyzed")
        self.stat_analyses = StatCard("📊", 0, "Analyses Available")
        stats_row.addWidget(self.stat_sessions)
        stats_row.addWidget(self.stat_datasets)
        stats_row.addWidget(self.stat_analyses)
        stats_row.addStretch()
        root.addLayout(stats_row)

        # -- Sessions section --
        section_row = QHBoxLayout()
        section = QLabel("💾 Saved Sessions")
        section.setStyleSheet("font-size: 15px; font-weight: 600; color: #cbd5e1; margin-top: 8px;")
        section_row.addWidget(section)
        section_row.addStretch()
        section_row.addWidget(self.btn_rename)
        section_row.addWidget(self.btn_delete)
        root.addLayout(section_row)

        self.session_list = QListWidget()
        self.session_list.setStyleSheet(
            "QListWidget {"
            "  background: #0f172a; color: #e2e8f0;"
            "  border: 1px solid #1e293b; border-radius: 8px;"
            "  font-size: 13px;"
            "}"
            "QListWidget::item { padding: 10px 12px; border-bottom: 1px solid #1e293b; }"
            "QListWidget::item:hover { background: rgba(99,102,241,0.1); }"
            "QListWidget::item:selected { background: rgba(99,102,241,0.2); }"
        )
        root.addWidget(self.session_list, stretch=1)

        # -- Empty state --
        self.lbl_empty = QLabel("No saved sessions yet. Start a new analysis to begin.")
        self.lbl_empty.setStyleSheet("font-size: 12px; color: #64748b; padding: 8px;")
        self.lbl_empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.lbl_empty)

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.btn_new.clicked.connect(lambda: self.navigate_requested.emit("upload"))
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
        for row in self._sessions:
            # row: (id, session_name, file_name, rows_count, cols_count, analysis_types, created_at)
            sid = row[0]
            name = row[1] or "Untitled Session"
            fname = row[2] or ""
            rows = row[3] or 0
            cols = row[4] or 0
            created = row[6] or ""

            label = f"{name}"
            if fname:
                label += f"  ·  {fname}"
            if rows:
                label += f"  ·  {rows:,} rows"
            if created:
                label += f"  ·  {created}"

            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, sid)
            self.session_list.addItem(item)

        self.lbl_empty.setVisible(len(self._sessions) == 0)

    def _refresh_stats(self) -> None:
        """Refresh the three summary stat cards from the database."""
        user_id = self.bridge.get("user_id")
        stats = get_user_stats(user_id) if user_id else {"sessions": 0, "datasets": 0, "analyses": 0}
        self.stat_sessions.set_value(stats.get("sessions", 0))
        self.stat_datasets.set_value(stats.get("datasets", 0))
        self.stat_analyses.set_value(stats.get("analyses", 0))

    def _on_selection_changed(self) -> None:
        """Enable/disable action buttons based on selection."""
        has_selection = self.session_list.currentItem() is not None
        self.btn_rename.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)

    def _on_session_open(self, item: QListWidgetItem) -> None:
        """Open a saved session in the dashboard."""
        sid = item.data(Qt.UserRole)
        if sid is None:
            return
        self.bridge.set("view_session_id", sid)
        self.bridge.set("view_session_name", item.text().split("  ·  ")[0])
        self.navigate_requested.emit("dashboard")

    def _on_context_menu(self, position) -> None:
        """Show context menu for session list."""
        item = self.session_list.itemAt(position)
        if not item:
            return

        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background: #1e293b; color: #e2e8f0;"
            "  border: 1px solid #334155; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; }"
            "QMenu::item:selected { background: rgba(99,102,241,0.3); }"
        )

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
        """Rename the selected session."""
        item = self.session_list.currentItem()
        if item is None:
            return
        sid = item.data(Qt.UserRole)
        if sid is None:
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename Session", "New name:",
            text=item.text().split("  ·  ")[0],
        )
        if ok and new_name.strip():
            user_id = self.bridge.get("user_id")
            rename_session_db(sid, new_name.strip(), user_id)
            invalidate_sessions_cache()
            self._refresh_sessions()
            self._refresh_stats()

    def _on_session_delete(self) -> None:
        """Delete the selected session."""
        item = self.session_list.currentItem()
        if item is None:
            return
        sid = item.data(Qt.UserRole)
        if sid is None:
            return

        reply = QMessageBox.question(
            self, "Delete Session",
            f"Delete '{item.text().split('  ·  ')[0]}'? This cannot be undone.",
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