"""desktop/pages/dashboard_page.py -- Dashboard page (Qt).

Replaces the Streamlit ``page_dashboard()`` with a native Qt page that:
  - Shows charts in a scrollable grid
  - Supports KPI cards
  - Provides HTML export via the headless export service
  - Handles saved-session viewing
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QLineEdit, QComboBox, QFileDialog,
    QMessageBox, QFrame, QSplitter, QTextEdit, QCheckBox, QSpinBox,
    QColorDialog, QFontComboBox, QInputDialog,
    QDialog, QDialogButtonBox,
)

from desktop import theme as _theme_mod
from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from desktop.widgets.kpi_dialog import KpiDialog
from desktop.widgets.formatting_pane import (
    CollapsibleSection, FieldRow, PresetGallery,
)
from desktop.widgets.report_canvas import ReportCanvas
from desktop.widgets import tile_geometry
from modules.canvas_geometry import TITLE_UID as _TITLE_UID
# Key stored alongside the tile geometry to record that the board was saved on
# the FREE canvas, so the grid migration never re-reads a free arrangement as
# legacy cells. Its value is a bare string; every geometry consumer filters by
# isinstance(dict), so it is ignored everywhere it is not explicitly read here.
_MODE_KEY = "__ly_mode__"
from desktop.workers import ExportWorker, ReportPreviewWorker
from services.export_service import (
    build_export_report, build_export_charts, apply_preset, EXPORT_PRESETS,
    EXPORT_DEFAULTS,
)
from services.chart_service import safe_filename
from services.database_service import (
    save_session_db, update_session_db, get_session_charts,
    get_session_meta, clear_draft, invalidate_sessions_cache,
)

log = logging.getLogger(__name__)

# The page widths the exporter understands, read from the service so the panel
# cannot offer a width the report writer does not handle. The panel used to
# hard-code ["Auto", "Fixed"], which made Narrow and Wide unreachable.
_EXPORT_WIDTHS = ("Auto", "Narrow", "Wide", "Full")

# Live-preview regeneration debounce, in ms. A combo box or a colour swatch
# changes once, so it gets the short delay; a spin box or a dragged slider
# streams values and must not rebuild the report per intermediate value.
_REGEN_FAST_MS = 220
_REGEN_SLOW_MS = 450

# Grid geometry lives in grid units, never pixels, so a window resize or a
# column-count change preserves the arrangement the user built.
MIN_TILE_SPAN = 1


# Default values for every export-style key. Imported from the export service
# rather than restated here: the panel, the live-apply path, the preview and the
# exported payload must all agree on the same defaults, and two hand-maintained
# copies of one 23-key dict is how they silently drifted apart before.
_STYLE_DEFAULTS = dict(EXPORT_DEFAULTS)

# The colour keys the dashboard's own picker and the preset gallery write.
#
# These are the EXPORT palette, and that is now the ONLY palette the dashboard
# shows. The page used to keep two: the canvas resolved most colours from the
# live app theme while the file was written from the stored values, which is why
# light mode looked broken (a near-black #1b2245 card with #f5f7ff text on a
# #f1f5f9 window) and why a preset could be selected in the gallery yet not
# appear on the board.
#
# With the canvas rendering the real report, that split has nowhere to live: the
# board and the file are the same document, so it is the export palette by
# construction. A report keeps its own colours whichever app theme is active --
# the same reason a Tableau dashboard stays white inside a dark IDE -- so
# switching light/dark deliberately does NOT repaint the board, and a chosen
# preset stays exactly as chosen.
_OVERRIDABLE_COLORS = (
    "ex_bg", "ex_card", "ex_kpi", "ex_border", "ex_text", "ex_accent",
    "ex_not_bg", "ex_not_bd", "ex_title_color", "ex_notes_color",
    "ex_kpi_text_color", "ex_kpi_val_color",
    "ex_slicer_bg", "ex_slicer_bd", "ex_slicer_text", "ex_slicer_accent",
)

# Style keys that a quick theme must NOT touch, because they are not colours:
# a theme is a palette, and silently re-sizing every KPI label or resetting the
# background image would undo work the user did after picking the theme.
_NON_COLOUR_STYLE_KEYS = (
    "ex_density", "ex_radius", "ex_chart_h", "ex_width", "ex_meta",
    "ex_kpi_val_size", "ex_kpi_label_size", "ex_kpi_label_upper",
    "ex_title_font", "ex_title_style", "ex_title_size",
    "ex_notes_font", "ex_notes_style", "ex_notes_size",
    "ex_bg_image", "ex_bg_image_opacity", "ex_bg_blend",
    "ex_card_image", "ex_card_image_opacity", "ex_card_blend",
    "ex_card_image_groups",
)


def _load_saved_tiles(sm: dict) -> dict:
    """Per-card tiling geometry saved with a session, or {} when absent.

    Stored inside the existing ``grid_fullwidth_json`` blob rather than a new DB
    column: it is the same concept (per-card grid placement) and the session
    schema already carries it, so this needs no migration. The legacy
    full-width map is honoured too, so a session saved by the pre-tiling build
        still opens with its wide charts spanning the full row instead of
        silently losing that choice.
    """
    try:
        raw = json.loads(sm.get("grid_fullwidth_json", "{}"))
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    cols = int(sm.get("grid_cols_n", 2) or 2)
    cols = max(1, cols)
    tiles: dict = {}
    for uid, val in raw.items():
        # The free-canvas mode marker is a bare string, not a tile. Passing it
        # through would otherwise fall into the legacy-boolean branch below and
        # become a bogus full-width cell, so it is skipped here.
        if uid == _MODE_KEY:
            continue
        # The title band is stored in free-form units like everything else, and
        # it is always full width -- so int()-coercing it here would produce
        # exactly the grid-shaped numbers this loader then mistakes for a
        # legacy cell arrangement and scales the WHOLE board through from_grid.
        # It is passed through untouched.
        if uid == _TITLE_UID and isinstance(val, dict) and "x" in val:
            tiles[uid] = {k: float(val.get(k, d))
                          for k, d in (("x", 0.0), ("y", 0.0),
                                       ("w", 1.0), ("h", 120.0))}
        elif isinstance(val, dict) and "x" in val:
            tiles[uid] = {"x": int(val.get("x", 0)), "y": int(val.get("y", 0)),
                          "w": int(val.get("w", 1)), "h": int(val.get("h", 1))}
        elif val:
            # Legacy boolean: "this chart spans the whole row".
            tiles[uid] = {"x": 0, "y": -1, "w": cols, "h": 1}
    return _densify_legacy_tiles(tiles, cols)


def _densify_legacy_tiles(tiles: dict, cols: int) -> dict:
    """Give legacy full-width entries a real row index.

    They were stored with y=-1 because the old grid worked out rows itself.
    Placing them in list order from the top preserves the arrangement those
    sessions were saved with.
    """
    out: dict = {}
    y = 0
    for uid, g in tiles.items():
        if g["y"] < 0:
            out[uid] = {"x": 0, "y": y, "w": cols, "h": 1}
            y += 1
        else:
            out[uid] = g
    return out


def _kpi_key(uid: str) -> str | None:
    """The persisted KPI key for a canvas uid, or None if it is not a KPI.

    Two spellings reach this module and they have to be reconciled here.

    The canvas overlay tags a KPI card with ``data-kpi="0"`` and reports it as
    ``kpi:0`` -- the prefix keeps a KPI distinguishable from a chart uid
    without the JS having to know how charts are keyed. The persisted blob and
    the report writer both key KPIs by the bare index, so ``kpi:0`` and ``"0"``
    have to be recognised as the same tile.

    Getting this wrong is quiet rather than loud: an unrecognised ``kpi:0`` is
    filed as a chart uid, fails the "is this chart still here" check a line
    later, and is dropped -- so a KPI drag simply does nothing, every time.
    """
    if not isinstance(uid, str) or not uid:
        return None
    if uid.isdigit():
        return uid
    if uid.startswith("kpi:") and uid[4:].isdigit():
        return uid[4:]
    return None


def _is_kpi_key(key: str) -> bool:
    """True for a KPI tile key, in either the persisted or the canvas spelling."""
    return _kpi_key(key) is not None


def _slicer_key(uid: str) -> str | None:
    """The persisted slicer key for a canvas uid, or None if it is not one.

    The exact analogue of :func:`_kpi_key`, and for the same reason. The
    overlay tags a slicer card with ``data-slicer="0"`` and reports
    ``slicer:0``; the persisted blob keys it by the bare index.

    This function is what stops a slicer from being silently lost. Without it
    ``slicer:0`` is not a KPI key, so it falls through to the CHART partition,
    fails the "is this chart still here" filter, and is dropped -- so a slicer
    drag would do nothing, every single time, with no error anywhere.
    """
    if not isinstance(uid, str) or not uid:
        return None
    if uid.isdigit():
        return None                 # a bare digit is a KPI, never a slicer
    if uid.startswith("slicer:") and uid[7:].isdigit():
        return uid[7:]
    return None


def _is_slicer_key(key: str) -> bool:
    """True for a slicer tile key, in either the persisted or canvas spelling."""
    return _slicer_key(key) is not None


def _is_user_override(bridge, key: str, stored) -> bool:
    """True when *stored* differs from the built-in default.

    The picker is the only writer of these keys, so "differs from default" is
    exactly "the user set it". Used to decide whether Reset should clear a key.
    """
    return stored != _STYLE_DEFAULTS.get(key)


class DashboardPage(QWidget):
    """Dashboard page: chart grid, KPIs, and export."""

    navigate_requested = Signal(str)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._color_swatches: dict[str, QPushButton] = {}
        self._viewing_saved = False
        # Encoded background images, keyed by (path, mtime, size). The report is
        # rebuilt on every settings tweak, and base64-encoding a photo each time
        # would put a visible stall on dragging the opacity slider.
        self._bg_image_cache: dict[tuple, str] = {}
        # Set while the panel widgets are being written FROM the bridge, so the
        # change signals that fires are not mistaken for user edits.
        self._syncing_widgets = False

        self._build_ui()
        self._connect_signals()
        self._refresh_charts()
        self._refresh_kpis()
        # After _build_ui, because it is what creates self.slicer_count.
        self._refresh_slicer_count()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("📈 Dashboard")
        title.setProperty("class", "pageTitle")
        header.addWidget(title)
        header.addStretch()

        self.btn_back = QPushButton("← Back")
        self.btn_back.setProperty("kind", "ghost")
        header.addWidget(self.btn_back)

        self.btn_tidy = QPushButton("\U0001F9F9 Tidy Layout")
        self.btn_tidy.setProperty("kind", "ghost")
        self.btn_tidy.setToolTip(
            "Re-flow every card into a clean board, keeping the order you\n"
            "can see and the size you gave each card.\n\n"
            "Cards are placed by dragging them, resized from the right,\n"
            "bottom or corner handle, and nudged with the arrow keys\n"
            "(hold Shift for a bigger step). Drag a card's grip, or\n"
            "anywhere on its background, to move it.")
        header.addWidget(self.btn_tidy)

        # Edit / Preview. The whole switch between editing and seeing the
        # finished report, and deliberately NOT a second rendering: it toggles
        # one CSS class on the report document, so the two modes are the same
        # DOM and cannot drift. See ReportCanvas.
        self.btn_mode = QPushButton("\U0001F441\ufe0f Preview")
        self.btn_mode.setCheckable(True)
        self.btn_mode.setProperty("kind", "ghost")
        self.btn_mode.setToolTip(
            "Hide the editing handles to see the report exactly as it will "
            "be exported.\nNothing is re-rendered -- this is the same page.")
        self.btn_mode.toggled.connect(self._on_mode_toggled)
        header.addWidget(self.btn_mode)

        self.btn_export = QPushButton("⬇️ Export HTML")
        self.btn_export.setProperty("kind", "primary")
        header.addWidget(self.btn_export)

        self.btn_export_png = QPushButton("📷 Export PNG")
        self.btn_export_png.setProperty("kind", "ghost")
        header.addWidget(self.btn_export_png)

        self.btn_save = QPushButton("💾 Save Session")
        self.btn_save.setProperty("kind", "success")
        header.addWidget(self.btn_save)
        root.addLayout(header)

        # -- Split: left = controls (session/title/layout/KPIs/style),
        #    right = the live dashboard itself (charts + KPI row). The
        #    right panel IS the preview -- every left-panel control applies
        #    to it immediately via _apply_style_to_cards(), there is no
        #    separate "preview vs real" rendering path to keep in sync.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)

        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        # -- The live canvas -------------------------------------------------
        # The right panel IS the exported report, generated on every change and
        # loaded into a QWebEngineView. It used to be a native Qt approximation
        # (KpiCard + ChartCard tiles on a TilingCanvas), which meant the board on
        # screen and the file on disk were two different renderers -- so density,
        # corner radius, chart height, page width and both typography groups were
        # live no-ops, and the two could visibly disagree.
        #
        # Now there is exactly one document, so those settings need no wiring at
        # all: they already reach the report, and the report is what is shown.
        self.report_canvas = ReportCanvas()
        right_layout.addWidget(self.report_canvas, stretch=1)

        self.report_canvas.geometry_changed.connect(self._on_canvas_geometry)
        self.report_canvas.notes_changed.connect(self._on_canvas_notes)
        self.report_canvas.action_requested.connect(self._on_canvas_action)
        # Without this a slicer click crosses the bridge (Python receives it)
        # and then dies: _on_canvas_slicer is never called and nothing filters.
        self.report_canvas.slicer_changed.connect(self._on_canvas_slicer)

        # Coalesced regeneration. Sliders and spin boxes stream values while the
        # user drags them, and a report rebuild is not something to do per
        # intermediate value; the two-tier delay follows the Analysis page --
        # short for discrete controls (a combo changed once), long for the
        # continuous ones.
        self._regen_fast = QTimer(self)
        self._regen_fast.setSingleShot(True)
        self._regen_fast.setInterval(_REGEN_FAST_MS)
        self._regen_fast.timeout.connect(self._regen_preview)
        self._regen_slow = QTimer(self)
        self._regen_slow.setSingleShot(True)
        self._regen_slow.setInterval(_REGEN_SLOW_MS)
        self._regen_slow.timeout.connect(self._regen_preview)
        # Monotonic request id. A render that finishes after a newer one was
        # requested is discarded, so a slow build can never overwrite a fresh
        # board with a stale one.
        self._regen_token = 0
        # Per-chart style ticks (the style dialog's sliders stream while
        # dragged) coalesce here the same way settings edits do on the
        # Analysis page: the LAST style wins shortly after the user pauses,
        # instead of one meta write + regen per tick.
        self._style_debounce = QTimer(self)
        self._style_debounce.setSingleShot(True)
        self._style_debounce.setInterval(150)
        self._style_debounce.timeout.connect(self._flush_pending_style)
        self._pending_style: Optional[tuple] = None  # (uid, style)
        self._preview_worker: Optional[ReportPreviewWorker] = None

        self.lbl_empty = QLabel("No charts yet. Go to Analysis to generate some!")
        self.lbl_empty.setProperty("class", "faint")
        self.lbl_empty.setStyleSheet("font-size: 13px; padding: 20px;")
        self.lbl_empty.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.lbl_empty)

        splitter.addWidget(right_panel)
        splitter.setSizes([300, 900])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

    def _build_left_panel(self) -> QWidget:
        """Build the scrollable control column: session/title/layout, KPI
        management, the quick themes, and the dashboard style controls.

        The sections are COLLAPSIBLE and share one row geometry (see
        ``formatting_pane``). That is the fix for the reported "clunky and
        overlapping" panel: the old version stacked eight always-open QGroupBoxes
        whose titles were drawn at a fixed top margin, so on a narrow column the
        caption sat on the first control, and ~32 rows of identical-looking
        inputs made the panel unreadable.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # The pane is user-resizable through the splitter handle, so the
        # vertical scrollbar is the only one it should ever need. Allowing a
        # horizontal one is what let a wide control (a font combo) push the
        # content past the right edge, where it was clipped with no way to
        # scroll across to it.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # A real minimum, not a token 260px: the field rows need ~96px of label
        # plus a usable control, and below ~320 the combos start truncating.
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(440)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 10, 6)
        layout.setSpacing(8)

        # -- Session / title / layout (open by default: the two things almost
        #    every user changes first) -----------------------------------
        info = CollapsibleSection("\U0001F4DD Dashboard Info", expanded=True)
        self.session_name = QLineEdit()
        self.session_name.setPlaceholderText("Session name")
        info.add(FieldRow("Session", self.session_name,
                          "Saved with the session; used as the report title "
                          "when no title is set."))
        self.dash_title = QLineEdit()
        self.dash_title.setPlaceholderText("e.g. Q1 2025 Sales Dashboard")
        info.add(FieldRow("Title", self.dash_title,
                          "The report's heading. Leave blank to reuse the "
                          "session name. It is a card on the board like any "
                          "other: drag it, resize it, tidy it."))
        self.layout_mode = QComboBox()
        self.layout_mode.addItems(["Portrait", "Landscape"])
        info.add(FieldRow("Orientation", self.layout_mode,
                          "Page shape of the exported report."))
        # The section has to go into the layout like every other one. Without
        # this line ``info`` was a local with no parent and no reference, so it
        # was garbage-collected the moment _build_left_panel returned -- taking
        # self.session_name, self.dash_title and self.layout_mode with it. The
        # panel then rendered without its first section, and every later read of
        # those widgets raised "Internal C++ object already deleted", which
        # _regen_preview swallowed: the canvas stayed permanently blank no
        # matter how many charts were generated.
        layout.addWidget(info)

        kpi_group = CollapsibleSection("\U0001F4CA KPI Cards", expanded=True)
        self.btn_add_kpi = QPushButton("+ Add KPI")
        self.btn_add_kpi.setProperty("kind", "primary")
        self.btn_add_kpi.clicked.connect(self._on_add_kpi)
        self.btn_add_kpi.setToolTip(
            "Add a card: totals, averages, shares, period-over-period change,\n"
            "running totals, or a custom expression over your columns.")
        kpi_group.add(self.btn_add_kpi)
        kpi_hint = QLabel("Hover a card to reorder, edit or remove it.")
        kpi_hint.setWordWrap(True)
        kpi_hint.setStyleSheet("font-size: 11px;")
        kpi_group.add(kpi_hint)
        layout.addWidget(kpi_group)

        # -- Slicers --------------------------------------------------------
        # The left panel is the report-wide entry point. A per-chart slicer is
        # created from this same form by choosing "One chart" as the scope --
        # one dialog, one object, two scopes -- and both land as ordinary cards
        # on the board, so the user arranges them exactly like KPIs.
        slicer_group = CollapsibleSection("\U0001F501 Slicers", expanded=True)
        self.btn_add_slicer = QPushButton("+ Add Slicer")
        self.btn_add_slicer.setProperty("kind", "primary")
        self.btn_add_slicer.clicked.connect(self._on_add_slicer)
        self.btn_add_slicer.setToolTip(
            "Add a filter control. Choose a column and which charts it\n"
            "affects: the whole report, or one chart only.\n"
            "Charts that plot the column re-run when the selection changes.")
        slicer_group.add(self.btn_add_slicer)
        self.slicer_count = QLabel("")
        self.slicer_count.setWordWrap(True)
        self.slicer_count.setStyleSheet("font-size: 11px;")
        slicer_group.add(self.slicer_count)
        slicer_hint = QLabel(
            "Hover a slicer card to edit, reorder, clear or remove it.")
        slicer_hint.setWordWrap(True)
        slicer_hint.setStyleSheet("font-size: 11px; opacity: 0.7;")
        slicer_group.add(slicer_hint)
        # Design section (note 3): the slicer has its own colours rather than
        # borrowing the KPI chrome. Empty = inherit, same hatched-swatch
        # convention as the KPI value colour, so existing reports are
        # unaffected until a colour is actually chosen here.
        for key, label, tip in (
            ("ex_slicer_bg", "Card color",
             "Slicer card background. Empty follows the KPI card colour."),
            ("ex_slicer_bd", "Border color",
             "Slicer card border. Empty follows the theme border."),
            ("ex_slicer_text", "Text color",
             "Title and option text. Empty follows the KPI label colour."),
            ("ex_slicer_accent", "Accent color",
             "Selection status line. Empty follows the theme accent."),
        ):
            slicer_group.add(FieldRow(label, self._make_color_field(key), tip))
        layout.addWidget(slicer_group)

        # -- Quick themes --------------------------------------------------
        themes = CollapsibleSection("\U0001F3A8 Quick Themes", expanded=True)
        self.preset_gallery = PresetGallery(columns=3)
        self.preset_gallery.preset_selected.connect(self._on_preset_selected)
        themes.add(self.preset_gallery)
        self.btn_reset_style = QPushButton("Reset to defaults")
        self.btn_reset_style.setProperty("kind", "ghost")
        self.btn_reset_style.clicked.connect(self._on_reset_style)
        themes.add(self.btn_reset_style)
        layout.addWidget(themes)

        # -- Canvas colours (collapsed: the presets cover the common case) ----
        canvas = CollapsibleSection("\U0001F58C️ Canvas Colours", expanded=False)
        for key, label in (("ex_bg", "Background"), ("ex_card", "Card"),
                           ("ex_kpi", "KPI card"), ("ex_border", "Border")):
            canvas.add(FieldRow(label, self._make_color_field(key)))
        layout.addWidget(canvas)

        # -- Text & accent --------------------------------------------------
        text_group = CollapsibleSection("\U0001F524 Text & Accent", expanded=False)
        for key, label in (("ex_text", "Text"), ("ex_accent", "Accent")):
            text_group.add(FieldRow(label, self._make_color_field(key)))
        layout.addWidget(text_group)

        # -- Notes styling --------------------------------------------------
        notes_group = CollapsibleSection("\U0001F5D2️ Notes Styling", expanded=False)
        for key, label in (("ex_not_bg", "Background"),
                           ("ex_not_bd", "Border")):
            notes_group.add(FieldRow(label, self._make_color_field(key)))
        layout.addWidget(notes_group)


        # -- KPI text -------------------------------------------------------
        # Value and label are styled SEPARATELY. They were one fixed block --
        # accent-coloured 26pt number over a 0.72rem uppercase label -- so the
        # only way to change either was to change the theme, which restyled
        # every chart at the same time. Four controls now cover the two halves
        # of a KPI: what the number looks like, and what the words under it
        # look like.
        kpi_text_group = CollapsibleSection("\U0001F522 KPI Text", expanded=False)
        kpi_text_group.add(FieldRow(
            "Value color", self._make_color_field("ex_kpi_val_color"),
            "Colour of the number. Left empty it follows the theme accent, "
            "which is what every report used before."))
        kpi_text_group.add(FieldRow(
            "Label color", self._make_color_field("ex_kpi_text_color"),
            "Colour of the words under the number."))
        self.ex_kpi_val_size = QSpinBox()
        self.ex_kpi_val_size.setRange(12, 48)
        self.ex_kpi_val_size.setValue(int(self.bridge.get(
            "ex_kpi_val_size", _STYLE_DEFAULTS["ex_kpi_val_size"])))
        self.ex_kpi_val_size.valueChanged.connect(
            lambda v: self._on_field_changed("ex_kpi_val_size", v))
        kpi_text_group.add(FieldRow(
            "Value size", self.ex_kpi_val_size,
            "Point size for a short KPI value. Longer values step down "
            "automatically, in the app and in the export alike."))
        self.ex_kpi_label_size = QSpinBox()
        self.ex_kpi_label_size.setRange(7, 24)
        self.ex_kpi_label_size.setValue(int(self.bridge.get(
            "ex_kpi_label_size", _STYLE_DEFAULTS["ex_kpi_label_size"])))
        self.ex_kpi_label_size.valueChanged.connect(
            lambda v: self._on_field_changed("ex_kpi_label_size", v))
        kpi_text_group.add(FieldRow(
            "Label size", self.ex_kpi_label_size,
            "Point size for the label. Turn off caps below for a sentence-case "
            "label, which reads better in a dense KPI row."))
        self.ex_kpi_label_upper = QCheckBox("Uppercase labels")
        self.ex_kpi_label_upper.setChecked(bool(self.bridge.get(
            "ex_kpi_label_upper", _STYLE_DEFAULTS["ex_kpi_label_upper"])))
        self.ex_kpi_label_upper.toggled.connect(
            lambda v: self._on_field_changed("ex_kpi_label_upper", bool(v)))
        kpi_text_group.add(self.ex_kpi_label_upper)
        layout.addWidget(kpi_text_group)

        # -- Background image ------------------------------------------------
        self._build_background_section(layout)
        self._build_card_image_section(layout)

        # -- Title / notes typography ----------------------------------------
        layout.addWidget(self._build_typography_group(
            "\U0001F170 Title Typography", prefix="ex_title", expanded=False))
        layout.addWidget(self._build_typography_group(
            "\U0001F170 Notes Typography", prefix="ex_notes", expanded=False))

        # The old "Layout" section (density, corner radius, chart height, page
        # width, metadata) is gone from here by request: with a free canvas the
        # card's size is set by dragging its handles, so a separate "Chart
        # height" spinbox would be a second, contradictory way to do the same
        # thing. The underlying style keys are untouched -- they are still read
        # by the exporter and still applied to the report, they just no longer
        # have a control in this panel. Tidy Layout in the header is now the
        # only layout affordance, which is what a canvas wants.

        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    # -- Background image ------------------------------------------------

    # Cap on the embedded image. A data URI is base64, so a 12 MB photo becomes a
    # 16 MB string inside EVERY export -- and the dashboard rebuilds the report
    # on every settings tweak, so that cost is paid repeatedly. The limit is
    # enforced with a message rather than silently, because a dashboard that
    # quietly drops the background the user just picked is worse than one that
    # says the file is too large.
    _MAX_BG_IMAGE_BYTES = 4 * 1024 * 1024

    _BG_FILTER = ("Image Files (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;"
                  "All Files (*)")

    def _image_to_data_uri(self, path: str) -> Optional[str]:
        """Read an image and return it as a data URI, or None if unusable.

        A data URI, not a path, and that is the whole point of doing it here: the
        exported HTML has to keep showing the background on another machine,
        where the chosen file does not exist. A ``file://`` URL would render
        perfectly on the author's desktop and come out blank everywhere else.

        Cached by (path, mtime, size) so dragging the opacity slider does not
        re-read and re-encode a multi-megabyte file on every value.
        """
        try:
            p = Path(path).expanduser()
            if not p.is_file():
                return None
            stat = p.stat()
            if stat.st_size > self._MAX_BG_IMAGE_BYTES:
                return None
            key = (str(p.resolve()), stat.st_mtime, stat.st_size)
            cached = self._bg_image_cache.get(key)
            if cached is not None:
                return cached
            import base64
            import mimetypes
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            uri = f"data:{mime};base64,{data}"
            self._bg_image_cache[key] = uri
            return uri
        except (OSError, ValueError):
            log.warning("background image could not be read: %s", path,
                        exc_info=True)
            return None

    def _build_background_section(self, layout) -> None:
        """Background image controls: pick, opacity, blend, clear.

        The image is composited OVER the theme colour rather than replacing it,
        which is what makes one picture work with every preset: the theme stays
        the base layer, so the report's own text colour is always sitting on a
        colour from its own palette.
        """
        section = CollapsibleSection("\U0001F5BC️ Background Image",
                                     expanded=False)
        pick = QWidget()
        pick_row = QHBoxLayout(pick)
        pick_row.setContentsMargins(0, 0, 0, 0)
        pick_row.setSpacing(6)
        self.btn_bg_image = QPushButton("Choose image...")
        self.btn_bg_image.setProperty("kind", "ghost")
        self.btn_bg_image.clicked.connect(self._on_choose_bg_image)
        self.btn_bg_image.setToolTip(
            "Use an image behind the whole report.\n\nIt is layered over the "
            "theme colour, so it works with every theme, and it is embedded "
            "into the exported file rather than linked -- the saved HTML shows "
            "the same background on any machine.")
        pick_row.addWidget(self.btn_bg_image)
        self.btn_bg_clear = QPushButton("Clear")
        self.btn_bg_clear.setProperty("kind", "ghost")
        self.btn_bg_clear.clicked.connect(self._on_clear_bg_image)
        self.lbl_bg_image = QLabel("No image")
        self.lbl_bg_image.setProperty("class", "faint")
        self.lbl_bg_image.setWordWrap(True)
        self.lbl_bg_image.setStyleSheet("font-size: 11px;")
        pick_row.addWidget(self.lbl_bg_image, stretch=1)
        section.add(pick)

        self.ex_bg_image_opacity = QSpinBox()
        self.ex_bg_image_opacity.setRange(0, 100)
        self.ex_bg_image_opacity.setSuffix(" %")
        self.ex_bg_image_opacity.setValue(int(self.bridge.get(
            "ex_bg_image_opacity",
            _STYLE_DEFAULTS["ex_bg_image_opacity"]) or 0))
        self.ex_bg_image_opacity.valueChanged.connect(
            lambda v: self._on_field_changed("ex_bg_image_opacity", v,
                                             continuous=True))
        section.add(FieldRow(
            "Opacity", self.ex_bg_image_opacity,
            "How strongly the image shows through the theme colour. Low values "
            "keep the report readable; a busy photo at 100% is not."))

        self.ex_bg_blend = QComboBox()
        self.ex_bg_blend.addItems(["Normal", "Multiply", "Screen", "Overlay",
                                   "Soft light", "Luminosity", "Darken",
                                   "Lighten"])
        _blend_keys = {"normal": 0, "multiply": 1, "screen": 2, "overlay": 3,
                       "soft light": 4, "soft-light": 4, "luminosity": 5,
                       "darken": 6, "lighten": 7}
        current_blend = str(self.bridge.get(
            "ex_bg_blend", _STYLE_DEFAULTS["ex_bg_blend"]) or "normal").lower()
        self.ex_bg_blend.setCurrentIndex(_blend_keys.get(current_blend, 0))
        self.ex_bg_blend.currentTextChanged.connect(
            lambda v: self._on_field_changed(
                "ex_bg_blend", v.lower().replace(" ", "-")))
        section.add(FieldRow(
            "Blend", self.ex_bg_blend,
            "How the image mixes with the theme colour. Multiply darkens a "
            "light theme, screen lightens a dark one, and Normal is the "
            "plain overlay."))
        layout.addWidget(section)
        self._refresh_bg_image_label()

    # The card groups the per-card image can be applied to, in the order they
    # are offered. Stored as one comma-separated style key rather than three
    # booleans so that "which cards" is a single persisted value that survives
    # a save/load round trip without the panel having to reconcile three
    # independent keys that could disagree with each other.
    _CARD_IMAGE_GROUPS = (("charts", "Chart cards"),
                          ("kpi", "KPI cards"),
                          ("title", "Title card"))

    def _build_card_image_section(self, layout) -> None:
        """Per-CARD background image: pick, where it applies, opacity, blend.

        The page Background Image and this are deliberately two separate
        settings rather than one image with a "where" switch. They solve
        different problems: the page image dresses the whole report, while this
        one is for a picture BEHIND THE CARDS -- a texture on the KPI strip, a
        branded banner behind the title band, a photo behind the charts --
        without touching the page at all. Sharing one control would mean
        choosing between those, not having both.
        """
        section = CollapsibleSection("\U0001F4E6 Card Backgrounds",
                                     expanded=False)
        pick = QWidget()
        pick_row = QHBoxLayout(pick)
        pick_row.setContentsMargins(0, 0, 0, 0)
        pick_row.setSpacing(6)
        self.btn_card_image = QPushButton("Choose image...")
        self.btn_card_image.setProperty("kind", "ghost")
        self.btn_card_image.clicked.connect(self._on_choose_card_image)
        self.btn_card_image.setToolTip(
            "Use an image behind the CARDS instead of behind the whole "
            "page.\n\nPick where it applies below -- the charts, the KPI "
            "cards, the title band, or any combination. Like the page "
            "background it is layered over the card colour and embedded into "
            "the exported file, so the saved HTML shows it on any machine.")
        pick_row.addWidget(self.btn_card_image)
        self.btn_card_image_clear = QPushButton("Clear")
        self.btn_card_image_clear.setProperty("kind", "ghost")
        self.btn_card_image_clear.clicked.connect(self._on_clear_card_image)
        self.lbl_card_image = QLabel("No image")
        self.lbl_card_image.setProperty("class", "faint")
        self.lbl_card_image.setWordWrap(True)
        self.lbl_card_image.setStyleSheet("font-size: 11px;")
        pick_row.addWidget(self.lbl_card_image, stretch=1)
        section.add(pick)

        # One checkbox per card kind, all defaulting on. Written back as the
        # single comma-separated key on every toggle.
        self._card_image_checks: dict = {}
        boxes = QWidget()
        boxes_row = QHBoxLayout(boxes)
        boxes_row.setContentsMargins(0, 0, 0, 0)
        boxes_row.setSpacing(10)
        for key, label in self._CARD_IMAGE_GROUPS:
            box = QCheckBox(label)
            box.setToolTip(f"Put the card image behind the {label.lower()}.")
            box.toggled.connect(self._on_card_image_group_toggled)
            boxes_row.addWidget(box)
            self._card_image_checks[key] = box
        boxes_row.addStretch()
        section.add(FieldRow(
            "Apply to", boxes,
            "Which cards carry the picture. Unchecking every one of them is "
            "the same as clearing it -- there is nowhere left to draw it."))

        self.ex_card_image_opacity = QSpinBox()
        self.ex_card_image_opacity.setRange(0, 100)
        self.ex_card_image_opacity.setSuffix(" %")
        self.ex_card_image_opacity.setValue(int(self.bridge.get(
            "ex_card_image_opacity",
            _STYLE_DEFAULTS["ex_card_image_opacity"]) or 0))
        self.ex_card_image_opacity.valueChanged.connect(
            lambda v: self._on_field_changed("ex_card_image_opacity", v,
                                             continuous=True))
        section.add(FieldRow(
            "Opacity", self.ex_card_image_opacity,
            "How strongly the picture shows through the card colour. Keep it "
            "low: the text on the card is the content, the picture is the "
            "texture behind it."))

        self.ex_card_blend = QComboBox()
        self.ex_card_blend.addItems(["Normal", "Multiply", "Screen", "Overlay",
                                     "Soft light", "Luminosity", "Darken",
                                     "Lighten"])
        _card_blend_keys = {"normal": 0, "multiply": 1, "screen": 2,
                            "overlay": 3, "soft light": 4, "soft-light": 4,
                            "luminosity": 5, "darken": 6, "lighten": 7}
        current_card_blend = str(self.bridge.get(
            "ex_card_blend",
            _STYLE_DEFAULTS["ex_card_blend"]) or "normal").lower()
        self.ex_card_blend.setCurrentIndex(
            _card_blend_keys.get(current_card_blend, 0))
        self.ex_card_blend.currentTextChanged.connect(
            lambda v: self._on_field_changed(
                "ex_card_blend", v.lower().replace(" ", "-")))
        section.add(FieldRow(
            "Blend", self.ex_card_blend,
            "How the picture mixes with the card colour. Soft light and "
            "overlay sit a texture on top without washing out the number; "
            "Multiply darkens a light card."))
        layout.addWidget(section)
        self._refresh_card_image_label()

    def _current_card_image_groups(self) -> list:
        """The card kinds currently ticked, as a list of style-key values."""
        return [key for key, _ in self._CARD_IMAGE_GROUPS
                if self._card_image_checks[key].isChecked()]

    def _on_card_image_group_toggled(self) -> None:
        """Write the ticked card kinds back as the one style key.

        Every toggle recomputes the whole list rather than toggling one entry:
        that way the stored value is always exactly what is on screen, and a
        load that found an unrecognised group name in the key cannot leave the
        panel and the report disagreeing.
        """
        if getattr(self, "_syncing_widgets", False):
            return
        self.bridge.set("ex_card_image_groups",
                        ",".join(self._current_card_image_groups()))
        self._schedule_regen()

    def _sync_card_image_enabled(self) -> None:
        """Grey out what cannot act until an image is actually chosen."""
        has_image = bool(self.bridge.get("ex_card_image", ""))
        self.ex_card_image_opacity.setEnabled(has_image)
        self.ex_card_blend.setEnabled(has_image)
        # The group boxes stay enabled with no image: deciding WHERE the
        # picture will go before choosing it is a reasonable order of work, and
        # the choice is remembered either way.
        self.btn_card_image_clear.setEnabled(has_image)

    def _refresh_card_image_label(self) -> None:
        """Show which card image is set, or that there is none."""
        uri = str(self.bridge.get("ex_card_image", "") or "")
        if not uri:
            self.lbl_card_image.setText("No image")
            self._sync_card_image_enabled()
            return
        # The SOURCE filename, not the data URI: the bridge deliberately stores
        # an embedded copy, and "data:image/png;base64,iVBOR..." is not
        # something a user can recognise as their own file.
        source = str(self.bridge.get("ex_card_image_name", "") or "image")
        try:
            self.lbl_card_image.setText(
                f"{Path(source).name}  "
                f"({max(1, len(uri) // 1024)} KB embedded)")
        except (TypeError, ValueError):
            self.lbl_card_image.setText("image")
        self._sync_card_image_enabled()

    def _on_choose_card_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a card background image", str(Path.home()),
            self._BG_FILTER)
        if not path:
            return
        uri = self._image_to_data_uri(path)
        if not uri:
            QMessageBox.warning(
                self, "Card Background Image",
                "That file could not be used.\n\n"
                f"Images up to {self._MAX_BG_IMAGE_BYTES // (1024 * 1024)} MB "
                "are embedded into the report so the exported file keeps the "
                "picture.")
            return
        self.bridge.set("ex_card_image", uri)
        # Remembered separately, for display only: the data URI is what the
        # report needs, and a user has to be able to recognise which file it
        # came from.
        self.bridge.set("ex_card_image_name", str(path))
        self._refresh_card_image_label()
        self._schedule_regen()

    def _on_clear_card_image(self) -> None:
        if not self.bridge.get("ex_card_image", ""):
            return
        self.bridge.set("ex_card_image", "")
        self.bridge.set("ex_card_image_name", "")
        self._refresh_card_image_label()
        self._schedule_regen()

    def _refresh_bg_image_label(self) -> None:
        """Show what the background image currently is, or that there is none."""
        uri = str(self.bridge.get("ex_bg_image", "") or "")
        if not uri:
            self.lbl_bg_image.setText("No image")
            self.btn_bg_clear.setEnabled(False)
            return
        # The label shows the SOURCE, not the data URI: the bridge deliberately
        # stores an embedded copy, and "data:image/png;base64,iVBOR..." is not
        # something a user can recognise as their own file.
        source = str(self.bridge.get("ex_bg_image_name", "") or "image")
        try:
            self.lbl_bg_image.setText(
                f"{Path(source).name}  "
                f"({max(1, len(uri) // 1024)} KB embedded)")
        except (TypeError, ValueError):
            self.lbl_bg_image.setText("image")
        self.btn_bg_clear.setEnabled(True)

    def _on_choose_bg_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a background image", str(Path.home()),
            self._BG_FILTER)
        if not path:
            return
        uri = self._image_to_data_uri(path)
        if not uri:
            QMessageBox.warning(
                self, "Background Image",
                "That file could not be used.\n\n"
                f"Images up to {self._MAX_BG_IMAGE_BYTES // (1024 * 1024)} MB "
                "are embedded into the report so the exported file keeps the "
                "background.")
            return
        self.bridge.set("ex_bg_image", uri)
        # Remembered separately, for display only: the data URI is what the
        # report needs, and a user has to be able to recognise which file it
        # came from.
        self.bridge.set("ex_bg_image_name", str(path))
        self._refresh_bg_image_label()
        self._schedule_regen()

    def _on_clear_bg_image(self) -> None:
        if not self.bridge.get("ex_bg_image", ""):
            return
        self.bridge.set("ex_bg_image", "")
        self.bridge.set("ex_bg_image_name", "")
        self._refresh_bg_image_label()
        self._schedule_regen()

    def _build_typography_group(self, title: str, prefix: str,
                                expanded: bool = True) -> CollapsibleSection:
        """Shared 4-field pattern (font, style, size, color) used for both
        title and notes typography -- avoids writing the same block twice."""
        group = CollapsibleSection(title, expanded=expanded)

        font_key = f"{prefix}_font"
        font_combo = QFontComboBox()
        font_combo.setCurrentFont(QFont(self.bridge.get(font_key, _STYLE_DEFAULTS[font_key])))
        font_combo.currentFontChanged.connect(
            lambda f, k=font_key: self._on_field_changed(k, f.family())
        )
        group.add(FieldRow("Font", font_combo))
        setattr(self, font_key, font_combo)

        style_key = f"{prefix}_style"
        style_combo = QComboBox()
        style_combo.addItems(["Normal", "Italic", "Bold"])
        style_combo.setCurrentText(self.bridge.get(style_key, _STYLE_DEFAULTS[style_key]))
        style_combo.currentTextChanged.connect(
            lambda v, k=style_key: self._on_field_changed(k, v)
        )
        group.add(FieldRow("Style", style_combo))
        setattr(self, style_key, style_combo)

        size_key = f"{prefix}_size"
        size_spin = QSpinBox()
        size_spin.setRange(8, 72)
        size_spin.setValue(int(self.bridge.get(size_key, _STYLE_DEFAULTS[size_key])))
        size_spin.valueChanged.connect(
            lambda v, k=size_key: self._on_field_changed(k, v)
        )
        group.add(FieldRow("Size", size_spin))
        setattr(self, size_key, size_spin)

        color_key = f"{prefix}_color"
        group.add(FieldRow("Color", self._make_color_field(color_key)))

        return group

    def _sync_style_widgets_from_bridge(self) -> None:
        """Update every style-panel widget's displayed value to match
        current bridge state. Needed after loading a saved session --
        the left panel's widgets are built once at page construction,
        reading bridge.get(key, default) at THAT moment only; writing
        new values into the bridge later (e.g. from a loaded session)
        doesn't retroactively update an already-constructed QComboBox's
        displayed text or a color swatch's background.

        Without this the panel would visibly contradict the board it controls:
        the report would correctly show a saved purple theme while the swatches
        still showed the previous session's colours.
        """
        style = self._current_style_dict()
        # Every write below fires the widget's change signal, and each of those
        # is wired to ``_on_field_changed`` -- so without this guard the sync
        # writes the bridge back from the widgets and marks unrelated fields
        # dirty. That is how loading a session used to half-revert a theme.
        self._syncing_widgets = True
        try:
            self._sync_widgets_from_style(style)
        finally:
            self._syncing_widgets = False

    def _sync_widgets_from_style(self, style: dict) -> None:
        """Write *style* into the panel widgets. Assumes the sync guard is set."""
        for key, swatch in self._color_swatches.items():
            color = style.get(key, _STYLE_DEFAULTS.get(key, "#000000"))
            swatch.setStyleSheet(
                f"background:{color}; border:1px solid palette(mid); border-radius:4px;"
                if str(color or "").strip() else
                ("background: palette(window); border:1px solid palette(mid); "
                 "border-radius:4px;")
            )

        # ex_density / ex_radius / ex_chart_h / ex_width / ex_meta have no
        # widget any more (the Layout section is gone), so there is nothing to
        # push their loaded values into. They are still carried in the style
        # dict and still reach the exporter, so a saved session keeps whatever
        # it was saved with.
        self.ex_kpi_val_size.setValue(int(style.get("ex_kpi_val_size", 26)))
        self.ex_kpi_label_size.setValue(int(style.get(
            "ex_kpi_label_size",
            _STYLE_DEFAULTS["ex_kpi_label_size"])))
        self.ex_kpi_label_upper.setChecked(bool(style.get(
            "ex_kpi_label_upper", _STYLE_DEFAULTS["ex_kpi_label_upper"])))
        self.ex_bg_image_opacity.setValue(int(style.get(
            "ex_bg_image_opacity",
            _STYLE_DEFAULTS["ex_bg_image_opacity"]) or 0))
        blend_index = {"normal": 0, "multiply": 1, "screen": 2, "overlay": 3,
                       "soft-light": 4, "luminosity": 5, "darken": 6,
                       "lighten": 7}.get(
                           str(style.get("ex_bg_blend", "normal") or "normal"),
                           0)
        self.ex_bg_blend.setCurrentIndex(blend_index)
        self._refresh_bg_image_label()

        # The card picture. Opacity and blend are ordinary controls, but the
        # GROUP checkboxes have to be driven from the stored comma-separated
        # key -- and they must be set INSIDE the sync guard, or each setChecked
        # fires `toggled` and the first box would rewrite the key before the
        # others had been read, silently dropping the groups it had not reached
        # yet.
        self.ex_card_image_opacity.setValue(int(style.get(
            "ex_card_image_opacity",
            _STYLE_DEFAULTS["ex_card_image_opacity"]) or 0))
        card_blend_index = {"normal": 0, "multiply": 1, "screen": 2,
                            "overlay": 3, "soft-light": 4, "luminosity": 5,
                            "darken": 6, "lighten": 7}.get(
                                str(style.get("ex_card_blend", "normal")
                                    or "normal").lower().replace(" ", "-"),
                                0)
        self.ex_card_blend.setCurrentIndex(card_blend_index)
        raw_groups = {
            part.strip() for part in
            str(style.get("ex_card_image_groups", "") or "").split(",")
            if part.strip()}
        for key, box in self._card_image_checks.items():
            box.setChecked(key in raw_groups)
        self._refresh_card_image_label()

        for prefix in ("ex_title", "ex_notes"):
            font_widget = getattr(self, f"{prefix}_font", None)
            if font_widget is not None:
                font_widget.setCurrentFont(QFont(style.get(f"{prefix}_font", "Inter")))
            style_widget = getattr(self, f"{prefix}_style", None)
            if style_widget is not None:
                style_widget.setCurrentText(style.get(f"{prefix}_style", "Normal"))
            size_widget = getattr(self, f"{prefix}_size", None)
            if size_widget is not None:
                # Fall back to _STYLE_DEFAULTS, not a literal: this hard-coded
                # 16 made the panel show 16 while the report used 28, so the
                # control visibly disagreed with the thing it controls.
                size_widget.setValue(int(style.get(
                    f"{prefix}_size", _STYLE_DEFAULTS[f"{prefix}_size"])))

    def _make_color_field(self, key: str) -> QWidget:
        """One reusable color-picker row: a clickable swatch that opens
        QColorDialog and writes straight into the bridge, then re-applies
        live styling -- shared by every one of the 8 color keys instead of
        writing near-identical blocks for each."""
        default = _STYLE_DEFAULTS.get(key, "#000000")
        current = self.bridge.get(key, default)

        def _sheet(colour: str) -> str:
            # A key may legitimately hold "" -- "no colour chosen, follow the
            # theme" (the KPI value colour is exactly that). Writing
            # "background:;" into a stylesheet leaves Qt painting the button in
            # whatever the app theme happens to be, which reads as a colour the
            # user never picked, so an unset swatch is drawn hatched instead.
            if not str(colour or "").strip():
                return ("background: palette(window); border:1px solid "
                        "palette(mid); border-radius:4px;")
            return (f"background:{colour}; border:1px solid palette(mid); "
                    "border-radius:4px;")

        swatch = QPushButton()
        swatch.setFixedSize(32, 22)
        swatch.setCursor(Qt.PointingHandCursor)
        swatch.setStyleSheet(_sheet(current))
        if not str(current or "").strip():
            swatch.setToolTip("Not set: this follows the theme accent colour.")

        def pick():
            color = QColorDialog.getColor(QColor(current or "#000000"), self)
            if color.isValid():
                hexval = color.name()
                swatch.setStyleSheet(_sheet(hexval))
                swatch.setToolTip("")
                self._on_field_changed(key, hexval)

        swatch.clicked.connect(pick)
        self._color_swatches[key] = swatch
        return swatch

    @staticmethod
    def _input_style() -> str:
        return _theme_mod.input_style()

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.btn_back.clicked.connect(self._on_back)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_tidy.clicked.connect(self._on_tidy_layout)
        self.btn_export_png.clicked.connect(self._on_export_png)
        self.btn_save.clicked.connect(self._on_save)
        self.bridge.charts_changed.connect(self._on_charts_changed)
        self.bridge.theme_changed.connect(self._on_theme_changed)
        # Slicers can be added from the ANALYSIS page (the per-chart ⊞ button
        # writes through the bridge), and this board is a different page:
        # without listening here, that slicer would only surface on the next
        # unrelated rebuild.
        self.bridge.state_changed.connect(self._on_bridge_state_changed)

        # The three "Dashboard Info" fields are read by _export_inputs, and
        # _export_inputs is only ever CALLED from a rebuild -- so with nothing
        # connected here, typing a title changed nothing on the board until some
        # other control happened to trigger a rebuild. That is the "the title is
        # not changing" report, and it applies to the session name and the
        # orientation exactly as much as to the title.
        #
        # Debounced like every other control, and on the SLOW timer because a
        # text field streams a value per keystroke.
        self.dash_title.textChanged.connect(self._on_report_field_changed)
        self.session_name.textChanged.connect(self._on_report_field_changed)
        self.layout_mode.currentTextChanged.connect(
            self._on_report_field_changed)

    def _on_report_field_changed(self, *_args) -> None:
        """Title / session name / orientation changed: rebuild the report."""
        if getattr(self, "_syncing_widgets", False):
            return
        self._schedule_regen(continuous=True)

    def _on_theme_changed(self, _mode: str) -> None:
        """Handle a dark<->light switch.

        The board deliberately does NOT re-render. A report is a document with
        its own palette, and it keeps it whichever app theme is active -- the
        same reason a Tableau dashboard stays white inside a dark IDE, and the
        reason a chosen preset looks identical in both modes instead of shifting
        under the user.

        The app chrome around the canvas is restyled by the global stylesheet,
        and the swatches are refreshed so the panel never contradicts the board.
        """
        try:
            self._sync_style_widgets_from_bridge()
        except Exception:
            log.debug("style widget sync failed", exc_info=True)

    # -- Style controls -------------------------------------------------

    def _current_style_dict(self) -> dict:
        """Current values for the 23 style keys.

        One dict, used by the panel, the live canvas AND the exported file.
        There used to be a separate theme-following dict for the board; with
        the canvas rendering the real report that split is impossible to
        maintain honestly, and having it is precisely why the board and the
        file disagreed.
        """
        return {key: self.bridge.get(key, default)
                for key, default in _STYLE_DEFAULTS.items()}

    def _export_style_dict(self) -> dict:
        """Style dict handed to the report writer.

        Carries one extra key beyond the 23 style keys: ``ex_preset``, the name
        of the quick theme in effect. It is not a colour and the report writer
        ignores it -- it is stored purely so re-opening the session can re-mark
        the right swatch in the theme gallery.
        """
        out = self._current_style_dict()
        out["ex_preset"] = str(self.bridge.get("ex_preset", "") or "")
        return out

    def _on_field_changed(self, key: str, value, *, continuous: bool = False) -> None:
        """Any style control (colour, font, size, checkbox...) calls this.

        Writes the bridge and asks for a rebuild. There is no per-setting wiring
        to the board any more: density, corner radius, chart height, page width,
        show-metadata and both typography groups already reach the report, and
        the report is what the canvas shows.

        ``continuous=True`` for controls that stream values while the pointer is
        still down (spin boxes, sliders), which take the longer debounce.
        """
        if getattr(self, "_syncing_widgets", False):
            return
        self.bridge.set(key, value)
        self._schedule_regen(continuous=continuous)

    def _on_preset_selected(self, name: str) -> None:
        """Apply one of the quick themes across the whole board.

        The colours are written as EXPLICIT overrides, which is what makes the
        board follow the theme: a value still at its default would otherwise
        not move, so the preset would show on the swatches but not on the board.

        Layout keys (density, radius, chart height, fonts) are deliberately not
        touched -- a colour theme must not silently undo the arrangement the
        user just made.
        """
        merged = apply_preset(self._current_style_dict(), name)
        for key in _OVERRIDABLE_COLORS:
            value = merged.get(key)
            if value is not None:
                self.bridge.set(key, value)
        self.bridge.set("ex_preset", str(name))
        self._schedule_regen()
        self._sync_style_widgets_from_bridge()
        self.preset_gallery.select(name)

    def _on_reset_style(self) -> None:
        """Drop every colour override and return to the built-in report palette.

        Confirmed first, because it discards a whole hand-picked colour scheme
        and there is no undo.
        """
        confirm = QMessageBox.question(
            self, "Reset styling",
            "Clear every colour override and return to the default report "
            "colours?\n\nLayout settings (density, sizes, fonts) are kept.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        for key in _OVERRIDABLE_COLORS:
            self.bridge.set(key, _STYLE_DEFAULTS.get(key, ""))
        # The background image goes too, and deliberately: "Reset to defaults"
        # that leaves a picture behind is not a reset, and the image is the one
        # setting whose presence the user is least likely to notice afterwards.
        self.bridge.set("ex_bg_image", "")
        self.bridge.set("ex_bg_image_name", "")
        # The card picture goes for the same reason: a picture left behind by a
        # "reset" is exactly the kind of thing the user does not notice.
        self.bridge.set("ex_card_image", "")
        self.bridge.set("ex_card_image_name", "")
        self.bridge.set("ex_preset", "")
        self._schedule_regen()
        self._sync_style_widgets_from_bridge()
        self.preset_gallery.clear()


    # -- Live canvas: regeneration ---------------------------------------
    # Every control funnels through here. There is deliberately no per-setting
    # call into the board any more: the board IS the report, and the report
    # already reads all of these keys.

    def _schedule_regen(self, *, continuous: bool = False) -> None:
        """Ask for a rebuild after a short quiet period."""
        fast, slow = self._regen_fast, self._regen_slow
        fast.stop()
        slow.stop()
        (slow if continuous else fast).start()

    def _snapshot_inputs(self) -> dict:
        """Cheap bridge reads for the background input assembler.

        Everything here is a reference copy or a small dict -- no recipe
        re-runs, no downsampling, no JSON. The heavy work happens in
        ReportInputsWorker; this must stay instant because it runs on the
        UI thread after every drag release and slicer click.
        """
        charts = self.bridge.get("charts", [])
        slicers = list(self.bridge.get("slicers", []) or [])
        cols = int(self.bridge.get("grid_cols_n", 2))
        tile_geom, kpi_geom = self._current_geometry(charts,
            self.bridge.get("kpis", []), cols)
        return {
            "token": self._regen_token + 1,
            "charts": charts,
            "kpis": self.bridge.get("kpis", []),
            "slicers": slicers,
            "df": self.bridge.df,
            "recipes": {c[0]: r for c in charts
                          for r in (self._chart_recipe(c[0]),) if r},
            "meta_map": {uid: self.bridge.get_chart_meta(uid)
                           for uid, _, _ in charts},
            "notes": {uid: self.bridge.get_note(uid)
                        for uid, _, _ in charts},
            "session_name": (self.session_name.text().strip()
                               or "Lytrize Report"),
            "orientation": ("landscape"
                              if self.layout_mode.currentText() == "Landscape"
                              else "portrait"),
            "state": self._export_style_dict(),
            "dashboard_title": self.dash_title.text().strip(),
            "grid_cols_n": cols,
            "tile_geometry": tile_geom,
            "kpi_geometry": kpi_geom,
            "slicer_geometry": dict(
                getattr(self, "_slicer_geom", {}) or {}),
            "geometry_mode": "free",
        }

    def _regen_preview(self) -> None:
        """Assemble inputs off-thread, then build the report, then show it."""
        if not getattr(self, "report_canvas", None) or \
                not self.report_canvas.is_available():
            return
        # Two-stage pipeline. Stage 1 (ReportInputsWorker) re-runs
        # slicer-affected recipes + builds the payload OFF the UI thread --
        # that synchronous work after every drag release was the "whole app
        # freezes" report. Stage 2 (ReportPreviewWorker) renders the HTML as
        # before. Both stages carry the SAME token so a newer request
        # discards a stale one at either step.
        #
        # The SAME inputs the saved file is built from, rather than a second
        # reconstruction of them. The worker is handed the already-built
        # export tuples because that is the only copy carrying notes and
        # chart_type: the raw session charts are bare (uid, title, fig), so
        # rebuilding them inside the worker silently dropped both -- the canvas
        # then showed charts with no notes while the exported file had them,
        # which is precisely the preview/actual disagreement this design exists
        # to eliminate.
        snap = self._snapshot_inputs()
        self._regen_token = snap["token"]
        token = snap["token"]
        self._pending_snap = snap
        from desktop.workers import ReportInputsWorker
        worker = ReportInputsWorker(token, snap, parent=self)
        worker.ready.connect(self._on_inputs_ready)
        worker.failed.connect(self._on_preview_failed)
        self._inputs_worker = worker
        worker.start()

    def _on_inputs_ready(self, token: int, partial: dict) -> None:
        """Stage 1 done: merge worker results, kick off the HTML build."""
        if token != self._regen_token:
            return
        snap = getattr(self, "_pending_snap", None) or {}
        d = dict(snap)
        d.update(partial)
        worker = ReportPreviewWorker(
            d["export_charts"],
            d["session_name"],
            d["state"],
            token,
            orientation=d["orientation"],
            kpis=d["kpis"],
            dashboard_title=d["dashboard_title"],
            grid_cols_n=d["grid_cols_n"],
            tile_geometry=d["tile_geometry"],
            kpi_geometry=d["kpi_geometry"],
            slicers=d["slicers"],
            slicer_geometry=d["slicer_geometry"],
            slicer_values=d["slicer_values"],
            geometry_mode=d["geometry_mode"],
            extra_css=self._preview_extra_css(),
            parent=self,
        )
        worker.ready.connect(self._on_preview_ready)
        worker.failed.connect(self._on_preview_failed)
        self._preview_worker = worker
        worker.start()

    @staticmethod
    def _preview_extra_css() -> str:
        """Fonts the canvas must be offered explicitly.

        Chromium cannot see fonts registered with Qt, so without this a chosen
        family silently substitutes and the canvas would promise a font the
        exported file never actually uses.
        """
        try:
            from desktop.widgets.plotly_web_view import bundled_font_css
            return bundled_font_css()
        except Exception:
            log.debug("bundled font CSS unavailable for canvas", exc_info=True)
            return ""

    def _on_preview_ready(self, token: int, html: str) -> None:
        """Show a finished build -- unless a newer one superseded it."""
        if token != self._regen_token:
            return
        self.report_canvas.set_report(html)

    def _on_preview_failed(self, token: int, error: str) -> None:
        if token != self._regen_token:
            return
        log.error("dashboard preview build failed: %s", error)

    # __GEOM__
    # -- Geometry ---------------------------------------------------------
    # Python is the authority. The page's overlay measures the live CSS grid and
    # PROPOSES a placement; everything is clamped and de-overlapped here before
    # it is stored or exported, so a malformed proposal can never reach the
    # report as invalid CSS (a browser discards "grid-column: -4 / span 2" and
    # silently full-bleeds the card instead).

    def _gap_px(self) -> float:
        """The report's card gap in pixels -- the spacing tidy re-flows to.

        Read from the exporter's own density table rather than from a constant
        here. That table is what actually decides the report's gap, and a second
        copy of the numbers is a second thing to forget to update: tidy would
        then re-flow the board to a spacing the exported file does not use.
        """
        from services.export_service import build_export_theme
        theme = build_export_theme(self._current_style_dict())
        return float(theme.get("gap", 24))

    def _card_height(self) -> float:
        """Default height for a newly placed card, from the report's own setting."""
        return float(self.bridge.get("ex_chart_h", _STYLE_DEFAULTS["ex_chart_h"])
                     or _STYLE_DEFAULTS["ex_chart_h"])

    def _current_geometry(self, charts, kpis, cols: int = 2) -> tuple:
        """The chart and KPI tile maps for the current board.

        KPI tiles are keyed "0", "1"... so both maps live in one persisted blob
        and the report writer reads each straight back.

        ``cols`` is accepted and ignored. It is what the grid used to need, and
        keeping it in the signature means the several call sites that pass it did
        not all have to change at once; the canvas no longer has columns.
        """
        stored = dict(self.bridge.get("tile_geometry", {}) or {})
        # MIGRATION. A blob written by the grid build stores {x, y, w, h} in
        # CELLS. Those numbers are read as free-form fractions and pixels, they
        # are clamped rather than rejected, and the board silently rearranges
        # itself on the first open -- so they are converted here, once, using the
        # card height and gap of the moment. The converted map is written back by
        # the next _persist_geometry, so this costs one render, not every one.
        #
        # An explicit "free" marker means this board was already saved on the
        # free canvas, so the migration must never run on it -- that is what let
        # a full-width free chart (w=1, x=0) be misread as a one-column grid cell
        # and reset to a scrambled position. Only a MARKER-LESS blob (a genuine
        # legacy grid session) falls through to the heuristic, which now also
        # inspects y/h so even that path no longer misreads a free card.
        if (stored and stored.get(_MODE_KEY) != "free"
                and tile_geometry.is_grid_shaped(
                    {u: g for u, g in stored.items()
                     if u != _TITLE_UID and u != _MODE_KEY}, cols or 2)):
            stored = dict(tile_geometry.from_grid(
                {u: g for u, g in stored.items()
                 if u != _TITLE_UID and u != _MODE_KEY},
                cols or 2, self._card_height(), self._gap_px()), **{
                    u: g for u, g in stored.items()
                    if u == _TITLE_UID or u == _MODE_KEY})

        chart_uids = [c[0] for c in charts]
        # The title card lives in the CHART map's sibling -- it is not a chart and
        # it must not be dropped by the "is this chart still here" filter below,
        # which would delete its stored placement on the very next render and
        # make the heading jump back to the top every time anything moved.
        chart_map = {u: g for u, g in stored.items()
                     if _kpi_key(u) is None and u != _TITLE_UID
                     and _slicer_key(u) is None}
        title_map = {u: g for u, g in stored.items() if u == _TITLE_UID}
        # Normalised on read as well as on write, so a blob written before the
        # two spellings were reconciled still lands on real tiles.
        kpi_map = {_kpi_key(u): g for u, g in stored.items()
                   if _kpi_key(u) is not None}
        # Slicer tiles are keyed "slicer:<i>" from the overlay and "0", "1"...
        # in the blob, exactly like KPIs. They are peeled off BEFORE the chart
        # filter below, because a "slicer:0" key would otherwise be filed as a
        # chart uid, fail the "is this chart still here" check, and be dropped --
        # which is what made a slicer drag silently do nothing.
        slicer_map = {_slicer_key(u): g for u, g in stored.items()
                      if _slicer_key(u) is not None}
        # Drop anything whose block is gone. A restored arrangement naming a
        # deleted chart otherwise keeps contributing cells forever, so the saved
        # blob grows a little more on every session.
        chart_map = {u: g for u, g in chart_map.items() if u in chart_uids}
        kpi_map = {u: g for u, g in kpi_map.items()
                   if u.isdigit() and int(u) < len(kpis)}
        gap = self._gap_px()
        # The title band, resolved through the SAME helper the report writer
        # uses. On a board that has never had one, that shifts everything else
        # down by one band -- and because both sides call one function, the
        # canvas, the preview and the saved file shift by the same amount
        # instead of the file quietly disagreeing with the screen.
        merged, _title_g = tile_geometry.ensure_title_band(
            {**chart_map, **kpi_map, **slicer_map, **title_map},
            height=tile_geometry.title_band_height(
                self.bridge.get("ex_title_size",
                                _STYLE_DEFAULTS["ex_title_size"]),
                bool(self.bridge.get("ex_meta", True))),
            gap_px=gap)
        title_map = {_TITLE_UID: merged[_TITLE_UID]}
        chart_map = {u: g for u, g in merged.items()
                     if _kpi_key(u) is None and _slicer_key(u) is None
                     and u != _TITLE_UID}
        kpi_map = {_kpi_key(u): g for u, g in merged.items()
                   if _kpi_key(u) is not None}
        slicer_map = {_slicer_key(u): g for u, g in merged.items()
                      if _slicer_key(u) is not None}
        # The two groups are auto-placed from SEPARATE maps, so both would put
        # their first card at y=0 on a fresh session -- and because the charts are
        # emitted after the KPIs they would simply cover the KPI row, which is
        # exactly the "KPI card does not appear" report. Placing the KPIs first
        # and using their height as the floor for the charts keeps the two bands
        # stacked. Only the auto-placed cards move; anything the user (or a saved
        # session) already placed is left alone.
        # The floor is the title band's bottom edge: a fresh board would
        # otherwise auto-place its first KPI at y=0, directly under the heading
        # and on top of it.
        floor = (float(title_map[_TITLE_UID]["y"])
                 + float(title_map[_TITLE_UID]["h"]) + gap)
        # Slicers go FIRST, between the title and the KPIs. A filter control
        # belongs where the eye looks for it -- above the numbers it filters --
        # and at the KPI height (120px of mostly-empty card) rather than the
        # chart height, so a row of them does not push the board down.
        slicer_count = len(self.bridge.get("slicers", []) or [])
        if slicer_count:
            slicer_map = tile_geometry.auto_place(
                [str(i) for i in range(slicer_count)], slicer_map,
                gap_px=gap, h=max(tile_geometry.MIN_H, 180.0),
                floor_px=floor)
            floor = tile_geometry.canvas_height(slicer_map, gap)
        else:
            slicer_map = {}
        kpi_map = tile_geometry.auto_place(
            [str(i) for i in range(len(kpis))], kpi_map, gap_px=gap,
            h=max(tile_geometry.MIN_H, 120.0), floor_px=floor)
        chart_map = tile_geometry.auto_place(
            chart_uids, chart_map, gap_px=gap, h=self._card_height(),
            floor_px=(tile_geometry.canvas_height(kpi_map, gap) if kpi_map
                      else floor))
        chart_map.update(title_map)
        self._slicer_geom = tile_geometry.clamp_all(slicer_map)
        return (tile_geometry.clamp_all(chart_map),
                tile_geometry.clamp_all(kpi_map))

    def _persist_geometry(self, chart_map: dict, kpi_map: dict,
                          cols: int = 2) -> None:
        """Store both maps in one blob and refresh the board."""
        # Slicer tiles join the same blob, but under a namespaced key. KPIs are
        # keyed by a BARE index ("0", "1"), so a slicer keyed the same way
        # would collide with KPI 0 and one would silently overwrite the
        # other's placement -- which is why chart uids and the reserved
        # __title__ key are namespaced too.
        slicer_map = getattr(self, "_slicer_geom", {}) or {}
        namespaced = {f"slicer:{k}": v for k, v in slicer_map.items()}
        # The "free" marker rides along so the next load knows this board was
        # saved on the free canvas and never runs the legacy grid migration on
        # it (which is what reset a dragged full-width chart).
        self.bridge.set("tile_geometry", {
            **chart_map, **kpi_map, **namespaced, _MODE_KEY: "free"})
        # Recorded so a session saved now can still be opened by an older build
        # (which expects a column count) even though the canvas no longer uses it.
        self.bridge.set("grid_cols_n", cols or 2)
        self._schedule_regen()

    # -- KPI management -------------------------------------------------

    def _refresh_kpis(self) -> None:
        """KPI cards are rendered by the report itself now.

        This used to build a row of KpiCard widgets duplicating the exporter's
        markup -- a second renderer to keep in step with the first, which is how
        the live card and the exported card came to disagree about value sizes
        and stale markers. The KPI list now only needs the board rebuilt.
        """
        self._schedule_regen()

    # -- Canvas bridge: geometry -------------------------------------------

    def _on_canvas_geometry(self, uid: str, geom: dict) -> None:
        """A card was dragged or resized on the canvas.

        The proposal is merged into the stored map, then the WHOLE arrangement
        is re-clamped and re-packed before anything is persisted or rendered.
        That is the point of keeping the authority in Python: the overlay can be
        wrong, or from a stale document, and the result is still a legal layout.
        """
        charts = self.bridge.get("charts", [])
        kpis = self.bridge.get("kpis", [])
        cols = int(self.bridge.get("grid_cols_n", 2))
        chart_map, kpi_map = self._current_geometry(charts, kpis, cols)

        # A slicer is its OWN partition, checked before the KPI/chart split.
        # "slicer:0" is neither a KPI key nor a chart uid, so the old code filed
        # it in chart_map -- where it failed the "is this chart still here"
        # filter, never reached _slicer_geom, and vanished on the next render.
        # The drag looked like it worked (the overlay moved the card) and then
        # sprang back, which is exactly the reported bug. _current_geometry has
        # already refreshed self._slicer_geom from the stored blob above, so the
        # proposal joins THAT map rather than a stale copy of it.
        slicer_key = _slicer_key(uid)
        if slicer_key is not None:
            self._slicer_geom[slicer_key] = tile_geometry.clamp(geom)
            self._persist_geometry(chart_map, kpi_map, cols)
            return

        kpi_uid = _kpi_key(uid)
        target = kpi_map if kpi_uid is not None else chart_map
        # Keyed by the PERSISTED spelling, so a KPI dragged as "kpi:0" lands on
        # the same tile the exporter and the next render read back.
        key = kpi_uid if kpi_uid is not None else str(uid)
        # Stored through the same clamp the exporter uses. NOT int()-coerced:
        # free-form positions are fractions of the canvas width, so rounding one
        # to an int would quantise every card's left edge and width to whole
        # columns -- the grid behaviour all over again, and the reason a dragged
        # card used to spring back to a cell.
        target[key] = tile_geometry.clamp(geom)

        # Bridge order follows the visual arrangement, so the chart list, the
        # reorder buttons and the exported order can never disagree.
        #
        # The title card is excluded from that order: it is in chart_map now, so
        # leaving it in would make the "is every card still here" test fail for
        # EVERY drag -- no chart is ever renamed to "__title__" -- and the chart
        # order would silently stop tracking the board the moment the title was
        # moved.
        if kpi_uid is None:
            ordered = {u: g for u, g in chart_map.items() if u != _TITLE_UID}
            ordered[key] = target[key]
            order = tile_geometry.visual_order(tile_geometry.clamp_all(ordered))
            by_uid = {c[0]: c for c in charts}
            if all(u in by_uid for u in order):
                wanted = [by_uid[u] for u in order]
                # Write ONLY when the order actually moved. Emitting
                # charts_changed re-runs every listener (tab rebuild, geometry
                # prune, a second scheduled preview build) and it used to fire
                # on EVERY drag frame -- one of the two rebuilds racing the
                # drag the user was still performing.
                if [c[0] for c in wanted] != [c[0] for c in charts]:
                    self.bridge.set("charts", wanted)
        # No pack(). Overlap is LEGAL on a free canvas -- a card may be dropped on
        # top of another, exactly as a window can. Pushing the offender down
        # instead was what made every drag look like it had been ignored.
        self._persist_geometry(chart_map, kpi_map, cols)

    def _on_canvas_notes(self, uid: str, text: str) -> None:
        """Notes edited in place on the canvas; committed on blur.

        The commit schedules a debounced regen rather than nothing, because the
        notes area is part of the generated document: storing the text alone
        leaves the canvas showing one thing while Preview, a theme change or an
        export is rebuilt from the stored value. Debounced, so a blur that
        changed nothing costs one build.
        """
        if not uid:
            return
        self.bridge.set_note(uid, text)
        self._schedule_regen()

    def _on_canvas_slicer(self, index: str, payload: str) -> None:
        """A slicer control was used; fold its selection into the slicer list.

        The payload is the WHOLE index's state -- every checked box, the
        low+high pair together -- so it REPLACES the selection rather than
        merging into it. Applying one box as a delta is how a click read as
        "filter to just this" in the first prototype.
        """
        import json

        from services.slicer_service import apply_selection
        try:
            idx = int(str(index))
        except (TypeError, ValueError):
            return
        slicers = list(self.bridge.get("slicers", []) or [])
        if not (0 <= idx < len(slicers)):
            return
        try:
            data = json.loads(payload or "{}")
        except (ValueError, TypeError):
            log.debug("_on_canvas_slicer: bad payload %r", payload)
            return
        if not isinstance(data, dict):
            return
        updated = dict(slicers[idx])
        for role, raw in data.items():
            updated = apply_selection(updated, str(role), raw)
        slicers[idx] = updated
        self.bridge.set("slicers", slicers)
        self._refresh_slicer_count()
        # Continuous controls (search box, slider drag) already debounce on
        # the JS side; the discrete ones are single events. Either way a
        # selection change is not a drag, so the fast timer is enough.
        self._schedule_regen()

    def _on_canvas_action(self, uid: str, action: str) -> None:
        """A per-card button on the canvas was pressed.

        The buttons live inside the report now, so the page owns what they mean.
        """
        if not uid:
            return
        # The title card carries no action bar at all (see report_editor), so it
        # should never reach here -- but if it ever did, falling through would run
        # the CHART handlers against a uid that is not a chart, so "delete" would
        # try to remove a chart called "__title__".
        if uid == _TITLE_UID:
            return
        # Slicer cards carry their index in the uid ("slicer:0"), exactly as
        # KPI cards do, and they get their OWN dispatch for the same reason:
        # the slicer toolbar offers edit/move/remove/clear, none of which are
        # chart operations. Handling them here and RETURNING unconditionally is
        # the point -- an unrecognised slicer action must not fall through to
        # the chart handlers, which is how these buttons were dead before.
        slicer_key = _slicer_key(uid)
        if slicer_key is not None:
            index = int(slicer_key)
            if action == "slicer-delete":
                self._on_remove_slicer(index)
            elif action == "slicer-edit":
                self._on_edit_slicer(index)
            elif action == "slicer-up":
                self._on_move_slicer_up(index)
            elif action == "slicer-down":
                self._on_move_slicer_down(index)
            elif action == "slicer-clear":
                self._on_clear_slicer(index)
            else:
                log.debug("unhandled slicer action %r", action)
            return
        # KPI cards carry their index in the uid ("kpi:0"), so their actions are
        # dispatched to the KPI handlers by index rather than to the chart ones.
        kpi_uid = _kpi_key(uid)
        if kpi_uid is not None:
            index = int(kpi_uid)
            if action == "kpi-delete":
                self._on_remove_kpi(index)
            elif action == "kpi-up":
                self._on_move_kpi_up(index)
            elif action == "kpi-down":
                self._on_move_kpi_down(index)
            elif action == "kpi-edit":
                self._on_edit_kpi(index)
            return
        if action == "delete":
            self._on_delete_chart(uid)
        elif action == "slicer-add":
            # The chart bar's "add slicer for this chart" button: same
            # pre-scoped dialog the Analysis page's card opens, so the user
            # never has to leave the board to filter the chart they are
            # looking at. Handled here (not routed to Analysis) precisely so
            # it does not navigate away.
            self._on_add_chart_slicer(uid)
        elif action == "open-analysis":
            self._open_chart_in_analysis(uid)
        elif action in ("up", "down", "style", "notes"):
            # Stale document: the toolbar no longer emits these (see
            # report_editor), but a cached preview could. The chart's home
            # for all three is the Analysis page, so route there instead of
            # silently dropping the click.
            self._open_chart_in_analysis(uid)

    def _open_chart_style(self, uid: str) -> None:
        """Open the per-chart style controls for *uid*.

        Same panel the Analysis page uses, built here rather than reached
        through a ChartCard: the dashboard no longer owns Qt chart widgets, so
        there is no card left to hang the dialog off.
        """
        from chart_style_panel import ChartStylePanel
        meta = dict(self.bridge.get_chart_meta(uid))
        style = dict(meta.get("display_options", {}))
        title = next((c[1] for c in self.bridge.get("charts", []) if c[0] == uid),
                     "Chart")
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Style - {title}")
        dialog.setMinimumWidth(340)
        box = QVBoxLayout(dialog)
        panel = ChartStylePanel(chart_type=self.bridge.get(f"chart_type_{uid}", ""),
                                style=style)

        # Style ticks debounce here (150ms), NOT per tick: the dialog's
        # sliders stream while dragged, and each tick used to cost one meta
        # write plus a scheduled rebuild -- the "style dialog freezes the
        # app" report. flush_pending_style() applies the LAST style once.
        def apply(new_style: dict) -> None:
            self._pending_style = (uid, dict(new_style))
            self._style_debounce.start()

        panel.style_changed.connect(apply)
        box.addWidget(panel)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.close)
        box.addWidget(buttons)
        dialog.exec()

    def _flush_pending_style(self) -> None:
        """Apply the queued per-chart style (no-op when nothing queued)."""
        pending = self._pending_style
        self._pending_style = None
        try:
            self._style_debounce.stop()
        except Exception:
            pass
        if not pending:
            return
        uid, style = pending
        self.bridge.set_chart_meta(uid, display_options=style)
        self._schedule_regen()

    def flush_pending_style(self) -> None:
        """Apply the queued style immediately. Tests only; idempotent."""
        self._flush_pending_style()

    def _open_chart_in_analysis(self, uid: str) -> None:
        """Open *uid* in the Analysis page with its settings form bound.

        The dashboard owns no Qt chart widgets, so the Analysis page is the
        only surface that can edit a chart; the canvas pencil just gets the
        user there. The uid travels on the bridge (a "pending edit" key the
        Analysis page consumes in showEvent/refresh) because pages only talk
        through navigate_requested + bridge state -- there is no direct
        page-to-page call.
        """
        if not uid or uid == _TITLE_UID:
            return
        if self.bridge.get(f"chart_type_{uid}", ""):
            self.bridge.set("_pending_edit_chart", uid)
        self.navigate_requested.emit("analysis")

    def _focus_notes(self, uid: str) -> None:
        """Put the caret in a card's notes box inside the canvas.

        Done with a querySelector + focus() rather than a Qt widget because the
        notes editor is now the report's own contenteditable.
        """
        escaped = str(uid).replace("\\", "\\\\").replace('"', '\\"')
        self.report_canvas.run_js(
            "(function(){var el=document.querySelector("
            f"'.chart-card[data-uid=\"{escaped}\"] .notes');"
            "if(el){el.focus();"
            "var r=document.createRange();r.selectNodeContents(el);"
            "var s=getSelection();s.removeAllRanges();s.addRange(r);}})();")

    def _on_mode_toggled(self, preview: bool) -> None:
        """Edit board <-> Preview.

        preview=True HIDES the editing chrome. Nothing is re-rendered: the canvas
        toggles one CSS class on the report document, so what Preview shows is
        literally the exported file.
        """
        self.report_canvas.set_editing(not preview)
        self.btn_tidy.setEnabled(not preview)

    def _on_add_kpi(self) -> None:
        """Open the type-driven KPI form and append the card it produces.

        The old dialog was three fixed fields (column / aggregation / label), so
        only Sum, Average, Count, Min and Max were reachable and a card could
        not be edited once added. Both are handled by ``KpiDialog`` +
        ``kpi_service``; this method is now just the dialog host.
        """
        df = self.bridge.df
        if df is None or df.empty:
            QMessageBox.warning(self, "No Data",
                                "Load a dataset before adding KPIs.")
            return
        dialog = KpiDialog(df, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        kpi = dialog.result_kpi()
        if str(kpi.get("value", "")).startswith("Err:"):
            # Adding a card that is already broken helps nobody; the form's own
            # preview showed the same error before OK was pressed.
            QMessageBox.warning(self, "KPI Error",
                                f"{kpi['value']}\n\nPick a different column "
                                "or type.")
            return
        kpis = list(self.bridge.get("kpis", []))
        kpis.append(kpi)
        self.bridge.set("kpis", kpis)
        self._refresh_kpis()

    def _on_edit_kpi(self, index: int) -> None:
        """Re-open an existing card on the form and store the result.

        A card with no ``_recipe`` (saved by the old dialog) is recomputed from
        the current dataset using a type inferred from its label, so editing
        upgrades it to a proper card rather than resetting it.
        """
        kpis = list(self.bridge.get("kpis", []))
        if not (0 <= index < len(kpis)):
            return
        df = self.bridge.df
        if df is None or df.empty:
            QMessageBox.warning(self, "No Data",
                                "Load a dataset before editing KPIs.")
            return
        dialog = KpiDialog(df, parent=self, existing=kpis[index])
        if dialog.exec() != QDialog.Accepted:
            return
        updated = dialog.result_kpi()
        if str(updated.get("value", "")).startswith("Err:"):
            QMessageBox.warning(self, "KPI Error",
                                f"{updated['value']}\n\nPick a different "
                                "column or type.")
            return
        kpis[index] = updated
        self.bridge.set("kpis", kpis)
        self._refresh_kpis()


    def _on_remove_kpi(self, index: int) -> None:
        kpis = list(self.bridge.get("kpis", []))
        if 0 <= index < len(kpis):
            kpis.pop(index)
            self.bridge.set("kpis", kpis)
            self._refresh_kpis()

    def _on_move_kpi_up(self, index: int) -> None:
        """Move a KPI earlier via its arrow button.

        The lower bound is ``0 < index``, NOT ``0 <= index``: index 0 has no
        neighbour above it, and allowing it makes ``kpis[index - 1]`` wrap to
        ``kpis[-1]`` -- so the "move up" on the FIRST card silently swaps it
        with the LAST one.
        """
        kpis = list(self.bridge.get("kpis", []))
        if 0 < index < len(kpis):
            kpis[index - 1], kpis[index] = kpis[index], kpis[index - 1]
            self.bridge.set("kpis", kpis)
            self._swap_kpi_tiles(index - 1, index)
            self._refresh_kpis()

    def _on_move_kpi_down(self, index: int) -> None:
        kpis = list(self.bridge.get("kpis", []))
        if 0 <= index < len(kpis) - 1:
            kpis[index], kpis[index + 1] = kpis[index + 1], kpis[index]
            self.bridge.set("kpis", kpis)
            self._swap_kpi_tiles(index, index + 1)
            self._refresh_kpis()

    def _swap_kpi_tiles(self, i: int, j: int) -> None:
        """Trade the placements of two KPI cards.

        Reordering the KPI LIST alone is not enough any more. KPI cards are
        absolutely positioned now, so which card sits where is decided by its
        geometry, not by its position in the list -- reordering the list and
        leaving the tiles alone makes the arrow buttons do nothing visible at
        all, which is the same "the control is decorative" bug as the handles.
        """
        cols = int(self.bridge.get("grid_cols_n", 2))
        chart_map, kpi_map = self._current_geometry(
            self.bridge.get("charts", []), self.bridge.get("kpis", []), cols)
        a, b = str(i), str(j)
        if a not in kpi_map or b not in kpi_map:
            self._schedule_regen()
            return
        kpi_map[a], kpi_map[b] = kpi_map[b], kpi_map[a]
        self._persist_geometry(chart_map, kpi_map, cols)

    # -- Slicer management -------------------------------------------------

    def _set_slicers(self, slicers: list) -> None:
        """Store the slicer list and rebuild the board.

        One write plus one debounced regen, exactly as KPIs behave, so editing
        a slicer is a single visible change rather than a flash per mutation.
        """
        self.bridge.set("slicers", list(slicers))
        self._refresh_slicer_count()
        self._schedule_regen()

    def _refresh_slicer_count(self) -> None:
        """Keep the panel's count honest.

        Counted from ``slicer_is_active`` rather than from the list length: a
        slicer that is present but has nothing ticked is not filtering, and
        saying "2 slicers" when neither is doing anything would imply the board
        is already narrowed.
        """
        if not getattr(self, "slicer_count", None):
            return
        from services.slicer_service import slicer_is_active

        slicers = self.bridge.get("slicers", []) or []
        active = sum(1 for s in slicers
                     if isinstance(s, dict) and slicer_is_active(s))
        if not slicers:
            self.slicer_count.setText("No slicers yet.")
        elif active:
            self.slicer_count.setText(
                f"{len(slicers)} slicer(s) — {active} filtering")
        else:
            self.slicer_count.setText(
                f"{len(slicers)} slicer(s) — none filtering")

    def _on_add_slicer(self) -> None:
        """Open the slicer form and append the card it produces."""
        df = self.bridge.df
        if df is None or df.empty:
            QMessageBox.warning(self, "No Data",
                                "Load a dataset before adding slicers.")
            return
        from desktop.widgets.slicer_dialog import SlicerDialog

        charts = self.bridge.get("charts", [])
        dialog = SlicerDialog(
            df, parent=self, charts=charts,
            # Recipes drive the dialog's live "Affects N of M charts" line --
            # computed with the same affected_charts() the recompute path
            # uses, so the promise matches the behaviour.
            recipes={c[0]: self.bridge.get_chart_meta(c[0])
                     for c in charts})
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            slicer = dialog.result_slicer()
        except ValueError as exc:
            # make_slicer refuses an unknown widget and a chart-scoped slicer
            # with no target. Both are form errors, so they are shown as such
            # rather than dropped: a swallowed slicer looks like the button did
            # nothing, which is the very bug this feature is here to fix.
            QMessageBox.warning(self, "Slicer", str(exc))
            return
        slicers = list(self.bridge.get("slicers", []) or [])
        slicers.append(slicer)
        self._set_slicers(slicers)

    def _on_add_chart_slicer(self, uid: str) -> None:
        """Open the slicer form pre-scoped to a single chart.

        The per-chart entry point, reachable from the chart bar's ``slicer-add``
        button. It is the same dialog ``_on_add_slicer`` opens, but born on
        "One chart" with this chart already selected -- so the scope decision is
        made right where the user is looking at the chart. Mirrors the Analysis
        page's identically named handler; both store through the bridge, so
        whichever surface creates the slicer, the board rebuilds the same way.
        """
        df = self.bridge.df
        if df is None or df.empty:
            QMessageBox.warning(self, "No Data",
                                "Load a dataset before adding slicers.")
            return
        from desktop.widgets.slicer_dialog import SlicerDialog

        charts = self.bridge.get("charts", [])
        # Recipes drive the dialog's live "Affects N of M charts" line with the
        # same affected_charts() the recompute path uses, so the hint cannot
        # drift from behaviour. Passed even though the scope is fixed to this
        # chart, so the dialog has no chart-type-specific blind spot.
        recipes = {c[0]: self.bridge.get_chart_meta(c[0]) for c in charts}
        dialog = SlicerDialog(df, parent=self, charts=charts, recipes=recipes,
                              preset_scope="One chart", preset_target=uid)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            slicer = dialog.result_slicer()
        except ValueError as exc:
            # make_slicer refuses a chart-scoped slicer with no target; shown as
            # the form error it is rather than swallowed, because a dropped
            # slicer looks exactly like a dead button.
            QMessageBox.warning(self, "Slicer", str(exc))
            return
        slicers = list(self.bridge.get("slicers", []) or [])
        slicers.append(slicer)
        self._set_slicers(slicers)

    def _on_edit_slicer(self, index: int) -> None:
        """Re-open an existing slicer and store the result."""
        slicers = list(self.bridge.get("slicers", []) or [])
        if not (0 <= index < len(slicers)):
            return
        df = self.bridge.df
        if df is None or df.empty:
            QMessageBox.warning(self, "No Data",
                                "Load a dataset before editing slicers.")
            return
        from desktop.widgets.slicer_dialog import SlicerDialog

        charts = self.bridge.get("charts", [])
        dialog = SlicerDialog(
            df, parent=self, charts=charts,
            recipes={c[0]: self.bridge.get_chart_meta(c[0])
                     for c in charts},
            existing=slicers[index])
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            updated = dialog.result_slicer()
        except ValueError as exc:
            QMessageBox.warning(self, "Slicer", str(exc))
            return
        # The uid is preserved so anything already holding a reference to this
        # slicer -- and the card's identity across a re-render -- survives the
        # edit. A fresh uid would read as delete-then-add to every observer.
        updated["uid"] = slicers[index].get("uid") or updated["uid"]
        slicers[index] = updated
        self._set_slicers(slicers)

    def _on_remove_slicer(self, index: int) -> None:
        """Delete a slicer.

        Its selection goes with it, by design: the card is gone, so keeping
        the rows it excluded would leave a filter the user can no longer see
        or clear.
        """
        slicers = list(self.bridge.get("slicers", []) or [])
        if 0 <= index < len(slicers):
            slicers.pop(index)
            self._set_slicers(slicers)

    def _on_clear_slicer(self, index: int) -> None:
        """Empty a slicer's SELECTION without removing the card.

        Clearing empties the selection, not the slicer -- the control stays on
        the board so the user can pick again. Zeroing the spec rather than
        deleting the card is the difference between "nothing is filtering" and
        "no rows match", and only the first is what they asked for.
        """
        slicers = list(self.bridge.get("slicers", []) or [])
        if not (0 <= index < len(slicers)):
            return
        slicer = dict(slicers[index])
        spec = dict(slicer.get("spec") or {})
        kind = str(spec.get("kind") or slicer.get("kind") or "")
        if kind in ("in", "not_in"):
            spec["values"] = []
        elif kind == "range":
            # Cleared back to "no constraint", so the slider returns to its
            # full span rather than to a zero-width one.
            spec["low"] = None
            spec["high"] = None
        elif kind == "search":
            spec["value"] = None
        slicer["spec"] = spec
        slicers[index] = slicer
        self._set_slicers(slicers)

    def _on_move_slicer_up(self, index: int) -> None:
        """Move a slicer earlier -- list AND tile together."""
        slicers = list(self.bridge.get("slicers", []) or [])
        if 0 < index < len(slicers):
            slicers[index - 1], slicers[index] = slicers[index], slicers[index - 1]
            self._set_slicers(slicers)
            self._swap_slicer_tiles(index - 1, index)

    def _on_move_slicer_down(self, index: int) -> None:
        slicers = list(self.bridge.get("slicers", []) or [])
        if 0 <= index < len(slicers) - 1:
            slicers[index], slicers[index + 1] = slicers[index + 1], slicers[index]
            self._set_slicers(slicers)
            self._swap_slicer_tiles(index, index + 1)

    def _swap_slicer_tiles(self, i: int, j: int) -> None:
        """Trade the placements of two slicer cards.

        Reordering the list alone does nothing visible: slicer tiles are
        absolutely positioned and keyed by index, so which card sits where is
        decided by the geometry rather than by the list order. Leaving the
        tiles alone makes the arrow buttons appear broken -- the same "the
        control is decorative" bug ``_swap_kpi_tiles`` documents for KPIs.
        """
        cols = int(self.bridge.get("grid_cols_n", 2))
        chart_map, kpi_map = self._current_geometry(
            self.bridge.get("charts", []), self.bridge.get("kpis", []), cols)
        geom = getattr(self, "_slicer_geom", {}) or {}
        a, b = str(i), str(j)
        if a not in geom or b not in geom:
            # Not placed yet. auto_place arranges them on this very render, so
            # there is nothing to trade and nothing that could fail.
            self._persist_geometry(chart_map, kpi_map, cols)
            return
        geom[a], geom[b] = geom[b], geom[a]
        self._slicer_geom = geom
        self._persist_geometry(chart_map, kpi_map, cols)

    # -- Slots ---------------------------------------------------------------

    def _on_back(self) -> None:
        """Navigate back to the previous page."""
        if self._viewing_saved:
            self.bridge.pop("view_session_id", None)
            self.bridge.pop("_view_charts", None)
            self.navigate_requested.emit("home")
        else:
            self.navigate_requested.emit("analysis")

    # -- Slicers: applying the selection ---------------------------------

    def _chart_recipe(self, uid: str) -> dict:
        """The saved recipe for one chart, or ``{}`` when there is none.

        ``run_chart_recipe`` needs ``chart_type``, and the analysis page keeps
        that in its own per-chart bridge key rather than inside meta -- so the
        two are joined here once, rather than at every call site that wants to
        rebuild a figure.
        """
        meta = self.bridge.get_chart_meta(uid)
        if not isinstance(meta, dict) or not meta.get("config"):
            return {}
        recipe = dict(meta)
        # Injected, NOT read from meta: analysis_page stores meta as
        # {config, layout, typography} with no uid in it, and _rebuild_one
        # refuses a recipe without one (it is what keeps the rebuilt figure
        # attached to the card's geometry and notes). Without this line every
        # rebuild returned None and every affected chart silently kept its
        # stale, unfiltered figure -- a slicer that appears to do nothing.
        recipe["uid"] = uid
        recipe.setdefault("chart_type", self.bridge.get(f"chart_type_{uid}", ""))
        return recipe

    @staticmethod
    def _empty_fig():
        """An honest empty state for a filter that removes every row.

        Keeping the previous figure instead would show a chart of data that is
        no longer under the selection -- the most misleading thing a slicer
        could do, and a silent one.
        """
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.update_layout(
            xaxis={"visible": False}, yaxis={"visible": False},
            annotations=[{"text": "No data<br>for this selection",
                          "xref": "paper", "yref": "paper",
                          "showarrow": False,
                          "font": {"size": 13, "color": "#8b93b5"}}],
            margin=dict(l=8, r=8, t=8, b=8),
            height=240, plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        return fig

    def _apply_slicers(self, charts: list, slicers: list) -> list:
        """Re-run only the charts a slicer touches, against the filtered rows.

        Returns the list unchanged when nothing is active, which is the common
        case: a freshly added slicer has nothing ticked, and treating that as
        "match no rows" would blank the board the moment it is created.

        The rebuild is restricted to ``affected_charts`` -- the intersection of
        slicer columns against the chartspec. Re-running all thirteen analysis
        types on every click is what would make a slicer feel heavy on a large
        dataset, and most of that work would be thrown away.
        """
        from services.chart_service import run_chart_recipe
        from services.slicer_service import (
            active_slicers, affected_charts, filter_frame,
        )

        if not charts or not active_slicers(slicers):
            return list(charts)
        df = self.bridge.df
        if df is None or df.empty:
            return list(charts)
        try:
            filtered = filter_frame(df, slicers)
        except Exception:
            log.warning("_apply_slicers: filter failed; charts left unfiltered",
                        exc_info=True)
            return list(charts)

        recipes = {c[0]: r for c in charts
                   for r in (self._chart_recipe(c[0]),) if r}
        hit = set(affected_charts(recipes, slicers))
        if not hit:
            return list(charts)

        out = []
        for chart in charts:
            uid = chart[0]
            if uid not in hit:
                out.append(chart)
                continue
            rebuilt = run_chart_recipe(recipes[uid], filtered)
            if rebuilt:
                # The STORED title is kept: ``rebuilt`` carries the runner's own
                # default, so taking it would silently discard a title the user
                # set in Layout.
                out.append((uid, chart[1], rebuilt[2], *chart[3:]))
            elif filtered.empty:
                out.append((uid, chart[1], self._empty_fig(), *chart[3:]))
            else:
                # None means either "no rows" or "recipe could not be rebuilt".
                # Only the first is a fact about the data. Reporting a fault as
                # "no data" would send the user hunting for a filter that is
                # not the problem, so the previous figure stays and the fault
                # goes to the log.
                log.warning("_apply_slicers: %r could not be rebuilt under the "
                            "filter; keeping the previous figure", uid)
                out.append(chart)
        return out

    @staticmethod
    def _slicer_values(slicers: list, df) -> dict:
        """Candidate values per list/dropdown slicer: ``{index: [values]}``.

        Only those two kinds need it. A slider carries its own bounds and a
        search box takes whatever is typed, so asking the dataset for the
        distinct values of a continuous column would be work with no consumer
        -- on a large dataset, exactly the kind of work worth skipping.
        """
        from services.filter_service import distinct_values

        out: dict = {}
        if df is None or df.empty:
            return out
        for i, s in enumerate(slicers or []):
            if not isinstance(s, dict):
                continue
            if str(s.get("widget") or "").lower() not in ("list", "dropdown"):
                continue
            try:
                out[str(i)] = distinct_values(df, str(s.get("column") or ""))
            except Exception:
                log.debug("_slicer_values: index %d failed", i, exc_info=True)
        return out

    def _export_inputs(self) -> dict:
        """Everything both export formats need, read once.

        The saved file and the live canvas are the same report, so they read the
        same style dict and the same arrangement. That is why a session can be
        exported and then edited on: there is nothing in between to differ.
        """
        charts = self.bridge.get("charts", [])
        kpis = self.bridge.get("kpis", [])
        slicers = list(self.bridge.get("slicers", []) or [])
        cols = int(self.bridge.get("grid_cols_n", 2))
        # Geometry is computed from the UNFILTERED list: filtering replaces
        # figures, never uids, but reading it before the swap keeps the two
        # questions separate -- where cards are, and what they draw.
        tile_geom, kpi_geom = self._current_geometry(charts, kpis, cols)
        meta_map = {uid: self.bridge.get_chart_meta(uid) for uid, _, _ in charts}
        notes = {uid: self.bridge.get_note(uid) for uid, _, _ in charts}
        # The board and the saved file are rebuilt from the SAME filtered
        # charts, which is the whole reason the preview and the export cannot
        # disagree about what a slicer selected.
        charts = self._apply_slicers(charts, slicers)
        # KEPT BARE, exactly like kpi_geometry: generate_html_report reads
        # slicer_geometry by index ("0", "1" ...) and namespaces the entries
        # itself as it merges them into the board's own map. Passing
        # "slicer:0" here made both lookups miss, so every stored placement was
        # discarded and the card fell back to auto-place -- a drag that appears
        # to work and then springs back on the next render.
        slicer_geom = dict(getattr(self, "_slicer_geom", {}) or {})
        # Column-pruned dataset for the in-browser slicer engine of the SAVED
        # file. Built here because this is the only layer that has the
        # DataFrame -- build_export_report itself is deliberately headless.
        # Only worth building when there ARE slicers: without them
        # generate_html_report emits no engine and would drop the payload
        # again. Failures degrade to a static export, never an export error.
        report_payload = None
        if slicers:
            try:
                from modules.report_data import build_report_payload
                report_payload = build_report_payload(
                    self.bridge.df, meta_map, slicers)
            except Exception:
                log.debug("report payload not built; exporting without live "
                          "slicers", exc_info=True)
        return {
            "charts": charts,
            "export_charts": build_export_charts(
                charts, meta_map=meta_map, notes=notes),
            "session_name": (self.session_name.text().strip()
                             or "Lytrize Report"),
            "orientation": ("landscape"
                            if self.layout_mode.currentText() == "Landscape"
                            else "portrait"),
            "state": self._export_style_dict(),
            "kpis": kpis,
            "dashboard_title": self.dash_title.text().strip(),
            "grid_cols_n": cols,
            "tile_geometry": tile_geom,
            "kpi_geometry": kpi_geom,
            "slicers": slicers,
            "slicer_geometry": slicer_geom,
            "slicer_values": self._slicer_values(slicers, self.bridge.df),
            # Specs stay None on purpose: generate_html_report derives them
            # from the 6-tuple chart meta itself, so there is one derivation
            # path rather than two that could disagree.
            "report_payload": report_payload,
            # The canvas edits in free-form units, so preview and export both
            # render the free canvas. There is no grid left to fall back to: a
            # session saved before the free canvas is CONVERTED on load by
            # _current_geometry, not rendered through the old cell layout.
            "geometry_mode": "free",
        }

    def _on_export(self) -> None:
        """Export the dashboard as a standalone HTML file."""
        charts = self.bridge.get("charts", [])
        if not charts:
            QMessageBox.warning(self, "No Charts", "No charts to export.")
            return          # was missing: the export ran anyway, after warning

        # Same two-stage pipeline as the preview: input assembly (recipe
        # re-runs, payload JSON) runs off the UI thread, then the HTML
        # build. _start_export is unchanged -- it just receives its dict
        # from the worker instead of a synchronous call.
        self._export_format = "html"
        self._assemble_export_inputs("html")

    def _assemble_export_inputs(self, fmt: str) -> None:
        """Snapshot cheap state, then assemble heavy inputs off-thread."""
        for btn in (self.btn_export, self.btn_export_png):
            btn.setEnabled(False)
        self.btn_export.setText("⏳ Working…")
        self.btn_export_png.setText("⏳ Rendering…")
        snap = self._snapshot_inputs()
        self._regen_token = snap["token"]
        self._pending_export_snap = snap
        self._pending_export_fmt = fmt
        from desktop.workers import ReportInputsWorker
        worker = ReportInputsWorker(snap["token"], snap, parent=self)
        worker.ready.connect(self._on_export_inputs_ready)
        worker.failed.connect(
            lambda _t, err: self._on_export_failed(err))
        self._export_inputs_worker = worker
        worker.start()

    def _on_export_inputs_ready(self, token: int, partial: dict) -> None:
        """Stage 1 done: merge and hand the full dict to _start_export."""
        if token != self._regen_token:
            self._reset_export_buttons()
            return
        snap = getattr(self, "_pending_export_snap", None) or {}
        fmt = getattr(self, "_pending_export_fmt", "html")
        d = dict(snap)
        d.update(partial)
        # Buttons were already disabled by _assemble_export_inputs;
        # _start_export disables again (idempotent) and launches the build.
        self._start_export(d, fmt=fmt)

    def _start_export(self, d: dict, fmt: str) -> None:
        """Kick off an export for *fmt* ("html" or "png").

        One path for both formats: they build the identical document and differ
        only in what happens once the HTML comes back. That is deliberate -- it
        is what stops "the PNG looks different from the HTML" from ever being a
        bug, because there is only one document.
        """
        for btn in (self.btn_export, self.btn_export_png):
            btn.setEnabled(False)
        self.btn_export.setText("⏳ Working…")
        self.btn_export_png.setText("⏳ Rendering…")
        self._export_format = fmt
        self._export_worker = ExportWorker(
            d["export_charts"], d["session_name"], d["state"],
            orientation=d["orientation"],
            kpis=d["kpis"],
            dashboard_title=d["dashboard_title"],
            grid_cols_n=d["grid_cols_n"],
            tile_geometry=d["tile_geometry"],
            kpi_geometry=d["kpi_geometry"],
            slicers=d["slicers"],
            slicer_geometry=d["slicer_geometry"],
            slicer_values=d["slicer_values"],
            report_payload=d.get("report_payload"),
            geometry_mode=d["geometry_mode"],
            parent=self,
        )
        self._export_worker.finished_ok.connect(self._on_export_finished)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_png(self) -> None:
        """Export the dashboard as a full-page PNG.

        Deliberately NOT a screenshot of the live Qt panel. That would have to
        composite native widgets together with embedded QWebEngineView charts in
        one grab, which has a well-documented history of not reliably capturing
        GPU-accelerated WebEngine content on some platforms. Rendering the
        already-correct report in a dedicated, single-purpose offscreen webview
        sidesteps that entirely.
        """
        if not self.bridge.get("charts", []):
            QMessageBox.warning(self, "No Charts", "No charts to export.")
            return          # was missing: the export ran anyway, after warning

        self._export_format = "png"
        self._assemble_export_inputs("png")

    def _on_export_finished(self, html: str, safe_file: str) -> None:
        """Handle a finished build.

        For PNG this only hands off to the renderer and RETURNS. It used to fall
        through, so "Export PNG" also popped a second dialog offering to save an
        .html file the user never asked for.
        """
        if getattr(self, "_export_format", "html") == "png":
            self._render_html_to_png(html, safe_file)
            return

        self._reset_export_buttons()
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Dashboard", str(Path.home() / f"{safe_file}.html"),
            "HTML Files (*.html)",
        )
        if not file_path:
            return
        try:
            Path(file_path).write_text(html, encoding="utf-8")
            QMessageBox.information(self, "Export Complete",
                                    f"Dashboard saved to:\n{file_path}")
        except Exception as exc:
            log.error("Export write failed: %s", exc)
            QMessageBox.critical(self, "Export Error", str(exc))

    def _reset_export_buttons(self) -> None:
        """Put both export buttons back the way they started."""
        self.btn_export.setEnabled(True)
        self.btn_export.setText("⬇️ Export HTML")
        self.btn_export_png.setEnabled(True)
        self.btn_export_png.setText("📷 Export PNG")

    def _render_html_to_png(self, html: str, safe_file: str) -> None:
        """Render the exported HTML report to a full-page PNG using a
        dedicated, offscreen QWebEngineView.

        Flow: write HTML to a temp file (setHtml() has a real ~2MB limit
        that the CDN/inline-JS chart rewrite already ran into once in
        this codebase -- see item A -- a multi-chart report can easily
        exceed that), load it, measure the page's actual rendered height
        via JS once loaded, resize the view to that full height, give
        Plotly's own async chart rendering a moment to finish (page
        loadFinished fires before Plotly.newPlot() completes its work),
        then grab() the view and save.
        """
        import tempfile
        from PySide6.QtCore import QUrl, QTimer, QSize, Qt
        from PySide6.QtWidgets import QWidget

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            self.btn_export_png.setEnabled(True)
            self.btn_export_png.setText("📷 Export PNG")
            QMessageBox.critical(self, "PNG Export Unavailable",
                                 "QtWebEngine is not available in this build, "
                                 "so PNG rendering can't run. HTML export still works.")

        width_map = {"Auto": 1200, "Narrow": 1000, "Wide": 1500, "Full": 1200}
        render_width = width_map.get(self._current_style_dict().get("ex_width", "Auto"), 1200)

        tmp_dir = Path(tempfile.gettempdir()) / "lytrize_png_export"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        html_path = tmp_dir / f"{safe_file}_render.html"
        html_path.write_text(html, encoding="utf-8")

        # Keep references alive on self -- a local variable view/timer would
        # get garbage-collected before the async load/render finishes.
        self._png_render_view = QWebEngineView()
        # Same reason as the other chart views: a map chart in the dashboard
        # needs a remote basemap style, which Qt WebEngine refuses to load for
        # a local (file://) document unless told otherwise. Set before load, and
        # on this view specifically -- it is a separate page from the report
        # canvas, so it does not inherit those settings.
        try:
            from desktop.widgets.plotly_web_view import enable_remote_content_access
            enable_remote_content_access(self._png_render_view.page())
        except Exception:
            log.debug("could not enable remote content for the PNG export "
                      "view; map charts may export blank", exc_info=True)
        # CRITICAL, confirmed by direct testing: a QWebEngineView that is
        # never shown never actually produces rendered frames for grab()
        # to capture, even after loadFinished fires and JS executes
        # successfully -- grab() silently returns a blank/near-blank image.
        # WA_DontShowOnScreen + show() forces the compositor to render
        # without putting a visible window on the user's screen (verified:
        # first attempt without this produced an image with only 1 distinct
        # color bucket across the whole render; with this fix, real chart
        # colors show up correctly).
        self._png_render_view.setAttribute(Qt.WA_DontShowOnScreen, True)
        self._png_render_view.resize(render_width, 800)  # placeholder height, corrected after load
        self._png_render_view.show()

        def on_load_finished(ok: bool) -> None:
            if not ok:
                self._finish_png_export(None, safe_file)
                return          # nothing left to measure on a failed load

            def on_height(height):
                try:
                    full_height = max(int(height), 400)
                except (TypeError, ValueError):
                    full_height = 1200
                full_height = min(full_height, 20000)  # sanity cap
                self._png_render_view.resize(render_width, full_height)
                # Wait for the charts to ACTUALLY paint instead of guessing a
                # fixed delay. Plotly's newPlot/react run after page
                # loadFinished, and a map's tiles keep loading after that --
                # grabbing at a fixed 1200 ms caught maps mid-paint with
                # low-resolution placeholder tiles, which is exactly the
                # "blurry/low-quality map in the PNG" report. The poll also
                # nudges any stale MapLibre canvas back to device resolution
                # before the capture.
                self._png_settle_deadline = time.monotonic() + 10.0
                self._png_settle_oks = 0
                self._poll_png_settle(safe_file)

            self._png_render_view.page().runJavaScript(
                "document.body.scrollHeight", on_height
            )

        self._png_render_view.loadFinished.connect(on_load_finished)
        self._png_render_view.load(QUrl.fromLocalFile(str(html_path)))
    # One-shot JS predicate run against the offscreen export page. Returns
    # "wait" until every chart has been laid out, the slicer engine has
    # drained its recompute queue, and every MapLibre canvas has a
    # device-resolution backing store; "ok:<maps>" once ready, where <maps>
    # is how many map canvases were seen (used to give tiles a little longer
    # to blit before the grab). A stale canvas triggers ONE window-resize
    # nudge (MapLibre only re-measures on that event) and reports "wait" so
    # the next poll can confirm it. Any JS error degrades to "ok" so a
    # broken probe can never block the export forever.
    _PNG_SETTLE_JS = (
        "(function(){try{"
        "if(!window.Plotly){return 'wait';}"
        "if((window.__lyPending||0)>0){return 'wait';}"
        "var ps=document.querySelectorAll('.js-plotly-plot');"
        "for(var i=0;i<ps.length;i++){var p=ps[i];"
        "if(!p._fullLayout||p._transitioning){return 'wait';}}"
        "var dpr=window.devicePixelRatio||1,fixed=false,maps=0;"
        "var cs=document.querySelectorAll("
        "'canvas.maplibregl-canvas,canvas.mapboxgl-canvas');"
        "for(var j=0;j<cs.length;j++){maps++;var c=cs[j];"
        "var w=c.clientWidth,h=c.clientHeight;if(!w||!h){continue;}"
        "if(Math.abs(c.width-Math.round(w*dpr))>2||"
        "Math.abs(c.height-Math.round(h*dpr))>2){"
        "if(!fixed){window.dispatchEvent(new Event('resize'));fixed=true;}}}"
        "if(fixed){return 'wait';}"
        "return 'ok:'+maps;"
        "}catch(e){return 'ok:0';}})()"
    )

    # Space between settle polls, and the consecutive-"ok" polls required
    # before the grab when no map is on the page. Each poll is one interval
    # apart, so this is also the settle grace after the charts report ready.
    _PNG_SETTLE_INTERVAL_MS = 250
    _PNG_SETTLE_OK_BASE = 2

    @classmethod
    def _png_settle_required_oks(cls, map_count: int) -> int:
        """Consecutive ready polls needed before capturing.

        Charts need a couple of quiet frames; a map needs longer still for
        its vector labels / raster tiles to blit after the style reports
        loaded, so each map canvas buys two extra polls.
        """
        return cls._PNG_SETTLE_OK_BASE + 2 * max(0, int(map_count))

    def _poll_png_settle(self, safe_file: str) -> None:
        """Probe the offscreen page and schedule the capture when it holds."""
        view = getattr(self, "_png_render_view", None)
        if view is None:
            return            # export cancelled/nulled underneath us
        try:
            view.page().runJavaScript(
                self._PNG_SETTLE_JS,
                lambda result: self._on_png_settle(safe_file, result))
        except Exception:
            log.debug("PNG settle probe failed; capturing on the old fixed "
                      "delay", exc_info=True)
            QTimer.singleShot(1200, lambda: self._capture_png(safe_file))

    def _on_png_settle(self, safe_file: str, result) -> None:
        """React to one settle probe; capture, retry, or time out."""
        if getattr(self, "_png_render_view", None) is None:
            return
        text = str(result if result is not None else "ok:0")
        ready = text.startswith("ok")
        try:
            map_count = int(text.split(":", 1)[1])
        except (IndexError, ValueError):
            map_count = 0
        expired = time.monotonic() >= float(
            getattr(self, "_png_settle_deadline", 0.0))
        if ready:
            self._png_settle_oks = int(getattr(self, "_png_settle_oks", 0)) + 1
            if self._png_settle_oks >= self._png_settle_required_oks(map_count):
                self._capture_png(safe_file)
                return
        elif not expired:
            self._png_settle_oks = 0
        if expired:
            # Capture whatever is on screen rather than hanging the export
            # forever: a chart that never finishes rendering should still
            # produce a picture.
            self._capture_png(safe_file)
            return
        QTimer.singleShot(self._PNG_SETTLE_INTERVAL_MS,
                          lambda: self._poll_png_settle(safe_file))

    def _capture_png(self, safe_file: str) -> None:
        """Grab the fully-rendered offscreen view and hand off to save."""
        pixmap = self._png_render_view.grab()
        self._finish_png_export(pixmap, safe_file)

    def _finish_png_export(self, pixmap, safe_file: str) -> None:
        self.btn_export_png.setEnabled(True)
        self.btn_export_png.setText("📷 Export PNG")

        if pixmap is None or pixmap.isNull():
            QMessageBox.critical(self, "PNG Export Failed",
                                 "Could not render the dashboard to an image.")
            # was missing a return: the code went on to open a save dialog and
            # then called save() on the null pixmap it had just reported.
            self._png_render_view = None
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Dashboard as Image", str(Path.home() / f"{safe_file}.png"),
            "PNG Files (*.png)",
        )
        if file_path:
            if pixmap.save(file_path, "PNG"):
                QMessageBox.information(self, "Export Complete",
                                        f"Dashboard image saved to:\n{file_path}")
            else:
                QMessageBox.critical(self, "Export Error", "Failed to save the PNG file.")

        self._png_render_view = None

    def _on_export_failed(self, error: str) -> None:
        """Handle export failure from the worker."""
        self.btn_export.setEnabled(True)
        self.btn_export.setText("⬇️ Export HTML")
        self.btn_export_png.setEnabled(True)
        self.btn_export_png.setText("📷 Export PNG")
        log.error("Export failed: %s", error)
        QMessageBox.critical(self, "Export Error", error)

    def _on_save(self) -> None:
        """Save the current dashboard as a session."""
        charts = self.bridge.get("charts", [])
        if not charts:
            QMessageBox.warning(self, "No Charts", "No charts to save.")

        user_id = self.bridge.get("user_id")
        if not user_id:
            QMessageBox.warning(self, "No User", "No user session found.")

        session_name = self.session_name.text().strip() or "Lytrize Session"
        dash_title = self.dash_title.text().strip()
        layout_mode = "landscape" if self.layout_mode.currentText() == "Landscape" else "portrait"

        # Check if we're editing an existing session
        editing_sid = self.bridge.get("editing_session_id")
        if editing_sid:
            reply = QMessageBox.question(
                self, "Overwrite Session?",
                f"Session '{session_name}' already exists.\nDo you want to overwrite it?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.No:
                # Prompt for new name
                new_name, ok = QInputDialog.getText(
                    self, "Save As", "Enter a new session name:",
                    text=f"{session_name} (copy)"
                )
                if not ok or not new_name.strip():
                    return
                session_name = new_name.strip()
                editing_sid = None  # Create new session

        # Build charts JSON
        from services.chart_service import charts_to_json
        meta_map = {
            uid: self.bridge.get_chart_meta(uid)
            for uid, _, _ in charts
        }
        notes = {
            uid: self.bridge.get_note(uid)
            for uid, _, _ in charts
        }
        charts_json = charts_to_json(charts, notes=notes, meta_map=meta_map)

        df = self.bridge.df
        grid_cols_n = int(self.bridge.get("grid_cols_n", 2))
        # The tiling canvas is the live arrangement, so save what IT holds --
        # reading grid_fullwidth here would persist the old boolean map and
        # throw away everything the user dragged or resized.
        # One blob for the whole arrangement: chart tiles keyed by uid, KPI
        # tiles keyed by their index string. Same column, so a session saved
        # before KPIs were tiles still round-trips untouched.
        chart_geom, kpi_geom = self._current_geometry(
            charts, self.bridge.get("kpis", []), grid_cols_n)
        grid_fullwidth_json = json.dumps({**chart_geom, **kpi_geom})
        # The 23-key style dict (colors, typography, layout density from
        # the dashboard style panel) was being read/applied correctly
        # during a live session but never persisted at all -- the DB
        # already has export_text_json/export_colours_json columns for
        # this (confirmed against the old codebase), but nothing here
        # ever wrote to them. Storing the whole style dict as one blob
        # in export_colours_json rather than replicating the old
        # codebase's exact text/colours key split -- this is a fresh,
        # internal-only column for this app, not required to match the
        # old JSON shape for any compatibility reason, and one dict
        # matching _current_style_dict()/_STYLE_DEFAULTS exactly is
        # simpler to keep correct than a hand-maintained translation
        # layer between two different key-naming conventions.
        # Raw stored values, NOT the live theme-following dict: persisting the
        # resolved light-mode colours would turn them into what looks like a
        # deliberate user override on the next load, so the dashboard would
        # stick to light colours after switching back to dark.
        export_colours_json = json.dumps(self._export_style_dict())
        try:
            if editing_sid:
                # Update existing session.
                #
                # BUG FIXED HERE: this call previously passed its arguments
                # positionally, copied from save_session_db's parameter
                # order -- but update_session_db has a genuinely different
                # signature (session_id, session_name, charts_json,
                # analysis_types, user_id, ...), no file_name/rows/cols
                # params at all, and dashboard_title in a different slot.
                # The positional call landed user_id where session_name
                # was expected, session_name where charts_json was
                # expected, and so on, then crashed outright with
                # "got multiple values for argument 'dashboard_title'"
                # since position 6 and the keyword both targeted the same
                # parameter. Confirmed by reproducing the exact call
                # shape in isolation. Every "update existing session" save
                # was broken. Switched to all-keyword arguments so this
                # entire class of bug can't happen again if either
                # function's parameter order ever changes.
                from services.database_service import update_session_db
                update_session_db(
                    session_id=editing_sid,
                    session_name=session_name,
                    charts_json=charts_json,
                    analysis_types=self.bridge.get("selected_analyses", []),
                    user_id=user_id,
                    dashboard_title=dash_title,
                    kpis_json=json.dumps(self.bridge.get("kpis", [])),
                    layout_mode=layout_mode,
                    grid_cols_n=grid_cols_n,
                    grid_fullwidth_json=grid_fullwidth_json,
                    export_colours_json=export_colours_json,
                )
                clear_draft(user_id)
                invalidate_sessions_cache()
                QMessageBox.information(self, "Updated",
                                        f"Session '{session_name}' updated successfully.")
                # Clear editing state
                self.bridge.pop("editing_session_id", None)
                self.bridge.pop("editing_session_name", None)
                self.bridge.pop("editing_file_name", None)
            else:
                # Create new session
                sid = save_session_db(
                    user_id=user_id,
                    session_name=session_name,
                    file_name=self.bridge.get("file_name", ""),
                    rows=df.shape[0] if df is not None else 0,
                    cols=df.shape[1] if df is not None else 0,
                    analysis_types=self.bridge.get("selected_analyses", []),
                    charts_json=charts_json,
                    dashboard_title=dash_title,
                    kpis_json=json.dumps(self.bridge.get("kpis", [])),
                    layout_mode=layout_mode,
                    grid_cols_n=grid_cols_n,
                    grid_fullwidth_json=grid_fullwidth_json,
                    export_colours_json=export_colours_json,
                )
                clear_draft(user_id)
                invalidate_sessions_cache()
                QMessageBox.information(self, "Saved",
                                        f"Session '{session_name}' saved successfully.")
        except Exception as exc:
            log.error("Save failed: %s", exc)
            QMessageBox.critical(self, "Save Error", str(exc))

    def _on_bridge_state_changed(self, key: str, _value) -> None:
        """A state key changed somewhere other than this page.

        The per-chart slicer entry on the Analysis page writes
        ``bridge.set("slicers", ...)``; this board is a different page, so it
        listens for that key and rebuilds -- otherwise the new slicer would
        only surface on the next unrelated regen.
        """
        if str(key) != "slicers":
            return
        try:
            self._refresh_slicer_count()
            self._schedule_regen()
        except Exception:
            log.debug("slicer state refresh failed", exc_info=True)

    def _on_charts_changed(self, charts) -> None:
        """Handle charts_changed signal."""
        self._refresh_charts()
        self._refresh_kpis()

    # -- Chart management ---------------------------------------------------

    def _refresh_charts(self) -> None:
        """Reconcile the board with the current chart list and rebuild it.

        There are no Qt chart widgets to create, update or tear down any more:
        the report renders the charts, so "refreshing" is just re-reading the
        list, re-placing any tile whose chart arrived or vanished, and asking
        for a rebuild. That removes the whole class of bug where rebuilding a
        card reloaded its QWebEngineView and flashed the entire board -- the
        "the app reopened itself" symptom.
        """
        charts = self.bridge.get("charts", [])
        cols = int(self.bridge.get("grid_cols_n", 2))

        # Drop geometry for charts that are gone. Otherwise a restored
        # arrangement naming a deleted chart keeps contributing cells and gets
        # written back out on every drag, growing the saved blob each session.
        self._prune_geometry([c[0] for c in charts])

        self.lbl_empty.setVisible(len(charts) == 0)
        self._schedule_regen()

    def _prune_geometry(self, chart_uids) -> None:
        """Forget stored placement for charts that no longer exist."""
        stored = dict(self.bridge.get("tile_geometry", {}) or {})
        # The free-canvas mode marker is preserved: it is not a chart or KPI
        # tile, but dropping it here would make the next _current_geometry treat
        # the board as a marker-less legacy blob and re-run the grid heuristic.
        keep = {u: g for u, g in stored.items() if u in set(chart_uids)
                or _is_kpi_key(u) or u == _MODE_KEY}
        if keep != stored:
            self.bridge.set("tile_geometry", keep)

    def _on_tidy_layout(self) -> None:
        """Re-flow the whole board into a clean arrangement, keeping its order.

        The escape hatch for a canvas dragged into a tangle, and now the ONLY
        layout control -- the column count went away with the grid. Both maps are
        tidied TOGETHER because they share one canvas: tidying the KPIs against
        the charts would interleave two separately-packed strips and undo the
        point. Order is preserved, so this tidies rather than reorders, and each
        card keeps the size the user gave it.
        """
        charts = self.bridge.get("charts", [])
        kpis = self.bridge.get("kpis", [])
        cols = int(self.bridge.get("grid_cols_n", 2))
        chart_map, kpi_map = self._current_geometry(charts, kpis, cols)
        gap = self._gap_px()
        combined = tile_geometry.tidy({**chart_map, **kpi_map}, gap_px=gap)
        self._persist_geometry(
            {u: g for u, g in combined.items() if u in chart_map},
            {u: g for u, g in combined.items() if _is_kpi_key(u)},
            cols,
        )

    def _on_move_up(self, uid: str) -> None:
        """Move a chart earlier via the canvas's action bar."""
        self._nudge_card(uid, -1)

    def _on_move_down(self, uid: str) -> None:
        """Move a chart later via the canvas's action bar."""
        self._nudge_card(uid, +1)

    def _nudge_card(self, uid: str, delta: int) -> None:
        """Trade *uid*'s placement with its visual neighbour's.

        Moving in the VISUAL order is what the user means: the card above or
        below on screen, regardless of what order the bridge happens to hold.

        The two cards EXCHANGE their rectangles rather than the board being
        re-flowed. Re-flowing was right when every card was a grid cell, but on a
        free canvas the arrangement is the user's work -- pressing "move down" on
        one card must not quietly re-lay-out the other nine.
        """
        charts = self.bridge.get("charts", [])
        kpis = self.bridge.get("kpis", [])
        cols = int(self.bridge.get("grid_cols_n", 2))
        chart_map, kpi_map = self._current_geometry(charts, kpis, cols)
        order = tile_geometry.visual_order(chart_map)
        if uid not in order:
            return
        i = order.index(uid)
        j = i + delta
        if not 0 <= j < len(order):
            return
        other = order[j]
        chart_map[uid], chart_map[other] = chart_map[other], chart_map[uid]

        by_uid = {c[0]: c for c in charts}
        new_order = list(order)
        new_order[i], new_order[j] = new_order[j], new_order[i]
        if all(u in by_uid for u in new_order):
            self.bridge.set("charts", [by_uid[u] for u in new_order])
        self._persist_geometry(chart_map, kpi_map, cols)

    def _on_delete_chart(self, uid: str) -> None:
        """Delete a chart from the dashboard."""
        self.bridge.remove_chart(uid)
        self._refresh_charts()

    def _on_note_changed(self, uid: str, text: str) -> None:
        """Handle note changes."""
        self.bridge.set_note(uid, text)

    def _on_style_changed(self, uid: str, style: dict) -> None:
        """Persist a chart's style settings to the bridge -- same pattern
        as _on_note_changed, so styling survives a session save instead
        of only living on the in-memory ChartCard instance."""
        self.bridge.set_chart_meta(uid, display_options=style)

    # -- Public API ----------------------------------------------------------

    def refresh(self) -> None:
        """Refresh the page when navigated to."""
        # Check if we're viewing a saved session
        view_sid = self.bridge.get("view_session_id")
        self._viewing_saved = view_sid is not None

        if view_sid:
            user_id = self.bridge.get("user_id")
            loaded = get_session_charts(view_sid, user_id)
            if loaded:
                # Convert 6-tuples to 3-tuples for the bridge
                charts = [(uid, title, fig) for uid, title, fig, *_ in loaded]
                self.bridge.set("charts", charts)
                for uid, title, fig, desc, ctype, meta in loaded:
                    self.bridge.set(f"chart_type_{uid}", ctype)
                    self.bridge.set(f"chart_meta_{uid}", meta)
                    if desc:
                        self.bridge.set_note(uid, desc)

                sm = get_session_meta(view_sid, user_id)
                if sm:
                    self.dash_title.setText(sm.get("dashboard_title", ""))
                    self.session_name.setText(
                        self.bridge.get("view_session_name", "Saved Session")
                    )
                    layout = sm.get("layout_mode", "portrait")
                    self.layout_mode.setCurrentText(
                        "Landscape" if layout == "landscape" else "Portrait"
                    )
                    try:
                        self.bridge.set("kpis", json.loads(sm.get("kpis_json", "[]")))
                    except Exception:
                        self.bridge.set("kpis", [])

                    # The column count and the arrangement both have to reach
                    # the BRIDGE, not just be read locally: the combo box shows
                    # one value and the board reads another, and a re-save
                    # without touching the combo then silently reverts to
                    # whatever the bridge happened to hold.
                    grid_cols_n = max(1, min(4, int(sm.get("grid_cols_n", 2))))
                    self.bridge.set("grid_cols_n", grid_cols_n)
                    # The saved blob holds per-block placement for chart tiles
                    # (keyed by uid) and KPI tiles (keyed by index); put it in
                    # the bridge under the key the geometry helpers read, so a
                    # re-save keeps the arrangement instead of reverting to an
                    # auto-arranged board.
                    self.bridge.set("tile_geometry",
                                    _load_saved_tiles(sm))

                    # Restore the 23-key style dict (colors, typography,
                    # layout density) into the bridge, then push those
                    # values into the actual widgets -- see
                    # _sync_style_widgets_from_bridge for why the second
                    # step is necessary and not implied by the first.
                    try:
                        saved_style = json.loads(sm.get("export_colours_json", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        saved_style = {}
                    for key, default in _STYLE_DEFAULTS.items():
                        self.bridge.set(key, saved_style.get(key, default))
                    self._sync_style_widgets_from_bridge()
                    # Re-mark the theme the session was saved with, so the
                    # gallery shows which one is in effect instead of appearing
                    # unselected while its exact colours are on the board.
                    saved_preset = str(saved_style.get("ex_preset", "") or "")
                    if saved_preset in EXPORT_PRESETS:
                        self.preset_gallery.select(saved_preset)
                    else:
                        self.preset_gallery.clear()
        else:
            # Not viewing saved — use current session state
            editing_name = self.bridge.get("editing_session_name")
            if editing_name:
                self.session_name.setText(editing_name)
            else:
                self.session_name.setText(
                    self.bridge.get("file_name", "Analysis Session")
                )
        self._refresh_charts()
        self._refresh_kpis()
        # Navigating to the page must show the real count: a draft restored in
        # the background can hold slicers this page has never seen.
        self._refresh_slicer_count()
