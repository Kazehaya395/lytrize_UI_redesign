"""desktop/widgets/analysis_notes_bar.py -- analysis-level notes footer.

New (Phase N) widget: a fixed-height footer with an "Analysis" header and a
plain text box where the user adds free-form notes for the currently-selected
*analysis type* (not the per-chart notes that live on each ChartCard).

Notes are persisted per analysis-type via SessionBridge, surviving a session
save — separate key space from the per-chart notes so the two don't collide.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTextEdit,
)

from desktop.session_bridge import SessionBridge
from desktop.theme import mode_tokens


_NOTE_KEY_PREFIX = "analysis_note__"


class AnalysisNotesBar(QWidget):
    """Fixed-height analysis-type notes footer.

    Emits note_changed(str) with the full plain text whenever the user edits
    it; the parent page forwards that to the bridge for persistence.
    """

    note_changed = Signal(str)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._updating = False  # guard re-entrant signal emission
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QLabel("Analysis")
        header.setProperty("class", "cardTitle")
        header.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(header)

        self.text = QTextEdit()
        self.text.setPlaceholderText("Add notes about this analysis…")
        self.text.setFixedHeight(72)
        self.text.setStyleSheet("font-size: 12px; padding: 6px;")
        self.text.textChanged.connect(self._on_changed)
        layout.addWidget(self.text)

    # -- Public API --------------------------------------------------------

    def set_analysis_type(self, aid: str) -> None:
        """Swap in the notes for analysis-type *aid*, blocking signals
        while seeding the widget so we don't emit a spurious change."""
        self._updating = True
        try:
            self.text.setPlainText(self.bridge.get(_NOTE_KEY_PREFIX + aid) or "")
        finally:
            self._updating = False

    # -- Slots -------------------------------------------------------------

    def _on_changed(self) -> None:
        if self._updating:
            return
        self.note_changed.emit(self.text.toPlainText())