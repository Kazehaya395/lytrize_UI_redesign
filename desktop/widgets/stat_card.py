"""desktop/widgets/stat_card.py -- Small summary stat card for the Home page.

Same visual language as KpiCard (dashboard), but standalone -- Home page
has no per-session theme system, so this doesn't need KpiCard's
apply_style()/export-style coupling. Shows an icon, a big number, and a
label underneath.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel


class StatCard(QFrame):
    """A single summary stat: icon + big number + label."""

    def __init__(self, icon: str, value: int, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("stat_card")
        self._build_ui(icon, value, label)

    def _build_ui(self, icon: str, value: int, label: str) -> None:

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        icon_lbl = QLabel(icon)
        icon_lbl.setProperty("class", "iconEmoji")
        top_row.addWidget(icon_lbl)

        self.lbl_value = QLabel(f"{value:,}")
        self.lbl_value.setProperty("class", "statValue")
        top_row.addWidget(self.lbl_value)
        top_row.addStretch()
        layout.addLayout(top_row)

        lbl_label = QLabel(label)
        lbl_label.setProperty("class", "statLabel")
        layout.addWidget(lbl_label)

        self.setMinimumWidth(160)
        self.setMinimumHeight(72)

    def set_value(self, value: int) -> None:
        self.lbl_value.setText(f"{value:,}")
