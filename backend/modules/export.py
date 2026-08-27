"""modules/export.py -- HTML export engine for Lytrize dashboards."""


import re
import copy
import datetime
from html import escape




_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FFFF"
    "\U00002500-\U00002BFF"
    "\U00002100-\U000024FF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+", flags=re.UNICODE)


_ICON_MAP = {
    "💰": "$", "📊": "[chart]", "📐": "[med]", "🔢": "#",
    "⬇️": "v", "⬆️": "^", "📈": "%", "🔍": "[?]",
    "📅": "[dt]", "🏆": "1st", "📉": "[low]",
    "💡": "*", "📝": "Note:", "•": "-",
}




def _clean_pdf(text: str) -> str:
    """Strip emojis and replace known icons for PDF-safe text."""
    s = str(text)
    for em, rep in _ICON_MAP.items():
        s = s.replace(em, rep)
    s = _EMOJI_RE.sub("", s)
    return s.encode("latin-1", "replace").decode("latin-1")




def _h(text) -> str:
    """HTML-escape a value."""
    return escape(str(text), quote=True)




def _default_text_style() -> dict:
    """Return the default typography settings used in exported charts."""
    return {
        "family": "Inter, system-ui, sans-serif",
        "header_size": 28,
        "header_color": "#6163df",
        "legend_title_size": 12,
        "legend_title_color": "#cbd5e1",
        "legend_item_size": 11,
        "legend_item_color": "#e2e8f0",
        "axis_title_size": 12,
        "axis_title_color": "#cbd5e1",
        "axis_tick_size": 10,
        "axis_tick_color": "#94a3b8",
    }




def _merge_text_style(raw: dict | None) -> dict:
    """Merge a stored text-style dict over the defaults."""
    style = _default_text_style()
    if not isinstance(raw, dict):
        return style
    for key, value in raw.items():
        if value in (None, ""):
            continue
        if key in style:
            style[key] = value
    return style




DEFAULT_THEME = {
    "bg_color":       "#121a2e",
    "card_bg":        "#1b2245",
    "kpi_bg":         "#1b2245",
    "accent_color":   "#6163df",
    "card_border":    "#2c3564",
    "text_color":     "#f5f7ff",
    "notes_bg":       "#1a1732",
    "notes_border":   "#8566fc",
    "card_radius":    12,
    "gap":            24,
    "body_padding":   32,
    "chart_height":   400,
    "max_width":      "",
    "show_meta":      True,
    "kpi_val_size":   14,
    "title_font":     "Inter",
    "title_style":    "Normal",
    "title_size":     28,
    "title_color":    "#6163df",
    "notes_font":     "Inter",
    "notes_style":    "Normal",
    "notes_size":     14,
    "notes_color":    "#f5f7ff",
}




def _merge_theme(user_theme: dict) -> dict:
    """Merge user overrides into the default theme. Missing keys use defaults."""
    t = dict(DEFAULT_THEME)
    if user_theme:
        t.update({k: v for k, v in user_theme.items() if v not in (None, "")})
    return t




def _apply_axes(fig, x_lbl, y_lbl, text_style: dict | None = None):
    """Apply axis titles and axis text styling to a chart copy."""
    try:
        f2 = copy.deepcopy(fig)
        style = _merge_text_style(text_style)
        axis_title_font = dict(
            size=int(style["axis_title_size"]),
            color=str(style["axis_title_color"]),
            family=str(style["family"]),
        )
        axis_tick_font = dict(
            size=int(style["axis_tick_size"]),
            color=str(style["axis_tick_color"]),
            family=str(style["family"]),
        )
        if x_lbl:
            f2.update_xaxes(title_text=x_lbl, title_font=axis_title_font)
        else:
            f2.update_xaxes(title_font=axis_title_font)
        if y_lbl:
            f2.update_yaxes(title_text=y_lbl, title_font=axis_title_font)
        else:
            f2.update_yaxes(title_font=axis_title_font)
        f2.update_xaxes(tickfont=axis_tick_font)
        f2.update_yaxes(tickfont=axis_tick_font)
        return f2
    except Exception:
        return fig




def _apply_legend_names(fig, legend_names: dict, legend_title: str = "", text_style: dict | None = None):
    """Rename traces and style the legend title/items."""
    try:
        f2 = copy.deepcopy(fig)
        style = _merge_text_style(text_style)
        legend_font = dict(
            size=int(style["legend_item_size"]),
            color=str(style["legend_item_color"]),
            family=str(style["family"]),
        )
        legend_title_font = dict(
            size=int(style["legend_title_size"]),
            color=str(style["legend_title_color"]),
            family=str(style["family"]),
        )
        if legend_names:
            for trace in f2.data:
                original = getattr(trace, "name", None)
                if original is not None and str(original) in legend_names:
                    custom = legend_names[str(original)]
                    if custom:
                        trace.name = custom
        if legend_title:
            f2.update_layout(
                legend_title_text=legend_title,
                legend=dict(font=legend_font, title=dict(font=legend_title_font)),
            )
        elif legend_title == "":
            f2.update_layout(legend=dict(font=legend_font, title=dict(font=legend_title_font)))
        return f2
    except Exception:
        return fig




def generate_html_report(
    charts,
    session_name,
    orientation="portrait",
    kpis=None,
    dashboard_title="",
    grid_cols_n=2,
    inline_plotly=True,
    theme: dict = None,
) -> str:
    """Generate a fully self-contained HTML dashboard file."""
    t = _merge_theme(theme or {})
    is_landscape = orientation == "landscape"
    max_width    = t.get("max_width") or "1840px"
    grid_css     = f"repeat({grid_cols_n}, 1fr)"
    title        = dashboard_title or session_name
    safe_title   = _h(title)
    now_str      = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
    card_radius  = int(t.get("card_radius", 12))
    gap_px       = int(t.get("gap", 24))
    padding_px   = int(t.get("body_padding", 40))
    chart_height = int(t.get("chart_height", 400))
    meta_html    = (
        f'<div class="meta">Generated by Lytrize &middot; {now_str}</div>'
        if t.get("show_meta", True) else ""
    )

    kpi_html = ""
    if kpis:
        def _change_style(k):
            if "change_pct" not in k:
                return ""
            clr = "#10b981" if k.get("change_pct", 0) >= 0 else "#ef4444"
            return f"color:{clr};font-weight:700"


        def _arrow(k):
            if "change_pct" not in k:
                return ""
            return "▲ " if k.get("change_pct", 0) >= 0 else "▼ "


        def _kpi_val_style(k, base):
            full = f'{k.get("prefix","")}{k.get("value","--")}{k.get("suffix","")}'
            base_size = t.get("kpi_val_size", 14)
            size = f"{max(base_size - 4, 10)}px" if len(full) > 16 else (
                   f"{max(base_size - 2, 10)}px" if len(full) > 12 else f"{base_size}px")
            kpi_col = t.get("kpi_text_color", "#f5f7ff")
            wrap = "white-space:normal;word-break:break-word;overflow-wrap:anywhere;" if len(full) > 12 else ""
            colour_part = base if base else f"color:{kpi_col}"
            return f"font-size:{size};{wrap}{colour_part}"


        kpi_items = "".join(
            f'<div class="kpi-card">'
            f'<div class="kpi-icon">{_h(k.get("icon","📊"))}</div>'
            f'<div class="kpi-value" style="{_kpi_val_style(k, _change_style(k))}">'
            f'{_h(_arrow(k))}{_h(k.get("prefix",""))}{_h(k.get("value","--"))}{_h(k.get("suffix",""))}</div>'
            f'<div class="kpi-label" style="color:{_h(t.get("kpi_text_color", "#f5f7ff"))}">'
            f'{_h(k.get("label","KPI"))}</div>'
            f'</div>'
            for k in kpis
        )
        kpi_html = f'<div class="kpi-row">{kpi_items}</div>'


    chart_blocks = ""
    for idx, item in enumerate(charts):
        uid, chart_title, fig, notes = item[:4]
        meta          = item[5] if len(item) > 5 else {}


        display_title = meta.get("custom_title") or chart_title
        col_span      = "grid-column: 1 / -1;" if meta.get("full_width") else ""
        text_style    = _merge_text_style(meta.get("text_style", {}))


        # Single deepcopy per chart — apply all modifications to the same copy
        fig_r = copy.deepcopy(fig)
        fig_r.update_layout(title_text="")
        fig_r = _apply_axes(fig_r, meta.get("x_label", ""), meta.get("y_label", ""), text_style)
        fig_r = _apply_legend_names(fig_r, meta.get("legend_names", {}), meta.get("legend_title", ""), text_style)
        is_horiz = any(
            getattr(tr, "orientation", "v") == "h"
            for tr in fig_r.data if hasattr(tr, "orientation")
        )
        if is_horiz:
            fig_r.update_yaxes(automargin=True)
            fig_r.update_xaxes()
            fig_r.update_layout(margin=dict(l=130, r=30, t=20, b=30))
        else:
            fig_r.update_xaxes(tickangle=-35, automargin=True)
            fig_r.update_yaxes(automargin=True)
            fig_r.update_layout(margin=dict(l=30, r=30, t=20, b=80))


        fig_r.update_layout(
            autosize=True, width=None, height=chart_height,
            paper_bgcolor=t["card_bg"],
            plot_bgcolor=t["card_bg"],
        )


        include_js = True if idx == 0 else False
        chart_html = fig_r.to_html(
            full_html=False,
            include_plotlyjs=include_js,
            config={
                "displayModeBar": "hover",
                "responsive": False,
            },
        )


        notes_str  = str(notes).strip() if notes else ""
        notes_html = (
            f'<div class="notes"><strong>Analysis Notes:</strong> {_h(notes_str)}</div>'
            if notes_str else ""
        )


        chart_blocks += (
            f'<div class="chart-card" style="{col_span}">'
            f'<h2 style="font-size:{text_style["header_size"] / 16:.2f}rem;'
            f'font-family:{text_style["family"]};color:{text_style["header_color"]};">'
            f'{_h(display_title)}</h2>'
            + f'<div class="chart-wrap">{chart_html}</div>'
            + notes_html
            + "</div>"
        )


    plotly_guard = ""


    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} -- Lytrize</title>
  <style>
    /*
     * No external @import — the file must work 100 % offline.
     * Inter is preferred; system-ui / sans-serif are the offline fallback.
     */
    @font-face {{
      font-family: 'Inter';
      font-style: normal;
      font-weight: 100 900;
      src: local('Inter'), local('Inter-Regular');
    }}


    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    /* Do NOT set height: 100% on html,body — that locks the document to the
       viewport height and cuts off content below the fold.  Instead, use
       min-height so the page scrolls naturally when content overflows. */
    html, body {{ min-height: 100vh; }}
    body {{
      font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont,
                   'Inter', 'DejaVu Sans', 'Liberation Sans', Arial, sans-serif;
      /* Vertical padding for top/bottom breathing room;
         horizontal padding acts as the outermost page margin. */
      padding: {padding_px}px;
      background: {t['bg_color']};
      color: {t['text_color']};
      overflow-y: auto;
    }}
    .wrapper {{
      max-width: {max_width};
      margin: 0 auto;
      width: 100%;
      box-sizing: border-box;
    }}


    .report-header {{ text-align: center; margin-bottom: 2rem; }}
    .report-header h1 {{ font-size: {t['title_size'] / 16:.2f}rem; font-weight: 800; color: {t['title_color']}; font-family: {t['title_font']}, 'DejaVu Sans', Arial, sans-serif; }}
    .report-header .meta {{ font-size: 0.8rem; color: {t['text_color']}; }}


    /* ── KPI strip ── */
    .kpi-row {{
      display: flex; gap: {gap_px}px; flex-wrap: wrap;
      margin-bottom: 1.5rem; justify-content: center;
    }}
    .kpi-card {{
      background: {t['kpi_bg']};
      border-radius: {card_radius}px;
      padding: 1rem 1.4rem;
      text-align: center;
      box-shadow: 0 2px 12px rgba(0,0,0,0.07);
      min-width: 130px; flex: 1; max-width: 220px;
      border: 1px solid {t['card_border']};
    }}
    .kpi-icon  {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
    .kpi-value {{
      font-size: 1.4rem; font-weight: 800;
      color: {t['accent_color']};
      line-height: 1.2; overflow: visible;
    }}
    .kpi-label {{
      font-size: 0.72rem; color: {t['kpi_text_color']};
      margin-top: 0.2rem; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.06em;
    }}


    hr {{ border: none; border-top: 1px solid {t['card_border']}; margin: 1.5rem 0; }}


    /* ── Chart grid ── */
    .grid {{
      display: grid;
      grid-template-columns: {grid_css};
      gap: {gap_px}px;
      width: 100%;
    }}
    .chart-card {{
      background: {t['card_bg']};
      border-radius: {card_radius}px;
      padding: 1.2rem;
      box-shadow: 0 2px 16px rgba(0,0,0,0.07);
      border: 1px solid {t['card_border']};
      min-width: 0; width: 100%; overflow: hidden;
    }}
    .chart-card h2 {{
      font-size: 0.95rem; font-weight: 700;
      margin-bottom: 0.15rem; color: {t['text_color']};
    }}
    .chart-wrap {{ width: 100%; overflow: hidden; display: block; }}
    .chart-wrap .js-plotly-plot {{ width: 100% !important; display: block !important; }}
    .chart-wrap .plotly          {{ width: 100% !important; }}
    .chart-wrap .plot-container  {{ width: 100% !important; }}
    .chart-wrap svg.main-svg     {{ width: 100% !important; }}


    /* ── Notes ── */
    .notes {{
      background: {t['notes_bg']};
      padding: 0.6rem 0.9rem;
      border-left: 4px solid {t['notes_border']};
      margin-top: 0.7rem;
      border-radius: 4px;
      font-size: {t['notes_size'] / 16:.2f}rem;
      color: {t['notes_color']};
      font-family: {t['notes_font']}, 'DejaVu Sans', Arial, sans-serif;
      font-style: italic;
      white-space: pre-wrap;
      word-break: break-word;
    }}


    @page {{ margin: 12mm 10mm; }}
    @media print {{
      * {{ -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }}
      body {{
        background: {t['bg_color']} !important;
        padding: 0 !important;
        margin: 0 !important;
      }}
      .wrapper {{
        max-width: 100% !important;
        padding: 0 !important;
        margin: 0 !important;
      }}
      .report-header {{
        margin-bottom: 1.2rem;
        padding-top: 0.5rem;
        break-after: avoid;
      }}
      /* Preserve the dashboard grid in print/PDF so the exported file
         matches the on-screen layout more closely. Cards still avoid page
         breaks inside the card body to keep each chart intact. */
      .grid {{
        display: grid !important;
        grid-template-columns: repeat({grid_cols_n}, minmax(0, 1fr)) !important;
        gap: 1rem !important;
        width: 100% !important;
        grid-auto-rows: minmax(260px, auto) !important;
      }}
      .chart-card {{
        display: block !important;
        box-sizing: border-box !important;
        break-inside: avoid-column !important;
        page-break-inside: avoid !important;
        page-break-after: avoid !important;
        border: 1px solid {t['card_border']} !important;
        box-shadow: none !important;
        margin-bottom: 1rem !important;
        padding: 0.9rem !important;
      }}
      .chart-wrap {{
        overflow: visible !important;
        page-break-inside: avoid !important;
        break-inside: avoid-column !important;
      }}
      /* Full-width chart cards keep their expanded width. */
      .chart-card[style*="grid-column"] {{
        width: 100% !important;
      }}
      .kpi-row {{
        break-inside: avoid;
        page-break-inside: avoid;
        margin-bottom: 1rem;
      }}
      .kpi-card {{
        box-shadow: none !important;
        border: 1px solid {t['card_border']} !important;
      }}
      .chart-wrap svg.main-svg {{ width: 100% !important; height: auto !important; }}
      hr {{ border-top: 1px solid {t['card_border']} !important; }}
    }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="report-header">
      <h1>{safe_title}</h1>
    </div>
    {kpi_html}
    <div class="grid">{chart_blocks}</div>
  </div>
  <script>
    /*
     * Plotly.js is already embedded inline above (no CDN required).
     * This script just triggers a relayout pass once the page is fully
     * loaded so every chart fills its container correctly.
     */
    function relayoutAll() {{
      if (typeof Plotly === 'undefined') return;
      document.querySelectorAll('.js-plotly-plot').forEach(function(el) {{
        try {{
          var w = el.closest('.chart-wrap');
          var width = w ? w.offsetWidth : el.offsetWidth;
          if (width > 0) Plotly.relayout(el, {{width: width, autosize: true}});
        }} catch(e) {{}}
      }});
    }}
    window.addEventListener('load',        function() {{ setTimeout(relayoutAll, 150); }});
    window.addEventListener('resize',      function() {{ setTimeout(relayoutAll,  80); }});
    window.addEventListener('beforeprint', function() {{ relayoutAll(); }});
  </script>
  <div style="position:fixed; bottom:0; left:0; width:100%; text-align:center; padding: 1rem 0; background:{t['bg_color']}; color:{t['text_color']};">{meta_html}</div>
</body>
</html>"""
