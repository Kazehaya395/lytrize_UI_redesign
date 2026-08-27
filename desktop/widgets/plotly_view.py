"""desktop/widgets/plotly_view.py -- QWebEngineView wrapper for Plotly figures.

Renders a Plotly figure as interactive HTML inside a QWebEngineView.
The figure is serialized to HTML with ``include_plotlyjs="inline"`` so
it works fully offline.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWebEngineWidgets import QWebEngineView

log = logging.getLogger(__name__)


class PlotlyView(QWebEngineView):
    """A QWebEngineView that renders a Plotly figure."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setMinimumHeight(300)

    def set_figure(self, fig: Any, *, height: Optional[int] = None,
                   config: Optional[dict] = None) -> None:
        """Render a Plotly figure in this view.

        Parameters
        ----------
        fig : plotly.graph_objects.Figure
            The figure to render.
        height : int, optional
            Override the figure height in pixels.
        config : dict, optional
            Plotly config dict (e.g. ``{"displayModeBar": "hover"}``).
        """
        try:
            import plotly.io as pio

            if height is not None:
                fig = fig.to_dict()
                fig["layout"] = dict(fig.get("layout", {}))
                fig["layout"]["height"] = height
                import plotly.graph_objects as go
                fig = go.Figure(fig)

            cfg = config or {"displayModeBar": "hover", "responsive": True}
            html = pio.to_html(
                fig,
                full_html=True,
                include_plotlyjs="inline",
                config=cfg,
            )
            self.setHtml(html, QUrl("about:blank"))
        except Exception as exc:
            log.warning("PlotlyView.set_figure failed: %s", exc)
            self.setHtml(
                "<html><body style='background:#0b1120;color:#94a3b8;"
                "font-family:sans-serif;padding:20px;'>"
                f"<p>Failed to render chart: {exc}</p></body></html>"
            )

    def set_html(self, html: str) -> None:
        """Render raw HTML in this view."""
        self.setHtml(html, QUrl("about:blank"))