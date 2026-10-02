"""desktop/widgets/kpi_card.py -- KPI value card for the live dashboard.

The live card is a stand-in for the ``.kpi-card`` the exporter writes
(``backend/modules/export.py``): icon on top, value centred and stepped down as
it gets longer, an uppercase letter-spaced label underneath, and a delta chip
ONLY for the comparison types. That is the whole point of the widget -- the
board the user arranges is the report they export, so a card cannot look one
way on screen and another in the file.

Two things the old version got wrong:

* It showed a permanent row of up/down/close buttons in the top-right corner of
  every card, which ate the space the number needed. The controls are now
  revealed on hover and on keyboard focus, and sit in an overlay row so that
  showing them cannot reflow the text.
* It took a free-text ``delta`` string, so an ordinary total could be handed an
  arrow that meant nothing. The arrow is now derived from ``change_pct``, which
  only the period-comparison KPI types set.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)

# Value length thresholds, in characters. These are the exporter's own numbers
# (see ``_kpi_val_style`` in modules/export.py): matching them is what stops a
# long value from filling the card on screen and then wrapping differently in
# the file.
LONG_VALUE_CHARS = 12
VERY_LONG_VALUE_CHARS = 16


class KpiCard(QFrame):
    """A single KPI metric card, laid out exactly as it will be exported."""

    move_up_requested = Signal(int)     # kpi index
    move_down_requested = Signal(int)   # kpi index
    remove_requested = Signal(int)      # kpi index
    edit_requested = Signal(int)        # kpi index
    # Drag-to-reorder, the peer the arrow buttons provide for keyboard users.
    # The page owns the KPI list, so the gesture is reported, not performed.
    drag_started = Signal(int, object)  # kpi index, QPoint (global pos)
    drag_moved = Signal(int, object)    # kpi index, QPoint (global pos)
    drag_ended = Signal(int)            # kpi index

    def __init__(self, kpi: Optional[dict] = None, index: int = 0,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("kpi_card")
        self._index = index
        self._kpi = dict(kpi or {})
        self._card_bg = "#1b2245"
        self._border = "#2c3564"
        self._text_color = "#f5f7ff"
        self._accent = "#6163df"
        self._value_base = 26
        self._value_size = 26
        self._build_ui()
        self.set_kpi(self._kpi)

    # -- Construction ------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(1)
        # The card is a peer in a row, so it takes an EQUAL share and never
        # demands a width of its own -- the old minimumWidth(140) on a long
        # value is what pushed the fourth card onto its own line.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(120)

        # -- Chrome: grip + actions, hidden until hover -------------------
        # Kept in its own row above the content so revealing it grows the card
        # rather than pushing the value around; the row is collapsed to zero
        # height while hidden, so the resting layout is exactly the export's.
        self.chrome = QHBoxLayout()
        self.chrome.setContentsMargins(0, 0, 0, 0)
        self.chrome.setSpacing(1)
        self.grip = QLabel("⠿")
        self.grip.setFixedSize(16, 18)
        self.grip.setCursor(Qt.OpenHandCursor)
        self.grip.setToolTip("Drag to reorder")
        self.grip.setAlignment(Qt.AlignCenter)
        self.grip.setFocusPolicy(Qt.NoFocus)
        self.chrome.addWidget(self.grip)
        self.chrome.addStretch()
        self.btn_up = self._icon_button("▲", "Move earlier")
        self.btn_up.clicked.connect(
            lambda: self.move_up_requested.emit(self._index))
        self.btn_down = self._icon_button("▼", "Move later")
        self.btn_down.clicked.connect(
            lambda: self.move_down_requested.emit(self._index))
        self.btn_edit = self._icon_button("✎", "Edit this KPI")
        self.btn_edit.clicked.connect(
            lambda: self.edit_requested.emit(self._index))
        self.btn_remove = self._icon_button("✕", "Remove KPI")
        self.btn_remove.clicked.connect(
            lambda: self.remove_requested.emit(self._index))
        for b in (self.btn_up, self.btn_down, self.btn_edit, self.btn_remove):
            self.chrome.addWidget(b)
        layout.addLayout(self.chrome)
        self._chrome_widgets = (self.grip, self.btn_up, self.btn_down,
                                self.btn_edit, self.btn_remove)
        for w in self._chrome_widgets:
            w.hide()

        # -- Content: the three lines the exporter emits -------------------
        self.lbl_icon = QLabel("")
        self.lbl_icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_icon)

        self.lbl_value = QLabel("--")
        self.lbl_value.setAlignment(Qt.AlignCenter)
        self.lbl_value.setWordWrap(True)
        self.lbl_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_value)

        self.lbl_delta = QLabel("")
        self.lbl_delta.setAlignment(Qt.AlignCenter)
        self.lbl_delta.setVisible(False)
        layout.addWidget(self.lbl_delta)

        self.lbl_label = QLabel("")
        self.lbl_label.setAlignment(Qt.AlignCenter)
        self.lbl_label.setWordWrap(True)
        layout.addWidget(self.lbl_label)

        self.setAttribute(Qt.WA_Hover, True)
        self.grip.mousePressEvent = self._on_grip_press
        self.grip.mouseMoveEvent = self._on_grip_move
        self.grip.mouseReleaseEvent = self._on_grip_release

    def _icon_button(self, text: str, tip: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(20, 20)
        b.setFocusPolicy(Qt.TabFocus)
        b.setToolTip(tip)
        b.setProperty("kind", "icon")
        b.setCursor(Qt.PointingHandCursor)
        return b

    # -- Content -----------------------------------------------------------

    def set_kpi(self, kpi: dict) -> None:
        """Load a KPI dict. Cheap enough to call on every restyle."""
        self._kpi = dict(kpi or {})
        icon = str(self._kpi.get("icon", "") or "")
        label = str(self._kpi.get("label", "") or "KPI")
        prefix = str(self._kpi.get("prefix", "") or "")
        value = str(self._kpi.get("value", "--") or "--")
        suffix = str(self._kpi.get("suffix", "") or "")
        stale = str(self._kpi.get("stale", "") or "")

        full = f"{prefix}{value}{suffix}"
        self.lbl_icon.setText(icon)
        self.lbl_label.setText(label)
        self.lbl_value.setText(full)
        self.setToolTip(f"{label}: {full}")
        self._apply_value_size(len(full))
        self._apply_delta()

        if stale:
            # A card whose column vanished after a re-upload says so, rather
            # than reading "--" -- which is indistinguishable from a genuinely
            # empty result and is how a wrong number survives unnoticed.
            self.lbl_label.setText(f'{label} \u2014 pick "{stale}" again')
        self._restyle()

    def _apply_value_size(self, chars: int) -> None:
        """Step the value down as it gets longer.

        These are the exporter's own tiers and the same subtraction
        (``_kpi_val_style`` in modules/export.py), so the on-screen card and the
        exported card break at the same character counts and use the same point
        sizes. Anything else and "the preview matches the export" is a promise
        the code does not keep.
        """
        base = int(self._value_base or 26)
        if chars > VERY_LONG_VALUE_CHARS:
            size = max(10, base - 4)
        elif chars > LONG_VALUE_CHARS:
            size = max(10, base - 2)
        else:
            size = base
        self._value_size = size
        self._restyle()

    def _apply_delta(self) -> None:
        """Show the arrow ONLY for a real period comparison."""
        chg = self._kpi.get("change_pct")
        if chg is None or isinstance(chg, bool):
            self.lbl_delta.setVisible(False)
            return
        try:
            pct = float(chg)
        except (TypeError, ValueError):
            self.lbl_delta.setVisible(False)
            return
        up = pct >= 0
        self.lbl_delta.setText(f"{'▲' if up else '▼'} {abs(pct):.1f}%")
        self.lbl_delta.setStyleSheet(
            f"font-size: 12px; font-weight: 700;"
            f" color: {'#10b981' if up else '#ef4444'};")
        self.lbl_delta.setVisible(True)
        self.lbl_delta.setToolTip("Versus the prior period")

    # -- Styling -----------------------------------------------------------

    def apply_style(self, style: dict) -> None:
        """Re-skin from a dashboard style dict, in place (no rebuild)."""
        self._card_bg = str(style.get("ex_kpi", "#1b2245"))
        self._border = str(style.get("ex_border", "#2c3564"))
        self._text_color = str(style.get("ex_kpi_text_color", "#f5f7ff"))
        self._accent = str(style.get("ex_accent", "#6163df"))
        try:
            # The same key the exporter reads, used the same way: it IS the
            # large-tier point size, and both sides step down from it for longer
            # values. Treating it as a "large" tier here and as an exact size
            # there is what made the live card and the file disagree.
            self._value_base = max(10, int(float(style.get("ex_kpi_val_size", 26))))
        except (TypeError, ValueError):
            self._value_base = 26
        self._restyle()

    def _restyle(self) -> None:
        """Re-apply every derived style in one place.

        Centralised because the value size, the delta colour and the theme all
        have to move together: setting them at their individual call sites is how
        the card ended up with a light-mode background and dark-mode text.
        """
        self.setStyleSheet(
            f"QFrame#kpi_card {{ background: {self._card_bg};"
            f" border: 1px solid {self._border}; border-radius: 10px; }}"
        )
        self.lbl_icon.setStyleSheet(f"font-size: 18px; color: {self._text_color};")
        # Qt stylesheets have no ``text-transform``, so the uppercase, letter-
        # spaced caption the export uses is applied through the font instead.
        cap_font = QFont(self.lbl_label.font())
        cap_font.setCapitalization(QFont.AllUppercase)
        cap_font.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        self.lbl_label.setFont(cap_font)
        self.lbl_label.setStyleSheet(
            f"font-size: 10px; color: {self._text_color}; font-weight: 600;")
        if self._kpi.get("stale"):
            self.lbl_value.setStyleSheet(
                "font-weight: 800; color: #f59e0b;")
        else:
            self.lbl_value.setStyleSheet(
                f"font-size: {self._value_size}px; font-weight: 800;"
                f" color: {self._accent};")
        # The chrome sits on the card surface, so it has to be re-coloured with
        # it -- left on the dark-theme default it is invisible in light mode.
        for w in self._chrome_widgets:
            w.setStyleSheet(f"color: {self._text_color}; background: transparent;")

    def _show_chrome(self, on: bool) -> None:
        for w in self._chrome_widgets:
            w.setVisible(bool(on))

    def enterEvent(self, event) -> None:
        self._show_chrome(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._show_chrome(False)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        # Keyboard users get the same affordance; without this the buttons are
        # reachable by Tab but invisible.
        self._show_chrome(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._show_chrome(False)
        super().focusOutEvent(event)

    # -- Drag to reorder ---------------------------------------------------

    def _on_grip_press(self, event) -> None:
        self.grip.setCursor(Qt.ClosedHandCursor)
        self.drag_started.emit(self._index, event.globalPosition().toPoint())
        self._dragging = True

    def _on_grip_move(self, event) -> None:
        if getattr(self, "_dragging", False):
            self.drag_moved.emit(self._index, event.globalPosition().toPoint())

    def _on_grip_release(self, _event) -> None:
        self.grip.setCursor(Qt.OpenHandCursor)
        if getattr(self, "_dragging", False):
            self._dragging = False
            self.drag_ended.emit(self._index)

    def set_drag_position(self, x: int) -> None:
        """Live visual feedback while a drag is in progress."""
        self.move(x, self.y())
