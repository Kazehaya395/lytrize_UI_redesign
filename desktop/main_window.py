"""desktop/main_window.py -- Lytrize native desktop main window.

A QMainWindow-based shell with:
  - Left sidebar navigation (Home, Upload, Analysis, Dashboard, Profile)
  - QStackedWidget for pages
  - Toolbar, status bar
  - SessionBridge injected into all pages

The Streamlit launcher (desktop/gui.py) stays in place during migration but
is NOT used by this shell.  This is the primary runtime for the native app.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon, QAction, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFrame, QListWidget,
    QListWidgetItem, QStatusBar, QToolBar, QSizePolicy,
)

from desktop.session_bridge import SessionBridge
from desktop.pages.home_page import HomePage
from desktop.pages.upload_page import UploadPage
from desktop.pages.analysis_page import AnalysisPage
from desktop.pages.dashboard_page import DashboardPage
from desktop.pages.profile_page import ProfilePage
from services.database_service import init_db, get_or_create_guest_user

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge

        self.setWindowTitle("Lytrize")
        self.setMinimumSize(1024, 700)
        self.resize(1280, 800)

        self._build_ui()
        self._build_statusbar()
        self._build_nav()
        self._build_toolbar()
        self._connect_signals()

        # Initialise the user session
        self._init_session()

        # Auto-save draft timer (every 2 minutes)
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.timeout.connect(self._auto_save_draft)
        self._auto_save_timer.start(2 * 60 * 1000)  # 2 minutes

    # -- UI construction ----------------------------------------------------

    def _load_logo(self) -> "QPixmap | None":
        """Return a scaled ``QPixmap`` of the app logo for the sidebar brand,
        or ``None`` if the asset is missing."""
        assets = Path(__file__).resolve().parent.parent / "backend" / "assets"
        pixmap = QPixmap()
        for name in ("lytrize.png", "Lytrize.png", "lytrize.ico", "Lytrize.ico"):
            candidate = assets / name
            if candidate.exists() and pixmap.load(str(candidate)):
                return pixmap.scaled(
                    36, 36,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
        return None

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- Sidebar (left) --
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet(
            "QFrame#sidebar { background: #0f172a; border-right: 1px solid #1e293b; }"
        )
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(12, 16, 12, 16)
        side_layout.setSpacing(4)

        # Logo / brand -- icon and name sit together on one row, vertically centered
        brand_wrap = QHBoxLayout()
        brand_wrap.setSpacing(10)
        logo_pm = self._load_logo()
        if logo_pm is not None and not logo_pm.isNull():
            logo_lbl = QLabel()
            logo_lbl.setPixmap(logo_pm)
            logo_lbl.setFixedSize(36, 36)
            logo_lbl.setScaledContents(True)
            brand_wrap.addWidget(logo_lbl, 0, Qt.AlignVCenter)
        brand = QLabel("Lytrize")
        brand.setObjectName("brand")
        brand.setStyleSheet("font-size: 20px; font-weight: 800; color: #a5b4fc;")
        brand_wrap.addWidget(brand, 1, Qt.AlignVCenter)
        side_layout.addLayout(brand_wrap)

        # Subtitle -- bolder and its left edge flush with the logo above so it
        # reads as perfectly parallel under the logo + name row.
        brand_sub = QLabel("Analytics Desktop")
        brand_sub.setObjectName("brand_sub")
        brand_sub.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: #94a3b8; padding: 0 0 12px;"
        )
        side_layout.addWidget(brand_sub)

        # Navigation list
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("nav_list")
        self.nav_list.setStyleSheet(
            "QListWidget#nav_list {"
            "  background: transparent; border: none;"
            "  font-size: 13px; color: #cbd5e1;"
            "}"
            "QListWidget#nav_list::item {"
            "  padding: 10px 12px; border-radius: 8px; margin: 2px 0;"
            "}"
            "QListWidget#nav_list::item:hover {"
            "  background: rgba(99,102,241,0.12);"
            "}"
            "QListWidget#nav_list::item:selected {"
            "  background: rgba(99,102,241,0.20); color: #a5b4fc;"
            "}"
        )
        self.nav_items = [
            ("home",      "🏠  Home"),
            ("upload",    "📂  Upload"),
            ("analysis",  "📊  Analysis"),
            ("dashboard", "📈  Dashboard"),
            ("profile",   "👤  Profile & Backup"),
        ]
        for key, label in self.nav_items:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.nav_list.addItem(item)
        side_layout.addWidget(self.nav_list, stretch=1)

        # Version footer
        version = QLabel("v1.0 · local-first")
        version.setObjectName("version")
        version.setStyleSheet("font-size: 10px; color: #475569; padding: 8px 12px;")
        side_layout.addWidget(version)

        root.addWidget(self.sidebar)

        # Sidebar toggle state
        self._sidebar_visible = True

        # -- Main content area --
        self.stack = QStackedWidget()
        self.stack.setObjectName("stack")
        self.stack.setStyleSheet(
            "QStackedWidget#stack { background: #0b1120; }"
        )
        root.addWidget(self.stack, stretch=1)

        # Add pages
        self.pages = {}

        # Home page
        self.home_page = HomePage(self.bridge)
        self.home_page.navigate_requested.connect(self.navigate_to)
        self.stack.addWidget(self.home_page)
        self.pages["home"] = self.home_page

        # Upload page
        self.upload_page = UploadPage(self.bridge)
        self.upload_page.navigate_requested.connect(self.navigate_to)
        self.stack.addWidget(self.upload_page)
        self.pages["upload"] = self.upload_page

        # Analysis page
        self.analysis_page = AnalysisPage(self.bridge)
        self.analysis_page.navigate_requested.connect(self.navigate_to)
        self.stack.addWidget(self.analysis_page)
        self.pages["analysis"] = self.analysis_page

        # Dashboard page
        self.dashboard_page = DashboardPage(self.bridge)
        self.dashboard_page.navigate_requested.connect(self.navigate_to)
        self.stack.addWidget(self.dashboard_page)
        self.pages["dashboard"] = self.dashboard_page

        # Profile page
        self.profile_page = ProfilePage(self.bridge)
        self.profile_page.navigate_requested.connect(self.navigate_to)
        self.stack.addWidget(self.profile_page)
        self.pages["profile"] = self.profile_page

    def _build_nav(self) -> None:
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self.nav_list.setCurrentRow(0)

    def _build_toolbar(self) -> None:
        """Build the top toolbar with hamburger menu button."""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setStyleSheet(
            "QToolBar { background: #0f172a; border: none; spacing: 8px; padding: 4px 8px; }"
        )
        self.addToolBar(toolbar)

        # Hamburger menu button
        self.btn_hamburger = QPushButton("☰")
        self.btn_hamburger.setFixedSize(36, 36)
        self.btn_hamburger.setStyleSheet(
            "QPushButton {"
            "  background: transparent; color: #e2e8f0; border: none;"
            "  font-size: 18px; font-weight: bold; border-radius: 6px;"
            "}"
            "QPushButton:hover { background: rgba(99,102,241,0.15); }"
        )
        self.btn_hamburger.setToolTip("Toggle sidebar")
        self.btn_hamburger.clicked.connect(self._toggle_sidebar)
        toolbar.addWidget(self.btn_hamburger)

        # Spacer to push subsequent items to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

    def _toggle_sidebar(self) -> None:
        """Toggle sidebar visibility."""
        self._sidebar_visible = not self._sidebar_visible
        if self._sidebar_visible:
            self.sidebar.show()
            self.sidebar.setFixedWidth(220)
        else:
            self.sidebar.hide()
            self.sidebar.setFixedWidth(0)

    def _build_statusbar(self) -> None:
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")
        # Add a persistent status label on the right
        self.status_user = QLabel("")
        self.status.addPermanentWidget(self.status_user)

    def _connect_signals(self) -> None:
        self.bridge.user_changed.connect(self._on_user_changed)

    # -- Session initialisation ----------------------------------------------

    def _init_session(self) -> None:
        """Initialise the database and guest user on startup."""
        try:
            init_db()
        except Exception as exc:
            log.warning("init_db failed: %s", exc)

        try:
            guest = get_or_create_guest_user()
            if guest.get("id") is not None:
                self.bridge.set("user_id", guest["id"])
                self.bridge.set("username", guest["username"])
                self.bridge.set("is_guest", True)
                self.bridge.user_changed.emit(guest["id"])
        except Exception as exc:
            log.warning("get_or_create_guest_user failed: %s", exc)

    # -- Slots ---------------------------------------------------------------

    def _on_nav_changed(self, row: int) -> None:
        if row < 0 or row >= len(self.nav_items):
            return
        key = self.nav_items[row][0]
        self.stack.setCurrentWidget(self.pages[key])
        self.bridge.set("page", key)
        self.status.showMessage(f"Page: {key}")

        # Refresh page-specific data when navigating
        if key == "home" and hasattr(self, "home_page"):
            self.home_page.refresh()
        elif key == "analysis" and hasattr(self, "analysis_page"):
            self.analysis_page.refresh()
        elif key == "dashboard" and hasattr(self, "dashboard_page"):
            self.dashboard_page.refresh()
        elif key == "profile" and hasattr(self, "profile_page"):
            self.profile_page.refresh()

    def _on_user_changed(self, user_id) -> None:
        self.status_user.setText(f"User: {user_id}")

    def _auto_save_draft(self) -> None:
        """Auto-save the current draft state."""
        user_id = self.bridge.get("user_id")
        if not user_id:
            return

        df = self.bridge.df
        if df is None:
            return

        try:
            self.bridge.save_draft()
            self.status.showMessage("Draft auto-saved", 3000)
        except Exception as exc:
            log.warning("Auto-save draft failed: %s", exc)

    # -- Convenience ---------------------------------------------------------

    def navigate_to(self, page_key: str) -> None:
        """Programmatically switch to a page by key."""
        for i, (key, _label) in enumerate(self.nav_items):
            if key == page_key:
                self.nav_list.setCurrentRow(i)
                return

    def toggle_sidebar(self) -> None:
        """Public method to toggle sidebar from other components."""
        self._toggle_sidebar()
