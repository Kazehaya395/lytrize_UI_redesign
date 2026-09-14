"""desktop/widgets/plotly_web_view.py -- Interactive Plotly chart widget.

Renders Plotly figures inside a QWebEngineView with full interactivity
(zoom, pan, hover, click), entirely offline and without reloading the
whole page on every update.

Design notes (why this file looks the way it does):
  * The Plotly.js bundle is embedded once, inline, from the plotly
    package's own ``plotly.offline.get_plotlyjs()`` -- this is the same
    JS that ships with whatever version of ``plotly`` is pip-installed,
    so there's no separate asset to vendor/download and no risk of a
    version mismatch between the Python figure spec and the JS renderer.
    It never fetches anything over the network (no CDN dependency),
    which matters for a local-first, offline app.
  * The HTML shell (Plotly.js + an empty <div>) is loaded exactly once
    per widget instance. Every subsequent ``set_figure()`` call sends
    the new figure spec through a ``QWebChannel`` bridge and calls
    ``Plotly.react()`` in JS, which patches the existing chart in place
    instead of tearing down and reloading the whole document. This is
    what removes the "browser rerun" stutter -- ``Plotly.newPlot()``
    only runs once, on first render.
  * Falls back to a static PNG label if QWebEngine isn't available in
    the runtime environment (e.g. some headless/CI Linux builds).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QObject, Signal, Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PySide6.QtGui import QPixmap

from desktop import theme as _theme_mod

log = logging.getLogger(__name__)


def _get_plotlyjs_source() -> str:
    """Return the Plotly.js bundle as a string, matching the installed
    ``plotly`` package version. Cached at module load so repeated widget
    instances don't re-read it from the package each time."""
    from plotly.offline import get_plotlyjs
    return get_plotlyjs()


_PLOTLYJS_SOURCE: Optional[str] = None


def _plotlyjs_source() -> str:
    global _PLOTLYJS_SOURCE
    if _PLOTLYJS_SOURCE is None:
        try:
            _PLOTLYJS_SOURCE = _get_plotlyjs_source()
        except Exception as exc:
            log.error("Failed to load bundled plotly.js: %s", exc)
            _PLOTLYJS_SOURCE = ""
    return _PLOTLYJS_SOURCE


_SHELL_FILE_PATH: Optional[Path] = None


def _shell_file_path() -> Path:
    """Write the chart shell HTML (with Plotly.js inlined) to a shared temp
    file, once per process, and return its path.

    ``QWebEnginePage.setHtml()`` silently truncates/fails above roughly 2MB
    of content (Chromium's data: URL limit) -- the plotly.js bundle alone is
    ~4.5MB, so the shell HTML must be loaded via ``load(QUrl.fromLocalFile())``
    from a real file instead of ``setHtml()``. All chart widgets in the
    process share this one file since the shell content is identical;
    per-widget state (which figure is shown) is sent afterwards over the
    QWebChannel bridge, not baked into the file.
    """
    global _SHELL_FILE_PATH
    if _SHELL_FILE_PATH is not None and _SHELL_FILE_PATH.exists():
        return _SHELL_FILE_PATH

    shell_html = _SHELL_TEMPLATE.format(plotlyjs=_plotlyjs_source())
    tmp_dir = Path(tempfile.gettempdir()) / "lytrize_chart_shell"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / "chart_shell.html"
    path.write_text(shell_html, encoding="utf-8")
    _SHELL_FILE_PATH = path
    return path


_SHELL_TEMPLATE = """
<html>
<head>
<meta charset="utf-8">
<script>{plotlyjs}</script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  html, body {{ margin:0; padding:0; background:#0f172a; height:100%; }}
  #chart {{ width:100%; height:100%; }}
</style>
</head>
<body>
<div id="chart"></div>
<script>
  var chartDiv = document.getElementById('chart');
  var plotted = false;
  var pendingSpec = null;

  function render(specJson) {{
      var spec = JSON.parse(specJson);
      var config = {{displayModeBar: true, responsive: true, scrollZoom: true}};
      if (!plotted) {{
          Plotly.newPlot(chartDiv, spec.data, spec.layout, config);
          plotted = true;
      }} else {{
          Plotly.react(chartDiv, spec.data, spec.layout, config);
      }}
  }}

  new QWebChannel(qt.webChannelTransport, function(channel) {{
      window.bridge = channel.objects.bridge;
      window.bridge.figureReady.connect(render);
      if (pendingSpec !== null) {{ render(pendingSpec); pendingSpec = null; }}
      window.bridge.channelReady();
  }});
</script>
</body>
</html>
"""


class _ChartBridge(QObject):
    """Qt/JS bridge: pushes new figure JSON into the already-loaded page."""

    figureReady = Signal(str)
    _channel_ready = False

    @Slot()
    def channelReady(self):  # exposed to JS via slot-like call
        self._channel_ready = True


class PlotlyWebView(QWidget):
    """Widget that renders a Plotly figure interactively, offline, with
    in-place updates on subsequent renders.

    Auto-resizes the Plotly chart to match the widget's current size
    (responsive:true + Plots.resize on Qt resizeEvent) so the chart
    always fills its container without clipping — the parent layout
    decides the size, this widget just follows."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        # Expanding in both directions so the chart fills whatever
        # space the parent layout assigns — never a hard-coded floor
        # that would push the preview off-screen.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(0)

        self._web_view = None
        self._fallback_label = None
        self._use_web_engine = False
        self._channel = None
        self._bridge: Optional[_ChartBridge] = None
        self._page_loaded = False
        self._pending_fig_json: Optional[str] = None
        self._current_fig = None

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebChannel import QWebChannel

            self._web_view = QWebEngineView()
            self._web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self._layout.addWidget(self._web_view)
            self._use_web_engine = True

            self._bridge = _ChartBridge()
            self._channel = QWebChannel()
            self._channel.registerObject("bridge", self._bridge)
            self._web_view.page().setWebChannel(self._channel)

            shell_path = _shell_file_path()
            self._web_view.loadFinished.connect(self._on_load_finished)
            self._web_view.load(QUrl.fromLocalFile(str(shell_path)))
        except ImportError:
            log.info("QWebEngine not available, using static PNG fallback")
            self._fallback_label = QLabel()
            self._fallback_label.setAlignment(Qt.AlignCenter)
            self._fallback_label.setStyleSheet("background: %s;" % _theme_mod.resolve_color("border_subtle"))
            self._layout.addWidget(self._fallback_label)

    # -- Qt resize forwarding --------------------------------------------

    def resizeEvent(self, event):  # noqa: N802 — Qt override signature
        """Forward Qt resize events to Plotly so the chart reflows
        instantly instead of waiting for the next render() call.

        Without this, dragging the splitter handle between the config
        pane and the preview pane leaves the chart clipped/cropped
        until the next set_figure() call. Plotly.Plots.resize() tells
        the JS chart to re-fit to its container's current pixel size."""
        super().resizeEvent(event)
        if self._use_web_engine and self._web_view is not None:
            try:
                self._web_view.page().runJavaScript(
                    "if(window.Plotly){Plotly.Plots.resize(document.getElementById('chart'))}"
                )
            except Exception as exc:  # pragma: no cover — JS runtime
                log.debug("Plotly resize forward failed: %s", exc)

    # -- Internal --------------------------------------------------------

    def _on_load_finished(self, ok: bool) -> None:
        self._page_loaded = ok
        if ok and self._pending_fig_json is not None:
            self._bridge.figureReady.emit(self._pending_fig_json)
            self._pending_fig_json = None

    def _push_figure(self, fig_json: str) -> None:
        if self._page_loaded and self._bridge is not None:
            self._bridge.figureReady.emit(fig_json)
        else:
            self._pending_fig_json = fig_json

    # -- Public API --------------------------------------------------------

    def set_figure(self, fig) -> None:
        """Render or update a Plotly figure.

        First call on a given widget instance does a full ``Plotly.newPlot``
        in JS; every call after that does an in-place ``Plotly.react``, so
        repeated updates (e.g. after a filter or style change) don't reload
        the page or restart the JS runtime.
        """
        if fig is None:
            return
        self._current_fig = fig

        if self._use_web_engine:
            try:
                import plotly.io as pio
                fig_json = pio.to_json(fig)
                self._push_figure(fig_json)
            except Exception as exc:
                log.error("Failed to serialize Plotly figure: %s", exc)
                self._render_static(fig)
        else:
            self._render_static(fig)

    def _render_static(self, fig) -> None:
        """Fallback: render figure as static PNG (no QWebEngine available)."""
        if self._fallback_label is None:
            return
        try:
            img_bytes = fig.to_image(format="png", width=800, height=500)
            pixmap = QPixmap()
            pixmap.loadFromData(img_bytes)
            self._fallback_label.setPixmap(pixmap.scaled(
                800, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation
            ))
        except Exception as exc:
            log.error("Failed to render static PNG: %s", exc)
            self._fallback_label.setText("Failed to render chart")

    def clear(self) -> None:
        """Clear the current content."""
        self._current_fig = None
        if self._use_web_engine and self._web_view is not None:
            self._push_figure('{"data": [], "layout": {}}')
        elif self._fallback_label is not None:
            self._fallback_label.clear()
