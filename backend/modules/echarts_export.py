"""Self-contained HTML export for validated semantic chart models (static snapshots + optional live slicer/KPI engine)."""

from __future__ import annotations

import base64
import json
import math
import re
from html import escape
from pathlib import Path

from modules.board_model import validate_board
from modules.chart_model import validate_chart_model
from modules.runtime_paths import assets_root


def _portable_image_uri(value: object) -> str:
    """Validate bounded base64 raster data for fully portable HTML exports."""
    if not isinstance(value, str):
        raise ValueError("Portable images must be embedded as data:image URIs")
    match = re.fullmatch(
        r"data:image/(?:png|jpeg|webp|gif);base64,([A-Za-z0-9+/=]+)", value,
        re.I)
    if not match or len(match.group(1)) > 16 * 1024 * 1024:
        raise ValueError("Image must be a supported base64 raster under 12 MiB")
    try:
        base64.b64decode(match.group(1), validate=True)
    except Exception as exc:
        raise ValueError("Image data is not valid base64") from exc
    return value


def _script_safe(value: str) -> str:
    """Keep serialized JSON from terminating its containing script element."""
    return (value.replace("<", "\\u003c")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _map_geojson_for_models(models) -> dict:
    """Load only allow-listed bundled map assets needed by exported models."""
    root = assets_root() / "echarts" / "maps" / "natural-earth"
    allowed = {"countries-110m": "countries-110m.geojson",
               "admin1-50m": "admin1-50m.geojson"}
    requested = set()
    for model in models:
        map_data = (model.get("data") or {}).get("map") or {}
        asset = map_data.get("asset")
        if model.get("chart_type") in {"map_plot"} and asset in allowed:
            requested.add(asset)
    return {asset: json.loads((root / allowed[asset]).read_text(encoding="utf-8"))
            for asset in sorted(requested)}


def _map_register_script(assets: dict) -> str:
    ids = {"countries-110m": "lytrize_ne_countries_110m",
           "admin1-50m": "lytrize_ne_admin1_50m"}
    payload = _script_safe(json.dumps(assets, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False))
    return ("const lytrizeMapAssets=JSON.parse(" + json.dumps(payload) + ");" +
            "Object.keys(lytrizeMapAssets).forEach(function(asset){" +
            "const id=asset==='admin1-50m'?" + json.dumps(ids["admin1-50m"]) +
            ":" + json.dumps(ids["countries-110m"]) +
            ";echarts.registerMap(id,{geoJSON:lytrizeMapAssets[asset]});});")


def _map_dataset_notice() -> str:
    metadata = assets_root() / "echarts" / "maps" / "natural-earth" / "metadata.json"
    try:
        details = json.loads(metadata.read_text(encoding="utf-8"))
        return (f"Natural Earth Vector {details.get('version', 'unknown')}; "
                f"{details.get('attribution', 'Public domain')}; generalized cartographic boundaries")
    except Exception:
        return "Natural Earth Vector; generalized cartographic boundaries; public domain"


def _kpi_trend_fragment(trend, direction, props):
    """Pre-render a KPI's trend sparkline and +/- delta for the static file.

    The live-filter worker repaints both from the same fields on every
    selection change, so a static export and an interactive one agree.
    Returns ``(sparkline_inner_svg, delta_html, delta_css_class, aria_label)``;
    callers keep the card readable when there is nothing to draw.
    """
    try:
        values = []
        for pair in list(trend or []):
            try:
                v = float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
            if math.isfinite(v):
                values.append(v)
        direction = str(direction or "").lower()
        if len(values) < 2:
            delta = str(props.get("delta") or "")
            cls = "up" if (direction == "up" or (delta and delta[0] == "\u25b2")) else (
                "down" if (direction == "down" or (delta and delta[0] == "\u25bc")) else "")
            return "", escape(delta), cls, "KPI trend"
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        n = len(values)
        pts = " ".join(
            f"{4 + i * 112 / (n - 1):.1f},{28 - (v - lo) * 22 / span:.1f}"
            for i, v in enumerate(values))
        up = (direction == "up") if direction in {"up", "down"} else values[-1] >= values[0]
        color = "#22c55e" if up else "#ef4444"
        spark = (f'<polyline points="{pts}" fill="none" stroke="{color}" '
                 f'stroke-width="2.5" stroke-linecap="round" '
                 f'stroke-linejoin="round"/>')
        pct = props.get("trend_change_pct")
        arrow = "\u25b2" if up else "\u25bc"
        pct_f = None
        try:
            pct_f = abs(float(pct)) if pct is not None else None
        except (TypeError, ValueError):
            pct_f = None
        delta_text = f"{arrow} {pct_f:.1f}%" if pct_f is not None else arrow
        aria = "Positive trend" if up else "Negative trend"
        if pct_f is not None:
            aria += f": {pct_f:.1f} percent"
        return spark, escape(delta_text), ("up" if up else "down"), aria
    except Exception:
        return "", escape(str(props.get("delta") or "")), "", "KPI trend"


def _table_model_fragment(model: dict) -> str:
    """Render a validated native table model as safe, scrollable HTML."""
    data = model.get("data") or {}
    headers = list(data.get("dimensions") or [])
    series = list(data.get("series") or [])
    rows = list(series[0].get("values") or []) if series else []
    display = model.get("display") or {}
    hierarchy = list(display.get("table_hierarchy") or [])

    theme_mode = str(display.get("theme_mode", "dark")).lower()

    def safe_color(key: str, default: str) -> str:
        value = str(display.get(key, default))
        if value.lower() in {"", "auto", "none"}:
            return {"table_font_color": "#1f2937" if theme_mode == "light" else "#f1f5f9",
                    "text_color": "#1f2937" if theme_mode == "light" else "#e2e8f0",
                    "table_stripe_even_color": "#ffffff" if theme_mode == "light" else "#1e293b",
                    "table_stripe_odd_color": "#f8fafc" if theme_mode == "light" else "#0f172a"}.get(key, default)
        if theme_mode == "light":
            legacy = {"table_font_color": {"#f1f5f9", "#e2e8f0"},
                      "text_color": {"#f1f5f9", "#e2e8f0"},
                      "table_stripe_even_color": {"#1e293b"},
                      "table_stripe_odd_color": {"#0f172a"}}
            if value.lower() in legacy.get(key, set()):
                return {"table_font_color": "#1f2937", "text_color": "#1f2937",
                        "table_stripe_even_color": "#ffffff",
                        "table_stripe_odd_color": "#f8fafc"}[key]
        return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else default

    def safe_size(key: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(display.get(key, default))))
        except (TypeError, ValueError):
            return default

    number_format = str(display.get("table_number_format", ",.2f"))
    if number_format not in {",.2f", ",.1f", ",.0f", ".2f", ".1f", ".0f"}:
        number_format = ",.2f"
    header_color = safe_color("table_header_color", "#6163df")
    font_color = safe_color("table_font_color", "#f1f5f9")
    even_color = safe_color("table_stripe_even_color", "#1e293b")
    odd_color = safe_color("table_stripe_odd_color", "#0f172a")
    header_size = safe_size("table_header_font_size", 12, 8, 24)
    font_size = safe_size("table_font_size", 11, 8, 24)
    typography = ((model.get("recipe") or {}).get("typography") or {})
    table_font = re.sub(r"[^A-Za-z0-9 _-]", "", str(
        display.get("table_font_family", typography.get("family", "Inter")))) or "Inter"
    table_header_font = re.sub(r"[^A-Za-z0-9 _-]", "", str(
        display.get("table_header_font_family", typography.get("header_family", table_font)))) or table_font
    row_height = safe_size("table_row_height", 26, 16, 60)
    header_height = safe_size("table_header_height", 28, 16, 60)
    align_first = display.get("table_index_align", "left")
    align_data = display.get("table_data_align", "right")
    align_first = str(align_first)
    align_data = str(align_data)
    if align_first not in {"left", "center", "right"}:
        align_first = "left"
    if align_data not in {"left", "center", "right"}:
        align_data = "right"

    def cell_text(value):
        if value is None:
            return "—"
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, int):
            return format(value, ",")
        if isinstance(value, (int, float)):
            try:
                return format(value, number_format)
            except (TypeError, ValueError):
                pass
        return str(value)

    # Title typography (Typography tab: Header size/color/font/style/align).
    # Auto colors ride the text_color legacy remap so light/dark stays legible.
    _raw_title_color = str(display.get("title_color", "auto")).strip().lower()
    if _raw_title_color in {"", "auto", "none"}:
        title_color = safe_color("text_color", "#e2e8f0")
    else:
        title_color = safe_color("title_color", safe_color("text_color", "#e2e8f0"))
    _title_style = str(display.get("title_font_style",
                                   typography.get("header_font_style", "Bold"))).lower()
    title_weight = "700" if "bold" in _title_style else "600"
    title_font_style = "italic" if "italic" in _title_style else "normal"
    title_align = str(display.get("title_align", "left")).strip().lower()
    if title_align not in {"left", "center", "right"}:
        title_align = "left"
    title = escape(str(model.get("title") or "Table"))
    head = "".join(
        f'<th scope="col" tabindex="0" data-sort-column="{index}" '
        'aria-sort="none" title="Sort by this column">'
        f'{escape(str(value))}<span class="sort-indicator" aria-hidden="true"></span></th>'
        for index, value in enumerate(headers))
    body_rows = []
    for index, row in enumerate(rows):
        bg = even_color if index % 2 == 0 else odd_color
        cells = []
        for column, value in enumerate(row):
            align = align_first if column == 0 else align_data
            content = escape(cell_text(value))
            if column == 0 and index < len(hierarchy):
                meta = hierarchy[index]
                depth = max(0, min(2, int(meta.get("depth", 0))))
                if meta.get("has_children"):
                    content = (f'<button type="button" class="tree-toggle" aria-expanded="false" '
                               f'data-row="{index}" aria-label="Expand {content}">▸</button>') + content
                cells.append(f'<td class="tree-cell" data-depth="{depth}" style="text-align:{align};background:{bg};">{content}</td>')
            else:
                cells.append(f'<td style="text-align:{align};background:{bg};">{content}</td>')
        depth = int(hierarchy[index].get("depth", 0)) if index < len(hierarchy) else 0
        hidden = ' hidden' if hierarchy and depth > 0 else ''
        body_rows.append(f'<tr data-row="{index}" data-depth="{depth}"{hidden}>' + "".join(cells) + "</tr>")
    return (
        f'<div class="table-model" style="color:{font_color};font-size:{font_size}px;font-family:\'{table_font}\',sans-serif">'
        f'<h2 style="font-size:{safe_size("title_size", 20, 8, 48)}px;text-align:{title_align};color:{title_color};font-family:\'{table_header_font}\',sans-serif;font-weight:{title_weight};font-style:{title_font_style}">{title}</h2>'
        '<div class="table-scroll"><table aria-label="Sortable pivot table"><thead><tr>'
        f'{head}</tr></thead><tbody>{"".join(body_rows)}</tbody></table></div>'
        f'<style>.table-model{{height:100%;display:flex;flex-direction:column;padding:8px;box-sizing:border-box;overflow:hidden}}'
        f'.table-model h2{{margin:0 0 8px;color:{safe_color("text_color", "#e2e8f0")};font-family:\'{table_header_font}\',sans-serif;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}'
        '.table-scroll{overflow:auto;flex:1;min-height:0}table{border-collapse:collapse;min-width:max-content;width:100%}'
        f'th{{position:sticky;top:0;z-index:1;height:{header_height}px;padding:4px 8px;background:{header_color};color:white;font-size:{header_size}px;text-align:left;white-space:nowrap;cursor:pointer;user-select:none}}th:focus-visible{{outline:2px solid #fff;outline-offset:-2px}}.sort-indicator{{display:inline-block;min-width:1em;margin-left:4px}}'
        f'td{{height:{row_height}px;padding:3px 8px;color:{font_color};white-space:nowrap;border-bottom:1px solid rgba(100,116,139,.28)}}'
        f'.tree-cell{{padding-left:calc(8px + var(--depth,0)*16px)}}.tree-cell[data-depth="1"]{{padding-left:24px}}.tree-cell[data-depth="2"]{{padding-left:40px}}'
        '.tree-toggle{border:0;background:transparent;color:inherit;cursor:pointer;margin-right:5px;font:inherit}.table-model tr[hidden]{display:none}'
        '</style></div>'
        '<script>(function(){var root=document.currentScript.previousElementSibling;'
        'var table=root&&root.querySelector("table");if(!table)return;'
        'var heads=table.querySelectorAll("th[data-sort-column]");'
        'function sort(head){if(table.querySelector(".tree-toggle"))return;var col=Number(head.getAttribute("data-sort-column"));'
        'var dir=head.getAttribute("aria-sort")==="ascending"?"descending":"ascending";'
        'heads.forEach(function(h){h.setAttribute("aria-sort","none");'
        'var mark=h.querySelector(".sort-indicator");if(mark)mark.textContent="";});'
        'head.setAttribute("aria-sort",dir);var mark=head.querySelector(".sort-indicator");'
        'if(mark)mark.textContent=dir==="ascending"?"▲":"▼";'
        'var body=table.tBodies[0];if(!body)return;var rows=Array.prototype.slice.call(body.rows);'
        'rows.sort(function(a,b){var x=(a.cells[col]&&a.cells[col].textContent||"").trim();'
        'var y=(b.cells[col]&&b.cells[col].textContent||"").trim();'
        'var nx=Number(x.replace(/,/g,"")),ny=Number(y.replace(/,/g,""));'
        'var cmp=Number.isFinite(nx)&&Number.isFinite(ny)?nx-ny:x.localeCompare(y,undefined,{numeric:true,sensitivity:"base"});'
        'return dir==="ascending"?cmp:-cmp;});rows.forEach(function(row){body.appendChild(row);});}'
        'heads.forEach(function(head){head.addEventListener("click",function(){sort(head);});'
        'head.addEventListener("keydown",function(event){if(event.key==="Enter"||event.key===" "){event.preventDefault();sort(head);}});});'
        'var expanded=new Set();function refreshTree(){var rows=Array.prototype.slice.call(table.tBodies[0].rows),stack=[];'
        'rows.forEach(function(row,i){var depth=Number(row.getAttribute("data-depth")||0);stack=stack.slice(0,depth);'
        'row.hidden=!stack.every(function(parent){return expanded.has(parent);});var button=row.querySelector(".tree-toggle");'
        'if(button){button.setAttribute("aria-expanded",expanded.has(i)?"true":"false");button.textContent=expanded.has(i)?"▾":"▸";}stack[depth]=i;});}'
        'table.querySelectorAll(".tree-toggle").forEach(function(button){button.addEventListener("click",function(){var i=Number(button.getAttribute("data-row"));'
        'if(expanded.has(i))expanded.delete(i);else expanded.add(i);refreshTree();});});})();</script>')


def chart_model_to_html(model: dict) -> str:
    """Return an offline, one-chart HTML document with embedded runtime/data.

    This is the static share mode: it embeds only the materialized chart model,
    never the source dataframe or a Python connection. Slicers are not live in
    this first export slice.
    """
    normalized = validate_chart_model(model)
    online_imagery = (
        normalized.get("chart_type") in {"map_plot"} and
        (normalized.get("display") or {}).get("map_basemap") == "satellite")
    image_sources = "data: blob: https://services.arcgisonline.com" if online_imagery else "data: blob:"
    if normalized.get("chart_type") == "data_table":
        title = escape(str(normalized.get("title") or "Lytrize table"))
        return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">"
                f"<title>{title}</title><style>html,body{{height:100%;margin:0;background:{'#fff' if str((normalized.get('display') or {}).get('theme_mode','dark')).lower() == 'light' else '#0f172a'};font-family:Inter,'Noto Sans',sans-serif}}</style>"
                f"</head><body>{_table_model_fragment(normalized)}</body></html>")
    asset_dir = assets_root() / "echarts"
    echarts_path = asset_dir / "echarts.min.js"
    adapter_path = asset_dir / "lytrize_adapter.js"
    map3d_path = asset_dir / "lytrize_map_3d.js"
    license_path = asset_dir / "LICENSE"
    notice_path = asset_dir / "NOTICE"
    d3_license_path = asset_dir / "LICENSE-d3"
    if not all(path.is_file() for path in
               (echarts_path, adapter_path, map3d_path, license_path, notice_path,
                d3_license_path)):
        raise FileNotFoundError("The bundled ECharts runtime is incomplete")

    echarts_source = echarts_path.read_text(encoding="utf-8")
    adapter_source = adapter_path.read_text(encoding="utf-8")
    map3d_source = map3d_path.read_text(encoding="utf-8")
    # Script tags below contain only trusted, pinned application assets.
    echarts_source = echarts_source.replace("</script", "<\\/script")
    adapter_source = adapter_source.replace("</script", "<\\/script")
    map3d_source = map3d_source.replace("</script", "<\\/script")
    payload = _script_safe(json.dumps(normalized, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False))
    geojson = _map_geojson_for_models([normalized])
    map_register = _map_register_script(geojson)
    map_notice = escape(_map_dataset_notice())
    title = escape(str(normalized.get("title") or "Lytrize chart"))
    notices = "\n\n".join(
        f"--- {path.name} ---\n{path.read_text(encoding='utf-8')}"
        for path in (license_path, notice_path, d3_license_path)
    ) + "\n\n--- Map boundary data ---\n" + _map_dataset_notice()
    notices_html = escape(notices)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' qrc:; style-src 'unsafe-inline'; img-src {image_sources}; font-src data: file:;">
<meta name="lytrize-online-imagery" content="{str(online_imagery).lower()}">
<meta name="generator" content="Lytrize static chart export">
<meta name="lytrize-map-data" content="{map_notice}">
<title>{title}</title>
<style>html,body,#chart{{width:100%;height:100%;margin:0;overflow:hidden;background:transparent}}body{{font-family:Inter,'Noto Sans',sans-serif}}</style>
</head><body><div id="chart" role="img" aria-label="Interactive chart"></div>
<template id="open-source-notices"><pre>{notices_html}</pre></template>
<script>{echarts_source}</script><script>{adapter_source}</script><script>{map3d_source}</script>
<script>
(function(){{
  {map_register}
  const model=JSON.parse({json.dumps(payload)});
  const root=document.getElementById('chart');
  let chart=null, timer=0;
  function draw(){{
    const width=root.clientWidth, height=root.clientHeight;
    if(!width||!height) return;
    const mapView=String((model.display&&model.display.map_visualization)||'').toLowerCase();
    if(mapView==='globe'||mapView==='extruded'){{
      if(chart){{chart.dispose();chart=null;}}
      const registered=echarts.getMap(model.data.map.id);
      if(!registered||!window.Lytrize3DMap){{root.textContent='3D map boundary data could not be loaded.';return;}}
      Lytrize3DMap.render(root,model,registered.geoJSON||registered.geoJson||registered);return;
    }}
    const renderer=model.display&&model.display.renderer==='svg'?'svg':'canvas';
    if(chart&&chart.__lytrizeRenderer!==renderer){{chart.dispose();chart=null;}}
    if(!chart){{chart=echarts.init(root,null,{{renderer:renderer}});chart.__lytrizeRenderer=renderer;}}
    chart.setOption(LytrizeEChartsAdapter.buildOption(model,width,height,root),{{notMerge:true}});
    chart.resize({{width:width,height:height}});
    LytrizeEChartsAdapter.applySatelliteBasemap(chart,model,root);
    if(!chart.__lytrizeMapRoamBound){{chart.__lytrizeMapRoamBound=true;
      chart.on('georoam',function(){{LytrizeEChartsAdapter.applySatelliteBasemap(chart,model,root);}});}}
  }}
  new ResizeObserver(function(){{clearTimeout(timer);timer=setTimeout(draw,80);}}).observe(root);
  window.addEventListener('beforeunload',function(){{if(chart) chart.dispose();}});
  document.fonts&&document.fonts.ready.then(draw);
}})();
</script></body></html>'''


def _live_update_script(livefilter_source: str) -> str:
    """Bind dashboard slicers to a local worker for filter and KPI work."""
    worker_code = livefilter_source + "\n" + _LIVE_FILTER_WORKER
    worker_literal = json.dumps(worker_code).replace("</", "<\\/")
    return _LIVE_UPDATE_JS.replace("__WORKER_SOURCE__", worker_literal)


_LIVE_FILTER_WORKER = r"""
var lyPayload=null,lyMeta=null,lyModels=null;
self.onmessage=function(event){
  var message=event.data||{};
  if(message.type==='init'){
    try{lyPayload=JSON.parse(message.payload);lyMeta=JSON.parse(message.meta);lyModels=JSON.parse(message.models);}
    catch(error){self.postMessage({type:'error',message:'Dashboard data could not be read.'});return;}
    self.postMessage({type:'ready',total:lyPayload.total_rows||0});return;
  }
  if(message.type==='kpis'&&lyMeta){lyMeta.kpis=Array.isArray(message.kpis)?message.kpis:[];self.postMessage({type:'kpis-ready'});return;}
  if(message.type==='slicers'&&lyMeta&&lyPayload){
    lyMeta.slicers=Array.isArray(message.slicers)?message.slicers:[];
    var options={},LF=self.LytrizeLiveFilter;
    lyMeta.slicers.forEach(function(s){
      if(!s||!s.uid||!s.column)return;
      var widget=String(s.widget||'').toLowerCase();
      if(widget==='list'||widget==='dropdown')options[String(s.uid)]=LF.distinctSlicerOptions(lyPayload,s,5000);
    });
    self.postMessage({type:'slicers-ready',revision:message.revision,options:options});return;
  }
  if(message.type!=='filter'||!lyPayload||!lyMeta||!lyModels)return;
  var LF=self.LytrizeLiveFilter,selection=message.selection||{},cache=Object.create(null),charts={},kpis=[];
  var anyActive=LF.hasActiveSlicers(lyMeta.slicers,selection);
  Object.keys(lyModels).forEach(function(key){
    var original=lyModels[key],uid=original.uid||('chart-'+key),indexes;
    var chartActive=LF.activeSlicersForChart(lyMeta.slicers,selection,uid).length>0;
    if(!anyActive){charts[key]=original;return;}
    if(!chartActive)return;
    try{indexes=LF.filterRowIndexes(lyPayload,lyMeta.slicers,selection,uid,cache);}
    catch(error){indexes=[];}
    try{charts[key]=LF.rebuildModel(original,lyPayload,indexes)||original;}
    catch(error){charts[key]=original;}
  });
  (lyMeta.kpis||[]).forEach(function(kpi,index){
    var result=null;
    if(LF.activeSlicersForChart(lyMeta.slicers,selection,'__kpi__').length){
      try{var indexes=LF.filterRowIndexes(lyPayload,lyMeta.slicers,selection,'__kpi__',cache);result=LF.recomputeKpi(kpi._recipe||kpi,lyPayload,indexes);}
      catch(error){}
    }else result={value:kpi.value==null?'--':kpi.value,delta:kpi.delta==null?null:kpi.delta,
      trend:kpi.trend==null?null:kpi.trend,trend_direction:kpi.trend_direction||null,
      trend_change_pct:kpi.trend_change_pct==null?null:kpi.trend_change_pct};
    kpis.push({index:kpi.index!==undefined&&kpi.index!==null?String(kpi.index):String(index),
      value:result?result.value:'—',delta:result?result.delta:null,
      prefix:kpi.prefix||'',suffix:kpi.suffix||'',
      trend:result&&result.trend?result.trend:null,
      trend_direction:result?result.trend_direction||null:null,
      trend_change_pct:result&&result.trend_change_pct!=null?result.trend_change_pct:null});
  });
  var count=0;
  try{count=LF.filterRowIndexes(lyPayload,lyMeta.slicers,selection,'__any__',cache).length;}catch(error){}
  self.postMessage({type:'result',request:message.request,charts:charts,kpis:kpis,
    selected:count,total:lyPayload.total_rows||0});
};
"""


_LIVE_UPDATE_JS = """<script>(function(){
var data=document.getElementById('lytrize-data'),meta=document.getElementById('lytrize-live-meta'),models=document.getElementById('chart-models');
if(!data||!meta||!models)return;
var banner=document.getElementById('ly-live-banner');
if(typeof Worker==='undefined'){if(banner)banner.textContent='Interactive filtering is unavailable in this browser.';return;}
var source=__WORKER_SOURCE__,worker;
try{worker=new Worker(URL.createObjectURL(new Blob([source],{type:'text/javascript'})));}catch(error){if(banner)banner.textContent='Interactive filtering is unavailable in this browser.';return;}
var uidByIndex={},ready=false,request=0,timer=0,pendingSelection=null,pendingKpis=null,pendingSlicers=null,slicerSyncRevision=0,hadActiveSelection=false;
var metaData;try{metaData=JSON.parse(meta.textContent);}catch(error){return;}
(metaData.slicers||[]).forEach(function(s){var i=s.index!==undefined&&s.index!==null?s.index:String(s.uid||'').replace(/^s_/,'').replace(/^slicer-/,'');if(s.uid)uidByIndex[String(i)]=s.uid;});
function selection(){var out={},groups={};document.querySelectorAll('[data-ly-slicer]').forEach(function(el){var idx=el.getAttribute('data-ly-slicer'),uid=el.getAttribute('data-ly-uid')||el.dataset.lyUid||uidByIndex[String(idx)]||('slicer-'+idx),g=groups[uid]||(groups[uid]={values:[],low:null,high:null,mode:'largest',n:5,value:''}),role=el.getAttribute('data-ly-role')||'values';if(el.type==='checkbox'){if(el.checked)g.values.push(el.value);}else if(role==='values'){if(el.multiple){for(var i=0;i<el.options.length;i++)if(el.options[i].selected&&el.options[i].value!=='')g.values.push(el.options[i].value);}else if(el.value!=='')g.values=[el.value];}else if(role==='low'||role==='high')g[role]=el.value===''?null:el.value;else if(role==='mode')g.mode=el.value;else if(role==='n')g.n=Math.max(1,parseInt(el.value,10)||5);else if(role==='value')g.value=el.value;});Object.keys(groups).forEach(function(uid){var s=(metaData.slicers||[]).filter(function(x){return String(x.uid)===String(uid);})[0]||{},w=String(s.widget||'dropdown').toLowerCase();if(w==='slider')out[uid]={low:groups[uid].low,high:groups[uid].high};else if(w==='search')out[uid]=groups[uid].value;else if(w==='top_n')out[uid]={mode:groups[uid].mode,n:groups[uid].n};else out[uid]=groups[uid].values;});return out;}
function update(){pendingSelection=selection();clearTimeout(timer);timer=setTimeout(function(){if(!ready)return;var active=window.LytrizeLiveFilter&&window.LytrizeLiveFilter.hasActiveSlicers(metaData.slicers||[],pendingSelection);if(!active&&!hadActiveSelection){var banner=document.getElementById('ly-live-banner');if(banner)banner.textContent='Interactive: '+(metaData.total_rows||0)+' / '+(metaData.total_rows||0)+' rows in selection';return;}hadActiveSelection=!!active;request++;worker.postMessage({type:'filter',request:request,selection:pendingSelection});},70);}
function applySlicerOptions(options){Object.keys(options||{}).forEach(function(uid){document.querySelectorAll('select[data-ly-uid]').forEach(function(select){if(select.getAttribute('data-ly-uid')!==uid)return;var selected=[];try{selected=JSON.parse(select.getAttribute('data-ly-selected')||'[]');}catch(error){}if(select.multiple)Array.prototype.forEach.call(select.options,function(o){if(o.selected&&o.value!=='')selected.push(o.value);});else if(select.value)selected=[select.value];var allow=Object.create(null);selected.forEach(function(value){allow[String(value)]=true;});while(select.options.length>1)select.remove(1);(options[uid]||[]).forEach(function(item){var value=item&&item.value!==undefined?item.value:item,option=document.createElement('option');option.value=String(value);option.textContent=String(item&&item.label!==undefined?item.label:value);option.selected=!!allow[String(value)];select.appendChild(option);});select.removeAttribute('data-ly-selected');});document.querySelectorAll('input[type="checkbox"][data-ly-uid]').forEach(function(input){if(input.getAttribute('data-ly-uid')!==uid)return;var list=input.closest('.slicer-list');if(!list)return;var selected=[];list.querySelectorAll('input:checked').forEach(function(cb){selected.push(cb.value);});var allow=Object.create(null);selected.forEach(function(v){allow[String(v)]=true;});while(list.firstChild)list.removeChild(list.firstChild);(options[uid]||[]).forEach(function(item){var value=item&&item.value!==undefined?item.value:item,label=item&&item.label!==undefined?item.label:value,row=document.createElement('label'),cb=document.createElement('input'),text=document.createElement('span');cb.type='checkbox';cb.value=String(value);cb.setAttribute('data-ly-slicer',input.getAttribute('data-ly-slicer'));cb.setAttribute('data-ly-uid',uid);cb.setAttribute('data-ly-role','value');cb.checked=!!allow[String(value)];text.textContent=String(label);row.appendChild(cb);row.appendChild(text);list.appendChild(row);});});});}
function paintKpiTrend(card,trend,delta,direction){var svg=card.querySelector('.kpi-sparkline');if(!svg)return;while(svg.firstChild)svg.removeChild(svg.firstChild);if(!Array.isArray(trend)||trend.length<2){svg.style.display='none';return;}svg.style.display='block';var vals=trend.map(function(p){return Number(p[1]);}),lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),span=hi-lo||1;var points=vals.map(function(v,i){return (4+i*112/(vals.length-1))+','+(28-(v-lo)*22/span);}).join(' ');var up=(direction==='up'||direction==='down')?direction==='up':(delta===null||delta===undefined?vals[vals.length-1]>=vals[0]:Number(delta)>=0);var color=up?'#22c55e':'#ef4444';var line=document.createElementNS('http://www.w3.org/2000/svg','polyline');line.setAttribute('points',points);line.setAttribute('fill','none');line.setAttribute('stroke',color);line.setAttribute('stroke-width','2.5');line.setAttribute('stroke-linecap','round');line.setAttribute('stroke-linejoin','round');svg.appendChild(line);svg.setAttribute('aria-label',(up?'Positive':'Negative')+' trend'+(delta==null?'':': '+Number(delta).toFixed(1)+' percent'));}
function paint(result){var keys=Object.keys(result.charts||{});requestAnimationFrame(function(){keys.forEach(function(key){var model=result.charts[key],root=document.getElementById('chart-'+key);if(!root||!window.LytrizeEChartsAdapter)return;var view=String((model.display&&model.display.map_visualization)||'').toLowerCase();if(view==='globe'||view==='extruded'){root.__lytrize3dModel=model;var old=echarts.getInstanceByDom(root);if(old)old.dispose();var registered=echarts.getMap(model.data.map.id);if(registered&&window.Lytrize3DMap)Lytrize3DMap.render(root,model,registered.geoJSON||registered.geoJson||registered);return;}var oldCanvas=root.querySelector('canvas.lytrize-map-3d');if(oldCanvas){oldCanvas.remove();var oldTip=root.querySelector('.lytrize-map-3d-tip');if(oldTip)oldTip.remove();}var chart=echarts.getInstanceByDom(root);if(!chart){var renderer=model.display&&model.display.renderer==='svg'?'svg':'canvas';try{chart=echarts.init(root,null,{renderer:renderer});}catch(error){return;}}chart.__lytrizeModel=model;try{chart.setOption(window.LytrizeEChartsAdapter.buildOption(model,root.clientWidth||600,root.clientHeight||320,root),{notMerge:true,lazyUpdate:true});chart.resize();window.LytrizeEChartsAdapter.applySatelliteBasemap(chart,model,root);}catch(error){}});(result.kpis||[]).forEach(function(kpi){var card=document.querySelector('[data-ly-kpi="'+kpi.index+'"]');if(!card)return;var value=card.querySelector('.kpi-value');if(value)value.textContent=kpi.prefix+kpi.value+kpi.suffix;var delta=card.querySelector('[data-ly-kpi-delta]');if(delta){delta.textContent=kpi.delta||'';delta.style.color=(kpi.trend_direction==='down'||(kpi.delta&&(String(kpi.delta).indexOf('\u25bc')===0||kpi.delta[0]==='-')))?'#f87171':'#34d399';delta.title=kpi.trend_direction==='down'?'Negative trend':'Positive trend';}paintKpiTrend(card,kpi.trend,kpi.trend_change_pct,kpi.trend_direction);});var banner=document.getElementById('ly-live-banner');if(banner)banner.textContent='Interactive: '+result.selected+' / '+result.total+' rows in selection';});}
worker.onmessage=function(event){var message=event.data||{};if(message.type==='ready'){ready=true;var hadPending=false;if(pendingSlicers){worker.postMessage({type:'slicers',revision:pendingSlicers.revision,slicers:pendingSlicers.slicers});pendingSlicers=null;hadPending=true;}if(pendingKpis){worker.postMessage({type:'kpis',kpis:pendingKpis});pendingKpis=null;hadPending=true;}if(!hadPending)update();}else if(message.type==='slicers-ready'){if(message.revision===slicerSyncRevision){applySlicerOptions(message.options);update();}}else if(message.type==='kpis-ready'){update();}else if(message.type==='result'&&message.request===request)paint(message);else if(message.type==='error'){var banner=document.getElementById('ly-live-banner');if(banner)banner.textContent=message.message;}};
worker.onerror=function(){var banner=document.getElementById('ly-live-banner');if(banner)banner.textContent='Interactive filtering is unavailable in this browser.';};
document.addEventListener('change',function(event){var el=event.target;if(el&&el.matches&&el.matches('[data-ly-slicer]'))update();});
window.__lytrizeDrawAll=update;
window.__lytrizeUpdateKpis=function(kpis){if(ready)worker.postMessage({type:'kpis',kpis:kpis||[]});else pendingKpis=kpis||[];};
window.__lytrizeUpdateSlicers=function(slicers){slicerSyncRevision++;metaData.slicers=slicers||[];var payload={revision:slicerSyncRevision,slicers:slicers||[]};if(ready)worker.postMessage({type:'slicers',revision:payload.revision,slicers:payload.slicers});else pendingSlicers=payload;};
worker.postMessage({type:'init',payload:data.textContent,meta:meta.textContent,models:models.textContent});
})();</script>
"""


_CHART_RUNTIME_JS = r"""<script>(function(){__MAP_REGISTER__
var models=JSON.parse(document.getElementById('chart-models').textContent),charts=[];
function draw(){
  Object.keys(models).forEach(function(key){
    var root=document.getElementById('chart-'+key);if(!root)return;
    var model=models[key],view=String((model.display&&model.display.map_visualization)||'').toLowerCase();
    if(view==='globe'||view==='extruded'){
      root.__lytrize3dModel=model;
      var old=charts[key];if(old){if(old.__lytrizeResizeObserver)old.__lytrizeResizeObserver.disconnect();old.dispose();charts[key]=null;}
      var registered=echarts.getMap(model.data.map.id);
      if(registered&&window.Lytrize3DMap)Lytrize3DMap.render(root,model,registered.geoJSON||registered.geoJson||registered);
      if(!root.__lytrize3dObserver){root.__lytrize3dObserver=new ResizeObserver(function(){var current=root.__lytrize3dModel||models[key],map=echarts.getMap(current.data.map.id);if(map&&window.Lytrize3DMap)Lytrize3DMap.render(root,current,map.geoJSON||map.geoJson||map);});root.__lytrize3dObserver.observe(root);}
      return;
    }
    if(root.querySelector('canvas.lytrize-map-3d')){root.querySelector('canvas.lytrize-map-3d').remove();var tip=root.querySelector('.lytrize-map-3d-tip');if(tip)tip.remove();}
    var wanted=models[key].display&&models[key].display.renderer==='svg'?'svg':'canvas';
    var chart=charts[key];
    if(chart&&chart.__lytrizeRenderer!==wanted){
      if(chart.__lytrizeResizeObserver)chart.__lytrizeResizeObserver.disconnect();
      chart.dispose();chart=null;
    }
    if(!chart){
      chart=charts[key]=echarts.init(root,null,{renderer:wanted});
      chart.__lytrizeRenderer=wanted;
      chart.__lytrizeModel=models[key];
      chart.__lytrizeSize={width:0,height:0};
      var frame=0;
      chart.__lytrizeResizeObserver=new ResizeObserver(function(){
        if(frame)cancelAnimationFrame(frame);
        frame=requestAnimationFrame(function(){
          frame=0;
          var width=root.clientWidth,height=root.clientHeight;
          if(!width||!height)return;
          var previous=chart.__lytrizeSize||{};
          if(width===previous.width&&height===previous.height){chart.resize({width:width,height:height});return;}
          chart.__lytrizeSize={width:width,height:height};
          /* Keep the existing drawing responsive while a card is being
           * dragged, but rebuild size-sensitive margins/radii only once the
           * resize settles. The editor also calls chart.resize() on pointer
           * movement; rebuilding options on every ResizeObserver callback
           * made one resize gesture run dozens of full chart layouts. */
          chart.resize({width:width,height:height});
          clearTimeout(chart.__lytrizeFitTimer);
          chart.__lytrizeFitTimer=setTimeout(function(){
            var fitWidth=root.clientWidth,fitHeight=root.clientHeight;
            if(!fitWidth||!fitHeight)return;
            var model=chart.__lytrizeModel||models[key];
            try{chart.setOption(LytrizeEChartsAdapter.buildOption(model,fitWidth,fitHeight,root),
              {notMerge:false,lazyUpdate:true});}catch(error){}
            chart.resize({width:fitWidth,height:fitHeight});
            LytrizeEChartsAdapter.applySatelliteBasemap(chart,model,root);
          },85);
        });
      });
      chart.__lytrizeResizeObserver.observe(root);
      chart.on('georoam',function(){
        LytrizeEChartsAdapter.applySatelliteBasemap(chart,chart.__lytrizeModel||models[key],root);
      });
    }
    var model=chart.__lytrizeModel||models[key];
    try{chart.setOption(LytrizeEChartsAdapter.buildOption(model,root.clientWidth,root.clientHeight,root),{notMerge:true,lazyUpdate:true});}
    catch(error){}
    chart.resize();
    LytrizeEChartsAdapter.applySatelliteBasemap(chart,model,root);
    chart.__lytrizeSize={width:root.clientWidth,height:root.clientHeight};
  });
}
draw();
window.addEventListener('beforeunload',function(){charts.forEach(function(chart){if(chart){if(chart.__lytrizeResizeObserver)chart.__lytrizeResizeObserver.disconnect();chart.dispose();}});});
})();</script>"""


def _chart_runtime_script(map_register: str) -> str:
    return _CHART_RUNTIME_JS.replace("__MAP_REGISTER__", map_register)


_LIVE_STYLE_JS = """(function(){
function put(el,key,value){if(el&&value!==undefined&&value!==null){if(String(key).indexOf('--')===0)el.style.setProperty(key,String(value));else el.style[key]=String(value);}}
window.__lytrizeApplyDashboardStyle=function(s,changed){
  if(!s)return;
  var board=document.getElementById('board');
  put(document.body,'backgroundColor',s.ex_bg);put(document.body,'color',s.ex_text);
  put(board,'backgroundColor',s.ex_bg);put(board,'color',s.ex_text);
  var radius=Math.max(0,Number(s.ex_radius||0));
  document.querySelectorAll('.tile').forEach(function(el){
    var bg=s.ex_card,bd=s.ex_border,ink=s.ex_text;
    if(el.classList.contains('kpi-card'))bg=s.ex_kpi;
    if(el.classList.contains('slicer-card')){bg=s.ex_slicer_bg||s.ex_kpi;bd=s.ex_slicer_bd||s.ex_border;ink=s.ex_slicer_text||s.ex_kpi_text_color||s.ex_text;}
    if(el.classList.contains('text-card')){bg=s.ex_not_bg||s.ex_card;bd=s.ex_not_bd||s.ex_border;ink=s.ex_notes_color||s.ex_text;}
    put(el,'backgroundColor',bg);put(el,'borderColor',bd);put(el,'color',ink);
    el.style.borderRadius=radius+'px';
  });
  document.querySelectorAll('.kpi-label').forEach(function(el){put(el,'color',s.ex_kpi_text_color||s.ex_text);put(el,'fontSize',Number(s.ex_kpi_label_size||13)+'px');el.style.textTransform=s.ex_kpi_label_upper?'uppercase':'none';});
  document.querySelectorAll('.kpi-value').forEach(function(el){put(el,'color',s.ex_kpi_val_color||s.ex_accent);put(el,'fontSize',Number(s.ex_kpi_val_size||32)+'px');});
  document.querySelectorAll('.slicer-title').forEach(function(el){put(el,'color',s.ex_slicer_label_color||s.ex_slicer_text||s.ex_text);put(el,'fontSize',Number(s.ex_slicer_label_size||13)+'px');});
  document.querySelectorAll('.slicer-select').forEach(function(el){put(el,'color',s.ex_slicer_value_color||s.ex_slicer_text||s.ex_text);put(el,'fontSize',Number(s.ex_slicer_value_size||13)+'px');el.style.accentColor=s.ex_slicer_accent||s.ex_accent;});
  document.querySelectorAll('.notes').forEach(function(el){put(el,'backgroundColor',s.ex_not_bg);put(el,'borderColor',s.ex_not_bd);put(el,'color',s.ex_notes_color||s.ex_text);put(el,'fontSize',Number(s.ex_notes_size||12)+'px');put(el,'fontFamily',s.ex_notes_font);var st=String(s.ex_notes_style||'').toLowerCase();el.style.fontWeight=st.indexOf('bold')>=0?'700':'400';el.style.fontStyle=st.indexOf('italic')>=0?'italic':'normal';});
  document.querySelectorAll('.title-card .text-object').forEach(function(el){put(el,'color',s.ex_title_color||s.ex_text);put(el,'fontSize',Number(s.ex_title_size||28)+'px');put(el,'fontFamily',s.ex_title_font);var st=String(s.ex_title_style||'').toLowerCase();el.style.fontWeight=st.indexOf('bold')>=0?'700':'600';el.style.fontStyle=st.indexOf('italic')>=0?'italic':'normal';});
  var changedSet=Object.create(null);(changed||[]).forEach(function(k){changedSet[k]=true;});
  var chartOverrideIds=Object.create(null);(s.ex_chart_image_override_ids||[]).forEach(function(id){chartOverrideIds[String(id)]=true;});
  function cardImage(group,prefix){if(!board)return;var key=prefix+'_image',prop='--ly-'+group+'-tile-image';if(changedSet[key]){var uri=s[key]||'';board.style.setProperty(prop,uri?'url("'+uri+'")':'none');}key=prefix+'_image_opacity';if(changedSet[key])put(board,'--ly-'+group+'-tile-image-opacity',Number(s[key]||0)/100);key=prefix+'_blend';if(changedSet[key])put(board,'--ly-'+group+'-tile-image-blend',s[key]||'normal');key=prefix+'_image_fit';if(changedSet[key]){put(board,'--ly-'+group+'-tile-image-fit',s[key]==='tile'?'auto':(s[key]||'cover'));put(board,'--ly-'+group+'-tile-image-repeat',s[key]==='tile'?'repeat':'no-repeat');}key=prefix+'_image_position';if(changedSet[key])put(board,'--ly-'+group+'-tile-image-position',String(s[key]||'center').replace(/-/g,' '));}
  if(changedSet.ex_chart_image_overrides)document.querySelectorAll('.chart-card[data-ly-image-override]').forEach(function(el){var uid=String(el.getAttribute('data-uid')||'');if(chartOverrideIds[uid])return;['--tile-image','--tile-image-opacity','--tile-image-blend','--tile-image-fit','--tile-image-repeat','--tile-image-position'].forEach(function(key){el.style.removeProperty(key);});el.removeAttribute('data-ly-image-override');});
  cardImage('chart','ex_chart');cardImage('kpi','ex_kpi');cardImage('title','ex_title');
  if(changedSet.ex_chart_image_overrides||changedSet.ex_chart_image_override_appearance){var target=String(s.ex_chart_image_target||''),override=s.ex_chart_image_override||{};if(chartOverrideIds[target])document.querySelectorAll('.chart-card[data-uid]').forEach(function(el){if(String(el.getAttribute('data-uid'))!==target)return;el.setAttribute('data-ly-image-override','true');if(changedSet.ex_chart_image_overrides){var uri=override.image||'';el.style.setProperty('--tile-image',uri?'url("'+uri+'")':'none');}put(el,'--tile-image-opacity',Number(override.opacity==null?35:override.opacity)/100);put(el,'--tile-image-blend',override.blend||'normal');put(el,'--tile-image-fit',override.fit==='tile'?'auto':(override.fit||'cover'));put(el,'--tile-image-repeat',override.fit==='tile'?'repeat':'no-repeat');put(el,'--tile-image-position',String(override.position||'center').replace(/-/g,' '));});}
  var banner=document.getElementById('ly-live-banner');if(banner)banner.hidden=!s.ex_meta;
};
})();"""


def board_to_html(document: dict, *, editor: bool = False,
                  grid_cols_n: int = 2, extra_css: str = "",
                  report_payload: dict = None,
                  report_slicers: list = None,
                  report_kpis: list = None,
                  report_specs: dict = None,
                  report_style: dict = None) -> str:
    """Export chart and non-chart dashboard objects as an HTML board.

    Without ``report_payload`` the file renders materialized snapshots
    statically. With a payload (full column-pruned dataset from
    ``modules.report_data``) plus ``report_slicers``/``report_kpis``/
    ``report_specs``, the file embeds the data and a live in-browser
    engine: slicers filter charts and recompute KPIs fully offline.
    Image objects require embedded data URIs so the document travels as one
    file and cannot reach a local filesystem path or remote URL.
    """
    board = validate_board(document)
    asset_dir = assets_root() / "echarts"
    required = ("echarts.min.js", "lytrize_adapter.js", "lytrize_live_filter.js",
                "LICENSE", "NOTICE", "LICENSE-d3")
    required = (*required, "lytrize_map_3d.js")
    files = {name: asset_dir / name for name in required}
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "The bundled ECharts runtime or notices are incomplete: "
            + ", ".join(sorted(missing)))
    source = {name: path.read_text(encoding="utf-8") for name, path in files.items()}
    live = bool(report_payload and (report_payload.get("columns")
                                     or report_slicers))
    width = float(board["metadata"].get("width", 1600) or 1600)
    height = float(board["metadata"].get("height", 900) or 900)
    if width <= 0 or height <= 0:
        raise ValueError("Board width and height must be positive")
    style = board["style"]
    background = board["background"]

    def color(value, fallback):
        return value if isinstance(value, str) and re.fullmatch(
            r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})",
            value) else fallback

    page_bg = color(background.get("color") or style.get("background"), "#ffffff")
    card_bg = color(style.get("card"), "#ffffff")
    border = color(style.get("border"), "#d4dbe7")
    text_color = color(style.get("text"), "#263247")
    accent = color(style.get("accent"), "#4f46e5")
    bg_uri = background.get("image_data_uri")
    if bg_uri:
        bg_uri = _portable_image_uri(bg_uri)
    card_images = style.get("card_images") or {}
    # Older sessions have one shared image plus a group list. New sessions
    # carry an independent image/opacity/blend tuple per card kind.
    legacy_card_uri = style.get("card_image") or ""
    if legacy_card_uri:
        legacy_card_uri = _portable_image_uri(legacy_card_uri)
    card_image_groups = set(str(style.get("card_image_groups", "charts,kpi,title")).split(","))
    prepared_card_images = {}
    for group in ("charts", "kpi", "title"):
        config = card_images.get(group) or {}
        image = config.get("image") or ""
        opacity = config.get("opacity", style.get("card_image_opacity", 35))
        blend = str(config.get("blend", style.get("card_blend", "normal")))
        if not image and legacy_card_uri and group in card_image_groups:
            image, opacity = legacy_card_uri, style.get("card_image_opacity", 35)
            blend = str(style.get("card_blend", "normal"))
        try:
            opacity = max(0, min(100, int(opacity))) / 100
        except (TypeError, ValueError):
            opacity = 0.35
        if blend not in {"normal", "multiply", "screen", "overlay", "soft-light",
                         "luminosity", "darken", "lighten"}:
            blend = "normal"
        prepared_card_images[group] = {
            "image": _portable_image_uri(image) if image else "",
            "opacity": opacity,
            "blend": blend,
            "fit": config.get("fit", "cover") if config.get("fit") in {"cover", "contain", "tile"} else "cover",
            "position": config.get("position", "center") if config.get("position") in {
                "center", "top", "bottom", "left", "right", "top-left",
                "top-right", "bottom-left", "bottom-right"} else "center",
        }

    # Store each card-family image once on the board. Cards inherit these
    # variables, so dynamically added KPI cards receive the selected image and
    # a dashboard with many charts does not duplicate a multi-megabyte data URI
    # into every tile's inline style.
    board_card_image_css = []
    for group, css_group in (("charts", "chart"), ("kpi", "kpi"),
                             ("title", "title")):
        config = prepared_card_images[group]
        image_value = f"url('{config['image']}')" if config["image"] else "none"
        fit_value = "auto" if config["fit"] == "tile" else config["fit"]
        repeat_value = "repeat" if config["fit"] == "tile" else "no-repeat"
        board_card_image_css.extend((
            f"--ly-{css_group}-tile-image:{image_value};",
            f"--ly-{css_group}-tile-image-opacity:{config['opacity']};",
            f"--ly-{css_group}-tile-image-blend:{config['blend']};",
            f"--ly-{css_group}-tile-image-fit:{fit_value};",
            f"--ly-{css_group}-tile-image-repeat:{repeat_value};",
            f"--ly-{css_group}-tile-image-position:{config['position'].replace('-', ' ')};",
        ))
    board_card_image_css = "".join(board_card_image_css)

    charts, tiles = {}, []
    for index, obj in enumerate(board["objects"]):
        props, geom = obj["properties"], obj["geometry"]
        if props.get("visible", True) is False:
            continue
        try:
            z_index = int(props.get("z_index", index))
        except (TypeError, ValueError):
            z_index = index
        style_attr = (
            f"left:{geom['x'] / width * 100:.6f}%;"
            # The free-canvas contract stores y/h in pixels. Scaling them as
            # percentages made card positions and heights change with the
            # viewport, and caused the final row to appear clipped on taller
            # windows. x/w remain fractions so width is responsive.
            f"top:{geom['y']:.2f}px;"
            f"width:{geom['w'] / width * 100:.6f}%;"
            f"height:{geom['h']:.2f}px;"
            f"z-index:{z_index};"
        )
        kind = obj["kind"]
        image_group = ("title" if kind == "text" and props.get("role") == "title"
                       else {"chart": "charts", "kpi": "kpi"}.get(kind))
        image_style = prepared_card_images.get(image_group or "", {})
        override = props.get("card_image_override")
        if kind == "chart" and isinstance(override, dict):
            image = override.get("image") or ""
            try:
                opacity = max(0, min(100, int(override.get("opacity", 35)))) / 100
            except (TypeError, ValueError):
                opacity = 0.35
            blend = str(override.get("blend", "normal"))
            image_style = {
                "image": _portable_image_uri(image) if image else "",
                "opacity": opacity,
                "blend": blend if blend in {"normal", "multiply", "screen", "overlay",
                                             "soft-light", "luminosity", "darken", "lighten"}
                         else "normal",
                "fit": override.get("fit") if override.get("fit") in {
                    "cover", "contain", "tile"} else "cover",
                "position": override.get("position") if override.get("position") in {
                    "center", "top", "bottom", "left", "right", "top-left",
                    "top-right", "bottom-left", "bottom-right"} else "center",
            }
        if kind == "chart" and isinstance(override, dict):
            # Inline variables are reserved for a chart-specific override.
            # An empty override intentionally hides the family image; removing
            # the override later reveals the inherited chart-family setting.
            override_image = image_style.get("image") or ""
            override_image_css = f"url('{override_image}')" if override_image else "none"
            override_fit = "auto" if image_style["fit"] == "tile" else image_style["fit"]
            override_repeat = "repeat" if image_style["fit"] == "tile" else "no-repeat"
            style_attr += (
                f"--tile-image:{override_image_css};"
                f"--tile-image-opacity:{image_style['opacity']};"
                f"--tile-image-blend:{image_style['blend']};"
                f"--tile-image-fit:{override_fit};"
                f"--tile-image-repeat:{override_repeat};"
                f"--tile-image-position:{image_style['position'].replace('-', ' ')};")
        image_override_attr = (' data-ly-image-override="true"'
                               if kind == "chart" and isinstance(override, dict) else "")
        edit_attrs = (f' data-uid="{escape(str(obj["id"]))}"'
                      if kind == "chart" else
                      f' data-kpi="{escape(str(props.get("index", index)))}"'
                      if kind == "kpi" else
                      f' data-slicer="{escape(str(props.get("index", index)))}"'
                      if kind == "slicer" else
                      f' data-uid="{escape(str(obj["id"]))}"'
                      if kind == "text" and props.get("role") == "title" else "")
        if kind == "chart":
            model = validate_chart_model(props.get("chart_model"))
            if model.get("chart_type") == "data_table":
                content = _table_model_fragment(model)
            else:
                charts[str(index)] = model
                content = (f'<div class="chart" id="chart-{index}" role="img" '
                           f'aria-label="{escape(model["title"])}"></div>')
            note = str(props.get("notes") or "").strip()
            if note:
                content += f'<div class="notes">{escape(note)}</div>'
            class_name = "chart-card"
        elif kind == "kpi":
            kpi_idx = escape(str(props.get("index", index)))
            trend = props.get("trend")
            direction = str(props.get("trend_direction") or "")
            spark_inner, delta_html, delta_cls, aria = _kpi_trend_fragment(trend, direction, props)
            content = (f'<div class="kpi" data-ly-kpi="{kpi_idx}">'
                       f'<div class="kpi-label">{escape(str(props.get("label", "")))}</div>'
                       f'<strong class="kpi-value">{escape(str(props.get("prefix", "")))}'
                       f'{escape(str(props.get("value", "--")))}'
                       f'{escape(str(props.get("suffix", "")))}</strong>'
                       f'<svg class="kpi-sparkline" viewBox="0 0 120 32" role="img" aria-label="{escape(aria)}">{spark_inner}</svg>'
                       f'<div class="kpi-delta {delta_cls}" data-ly-kpi-delta="{kpi_idx}">{delta_html}</div></div>')
            class_name = "kpi-card"
        elif kind == "slicer":
            selected = props.get("selected", [])
            if not isinstance(selected, list):
                selected = [selected]
            label = escape(str(props.get("label", props.get("column", "Filter"))))
            idx = escape(str(props.get("index", index)))
            widget = str(props.get("widget", "dropdown") or "dropdown").lower()
            slicer_uid = escape(str(props.get("uid", "slicer-" + str(props.get("index", index)))))
            spec = props.get("spec") or {}
            choices = spec.get("options") or props.get("values") or []
            if not isinstance(choices, list):
                choices = []
            if widget == "list":
                selected_set = {str(v) for v in selected}
                options = "".join(
                    '<label><input type="checkbox" data-ly-slicer="' + idx + '" data-ly-uid="' + slicer_uid + '" data-ly-role="value" value="' + escape(str(value)) + '"' + (' checked' if str(value) in selected_set else '') + '><span>' + escape(str(value)) + '</span></label>'
                    for value in choices)
                content = ('<div class="slicer"><b class="slicer-title">' + label + '</b>'
                           + '<div class="slicer-body"><div class="slicer-list">' + options + '</div>'
                           + '</div></div>')
                class_name = "slicer-card"
            elif widget == "dropdown":
                options = "".join(
                    f'<option value="{escape(str(value))}"'
                    f'{" selected" if str(value) in {str(v) for v in selected} else ""}>'
                    f'{escape(str(value))}</option>' for value in choices)
                content = ('<div class="slicer"><b class="slicer-title">' + label + '</b>'
                           + '<div class="slicer-body"><select class="slicer-select" data-ly-slicer="' + idx + '" data-ly-uid="' + slicer_uid + '" data-ly-role="values"'
                           + (' disabled aria-disabled="true"' if not live else '') + '>' + '<option value="">All</option>' + options + '</select>'
                           + '</div></div>')
                class_name = "slicer-card"
            elif widget == "slider":
                low = spec.get("low", selected[0] if isinstance(selected, list) and len(selected) > 0 else spec.get("min", ""))
                high = spec.get("high", selected[1] if isinstance(selected, list) and len(selected) > 1 else spec.get("max", ""))
                range_type = "date" if str(spec.get("bounds_kind", "")).lower() == "datetime" else "range"
                content = ('<div class="slicer"><b class="slicer-title">' + label + '</b>'
                           + '<div class="slicer-body"><input class="slicer-select" data-ly-slicer="' + idx + '" data-ly-uid="' + slicer_uid + '" data-ly-role="low" type="' + range_type + '" min="' + escape(str(spec.get("min", 0))) + '" max="' + escape(str(spec.get("max", 100))) + '" value="' + escape(str(low if low not in (None, "") else spec.get("min", 0))) + '">'
                           + '<input class="slicer-select" data-ly-slicer="' + idx + '" data-ly-uid="' + slicer_uid + '" data-ly-role="high" type="' + range_type + '" min="' + escape(str(spec.get("min", 0))) + '" max="' + escape(str(spec.get("max", 100))) + '" value="' + escape(str(high if high not in (None, "") else spec.get("max", 100))) + '">'
                           + '</div></div>')
                class_name = "slicer-card"
            elif widget == "search":
                value = selected[0] if isinstance(selected, list) and len(selected) > 0 else ""
                content = ('<div class="slicer"><b class="slicer-title">' + label + '</b>'
                           + '<div class="slicer-body"><input class="slicer-search" data-ly-slicer="' + idx + '" data-ly-uid="' + slicer_uid + '" data-ly-role="value" type="text" value="' + escape(str(value)) + '">'
                           + '</div></div>')
                class_name = "slicer-card"
            elif widget == "top_n":
                mode = ""
                n = 5
                if isinstance(selected, list):
                    if len(selected) > 0: mode = str(selected[0])
                    if len(selected) > 1: n = str(selected[1])
                content = ('<div class="slicer"><b class="slicer-title">' + label + '</b>'
                           + '<div class="slicer-body"><select data-ly-slicer="' + idx + '" data-ly-uid="' + slicer_uid + '" data-ly-role="mode">'
                           + '<option value="largest"' + (' selected' if mode == 'largest' else '') + '>Largest</option>'
                           + '<option value="smallest"' + (' selected' if mode == 'smallest' or spec.get('least') else '') + '>Smallest</option>'
                           + '</select><input data-ly-slicer="' + idx + '" data-ly-uid="' + slicer_uid + '" data-ly-role="n" type="number" min="1" value="' + escape(str(spec.get("n", n))) + '">'
                           + '</div></div>')
                class_name = "slicer-card"
            else:
                options = "".join(
                    f'<option value="{escape(str(value))}"'
                    f'{" selected" if str(value) in {str(v) for v in selected} else ""}>'
                    f'{escape(str(value))}</option>' for value in choices)
                content = ('<div class="slicer"><b class="slicer-title">' + label + '</b>'
                           + '<div class="slicer-body"><select class="slicer-select" data-ly-slicer="' + idx + '" data-ly-role="values"'
                           + (' disabled aria-disabled="true"' if not live else '') + '>' + '<option value="">All</option>' + options + '</select>'
                           + '</div></div>')
                class_name = "slicer-card"
        elif kind == "text":
            try:
                size = max(8, min(96, int(props.get("font_size", 16) or 16)))
            except (TypeError, ValueError):
                size = 16
            foreground = color(props.get("color"), text_color)
            body = escape(str(props.get("text", ""))).replace("\n", "<br>")
            content = f'<div class="text-object" style="font-size:{size}px;color:{foreground}">{body}</div>'
            class_name = "title-card" if props.get("role") == "title" else "text-card"
        else:
            uri = props.get("data_uri") or props.get("src")
            uri = _portable_image_uri(uri)
            fit = "contain" if props.get("fit") == "contain" else "cover"
            content = (f'<img class="image" src="{uri}" alt="'
                       f'{escape(str(props.get("alt", "Dashboard image")))}" '
                       f'style="object-fit:{fit}">')
            class_name = "image-card"
        tiles.append(f'<section class="tile {class_name}"{edit_attrs}{image_override_attr} '
                     f'style="{style_attr}">{content}</section>')

    map_geojson = _map_geojson_for_models(charts.values())
    map_register = _map_register_script(map_geojson)
    chart_runtime_script = _chart_runtime_script(map_register)
    online_imagery = any(
        model.get("chart_type") in {"map_plot"} and
        (model.get("display") or {}).get("map_basemap") == "satellite"
        for model in charts.values())
    image_sources = "data: blob: https://services.arcgisonline.com" if online_imagery else "data: blob:"
    map_notice = escape(_map_dataset_notice())

    notices = "\n\n".join(f"--- {name} ---\n{source[name]}"
                           for name in ("LICENSE", "NOTICE", "LICENSE-d3"))
    notices += "\n\n--- Map boundary data ---\n" + _map_dataset_notice()
    model_json = _script_safe(json.dumps(charts, ensure_ascii=False,
                                         separators=(",", ":"), allow_nan=False))
    echarts_js = source["echarts.min.js"].replace("</script", "<\\/script")
    adapter_js = source["lytrize_adapter.js"].replace("</script", "<\\/script")
    map3d_js = source["lytrize_map_3d.js"].replace("</script", "<\\/script")
    livefilter_js = source["lytrize_live_filter.js"].replace("</script", "<\\/script")
    live = bool(report_payload and (report_payload.get("columns") or report_slicers))
    data_json = ""
    live_meta = ""
    live_script = ""
    if live:
        data_json = _script_safe(json.dumps(report_payload, ensure_ascii=False,
                                            separators=(",", ":"), allow_nan=False))
        live_meta = _script_safe(json.dumps({
            "slicers": report_slicers or [],
            "kpis": report_kpis or [],
            "specs": report_specs or {},
            "total_rows": int(report_payload.get("total_rows") or 0),
            "n_columns": len(report_payload.get("columns") or []),
        }, ensure_ascii=False, separators=(",", ":")))
        live_script = _live_update_script(source["lytrize_live_filter.js"])
    generator = ("Lytrize interactive board export (full embedded dataset)"
                 if live else "Lytrize static board export")
    banner_rows = str(int((report_payload or {}).get('total_rows') or 0))
    if live:
        live_banner = ("<div id=\"ly-live-banner\" style=\"position:fixed;top:8px;right:8px;z-index:50;\">" + "Interactive: " + banner_rows + " rows embedded</div>")
        data_block = ("<script type=\"application/json\" id=\"lytrize-data\">" + data_json + "</script>")
        meta_block = ("<script type=\"application/json\" id=\"lytrize-live-meta\">" + live_meta + "</script>")
    else:
        live_banner = ("<div class=\"ly-static-status\">Static export — slicers are disabled</div>"
                       if report_slicers else "")
        data_block = ""
        meta_block = ""
    background_css = (f"background-image:url('{bg_uri}');background-size:cover;"
                      "background-position:center;" if bg_uri else "")
    canvas_height = max(1, int(float(board["metadata"].get("height", height) or height)))
    editor_css_block = ""
    editor_script = ""
    body_class = ""
    if editor:
        from modules.report_editor import editor_css, editor_js
        editor_css_block = editor_css(accent)
        editor_script = editor_js(grid_cols_n, 24, mode="free")
        body_class = ' class="ly-editing ly-free"'
    initial_style = dict(report_style or {})
    # Image bytes are already embedded once per card in its initial inline CSS.
    # Excluding them here keeps a large photo from being duplicated in script
    # payload just to apply the remaining appearance values on first load.
    for prefix in ("ex_chart", "ex_kpi", "ex_title"):
        initial_style.pop(f"{prefix}_image", None)
        initial_style.pop(f"{prefix}_image_name", None)
    for key in ("ex_bg_image", "ex_bg_image_name", "ex_card_image",
                "ex_card_image_name"):
        initial_style.pop(key, None)
    overrides = initial_style.pop("ex_chart_image_overrides", {})
    initial_style["ex_chart_image_override_ids"] = (
        list(overrides) if isinstance(overrides, dict) else [])
    initial_changed = [key for key in initial_style if key.endswith((
        "_image_opacity", "_blend", "_image_fit", "_image_position"))]
    initial_style_script = ""
    if initial_style:
        initial_style_script = (
            "window.__lytrizeApplyDashboardStyle&&"
            f"window.__lytrizeApplyDashboardStyle({_script_safe(json.dumps(initial_style, ensure_ascii=False, separators=(',', ':')))},"
            f"{json.dumps(initial_changed)});"
        )
    style_patch_script = "<script>" + _LIVE_STYLE_JS + initial_style_script + "</script>"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' blob: qrc:; worker-src blob:; style-src 'unsafe-inline'; img-src {image_sources}; font-src data: file:;">
<meta name="lytrize-online-imagery" content="{str(online_imagery).lower()}">
<meta name="generator" content="{generator}"><title>Lytrize dashboard</title>
<meta name="lytrize-map-data" content="{map_notice}">
<meta name="lytrize-interactive" content="{str(live).lower()}">
<style>{extra_css}*{{box-sizing:border-box}}html,body{{width:100%;min-height:100%;margin:0;overflow:auto;background:{page_bg};color:{text_color};font-family:Inter,'Noto Sans',sans-serif}}
#board{{position:relative;width:100%;height:max(100vh,{canvas_height}px);overflow:hidden;{background_css}{board_card_image_css}}}
.tile{{position:absolute;overflow:hidden;border:1px solid {border};border-radius:8px;background:{card_bg};color:{text_color}}}.tile::before{{content:'';position:absolute;inset:0;z-index:0;pointer-events:none;background-image:var(--tile-image);background-size:var(--tile-image-fit,cover);background-position:var(--tile-image-position,center);background-repeat:var(--tile-image-repeat,no-repeat);opacity:var(--tile-image-opacity,0);mix-blend-mode:var(--tile-image-blend,normal)}}.tile>*{{position:relative;z-index:1}}
.chart-card{{--tile-image:var(--ly-chart-tile-image,none);--tile-image-opacity:var(--ly-chart-tile-image-opacity,0);--tile-image-blend:var(--ly-chart-tile-image-blend,normal);--tile-image-fit:var(--ly-chart-tile-image-fit,cover);--tile-image-repeat:var(--ly-chart-tile-image-repeat,no-repeat);--tile-image-position:var(--ly-chart-tile-image-position,center)}}.kpi-card{{--tile-image:var(--ly-kpi-tile-image,none);--tile-image-opacity:var(--ly-kpi-tile-image-opacity,0);--tile-image-blend:var(--ly-kpi-tile-image-blend,normal);--tile-image-fit:var(--ly-kpi-tile-image-fit,cover);--tile-image-repeat:var(--ly-kpi-tile-image-repeat,no-repeat);--tile-image-position:var(--ly-kpi-tile-image-position,center)}}.title-card{{--tile-image:var(--ly-title-tile-image,none);--tile-image-opacity:var(--ly-title-tile-image-opacity,0);--tile-image-blend:var(--ly-title-tile-image-blend,normal);--tile-image-fit:var(--ly-title-tile-image-fit,cover);--tile-image-repeat:var(--ly-title-tile-image-repeat,no-repeat);--tile-image-position:var(--ly-title-tile-image-position,center)}}
.chart{{width:100%;height:100%;min-height:0}}.chart-card{{display:flex;flex-direction:column;padding:8px}}.chart-card .chart{{flex:1;min-height:0}}.notes{{max-height:24%;overflow:auto;padding:4px 8px;font-size:12px}}
.kpi,.slicer,.text-object{{height:100%;width:100%;padding:14px;display:flex;flex-direction:column;justify-content:center;overflow:hidden;min-width:0;min-height:0}}.title-card .text-object{{padding:8px 14px;font-weight:600}}
.kpi strong{{font-size:clamp(20px,4vw,52px);color:{accent};overflow-wrap:anywhere}}.slicer{{justify-content:flex-start;padding:10px 12px}}.slicer-title{{display:block;flex:0 0 auto;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.slicer-body{{display:flex;flex:1 1 auto;min-height:0;min-width:0;margin-top:6px;overflow:auto}}.slicer-select{{display:block;width:100%;min-width:0;min-height:30px;max-height:100%;border:1px solid {border};border-radius:5px;padding:5px 8px;background:transparent;color:inherit;font:inherit}}.slicer-select[multiple]{{height:100%;overflow:auto}}.ly-static-status{{position:fixed;left:8px;bottom:8px;z-index:50;padding:4px 8px;border-radius:4px;background:{card_bg};color:{text_color};font-size:11px;opacity:.8}}.image{{display:block;width:100%;height:100%}}</style>
{f'<style>{editor_css_block}</style>' if editor_css_block else ''}<style>.kpi-sparkline{{display:block;width:min(150px,45%);height:32px;margin-top:5px;overflow:visible}}.kpi-delta{{font-size:13px;font-weight:700;min-height:1em}}.kpi-delta.up{{color:#34d399}}.kpi-delta.down{{color:#f87171}}.slicer-list{{display:flex;flex-direction:column;gap:4px;overflow:auto;width:100%}}.slicer-list label{{display:flex;gap:7px;align-items:center;min-height:24px;cursor:pointer}}.slicer-list input{{accent-color:{accent}}}</style></head>
<body{body_class}><main id="board" class="ly-canvas" data-min-height="{canvas_height}" aria-label="Lytrize dashboard">{''.join(tiles)}</main>
{live_banner}<template id="open-source-notices"><pre>{escape(notices)}</pre></template>
<script>{echarts_js}</script><script>{adapter_js}</script><script>{map3d_js}</script>
<script>{livefilter_js}</script>
{data_block}<script type="application/json" id="chart-models">{model_json}</script>
{chart_runtime_script}
{meta_block}{live_script}{style_patch_script}
{editor_script}</body></html>'''
