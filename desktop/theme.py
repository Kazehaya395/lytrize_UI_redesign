"""desktop/theme.py -- Lytrize design language + global theming (dark / light).

Single source of truth for the app's colour tokens, fonts and the reusable
Qt stylesheet fragments that give tables (header row, row-index gutter),
inputs, cards and the whole window shell their consistent, Lytrize-brand
look in BOTH a dark and a light palette.

Two theme modes are supported: "dark" (default, the historical Lytrize
look) and "light".  Every colour used by the app should come from one
of the token dicts in THEMES so that switching modes re-colours every
widget.  The recommended usage:

1. apply_theme(app, mode) once at startup (see desktop.app.main).
2. To switch modes at runtime (toolbar toggle), just call
   apply_theme(QApplication.instance(), new_mode) again.  The stylesheet
   is re-applied (Qt caches palette() references resolved at polish
   time, so a palette-only change leaves colors stale); the setStyle/
   setFont calls run only on the first apply.  The toolbar button swaps
   its glyph synchronously so the click feels instant; the ~0.8s
   re-polish happens on the next event-loop iteration.
3. Widgets that need a bespoke shape (rounded cards, accent headers,
   button kinds) are styled by object-name / dynamic-property selectors
   in the global QSS instead of per-widget setStyleSheet() calls, so
   nothing hard-codes a single palette into the widgets themselves.

The dark palette is the historical Lytrize look (indigo accent, deep
slate surfaces).  The light palette is a clean near-white alternative
with the same indigo accent and role structure.

Only QSS + QPalette are used -- no extra runtime dependency (in
particular we deliberately do NOT pull in qdarktheme/qfluentwidgets
since they are not in requirements.txt).
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette


# Brand tokens (shared by both modes)
ACCENT = "#4f46e5"
ACCENT_HOVER = "#6366f1"
ACCENT_LIGHT = "#818cf8"

# Page-level layout margins (left, top, right, bottom) shared by all five
# pages so the content area starts at a consistent, compact rhythm below
# the toolbar. Inner card/panel margins intentionally stay 16px.
PAGE_MARGINS = (16, 12, 16, 16)

# Semi-transparent accent/danger/warning fills used in QSS. These work on both
# dark and light backgrounds, so they can be hardcoded (same value for both
# modes) and the QSS never needs to change between themes.
ACCENT_RGBA_FILL = "rgba(99,102,241,0.12)"
ACCENT_RGBA_HOVER = "rgba(99,102,241,0.08)"
ACCENT_RGBA_SELECTED = "rgba(99,102,241,0.20)"
DANGER_RGBA = "rgba(239,68,68,0.12)"
DANGER_RGBA_BORDER = "rgba(239,68,68,0.30)"
DANGER_RGBA_HOVER = "rgba(239,68,68,0.20)"
WARNING_RGBA = "rgba(245,158,11,0.12)"
WARNING_RGBA_BORDER = "rgba(245,158,11,0.30)"

# Chart canvas colours, mirroring chart_display.CHART_THEME[*]["canvas"].
# The ChartCard's surface has to equal the figure's paper/plot background
# so the chart does not sit on a mismatched rectangle. See _cards_qss().
CHART_CANVAS = {
    "dark": "#0f172a",
    "light": "#ffffff",
}

# Theme palettes. Keep the same KEYS in both dicts so a mode switch is a
# pure value swap.
THEMES = {
    "dark": {
        "window_bg": "#0b1120",
        "sidebar_bg": "#0f172a",
        "sidebar_border": "#1e293b",
        "card_bg": "#111c33",
        "input_bg": "#1e2a44",
        "border": "#334155",
        "border_subtle": "#1e293b",
        "text": "#e2e8f0",
        "text_muted": "#94a3b8",
        "text_faint": "#64748b",
        "text_bright": "#a5b4fc",
        "brand": "#94a3b8",
        "header_bg": "#312e81",
        "header_text": "#e0e7ff",
        "accent_fill": "#1e1b4b",
        "accent_hover_fill": "#1a1848",
        "accent_selected_fill": "#2d2a5e",
        "index_bg": "#1e1b4b",
        "index_text": "#a5b4fc",
        "chart_canvas_key": "dark",
        "success": "#10b981",
        "success_hover": "#34d399",
        "danger": "#ef4444",
        "danger_fill": "#3b1a1a",
        "danger_border": "#5b2a2a",
        "danger_hover": "#4a2020",
        "danger_text": "#f87171",
        "btn_bg": "#1e293b",
        "btn_border": "#334155",
        "btn_text": "#cbd5e1",
        "btn_hover": "#334155",
        "btn_disabled_bg": "#1e293b",
        "btn_disabled_text": "#475569",
        "tooltip_bg": "#1e293b",
        "popup_bg": "#1e293b",
        "status_text": "#cbd5e1",
        "scrollbar_bg": "#1e293b",
        "scrollbar_handle": "#334155",
        "warning_text": "#b45309",
        "warning_fill": "#2a1f0a",
        "warning_border": "#5c4214",
    },
    "light": {
        "window_bg": "#f1f5f9",
        "sidebar_bg": "#ffffff",
        "sidebar_border": "#e2e8f0",
        "card_bg": "#ffffff",
        "input_bg": "#ffffff",
        "border": "#cbd5e1",
        "border_subtle": "#e2e8f0",
        "text": "#0f172a",
        "text_muted": "#475569",
        # Light-mode "faint" text (placeholders, disabled states, hint
        # labels) must stay readable on the near-white surfaces: #94a3b8
        # was ~2.3:1 against #ffffff (the "unreadable tiles" report), while
        # slate-500 clears 4.5:1 AA.
        "text_faint": "#64748b",
        "text_bright": "#4f46e5",
        "brand": "#475569",
        "header_bg": "#4f46e5",
        "header_text": "#ffffff",
        "accent_fill": "#e8ebff",
        "accent_hover_fill": "#e0e4fc",
        "accent_selected_fill": "#d6daff",
        "index_bg": "#e8ebff",
        "index_text": "#4f46e5",
        "chart_canvas_key": "light",
        "success": "#059669",
        "success_hover": "#10b981",
        "danger": "#dc2626",
        "danger_fill": "#fce8e8",
        "danger_border": "#f5b5b5",
        "danger_hover": "#f8d0d0",
        "danger_text": "#dc2626",
        "btn_bg": "#f8fafc",
        "btn_border": "#cbd5e1",
        "btn_text": "#334155",
        "btn_hover": "#eef2f7",
        "btn_disabled_bg": "#f1f5f9",
        "btn_disabled_text": "#94a3b8",
        "tooltip_bg": "#ffffff",
        "popup_bg": "#ffffff",
        "status_text": "#334155",
        "scrollbar_bg": "#e2e8f0",
        "scrollbar_handle": "#94a3b8",
        "warning_text": "#b45309",
        "warning_fill": "#fef3c7",
        "warning_border": "#fcd34d",
    },
}

_DEFAULT_MODE = "dark"
_current_mode = _DEFAULT_MODE
_last_applied_font = None
_last_applied_qss_mode = None

# Typography
BASE_FONT_PT = 10
HEADER_FONT_PT = 12
TITLE_FONT_PT = 14


def app_font() -> QFont:
    return QFont("", BASE_FONT_PT)


def current_mode() -> str:
    return _current_mode


def mode_tokens(mode=None):
    mode = mode or _current_mode
    return THEMES.get(mode, THEMES[_DEFAULT_MODE])


def resolve_color(name, mode=None):
    tokens = mode_tokens(mode)
    if name in tokens:
        return tokens[name]
    shared = {"accent": ACCENT, "accent_hover": ACCENT_HOVER, "accent_light": ACCENT_LIGHT}
    return shared.get(name, ACCENT)


# QPalette (covers native widgets not reached by QSS)
def build_palette(mode=None):
    t = mode_tokens(mode)
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(t["window_bg"]))
    pal.setColor(QPalette.Base, c(t["input_bg"]))
    pal.setColor(QPalette.AlternateBase, c(t["card_bg"]))
    pal.setColor(QPalette.WindowText, c(t["text"]))
    pal.setColor(QPalette.Text, c(t["text"]))
    pal.setColor(QPalette.PlaceholderText, c(t["text_faint"]))
    pal.setColor(QPalette.Button, c(t["btn_bg"]))
    pal.setColor(QPalette.ButtonText, c(t["btn_text"]))
    pal.setColor(QPalette.BrightText, c("#ffffff"))
    pal.setColor(QPalette.Highlight, c(ACCENT))
    pal.setColor(QPalette.HighlightedText, c("#ffffff"))
    pal.setColor(QPalette.Link, c(ACCENT_LIGHT))
    pal.setColor(QPalette.LinkVisited, c(ACCENT_LIGHT))
    pal.setColor(QPalette.Disabled, QPalette.Text, c(t["text_faint"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, c(t["btn_disabled_text"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, c(t["text_faint"]))
    pal.setColor(QPalette.Disabled, QPalette.Base, c(t["btn_disabled_bg"]))
    pal.setColor(QPalette.ToolTipBase, c(t["tooltip_bg"]))
    pal.setColor(QPalette.ToolTipText, c(t["text"]))
    pal.setColor(QPalette.Inactive, QPalette.Highlight, c(ACCENT))
    pal.setColor(QPalette.Inactive, QPalette.Text, c(t["text"]))
    # Additional roles referenced by QSS palette() references
    pal.setColor(QPalette.Light, c(t["btn_hover"]))
    pal.setColor(QPalette.Midlight, c(t["border_subtle"]))
    pal.setColor(QPalette.Mid, c(t["border"]))
    # Dark is a TEXT role here, not a 3D-border color: the global QSS maps
    # the "faint" label class (subtitles, db-path, sidebar version) to
    # palette(dark). text_muted keeps ~8:1 contrast on card backgrounds in
    # BOTH modes, whereas the previous text_faint mapping was unreadable.
    pal.setColor(QPalette.Dark, c(t["text_muted"]))
    pal.setColor(QPalette.Shadow, c(t["text_faint"]))
    return pal


# Global QSS construction
def build_qss(mode=None):
    # The main stylesheet uses palette() role references so that changing
    # QPalette (cheap) updates all colors without re-parsing the QSS (expensive).
    # Accent/danger/warning fills use hardcoded rgba that work on both modes.
    qss = f"""
/* ---- Window shell ---- */
QMainWindow, QStackedWidget#stack {{ background: palette(window); }}
QWidget {{ color: palette(window-text); font-weight: 600; }}
/* ---- Sidebar ---- */
QFrame#sidebar {{ background: palette(window); border-right: 1px solid palette(midlight); }}
QLabel#brand {{ font-size: 20px; font-weight: 700; color: palette(window-text); }}
QLabel#brand_sub {{ font-size: 12px; font-weight: 600; color: palette(window-text); padding: 0 0 12px; }}
QLabel#version {{ font-size: 10px; color: palette(dark); padding: 8px 12px; }}
QListWidget#nav_list {{ background: transparent; border: none; font-size: 14px; font-weight: 600; color: palette(window-text); outline: none; }}
QListWidget#nav_list::item {{ background: transparent; padding: 10px 12px; border-radius: 8px; margin: 2px 0; }}
QListWidget#nav_list::item:hover {{ background: {ACCENT_RGBA_HOVER}; }}
QListWidget#nav_list::item:selected {{ background: {ACCENT_RGBA_SELECTED}; color: palette(text); }}
/* ---- Top rail (hamburger + theme toggle) ---- */
#top_rail {{ background: palette(window); border-bottom: 1px solid palette(mid); }}
QToolButton#topbar_btn {{ background: transparent; color: palette(window-text); border: none; border-radius: 6px; padding: 4px; }}
QToolButton#topbar_btn:hover {{ background: {ACCENT_RGBA_HOVER}; }}
/* ---- Generic labels ---- */
QLabel[class="pageTitle"] {{ font-size: 20px; font-weight: 700; color: palette(window-text); }}
QLabel[class="cardTitle"] {{ font-size: 14px; font-weight: 600; color: palette(window-text); }}
QLabel[class="cardSub"] {{ font-size: 12px; font-weight: 600; color: palette(window-text); }}
QLabel[class="sectionTitle"] {{ font-size: 15px; font-weight: 700; color: palette(window-text); margin-top: 8px; }}
QLabel[class="muted"] {{ color: palette(window-text); }}
QLabel[class="faint"] {{ color: palette(dark); }}
QLabel[class="kpiLabel"] {{ font-size: 11px; font-weight: 600; color: palette(window-text); }}
QLabel[class="kpiValue"] {{ font-size: 14px; font-weight: 700; color: palette(window-text); }}
QLabel[class="kpiDelta"] {{ font-size: 11px; color: #34d399; }}
QLabel[class="statValue"] {{ font-size: 24px; font-weight: 700; color: palette(window-text); }}
QLabel[class="statLabel"] {{ font-size: 12px; font-weight: 600; color: palette(window-text); }}
QLabel[class="warning"] {{ font-size: 12px; font-weight: 600; color: #b45309; background: {WARNING_RGBA}; border: 1px solid {WARNING_RGBA_BORDER}; border-radius: 6px; padding: 8px; }}
QLabel[class="iconEmoji"] {{ font-size: 18px; }}
"""
    qss += _statusbar_qss()
    qss += _buttons_qss()
    qss += _inputs_qss()
    qss += _tables_qss()
    qss += _lists_qss()
    qss += _tabs_qss()
    qss += _groupbox_qss()
    # mode is threaded explicitly: the chart-card rule below resolves a
    # per-mode colour, and reading the module-global _current_mode instead
    # would make build_qss("light") emit the DARK canvas whenever it is
    # called for a mode other than the currently-applied one (which is
    # exactly what a theme toggle preview / test does).
    qss += _cards_qss(mode)
    qss += _scroll_split_qss()
    qss += _chrome_qss(mode)
    qss += _misc_qss()
    return qss


def _statusbar_qss():
    return f"""QStatusBar {{ background: palette(window); color: palette(window-text); border-top: 1px solid palette(midlight); }}"""


def _buttons_qss():
    return f"""
QPushButton {{ background: palette(button); color: palette(button-text); border: 1px solid palette(mid); border-radius: 8px; padding: 8px 16px; font-weight: 600; font-size: {BASE_FONT_PT}dpt; }}
QPushButton:hover {{ background: palette(light); color: palette(window-text); }}
QPushButton:pressed {{ background: {ACCENT_RGBA_HOVER}; }}
QPushButton:disabled {{ background: palette(button); color: palette(mid); border-color: palette(midlight); }}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: #ffffff; border: none; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[kind="primary"]:pressed {{ background: #4338ca; }}
QPushButton[kind="success"] {{ background: #10b981; color: #ffffff; border: none; }}
QPushButton[kind="success"]:hover {{ background: #34d399; }}
QPushButton[kind="danger"] {{ background: {DANGER_RGBA}; color: #f87171; border: 1px solid {DANGER_RGBA_BORDER}; }}
QPushButton[kind="danger"]:hover {{ background: {DANGER_RGBA_HOVER}; }}
QPushButton[kind="ghost"] {{ background: transparent; color: palette(window-text); border: none; padding: 6px; border-radius: 6px; }}
QPushButton[kind="ghost"]:hover {{ background: {ACCENT_RGBA_HOVER}; color: palette(window-text); }}
QPushButton[kind="icon"] {{ background: transparent; color: palette(window-text); border: none; border-radius: 4px; font-size: 13px; padding: 2px; }}
QPushButton[kind="icon"]:hover {{ background: {ACCENT_RGBA_HOVER}; color: palette(window-text); }}
QPushButton[kind="icon"]:checked {{ background: {ACCENT_RGBA_SELECTED}; color: palette(text); }}
QPushButton[kind="icon"]:disabled {{ color: palette(mid); background: transparent; }}
"""


def _inputs_qss():
    return f"""
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{ background: palette(base); color: palette(window-text); border: 1px solid palette(mid); border-radius: 6px; padding: 6px 10px; font-size: {BASE_FONT_PT}dpt; selection-background-color: {ACCENT}; selection-color: #ffffff; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {ACCENT_LIGHT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT_LIGHT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid palette(window-text); }}
QComboBox QAbstractItemView {{ background: palette(base); color: palette(window-text); border: 1px solid palette(mid); border-radius: 4px; selection-background-color: {ACCENT}; selection-color: #ffffff; outline: none; }}
"""


def _tables_qss():
    return f"""
QTableView, QTableWidget {{ background: palette(window); color: palette(window-text); border: 1px solid palette(mid); border-radius: 8px; gridline-color: palette(mid); font-size: {BASE_FONT_PT}dpt; alternate-background-color: palette(alternate-base); }}
QTableView::item, QTableWidget::item {{ padding: 4px 6px; }}
QTableView::item:selected, QTableWidget::item:selected {{ background: {ACCENT_RGBA_SELECTED}; color: palette(window-text); }}
QTableView::item:hover, QTableWidget::item:hover {{ background: {ACCENT_RGBA_HOVER}; }}
QHeaderView::section {{ background: palette(highlight); color: palette(highlighted-text); border: none; padding: 7px 8px; font-weight: 700; font-size: {HEADER_FONT_PT}dpt; }}
QTableCornerButton::section {{ background: palette(highlight); border: none; }}
"""


def _lists_qss():
    return f"""
QListWidget, QListView {{ background: palette(window); color: palette(window-text); border: 1px solid palette(midlight); border-radius: 8px; font-size: {BASE_FONT_PT}dpt; outline: none; }}
QListWidget::item, QListView::item {{ padding: 6px 10px; border-radius: 4px; }}
QListWidget::item:hover, QListView::item:hover {{ background: {ACCENT_RGBA_HOVER}; }}
QListWidget::item:selected, QListView::item:selected {{ background: {ACCENT_RGBA_SELECTED}; color: palette(text); }}
"""


def _tabs_qss():
    return f"""
QTabWidget::pane {{ border: 1px solid palette(midlight); border-radius: 6px; background: palette(window); top: -1px; }}
QTabBar::tab {{ background: transparent; color: palette(window-text); padding: 8px 14px; border: none; border-bottom: 2px solid transparent; font-weight: 600; font-size: {BASE_FONT_PT}dpt; }}
QTabBar::tab:hover {{ color: palette(window-text); }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
"""


def _groupbox_qss():
    return """
QGroupBox { background: palette(alternate-base); border: 1px solid palette(mid); border-radius: 8px; margin-top: 10px; padding-top: 8px; font-weight: 600; color: palette(window-text); }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: palette(window-text); }
"""


def _cards_qss(mode=None):
    t = mode_tokens(mode)
    # The chart card is the surface the Plotly canvas is painted onto, so
    # it has to use the SAME colour the figure's paper/plot background
    # resolves to (chart_display.CHART_THEME[*]["canvas"]). It used to use
    # palette(alternate-base) = card_bg, which in dark mode is #111c33
    # against a #0f172a canvas -- a visible lighter rectangle framing the
    # chart. Matching the canvas makes the card read as one seamless
    # surface (the "BG should be transparent" report). The values are
    # duplicated from chart_display on purpose: the QSS is a static string
    # built per mode, and importing the backend module here would create a
    # desktop -> backend dependency for two hex values. Keep them in sync.
    chart_canvas = CHART_CANVAS[t["chart_canvas_key"]]
    return f"""
QFrame#kpi_card, QFrame#stat_card {{ background: palette(alternate-base); border: 1px solid palette(midlight); border-radius: 10px; }}
QFrame#chart_card {{ background: {chart_canvas}; border: 1px solid palette(midlight); border-radius: 10px; }}
QLabel#welcome_banner {{ background: palette(alternate-base); border: 1px solid palette(midlight); border-radius: 8px; padding: 16px; color: palette(window-text); }}
QFrame#welcome_banner {{ background: palette(alternate-base); border: 1px solid palette(midlight); border-radius: 8px; }}
QFrame#config_panel {{ background: palette(alternate-base); border: 1px solid palette(midlight); border-radius: 8px; }}
/* Analysis-page chart-settings tile: it previously had NO background rule
   at all, so it painted fully transparent (the window colour bled through
   and the settings form read as "see-through" -- worst in light mode).
   Give it the same opaque card surface as the other panels. */
QFrame#analysis_config_panel {{ background: palette(alternate-base); border: 1px solid palette(midlight); border-radius: 8px; }}
QFrame#user_card {{ background: palette(alternate-base); border: 1px solid palette(midlight); border-radius: 10px; }}
"""


def _scroll_split_qss():
    return """
QScrollArea { background: transparent; border: none; }
QSplitter::handle { background: palette(midlight); }
QSplitter::handle:hover { background: palette(mid); }
QSplitter[style="analysis_v"]::handle { background: #1e293b; }
QSplitter[style="analysis_v"]::handle:hover { background: #334155; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }

/* Tab buttons (+/x) restyle */
QTabBar::tab { padding: 6px 10px; }
"""


def _chrome_qss(mode=None):
    # QToolTip is styled with LITERAL colours, not palette() role references.
    # The tooltip is a short-lived top-level window created by QToolTip on
    # hover: it is NOT a child of any widget, so the app-wide
    # setStyleSheet/setPalette repolish on a mode toggle never reaches it and
    # palette() keeps resolving against the palette captured when the tooltip
    # was first polished. The visible result was a DARK plate with LIGHT text
    # in light mode (and vice versa). A literal is baked into the new QSS, so
    # the next tooltip built after a toggle is correct, and an already-open
    # one is repainted by the re-polish that the stylesheet swap does trigger.
    t = mode_tokens(mode)
    return f"""
QScrollBar:vertical {{ background: palette(window); width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: palette(mid); border-radius: 6px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_LIGHT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: palette(window); height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: palette(mid); border-radius: 6px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QToolTip {{ background: {t["tooltip_bg"]}; color: {t["text"]}; border: 1px solid {t["border"]}; padding: 4px 8px; border-radius: 4px; }}
QMenu {{ background: palette(base); color: palette(window-text); border: 1px solid palette(mid); padding: 4px; border-radius: 6px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_RGBA_SELECTED}; }}
QMenu::separator {{ height: 1px; background: palette(midlight); margin: 4px 8px; }}
QMessageBox, QDialog {{ background: palette(window); color: palette(window-text); }}
QTextEdit, QPlainTextEdit {{ background: palette(base); color: palette(window-text); border: 1px solid palette(mid); border-radius: 6px; selection-background-color: {ACCENT}; }}
/* Analysis-note tile inside each chart card: explicit opaque surface +
   12px body so the notes area never inherits a transparent/oversized
   look from the card (light mode used to wash it out). */
QTextEdit#chart_notes {{ background: palette(base); color: palette(window-text); border: 1px solid palette(mid); border-radius: 6px; padding: 6px; font-size: 12px; selection-background-color: {ACCENT}; }}
"""


def _misc_qss():
    return f"""
QCheckBox, QRadioButton {{ color: palette(window-text); font-size: {BASE_FONT_PT}dpt; spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid palette(mid); border-radius: 4px; background: palette(base); }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton::indicator {{ border: 1px solid palette(mid); border-radius: 8px; background: palette(base); }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QProgressBar {{ background: palette(midlight); border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
"""


FULL_QSS = build_qss(_DEFAULT_MODE)


def apply_theme(app, mode=None):
    global _current_mode, _last_applied_font, _last_applied_qss_mode
    mode = mode if mode in THEMES else _DEFAULT_MODE
    _current_mode = mode
    first = _last_applied_qss_mode is None
    if first:
        app.setStyle("Fusion")
        # setFont forces a relayout of every widget -- apply only once.
        new_font = app_font()
        app.setFont(new_font)
        _last_applied_font = new_font
    # Palette + stylesheet are applied on EVERY toggle: Qt resolves QSS
    # palette() references at polish time and CACHES them, so a palette-only
    # change leaves colors stale (verified: banner/input backgrounds frozen
    # across modes).  Re-applying the stylesheet forces a full re-polish so
    # every palette() reference re-resolves.  The reparse is ~800ms on a
    # loaded workspace -- acceptable for an explicit user toggle, and the
    # toolbar icon already swapped within one frame (see toggle_theme()).
    app.setPalette(build_palette(mode))
    app.setStyleSheet(build_qss(mode))
    _last_applied_qss_mode = mode
    # A tooltip that is ON SCREEN at the moment of the toggle is a top-level
    # window that the stylesheet swap cannot re-polish, so it keeps painting
    # the previous mode's plate until it closes. Dismissing it means the user
    # never sees a dark box over a light UI (the tooltip simply reappears, in
    # the new colours, if the cursor is still hovering).
    try:
        from PySide6.QtWidgets import QToolTip
        QToolTip.hideText()
    except Exception:
        pass




# ---- Reusable per-widget style builders (theme-aware, runtime-live) ----
def input_style(mode=None):
    t = mode_tokens(mode)
    return (
        "QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {"
        f"  background: {t['input_bg']}; color: {t['text']};"
        f"  border: 1px solid {t['border']}; border-radius: 6px;"
        "  padding: 5px 10px; font-size: 13px;"
        "}"
        "QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: " + ACCENT_LIGHT + " }"
        "QComboBox QAbstractItemView {"
        f"  background: {t['popup_bg']}; color: {t['text']};"
        f"  border: 1px solid {t['border']};"
        f"  selection-background-color: {ACCENT}; selection-color: #fff;"
        "}"
    )


def label_style(mode=None):
    t = mode_tokens(mode)
    # 13px/600 body: UI review (Image 3) flagged every Generate/Layout/
    # Typography caption as "extreme small, unreadable" -- 12px/500 sits
    # below the app's own 13px body baseline.
    return f"font-size: 13px; font-weight: 600; color: {t['text']};"


def group_label_style(mode=None):
    t = mode_tokens(mode)
    # 13px section headers to match (same Image 3 review -- the old 11px
    # accent caps were the worst offenders). Colour follows the mode:
    # ACCENT_LIGHT (#818cf8) is a dark-mode colour -- on light-mode white
    # surfaces it drops to ~2.5:1 contrast and the chart-settings section
    # headers read as washed out / transparent. text_bright is #a5b4fc in
    # dark (same look as before) and #4f46e5 in light (4.5:1+).
    accent = ACCENT_LIGHT if (mode or _current_mode) == "dark" else t["text_bright"]
    return f"font-size: 13px; font-weight: 700; color: {accent}; letter-spacing: 0.6px;"


def hr_style(mode=None):
    t = mode_tokens(mode)
    return f"color: {t['border_subtle']};"

# Persistence via QSettings (per-machine, not per-session)
def load_saved_mode():
    from PySide6.QtCore import QSettings
    try:
        s = QSettings()
        val = s.value("theme_mode", _DEFAULT_MODE)
        return val if val in THEMES else _DEFAULT_MODE
    except Exception:
        return _DEFAULT_MODE


def save_mode(mode):
    from PySide6.QtCore import QSettings
    try:
        s = QSettings()
        s.setValue("theme_mode", mode)
        # Do NOT call s.sync() here -- it blocks the GUI thread on disk
        # I/O. QSettings auto-flushes on event loop idle / app exit, which
        # is sufficient for a per-machine preference like theme mode.
    except Exception:
        pass
