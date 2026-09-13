"""desktop/pages/profile_page.py -- Profile page (Qt).

Replaces the Streamlit ``page_profile()`` with a native Qt page that shows
the local guest user profile and provides backup/restore of sessions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QFrame, QGroupBox, QFormLayout,
)

from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from services.database_service import (
    get_or_create_guest_user, export_sessions_to_dict,
    import_sessions_from_dict, invalidate_sessions_cache,
)

log = logging.getLogger(__name__)


class ProfilePage(QWidget):
    """Profile page: user info and backup/restore."""

    navigate_requested = Signal(str)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge

        self._build_ui()
        self._connect_signals()
        self._refresh_user_info()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("👤 Profile")
        title.setProperty("class", "pageTitle")
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        # -- User info card --
        user_card = QFrame()
        user_card.setObjectName("user_card")
        # (background themed by global QSS via objectName "user_card")
        user_layout = QVBoxLayout(user_card)
        user_layout.setContentsMargins(16, 16, 16, 16)
        user_layout.setSpacing(8)

        self.lbl_username = QLabel("")
        self.lbl_username.setProperty("class", "cardTitle"); self.lbl_username.setStyleSheet("font-size: 16px; font-weight: 700;")
        user_layout.addWidget(self.lbl_username)

        self.lbl_session_count = QLabel("")
        self.lbl_session_count.setProperty("class", "faint"); self.lbl_session_count.setStyleSheet("font-size: 12px;")
        user_layout.addWidget(self.lbl_session_count)

        root.addWidget(user_card)

        # -- Backup / Restore section --
        backup_group = QGroupBox("💾 Backup & Restore")
        # (GroupBox themed globally via QSS)
        backup_layout = QVBoxLayout(backup_group)
        backup_layout.setContentsMargins(16, 12, 16, 16)
        backup_layout.setSpacing(8)

        backup_desc = QLabel(
            "Export all your saved sessions to a JSON backup file, or restore "
            "from a previously created backup."
        )
        backup_desc.setProperty("class", "muted"); backup_desc.setStyleSheet("font-size: 12px;")
        backup_desc.setWordWrap(True)
        backup_layout.addWidget(backup_desc)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_backup = QPushButton("⬇️ Export Backup")
        self.btn_backup.setProperty("kind", "primary")
        btn_row.addWidget(self.btn_backup)

        self.btn_restore = QPushButton("⬆️ Restore Backup")
        self.btn_restore.setProperty("kind", "ghost")
        btn_row.addWidget(self.btn_restore)
        # Buttons collapse to their content size; the row's right side stays
        # empty on purpose (UI feedback: half-page-wide buttons looked bloated).
        btn_row.addStretch(1)

        backup_layout.addLayout(btn_row)
        root.addWidget(backup_group)

        # -- Data location info --
        data_group = QGroupBox("📁 Data Location")
        # (GroupBox themed globally via QSS)
        data_layout = QVBoxLayout(data_group)
        data_layout.setContentsMargins(16, 12, 16, 16)

        data_desc = QLabel(
            "All data is stored locally on this device. No data is sent to any server."
        )
        data_desc.setProperty("class", "muted"); data_desc.setStyleSheet("font-size: 12px;")
        data_desc.setWordWrap(True)
        data_layout.addWidget(data_desc)

        self.lbl_db_path = QLabel("")
        self.lbl_db_path.setProperty("class", "faint"); self.lbl_db_path.setStyleSheet("font-size: 11px;")
        self.lbl_db_path.setWordWrap(True)
        data_layout.addWidget(self.lbl_db_path)

        root.addWidget(data_group)
        root.addStretch()

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.btn_backup.clicked.connect(self._on_backup)
        self.btn_restore.clicked.connect(self._on_restore)

    # -- User info ----------------------------------------------------------

    def _refresh_user_info(self) -> None:
        """Refresh the displayed user information."""
        user_id = self.bridge.get("user_id")
        username = self.bridge.get("username", "")

        self.lbl_username.setText(username or "User")

        from services.database_service import get_user_sessions, DB_PATH
        try:
            count = len(get_user_sessions(user_id)) if user_id else 0
        except Exception:
            count = 0
        self.lbl_session_count.setText(
            f"{count} saved session{'s' if count != 1 else ''}"
        )
        self.lbl_db_path.setText(f"Database: {DB_PATH}")

    # -- Backup / Restore ---------------------------------------------------

    def _on_backup(self) -> None:
        """Export all sessions to a JSON backup file."""
        user_id = self.bridge.get("user_id")
        if not user_id:
            QMessageBox.warning(self, "No User", "No user session found.")
            return

        username = self.bridge.get("username", "guest")
        try:
            sessions = export_sessions_to_dict(user_id, username)
            if not sessions:
                QMessageBox.information(self, "No Sessions",
                                        "No saved sessions to export.")
                return

            file_path, _ = QFileDialog.getSaveFileName(
                self, "Export Backup", str(Path.home() / "lytrize_backup.json"),
                "JSON Files (*.json)",
            )
            if not file_path:
                return

            backup = {
                "app": "lytrize",
                "version": 1,
                "exported_at": __import__("datetime").datetime.now().isoformat(),
                "sessions": sessions,
            }
            Path(file_path).write_text(
                json.dumps(backup, indent=2, default=str), encoding="utf-8"
            )
            QMessageBox.information(self, "Backup Complete",
                                    f"Exported {len(sessions)} sessions to:\n{file_path}")
        except Exception as exc:
            log.error("Backup failed: %s", exc)
            QMessageBox.critical(self, "Backup Error", str(exc))

    def _on_restore(self) -> None:
        """Restore sessions from a JSON backup file."""
        user_id = self.bridge.get("user_id")
        if not user_id:
            QMessageBox.warning(self, "No User", "No user session found.")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Restore Backup", str(Path.home()),
            "JSON Files (*.json)",
        )
        if not file_path:
            return

        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
            sessions = data.get("sessions", []) if isinstance(data, dict) else data

            if not sessions:
                QMessageBox.warning(self, "Invalid Backup",
                                    "No sessions found in the backup file.")
                return

            imported, updated, skipped = import_sessions_from_dict(user_id, sessions)
            invalidate_sessions_cache()

            msg = f"Imported: {imported}\nUpdated: {updated}"
            if skipped:
                msg += f"\nSkipped: {len(skipped)}"
            QMessageBox.information(self, "Restore Complete", msg)
        except Exception as exc:
            log.error("Restore failed: %s", exc)
            QMessageBox.critical(self, "Restore Error", str(exc))

    # -- Public API ----------------------------------------------------------

    def refresh(self) -> None:
        """Refresh the page when navigated to."""
        self._refresh_user_info()