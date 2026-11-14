"""desktop/app.py -- Lytrize native desktop application entry point.

This is the primary runtime for the native desktop app.  It replaces the
Streamlit launcher (desktop/gui.py) as the main application surface.

Run with::

    python desktop/app.py
"""

from __future__ import annotations

import logging
import os
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

from desktop import theme
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


#: Font FILES that crash the Qt 6 FreeType backend. The ``.woff2`` icon fonts
#: shipped under /usr/share/fonts (Material Symbols) declare an EMPTY family
#: name, so fontconfig mis-binds them to ordinary families -- ``fc-match
#: Inter`` returns material-symbols-outlined.woff2 -- and drops an icon font
#: into the middle of every fallback chain. Qt then segfaults inside
#: ``FcCharSetHasChar`` while probing it (via QPushButton::sizeHint ->
#: QFontMetrics::boundingRect -> QTextEngine::shapeText), killing the process
#: before its first frame.
#:
#: NOTE: the colour emoji font is deliberately NOT rejected. NotoColorEmoji is
#: the ONLY font on this system carrying glyphs like U+1F5D1 (trash) and
#: U+1F441 (eye); no monochrome fallback is installed. Rejecting it as well
#: stops the crash but renders every one of those glyphs as an empty tofu box.
#: Rejecting the woff2 icon fonts alone fixes the crash AND keeps the emoji
#: drawing correctly.
_CRASHER_FONT_GLOBS = ("*.woff2", "*.woff")


def _guard_crasher_emoji_fonts() -> bool:
    """Keep Qt from picking up icon fonts it segfaults on.

    Installs a private fontconfig config that rejects those files, so text
    shaping falls back to a font Qt can handle safely.

    Must run BEFORE QApplication is constructed -- fontconfig initialises its
    font set on first use, so setting the env var later has no effect.

    Returns True if the guard was installed. An existing FONTCONFIG_FILE set
    by the user is respected and never overwritten.
    """
    if os.environ.get("LYTRIZE_SKIP_FONT_GUARD"):
        return False
    if os.environ.get("FONTCONFIG_FILE"):
        # The user is driving font resolution explicitly; do not interfere.
        return False

    rejects = "".join(f"<glob>{g}</glob>" for g in _CRASHER_FONT_GLOBS)
    conf = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
        "<fontconfig>\n"
        '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
        "  <selectfont><rejectfont>"
        f"{rejects}"
        "</rejectfont></selectfont>\n"
        "</fontconfig>\n"
    )

    try:
        conf_dir = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        conf_dir = conf_dir / "lytrize"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fonts.conf"
        # Rewrite only when changed: fontconfig caches on mtime and a fresh
        # write on every launch would force a full font re-scan each start.
        if not conf_path.exists() or conf_path.read_text() != conf:
            conf_path.write_text(conf)
        os.environ["FONTCONFIG_FILE"] = str(conf_path)
        return True
    except OSError as exc:
        # Non-fatal: the guard is a crash workaround, not a feature.
        log.warning("Could not install the emoji font guard: %s", exc)
        return False


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

    # Must run before QApplication: fontconfig locks in its font set on
    # first use, so this cannot be installed after the app object exists.
    _guard_crasher_emoji_fonts()

    # Prevent Chromium Linux GPU/Vulkan rasterization compositor dropouts
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                          "--disable-vulkan-surface --disable-gpu-rasterization --disable-zero-copy")

    # High-DPI: crisp fractional geometry and high-res pixmaps at any DPI.
    # Both attributes are deprecated and no-ops in Qt 6 (high-DPI scaling is
    # always on), but they are kept so an older Qt still behaves as before.
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    # Register the fonts the app ships (backend/assets/fonts) with Qt. This must be
    # AFTER the QApplication exists -- QFontDatabase needs one -- unlike the
    # fontconfig guard above, which has to run before it. Without this the
    # settings panel's font preview and the combo boxes cannot see the bundled
    # families at all. Idempotent, and a no-op if the directory is missing.
    try:
        from font_bundle import register_qt_fonts
        register_qt_fonts()
    except Exception as exc:
        log.debug("bundled font registration skipped: %s", exc)

    icon_path = _find_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    # Apply the saved (or default) theme (dark/light) BEFORE the window
    # is created so the whole UI starts in the right palette.
    theme.apply_theme(app, theme.load_saved_mode())

    # Create the session bridge (headless SessionManager + Qt signals)
    bridge = SessionBridge()
    # Mirror the current theme into session state so any page/widget that
    # wants to react (e.g. re-theme an embedded Plotly chart) can read it.
    bridge.set("theme_mode", theme.current_mode())

    # Create and show the main window
    window = MainWindow(bridge)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
