"""desktop/widgets/dataframe_model.py -- QAbstractTableModel for pandas DataFrames.

Provides a lazy, model-backed view of a pandas DataFrame for QTableView.
Large datasets stay responsive because only the visible rows are fetched
from the DataFrame on demand.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont

log = logging.getLogger(__name__)


class DataFrameModel(QAbstractTableModel):
    """A QAbstractTableModel that wraps a pandas DataFrame.

    The model reads rows lazily from the DataFrame, so large datasets
    (hundreds of thousands of rows) remain responsive in a QTableView.
    """

    def __init__(self, df: Optional[pd.DataFrame] = None, parent=None) -> None:
        super().__init__(parent)
        self._df = df if df is not None else pd.DataFrame()

    # -- Public API ---------------------------------------------------------

    def set_dataframe(self, df: pd.DataFrame) -> None:
        """Replace the underlying DataFrame and notify views."""
        self.beginResetModel()
        self._df = df if df is not None else pd.DataFrame()
        self.endResetModel()

    def dataframe(self) -> pd.DataFrame:
        """Return the underlying DataFrame."""
        return self._df

    # -- QAbstractTableModel overrides --------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._df)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._df.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row < 0 or row >= len(self._df) or col < 0 or col >= len(self._df.columns):
            return None

        if role == Qt.DisplayRole or role == Qt.EditRole:
            try:
                value = self._df.iat[row, col]
                if pd.isna(value):
                    return ""
                return str(value)
            except Exception:
                return ""

        if role == Qt.TextAlignmentRole:
            # Right-align numeric columns
            try:
                col_name = self._df.columns[col]
                if pd.api.types.is_numeric_dtype(self._df[col_name]):
                    return int(Qt.AlignRight | Qt.AlignVCenter)
            except Exception:
                pass
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:  # noqa: N802
        # Column headers: make them bold (brand styling also comes from the
        # global QSS on QHeaderView::section, but the font role keeps the
        # emphasis even if a caller overrides the QSS).
        if role in (Qt.FontRole, Qt.ForegroundRole) and orientation == Qt.Horizontal:
            if 0 <= section < len(self._df.columns):
                if role == Qt.FontRole:
                    return QFont("", 10, QFont.Weight.Bold)
                return QColor("#e0e7ff")
        # Row index gutter: tinted, bold, indigo numerals for quick scanning.
        if role == Qt.ForegroundRole and orientation == Qt.Vertical:
            return QColor("#a5b4fc")
        if role == Qt.FontRole and orientation == Qt.Vertical:
            return QFont("", 10, QFont.Weight.Bold)
        if role != Qt.DisplayRole:
            return None

        if orientation == Qt.Horizontal:
            if 0 <= section < len(self._df.columns):
                return str(self._df.columns[section])
        elif orientation == Qt.Vertical:
            return section + 1

        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:  # noqa: N802
        """Sort the DataFrame by the given column."""
        if column < 0 or column >= len(self._df.columns):
            return

        col_name = self._df.columns[column]
        ascending = order == Qt.AscendingOrder

        self.beginResetModel()
        try:
            self._df = self._df.sort_values(by=col_name, ascending=ascending, kind="mergesort")
        except Exception as exc:
            log.warning("DataFrameModel.sort failed: %s", exc)
        self.endResetModel()