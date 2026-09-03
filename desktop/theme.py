"""desktop/theme.py -- Lytrize design language + global theming.

Single source of truth for the app's colour tokens, fonts and the reusable
Qt stylesheet fragments that give tables (header row, row-index gutter),
inputs and cards their consistent, high-contrast, Lytrize-brand look.

"Lytrize theme color" = the indigo accent (#4f46e5 / #818cf8) that already
runs through the app's buttons; we extend it to preview-table headers, the
row-index gutter and the settings cards so users -- especially on HQ / high-DPI
monitors -- can read them without straining.
"""

from __future__ import annotations

from PySide6.QtGui import QFont

# ── Brand tokens ──────────────────────────────────────────────────────────
ACCENT = "#4f46e5"
ACCENT_HOVER = "#6366f1"
ACCENT_LIGHT = "#818cf8"
ACCENT_HEADER_BG = "#312e81"      # indigo-900: header row, high contrast
ACCENT_HEADER_TEXT = "#e0e7ff"     # indigo-100 text on the header
ACCENT_INDEX_BG = "rgba(79,70,229,0.18)"  # row-index gutter tint
ACCENT_INDEX_TEXT = "#a5b4fc"      # indigo-300 bold index numerals
ACCENT_BG = "rgba(99,102,241,0.15)"       # soft hover / selected fill
ACCENT_BORDER = "#818cf8"

# Neutral surfaces (slightly lighter than the page bg for card "pop").
SURFACE_CARD = "#111c33"
SURFACE_INPUT = "#1e2a44"
BORDER = "#334155"
TEXT = "#e2e8f0"
TEXT_MUTED = "#94a3b8"
TEXT_FAINT = "#64748b"

# ── Typography ────────────────────────────────────────────────────────────
# Base point size applied app-wide. Using pt (not px) lets Qt scale the text
# with the system / display DPI instead of staying pixel-locked and tiny on
# HQ monitors. Bump this single constant to make the whole app larger.
BASE_FONT_PT = 10
HEADER_FONT_PT = 12
TITLE_FONT_PT = 14


def app_font() -> QFont:
    """The application-wide base font (scales with high-DPI displays)."""
    return QFont("", BASE_FONT_PT)


# ── Reusable stylesheet fragments ─────────────────────────────────────────

# QTableView / QTableWidget header + row-index gutter, with the accent tint.
TABLE_HEADER_QSS = """
QHeaderView::section {
    background: %(header_bg)s;
    color: %(header_text)s;
    border: none;
    padding: 7px 8px;
    font-weight: 700;
    font-size: %(header_pt)dpt;
}
""" % {
    "header_bg": ACCENT_HEADER_BG,
    "header_text": ACCENT_HEADER_TEXT,
    "header_pt": HEADER_FONT_PT,
}

# A full table style: dark body, accent header, tinted index gutter, clear
# gridlines, brand highlight on hover/selection and a focus ring.
TABLE_QSS = (
    "QTableView, QTableWidget {"
    "  background: #0f172a; color: %(text)s;"
    "  border: 1px solid %(border)s; border-radius: 8px;"
    "  gridline-color: %(border)s;"
    "  font-size: %(base_pt)dpt;"
    "}"
    "QTableView::item, QTableWidget::item { padding: 4px 6px; }"
    "QTableView::item:selected, QTableWidget::item:selected {"
    "  background: %(accent_bg)s; color: %(text)s;"
    "}"
    "QTableView::item:hover, QTableWidget::item:hover {"
    "  background: %(accent_bg)s;"
    "}"
    # Row-number gutter: tinted + bold indigo so rows are quick to scan.
    "QHeaderView::section {"
    "  background: %(header_bg)s; color: %(header_text)s;"
    "  border: none; padding: 7px 8px; font-weight: 700;"
    "  font-size: %(base_pt)dpt;"
    "}"
    "QTableCornerButton::section { background: %(header_bg)s; border: none; }"
) % {
    "text": TEXT,
    "border": BORDER,
    "base_pt": HEADER_FONT_PT,
    "accent_bg": ACCENT_BG,
    "header_bg": ACCENT_HEADER_BG,
    "header_text": ACCENT_HEADER_TEXT,
}

# Inputs: combos, edits, spins -- light surface + accent focus ring.
INPUT_QSS = (
    "QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {"
    "  background: %(input)s; color: %(text)s;"
    "  border: 1px solid %(border)s; border-radius: 6px;"
    "  padding: 6px 10px; font-size: %(base_pt)dpt;"
    "}"
    "QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {"
    "  border-color: %(accent_light)s;"
    "}"
    "QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {"
    "  border-color: %(accent_light)s;"
    "}"
    "QComboBox::drop-down { border: none; width: 24px; }"
    "QComboBox QAbstractItemView {"
    "  background: %(input)s; color: %(text)s;"
    "  border: 1px solid %(border)s;"
    "  selection-background-color: %(accent)s; selection-color: #ffffff;"
    "}"
    "QLineEdit, QSpinBox, QDoubleSpinBox { selection-background-color: %(accent)s; }"
) % {
    "input": SURFACE_INPUT,
    "text": TEXT,
    "border": BORDER,
    "base_pt": BASE_FONT_PT,
    "accent_light": ACCENT_BORDER,
    "accent": ACCENT,
}

# The settings "card": lighter surface, rounded, emphasised border so it
# reads as a distinct panel next to the preview.
CARD_QSS = (
    "QFrame { background: %(card)s; border: 1px solid %(border)s;"
    "  border-radius: 8px; }"
) % {
    "card": SURFACE_CARD,
    "border": BORDER,
}

# Buttons: primary (accent), success (green), danger (red) + ghost.
BTN_PRIMARY_QSS = (
    "QPushButton { background: %(accent)s; color: #ffffff; border: none;"
    "  border-radius: 8px; padding: 8px 18px; font-weight: 700;"
    "  font-size: %(base_pt)dpt; }"
    "QPushButton:hover { background: %(hover)s; }"
    "QPushButton:pressed { background: #4338ca; }"
    "QPushButton:disabled { background: #334155; color: #64748b; }"
) % {
    "accent": ACCENT,
    "hover": ACCENT_HOVER,
    "base_pt": BASE_FONT_PT,
}

FULL_QSS = TABLE_QSS + INPUT_QSS + CARD_QSS + BTN_PRIMARY_QSS


def apply_app_theme(app) -> None:
    """Set the app-wide base font and the global stylesheet.

    Call once in ``app.main()`` before creating windows. Panels may still
    override specific widgets, but the defaults here give every table its
    accent header/index and every input its dark, focused style.
    """
    app.setFont(app_font())
    app.setStyleSheet(FULL_QSS)