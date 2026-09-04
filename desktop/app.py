"""desktop/app.py -- Lytrize native desktop application entry point.

This is the primary runtime for the native desktop app.  It replaces the
Streamlit launcher (desktop/gui.py) as the main application surface.

Run with::

    python desktop/app.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure backend/ is on the import path so `modules.*` and `services.*`
# resolve correctly regardless of how the app is launched.
BASE = Path(__file__).resolve().parent.parent
BACKEND = BASE / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from desktop.main_window import MainWindow
from desktop.session_bridge import SessionBridge

log = logging.getLogger(__name__)


def _find_icon() -> str:
    """Return the path to the app icon, or an empty string."""
    assets = BACKEND / "assets"
    for name in ("lytrize.png", "Lytrize.png", "lytrize.ico", "Lytrize.ico"):
        candidate = assets / name
        if candidate.exists():
            return str(candidate)
    return ""


def main() -> int:
    """Run the Lytrize desktop application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    QApplication.setApplicationName("Lytrize")
    QApplication.setOrganizationName("Lytrize")
    QApplication.setApplicationDisplayName("Lytrize")
    QApplication.setDesktopFileName("lytrize")

    # High-DPI: crisp fractional geometry and high-res pixmaps at any DPI.
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    icon_path = _find_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    # Create the session bridge (headless SessionManager + Qt signals)
    bridge = SessionBridge()

    # Create and show the main window
    window = MainWindow(bridge)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())