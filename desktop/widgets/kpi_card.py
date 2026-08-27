"""desktop/widgets/kpi_card.py -- Small KPI value card for the dashboard.

Displays a single metric (label + value, optional delta) matching the
same visual language already used in the export HTML's ``ex_kpi``
styling -- the goal is that what's shown here in the live dashboard
matches what actually appears in the exported report, since previously
KPIs were computed and exported but never rendered on screen.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


class KpiCard(QFrame):
    """A single KPI metric card: label on top, big value, optional delta.

    Has a small header with move-up/move-down/remove controls -- the
    "location edit" gap this addresses: previously a KPI could be added
    (via the dashboard's own "+ Add KPI" dialog) but never removed or
    reordered from the UI. DashboardPage._on_remove_kpi() already
    existed as a method but nothing ever called it -- this card had no
    button to trigger it at all.
    """

    move_up_requested = Signal(int)     # kpi index
    move_down_requested = Signal(int)   # kpi index
    remove_requested = Signal(int)      # kpi index

    def __init__(self, label: str, value: str, delta: Optional[str] = None,
                 index: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("kpi_card")
        self._index = index
        self._build_ui(label, value, delta)

    def _build_ui(self, label: str, value: str, delta: Optional[str]) -> None:
        self.setStyleSheet(
            "QFrame#kpi_card {"
            "  background: #1b2245; border: 1px solid #2c3564;"
            "  border-radius: 10px;"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(2)

        btn_style = (
            "QPushButton { background: transparent; color: #64748b; border: none;"
            "  font-size: 11px; padding: 0px; }"
            "QPushButton:hover { color: #e2e8f0; }"
        )
        header = QHBoxLayout()
        header.setSpacing(2)
        header.addStretch()
        self.btn_up = QPushButton("⬆")
        self.btn_up.setFixedSize(18, 18)
        self.btn_up.setToolTip("Move up")
        self.btn_up.setStyleSheet(btn_style)
        self.btn_up.clicked.connect(lambda: self.move_up_requested.emit(self._index))
        header.addWidget(self.btn_up)

        self.btn_down = QPushButton("⬇")
        self.btn_down.setFixedSize(18, 18)
        self.btn_down.setToolTip("Move down")
        self.btn_down.setStyleSheet(btn_style)
        self.btn_down.clicked.connect(lambda: self.move_down_requested.emit(self._index))
        header.addWidget(self.btn_down)

        self.btn_remove = QPushButton("✕")
        self.btn_remove.setFixedSize(18, 18)
        self.btn_remove.setToolTip("Remove KPI")
        self.btn_remove.setStyleSheet(btn_style)
        self.btn_remove.clicked.connect(lambda: self.remove_requested.emit(self._index))
        header.addWidget(self.btn_remove)
        layout.addLayout(header)

        self.lbl_label = QLabel(label)
        self.lbl_label.setStyleSheet("font-size: 11px; color: #94a3b8; font-weight: 600;")
        layout.addWidget(self.lbl_label)

        self.lbl_value = QLabel(str(value))
        self.lbl_value.setStyleSheet("font-size: 14px; color: #f5f7ff; font-weight: 700;")
        layout.addWidget(self.lbl_value)

        self.lbl_delta = QLabel(delta or "")
        self.lbl_delta.setStyleSheet("font-size: 11px; color: #34d399;")
        self.lbl_delta.setVisible(bool(delta))
        layout.addWidget(self.lbl_delta)

        self.setMinimumWidth(140)

    def apply_style(self, style: dict) -> None:
        """Re-skin from a dashboard export-style dict, same pattern as
        ``ChartCard.apply_style`` -- in-place QSS update, no rebuild."""
        card_bg = style.get("ex_kpi", "#1b2245")
        border = style.get("ex_border", "#2c3564")
        text_color = style.get("ex_kpi_text_color", "#f5f7ff")
        val_size = style.get("ex_kpi_val_size", 20)
        self.setStyleSheet(
            f"QFrame#kpi_card {{ background: {card_bg}; border: 1px solid {border};"
            f"  border-radius: 10px; }}"
        )
        self.lbl_value.setStyleSheet(
            f"font-size: {val_size}px; color: {text_color}; font-weight: 700;"
        )
