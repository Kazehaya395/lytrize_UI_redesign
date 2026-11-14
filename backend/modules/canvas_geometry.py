"""backend/modules/canvas_geometry.py -- free-form placement maths for the canvas.

The dashboard canvas is a WINDOW MANAGER, not a spreadsheet: a card can be put
anywhere and made any size, including on top of its neighbour. That rules out
CSS grid as the placement model -- a grid cell is the smallest thing a card can
be, so "drag it slightly left" has no meaning and the handle springs back.

Units
-----
========  ==================================  ==========================
field     meaning                             why not pixels
========  ==================================  ==========================
``x``     fraction of the canvas WIDTH (0-1)  a report is read at many
``w``     fraction of the canvas WIDTH (0-1)  window widths; fractions
``y``     pixels from the canvas top          keep the proportions the
``h``     pixels                              user chose, forever
========  ==================================  ==========================

Horizontal position is therefore RESPONSIVE (narrow the window and cards keep
their relative widths) while vertical position is ABSOLUTE (a card is exactly
as tall as the user made it, because a chart's height is not something to scale
with the viewport). The canvas itself is as tall as its lowest card.

This module is the single AUTHORITY for what a placement is allowed to be. The
page's JavaScript overlay only PROPOSES one (it measures live pixels and
reports where a card was dropped); whatever comes back is clamped here before it
is stored or exported. That split is deliberate: the page knows where the
pixels are, this knows what is legal.

It lives in ``backend`` rather than ``desktop`` so the HTML writer and the
PySide page import the SAME functions. When they each had their own copy, a
clamp added to one was a clamp missing from the other -- and the canvas and the
exported file disagreed about the same session. Pure functions, no Qt, so the
whole arrangement model is testable without a display (which matters: a
QWebEngineView cannot be constructed headlessly, so the canvas widget itself
cannot be unit tested).
"""

from __future__ import annotations

from typing import Iterable, Optional

# A card smaller than this is a mistake, not a design: the grip and the four
# resize handles stop fitting inside it and the chart has no room to draw.
MIN_W = 0.12
MIN_H = 120.0

# Floor for the TITLE band only. The heading card is sized by its own text
# (see ``title_band_height``), so it must be allowed below the chart floor --
# clamping it to MIN_H is what turned a one-line heading into a tall mostly-
# empty box (the "huge fixed gap above the text" report). Keep in sync with
# the TITLE_MIN_H mirror in ``report_editor``'s clampGeom.
TITLE_MIN_H = 24.0

# What a card gets when it has never been placed. Half the canvas wide reads as
# a deliberate two-up board rather than one lonely full-bleed chart.
DEFAULT_W = 0.5
DEFAULT_H = 400.0

# A slicer is a control, not a chart, so it starts NARROWER than DEFAULT_W: a
# dropdown or checkbox list at half the canvas read as a huge empty card
# spilling over its neighbours (the "too much width" screenshot). Still wide
# enough for a readable value list. The user can drag/resize it like any card.
SLICER_DEFAULT_W = 0.24

# The report heading is a CARD on the free canvas, exactly like a chart or a
# KPI, so it carries its own key in the arrangement rather than living outside
# it. Reserved-looking because no chart can ever be handed this id.
TITLE_UID = "__title__"

# Default height of the title band, in pixels. Derived from the report's own
# title size (px) with room for the metadata line, so a 72pt heading is not
# clipped by a band sized for 28pt. See ``title_band_height``.
DEFAULT_TITLE_H = 96.0


def title_band_height(title_size, show_meta: bool = True) -> float:
    """A title band tall enough for *title_size* px -- heading only.

    Not a constant: the heading is a CARD now, and a card with a fixed height
    crops a heading the user has made larger -- the same failure a fixed chart
    height had before the canvas became free.

    ``show_meta`` is kept in the signature for callers, but no longer adds
    height: the timestamp moved to the extreme bottom edge of the dashboard,
    so the title card never reserves room for it.
    """
    size = max(8.0, _num(title_size, 28.0))
    # A heading is roughly 1.35x its point size in line box. No card padding
    # is reserved here either -- the title may touch the card edges, so the
    # band is the text height only. The floor is the title's own (24px), NOT
    # the canvas-wide MIN_H (120px): that floor is for charts and KPIs, and
    # applying it to the heading is exactly the "huge fixed gap" report.
    h = size * 1.35
    return round(max(TITLE_MIN_H, min(h, 400.0)), 2)


# Spacing used when REBUILDING an arrangement (tidy / first placement). While
# the user drags, no gap is enforced at all -- that is the whole point.
GAP_PX = 24.0
GAP_FRAC = 0.02

EPS = 1e-6


def _num(value, default: float) -> float:
    """Coerce to a finite float, falling back to *default*.

    Geometry arrives from three places that can all be wrong: stored session
    JSON, a drag, and a keyboard nudge. NaN is the dangerous one -- it compares
    false against everything, so a NaN slips through a naive ``min``/``max``
    clamp and reaches CSS as ``left: NaN%``, which the browser discards and the
    card vanishes.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float(default)
    if f != f or f in (float("inf"), float("-inf")):
        return float(default)
    return f


def clamp(g: dict, min_h: float = MIN_H) -> dict:
    """Force one placement inside the canvas, as a new dict.

    The single choke point for validation. A card can never be dragged partly
    off the right edge or shrunk to nothing, and can never sit above the canvas
    top -- both of which produce a card that is unreachable or invisible.
    """
    w = max(MIN_W, min(1.0, _num(g.get("w"), DEFAULT_W)))
    x = max(0.0, min(_num(g.get("x"), 0.0), 1.0 - w))
    return {
        "x": round(x, 5),
        "y": round(max(0.0, _num(g.get("y"), 0.0)), 2),
        "w": round(w, 5),
        "h": round(max(min_h, _num(g.get("h"), DEFAULT_H)), 2),
    }


def clamp_all(geom: Optional[dict]) -> dict:
    """Clamp a whole arrangement, dropping entries that are not placements."""
    out: dict = {}
    for uid, g in (geom or {}).items():
        if isinstance(g, dict):
            # The title band carries its own, smaller floor: see TITLE_MIN_H.
            out[uid] = clamp(g, min_h=(TITLE_MIN_H if uid == TITLE_UID else MIN_H))
    return out


def visual_order(geom: dict) -> list:
    """uids in top-to-bottom, left-to-right order.

    This is what export walks and what the reorder buttons act on. After
    free-form placement the *visual* order is no longer the insertion order, so
    anything meaning "the card above this one" has to read this rather than
    dict order. y is compared exactly -- two cards dropped on the same row are
    then ordered by x, which is the tie-break the eye already applies.
    """
    return sorted(geom, key=lambda u: (geom[u]["y"], geom[u]["x"], str(u)))


def canvas_height(geom: Optional[dict], pad: float = 0.0) -> float:
    """Height the canvas needs to show every card in *geom*.

    Absolute positioning takes the cards out of flow, so the container has to be
    told how tall it is or everything below the fold is simply unreachable.
    """
    if not geom:
        return float(pad)
    return round(max(g["y"] + g["h"] for g in geom.values()) + pad, 2)


def overlaps(geom: dict) -> list:
    """Pairs of tiles whose rectangles intersect.

    Legal on a free canvas -- cards are allowed to stack, and z-order is DOM
    order -- but it is worth being able to ask, and it is what tells a "tidied"
    board apart from a tangled one.
    """
    clashes = []
    items = list(geom.items())
    for i, (uid_a, a) in enumerate(items):
        for uid_b, b in items[i + 1:]:
            if (a["x"] < b["x"] + b["w"] - EPS and b["x"] < a["x"] + a["w"] - EPS
                    and a["y"] < b["y"] + b["h"] - EPS
                    and b["y"] < a["y"] + a["h"] - EPS):
                clashes.append((uid_a, uid_b))
    return clashes


def tidy(geom: dict, gap_px: float = GAP_PX, gap_frac: float = GAP_FRAC) -> dict:
    """Re-flow every tile into a clean board, keeping the current visual order.

    The escape hatch for a canvas that has been dragged into a tangle, and the
    only layout control left once the column count went away. It is a genuine
    RE-FLOW rather than a snap-to-grid: each card keeps the width and height
    the user gave it, cards are packed left to right and wrapped at the canvas
    edge, and a row's height is set by its tallest card. So a deliberately large
    card survives "tidy" instead of being quietly shrunk back to a cell.
    """
    clean = clamp_all(geom)
    out: dict = {}
    x = 0.0          # left edge the NEXT card would take
    lead = 0.0       # gutter to apply to it; 0 for the first card in a row
    y = 0.0
    row_bottom = 0.0
    for uid in visual_order(clean):
        g = clean[uid]
        # Fit test ignores the gutter, because a gutter belongs BETWEEN two
        # cards -- there is none after the last one in a row. Testing the
        # guttered position instead makes two half-width cards miss each other
        # by 2% and stack, which is how a "fill two across" board ends up one
        # card per row.
        if x > EPS and x - lead + g["w"] > 1.0 + EPS:
            y = row_bottom + gap_px
            x = 0.0
            lead = 0.0
        out[uid] = {"x": round(x, 5), "y": round(y, 2),
                    "w": g["w"], "h": g["h"]}
        row_bottom = max(row_bottom, y + g["h"])
        x += g["w"] + gap_frac
        lead = gap_frac
    return out


def auto_place(uids: Iterable[str], existing: Optional[dict] = None,
               gap_px: float = GAP_PX, gap_frac: float = GAP_FRAC,
               w: float = DEFAULT_W, h: float = DEFAULT_H,
               floor_px: float = 0.0) -> dict:
    """Place *uids* that have no geometry yet, below whatever is already there.

    Scanned left-to-right then top-to-bottom, so a fresh board fills two across
    and then starts a new row instead of running every new card down the left
    edge. ``existing`` is left untouched apart from clamping -- only the missing
    ids are added -- so opening a saved session cannot reshuffle a board the
    user had already arranged.

    ``floor_px`` pushes new cards below a mark that is NOT in ``existing``. The
    dashboard needs it because charts and KPIs are auto-placed from two separate
    maps, and both maps start empty on a fresh session: each would then place its
    own first card at y=0 and the KPI row would end up underneath the chart row,
    invisible. Passing the other group's height as the floor keeps the two bands
    stacked instead of coincident.
    """
    out = clamp_all(existing)
    missing = [u for u in uids if u not in out]
    if not missing:
        return out
    w = max(MIN_W, min(1.0, _num(w, DEFAULT_W)))
    h = max(MIN_H, _num(h, DEFAULT_H))
    floor = _num(floor_px, 0.0)
    y = canvas_height(out, gap_px) if out else floor
    if out:
        y = max(y, floor)
    x = 0.0
    lead = 0.0
    row_bottom = y
    for uid in missing:
        # Same gutter accounting as tidy(): a gutter sits between cards, so the
        # last card in a row is not charged for one it would never show.
        if x > EPS and x - lead + w > 1.0 + EPS:
            y = row_bottom + gap_px
            x = 0.0
            lead = 0.0
        out[uid] = {"x": round(x, 5), "y": round(y, 2),
                    "w": round(w, 5), "h": round(h, 2)}
        row_bottom = max(row_bottom, y + h)
        x += w + gap_frac
        lead = gap_frac
    return clamp_all(out)


def ensure_title_band(geom: Optional[dict], height: float = DEFAULT_TITLE_H,
                      gap_px: float = GAP_PX) -> tuple[dict, dict]:
    """Make sure the board has a title band, and return ``(geom, title)``.

    The report heading used to live in a ``.report-header`` block ABOVE the
    canvas, outside it. That made it the one thing on the board with no grip, no
    resize handles and no place in the saved arrangement -- you could drag every
    chart and every KPI and the title stayed exactly where the template put it,
    which is the "the title is not a card like the others" report.

    It is a card now, keyed :data:`TITLE_UID`, on the same canvas as everything
    else. The catch is the FIRST board this runs on: every saved arrangement
    starts its first card at y=0, which is exactly where the new title band
    lands. So on a geometry that has no title yet, the existing cards are
    shifted down by one band. Their order, widths and heights are untouched --
    only their y moves -- and it happens once, because the title is stored with
    them afterwards and this branch is then never taken again.

    Returns the (possibly shifted) arrangement and the title's own placement.
    """
    clean = clamp_all(geom)
    existing = clean.get(TITLE_UID)
    if isinstance(existing, dict):
        return clean, clamp(existing, min_h=TITLE_MIN_H)

    # TITLE_MIN_H, not MIN_H: the band is the heading's line box, and the
    # canvas-wide chart floor is exactly what made it a mostly-empty box.
    band_h = max(TITLE_MIN_H, _num(height, DEFAULT_TITLE_H))
    shift = band_h + _num(gap_px, GAP_PX)
    out: dict = {}
    for uid, g in clean.items():
        # Preserve any placement already below the title band. Saved free
        # boards created during the title migration can lack the marker while
        # carrying valid pixel coordinates; shifting every card on each load
        # made slicers and charts drift even though their geometry was sound.
        y = max(g["y"], shift)
        out[uid] = {"x": g["x"], "y": round(y, 2),
                    "w": g["w"], "h": g["h"]}
    title = {"x": 0.0, "y": 0.0, "w": 1.0, "h": round(band_h, 2)}
    out[TITLE_UID] = title
    return clamp_all(out), title


def from_grid(geom: Optional[dict], cols: int = 2, row_h: float = DEFAULT_H,
              gap_px: float = GAP_PX) -> dict:
    """Convert a saved GRID placement into free-form units.

    Sessions saved before the free canvas store ``{x, y, w, h}`` in grid cells.
    Dropping those on the floor would silently reset every saved board to
    auto-flow, so they are converted once, on load, using the card height and
    gap of the moment. The result is an approximation -- a cell-based board has
    no single true pixel size -- but it lands every card roughly where the user
    last saw it, which is the point.
    """
    cols = max(1, int(cols))
    pitch = max(1.0, _num(row_h, DEFAULT_H) + _num(gap_px, GAP_PX))
    out: dict = {}
    for uid, g in (geom or {}).items():
        if not isinstance(g, dict):
            continue
        x = max(0, int(_num(g.get("x"), 0)))
        y = max(0, int(_num(g.get("y"), 0)))
        w = max(1, int(_num(g.get("w"), 1)))
        h = max(1, int(_num(g.get("h"), 1)))
        x = min(x, cols - 1)
        w = min(w, cols - x)
        out[uid] = clamp({
            "x": x / cols,
            "y": y * pitch,
            "w": w / cols,
            "h": max(MIN_H, h * pitch - _num(gap_px, GAP_PX)),
        })
    return out


def is_grid_shaped(geom: Optional[dict], cols: int = 2) -> bool:
    """True when *geom* looks like saved grid cells rather than free units.

    Only used as a tie-break on load, for a blob written before the canvas
    carried a mode marker. Free ``x``/``w`` are fractions of the canvas width,
    so anything that is not a whole number of columns gives it away; grid ``x``
    is always a small integer.

    ``h`` is the deciding field. A grid cell's height is a ROW-SPAN -- a small
    integer (1, 2, ...). A free card's height is PIXELS and can never be below
    :data:`MIN_H` (120), so a full-width free chart (``w=1, x=0``) is told apart
    from a one-column grid cell purely by its height. ``y`` must be a small
    integer too: grid stores a row index, free stores pixels. Without these two
    checks a full-width free board (or any single-chart report) was misread as a
    grid blob, and ``from_grid`` then multiplied its pixel ``y``/``h`` by the row
    pitch -- which is what reset a dragged chart to a wildly wrong position.
    """
    items = [g for g in (geom or {}).values() if isinstance(g, dict)]
    if not items:
        return False
    cols = max(1, int(cols))
    # A row-span is a small count of rows; anything at/above MIN_H is pixels.
    _MAX_GRID_H = int(MIN_H)  # 120
    _MAX_GRID_Y = 4096  # row index; free y is unbounded pixels
    for g in items:
        w = _num(g.get("w"), 1.0)
        x = _num(g.get("x"), 0.0)
        h = _num(g.get("h"), 1.0)
        y = _num(g.get("y"), 0.0)
        if w != int(w) or not (1 <= w <= cols):
            return False
        if x != int(x) or not (0 <= x <= cols - 1):
            return False
        # h/y in pixels (any card, always) => free units, never a grid cell.
        if h != int(h) or not (1 <= h < _MAX_GRID_H):
            return False
        if y != int(y) or not (0 <= y <= _MAX_GRID_Y):
            return False
    return True
