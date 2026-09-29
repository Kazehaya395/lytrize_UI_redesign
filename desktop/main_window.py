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
import math
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QStackedWidget, QFrame, QListWidget,
    QListWidgetItem, QStatusBar, QToolButton,
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

        # QMainWindow ships with an empty menu bar; without a visible menu it
        # can still reserve a strip at the very top (the white band reported
        # above the pages). Hide it -- navigation lives in the sidebar.
        self.menuBar().setVisible(False)

        self._theme_icon_cache: dict[str, QIcon] = {}
        self._build_ui()
        self._build_statusbar()
        self._build_nav()
        self._build_top_rail()
        self._connect_signals()

        # Initialise the user session
        self._init_session()

        # Auto-save draft timer (every 2 minutes)
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.timeout.connect(self._auto_save_draft)
        self._auto_save_timer.start(2 * 60 * 1000)  # 2 minutes

        # Restore the last auto-saved draft. Deferred to the event loop so the
        # window is already up: restoring rebuilds the analysis tabs, and doing
        # that during __init__ meant painting them into a not-yet-shown window.
        QTimer.singleShot(0, self._restore_draft_if_any)

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

        # Outer vertical stack: persistent top rail (hamburger + theme, always
        # visible even when the sidebar is hidden) above the body (sidebar +
        # page stack). The rail spans the full window width so the ☰ button
        # survives a sidebar toggle and the theme icon sits at the corner.
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- Top rail (compact, functional, never an empty band) --
        self.top_rail = QFrame()
        self.top_rail.setObjectName("top_rail")
        self.top_rail.setFixedHeight(38)
        rail_layout = QHBoxLayout(self.top_rail)
        rail_layout.setContentsMargins(8, 0, 8, 0)
        rail_layout.setSpacing(4)

        # Hamburger: toggles ONLY the sidebar, so this button is always
        # available to bring the sidebar back.
        self.btn_sidebar_top = QToolButton()
        self.btn_sidebar_top.setObjectName("topbar_btn")
        self.btn_sidebar_top.setText("☰")
        self.btn_sidebar_top.setToolTip("Toggle sidebar")
        self.btn_sidebar_top.setIconSize(QSize(20, 20))
        self.btn_sidebar_top.clicked.connect(self._toggle_sidebar)
        rail_layout.addWidget(self.btn_sidebar_top)

        rail_layout.addStretch()

        # Theme toggle: pinned to the extreme top-right corner, one global
        # instance, always visible regardless of page or sidebar state.
        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("topbar_btn")
        self.theme_btn.setToolTip("Toggle dark / light theme")
        self.theme_btn.setIconSize(QSize(20, 20))
        self.theme_btn.clicked.connect(self.toggle_theme)
        rail_layout.addWidget(self.theme_btn)
        outer.addWidget(self.top_rail)

        # -- Body: sidebar (left) + page stack (right) --
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # -- Sidebar (left) --
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(12, 16, 12, 16)
        side_layout.setSpacing(4)

        # Logo / brand -- icon and name sit together on one row, vertically centered.
        # A QWidget container (rather than a bare sub-layout) centers reliably
        # when added to the sidebar's vertical layout via AlignHCenter.
        brand_container = QWidget()
        brand_wrap = QHBoxLayout(brand_container)
        brand_wrap.setContentsMargins(0, 0, 0, 0)
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
        brand_wrap.addWidget(brand, Qt.AlignVCenter)
        side_layout.addWidget(brand_container, 0, Qt.AlignHCenter)

        # Subtitle -- bolder and its left edge flush with the logo above so it
        # reads as perfectly parallel under the logo + name row.
        brand_sub = QLabel("Analytics Desktop")
        brand_sub.setObjectName("brand_sub")
        side_layout.addWidget(brand_sub, 0, Qt.AlignHCenter)

        # Navigation list
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("nav_list")
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
        side_layout.addWidget(version)

        body.addWidget(self.sidebar)

        # Sidebar toggle state
        self._sidebar_visible = True

        # -- Main content area --
        self.stack = QStackedWidget()
        self.stack.setObjectName("stack")
        body.addWidget(self.stack, stretch=1)

        outer.addLayout(body)

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

    def _build_top_rail(self) -> None:
        # The rail itself (☰ + theme icon) is created in _build_ui. This hook
        # just syncs the theme glyph + tooltip from the saved mode and wires
        # the app-wide Ctrl+T shortcut. Called once from __init__.
        from desktop import theme as _theme_mod
        self.theme_btn.setIcon(self._icon_for_current())
        self.theme_btn.setToolTip(
            "Switch to light mode (Ctrl+T)" if _theme_mod.current_mode() == "dark"
            else "Switch to dark mode (Ctrl+T)"
        )
        # Ctrl+T toggles the theme app-wide.
        self._theme_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self._theme_shortcut.activated.connect(self.toggle_theme)

    def _theme_icon(self, target_mode: str) -> QIcon:
        """Paint the glyph for the mode we'd switch TO: a crescent moon
        when the target is dark, an amber sun when the target is light.
        (Convention: the button shows the action, not the current state.)
        Painted at the button's 32px icon size -- vector shapes, so it
        stays crisp at HiDPI scales too."""
        size = 32
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        if target_mode == "dark":
            # Slate crescent moon (offers dark mode)
            painter.setBrush(QColor("#94a3b8"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(5, 5, 22, 22)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.drawEllipse(11, 3, 19, 22)
        else:
            # Amber sun: disc + rays (offers light mode)
            painter.setBrush(QColor("#f59e0b"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(6, 6, 19, 19)
            painter.setPen(QPen(QColor("#fbbf24"), 2.5))
            for angle in range(0, 360, 45):
                rad = math.radians(angle)
                x1 = int(16 + 11 * math.cos(rad))
                y1 = int(16 + 11 * math.sin(rad))
                x2 = int(16 + 15 * math.cos(rad))
                y2 = int(16 + 15 * math.sin(rad))
                painter.drawLine(x1, y1, x2, y2)

        painter.end()
        return QIcon(pixmap)

    def _get_theme_icon(self, mode: str) -> QIcon:
        """Return a cached sun/moon icon for *mode* (built once per mode)."""
        if mode not in self._theme_icon_cache:
            self._theme_icon_cache[mode] = self._theme_icon(mode)
        return self._theme_icon_cache[mode]

    def _icon_for_current(self) -> QIcon:
        """Icon showing the mode we'd switch to from the CURRENT theme."""
        from desktop import theme as theme_mod
        cur = theme_mod.current_mode()
        return self._get_theme_icon("light" if cur == "dark" else "dark")

    def toggle_theme(self) -> None:
        """Switch the whole app between dark and light and persist the choice.

        The heavy work (repolishing the full widget tree) is deferred to the
        next event-loop iteration so the button icon + status message update
        within one frame -- the UI never appears frozen even on large tables.
        """
        from PySide6.QtWidgets import QApplication
        from desktop import theme as theme_mod
        from PySide6.QtCore import QTimer

        next_mode = "light" if theme_mod.current_mode() == "dark" else "dark"

        # Instant feedback: swap glyph + status now (one frame, no repolish).
        # The glyph always shows the mode we'd switch to FROM the resulting
        # state, so it stays consistent with the actual theme afterwards.
        self.theme_btn.setIcon(
            self._get_theme_icon("dark" if next_mode == "light" else "light")
        )
        self.theme_btn.setToolTip(
            "Switch to light mode (Ctrl+T)" if next_mode == "dark"
            else "Switch to dark mode (Ctrl+T)"
        )
        self.status.showMessage(f"Theme: {next_mode.title()}", 2000)

        # Defer the expensive repolish so the icon swap paints first.
        def _apply():
            app = QApplication.instance()
            if app is None:
                return
            # Suspend repaints/layout across the whole tree during the switch
            # so Qt resolves the new stylesheet once, not per-widget.
            self.setUpdatesEnabled(False)
            try:
                theme_mod.apply_theme(app, next_mode)
                theme_mod.save_mode(next_mode)
                self.bridge.set("theme_mode", next_mode)
                self.bridge.theme_changed.emit(next_mode)
            finally:
                self.setUpdatesEnabled(True)

        QTimer.singleShot(0, _apply)

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

    def _restore_draft_if_any(self) -> None:
        """Restore the auto-saved draft and navigate back to its page.

        Runs once from a zero-delay timer after the window is up. Guards on
        ``_draft_restored`` so a later rebuild/re-entry can never re-apply a
        stale draft over live work. A failure here is logged and swallowed:
        an unreadable draft must degrade to a normal empty start, never block
        launch.
        """
        if getattr(self, "_draft_restored", False):
            return
        self._draft_restored = True

        try:
            restored = self.bridge.restore_draft()
        except Exception as exc:
            log.warning("Draft restore failed, starting empty: %s", exc)
            return

        if not restored:
            return

        page = self.bridge.get("draft_page", "")
        if page and page != "home" and page in self.pages:
            self.navigate_to(page)
        self.status.showMessage("Restored your last session", 4000)

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
