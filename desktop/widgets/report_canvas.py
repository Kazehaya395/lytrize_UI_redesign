"""desktop/widgets/report_canvas.py -- the live dashboard canvas.

The dashboard is not an approximation of the exported report. It *is* the
report: ``services.export_service.build_export_report`` output, loaded into a
QWebEngineView. Every control on the page already writes into the HTML, so
there is nothing to wire per-setting and, more importantly, no second renderer
that could disagree with the file the user eventually gets.

Why this is shaped like Power BI / Tableau / Looker Studio
---------------------------------------------------------
All three keep exactly one canvas and never swap the renderer; what they toggle
is the editing CHROME. Power BI's "View" button hides selection outlines and
handles over an unchanged canvas. Looker Studio has no preview mode at all --
its canvas is always the real report. That is the model here:

* **Edit board** adds the ``.ly-editing`` class to ``<body>`` (grips, handles,
  outlines, hover toolbar, inline notes).
* **Preview** removes it. Same DOM, same pixels as the saved file. Switching is
  one CSS class, so the two modes cannot drift -- there is nothing to drift,
  because there is only one document.

Bridge protocol
---------------
The overlay talks to Qt over QWebChannel -- the same mechanism PlotlyWebView
already uses, inverted. JS proposes, Python disposes:

===============  ==========================  ================================
JS calls         Python does                 meaning
===============  ==========================  ================================
geometryChanged  validate, clamp, persist,   JS PROPOSED a placement; Python
                 re-render once              is the authority on what is legal
notesChanged     store note, re-render        text committed on blur
actionRequested  run the page's own handler   per-card buttons live in the page
slicerChanged    store the selection,         a slicer control was used; only
                 re-render affected charts    the charts it plots are re-run
editingReady     mark the bridge usable       JS finished wiring up
===============  ==========================  ================================

A drag therefore never regenerates anything mid-gesture: only a CSS ghost
outline moves, and the report is rebuilt once, on release.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from desktop.widgets.plotly_web_view import enable_remote_content_access

log = logging.getLogger(__name__)

# One report file per content digest, so a re-render only rewrites the small
# HTML and the browser still sees a genuinely new URL (a reload to the SAME url
# is served from cache and silently shows the previous report).
_REPORT_DIR = Path(tempfile.gettempdir()) / "lytrize_report_canvas"


def _write_shared_plotly() -> Optional[Path]:
    """Write plotly.js next to the report, once per process.

    The report is generated with ``inline_plotly=False`` so it references this
    file by name. Two consequences make it necessary rather than merely nice:
    re-embedding ~4.5 MB of JavaScript on every settings tweak would make the
    live canvas unusable, and ``QWebEnginePage.setHtml()`` truncates above
    roughly 2 MB (already hit in this codebase -- see PlotlyWebView's shell).
    """
    try:
        from desktop.widgets.plotly_web_view import plotlyjs_source
        src = plotlyjs_source()
    except Exception:
        log.warning("plotly.js unavailable; report charts will not render",
                    exc_info=True)
        return None
    if not src:
        return None
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(src.encode("utf-8")).hexdigest()[:10]
    path = _REPORT_DIR / f"lytrize_plotly_{digest}.js"
    if not path.exists():
        path.write_text(src, encoding="utf-8")
    # The report asks for a fixed name, so link it to the digest-named file
    # once rather than rewriting the bundle under the stable name each run.
    stable = _REPORT_DIR / "lytrize_plotly.js"
    if not stable.exists() or stable.resolve() != path.resolve():
        try:
            stable.unlink()
        except OSError:
            pass
        try:
            stable.symlink_to(path.name)
        except OSError:
            # Windows without developer mode: fall back to a copy.
            stable.write_text(src, encoding="utf-8")
    return stable


class _ReportBridge(QObject):
    """Qt side of the editor overlay's QWebChannel.

    Deliberately dumb: it validates and forwards, and owns no state of its own.
    The page decides the geometry and the page stays the single renderer -- this
    object only decides what is legal and tells the page when to re-render.
    """

    geometry_changed = Signal(str, dict)
    notes_changed = Signal(str, str)
    action_requested = Signal(str, str)
    slicer_changed = Signal(str, str)     # index, raw payload JSON
    editing_ready = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ready = False

    @Slot(str, str)
    def geometryChanged(self, uid: str, geometry_json: str) -> None:  # noqa: N802
        """JS proposed a placement. Emit it; the page clamps and persists.

        The raw JSON string arrives rather than an object because QWebChannel
        only marshals primitives reliably across the boundary.
        """
        import json
        try:
            geom = json.loads(geometry_json)
        except (ValueError, TypeError):
            log.debug("bad geometry payload from canvas: %r", geometry_json)
            return
        if not isinstance(geom, dict) or not uid:
            return
        self.geometry_changed.emit(str(uid), geom)

    @Slot(str, str)
    def notesChanged(self, uid: str, text: str) -> None:  # noqa: N802
        if uid:
            self.notes_changed.emit(str(uid), str(text))

    @Slot(str, str)
    def actionRequested(self, uid: str, action: str) -> None:  # noqa: N802
        if uid:
            self.action_requested.emit(str(uid), str(action))

    @Slot(str, str)
    def slicerChanged(self, index: str, payload: str) -> None:  # noqa: N802
        """A slicer control was used.

        The RAW JSON string is forwarded rather than parsed here: this object
        "validates and forwards, and owns no state of its own", and the shape
        of a selection is the page's business -- it is what decides which
        slice of the payload is a range and which is a list of values.
        """
        if index != "":
            self.slicer_changed.emit(str(index), str(payload))

    @Slot()
    def editingReady(self) -> None:  # noqa: N802
        """JS has connected over the bridge.

        Until this fires, every emit from the page is silently dropped, which is
        why the canvas does not reload the document before it arrives.
        """
        self._ready = True
        self.editing_ready.emit()


class ReportCanvas(QWidget):
    """The dashboard surface: the exported report, live and editable in place.

    Emits ``geometry_changed``/``notes_changed``/``action_requested``/
    ``slicer_changed`` for the page to act on; it never stores anything itself.
    """

    geometry_changed = Signal(str, dict)
    notes_changed = Signal(str, str)
    action_requested = Signal(str, str)
    slicer_changed = Signal(str, str)     # index, raw payload JSON

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._editing = True
        self._pending_edit_mode: Optional[bool] = None
        self._last_html: Optional[str] = None
        self._loading = False

        self._bridge = _ReportBridge(self)
        self._bridge.geometry_changed.connect(self.geometry_changed)
        self._bridge.notes_changed.connect(self.notes_changed)
        self._bridge.action_requested.connect(self.action_requested)
        # Forwarded like the rest, in the same place: the bridge is wired to
        # the widget here once, and the page connects to the widget. Without
        # this line a slicer click reached the bridge and died there.
        self._bridge.slicer_changed.connect(self.slicer_changed)

        self._view = None
        self._fallback: Optional[QLabel] = None
        try:
            from PySide6.QtWebChannel import QWebChannel
            from PySide6.QtWebEngineWidgets import QWebEngineView

            self._view = QWebEngineView(self)
            self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            # A dashboard can carry a map chart. The report is a local (file://)
            # document and its basemap styles are remote, which Qt WebEngine
            # blocks by default -- the map then renders blank in the preview and
            # in the PNG export, since both come through this view.
            enable_remote_content_access(self._view.page())
            self._channel = QWebChannel(self._view.page())
            self._channel.registerObject("bridge", self._bridge)
            self._view.page().setWebChannel(self._channel)
            self._view.loadFinished.connect(self._on_load_finished)
            self._layout.addWidget(self._view)
        except ImportError:
            # Same degradation as PlotlyWebView: the app still runs, it just
            # says plainly why the canvas is empty instead of showing a
            # silently blank pane.
            log.info("QWebEngine not available; report canvas disabled")
            self._fallback = QLabel(
                "The live dashboard preview needs QtWebEngine, which this "
                "build does not have.\n\n"
                "HTML and PNG export still work.")
            self._fallback.setAlignment(Qt.AlignCenter)
            self._fallback.setWordWrap(True)
            self._fallback.setProperty("class", "faint")
            self._layout.addWidget(self._fallback)

    def is_available(self) -> bool:
        """False when QtWebEngine is missing; the caller falls back."""
        return self._view is not None

    def run_js(self, script: str) -> None:
        """Evaluate JS in the report document, if there is one.

        A no-op without a webview, so callers do not each have to guard.
        """
        if self._view is not None:
            self._view.page().runJavaScript(script)

    def set_report(self, html: str) -> None:
        """Show *html* as the dashboard.

        Identical content is skipped: a control that fires redundantly would
        otherwise reload the page and flash the whole board, which is the
        single most distracting thing a live preview can do.
        """
        if self._view is None:
            return
        if html == self._last_html:
            return
        self._last_html = html
        path = self._write(html)
        if path is None:
            return
        self._loading = True
        self._view.load(QUrl.fromLocalFile(str(path)))

    def _write(self, html: str) -> Optional[Path]:
        """Write the report to a digest-named file and return its path.

        A real file, never ``setHtml()``: the document can exceed Chromium's
        ~2 MB data-URL ceiling. Digest-named so a re-render is a genuinely new
        URL -- reloading the same path is served from cache and would keep
        showing the previous report.

        The shared plotly bundle is best-effort. If it cannot be written the
        report still loads, with its charts missing -- a visible, diagnosable
        failure -- rather than the canvas staying blank, which is neither.
        """
        try:
            _write_shared_plotly()
        except Exception:
            log.warning("shared plotly.js not written for the canvas",
                        exc_info=True)
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(html.encode("utf-8")).hexdigest()[:12]
        path = _REPORT_DIR / f"report_{digest}.html"
        try:
            path.write_text(html, encoding="utf-8")
        except OSError:
            log.exception("could not write report canvas file")
            return None
        return path

    # -- Edit / Preview -----------------------------------------------------

    def set_editing(self, on: bool) -> None:
        """Toggle the editing chrome.

        This is the whole of the Edit/Preview switch: one class on <body>. The
        report underneath is never re-rendered, which is exactly why the two
        modes cannot disagree -- there is only one document.
        """
        on = bool(on)
        self._editing = on
        if self._view is None:
            return
        if self._loading:
            # Mid-load, so lySetEditing does not exist yet. Applied on
            # loadFinished instead; remembering the intent is what stops
            # Preview from flashing back to Edit mode.
            self._pending_edit_mode = on
            return
        self._view.page().runJavaScript(
            f"window.lySetEditing && window.lySetEditing({str(on).lower()});")

    def is_editing(self) -> bool:
        return self._editing

    def _on_load_finished(self, ok: bool) -> None:
        self._loading = False
        if not ok:
            log.warning("report canvas failed to load")
            return
        mode = (self._pending_edit_mode
                if self._pending_edit_mode is not None else self._editing)
        self._pending_edit_mode = None
        # Re-applied on every load: the document was replaced, so the class and
        # any selection went with it.
        self._view.page().runJavaScript(
            "window.lySetEditing && window.lySetEditing(%s);"
            % ("true" if mode else "false"))