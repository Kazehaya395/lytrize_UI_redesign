"""desktop/widgets/dataframe_model.py -- QAbstractTableModel for Polars DataFrames.

Provides a lazy, model-backed view of a Polars DataFrame for QTableView.
Large datasets stay responsive because only the visible rows are fetched
from the DataFrame on demand.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import polars as pl
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from desktop.theme import resolve_color

log = logging.getLogger(__name__)


class DataFrameModel(QAbstractTableModel):
    """A QAbstractTableModel that wraps a Polars DataFrame.

    The model reads rows lazily from the DataFrame, so large datasets
    (hundreds of thousands of rows) remain responsive in a QTableView.
    """

    def __init__(self, df: Optional[pl.DataFrame] = None, parent=None) -> None:
        super().__init__(parent)
        if df is not None and not isinstance(df, pl.DataFrame):
            try:
                df = pl.from_pandas(df)
            except Exception:
                pass
        self._df = df if df is not None else pl.DataFrame()
        self._row_limit: Optional[int] = None
        self._colors_cache: dict = {}
        self._colors_cache_mode: str = ""
        from desktop.theme import current_mode as _cm
        self._current_theme_mode: str = _cm()

    # -- Public API ---------------------------------------------------------

    def set_dataframe(self, df: pl.DataFrame) -> None:
        """Replace the underlying DataFrame and notify views."""
        self.beginResetModel()
        if df is not None and not isinstance(df, pl.DataFrame):
            try:
                df = pl.from_pandas(df)
            except Exception:
                pass
        self._df = df if df is not None else pl.DataFrame()
        self._row_limit = None
        self.endResetModel()

    # -- Row limit --------------------------------------------------------
    # A transient cap on the model's visible row count, used by the column
    # content-fit pass so resizeColumnsToContents() measures a head slice
    # instead of walking a million-row frame on the UI thread. Cleared back
    # to None afterwards; rowCount honours it, everything else is unchanged.
    def set_row_limit(self, limit: Optional[int]) -> None:
        """Temporarily cap the reported row count (None = no limit)."""
        try:
            limit = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            limit = None
        if limit is not None and limit < 0:
            limit = None
        self.beginResetModel()
        self._row_limit = limit
        self.endResetModel()

    def on_theme_changed(self) -> None:
        """Invalidate per-mode header colors and re-fetch them on next
        paint (called after a dark/light toggle so the header row and
        row-index gutter re-resolve their colors)."""
        from desktop.theme import current_mode as _cm
        self._current_theme_mode = _cm()
        self._colors_cache_mode = ""
        self.beginResetModel()
        self.endResetModel()

    def dataframe(self) -> pl.DataFrame:
        """Return the underlying DataFrame."""
        return self._df

    # -- QAbstractTableModel overrides --------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        n = getattr(self._df, "height", None)
        if n is None:
            n = len(self._df)
        if self._row_limit is not None and self._row_limit < n:
            return self._row_limit
        return n

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._df.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:  # noqa: N802
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row >= self._df.height or col >= len(self._df.columns):
            return None

        col_name = self._df.columns[col]
        try:
            value = self._df.item(row, col)
        except Exception:
            return None

        if role == Qt.DisplayRole:
            if value is None:
                return ""
            return str(value)

        if role == Qt.EditRole:
            if value is None:
                return ""
            return str(value)

        if role == Qt.TextAlignmentRole:
            # Right-align numeric columns
            try:
                dtype = self._df.schema[col_name]
                if dtype.is_numeric():
                    return int(Qt.AlignRight | Qt.AlignVCenter)
            except Exception:
                pass
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:  # noqa: N802
        # Column headers: make them bold (brand styling also comes from the
        # global QSS on QHeaderView::section, but the font role keeps the
        # emphasis even if a caller overrides the QSS).
        # Use theme colors for header text (read from the global theme tokens
        # so the header reads correctly in both dark and light modes).
        # Colors are cached per mode to avoid repeated resolve_color calls
        # during the relayout storm triggered by a theme toggle.
        if role in (Qt.FontRole, Qt.ForegroundRole, Qt.BackgroundRole) and orientation in (Qt.Horizontal, Qt.Vertical):
            colors = self._header_colors
            if orientation == Qt.Horizontal and role in (Qt.FontRole, Qt.ForegroundRole):
                if 0 <= section < len(self._df.columns):
                    if role == Qt.FontRole:
                        return QFont("Inter", 10, QFont.Weight.Bold)
                    return colors.get("header_text")
            elif orientation == Qt.Vertical:
                if role == Qt.ForegroundRole:
                    return colors.get("index_text")
                if role == Qt.BackgroundRole:
                    return colors.get("index_bg")
                if role == Qt.FontRole:
                    return QFont("Inter", 10, QFont.Weight.Bold)
        if role != Qt.DisplayRole:
            return None

        if orientation == Qt.Horizontal:
            if 0 <= section < len(self._df.columns):
                return str(self._df.columns[section])
        elif orientation == Qt.Vertical:
            return section + 1

        return None

    @property
    def _header_colors(self) -> dict:
        """Cache theme colors per mode to avoid repeated resolve_color calls."""
        mode = self._current_theme_mode
        if self._colors_cache_mode != mode:
            self._colors_cache_mode = mode
            self._colors_cache = {
                "header_text": QColor(resolve_color("header_text")),
                "index_text": QColor(resolve_color("index_text")),
                "index_bg": QColor(resolve_color("index_bg")),
            }
        return self._colors_cache

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:  # noqa: N802
        """Sort the DataFrame by the given column."""
        if column < 0 or column >= len(self._df.columns):
            return

        col_name = self._df.columns[column]
        ascending = order == Qt.AscendingOrder

        self.beginResetModel()
        try:
            self._df = self._df.sort(col_name, descending=not ascending)
        except Exception as exc:
            log.warning("DataFrameModel.sort failed: %s", exc)
        self.endResetModel()
