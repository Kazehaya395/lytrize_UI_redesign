"""desktop/widgets/table_index_header.py -- Shared row-index gutter sizing.

Every table preview in the app (the Upload page grid, the Data Cleaner
previews, the Dtype Transformer before/after table, the Column Manager
preview) shows a vertical header of 1-based row numbers. That gutter used
to be sized by two different ad-hoc guesses:

  * ``UploadPage._auto_fit_index_width`` used a hand-rolled formula
    (``26 + digits * 8``, floored at 36px) computed from the FULL loaded
    dataset length. A 149,116-row file therefore reserved 74px for a
    gutter that only ever draws "1".."10" while the preview mode is set to
    "Top 10" -- the first data column visibly lost a chunk of width.
  * every other panel hard-coded ``setFixedWidth(36)``, which is both too
    wide for a 10-row preview and too NARROW for a >=4-digit index (the
    numbers get clipped).

Both also ignored the two things that actually decide the width: the font
the index numbers are painted in, and how many rows are currently
*displayed*. This module centralises that into one measured helper so
every preview behaves the same:

    from desktop.widgets.table_index_header import auto_fit_index_header
    auto_fit_index_header(self.table)   # after every model/data change

Sizing is measured, not guessed, and is intentionally compact: the gutter
is a reference column, so it gets only the breathing room a bold number
actually needs (a few px each side) plus a sane floor/ceiling.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QHeaderView

log = logging.getLogger(__name__)

# The index gutter is styled by the global QSS
# (`QHeaderView::section { padding: 7px 8px; font-weight: 700 }`), so a
# section never needs more than its glyphs plus a little breathing room.
# These bounds keep the gutter from collapsing to an unreadable sliver on a
# 1-2 row preview, or ballooning on a 7+ digit index.
MIN_INDEX_HEADER_WIDTH = 30
MAX_INDEX_HEADER_WIDTH = 96

# Horizontal breathing room around the number, in px (6 per side). The
# QSS padding of 8px per side is deliberately NOT added on top: Qt
# reports a section size that already includes the stylesheet padding when
# the view is asked to size a section, and double-counting it is exactly
# what made the old formula overshoot.
_PADDING_PX = 12


def _index_font(table) -> QFont:
    """The font the row numbers are actually drawn in.

    ``DataFrameModel.headerData`` hands the vertical header a bold 10pt
    font via ``Qt.FontRole``, so measuring with the view's default font
    would under-measure. Take the header's own font when the model
    supplies one, otherwise fall back to the table font.
    """
    header = table.verticalHeader()
    model = table.model()
    if model is not None:
        try:
            idx = model.index(0, 0)
            font = model.headerData(
                0, Qt.Orientation.Vertical, Qt.ItemDataRole.FontRole
            )
            if isinstance(font, QFont):
                return font
        except Exception:
            pass
    try:
        font = header.font()
        if font is not None:
            return font
    except Exception:
        pass
    return table.font()


def auto_fit_index_header(table, rows: int | None = None) -> int:
    """Size (and centre) *table*'s row-index gutter to the numbers it shows.

    *rows* overrides the displayed row count; by default the model is
    asked. The result is the applied pixel width, which makes this easy to
    assert on in tests.

    Every call site should invoke this after the table's data or preview
    mode changes -- the displayed row count (and therefore the widest
    index label) changes with it.
    """
    if table is None:
        return 0
    header = table.verticalHeader()
    if header is None:
        return 0

    if rows is None:
        try:
            rows = int(table.model().rowCount()) if table.model() is not None else 0
        except Exception:
            rows = 0
    rows = max(0, int(rows or 0))

    # The header shows 1-based row numbers (DataFrameModel returns
    # ``section + 1``), so the widest label is the row count itself.
    widest = max(1, rows)
    try:
        fm = QFontMetrics(_index_font(table))
        text_w = int(fm.horizontalAdvance(str(widest)))
    except Exception:
        # Defensive: a bad/absent font must not take the table down, so
        # fall back to a per-digit estimate close to a 10pt bold digit.
        text_w = 7 * len(str(widest))

    width = max(MIN_INDEX_HEADER_WIDTH, min(MAX_INDEX_HEADER_WIDTH, text_w + _PADDING_PX))

    try:
        # Keep the gutter fixed: the point of the helper is that the
        # header does NOT participate in the interactive/stretched sizing
        # that would otherwise re-widen it on every model reset.
        header.setSectionResizeMode(QHeaderView.Fixed)
    except Exception:
        pass

    # DO NOT call setMinimumSectionSize() here. On a QHeaderView that
    # property is the floor for the section size on BOTH axes, and the
    # vertical header's section size IS the row height -- so setting it to
    # the gutter width (53px) silently inflated every data row to 53px.
    # That is exactly the regression this replaced: "compact the index
    # column" must never come at the cost of taller rows.
    #
    # The gutter width is constrained purely by setFixedWidth(), and the row
    # height stays under the caller's control via setDefaultSectionSize()
    # (28px on the Upload page). minimumSectionSize is left at whatever the
    # view/table established, which only ever acts as a floor on a value
    # that is already smaller.
    header.setFixedWidth(width)
    header.setDefaultAlignment(Qt.AlignCenter)
    return width
