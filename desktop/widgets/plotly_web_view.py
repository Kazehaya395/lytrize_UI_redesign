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
  * The shell is loaded from a ``file://`` URL (see ``_shell_file_path`` for
    why it cannot be a ``data:`` URL), which makes Qt WebEngine classify the
    chart as *local content*. Its ``LocalContentCanAccessRemoteUrls`` setting
    defaults to **False** -- a local page may not fetch remote resources -- and
    every Plotly map basemap is exactly that: Plotly.js hands
    ``layout.map.style`` to MapLibre, which fetches a style JSON over the
    network (``https://basemaps.cartocdn.com/gl/positron-gl-style/style.json``
    for ``carto-positron``, and so on). With the default the fetch fails
    (``TypeError: Failed to fetch``), MapLibre raises "Map error", and the
    chart renders as an EMPTY plot area -- axes and colour bar present, no
    basemap and no markers. ``enable_remote_content_access()`` lifts that
    restriction; every page that renders a figure applies it.
  * Falls back to a static PNG label if QWebEngine isn't available in
    the runtime environment (e.g. some headless/CI Linux builds).
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PySide6.QtGui import QPixmap

from desktop import theme as _theme_mod

log = logging.getLogger(__name__)

# Ceiling for the self-re-arming "wait until the view is sizeable" poll in
# _relayout_when_sized. Deliberately far larger than any real cold start
# (~1.3s measured): the point is that the wait adapts to the machine, and this
# only stops a permanently 0-height widget from polling forever.
_RELAYOUT_MAX_SECONDS = 30.0

# Debounce for the legend refit. A splitter drag or a dashboard re-grid arrives
# as dozens of resize events within a few frames; recomputing and re-pushing
# the figure on each one is wasted work and makes the chart visibly stutter.
# Long enough to coalesce a drag, short enough that the chart has settled
# before the user looks at it.
_REFIT_DEBOUNCE_MS = 180

# A refit is only worth doing when the width moved enough to change the
# answer. Below this the layout is recomputed to a fraction that differs in the
# third decimal, which is invisible; the threshold is also large enough to
# absorb the 1-2px jitter a real drag produces.
_REFIT_MIN_DELTA_PX = 24


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


def plotlyjs_source() -> str:
    """The Plotly.js bundle as a string, read from disk once per process.

    Public accessor for the dashboard canvas, which serves the same bundle to
    the report preview as a separate file rather than re-embedding it. Public
    because the loader below is a module-level cache: calling this a second
    time costs nothing and never re-reads the 4.5 MB file.
    """
    return _plotlyjs_source()


def bundled_font_css() -> str:
    """Public accessor for the bundled-font ``@font-face`` rules.

    The report preview renders in Chromium, which cannot see fonts registered
    with Qt, so the families the app ships have to be offered to the page
    explicitly -- otherwise a chosen font silently substitutes and the preview
    lies about what the exported file will look like.
    """
    return _font_face_css()


def _font_face_css() -> str:
    """CSS @font-face rules for the bundled fonts, for the chart shell.

    Chromium does not see fonts registered with Qt, so the app's own font files
    have to be offered to the page explicitly. Returns an empty string when no
    fonts are bundled, so the shell still builds.
    """
    try:
        from font_bundle import font_face_css
        return font_face_css()
    except Exception as exc:
        log.debug("bundled font CSS unavailable: %s", exc)
        return ""


def enable_remote_content_access(page) -> bool:
    """Let a locally-loaded chart page fetch the resources a figure needs.

    Maps are the reason this exists. Plotly.js renders a map by handing
    ``layout.map.style`` to MapLibre, which fetches a basemap style JSON and a
    stream of tiles over HTTPS. The chart shell is loaded from a ``file://``
    URL, so Qt WebEngine treats the document as *local content* and -- by
    default -- forbids it from reaching the network at all. The fetch is
    rejected before it leaves the renderer (``TypeError: Failed to fetch``),
    MapLibre raises "Map error", and the figure comes out as an empty plot
    area: the axes and colour bar are there, the basemap and every marker are
    not. Nothing in the figure spec is wrong, which is why this looked like a
    backend bug.

    ``LocalContentCanAccessRemoteUrls`` is the switch. It is also what makes
    ``LocalContentCanAccessFileUrls`` matter here: the shell's ``@font-face``
    rules point at ``file://`` URLs under ``app_assets/fonts``, so the bundled
    fonts are loaded by exactly the same local-to-local hop. Both are enabled
    together rather than leaving the font half to chance.

    ``LocalContentCanAccessDataUrls`` is left alone: the shell inlines the
    ~4.5 MB Plotly.js bundle as a literal ``<script>`` body, which needs no
    data-URL permission.

    Safe to call on any page and more than once. Returns False (without
    raising) when the page or the Qt version does not offer these attributes,
    so a caller never needs to guard it -- the worst case is the old,
    map-less behaviour rather than a crash.
    """
    if page is None:
        return False
    try:
        from PySide6.QtWebEngineCore import QWebEngineSettings
        attrs = QWebEngineSettings.WebAttribute
    except Exception as exc:
        log.debug("QWebEngine settings unavailable; remote content left "
                  "at its default (maps will not load)", exc_info=True)
        return False

    applied = False
    for name in ('LocalContentCanAccessRemoteUrls',
                 'LocalContentCanAccessFileUrls'):
        try:
            attr = getattr(attrs, name)
        except AttributeError:
            # Older/newer Qt: leave that one at its default.
            continue
        try:
            page.settings().setAttribute(attr, True)
            applied = True
        except Exception as exc:
            log.debug("could not enable %s", name, exc_info=True)
    return applied


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

    The filename carries a short digest of the template so editing the shell
    (CSS, JS) cannot be defeated by the file left in the temp dir by a
    PREVIOUS run of the app -- without it, a stale shell kept serving the
    old markup (e.g. the old opaque body background) until the temp
    directory happened to be cleared.
    """
    global _SHELL_FILE_PATH
    if _SHELL_FILE_PATH is not None and _SHELL_FILE_PATH.exists():
        return _SHELL_FILE_PATH

    shell_html = _SHELL_TEMPLATE.format(plotlyjs=_plotlyjs_source(),
                                        fontfacecss=_font_face_css())
    # Digest the TEMPLATE only (not the inlined plotly.js, which is ~4.5MB
    # and would cost a needless full re-hash on every start). The bundled-font
    # CSS is folded in too: swapping or adding a font in app_assets/fonts must
    # produce a NEW shell file, otherwise a stale one from a previous run
    # keeps serving the old @font-face set.
    digest_src = _SHELL_TEMPLATE + _font_face_css()
    digest = hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:10]
    tmp_dir = Path(tempfile.gettempdir()) / "lytrize_chart_shell"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / f"chart_shell_{digest}.html"
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
  /* Bundled fonts (app_assets/fonts). The charts are drawn by Chromium, which
     has its own font stack and cannot see fonts registered with Qt, so the
     families the app ships are handed to the page explicitly here. Without
     this the chart silently substituted a system default and most font
     choices produced no visible change. */
  {fontfacecss}
  /* Transparent by design: the figure's own paper/plot background is what
     paints the chart canvas (chart_display.theme_figure), and the
     surrounding widget/card shows through. A hard-coded body colour here
     would frame the plot in a rectangle that does not match the card. */
  html, body {{ margin:0; padding:0; background:transparent; height:100%; }}
  #chart {{ width:100%; height:100%; }}
  /* The ChartCard floats its own action buttons (edit/style/delete) over the
     canvas's top-right corner so the chart can start at the very top of the
     card. Plotly draws its own modebar in that same corner, so push the
     modebar down by one button row (28px + a small gap) to keep the two from
     overlapping. Purely positional -- it does not affect the figure. */
  .modebar {{ top: 32px !important; }}
</style>
</head>
<body>
<div id="chart"></div>
<script>
  var chartDiv = document.getElementById('chart');
  var plotted = false;
  var pendingSpec = null;
  var lastMapStyle = null;

  /* The basemap style currently described by a figure spec, or null when the
     figure is not a map. Reads BOTH subplot ids because the runner may emit
     `map` (scattermap) or `mapbox` (scattermapbox) depending on the plotly
     version's express path. */
  function mapStyleOf(spec) {{
      var lay = spec && spec.layout;
      var sub = lay && (lay.map || lay.mapbox);
      return (sub && typeof sub.style === 'string') ? sub.style : null;
  }}

  /* A MapLibre canvas whose backing store no longer matches its CSS size
     (clientWidth x devicePixelRatio) is stretched by the compositor, which
     is exactly what "the map is blurry" looks like: the pixels are there,
     they are just being upscaled. MapLibre only re-measures on a window
     resize, and Qt/plotly resizes happen without one, so nudge it -- but
     only when a mismatch is actually detected, so the common case costs
     nothing and no resize storm can start. */
  function lyFixMapCanvases() {{
      try {{
          var dpr = window.devicePixelRatio || 1;
          var cs = document.querySelectorAll(
              'canvas.maplibregl-canvas, canvas.mapboxgl-canvas');
          for (var i = 0; i < cs.length; i++) {{
              var c = cs[i];
              var w = c.clientWidth, h = c.clientHeight;
              if (!w || !h) continue;
              if (Math.abs(c.width - Math.round(w * dpr)) > 2 ||
                  Math.abs(c.height - Math.round(h * dpr)) > 2) {{
                  window.dispatchEvent(new Event('resize'));
                  return true;
              }}
          }}
      }} catch (e) {{ /* no map on this figure: nothing to fix */ }}
      return false;
  }}
  window.lyFixMapCanvases = lyFixMapCanvases;

  function render(specJson) {{
      var spec = JSON.parse(specJson);
      var config = {{displayModeBar: true, responsive: true, scrollZoom: true}};
      var style = mapStyleOf(spec);
      /* A style CHANGE must go through Plotly.newPlot, not react: plotly.js
         keeps the MapLibre instance across react() and swaps the style on it,
         which aborts with "Mapbox error" when layout.map.layers is present
         (setting a paint property on a layer the NEW style does not have yet)
         -- the update chain dies and the basemap never changes. newPlot
         tears the map down and builds it with the new style, which always
         works. See plotly/plotly.js#5505. */
      var styleChanged = plotted && style !== null && style !== lastMapStyle;
      var p;
      if (!plotted || styleChanged) {{
          p = Plotly.newPlot(chartDiv, spec.data, spec.layout, config);
          plotted = true;
      }} else {{
          p = Plotly.react(chartDiv, spec.data, spec.layout, config);
      }}
      lastMapStyle = style;
      /* After the (promise-returning) plot settles, make sure any map canvas
         is at device resolution rather than a stretched 1x buffer. */
      try {{
          Promise.resolve(p).then(function () {{ setTimeout(lyFixMapCanvases, 0); }});
      }} catch (e) {{
          setTimeout(lyFixMapCanvases, 120);
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
    # Emitted once the JS side has finished constructing the QWebChannel
    # and connected ``figureReady`` to its render function. Until this
    # fires, a ``figureReady`` emit has no listener and is silently lost --
    # which is why a freshly generated chart could stay blank forever.
    channelConnected = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._channel_ready = False

    @Slot()
    def channelReady(self):  # exposed to JS via slot-like call
        self._channel_ready = True
        self.channelConnected.emit()

    @property
    def channel_ready(self) -> bool:
        return self._channel_ready

    def reset_channel(self) -> None:
        """Mark the channel as not-yet-connected.

        Called when a page load STARTS: the outgoing document (and its JS
        ``figureReady`` subscription) is about to be torn down, so the
        Python-side flag must go back to False or a figure pushed during
        the reload would be emitted into a dead channel.
        """
        self._channel_ready = False


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
        self._shell_url: Optional[QUrl] = None
        self._pending_fig_json: Optional[str] = None
        self._current_fig = None      # figure as handed in by the caller (un-themed)
        self._render_fig = None       # the themed copy actually pushed to JS
        self._pending_bg: Optional[str] = None
        # True while a "wait until sizeable" relayout chain is in flight, so
        # overlapping callers (set_figure + showEvent) share one chain.
        self._relayout_pending = False
        # Width the legend layout was last laid out against. Charts arrive
        # built on a worker with no width, so the first real size has to
        # trigger a refit -- but a resize storm then arrives as dozens of
        # events in a few frames. The refit is debounced onto this timer and
        # skipped entirely when the width has not meaningfully moved, so a
        # dragged splitter reflows once rather than on every tick.
        self._refit_width = 0
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(_REFIT_DEBOUNCE_MS)
        self._refit_timer.timeout.connect(self._do_debounced_refit)

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebChannel import QWebChannel

            self._web_view = QWebEngineView()
            self._web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            # Before any load: the basemap styles a map figure needs are remote,
            # and this document is local (file://). See
            # enable_remote_content_access() -- without this every map renders
            # as an empty plot area.
            enable_remote_content_access(self._web_view.page())
            self._layout.addWidget(self._web_view)
            self._use_web_engine = True

            # Let the page paint through onto the widget surface so the
            # chart blends into the ChartCard instead of sitting on an
            # opaque rectangle of its own (the "BG should be transparent"
            # report). The page's own background is themed per mode in
            # _apply_page_background, so a transparent widget background
            # only shows the card where the figure itself is transparent.
            try:
                self._web_view.page().setBackgroundColor(Qt.GlobalColor.transparent)
            except Exception:
                log.debug("web page background transparency failed", exc_info=True)

            self._bridge = _ChartBridge()
            self._bridge.channelConnected.connect(self._on_channel_connected)
            self._channel = QWebChannel()
            self._channel.registerObject("bridge", self._bridge)
            self._web_view.page().setWebChannel(self._channel)

            shell_path = _shell_file_path()
            # Keep the shell URL: showEvent re-uses it to kick the deferred
            # load for a tab that was created while hidden.
            self._shell_url = QUrl.fromLocalFile(str(shell_path))
            self._web_view.loadStarted.connect(self._on_load_started)
            self._web_view.loadFinished.connect(self._on_load_finished)
            self._web_view.load(self._shell_url)
        except ImportError:
            log.info("QWebEngine not available, using static PNG fallback")
            self._fallback_label = QLabel()
            self._fallback_label.setAlignment(Qt.AlignCenter)
            self._fallback_label.setStyleSheet("background: %s;" % self._canvas_bg())
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
        self.relayout()

    def showEvent(self, event):  # noqa: N802 — Qt override signature
        """Re-push the current figure when this widget becomes visible.

        Root-cause fix for "only the last created/edited chart shows, other
        tabs are blank": a ``QWebEngineView`` inside a hidden ``QTabWidget``
        page does not reliably run its pending page load while off-screen,
        so ``_on_load_finished`` never fires for that tab and the figure
        sent by ``set_figure()`` sits in ``_pending_fig_json`` forever. The
        freshly generated chart always worked because generation selects
        its tab (visible → page loads); older tabs stayed blank.

        On first show this kicks the deferred shell load (pending figure
        flushes from ``_on_load_finished`` as usual); on later shows it
        re-emits the current figure so a tab that loaded while hidden
        repaints at the correct size. ``set_figure()`` stays the single
        write path -- this only *replays* the latest figure.
        """
        super().showEvent(event)
        if not self._use_web_engine or self._web_view is None:
            return
        try:
            if not self._page_loaded:
                # Shell not up yet: (re)kick the load. ``_shell_url`` is set
                # in __init__; guard anyway so a partially constructed widget
                # can never raise AttributeError out of an event handler.
                if self._shell_url is not None:
                    self._web_view.load(self._shell_url)
            elif self._render_fig is not None:
                import plotly.io as pio
                self._apply_page_background()
                self._bridge.figureReady.emit(pio.to_json(self._render_fig))
            # Becoming visible is exactly when a hidden-tab chart needs to
            # re-measure against its now-real container size.
            self._relayout_when_sized()
        except Exception as exc:  # pragma: no cover — JS bridge runtime
            log.debug("Plotly showEvent re-push failed: %s", exc)

    def relayout(self) -> None:
        """Tell Plotly to re-fit to the current widget size, right now.

        ``responsive: true`` only re-fits the chart when the *browser*
        fires a resize event. A QWebEngineView that was laid out at 0px
        (inside a not-yet-activated page, or a collapsed tab) and then
        given a real size by a later layout pass does not always produce
        that event, so the chart can stay unsized/blank until the user
        happens to drag something. Calling Plotly.Plots.resize() directly
        closes that gap without reloading the page.

        Cheap and idempotent -- Plotly recomputes from the live container
        dimensions -- so it is safe to call after any layout change.
        """
        if not self._use_web_engine or self._web_view is None:
            return
        # A card that was first shown at 0px (a hidden tab, a collapsed
        # pane) is only now getting a real width, and the figure it holds
        # was sized for a guess. Re-derive the legend layout against the
        # pixels we actually have before asking Plotly to re-fit, then push
        # the corrected figure. Debounced, and skipped when the width has not
        # meaningfully changed since the last refit.
        self._schedule_legend_refit()
        try:
            self._web_view.page().runJavaScript(
                # Re-fit, then make sure any MapLibre canvas is at device
                # resolution: plotly re-lays-out the container, but MapLibre
                # only re-measures on a window resize -- without the nudge the
                # canvas keeps its old backing store and the map stays soft
                # after every Qt-side resize.
                "if(window.Plotly){Plotly.Plots.resize("
                "document.getElementById('chart'));}"
                "if(window.lyFixMapCanvases){window.lyFixMapCanvases();}"
            )
        except Exception as exc:  # pragma: no cover — JS runtime
            log.debug("Plotly relayout failed: %s", exc)

    def _relayout_when_sized(self) -> None:
        """Re-fit the chart as soon as the view is genuinely usable.

        A freshly created card is laid out before its stack page becomes
        current, so the first real size arrives one event-loop turn later --
        and on a COLD start much later than that: the QWebEngine shell has to
        boot, load ~4.5MB of inlined Plotly.js and finish the QWebChannel
        handshake first (measured at ~1.3s from Generate on this machine).

        The old version polled 20x20ms and then gave up, i.e. a 400ms budget
        that expires long before a cold start is ready. When that happened the
        chart stayed laid out at 0px and only appeared after the user happened
        to resize the window -- the "close the app, reopen, Generate is blank
        again" report. It re-armed on some paths but not all, so the window
        between the poll expiring and the next manual resize was a real hole.

        Instead of a fixed number of attempts, keep polling on a timer that
        re-arms itself until the view is both loaded AND taller than zero
        (bounded only by a generous ceiling so a genuinely 0-height widget
        cannot spin forever), then relayout once and stop. The re-arm makes
        the fix independent of how slow the start is, which is what makes it
        hold across restarts rather than only on a warm process.
        """
        if not self._use_web_engine or self._web_view is None:
            return

        # One chain per widget: a second caller (e.g. showEvent firing while
        # set_figure is still polling) must not start a competing chain.
        if self._relayout_pending:
            return
        self._relayout_pending = True
        started = time.monotonic()

        def _try() -> None:
            try:
                ready = (self._page_loaded and self._web_view is not None
                         and self._web_view.height() > 0)
            except RuntimeError:
                # Widget destroyed underneath us (card deleted mid-flight).
                self._relayout_pending = False
                return
            if ready:
                self._relayout_pending = False
                self.relayout()
                return
            # Still not ready. Ceiling is generous (30s) and only exists so a
            # permanently hidden/0-height widget cannot poll forever.
            if time.monotonic() - started > _RELAYOUT_MAX_SECONDS:
                self._relayout_pending = False
                log.debug("relayout gave up: view never became sizeable")
                return
            QTimer.singleShot(20, _try)

        QTimer.singleShot(0, _try)

    # -- Internal --------------------------------------------------------

    def _canvas_bg(self) -> str:
        """Canvas colour for the current app theme (self-contained figure)."""
        try:
            from chart_display import chart_canvas_color
            return str(chart_canvas_color(_theme_mod.current_mode()))
        except Exception:
            return "#0f172a"

    def _apply_page_background(self) -> None:
        """Make the page behind the chart transparent.

        The shell HTML is written once per process and shared by every chart
        widget, so it carries no mode-specific background (its CSS is now
        ``background:transparent`` for exactly this reason). Clearing the
        inline styles here is belt-and-braces for a page that was reloaded
        from an older cached shell, and keeps the first paint correct.

        The canvas colour is NOT re-applied to the body any more: the
        figure's own ``paper_bgcolor``/``plot_bgcolor`` (set by
        ``chart_display.theme_figure``) already paints the plot area, and
        the ChartCard surface is styled to the same colour, so a body
        colour here would only re-introduce the mismatched frame.
        """
        if not self._use_web_engine or self._web_view is None:
            return
        bg = self._canvas_bg()
        if not self._page_loaded:
            # Shell not ready yet: remember it and apply from
            # _on_load_finished, so the first paint is already correct.
            self._pending_bg = bg
            return
        try:
            self._web_view.page().runJavaScript(
                "document.documentElement.style.background='transparent';"
                "if(document.body){document.body.style.background='transparent';}"
            )
        except Exception as exc:  # pragma: no cover — JS runtime
            log.debug("Plotly canvas background failed: %s", exc)

    def _on_load_started(self) -> None:
        """A new document is on its way: the old channel is about to die.

        The JS side unsubscribes ``figureReady`` the instant the document
        goes away, so the Python-side ready flag has to be cleared NOW --
        otherwise a figure set during the reload is emitted into a channel
        with no listener and the chart comes up blank.
        """
        self._page_loaded = False
        if self._bridge is not None:
            self._bridge.reset_channel()

    def _on_load_finished(self, ok: bool) -> None:
        self._page_loaded = ok
        if not ok:
            return
        if self._pending_bg is not None:
            self._pending_bg = None
            self._apply_page_background()
        # The figure is flushed from _on_channel_connected, NOT here:
        # loadFinished only means the document parsed -- the QWebChannel
        # handshake happens a tick later, so emitting now drops the
        # figure on the floor and the chart stays blank.
        if self._bridge is not None and not self._bridge.channel_ready:
            return
        self._flush_pending_figure()
        # The shell has loaded; if the widget only just received a real
        # size (a card added to a tab that became current now), Plotly
        # still has to be told to measure itself. Without this the first
        # chart of a freshly generated tab can stay blank until the user
        # resizes the window.
        self._relayout_when_sized()

    def _push_figure(self, fig_json: str) -> None:
        """Hand *fig_json* to the page, or queue it until the page can take it.

        Both the document load AND the QWebChannel handshake must be done
        before ``figureReady`` has a listener. Emitting after only
        loadFinished silently discards the figure, which is the root cause
        of "chart generated but nothing appears".
        """
        if (self._page_loaded and self._bridge is not None
                and self._bridge.channel_ready):
            self._bridge.figureReady.emit(fig_json)
        else:
            self._pending_fig_json = fig_json

    def _flush_pending_figure(self) -> None:
        """Deliver the queued figure once the channel is actually live."""
        if self._pending_fig_json is None or self._bridge is None:
            return
        if not (self._page_loaded and self._bridge.channel_ready):
            return
        self._bridge.figureReady.emit(self._pending_fig_json)
        self._pending_fig_json = None

    def _on_channel_connected(self) -> None:
        """JS finished the QWebChannel handshake -- deliver anything queued."""
        self._flush_pending_figure()
        self._relayout_when_sized()

    # -- Public API --------------------------------------------------------

    def set_figure(self, fig) -> None:
        """Render or update a Plotly figure.

        First call on a given widget instance does a full ``Plotly.newPlot``
        in JS; every call after that does an in-place ``Plotly.react``, so
        repeated updates (e.g. after a filter or style change) don't reload
        the page or restart the JS runtime.

        The figure is re-coloured for the current app theme on the way in
        (``chart_display.theme_figure``): the canvas, gridlines, hover label
        and every colour that was chosen to sit on a dark canvas follow the
        mode.  The caller's own figure object is never mutated, so the copy
        kept in the session/export path keeps its own colours.
        """
        if fig is None:
            return
        self._current_fig = fig
        render_fig = self._themed(fig)
        # A NEW figure has not been laid out for this width yet, so forget the
        # width the previous one was refit against. Otherwise the delta guard
        # would treat a freshly generated chart at the same card size as
        # "already correct" and leave it on its worker-time guess.
        self._refit_width = 0
        # The figure was built without knowing how wide this card ended up
        # (Generate runs on a worker thread), so re-size the reserved legend
        # layout against the live view before pushing. No-op when the view has
        # no width yet; the first sizeable pass re-runs this via relayout().
        if self._use_web_engine:
            if self._refit_legend_to_width(render_fig):
                self._refit_width = int(self._web_view.width() or 0)
        self._render_fig = render_fig

        if self._use_web_engine:
            self._apply_page_background()
            try:
                import plotly.io as pio
                self._push_figure(pio.to_json(render_fig))
            except Exception as exc:
                log.error("Failed to serialize Plotly figure: %s", exc)
                self._render_static(render_fig)
            # A figure pushed while the view still has no real size renders
            # at 0x0; re-fit as soon as the layout gives it one.
            self._relayout_when_sized()
        else:
            self._render_static(render_fig)

    def _themed(self, fig):
        """Return the mode-appropriate COPY of *fig* (never the original)."""
        try:
            from chart_display import theme_figure
            return theme_figure(fig, _theme_mod.current_mode())
        except Exception:
            log.debug("chart theming failed; rendering the figure as-is",
                      exc_info=True)
            return fig

    def _schedule_legend_refit(self) -> None:
        """Ask for a legend refit on the next quiet moment, if one is warranted.

        Called from every relayout, so it is on the hot path of a resize and has
        to be cheap. Two guards keep the real work rare:

          * a width that has not moved since the last refit is a no-op, and
          * a pending timer is merely restarted, so a whole drag collapses to
            one refit at the end rather than one per event.
        """
        if not self._use_web_engine or self._web_view is None:
            return
        if self._render_fig is None or self._bridge is None:
            return
        w = int(self._web_view.width() or 0)
        if w <= 0:
            # A hidden tab or collapsed pane has no real width yet; the
            # self-re-arming _relayout_when_sized() poll will call back once
            # the widget is genuinely sizeable.
            return
        if (self._refit_width and abs(w - self._refit_width) < _REFIT_MIN_DELTA_PX):
            return
        self._refit_timer.start()

    def _do_debounced_refit(self) -> None:
        """Re-lay the legend out for the settled width and push the figure.

        Only fires after the resize storm has paused, so the expensive part
        (deep-copying, re-measuring, re-serialising) happens once. The width
        is recorded afterwards so an unchanged size is never recomputed.
        """
        if not self._use_web_engine or self._web_view is None:
            return
        fig = self._render_fig
        if fig is None:
            return
        w = int(self._web_view.width() or 0)
        if w <= 0:
            return
        if (self._refit_width and abs(w - self._refit_width) < _REFIT_MIN_DELTA_PX):
            return
        try:
            if not self._refit_legend_to_width(fig):
                self._refit_width = w
                return
            self._refit_width = w
            import plotly.io as _pio
            self._bridge.figureReady.emit(_pio.to_json(fig))
        except Exception as exc:  # pragma: no cover — JS/serialisation
            log.debug("debounced legend refit failed: %s", exc)

    def _refit_legend_to_width(self, fig):
        """Re-derive the reserved legend layout against the REAL view width.

        Figures are generated on a worker thread, which has no view and
        therefore no width, so the legend layout falls back to an assumed card
        width. On a full-width chart that reserved a few hundred px for a legend
        box that needs ~76px -- the "big gap between chart and legend" report --
        and on a narrow dashboard tile it could not fit a column at all.

        This view knows its own width by the time it paints, so the layout is
        recomputed here against those pixels. It is safe to call repeatedly:
        ``refit_legend_width`` restores the baseline first and converges, so a
        second call at the same width is a no-op rather than a compounding strip.

        Returns True when the figure's layout actually changed, so the caller
        knows whether it needs to re-push it to the browser.
        """
        if fig is None or not self._use_web_engine or self._web_view is None:
            return False
        try:
            from chart_display import refit_legend_width
            return bool(refit_legend_width(fig, self._web_view.width()))
        except Exception:
            log.debug("legend width refit skipped", exc_info=True)
            return False

    def apply_theme(self) -> None:
        """Re-colour and repaint the current figure after a dark<->light switch.

        In place by design: ``set_figure`` renders through ``Plotly.react``,
        so a theme toggle repaints every open chart without reloading the web
        view -- a reload is what flashes the whole chart area.
        """
        if self._current_fig is None:
            self._apply_page_background()
            return
        self.set_figure(self._current_fig)

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
        self._render_fig = None
        self._pending_fig_json = None
        self._refit_width = 0
        self._refit_timer.stop()
        if self._use_web_engine and self._web_view is not None:
            self._push_figure('{"data": [], "layout": {}}')
        elif self._fallback_label is not None:
            self._fallback_label.clear()
