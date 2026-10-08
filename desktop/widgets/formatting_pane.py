"""desktop/widgets/formatting_pane.py -- Dashboard formatting pane helpers.

The left-hand formatting panel used to be a plain stack of always-open
``QGroupBox``es inside a 260-420px column. Three things were wrong with it, and
all three are what "the settings are clunky and the label and the card overlap"
describes:

1. ``QGroupBox::title`` is drawn INSIDE the box's top border at the box's own
   top margin, while the form layout inside started at a fixed 14px -- so on a
   tight column the caption sat on top of the first control. The title needs to
   sit ABOVE the content, which means giving it its own row rather than
   letting a border subcontrol float over a fixed margin.
2. Eight always-open groups meant a wall of ~32 rows with no hierarchy, so
   nothing was findable. Every desktop tool solves this the same way:
   collapsible sections, a couple open at a time.
3. Rows had no shared geometry, so labels and controls formed a ragged left
   edge that reads as "unpolished" even when nothing is actually overlapping.

``CollapsibleSection`` fixes all three; ``PresetGallery`` adds the one-click
themes whose absence was reported against the old app.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPen
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from services.export_service import EXPORT_PRESETS, PRESET_SWATCH_KEYS

log = logging.getLogger(__name__)

# Row geometry, shared by every group so the whole pane lines up.
LABEL_W = 96
ROW_GAP = 8


def luminance(hex_color: str) -> float:
    """Relative luminance of a hex colour, 0..1.

    Parsed by hand rather than through QColor so it is available before a
    QApplication exists (the gallery is also built in headless tests).
    """
    try:
        h = str(hex_color).lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 0.0

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


class CollapsibleSection(QFrame):
    """A titled group whose body can be folded away.

    The caption is a real widget in the layout rather than a QGroupBox title, so
    it can never be drawn on top of the first control: it takes its own row and
    the body starts below it.
    """

    toggled_open = Signal(bool)

    def __init__(self, title: str, parent=None, expanded: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("format_section")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.btn_head = QPushButton(self._caption(title, expanded))
        self.btn_head.setProperty("kind", "section")
        self.btn_head.setCheckable(True)
        self.btn_head.setChecked(bool(expanded))
        self.btn_head.setCursor(Qt.PointingHandCursor)
        self.btn_head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_head.setToolTip("Click to fold this section away")
        self._title = title
        head.addWidget(self.btn_head, 1)
        outer.addLayout(head)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(10, 6, 10, 8)
        self.body_layout.setSpacing(ROW_GAP)
        self.body.setVisible(bool(expanded))
        outer.addWidget(self.body)

        self.btn_head.toggled.connect(self._on_toggled)

    @staticmethod
    def _caption(title: str, expanded: bool) -> str:
        return ("▾  " if expanded else "▸  ") + title

    def _on_toggled(self, on: bool) -> None:
        self.body.setVisible(bool(on))
        self.btn_head.setText(self._caption(self._title, on))
        # The frame is Maximum-height, so it has to be told to shrink; otherwise
        # the collapsed body still reserves its old space and leaves a gap where
        # the section used to be.
        self.updateGeometry()
        self.toggled_open.emit(bool(on))

    def add(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def set_expanded(self, on: bool) -> None:
        self.btn_head.setChecked(bool(on))


class FieldRow(QWidget):
    """A label + control pair on the shared grid.

    The label gets a FIXED width so every control in the pane starts at the
    same x, which is what turns a stack of inputs into a table.
    """

    def __init__(self, label: str, control: QWidget, tip: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        cap = QLabel(label)
        cap.setFixedWidth(LABEL_W)
        cap.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        cap.setStyleSheet("font-size: 12px;")
        lay.addWidget(cap)
        # Whether the control may soak up the row's spare width has to be read
        # BEFORE the policy is overwritten below, because the caller's intent
        # is the only signal: a colour swatch arrives already setFixedSize()
        # (so Qt reports Fixed), while a combo/line edit arrives Expanding.
        was_expanding = control.sizePolicy().horizontalPolicy() in (
            QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        # setFixedSize() pins min == max. Capture that BEFORE the policies are
        # touched below, because it decides both whether the minimum may be
        # released and whether the control is allowed to take the stretch.
        was_fixed = (control.minimumWidth() == control.maximumWidth()
                     and control.minimumWidth() > 0)
        # A combo/line edit advertises a large minimumSizeHint (a
        # QFontComboBox alone asks for ~327px). Because FieldRow is inside a
        # widgetResizable QScrollArea, that hint became the pane's minimum
        # width, which is wider than the pane is ever allowed to be -- so the
        # content was clipped with no way to reach the right-hand control.
        # Releasing the minimum lets the row shrink to the pane instead of the
        # other way round. A control pinned with setFixedSize() (the colour
        # swatches) is left completely alone: its min == max, and clearing
        # that would let the layout squash the swatch to nothing.
        if not was_fixed:
            control.setMinimumWidth(0)
        if not was_fixed and hasattr(control, "setMinimumContentsLength"):
            try:
                control.setMinimumContentsLength(6)
            except TypeError:
                pass
        if hasattr(control, "setSizeAdjustPolicy"):
            from PySide6.QtWidgets import QComboBox
            try:
                control.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            except (AttributeError, TypeError):
                pass
        if was_expanding:
            control.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lay.addWidget(control, 1)
        else:
            # A fixed-width control must NOT be given the stretch. A
            # QHBoxLayout with nothing able to expand splits the surplus
            # evenly around the non-expanding items, so the label ended up
            # stranded in the middle of the row with a ~50px dead gutter to its
            # left -- the "empty space beside the colour swatches" report. A
            # trailing stretch pins the pair to the left edge instead.
            control.setSizePolicy(QSizePolicy.Fixed,
                                  control.sizePolicy().verticalPolicy())
            lay.addWidget(control, 0)
            lay.addStretch(1)
        if tip:
            self.setToolTip(tip)
            control.setToolTip(tip)
        self.control = control
        self.caption = cap


class PresetButton(QPushButton):
    """One quick-theme cell: the theme's own colour bands plus its NAME.

    The bands are painted in ``paintEvent`` rather than declared in a Qt
    style sheet, because Qt Style Sheets implement a subset of CSS and
    ``qlinear-gradient`` is not in it -- the declaration parses, is silently
    dropped, and every button renders as an unstyled grey box. That is what
    made the gallery read as a grid of blank rectangles.

    The name is drawn on the card rather than left to a tooltip, so a
    screenshot is enough to tell the themes apart, and so there is a visible
    label for anyone who does not hover.
    """

    def __init__(self, name: str, colors: dict, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._bands = [str(colors.get(key, "#121a2e"))
                       for key in PRESET_SWATCH_KEYS]
        # The readable half of the card is filled with the theme's PAGE colour,
        # so the ink has to contrast with THAT: a dark theme needs light text
        # and a light theme needs dark text. Getting this backwards paints
        # "Midnight Pulse" in near-black on near-black and the label vanishes.
        self._label = _strip_emoji(name)
        page_lum = luminance(colors.get("ex_bg", "#121a2e"))
        self._fg = "#f8fbff" if page_lum < 0.5 else "#0f172a"
        self._accent = str(colors.get("ex_accent", "#6163df"))
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFixedHeight(46)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("kind", "preset")
        self.setToolTip(f"Apply the {name} theme")
        self.setAccessibleName(f"Apply the {name} dashboard theme")

    def name(self) -> str:
        return self._name

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        from PySide6.QtGui import QColor, QPainter

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        page = QColor(self._bands[0])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(page)
        p.drawRoundedRect(rect, 6, 6)

        # Four equal bands across the TOP of the card: page, card, accent, KPI.
        band_h = max(10, rect.height() // 2)
        band_w = rect.width() / max(1, len(self._bands))
        p.setClipRect(rect)
        for i, colour in enumerate(self._bands):
            p.fillRect(QRect(int(i * band_w), rect.top(),
                             int(band_w) + 1, band_h), QColor(colour))
        p.setClipping(False)

        # Label on the page-coloured half, in a contrasting ink.
        p.setPen(QColor(self._fg))
        font = p.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        font.setBold(True)
        p.setFont(font)
        text_rect = QRect(rect.left() + 4, rect.top() + band_h,
                          rect.width() - 8, rect.height() - band_h)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(font).elidedText(
                       self._label, Qt.TextElideMode.ElideRight,
                       text_rect.width()))

        # Selection / hover ring, in the theme's own accent.
        if self.isChecked():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(self._accent), 2))
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        p.end()


def _strip_emoji(name: str) -> str:
    """The preset's readable name, without its leading emoji.

    ``⚡ Midnight Pulse`` is a fine tooltip and a bad 46px-tall button label:
    the glyph eats a third of the width and pushes the words into an ellipsis.
    """
    out = "".join(ch for ch in str(name)
                  if not (0x1F000 <= ord(ch) <= 0x1FAFF
                          or 0x2600 <= ord(ch) <= 0x27BF
                          or ch in "️☀️"))
    return " ".join(out.split()) or str(name)


class PresetGallery(QWidget):
    """The one-click dashboard themes.

    Each button paints the preset's own page / card / accent / KPI colours as
    four bands and shows the theme's name, so the button IS the preview -- there
    is no separate preview step, and applying one repaints the live board
    immediately. See ``PresetButton`` for why this is painted rather than
    style-sheeted.
    """

    preset_selected = Signal(str)

    def __init__(self, parent=None, columns: int = 3) -> None:
        super().__init__(parent)
        self.setObjectName("preset_gallery")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self._buttons: dict[str, QPushButton] = {}
        for i, (name, colors) in enumerate(EXPORT_PRESETS.items()):
            btn = PresetButton(name, colors, self)
            btn.clicked.connect(
                lambda _checked=False, n=name: self.preset_selected.emit(n))
            grid.addWidget(btn, i // columns, i % columns)
            self._buttons[name] = btn

    @staticmethod
    def _swatch_css(colors: dict) -> str:
        """Ring colours for a preset, kept for callers that set a sheet.

        No gradient here on purpose: Qt Style Sheets do not implement
        ``qlinear-gradient``, so a stylesheet declaring one produces an
        unstyled button rather than a gradient.
        """
        accent = colors.get("ex_accent", "#6163df")
        page = colors.get("ex_bg", "#121a2e")
        fg = "#0f172a" if luminance(page) < 0.5 else "#f8fbff"
        return (
            "QPushButton {"
            f" border: 1px solid {accent}; border-radius: 6px; padding: 0px;"
            " background: transparent; }"
            f"QPushButton:checked {{ border: 2px solid {accent}; }}"
            f"QPushButton:focus {{ border: 2px solid {fg}; }}"
        )

    def select(self, name: str) -> None:
        """Mark a preset as the active one (e.g. when a session is loaded)."""
        btn = self._buttons.get(str(name or ""))
        if btn is not None:
            btn.setChecked(True)

    def clear(self) -> None:
        """Un-mark every preset, i.e. "the colours are hand-picked".

        The buttons are auto-exclusive, and Qt refuses to un-check the checked
        member of an exclusive group through ``setChecked(False)`` -- so the
        exclusivity is lifted for the sweep and put back afterwards.
        """
        for btn in self._buttons.values():
            btn.setAutoExclusive(False)
            btn.setChecked(False)
        for btn in self._buttons.values():
            btn.setAutoExclusive(True)

