"""desktop/widgets/tiling_canvas.py -- Free-form tiling surface for dashboard cards.

Replaces the QGridLayout the dashboard used to place chart cards in. The grid
could only express "N equal columns, full width or nothing", so arranging a
dashboard meant moving things one slot at a time with the arrow buttons, and a
card could never be, say, two columns wide and half a row tall.

This canvas is the CSS-grid analogue: cards are children positioned
absolutely, and each one remembers its own (x, y, w, h) in GRID UNITS. The
pixel geometry is derived from the current column count, so changing the column
setting re-flows everything instead of discarding the arrangement.

Design notes
------------
* Geometry is stored in grid units, never pixels, so a window resize or a
  change of column count preserves the layout the user built.
* A plain first-fit pass resolves overlaps (later cards are pushed down), so
  dropping a card onto an occupied cell reflows instead of hiding one behind
  the other.
* Dragging and resizing are driven from the canvas, not the card: the card only
  reports "a gesture started here" (ChartCard.drag_started / resize_started)
  because the grid maths lives here.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QPoint, QRect, Signal
from PySide6.QtWidgets import QWidget, QSizePolicy

log = logging.getLogger(__name__)

# Height of one grid row by default; a card can then be made taller (h > 1).
DEFAULT_ROW_HEIGHT = 320
MIN_COL_SPAN = 1
MIN_ROW_SPAN = 1
MIN_CARD_H = 160
# Gap between cards, matching the grid spacing the dashboard used before.
GAP = 12


class TilingCanvas(QWidget):
    """An absolutely-positioned, freely rearrangeable surface for cards."""

    # uid -> {"x","y","w","h"} in grid units, plus the column count, after any
    # user gesture. The page persists this so a saved session restores the
    # arrangement rather than falling back to "everything equal".
    layout_changed = Signal(dict, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: dict[str, QWidget] = {}
        self._geom: dict[str, dict] = {}
        self._cols = 2
        self._row_height = DEFAULT_ROW_HEIGHT
        self._drag: Optional[dict] = None
        # Guard flag: relayout() resizes the canvas, which re-enters it.
        self._in_relayout: bool = False

        self.setObjectName("tiling_canvas")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    # -- Population -------------------------------------------------------

    def set_columns(self, cols: int) -> None:
        """Change the column count, keeping every card's grid-unit geometry.

        A card that was 2 columns wide stays 2 columns wide; one wider than the
        new count is clamped so it cannot hang off the edge.
        """
        self._cols = max(1, int(cols))
        self._clamp_geometry()
        self.relayout()

    def columns(self) -> int:
        return self._cols

    def set_row_height(self, px: int) -> None:
        """Height of one grid row, tied to the dashboard's 'Chart height' so the
        visual grid and the cards agree."""
        self._row_height = max(MIN_CARD_H, int(px))
        self.relayout()

    def add_card(self, uid: str, card: QWidget) -> None:
        """Register a card. New cards land below the existing arrangement."""
        if uid in self._cards:
            self.remove_card(uid)
        self._cards[uid] = card
        if uid not in self._geom:
            self._geom[uid] = self._next_free_cell()
        # Cards are positioned by this canvas, so they must not also be owned
        # by a layout -- the old QGridLayout would fight every setGeometry.
        card.setParent(self)
        # setParent() HIDES the widget it moves. Nothing else shows it again --
        # the old QGridLayout did that as a side effect of addWidget() -- so
        # without this the cards are laid out correctly but never painted.
        card.show()
        if hasattr(card, "set_tiling_enabled"):
            card.set_tiling_enabled(True)
        self.relayout()

    def remove_card(self, uid: str) -> None:
        self._cards.pop(uid, None)
        self._geom.pop(uid, None)

    def card_geometry(self, uid: str) -> Optional[dict]:
        return dict(self._geom[uid]) if uid in self._geom else None

    def card_widget(self, uid: str):
        """The widget currently shown for *uid*, or None."""
        return self._cards.get(uid)

    def set_geometry_map(self, geom: dict) -> None:
        """Restore a saved arrangement (unknown uids ignored, missing ones fall
        back to auto-placement in relayout)."""
        self._geom = {u: dict(g) for u, g in (geom or {}).items()
                      if isinstance(g, dict)}
        self._clamp_geometry()
        self.relayout()

    def geometry_map(self) -> dict:
        """The current arrangement, for persistence."""
        return {u: dict(g) for u, g in self._geom.items()}

    def card_order(self) -> list:
        """Cards in top-to-bottom, left-to-right visual order.

        This is what a drop reorders and what export walks: after free-form
        tiling the *visual* order is no longer the insertion order.
        """
        def key(uid):
            g = self._geom.get(uid) or {"x": 0, "y": 0}
            return (g["y"], g["x"], uid)
        return sorted(self._cards, key=key)

    # -- Layout -----------------------------------------------------------

    def _clamp_geometry(self) -> None:
        """Keep every span inside the current grid."""
        for g in self._geom.values():
            g["x"] = max(0, min(int(g.get("x", 0)), self._cols - 1))
            g["w"] = max(MIN_COL_SPAN,
                         min(int(g.get("w", 1)), self._cols - g["x"]))
            g["y"] = max(0, int(g.get("y", 0)))
            g["h"] = max(MIN_ROW_SPAN, int(g.get("h", 1)))

    def _col_px(self, total_w: int) -> int:
        """Pixel width of one column, gaps accounted for."""
        usable = max(self._cols, 1)
        return max(1, (total_w - GAP * (usable + 1)) // usable)

    def _rect_for(self, g: dict, total_w: int) -> QRect:
        col_px = self._col_px(total_w)
        x = GAP + g["x"] * (col_px + GAP)
        w = g["w"] * col_px + (g["w"] - 1) * GAP
        y = GAP + g["y"] * (self._row_height + GAP)
        h = g["h"] * self._row_height + (g["h"] - 1) * GAP
        return QRect(int(x), int(y), int(w), int(h))

    def _next_free_cell(self) -> dict:
        """First free 1x1 cell, scanned left-to-right then top-to-bottom.

        Scanning in that order is what makes a fresh board fill row by row
        (two across, then the next row) instead of running every card down the
        left edge -- which is what "always start at the next free row" did once
        cards could be placed anywhere.
        """
        if not self._geom:
            return {"x": 0, "y": 0, "w": 1, "h": 1}
        rows = max(g["y"] + g["h"] for g in self._geom.values())
        for y in range(rows + 1):
            for x in range(self._cols):
                if not self._cells_taken(x, y, 1, 1, self._occupied_cells()):
                    return {"x": x, "y": y, "w": 1, "h": 1}
        return {"x": 0, "y": rows, "w": 1, "h": 1}

    def _occupied_cells(self) -> set:
        cells: set = set()
        for g in self._geom.values():
            for yy in range(g["y"], g["y"] + g["h"]):
                for xx in range(g["x"], g["x"] + g["w"]):
                    cells.add((xx, yy))
        return cells

    def relayout(self) -> None:
        """Place every card from its grid geometry, then size the canvas to fit.

        Called on add/remove, on a column or row-height change, and on resize.
        Overlaps are resolved by a first-fit pass that pushes later cards down.
        """
        # Re-entrancy guard. relayout() resizes the canvas to fit its rows, and
        # that resize fires resizeEvent, which calls relayout() again -- so
        # inside a real layout (which may itself push the height back) the two
        # bounce off each other until the recursion limit. While a pass is
        # already running, that pass owns the geometry.
        if self._in_relayout:
            return
        # Drop geometry for cards that no longer exist. Otherwise a restored
        # arrangement naming deleted charts keeps contributing cells (so the
        # canvas never shrinks) and gets written back out by every drag,
        # growing the saved blob a little more each session.
        for uid in [u for u in self._geom if u not in self._cards]:
            del self._geom[uid]
        self._in_relayout = True
        try:
            self._relayout()
        finally:
            self._in_relayout = False

    def _relayout(self) -> None:
        """Body of relayout, run under the re-entrancy guard."""
        self._position_cards()
        self._fit_height()

    def _position_cards(self) -> None:
        """Place every card from its grid geometry, resolving overlaps."""
        self._clamp_geometry()
        total_w = max(self.width(), 1)
        occupied: set = set()
        for uid in self.card_order():
            g = self._geom.get(uid)
            card = self._cards.get(uid)
            if g is None or card is None:
                continue
            # Push down until this card's cells are free.
            while self._cells_taken(g["x"], g["y"], g["w"], g["h"], occupied):
                g["y"] += 1
            for yy in range(g["y"], g["y"] + g["h"]):
                for xx in range(g["x"], g["x"] + g["w"]):
                    occupied.add((xx, yy))
            card.setGeometry(self._rect_for(g, total_w))

    def _fit_height(self) -> None:
        """Ask to be exactly tall enough for the rows in use.

        Only the size HINT is set. Deliberately no self.resize(): the canvas
        lives inside a QScrollArea, so an explicit resize here bounces against
        the scroll area's own sizing, fires another resizeEvent, and loops
        until the recursion limit. Letting the layout honour the hint settles
        after one round trip.
        """
        rows = 0
        for g in self._geom.values():
            rows = max(rows, g["y"] + g["h"])
        height = GAP * 2 + rows * self._row_height + max(0, rows - 1) * GAP
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
            if self.height() < height:
                self.resize(self.width(), height)

    def _cells_taken(self, x, y, w, h, occupied) -> bool:
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if (xx, yy) in occupied:
                    return True
        return False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Reposition ONLY. Calling the full relayout() here would re-assert the
        # height we just reacted to, and inside the scroll area that turns into
        # an endless resize/relayout cycle.
        self._position_cards()

    # -- Drag / resize ----------------------------------------------------

    def begin_drag(self, uid: str, global_pos: QPoint) -> None:
        """Start a move gesture for *uid*."""
        if uid not in self._geom or uid not in self._cards:
            return
        self._drag = {
            "mode": "move", "uid": uid,
            "grab": self._cards[uid].mapToGlobal(QPoint(0, 0)) - global_pos,
            "origin": global_pos,
            "start": dict(self._geom[uid]),
        }
        self.grabMouse()

    def begin_resize(self, uid: str, global_pos: QPoint) -> None:
        """Start a resize gesture for *uid*."""
        if uid not in self._geom:
            return
        self._drag = {
            "mode": "resize", "uid": uid,
            "grab": global_pos,
            "start": dict(self._geom[uid]),
        }
        self.grabMouse()

    def _end_drag(self) -> None:
        if self._drag is not None:
            self.releaseMouse()
            self._drag = None
            # Commit + emit only on a real gesture, so a stray click on the
            # grip does not churn the persisted layout.
            self.layout_changed.emit(self.geometry_map(), self._cols)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        d = self._drag
        g = self._geom.get(d["uid"])
        if g is None:
            return
        col_px = self._col_px(max(self.width(), 1))
        if d["mode"] == "move":
            top_left = event.globalPosition().toPoint() + d["grab"]
            g["x"] = self._clamp((top_left.x() - GAP) // (col_px + GAP),
                                 0, self._cols - g["w"])
            g["y"] = max(0, (top_left.y() - GAP) // (self._row_height + GAP))
        else:
            delta = event.globalPosition().toPoint() - d["grab"]
            dw = int(round(delta.x() / float(col_px + GAP)))
            dh = int(round(delta.y() / float(self._row_height + GAP)))
            g["w"] = max(MIN_COL_SPAN, min(d["start"]["w"] + dw,
                                           self._cols - d["start"]["x"]))
            g["h"] = max(MIN_ROW_SPAN, d["start"]["h"] + dh)
        self.relayout()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._end_drag()

    @staticmethod
    def _clamp(v, lo, hi):
        return max(lo, min(int(v), hi))
