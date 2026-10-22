"""desktop/widgets/resizable_table.py -- Readable, scrollable preview tables.

The bug this replaces
---------------------
Every preview table in the app sized its columns with ``QHeaderView.Stretch``
(Upload's ``setStretchLastSection(True)`` was a close cousin). ``Stretch``
divides the FULL viewport width by the column count and forces every column
into the result, so the wider the dataset the narrower each column gets: a
23-column ``fraudTrain.csv`` preview rendered ~30px cells of ``20..`` / ``fr..``
with no horizontal scrollbar -- the "unreadable / can't tell columns apart" report.

Two separate mistakes were bundled there:

* **Stretch is the wrong policy for wide data.** It removes the horizontal
  scrollbar by design -- the whole point of Stretch is "never scroll, always
  fit". A 23-column table at 1366px must scroll, and each column must keep a
  readable width.
* **``QTableWidget`` materialises every cell.** On a 1.3M-row frame that is a
  multi-million-object allocation on the UI thread, the second half of the
  "choppy / laggy" report. ``QTableView`` over the existing lazy
  ``DataFrameModel`` fetches only the visible rows.

The fix, one widget
-------------------
``ResizableTableView`` is a ``QTableView`` configured so columns are
``Interactive`` (user-draggable, never stretched), each with a sane default
and a minimum width; the horizontal scrollbar is always available;
content-fit is capped to the first visible rows; and the row-index gutter
keeps its compact measured width from ``table_index_header``.

``apply_table_defaults(table)`` does the same for a pre-existing
``QTableWidget`` (the Data Cleaner / Dtype / Column Manager previews build
their rows with widget items), which fixes the squeeze without re-platforming
those panels in the same pass.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableView

from desktop.widgets.dataframe_model import DataFrameModel
from desktop.widgets.table_index_header import auto_fit_index_header

log = logging.getLogger(__name__)

# Column geometry. A preview cell needs room for a full-ish value plus the
# ellipsis; below ~96px a date or a float truncates to nothing, which is the
# exact failure this widget exists to stop.
DEFAULT_SECTION_WIDTH = 140
MIN_SECTION_WIDTH = 96
MAX_SECTION_WIDTH = 420

# resizeColumnsToContents() measures per column over the data, so on a
# 1.3M-row frame it is a multi-second UI-thread stall. Only the first N rows
# are sampled: they are what the user actually sees and a fine width estimate
# for the rest.
_CONTENT_FIT_ROW_SAMPLE = 60


def apply_table_defaults(table) -> bool:
    """Make any ``QTableView``/``QTableWidget`` readable and scrollable.

    Safe to call repeatedly and on a table that already holds data; each
    call is idempotent. Returns True when a horizontal header was found.
    """
    if table is None:
        return False
    header = table.horizontalHeader()
    if header is None:
        return False

    # Interactive, NOT Stretch: columns keep their own width and the table
    # scrolls horizontally when they don't all fit. setStretchLastSection is
    # cleared too -- it is Stretch's little brother and stretched exactly the
    # last column, which is the one the user most wanted to read.
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setStretchLastSection(False)
    # Cascading means "a column changed, so re-resize every column after it",
    # which made a single drag reflow the whole table.
    header.setCascadingSectionResizes(False)

    header.setDefaultSectionSize(DEFAULT_SECTION_WIDTH)
    # The floor that keeps every column legible. On QHeaderView this is a
    # per-axis property and the HORIZONTAL header owns the column widths, so
    # it does not touch the vertical header's row height (the trap
    # auto_fit_index_header documents).
    header.setMinimumSectionSize(MIN_SECTION_WIDTH)

    # Pixel-per-pixel scrolling: QTableView's default (ScrollPerItem) jumps a
    # whole column at a time, so a 23-column table felt like it teleported.
    table.setHorizontalScrollMode(QTableView.ScrollPerPixel)
    table.setVerticalScrollMode(QTableView.ScrollPerPixel)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    table.setWordWrap(False)
    return True


def fit_columns_to_content(table, max_rows: int = _CONTENT_FIT_ROW_SAMPLE,
                           *, max_width: int = MAX_SECTION_WIDTH) -> None:
    """Size columns to their visible content, capped.

    Calls ``resizeSections(ResizeToContents)`` and then clamps each section
    into ``[MIN_SECTION_WIDTH, max_width]`` so one long free-text column
    cannot eat the whole viewport. When *max_rows* is smaller than the model's
    row count and the model supports ``set_row_limit``, only the head slice is
    measured so the pass never touches more than that many rows.
    """
    if table is None:
        return
    header = table.horizontalHeader()
    if header is None:
        return

    model = table.model()
    limited = False
    try:
        if model is not None and max_rows and hasattr(model, "set_row_limit"):
            total = int(model.rowCount())
            if total > int(max_rows):
                model.set_row_limit(int(max_rows))
                limited = True
        header.resizeSections(QHeaderView.ResizeToContents)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("fit_columns_to_content: measure failed: %s", exc)
    finally:
        if limited and model is not None:
            try:
                model.set_row_limit(None)
            except Exception:
                pass

    # resizeSections(ResizeToContents) flips every section's resize MODE to
    # ResizeToContents, which would silently replace the Interactive policy
    # this module installs (and disable the user's drag-resize); and clearing
    # the row limit resets the model, which re-applies default header modes.
    # So the policy is restored AFTER both, right before the clamped widths.
    try:
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
    except Exception:
        pass

    try:
        for i in range(header.count()):
            w = int(header.sectionSize(i))
            if w <= 0:
                continue
            header.resizeSection(i, max(MIN_SECTION_WIDTH, min(int(max_width), w)))
    except Exception:  # pragma: no cover - defensive
        pass



class ResizableTableView(QTableView):
    """A ``QTableView`` over a lazy ``DataFrameModel``, readable by default.

    Use ``set_dataframe(df)`` to load data. It applies the readable-column
    policy immediately and sizes the columns to a head slice, so a 23-column
    table arrives scrollable and legible rather than squeezed.
    """

    def __init__(self, df=None, parent=None) -> None:
        super().__init__(parent)
        self._model = DataFrameModel(df)
        self.setModel(self._model)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setEditTriggers(QTableView.NoEditTriggers)
        self.setSortingEnabled(True)
        # Row height: fixed and compact, so the vertical header's section
        # size stays under our control (see auto_fit_index_header's warning
        # about minimumSectionSize inflating rows).
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(28)
        apply_table_defaults(self)

    # -- API ----------------------------------------------------------------

    def set_dataframe(self, df) -> None:
        """Load *df* and re-apply readable column geometry."""
        self._model.set_dataframe(df)
        self.refresh_layout()

    def dataframe(self):
        return self._model.dataframe()

    def refresh_layout(self) -> None:
        """Re-size the gutter and fit the columns to the current data."""
        try:
            auto_fit_index_header(self)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("refresh_layout: index gutter sizing failed: %s", exc)
        fit_columns_to_content(self)

    def on_theme_changed(self) -> None:
        try:
            self._model.on_theme_changed()
        except Exception:  # pragma: no cover - defensive
            pass
