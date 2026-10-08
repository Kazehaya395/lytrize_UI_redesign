"""modules/report_slicer.py -- In-browser slicer engine for the saved file.

The exported HTML is self-contained (no Python, no bridge), so slicer
controls there cannot call back into ``filter_service``. This module
ships the small dependency-free JS engine that does the same job:

* reads ``window.LYTRIZE_DATA`` (column-major payload from
  ``modules.report_data``) and ``window.LYTRIZE_SPECS`` (one chartspec
  per chart uid);
* listens on the same ``data-ly-slicer`` / ``data-ly-role`` controls
  the live canvas uses, so the markup is identical in both places;
* recomputes only the charts whose spec columns intersect the active
  slicer columns (never "re-run everything") and applies data-only
  updates via ``Plotly.react``, preserving trace styling;
* exposes ``window.lyWhenSettled()`` so the PNG renderer can wait for
  real render completion instead of a fixed timer.

Kept as a plain string (not an f-string) for the same reason as
``report_editor``: the JS is full of braces. ``export.py`` injects it
with ``str.replace`` on the ``__`` tokens, never ``.format``.
"""

SLICER_JS = r"""
(function () {
  "use strict";
  var DATA = window.LYTRIZE_DATA || null;
  var SPECS = window.LYTRIZE_SPECS || {};
  var SLICERS = window.LYTRIZE_SLICERS || [];
  if (!DATA || !DATA.columns || !DATA.columns.length) return;

  var NCOL = DATA.columns.length;
  var COLIDX = {};
  DATA.columns.forEach(function (c, i) { COLIDX[c] = i; });
  var NROW = DATA.rows && DATA.rows.length ? DATA.rows[0].length : 0;

  window.__lyPending = 0;

  function numVal(v) {
    if (v === null || v === undefined || v === "") return NaN;
    var n = Number(v);
    return (n !== n) ? NaN : n;
  }

  /* -- mask -------------------------------------------------------- */
  function specOf(s) {
    /* Mirror of slicer_service.slicer_spec: a populated ``spec`` wins, and a
       definition that predates it is rebuilt from its flat fields -- without
       this a legacy session would silently filter on the wrong kind. */
    var spec = (s.spec && typeof s.spec === "object") ? s.spec : {};
    var kind = String(spec.kind || s.kind || "").toLowerCase();
    if (!kind) {
      var w = String(s.widget || "").toLowerCase();
      kind = ({ "slider": "range", "search": "search",
                "top_n": "top_n" })[w] || "in";
    }
    var out = { kind: kind, column: spec.column || s.column || "" };
    if (kind === "in" || kind === "not_in")
      out.values = spec.values || s.values || [];
    else if (kind === "range") {
      out.low  = ("low"  in spec) ? spec.low  : s.low;
      out.high = ("high" in spec) ? spec.high : s.high;
    } else if (kind === "search")
      out.value = ("value" in spec) ? spec.value : (s.value || s.label || "");
    else if (kind === "top_n")
      out.n = ("n" in spec) ? spec.n : (s.n || 10);
    return out;
  }

  function maskFor(active) {
    var mask = new Array(NROW).fill(true);
    /* Exclusions run AFTER inclusions, mirroring filter_service.apply_specs:
       in a single left-to-right pass "include EU/APAC, exclude EU" would be
       computed against rows the exclusion was about to drop and quietly keep
       the EU rows back. */
    var inc = [], exc = [];
    (active || []).forEach(function (s) {
      (specOf(s).kind === "not_in" ? exc : inc).push(s);
    });
    inc.concat(exc).forEach(function (s) {
      var sp = specOf(s);
      var j = COLIDX[sp.column];
      if (j === undefined) return;
      var col = DATA.rows[j];
      var spec = sp;
      var kind = sp.kind;
      if (kind === "in") {
        var want = {};
        (spec.values || []).forEach(function (v) { want[String(v)] = 1; });
        if (!Object.keys(want).length) return;   /* inert, not "match none" */
        for (var i = 0; i < NROW; i++)
          if (mask[i] && !want[String(col[i])]) mask[i] = false;
      } else if (kind === "not_in") {
        var drop = {};
        (spec.values || []).forEach(function (v) { drop[String(v)] = 1; });
        if (!Object.keys(drop).length) return;
        for (var k = 0; k < NROW; k++)
          if (mask[k] && drop[String(col[k])]) mask[k] = false;
      } else if (kind === "range") {
        var lo = (spec.low === null || spec.low === undefined) ? -Infinity
                 : numVal(spec.low);
        var hi = (spec.high === null || spec.high === undefined) ? Infinity
                 : numVal(spec.high);
        var isDt = (DATA.dtypes || {})[s.column] === "datetime";
        for (var m = 0; m < NROW; m++) {
          if (!mask[m]) continue;
          var v = isDt ? Date.parse(col[m]) : numVal(col[m]);
          if (v !== v || v < lo || v > hi) mask[m] = false;
        }
      } else if (kind === "search") {
        var q = String(spec.value || "").toLowerCase();
        if (!q) return;
        for (var n = 0; n < NROW; n++)
          if (mask[n] && String(col[n]).toLowerCase().indexOf(q) < 0)
            mask[n] = false;
      }
      /* top_n is a display option, not a row filter: handled per chart. */
    });
    /* exclusions last so they win -- mirrors filter_service. */
    return mask;
  }

  function activeSlicers(list) {
    /* Mirror of slicer_service.slicer_is_active. A range is active when
       EITHER bound is set (Python: ``low in (None, "") or high ...``) --
       requiring both would make a slider whose user only moved one handle
       stop filtering, which reads as the control being broken.
       ``list`` defaults to the page's own slicers; tests pass one in. */
    return (list || SLICERS).filter(function (s) {
      if (!s || typeof s !== "object") return false;
      var spec = specOf(s);
      var kind = spec.kind;
      if (kind === "in" || kind === "not_in")
        return !!(spec.values && spec.values.length);
      if (kind === "range")
        return (spec.low !== null && spec.low !== undefined && spec.low !== "") ||
               (spec.high !== null && spec.high !== undefined && spec.high !== "");
      if (kind === "search") return !!String(spec.value || "");
      if (kind === "top_n") {
        var n = parseInt(spec.n, 10);
        return n === n && n > 0;
      }
      return false;
    });
  }
  function aggOf(spec) {
    var a = String(spec.aggregate || "Mean").toLowerCase();
    if (a === "sum") return "sum";
    if (a.indexOf("count") >= 0) return "count";
    if (a === "min") return "min";
    if (a === "max") return "max";
    if (a === "median") return "median";
    if (a === "std") return "std";
    return "mean";
  }
  function aggregate(mask, dim, measure, agg) {
    var groups = {}, order = [];
    var jD = COLIDX[dim], jM = measure ? COLIDX[measure] : -1;
    for (var i = 0; i < NROW; i++) {
      if (!mask[i]) continue;
      var key = String(DATA.rows[jD][i]);
      if (!(key in groups)) { groups[key] = []; order.push(key); }
      groups[key].push(jM >= 0 ? numVal(DATA.rows[jM][i]) : 1);
    }
    var xs = [], ys = [];
    order.forEach(function (k) {
      var vals = groups[k].filter(function (v) { return v === v; });
      var y = 0;
      if (agg === "sum") y = vals.reduce(function (a, b) { return a + b; }, 0);
      else if (agg === "count") y = vals.length;
      else if (agg === "min") y = vals.length ? Math.min.apply(null, vals) : 0;
      else if (agg === "max") y = vals.length ? Math.max.apply(null, vals) : 0;
      else if (agg === "median") {
        if (vals.length) {
          var s = vals.slice().sort(function (a, b) { return a - b; });
          var m = Math.floor(s.length / 2);
          y = s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
        }
      } else if (agg === "std") {
        if (vals.length > 1) {
          var mu = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
          var va = vals.reduce(function (a, b) { return a + (b - mu) * (b - mu); }, 0) / (vals.length - 1);
          y = Math.sqrt(va);
        }
      } else {
        y = vals.length ? vals.reduce(function (a, b) { return a + b; }, 0) / vals.length : 0;
      }
      xs.push(k); ys.push(y);
    });
    return { x: xs, y: ys };
  }
  function countsFor(mask, dim) {
    var counts = {}, ord = [];
    var jC = COLIDX[dim];
    for (var m = 0; m < NROW; m++) {
      if (!mask[m]) continue;
      var key = String(DATA.rows[jC][m]);
      if (!(key in counts)) { counts[key] = 0; ord.push(key); }
      counts[key]++;
    }
    return { x: ord, y: ord.map(function (k) { return counts[k]; }) };
  }
  function chartEl(uid) {
    var card = document.querySelector('.chart-card[data-uid="' + uid + '"] .chart-wrap');
    if (!card) return null;
    return card.querySelector(".js-plotly-plot");
  }
  function recompute(uid, mask, active) {
    var spec = SPECS[uid];
    var el = chartEl(uid);
    if (!spec || !el || typeof Plotly === "undefined") return;
    var myCols = spec.columns || [];
    var touches = active.some(function (s) { return myCols.indexOf(s.column) >= 0; });
    if (!touches) return;
    var scoped = active.filter(function (s) { return s.scope === "chart"; });
    if (scoped.length) {
      var mine = scoped.some(function (s) { return s.target === uid; });
      var reportHit = active.some(function (s) {
        return s.scope !== "chart" && myCols.indexOf(s.column) >= 0;
      });
      if (!mine && !reportHit) return;
    }
    var dim = (spec.dimensions || [])[0];
    var measure = (spec.measures || [])[0] || null;
    var ctype = spec.chart_type || "";
    try {
      window.__lyPending++;
      if (ctype === "pie_chart" && dim) {
        var r = measure ? aggregate(mask, dim, measure, aggOf(spec)) : countsFor(mask, dim);
        Plotly.react(el, [{ type: "pie", labels: r.x, values: r.y, hole: 0.45 }]).then(done, done);
      } else if (ctype === "distribution" && dim) {
        var jD = COLIDX[dim], vals = [];
        for (var i = 0; i < NROW; i++)
          if (mask[i]) { var v = numVal(DATA.rows[jD][i]); if (v === v) vals.push(v); }
        Plotly.react(el, [{ type: "histogram", x: vals }]).then(done, done);
      } else if (ctype === "scatter_plot" && dim && measure) {
        var jX = COLIDX[dim], jY = COLIDX[measure], xs = [], ys = [];
        for (var k = 0; k < NROW; k++)
          if (mask[k]) { xs.push(DATA.rows[jX][k]); ys.push(DATA.rows[jY][k]); }
        Plotly.react(el, [{ type: "scatter", mode: "markers", x: xs, y: ys }]).then(done, done);
      } else if (dim) {
        var out = measure ? aggregate(mask, dim, measure, aggOf(spec)) : countsFor(mask, dim);
        Plotly.react(el, [{ x: [out.x], y: [out.y] }]).then(done, done);
      } else { done(); }
    } catch (e) { done(); }
    function done() { window.__lyPending = Math.max(0, window.__lyPending - 1); }
  }
  function reapplyAll() {
    var active = activeSlicers();
    var mask = maskFor(active);
    Object.keys(SPECS).forEach(function (uid) { recompute(uid, mask, active); });
    updateStatus(active);
  }
  function updateStatus(active) {
    var n = active.length;
    document.querySelectorAll(".slicer-card").forEach(function (card) {
      var st = card.querySelector(".slicer-status");
      if (!st) return;
      st.textContent = n ? (n + (n === 1 ? " filter" : " filters") + " active") : "Select to filter";
    });
  }
  function readControl(ctrl) {
    var idx = ctrl.getAttribute("data-ly-slicer");
    var role = ctrl.getAttribute("data-ly-role");
    var s = SLICERS[+idx];
    if (!s) return;
    /* Seed kind/column from the resolved spec before writing: a legacy flat
       definition (no ``spec`` field) would otherwise gain a spec carrying the
       value but not the kind, and specOf would then fall back to a widget
       name the control does not set. */
    var base = specOf(s);
    s.spec = (s.spec && typeof s.spec === "object") ? s.spec : {};
    if (!s.spec.kind) s.spec.kind = base.kind;
    if (!s.spec.column) s.spec.column = base.column;
    if (role === "values") {
      var vals = [];
      if (ctrl.tagName === "SELECT") {
        for (var i = 0; i < ctrl.options.length; i++)
          if (ctrl.options[i].selected) vals.push(ctrl.options[i].value);
      }
      s.spec.values = vals;
    } else if (role === "value") {
      if (ctrl.type === "checkbox") {
        var sc = ctrl.closest(".slicer-card") || document;
        var vs = [];
        sc.querySelectorAll('input[data-ly-role="value"]:checked').forEach(function (b) {
          vs.push(b.value);
        });
        s.spec.values = vs;
      } else {
        s.spec.value = ctrl.value;
      }
    } else if (role === "low" || role === "high") {
      var n = parseFloat(ctrl.value);
      s.spec[role] = isNaN(n) ? null : n;
    } else if (role === "n") {
      s.spec.n = Math.max(1, parseInt(ctrl.value, 10) || 1);
    }
  }
  var debounce = null;
  document.addEventListener("change", function (ev) {
    var t = ev.target;
    if (!t || !t.getAttribute || t.getAttribute("data-ly-slicer") === null) return;
    readControl(t);
    clearTimeout(debounce);
    debounce = setTimeout(reapplyAll, 120);
  });
  document.addEventListener("input", function (ev) {
    var t = ev.target;
    if (!t || !t.getAttribute || t.getAttribute("data-ly-slicer") === null) return;
    if (t.type !== "range" && t.type !== "search" && t.type !== "number") return;
    readControl(t);
    clearTimeout(debounce);
    debounce = setTimeout(reapplyAll, 200);
  });
  /* Test handle. The engine runs inside an IIFE, so without this the mask
     and aggregation algebra could only be tested through a real browser.
     It exposes pure functions only -- no DOM -- so a stubbed QJSEngine can
     drive it in CI. */
  window.lySlicer = {
    maskFor: maskFor,
    activeSlicers: activeSlicers,
    aggregate: aggregate,
    aggOf: aggOf,
    specOf: specOf,
    reapplyAll: reapplyAll
  };

  window.lyWhenSettled = function (timeoutMs) {
    var timeout = timeoutMs || 8000;
    return new Promise(function (resolve) {
      var start = Date.now();
      (function poll() {
        var plots = document.querySelectorAll(".js-plotly-plot").length;
        if ((window.__lyPending || 0) <= 0 && plots > 0) return resolve(true);
        if (Date.now() - start > timeout) return resolve(false);
        setTimeout(poll, 100);
      })();
    });
  };
})();
"""


def slicer_js() -> str:
    """The engine source for injection into the exported file."""
    return SLICER_JS
