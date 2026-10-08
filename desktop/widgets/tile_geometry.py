"""desktop/widgets/tile_geometry.py -- placement maths for dashboard cards.

A thin re-export of :mod:`modules.canvas_geometry`. The maths used to live here,
in ``desktop``, and the HTML writer grew its own copy. That is exactly how the
live canvas and the exported file end up disagreeing about the same session: a
clamp added to one is a clamp missing from the other, and the user sees a card
placed one way on the board and another way in the file. There is now one
implementation, in ``backend``, imported by both.

The functions stay pure (no Qt, no widgets) so the arrangement model remains
testable without a display -- which matters, because a QWebEngineView cannot be
constructed headlessly and the canvas widget itself therefore cannot be unit
tested.

See ``backend/modules/canvas_geometry.py`` for the units and the rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The backend package is imported as top-level ``modules.*`` throughout this
# codebase (that is how export.py, export_service.py and the page reach it), so
# reach it the same way instead of assuming the repo root is on sys.path.
_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from modules.canvas_geometry import (  # noqa: E402,F401
    DEFAULT_H,
    DEFAULT_TITLE_H,
    DEFAULT_W,
    GAP_FRAC,
    GAP_PX,
    MIN_H,
    MIN_W,
    TITLE_UID,
    auto_place,
    canvas_height,
    clamp,
    clamp_all,
    ensure_title_band,
    from_grid,
    is_grid_shaped,
    overlaps,
    tidy,
    title_band_height,
    visual_order,
)
