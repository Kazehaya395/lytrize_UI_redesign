"""modules/report_editor.py -- the in-app editing overlay for the report HTML.

The dashboard's live canvas is the exported report document itself, loaded in a
QWebEngineView. There is no second rendering to keep in sync with the file, so
editing affordances have to live INSIDE that document -- as an overlay that
Preview mode simply switches off.

Design
------
* Everything is scoped to ``body.ly-editing``. Preview removes that one class,
  which hides all the chrome without re-rendering a pixel of the report. Edit and
  Preview are therefore guaranteed to be the same document.
* Geometry is a PROPOSE-then-validate split: JavaScript proposes a placement,
  Python clamps and stores it. A drag never round-trips to regenerate anything
  -- during the gesture only a CSS ghost outline moves, and the report is
  regenerated exactly once, on release.
* This module is a plain string library on purpose. The CSS and JS are full of
  braces, so keeping them out of the report's f-string template means none of it
  has to be brace-doubled or ``%``-escaped.

The saved HTML file is written with the overlay OFF, so it contains none of this.
"""

from __future__ import annotations

# Region markers: JS between these is grid-only and is STRIPPED from the free
# bundle. Kept as named constants so the strip and the markers cannot drift.
GRID_BEGIN = "/*__GRID_BEGIN__*/"
GRID_END = "/*__GRID_END__*/"


EDITOR_CSS = """
/* Only cards in a GRID get position:relative -- the grip, the selection ring and
   the handles are absolutely positioned inside the card, which needs a
   positioned ancestor. A card on the free canvas is ALREADY absolutely
   positioned by its placement; forcing relative on it here would silently drop
   its left/top and collapse the whole board to the top-left corner. Hence the
   .ly-grid-editing qualifier, applied by the script in free mode. */
.ly-editing.ly-grid-editing .chart-card,
.ly-editing.ly-grid-editing .kpi-card,
.ly-editing.ly-grid-editing .slicer-card,
.ly-editing.ly-grid-editing .title-card { position: relative; }
.ly-editing .grid, .ly-editing .ly-canvas { position: relative; }

/* On the free canvas the whole card is a drag handle. A window manager lets you
   grab a window anywhere, not only on a 26px title strip -- and on a dashboard
   the plot fills the card, so there is no strip to grab. The grip stays as the
   visible affordance, but the card body moves too. The title card is included:
   it is a card like any other, so it moves and resizes like any other. */
.ly-editing.ly-free .chart-card,
.ly-editing.ly-free .kpi-card,
.ly-editing.ly-free .slicer-card,
.ly-editing.ly-free .title-card { cursor: grab; }
.ly-editing.ly-free .ly-grip { cursor: grabbing; }

/* A dashed cell outline shows the snap target BEFORE the drop, so a drag is a
   decision rather than a guess. */
.ly-ghost { position: absolute; pointer-events: none; display: none;
  border: 2px dashed __ACCENT__; border-radius: 8px;
  background: __ACCENT__22; z-index: 40; }
.ly-ghost.on { display: block; }

/* Selection outline sits on the card so it draws above the plot. */
.ly-sel { position: absolute; inset: -2px; pointer-events: none; display: none;
  border: 2px solid __ACCENT__; border-radius: 10px; z-index: 30; }
.ly-selected > .ly-sel { display: block; }

.ly-grip { position: absolute; top: 4px; left: 4px; width: 26px; height: 22px;
  display: none; align-items: center; justify-content: center;
  cursor: grab; background: __ACCENT__; color: #fff; font-size: 13px;
  border-radius: 5px; z-index: 35; user-select: none; line-height: 1; }
.ly-handle { position: absolute; display: none; z-index: 35;
  background: __ACCENT__; border-radius: 3px; }
.ly-editing .ly-grip, .ly-editing .ly-handle { display: flex; }
.ly-handle { align-items: center; justify-content: center; color: #fff;
  font-size: 10px; }
/* The card clips its overflow, which is what keeps a wide plot from bleeding
   over the rounded corners -- but it also chopped the right and bottom handles
   in half, leaving a ~5px sliver that is almost impossible to grab. They
   therefore sit INSIDE the card edge, not straddling it: the grab target stays
   full size, the card still clips, and a handle can never sit on a neighbour's
   card. */
/* Sized for the pointer, not for the eye. 10px is below the ~16px comfortable
   grab target, which is why the handles read as decoration rather than as
   something you can pick up. The invisible extension does the real work: the
   visible chip stays small enough not to cover the plot, while a ::after
   pseudo-element widens the actual hit area well past the edge. */
.ly-h-right  { top: 50%; right: 0; width: 12px; height: 44px;
  margin-top: -22px; cursor: ew-resize; }
.ly-h-bottom { left: 50%; bottom: 0; height: 12px; width: 44px;
  margin-left: -22px; cursor: ns-resize; }
.ly-h-corner { right: 0; bottom: 0; width: 20px; height: 20px;
  cursor: nesw-resize; }
.ly-h-right::after, .ly-h-bottom::after, .ly-h-corner::after {
  content: ''; position: absolute; inset: -8px; }
.ly-h-corner::after { inset: -10px; }

/* The heading needs clearance from the edit chrome. The grip (top-left) and the
   toolbar (top-right) are absolutely positioned in the card's corners and are
   hidden in Preview, so the space is only reserved while editing -- otherwise
   Preview would show a heading with a mysterious empty band above it. */
.ly-editing .chart-card h2,
.ly-editing .kpi-card .kpi-value,
.ly-editing .slicer-card .slicer-title,
.ly-editing .title-card h1 { margin-top: 26px; }
/* The slicer's control list needs the same bottom clearance the KPI label
   does: the resize handles are drawn over the card's lower edge, and the last
   value in a scrollable list is exactly what sits there. */
.ly-editing .slicer-card .slicer-body { padding-bottom: 0.75rem; }
/* ...and the BOTTOM edge needs clearance too, for the bottom/corner resize
   handles. Without it a KPI's label -- the one line that says what the number
   means -- ends up underneath the handle drawn over it. Scoped to editing, so
   Preview and the saved file keep their natural spacing. */
.ly-editing .kpi-card .kpi-label { margin-bottom: 14px; }
.ly-editing .chart-card .notes { padding-bottom: 0.9rem; }
.ly-editing .chart-card > .notes { clear: both; }

/* Hover toolbar, mirroring the Qt card's on-hover action bar. */
.ly-bar { position: absolute; top: 4px; right: 4px; display: none;
  gap: 3px; z-index: 35; }
.ly-editing .ly-bar { display: flex; }
.ly-btn { border: none; cursor: pointer; width: 22px; height: 22px;
  border-radius: 5px; background: __ACCENT__; color: #fff;
  font-size: 12px; line-height: 1; padding: 0; }
.ly-btn:hover { filter: brightness(1.15); }

/* A card being dragged must not also pan the Plotly chart underneath it. */
.ly-dragging { user-select: none; }
.ly-dragging .plotly, .ly-dragging .modebar { pointer-events: none !important; }

/* The notes box becomes editable in place while editing. */
.ly-editing .notes { cursor: text; }
.ly-editing .notes:focus { outline: 2px solid __ACCENT__; }
.ly-editing .ly-selected { outline: none; }
"""


EDITOR_JS = r"""
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
/*
 * In-app dashboard editor.
 *
 * Moves and resizes the REAL report cards in the DOM. There is no second
 * rendering to keep in sync with the exported file -- that is the entire
 * reason the dashboard canvas works this way.
 *
 * Geometry is propose-then-validate: JS proposes a placement, Python clamps
 * and stores it. So a drag never regenerates anything mid-gesture; the report
 * is rebuilt exactly once, on release.
 */
(function () {
  var GAP_FALLBACK = __GAP__;
  var COLS = __COLS__;
  var MODE = '__MODE__';
  var FREE = MODE !== 'grid';
  /* Mirrors canvas_geometry.MIN_W / MIN_H. A card below this stops being
   * grabbable: the grip and the four handles no longer fit inside it. */
  var MIN_W = 0.12, MIN_H = 120;
  var bridge = null;

  function uidOf(el) {
    if (el.dataset && el.dataset.uid) return el.dataset.uid;
    if (el.dataset && el.dataset.kpi !== undefined) return 'kpi:' + el.dataset.kpi;
    if (el.dataset && el.dataset.slicer !== undefined) return 'slicer:' + el.dataset.slicer;
    return '';
  }
  function cards() {
    /* The title card is in this list for the same reason a KPI card is: it is
       a card, so it gets the same grip, the same three handles, the same
       click-to-select and the same arrow-key nudge. Excluding it is what made
       the heading the one element on the board that could not be arranged.
       A slicer card is here for the same reason -- it is draggable and
       resizable exactly like a chart, which is the whole of the feature
       request's "same as charts, KPI and title cards". */
    return Array.prototype.slice.call(document.querySelectorAll(
      '.chart-card[data-uid], .kpi-card[data-kpi], .title-card[data-uid], ' +
      '.slicer-card[data-slicer]'));
  }
  function canvasOf(el) {
    return el.closest('.ly-canvas, .grid');
  }

  /* ---- free-form geometry -------------------------------------------
   * Read and write the SAME units canvas_geometry uses: x/w are fractions of
   * the canvas width, y/h are pixels. Reading them back out of the computed
   * style rather than out of a data attribute means a card that was placed by
   * the server and never touched still reports the position it actually has.
   *
   * There is deliberately NO snapping to a cell pitch here. The previous build
   * rounded every drag to the grid's column and row widths, which is why the
   * handles felt dead: a card could only ever occupy a whole cell, so dragging
   * by 30px moved it by a full column and then Python re-packed it straight
   * back. A window manager places by the pixel, and so does this.
   */
  function canvasSize(el) {
    var c = canvasOf(el);
    var r = c ? c.getBoundingClientRect() : {width: 1, height: 0};
    return {w: r.width || 1, h: r.height || 0};
  }
  function geomOf(el) {
    var cs = getComputedStyle(el);
    var c = canvasSize(el);
    var x = parseFloat(cs.left), y = parseFloat(cs.top);
    var w = parseFloat(cs.width), h = parseFloat(cs.height);
    if (isNaN(x) || isNaN(y) || isNaN(w) || isNaN(h)) {
      return {x: 0, y: 0, w: 0.5, h: MIN_H};
    }
    return {x: x / c.w, y: y, w: w / c.w, h: h};
  }
  function applyGeom(el, g) {
    el.style.left = (g.x * 100) + '%';
    el.style.top = g.y + 'px';
    el.style.width = (g.w * 100) + '%';
    el.style.height = g.h + 'px';
  }
  /* Clamp exactly as canvas_geometry.clamp does, so a card cannot be dragged
   * somewhere the page would then refuse to store. Doing it on both sides is
   * deliberate: this one keeps the drag honest while it is happening, and the
   * Python one is still the authority for what gets persisted. */
  function clampGeom(g) {
    var w = Math.max(MIN_W, Math.min(1, g.w));
    g.w = w;
    g.x = Math.max(0, Math.min(g.x, 1 - w));
    g.y = Math.max(0, g.y);
    g.h = Math.max(MIN_H, g.h);
    return g;
  }

  /* ---- grid-mode geometry (legacy sessions only) -------------------- */
  /* The whole block between the GRID markers is stripped from the free bundle.
     Leaving it in as unreachable code is how the grid behaviour came back the
     first time: the rounding was "harmless dead code" until a path reached it,
     and a reader could not tell which maths a given build was using. */
  /*__GRID_BEGIN__*/
  function tracks(el, prop) {
    var raw = getComputedStyle(el.parentElement)[prop] || '';
    var out = [];
    raw.split(' ').forEach(function (v) {
      var n = parseFloat(v);
      if (!isNaN(n)) out.push(n);
    });
    return out;
  }
  function gapOf(el) {
    var g = parseFloat(getComputedStyle(el.parentElement).rowGap);
    return isNaN(g) ? GAP_FALLBACK : g;
  }
  function offsets(sizes, gap) {
    var out = [], acc = 0;
    for (var i = 0; i < sizes.length; i++) { out.push(acc); acc += sizes[i] + gap; }
    return out;
  }
  function colCount(el) {
    var n = tracks(el, 'gridTemplateColumns').length;
    return n || COLS;
  }
  function gridGeomOf(el) {
    var cs = getComputedStyle(el);
    var xs = parseInt(cs.gridColumnStart, 10), xe = parseInt(cs.gridColumnEnd, 10);
    var ys = parseInt(cs.gridRowStart, 10), ye = parseInt(cs.gridRowEnd, 10);
    if (isNaN(xs) || isNaN(ys)) return {x: 0, y: 0, w: 1, h: 1};
    return {x: xs - 1, y: ys - 1,
            w: Math.max(1, (isNaN(xe) ? xs + 1 : xe) - xs),
            h: Math.max(1, (isNaN(ye) ? ys + 1 : ye) - ys)};
  }
  function applyGridGeom(el, g) {
    el.style.gridColumn = (g.x + 1) + ' / span ' + Math.max(1, g.w);
    el.style.gridRow = (g.y + 1) + ' / span ' + Math.max(1, g.h);
  }
  function nearest(offs, px) {
    var best = 0, bestD = Infinity;
    for (var i = 0; i < offs.length; i++) {
      var d = Math.abs(offs[i] - px);
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }
  function snapCol(el, px) {
    return nearest(offsets(tracks(el, 'gridTemplateColumns'), gapOf(el)), px);
  }
  function snapRow(el, py) {
    return nearest(offsets(tracks(el, 'gridTemplateRows'), gapOf(el)), py);
  }
  /*__GRID_END__*/

  /* Mode dispatch, so the gesture code below can be written once. Written
     rather than stripped, because readGeom/writeGeom are called on every
     gesture and must resolve to something in both builds. */
  function readGeom(el) {
    if (FREE) return geomOf(el);
    /*__GRID_BEGIN__*/ return gridGeomOf(el); /*__GRID_END__*/
  }
  function writeGeom(el, g) {
    if (FREE) { applyGeom(el, g); return; }
    /*__GRID_BEGIN__*/ applyGridGeom(el, g); /*__GRID_END__*/
  }

  function ghost(el) {
    var grid = el.parentElement;
    var gh = grid.querySelector('.ly-ghost');
    if (!gh) {
      gh = document.createElement('div');
      gh.className = 'ly-ghost';
      grid.appendChild(gh);
    }
    return gh;
  }
  function paintGhost(el, g) {
    var gh = ghost(el);
    /*__GRID_BEGIN__*/
    if (!FREE) {
      var grid = el.parentElement;
      var cols = tracks(el, 'gridTemplateColumns');
      var rows = tracks(el, 'gridTemplateRows');
      var cg = gapOf(el);
      var x = offsets(cols, cg)[g.x] || 0;
      var y = offsets(rows, cg)[g.y] || 0;
      var w = 0, j;
      for (j = g.x; j < g.x + g.w && j < cols.length; j++) w += cols[j];
      w += cg * Math.max(0, g.w - 1);
      var h = 0;
      for (j = g.y; j < g.y + g.h && j < rows.length; j++) h += rows[j];
      h += cg * Math.max(0, g.h - 1);
      var gr = grid.getBoundingClientRect(), er = el.getBoundingClientRect();
      gh.style.left = (er.left - gr.left + x) + 'px';
      gh.style.top = (er.top - gr.top + y) + 'px';
      gh.style.width = w + 'px';
      gh.style.height = h + 'px';
      gh.classList.add('on');
      return;
    }
    /*__GRID_END__*/
    /* Free mode: the ghost is the card's proposed rectangle, in the same units
     * the card itself uses. Percentages rather than pixels for the horizontal,
     * so the outline stays glued to the card while the window is being resized
     * mid-drag. */
    var host = canvasOf(el);
    gh.style.left = (g.x * 100) + '%';
    gh.style.top = g.y + 'px';
    gh.style.width = (g.w * 100) + '%';
    gh.style.height = g.h + 'px';
    /* The canvas has a fixed height from the server. A card dragged past it
     * would land outside the scrollable area, so the canvas is grown live for
     * the duration of the gesture and restored on release. */
    if (host && g.y + g.h > host.offsetHeight) {
      host.style.height = (g.y + g.h) + 'px';
    }
    gh.classList.add('on');
  }
  function clearGhost() {
    Array.prototype.forEach.call(document.querySelectorAll('.ly-ghost.on'), function (n) {
      n.classList.remove('on');
    });
  }

  function select(el) {
    Array.prototype.forEach.call(document.querySelectorAll('.ly-selected'), function (n) {
      n.classList.remove('ly-selected');
    });
    if (el) { el.classList.add('ly-selected'); if (el.focus) el.focus(); }
  }
  /* A resized card still renders its plot at the old pixel size until Plotly is
   * told to re-fit, so every geometry change ends with this. Plotly.Plots.resize
   * reads the container, which on the free canvas has just changed in BOTH
   * axes -- so this is what makes a shortened card show a shorter chart rather
   * than its original plot spilling out past the bottom edge. */
  function refit(el) {
    var plot = el.querySelector('.js-plotly-plot');
    if (!plot) return;
    try {
      var wrap = el.querySelector('.chart-wrap');
      var w = wrap ? wrap.offsetWidth : 0, h = wrap ? wrap.offsetHeight : 0;
      Plotly.relayout(plot, {
        width: w > 0 ? w : undefined,
        height: h > 0 ? h : undefined,
        autosize: true
      });
    } catch (e) {
      try { Plotly.Plots.resize(plot); } catch (e2) {}
    }
  }
  /* ---- gestures ------------------------------------------------------- */
  var drag = null;
  function begin(mode, edge) {
    return function (ev) {
      /* Every draggable family has to be listed here AND in cards(): the
         pointerdown handler in decorate() fires the gesture, but this
         resolves it to the card actually being dragged. A family present in
         one list and not the other looks fine -- buttons appear, hover
         works -- and then silently does nothing on drag, because
         closest() returns null and we return early. */
      var card = ev.currentTarget.closest(
        '.chart-card, .kpi-card, .slicer-card, .title-card');
      if (!card) return;
      ev.preventDefault();
      ev.stopPropagation();
      select(card);
      var r = card.getBoundingClientRect();
      drag = {mode: mode, edge: edge, el: card, start: readGeom(card),
              grabX: ev.clientX, grabY: ev.clientY,
              originX: r.left, originY: r.top, pending: null};
      document.body.classList.add('ly-dragging');
    };
  }

  function move(ev) {
    if (!drag) return;
    var el = drag.el;
    var g = {x: drag.start.x, y: drag.start.y,
             w: drag.start.w, h: drag.start.h};
    var dx = ev.clientX - drag.grabX, dy = ev.clientY - drag.grabY;

    if (FREE) {
      /* Pixel-for-pixel, no pitch, no rounding. The grab offset was captured at
       * pointerdown, so the card keeps the exact point under the cursor rather
       * than jumping -- the same correction the grid version needed, but here it
       * is the difference between the card following the mouse and lagging
       * behind it by half its own size. */
      var c = canvasSize(el);
      if (drag.mode === 'move') {
        g.x = drag.start.x + dx / c.w;
        g.y = drag.start.y + dy;
      } else {
        /* Only the edge that was grabbed moves. Dragging the right edge must
         * not silently resize the height too -- a corner handle does both. */
        if (drag.edge !== 'bottom') g.w = drag.start.w + dx / c.w;
        if (drag.edge !== 'right')  g.h = drag.start.h + dy;
      }
      clampGeom(g);
    }
    /*__GRID_BEGIN__*/
    else {
      var gr = el.parentElement.getBoundingClientRect();
      if (drag.mode === 'move') {
        g.x = snapCol(el, ev.clientX - drag.grabX - gr.left
                         + (drag.originX - gr.left));
        g.y = snapRow(el, ev.clientY - drag.grabY - gr.top
                         + (drag.originY - gr.top));
      } else {
        var cols = tracks(el, 'gridTemplateColumns');
        var rows = tracks(el, 'gridTemplateRows');
        var pitchX = (cols[0] || 1) + gapOf(el);
        var pitchY = (rows[0] || 1) + gapOf(el);
        if (drag.edge !== 'bottom') g.w = drag.start.w + Math.round(dx / pitchX);
        if (drag.edge !== 'right')  g.h = drag.start.h + Math.round(dy / pitchY);
      }
      var n = colCount(el);
      g.x = Math.max(0, Math.min(g.x, n - 1));
      g.w = Math.max(1, Math.min(g.w, n - g.x));
      g.h = Math.max(1, g.h);
      g.y = Math.max(0, g.y);
    }
    /*__GRID_END__*/
    drag.pending = g;
    /* The CARD follows the pointer, not just the ghost outline. A dashed
     * rectangle that moves while the card stays put reads as "this will happen
     * if you let go", which is a weaker and slower way to arrange a board than
     * the window-manager behaviour people already have in their hands. */
    writeGeom(el, g);
    refit(el);
    paintGhost(el, g);
  }

  function end() {
    if (!drag) return;
    document.body.classList.remove('ly-dragging');
    var d = drag;
    drag = null;
    clearGhost();
    if (!d.pending) return;
    var g = d.pending, b = d.start;
    writeGeom(d.el, g);
    refit(d.el);
    /* The canvas was grown live to let a card be dropped below its old bottom
     * (see paintGhost). Python re-renders with the correct height from the
     * stored geometry, so the temporary growth must be handed back or the board
     * keeps a canvas taller than its contents until something else redraws. */
    var host = canvasOf(d.el);
    if (host && FREE && host.style.height) host.style.height = '';
    if (!bridge) return;
    var same = Math.abs(g.x - b.x) < 0.0005 && Math.abs(g.y - b.y) < 0.5 &&
               Math.abs(g.w - b.w) < 0.0005 && Math.abs(g.h - b.h) < 0.5;
    if (same) return;   // a click that never moved is not a layout change
    /* Final geometry only: Python re-renders exactly once per gesture. */
    bridge.geometryChanged(uidOf(d.el), JSON.stringify(g));
  }

  /* Arrow keys nudge the selected card; Shift crosses a whole row. A layout
   * tool with no keyboard path is not a finished one -- this is the same
   * affordance Power BI and Tableau both provide for aligned placement. */
  function keyNav(ev) {
    var el = document.activeElement;
    if (!el || !el.classList || !el.classList.contains('ly-selected')) return;
    var g = readGeom(el), hit = true;
    /* Nudge in PIXELS on the free canvas, not in cells. A one-cell step on a
     * free board is a jump of a third of the screen, which is the opposite of
     * fine alignment; Shift takes a bigger stride for coarse moves. */
    var stepPx = ev.shiftKey ? 40 : 10;
    var stepX = FREE ? (canvasSize(el).w > 0 ? stepPx / canvasSize(el).w : 0)
                     : (ev.shiftKey ? 2 : 1);
    var step = FREE ? stepPx : (ev.shiftKey ? 2 : 1);
    if (ev.key === 'ArrowLeft') g.x -= stepX;
    else if (ev.key === 'ArrowRight') g.w += stepX;
    else if (ev.key === 'ArrowUp') g.y -= step;
    else if (ev.key === 'ArrowDown') { if (ev.shiftKey) g.h += step; else g.y += step; }
    else hit = false;
    if (!hit) return;
    ev.preventDefault();
    if (FREE) {
      clampGeom(g);
    }
    /*__GRID_BEGIN__*/
    else {
      var n = colCount(el);
      g.x = Math.max(0, Math.min(g.x, n - 1));
      g.w = Math.max(1, Math.min(g.w, n - g.x));
      g.h = Math.max(1, g.h);
      g.y = Math.max(0, g.y);
    }
    /*__GRID_END__*/
    writeGeom(el, g);
    refit(el);
    if (bridge) bridge.geometryChanged(uidOf(el), JSON.stringify(g));
  }
  /* ---- chrome --------------------------------------------------------- */
  function btn(label, title, fn) {
    var b = document.createElement('button');
    b.className = 'ly-btn';
    b.textContent = label;
    b.title = title;
    b.type = 'button';
    b.addEventListener('click', function (ev) {
      ev.stopPropagation();
      fn();
    });
    return b;
  }

  function decorate(el) {
    if (el.querySelector('.ly-sel')) return;
    var uid = uidOf(el);

    var sel = document.createElement('div');
    sel.className = 'ly-sel';
    el.appendChild(sel);

    var grip = document.createElement('div');
    grip.className = 'ly-grip';
    grip.textContent = '\u2838';
    grip.title = 'Drag to move';
    grip.addEventListener('pointerdown', begin('move'));
    el.appendChild(grip);

    var handles = [['right', '\u2195'], ['bottom', '\u2195'], ['corner', '\u292F']];
    handles.forEach(function (spec) {
      var h = document.createElement('div');
      h.className = 'ly-handle ly-h-' + spec[0];
      h.textContent = spec[1];
      h.title = 'Drag to resize';
      h.addEventListener('pointerdown', begin('resize', spec[0]));
      el.appendChild(h);
    });

    /* The action bar lives inside the page, so the per-card operations that
     * used to be Qt buttons are now bridge calls back into the page's own
     * dialogs. A KPI card gets its OWN operations: "notes" and "style" name
     * chart concepts that do not exist for a KPI, so offering them there meant
     * pressing one ran a chart handler against a KPI index. */
    var bar = document.createElement('div');
    bar.className = 'ly-bar';
    var act = function (a) {
      return function () { if (bridge) bridge.actionRequested(uid, a); };
    };
    if (el.classList.contains('kpi-card')) {
      bar.appendChild(btn('\u270E', 'Edit KPI', act('kpi-edit')));
      /* Free canvas: position IS the order, so up/down reordering is
       * meaningless here (it meant something on the old grid). Edit +
       * remove only. */
      bar.appendChild(btn('\u2715', 'Remove KPI', act('kpi-delete')));
    } else if (el.classList.contains('slicer-card')) {
      /* Its own operations, for the same reason the KPI has its own: "notes"
         and "style" name chart concepts that mean nothing for a slicer, and
         offering them ran a chart handler against a slicer index. */
      /* Slicer cards likewise get edit + remove + clear only: no up/down
       * on a free canvas (see the KPI note above). Clear empties the
       * selection without removing the card. */
      bar.appendChild(btn('\u270E', 'Edit slicer', act('slicer-edit')));
      bar.appendChild(btn('\u2715', 'Remove slicer', act('slicer-delete')));
      /* Clearing is destructive enough to deserve its own button rather than
         living in the edit dialog: it is the one thing a user reaches for
         when a slicer combination returns nothing useful. */
      bar.appendChild(btn('\u2716', 'Clear selection', act('slicer-clear')));
    } else if (el.classList.contains('title-card')) {
      /* No bar at all. Every button here is an operation ON a chart or a KPI --
         edit its notes, restyle it, reorder it, delete it -- and the heading has
         none of those. Showing the chart toolbar on it would offer to "remove
         the chart" on the dashboard's title, which the page would then have to
         refuse; the grip and the handles are the whole of its affordance. */
    } else {
      /* The pencil OPENS this chart in the Analysis page (bound settings
         form) -- it never edited notes inline, so the old "Edit analysis
         notes" tooltip was a lie that sent users hunting for a dialog that
         did not exist. Notes are edited by clicking the notes text itself
         (contenteditable, blur-committed). Style/move live in the Analysis
         page too, so the middle three buttons were removed: they duplicated
         that surface and their handlers raced the drag the user was still
         performing. Delete stays: it is the one destructive action that
         belongs on the board. */
      bar.appendChild(btn('\u270E', 'Edit chart in Analysis', act('open-analysis')));
      bar.appendChild(btn('\u229E', 'Add slicer for this chart', act('slicer-add')));
      bar.appendChild(btn('\u2715', 'Remove chart', act('delete')));
    }
    el.appendChild(bar);

    el.tabIndex = 0;
    el.addEventListener('pointerdown', function () { select(el); });

    /* Free canvas only: the card body itself is a drag handle, the way a window
     * manager behaves. Grid mode is left alone -- there the plot fills the card
     * and stealing every pointerdown would break Plotly's own pan/zoom.
     *
     * Guarded against interactive descendants, and deliberately NOT the plot:
     * the plot is skipped too, because dragging a chart is a real gesture in
     * its own right and taking it away would be a regression. The grip, the
     * handles and the action bar keep their own handlers. */
    if (FREE) {
      el.addEventListener('pointerdown', function (ev) {
        if (ev.button !== 0) return;
        var t = ev.target;
        if (t.closest && t.closest('.ly-grip, .ly-handle, .ly-bar, .ly-sel, ' +
                                    'button, input, select, textarea, label, ' +
                                    '[contenteditable], ' +
                                    '.notes, .slicer-body, .js-plotly-plot, ' +
                                    '.modebar')) return;
        begin('move')(ev);
      });
    }
  }

  /* Notes are edited in place, which is what the Qt card's QTextEdit did, and
   * committed on BLUR rather than per keystroke -- otherwise every character
   * would trigger a full report regeneration. */
  function wireNotes() {
    Array.prototype.forEach.call(document.querySelectorAll('.notes'), function (n) {
      if (n.dataset.lyNotes) return;
      n.dataset.lyNotes = '1';
      n.setAttribute('contenteditable', 'true');
      n.addEventListener('keydown', function (ev) {
        ev.stopPropagation();
        if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
          ev.preventDefault();
          n.blur();
        }
      });
      n.addEventListener('blur', function () {
        var card = n.closest('.chart-card');
        if (!card || !bridge) return;
        var text = n.innerText.replace(/^\s*Analysis Notes:\s*/i, '').trim();
        bridge.notesChanged(uidOf(card), text);
      });
    });
  }

  /* Slicer controls report their own state back over the bridge. Each control
   * carries data-ly-slicer="{index}" and data-ly-role (values/value/low/
   * high/n); this collects the whole INDEX's state at once. A list slicer is
   * every CHECKED box, so a click reports the group, not the one box -- the
   * page's apply_selection replaces the selection, and sending one box would
   * read as "filter to just this". A slider is the low+high pair together
   * for the same reason: one handle alone would collapse the range.
   *
   * Idempotent via dataset.lyWired: cards() re-decorates after every render,
   * and wiring twice would emit two bridge calls per click.
   *
   * The flag spells "wired", deliberately NOT "slicer": the index lives in
   * data-ly-slicer, which QWebChannel exposes as dataset.lySlicer. Flagging
   * the element with dataset.lySlicer = '1' would overwrite the index with
   * the string '1', so every slicer on the board would report as slicer 1.
   *
   * The .slicer-body exclusion in the drag guard above is what lets these
   * fire at all: without it the pointerdown would start a move gesture and
   * the click would drag the card instead of ticking the box. */
  function wireSlicers() {
    Array.prototype.forEach.call(
      document.querySelectorAll('[data-ly-slicer]'), function (ctrl) {
      if (ctrl.dataset.lyWired) return;
      ctrl.dataset.lyWired = '1';
      var ev = (ctrl.tagName === 'SELECT' || ctrl.type === 'checkbox' ||
                ctrl.type === 'range' || ctrl.type === 'number')
        ? 'change' : 'input';
      var timer = null;
      ctrl.addEventListener(ev, function () {
        if (!bridge || !bridge.slicerChanged) return;
        function send() {
          var card = ctrl.closest('.slicer-card');
          var scope = card || document;
          var idx = ctrl.dataset.lySlicer;
          var q = '[data-ly-slicer="' + idx + '"]';
          var payload = {};
          Array.prototype.forEach.call(
            scope.querySelectorAll(q), function (c) {
            var role = c.dataset.lyRole || 'value';
            if (c.type === 'checkbox') {
              if (c.checked) {
                (payload[role] = payload[role] || []).push(c.value);
              }
            } else if (c.tagName === 'SELECT' && c.multiple) {
              payload[role] = Array.prototype.map.call(
                c.selectedOptions || [], function (o) { return o.value; });
            } else {
              payload[role] = c.value;
            }
          });
          try {
            bridge.slicerChanged(String(idx), JSON.stringify(payload));
          } catch (e) {}
        }
        /* A search box fires per keystroke: debouncing keeps one regen per
         * word rather than one per letter. Every other control reports
         * immediately -- change events are already discrete. */
        if (ctrl.type === 'search' || ctrl.type === undefined) {
          if (timer) clearTimeout(timer);
          timer = setTimeout(send, 350);
        } else {
          send();
        }
      });
    });
  }

  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', end);
  document.addEventListener('pointercancel', end);
  document.addEventListener('keydown', keyNav);
  window.addEventListener('resize', clearGhost);

  /* Called from Python. Switching modes is ONE class toggle: the report DOM
   * underneath is untouched, so Edit and Preview are the same document. */
  window.lySetEditing = function (on) {
    document.body.classList.toggle('ly-editing', !!on);
    document.body.classList.toggle('ly-free', FREE);
    document.body.classList.toggle('ly-grid-editing', !FREE);
    if (!on) { clearGhost(); select(null); }
  };
  window.lyEditMode = function () {
    return document.body.classList.contains('ly-editing');
  };

  /* The mode classes are set HERE as well as in lySetEditing: the document is
   * emitted already in edit mode, so before the first call from Python the body
   * would have .ly-editing but not .ly-free, and every free-canvas rule would be
   * switched off -- the board would look uneditable until something toggled the
   * mode. */
  document.body.classList.toggle('ly-free', FREE);
  document.body.classList.toggle('ly-grid-editing', !FREE);

  cards().forEach(decorate);
  wireNotes();
  wireSlicers();

  if (typeof QWebChannel !== 'undefined') {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      /* Assign the GLOBAL as well as the local. Every handler above closes over
       * the IIFE-local `bridge`, which is all the editor needs -- but leaving
       * the global unset makes a connected-but-working bridge
       * indistinguishable from a dead one, which is exactly the wrong answer
       * to give anyone (or any test) diagnosing this. It also diverges from
       * the chart shell, which publishes window.bridge. */
      window.bridge = bridge = channel.objects.bridge;
      if (bridge && bridge.editingReady) bridge.editingReady();
    });
  }
})();
</script>
"""


def editor_css(accent: str = "#6163df") -> str:
    """The overlay CSS, with the accent substituted in.

    The accent comes from the theme rather than being hard-coded, so the grips
    and selection outline belong to the report the user actually chose.
    """
    safe = str(accent or "#6163df").strip() or "#6163df"
    return EDITOR_CSS.replace("__ACCENT__", safe)


def editor_js(cols: int = 2, gap: int = 24, mode: str = "free") -> str:
    """The editor JS, with the layout numbers and mode substituted in.

    ``mode`` selects which gesture maths the overlay uses. ``"free"`` is the
    pixel canvas the app edits in; ``"grid"`` is the older cell-based path,
    kept only so a session saved before the free canvas can still be opened and
    rearranged.

    Substituted with ``str.replace`` rather than ``%``/``.format`` because the
    script is full of braces that every other formatting mechanism would either
    choke on or require doubling.
    """
    safe_mode = "grid" if str(mode or "free").lower() == "grid" else "free"
    js = EDITOR_JS
    if safe_mode == "free":
        # Remove the grid-only regions outright rather than shipping them
        # unreachable. Dead snapping maths sitting in the free bundle is a trap:
        # it reads as active, it is what made the cell-rounding behaviour look
        # intentional, and the next edit that reaches it silently brings the grid
        # back. Anything the free build needs is written outside the markers.
        while GRID_BEGIN in js:
            head, rest = js.split(GRID_BEGIN, 1)
            if GRID_END not in rest:
                # An unterminated marker means the source was edited badly.
                # Leaving the region in beats deleting the rest of the script.
                js = head + GRID_BEGIN + rest
                break
            _, tail = rest.split(GRID_END, 1)
            js = head + tail
    return (js
            .replace("__COLS__", str(int(cols)))
            .replace("__GAP__", str(int(gap)))
            .replace("__MODE__", safe_mode))