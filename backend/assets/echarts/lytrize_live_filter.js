/* LytrizeLiveFilter: in-browser slicer/KPI recompute for exported HTML.
 * Pure data engine, no DOM/echarts deps. Mirrors filter_service +
 * kpi_service semantics: null-safe, NaN-as-missing, per-chart scoping. */
(function (global) {
"use strict";
function strVal(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "number" && !isFinite(v)) return "";
  return String(v);
}
function numVal(v) {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return isFinite(v) ? v : null;
  var n = Number(String(v).replace(/,/g, ""));
  return isFinite(n) ? n : null;
}
function normStr(v) { return strVal(v).trim().toLowerCase(); }
function getSpec(s) {
  if (!s || typeof s !== "object") return {};
  if (s.spec && typeof s.spec === "object") return s.spec;
  var o = {};
  ["kind","column","values","value","low","high","n","mode","target","scope"].forEach(function (k) {
    if (s[k] !== undefined) o[k] = s[k];
  });
  return o;
}
function isActive(s, sel) {
  var spec = getSpec(s);
  var v = sel && sel[s.uid] !== undefined ? sel[s.uid] : null;
  var kind = String(spec.kind || s.kind || "in").toLowerCase();
  var vals = v !== null ? v : spec.values;
  if (kind === "in") {
    return Array.isArray(vals) ? vals.length > 0 : vals !== null && vals !== undefined && String(vals) !== "";
  }
  if (kind === "not_in") return Array.isArray(vals) && vals.length > 0;
  if (kind === "range") {
    var lo = v && v.low !== undefined ? v.low : spec.low;
    var hi = v && v.high !== undefined ? v.high : spec.high;
    return (lo !== null && lo !== undefined && lo !== "") || (hi !== null && hi !== undefined && hi !== "");
  }
  if (kind === "search") { var t = v !== null ? v : spec.value; return !!(t && String(t).trim()); }
  if (kind === "top_n") return true;
  return false;
}
function appliesTo(s, uid) {
  if (String(s.scope || "report").toLowerCase() !== "chart") return true;
  return String(s.target || getSpec(s).target || "") === String(uid);
}
function activeFor(slicers, sel, uid) {
  var o = [];
  (slicers || []).forEach(function (s) {
    if (!s || !s.column) return;
    if (!appliesTo(s, uid)) return;
    if (isActive(s, sel)) o.push(s);
  });
  return o;
}
function hasActive(slicers, sel) {
  return (slicers || []).some(function (s) { return isActive(s, sel); });
}
function filterIdx(payload, slicers, sel, uid, cache) {
  var n = payload.total_rows || 0;
  if (!n || !payload.columns) return [];
  var act = activeFor(slicers, sel, uid);
  // One board update asks for the same report-wide selection once per chart
  // and again for KPIs. Cache by the active slicers and their selected values
  // so a million-row mask is built once per distinct scope, not per card.
  var cacheKey = null;
  if (cache) {
    cacheKey = act.map(function (s) {
      var id = String(s.uid || "");
      return id + "=" + JSON.stringify(sel && sel[id] !== undefined ? sel[id] : null);
    }).join("|");
    if (Object.prototype.hasOwnProperty.call(cache, cacheKey)) return cache[cacheKey];
  }
  var ci = {};
  payload.columns.forEach(function (c, i) { ci[c] = i; });
  if (!act.length) {
    var a = new Array(n); for (var i = 0; i < n; i++) a[i] = i;
    if (cache) cache[cacheKey] = a;
    return a;
  }
  var ord = [], top = [];
  act.forEach(function (s) {
    ((String((getSpec(s).kind || s.kind || "in")).toLowerCase() === "top_n") ? top : ord).push(s);
  });
  var keep = new Array(n);
  for (var r = 0; r < n; r++) keep[r] = true;
  ord.forEach(function (s) {
    var spec = getSpec(s);
    var kind = String(spec.kind || s.kind || "in").toLowerCase();
    var c = ci[s.column];
    if (c === undefined) return;
    var col = payload.rows[c] || [];
    var v = sel && sel[s.uid] !== undefined ? sel[s.uid] : null;
    var vals = (v !== null && kind !== "range" && kind !== "search") ? v : spec.values;
    var i, j, k;
    if (kind === "in" || kind === "not_in") {
      var want = {};
      (Array.isArray(vals) ? vals : [vals]).forEach(function (x) {
        if (x !== null && x !== undefined && String(x) !== "") want[normStr(x)] = true;
      });
      var neg = kind === "not_in";
      for (i = 0; i < n; i++) { if (keep[i]) keep[i] = neg ? !want[normStr(col[i])] : !!want[normStr(col[i])]; }
    } else if (kind === "range") {
      var lo0 = v && v.low !== undefined ? v.low : spec.low;
      var hi0 = v && v.high !== undefined ? v.high : spec.high;
      var lo = numVal(lo0), hi = numVal(hi0);
      var isD = (payload.dtypes || {})[s.column] === "datetime";
      for (j = 0; j < n; j++) {
        if (!keep[j]) continue;
        var cell = col[j];
        if (cell === null || cell === undefined || cell === "") { keep[j] = false; continue; }
        if (isD) {
          var t = Date.parse(cell);
          if (!isFinite(t)) { keep[j] = false; continue; }
          var tL = (lo0 !== null && lo0 !== undefined && lo0 !== "") ? Date.parse(lo0) : NaN;
          var tH = (hi0 !== null && hi0 !== undefined && hi0 !== "") ? Date.parse(hi0) : NaN;
          if (isFinite(tL) && t < tL) keep[j] = false;
          if (isFinite(tH) && t > tH) keep[j] = false;
        } else {
          var q = numVal(cell);
          if (q === null) { keep[j] = false; continue; }
          if (lo !== null && q < lo) keep[j] = false;
          if (hi !== null && q > hi) keep[j] = false;
        }
      }
    } else if (kind === "search") {
      var nd = normStr(v !== null ? v : spec.value);
      if (!nd) return;
      for (k = 0; k < n; k++) { if (keep[k] && normStr(col[k]).indexOf(nd) < 0) keep[k] = false; }
    }
  });
  var res = [];
  for (var m = 0; m < n; m++) if (keep[m]) res.push(m);
  top.forEach(function (s) {
    var spec = getSpec(s);
    var c = ci[s.column];
    if (c === undefined || !res.length) { res = []; return; }
    var col = payload.rows[c] || [];
    var v = sel && sel[s.uid] !== undefined ? sel[s.uid] : null;
    var cnt = parseInt((v && v.n !== undefined ? v.n : spec.n), 10);
    if (!isFinite(cnt) || cnt < 1) return;
    var asc = String((v && v.mode) || spec.mode || (spec.least ? "smallest" : "largest")).toLowerCase().indexOf("small") >= 0;
    res.sort(function (p, q) {
      var x = numVal(col[p]), y = numVal(col[q]);
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return asc ? x - y : y - x;
    });
    res = res.slice(0, cnt);
  });
  if (cache) cache[cacheKey] = res;
  return res;
}
function aggr(payload, idx, dim, met, op) {
  op = String(op || (met ? "mean" : "count")).toLowerCase();
  var ci = {};
  payload.columns.forEach(function (c, i) { ci[c] = i; });
  var dC = dim && ci[dim] !== undefined ? payload.rows[ci[dim]] : null;
  var mC = met && ci[met] !== undefined ? payload.rows[ci[met]] : null;
  var g = {}, order = [];
  idx.forEach(function (r) {
    var raw = dC ? dC[r] : "(all)";
    if (raw === null || raw === undefined || raw === "") return;
    var k = strVal(raw);
    if (!Object.prototype.hasOwnProperty.call(g, k)) {
      g[k] = {count: 0, valid: 0, sum: 0, min: Infinity, max: -Infinity, values: []};
      order.push(k);
    }
    var bucket = g[k];
    bucket.count++;
    var q = mC ? numVal(mC[r]) : 1;
    if (q === null) return;
    bucket.valid++;
    bucket.sum += q;
    if (q < bucket.min) bucket.min = q;
    if (q > bucket.max) bucket.max = q;
    if (op === "median") bucket.values.push(q);
  });
  function calc(bucket) {
    if (op === "count") return bucket.count;
    if (!bucket.valid) return null;
    if (op === "sum") return bucket.sum;
    if (op === "min") return bucket.min;
    if (op === "max") return bucket.max;
    if (op === "median") {
      var s = bucket.values.sort(function (a, b) { return a - b; });
      var mid = Math.floor(s.length / 2);
      return s.length % 2 ? s[mid] : (s[mid-1] + s[mid]) / 2;
    }
    return bucket.sum / bucket.valid;
  }
  return order.map(function (k) { return [k, calc(g[k])]; });
}
function sortP(pairs, sort, topN) {
  var m = String(sort || "value_desc").toLowerCase();
  var s = pairs.slice();
  if (m.indexOf("category_asc") >= 0 || m.indexOf("a-z") >= 0) s.sort(function (a, b) { return String(a[0]).localeCompare(String(b[0])); });
  else if (m.indexOf("category_desc") >= 0 || m.indexOf("z-a") >= 0) s.sort(function (a, b) { return String(b[0]).localeCompare(String(a[0])); });
  else if (m.indexOf("value_asc") >= 0) s.sort(function (a, b) { return (a[1] || 0) - (b[1] || 0); });
  else s.sort(function (a, b) { return (b[1] || 0) - (a[1] || 0); });
  if (topN && topN > 0 && s.length > topN) s = s.slice(0, topN);
  return s;
}
function distinctSlicerOptions(payload, slicer, limit) {
  var column = String((slicer || {}).column || "");
  var ci = (payload.columns || []).indexOf(column);
  if (ci < 0) return [];
  var values = payload.rows[ci] || [], dtype = (payload.dtypes || {})[column] || "string";
  var cap = Math.max(1, parseInt(limit, 10) || 5000);
  if (dtype === "boolean") return ["True", "False"];
  var counts = Object.create(null), first = [], rawByKey = Object.create(null);
  for (var i = 0; i < values.length; i++) {
    var value = values[i];
    if (value === null || value === undefined || value === "") continue;
    var key = String(value);
    if (!Object.prototype.hasOwnProperty.call(counts, key)) {
      counts[key] = 0;
      rawByKey[key] = value;
      first.push(key);
    }
    counts[key]++;
  }
  if (dtype === "number") {
    first.sort(function (a, b) { return Number(rawByKey[a]) - Number(rawByKey[b]); });
  } else if (dtype === "datetime") {
    first.sort(function (a, b) { return Date.parse(a) - Date.parse(b); });
  } else {
    // Match the desktop slicer's established default: common text values
    // first, preserving first-seen order for ties.
    first.sort(function (a, b) { return counts[b] - counts[a]; });
  }
  return first.slice(0, cap);
}
var OPMAP = {avg:"mean",average:"mean",mean:"mean",sum:"sum",median:"median",count:"count",min:"min",max:"max"};
var DIMK = ["x","x_cols","index_col","columns_col","geo_col","location_col","color","x_col","source_col","target_col","path_cols","date_col"];
var METK = ["y","y_cols","values_col","value_col","color_col","size_col","y_col"];
function firstCol(cfg, keys) {
  for (var i = 0; i < keys.length; i++) {
    var v = cfg[keys[i]];
    if (Array.isArray(v) && v.length) return String(v[0]);
    if (typeof v === "string" && v && v !== "None") return v;
  }
  return null;
}
function allCols(cfg, keys) {
  var o = [];
  keys.forEach(function (k) {
    var v = cfg[k];
    if (Array.isArray(v)) v.forEach(function (c) { if (c && o.indexOf(c) < 0) o.push(String(c)); });
    else if (typeof v === "string" && v && v !== "None" && o.indexOf(v) < 0) o.push(v);
  });
  return o;
}
function rebuild(model, payload, idx) {
  try {
    var recipe = model.recipe || {}, cfg = recipe.config || {};
    var ct = model.chart_type || recipe.chart_type || "";
    var opRaw = cfg.agg || cfg.agg_func || "mean";
    var op = OPMAP[String(opRaw).toLowerCase()] || "mean";
    var sort = cfg.sort || cfg.sort_by || "value_desc";
    var topN = parseInt(cfg.top_n, 10);
    if (!isFinite(topN) || topN < 1) topN = 0;
    var out = JSON.parse(JSON.stringify(model));
    var dim, met, pairs;
    if (ct === "pie_chart" || ct === "nightingale_rose") {
      dim = firstCol(cfg, DIMK) || ((model.data.dimensions || [])[0]);
      met = firstCol(cfg, METK);
      var hasM = !!(met && payload.columns.indexOf(met) >= 0);
      pairs = sortP(aggr(payload, idx, dim, hasM ? met : null, hasM ? op : "count"), sort, topN);
      var s0 = (model.data.series || [])[0] || {};
      out.data.series = [{id: s0.id || "main", name: s0.name || "value", type: "pie", values: pairs}];
      return out;
    }
    if (ct === "categorical" || ct === "statistical" || ct === "distribution" || ct === "time_series" || ct === "waterfall" || ct === "funnel") {
      var dims = allCols(cfg, DIMK);
      if (!dims.length && model.data.dimensions && model.data.dimensions.length) dims = [String(model.data.dimensions[0])];
      var mets = allCols(cfg, METK);
      dim = dims[0] || null;
      var list = mets.length ? mets : [null];
      var ser = [];
      list.forEach(function (m, si) {
        var pr = sortP(aggr(payload, idx, dim, m, m ? op : "count"), sort, topN);
        var og = (model.data.series || [])[si] || {};
        ser.push({id: og.id || ("series-" + si), name: og.name || (m || "Count"),
          type: og.type || (ct === "distribution" ? "line" : "bar"), values: pr});
      });
      out.data.series = ser;
      return out;
    }
    if (ct === "sankey") {
      var src = cfg.source_col, dst = cfg.target_col, val = cfg.value_col;
      if (!src || !dst || payload.columns.indexOf(src) < 0 || payload.columns.indexOf(dst) < 0) return null;
      var si0 = payload.columns.indexOf(src), di0 = payload.columns.indexOf(dst), vi0 = val ? payload.columns.indexOf(val) : -1;
      var links = {}, nodes = {};
      idx.forEach(function (r) { var a = strVal(payload.rows[si0][r]), b = strVal(payload.rows[di0][r]); if (!a || !b) return;
        var key = a + "\u0000" + b; if (!links[key]) links[key] = {source:a,target:b,values:[]};
        links[key].values.push(vi0 >= 0 ? numVal(payload.rows[vi0][r]) : 1); nodes[a]=true; nodes[b]=true; });
      var outLinks = Object.keys(links).map(function (key) { var q=links[key], ns=q.values.filter(function(v){return v!==null;});
        var value=op==="count"?q.values.length:op==="sum"?ns.reduce(function(a,b){return a+b;},0):ns.length?ns.reduce(function(a,b){return a+b;},0)/ns.length:0;
        return {source:q.source,target:q.target,value:value}; });
      out.data.sankey={nodes:Object.keys(nodes).map(function(name){return {name:name};}),links:outLinks}; return out;
    }
    if (ct === "sunburst") {
      var paths=allCols(cfg,["path_cols"]); if(!paths.length) paths=(model.data.dimensions||[]).slice(0,3);
      var cols=paths.map(function(c){return payload.columns.indexOf(c);}), mv=firstCol(cfg,METK), mi=mv?payload.columns.indexOf(mv):-1, groups={};
      idx.forEach(function(r){var key=cols.map(function(c){return c<0?"":strVal(payload.rows[c][r]);});if(key.some(function(v){return !v;}))return;
        var k=JSON.stringify(key);if(!groups[k])groups[k]={key:key,values:[]};groups[k].values.push(mi<0?1:numVal(payload.rows[mi][r]));});
      var root={name:"All",children:[]};
      Object.keys(groups).forEach(function(k){var g=groups[k],node=root;g.key.forEach(function(name){var child=node.children.find(function(c){return c.name===name;});if(!child){child={name:name,children:[]};node.children.push(child);}node=child;});var ns=g.values.filter(function(v){return v!==null;});node.value=op==="sum"?ns.reduce(function(a,b){return a+b;},0):op==="count"?g.values.length:ns.length?ns.reduce(function(a,b){return a+b;},0)/ns.length:0;});
      function finish(n){if(n.children&&n.children.length){n.children.forEach(finish);n.value=n.children.reduce(function(a,b){return a+(b.value||0);},0);}else delete n.children;}
      finish(root);out.data.tree=[root];return out;
    }
    if (ct === "calendar_heatmap") {
      var dc=cfg.date_col, dci=payload.columns.indexOf(dc), vc=firstCol(cfg,METK), vci=vc?payload.columns.indexOf(vc):-1, days={};
      if(dci<0)return null;idx.forEach(function(r){var raw=strVal(payload.rows[dci][r]),date=raw.slice(0,10);if(!date)return;if(!days[date])days[date]=[];days[date].push(vci<0?1:numVal(payload.rows[vci][r]));});
      var daily=Object.keys(days).sort().map(function(day){var ns=days[day].filter(function(v){return v!==null;});var value=op==="sum"?ns.reduce(function(a,b){return a+b;},0):op==="count"?days[day].length:ns.length?ns.reduce(function(a,b){return a+b;},0)/ns.length:0;return [day,value];});
      out.data.calendar={values:daily,range:daily.length?[daily[0][0],daily[daily.length-1][0]]:[]};return out;
    }
    /* Chart types with no browser-side rebuild are left to the caller:
     * returning null tells it to keep the server-built model instead of
     * painting a half-recomputed chart. */
    return null;
  } catch (e) {
    return null;
  }
}
function kNums(payload, idx, col) {
  var c = payload.columns.indexOf(col);
  if (c < 0) return [];
  var arr = payload.rows[c] || [], o = [];
  idx.forEach(function (r) { var q = numVal(arr[r]); if (q !== null) o.push(q); });
  return o;
}
function nnCount(payload, idx, col) {
  if (!col) return idx.length;
  var c = payload.columns.indexOf(col);
  if (c < 0) return 0;
  var arr = payload.rows[c] || [], n = 0;
  idx.forEach(function (r) { var v = arr[r]; if (v !== null && v !== undefined) n++; });
  return n;
}
function fNum(v) {
  /* Mirrors kpi_service.fmt_num: integral counts stay integral and the
   * magnitude picks the precision. */
  if (v === null || v === undefined || !isFinite(v)) return "--";
  if (Number.isInteger(v) && Math.abs(v) < 1e15) return Math.trunc(v).toLocaleString("en-US");
  var a = Math.abs(v), d;
  if (a >= 1000) d = 0; else if (a >= 100) d = 1; else if (a >= 1) d = 2;
  else if (a >= 0.001) d = 4; else return v.toExponential(2);
  return Number(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
}
function fPct(p) {
  /* Mirrors kpi_service.fmt_pct: signed one-decimal percent. */
  if (p === null || p === undefined || !isFinite(p)) return "--";
  return (p >= 0 ? "+" : "") + p.toFixed(1);
}
function kpiShare(recipe, payload, idx, col) {
  /* Mirrors kpi_service._kpi_share: the share of this (slicer-filtered)
   * frame's total that sits on one value of the group column. */
  var groupCol = recipe.group_col || null, filterVal = recipe.filter_val;
  var ci = payload.columns.indexOf(col);
  var gi = groupCol ? payload.columns.indexOf(groupCol) : -1;
  if (!groupCol || ci < 0 || gi < 0) return {value: "0.0", delta: null};
  var match = filterVal !== null && filterVal !== undefined && filterVal !== "";
  var want = strVal(filterVal), total = 0, sub = 0;
  idx.forEach(function (r) {
    var q = numVal(payload.rows[ci][r]); if (q === null) return;
    total += q;
    if (match && strVal(payload.rows[gi][r]) === want) sub += q;
  });
  if (!total) return {value: "0.0", delta: null};
  return {value: fPct(sub / total * 100), delta: null};
}
function kpiTopBot(recipe, payload, idx, t) {
  /* Mirrors kpi_service._kpi_extreme_cat: sum the metric per category and
   * report "Category: total" for the highest (or lowest) category. */
  var groupCol = recipe.group_col || null, metCol = recipe.metric_col || null;
  var gi = groupCol ? payload.columns.indexOf(groupCol) : -1;
  var mi = metCol ? payload.columns.indexOf(metCol) : -1;
  if (gi < 0 || mi < 0) return {value: "--", delta: null};
  var totals = Object.create(null);
  idx.forEach(function (r) {
    var raw = payload.rows[gi][r];
    if (raw === null || raw === undefined || String(raw) === "") return;
    var q = numVal(payload.rows[mi][r]); if (q === null) return;
    var k = strVal(raw);
    totals[k] = (totals[k] || 0) + q;
  });
  var keys = Object.keys(totals);
  if (!keys.length) return {value: "--", delta: null};
  var top = String(t).indexOf("top") >= 0;
  keys.sort(function (a, b) { return top ? totals[b] - totals[a] : totals[a] - totals[b]; });
  var best = keys[0];
  return {value: best + ": " + fNum(totals[best]), delta: null};
}
function kpiPeriod(recipe, payload, idx, t, col) {
  /* Mirrors kpi_service._kpi_period_change: percentage change of the latest
   * calendar period against the one immediately before it. An absent month or
   * year does not shift the pairing. */
  var isYear = String(t).indexOf("year") >= 0 || String(t) === "yoy";
  var dateCol = recipe.filter_col || recipe.trend_date_col || null;
  if (!dateCol) {
    dateCol = (payload.columns || []).filter(function (name) {
      return (payload.dtypes || {})[name] === "datetime";
    })[0] || null;
  }
  var ci = col ? payload.columns.indexOf(col) : -1;
  var di = dateCol ? payload.columns.indexOf(dateCol) : -1;
  if (ci < 0 || di < 0) return {value: "--", delta: null};
  var dates = payload.rows[di] || [], values = payload.rows[ci] || [];
  var buckets = Object.create(null);
  idx.forEach(function (r) {
    var time = Date.parse(dates[r]), q = numVal(values[r]);
    if (!isFinite(time) || q === null) return;
    var d = new Date(time);
    var key = isYear
      ? String(d.getFullYear())
      : d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
    buckets[key] = (buckets[key] || 0) + q;
  });
  var keys = Object.keys(buckets).sort();
  if (keys.length < 2) return {value: "--", delta: null};
  var latest = keys[keys.length - 1], previous = keys[keys.length - 2];
  if (isYear) {
    if (Number(latest) - Number(previous) !== 1) return {value: "--", delta: null};
  } else {
    var lp = latest.split("-"), pp = previous.split("-");
    var expected = Number(lp[0]) * 12 + Number(lp[1]) - 1;
    var actual = Number(pp[0]) * 12 + Number(pp[1]) - 1;
    if (expected - actual !== 1) return {value: "--", delta: null};
  }
  var prev = buckets[previous];
  if (!prev) return {value: "--", delta: null};
  var pct = (buckets[latest] - prev) / Math.abs(prev) * 100;
  return {value: fPct(pct), delta: fPct(pct) + "%"};
}
function kPer(payload, idx, dCol, vCol) {
  var cd = payload.columns.indexOf(dCol), cv = payload.columns.indexOf(vCol);
  var per = {};
  if (cd < 0 || cv < 0) return per;
  var da = payload.rows[cd] || [], va = payload.rows[cv] || [];
  idx.forEach(function (r) {
    var t = Date.parse(da[r]);
    if (!isFinite(t)) return;
    var d = new Date(t);
    var k = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2);
    var q = numVal(va[r]);
    if (q === null) return;
    per[k] = (per[k] || 0) + q;
  });
  return per;
}
function fN(v, d) {
  if (v === null || v === undefined || !isFinite(v)) return "--";
  return Number(v).toLocaleString("en-US", {maximumFractionDigits: d !== undefined ? d : 2});
}

function kpiSimple(t, list) {
  var emp = {value: "--", delta: null};
  if (!list.length) return emp;
  var s = list.reduce(function (a, b) { return a + b; }, 0);
  if (t === "sum" || (t.indexOf("total") >= 0 && t.indexOf("share") < 0)) return {value: fN(s), delta: null};
  if (t === "mean" || t.indexOf("average") >= 0) return {value: fN(s / list.length), delta: null};
  if (t === "median") {
    var o = list.slice().sort(function (a, b) { return a - b; });
    var m = Math.floor(o.length / 2);
    return {value: fN(o.length % 2 ? o[m] : (o[m - 1] + o[m]) / 2), delta: null};
  }
  if (t === "min" || t.indexOf("minimum") >= 0) return {value: fN(Math.min.apply(null, list)), delta: null};
  if (t === "max" || t.indexOf("maximum") >= 0) return {value: fN(Math.max.apply(null, list)), delta: null};
  if (t === "std" || t.indexOf("std") >= 0) {
    var mu = s / list.length;
    var va = list.reduce(function (a, b) { return a + (b - mu) * (b - mu); }, 0) / Math.max(1, list.length - 1);
    return {value: fN(Math.sqrt(va)), delta: null};
  }
  return {value: fN(s), delta: null};
}
function kpiMain(recipe, payload, idx) {
  recipe = recipe || {};
  var t = String(recipe.kpi_type || recipe.type || "sum").toLowerCase();
  var emp = {value: "--", delta: null};
  var col = recipe.col || null;
  if (t.indexOf("count") >= 0 && t.indexOf("row") >= 0) return {value: String(idx.length), delta: null};
  /* Plain "count" is the row count (kpi_service._kpi_count returns df.height);
   * without this it fell through to kpiSimple's sum fallback. */
  if (t === "count") return {value: String(idx.length), delta: null};
  if (t === "count_nonnull" || t.indexOf("non-empty") >= 0) return {value: String(nnCount(payload, idx, col)), delta: null};
  if (t === "nunique" || t.indexOf("unique") >= 0) {
    var ci = payload.columns.indexOf(col);
    if (ci < 0) return emp;
    var arr = payload.rows[ci] || [], seen = {};
    idx.forEach(function (r) { var v = arr[r]; if (v !== null && v !== undefined && String(v) !== "") seen[normStr(v)] = true; });
    return {value: String(Object.keys(seen).length), delta: null};
  }
  if (t === "missing" || t.indexOf("missing") >= 0) {
    var cm = payload.columns.indexOf(col);
    if (cm < 0) return emp;
    var am = payload.rows[cm] || [], n = 0;
    idx.forEach(function (r) { var v = am[r]; if (v === null || v === undefined || String(v) === "") n++; });
    return {value: String(n), delta: null};
  }
  if (t === "date_range" || t.indexOf("date range") >= 0) {
    var cd = payload.columns.indexOf(col);
    if (cd < 0) return emp;
    var ad = payload.rows[cd] || [], ts = [];
    idx.forEach(function (r) { var x = Date.parse(ad[r]); if (isFinite(x)) ts.push(x); });
    if (!ts.length) return emp;
    return {value: Math.round((Math.max.apply(null, ts) - Math.min.apply(null, ts)) / 86400000) + " days", delta: null};
  }
  var nums = col ? kNums(payload, idx, col) : [];
  if (t === "sum" || t === "mean" || t === "median" || t === "min" || t === "max" || t === "std" ||
      t.indexOf("total (sum)") >= 0 || t.indexOf("average") >= 0) return kpiSimple(t, nums);
  if (t === "share" || t.indexOf("share") >= 0 || t.indexOf("% of total") >= 0) return kpiShare(recipe, payload, idx, col);
  if (t === "top_cat" || t === "bottom_cat" || t.indexOf("top category") >= 0 || t.indexOf("bottom category") >= 0) {
    return kpiTopBot(recipe, payload, idx, t);
  }
  if (t === "mom" || t === "yoy" || t.indexOf("month") >= 0 || t.indexOf("year") >= 0) {
    return kpiPeriod(recipe, payload, idx, t, col);
  }
  if (t === "running" || t.indexOf("running") >= 0) {
    var pr = kPer(payload, idx, recipe.filter_col, col);
    var kk = Object.keys(pr).sort();
    if (!kk.length) return emp;
    return {value: fN(kk.reduce(function (a, k) { return a + pr[k]; }, 0)), delta: null};
  }
  if (t === "custom" || t.indexOf("custom") >= 0) {
    var f = String(recipe.formula || "");
    var mm = f.match(/^(SUM|AVG|MEAN|MIN|MAX|COUNT)\s*\(\s*([^)]+)\s*\)$/i);
    if (mm) {
      var fn = mm[1].toUpperCase();
      var tg = mm[2].trim().replace(/^["']|["']$/g, "");
      var li = kNums(payload, idx, tg);
      if (fn === "COUNT") return {value: String(li.length), delta: null};
      if (!li.length) return emp;
      var s3 = li.reduce(function (a, b) { return a + b; }, 0);
      if (fn === "SUM") return {value: fN(s3), delta: null};
      if (fn === "AVG" || fn === "MEAN") return {value: fN(s3 / li.length), delta: null};
      if (fn === "MIN") return {value: fN(Math.min.apply(null, li)), delta: null};
      if (fn === "MAX") return {value: fN(Math.max.apply(null, li)), delta: null};
    }
    return {value: "n/a", delta: null};
  }
  return kpiSimple(t, nums);
}
function kpiTrend(recipe, payload, idx) {
  recipe = recipe || {};
  var dateCol = recipe.trend_date_col || recipe.filter_col || null;
  if (!dateCol) dateCol = (payload.columns || []).filter(function (name) {
    return (payload.dtypes || {})[name] === "datetime";
  })[0] || null;
  var valueCol = recipe.col || recipe.metric_col || null;
  var di = (payload.columns || []).indexOf(dateCol), vi = (payload.columns || []).indexOf(valueCol);
  if (di < 0 || vi < 0 || idx.length < 2) return null;
  var dates = payload.rows[di] || [], values = payload.rows[vi] || [], grouped = Object.create(null);
  idx.forEach(function (row) {
    var time = Date.parse(dates[row]), value = numVal(values[row]);
    if (!isFinite(time) || value === null) return;
    var d = new Date(time), key = d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0");
    if (!grouped[key]) grouped[key] = {sum: 0, n: 0};
    grouped[key].sum += value; grouped[key].n++;
  });
  var keys = Object.keys(grouped).sort();
  if (keys.length < 2) return null;
  var mean = String(recipe.kpi_key || recipe.kpi_type || "").toLowerCase().indexOf("mean") >= 0;
  var trend = keys.slice(-24).map(function (key) {
    var bucket = grouped[key]; return [key, mean ? bucket.sum / bucket.n : bucket.sum];
  });
  var first = trend[0][1], last = trend[trend.length - 1][1];
  return {trend: trend, trend_direction: last >= first ? "up" : "down",
    trend_change_pct: first ? ((last - first) / Math.abs(first)) * 100 : null};
}
function recomputeKpi(recipe, payload, idx) {
  var result = kpiMain(recipe, payload, idx), trend = kpiTrend(recipe, payload, idx);
  if (trend) {
    result.trend = trend.trend;
    result.trend_direction = trend.trend_direction;
    result.trend_change_pct = trend.trend_change_pct;
    if (result.delta === null || result.delta === undefined) {
      var tdir = trend.trend_direction === "down" ? "down" : "up";
      result.delta = trend.trend_change_pct === null || trend.trend_change_pct === undefined
        ? (tdir === "up" ? "\u25b2" : "\u25bc")
        : ((tdir === "up" ? "\u25b2 " : "\u25bc ") + Math.abs(trend.trend_change_pct).toFixed(1) + "%");
    }
  }
  return result;
}
global.LytrizeLiveFilter = {filterRowIndexes: filterIdx, activeSlicersForChart: activeFor,
  hasActiveSlicers: hasActive, aggregate: aggr, sortPairs: sortP, rebuildModel: rebuild,
  recomputeKpi: recomputeKpi, distinctSlicerOptions: distinctSlicerOptions};
})(typeof self !== "undefined" ? self : window);
