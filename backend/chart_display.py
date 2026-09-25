"""backend/chart_display.py -- Headless Layout + Typography applier."""
from __future__ import annotations
import copy
FONT_STACKS = {"Inter": "Inter, system-ui, sans-serif", "Arial": "Arial, Helvetica, sans-serif", "Georgia": "Georgia, serif"}
FONT_FAMILIES = ["Inter", "Arial", "Georgia", "Times New Roman", "Verdana", "Courier New"]
FONT_STYLES = ["Normal", "Bold", "Italic", "Bold Italic", "Underline"]
LABEL_POSITIONS = ["outside", "inside", "auto"]
BAR_MODES = ["group", "stack", "relative", "overlay"]
LEGEND_POSITIONS = ["Top Right", "Top Left", "Bottom Right", "Bottom Left"]

def default_layout_opts():
    return {"title": "", "x_label": "", "y_label": "", "legend_title": "", "legend_labels": {}, "show_value_labels": False, "value_label_color": "#ffffff", "label_position": "outside", "bar_mode": "group", "bar_gap": 0.28, "show_grid": True, "legend_position": "Top Right"}

def default_typo_opts():
    return {"family": "Inter", "font_style": "Normal", "header_size": 28, "header_color": "#6163df", "header_family": "Inter", "header_font_style": "Normal", "axis_title_size": 12, "axis_title_color": "#cbd5e1", "axis_tick_size": 10, "axis_tick_color": "#94a3b8", "legend_title_size": 12, "legend_title_color": "#cbd5e1", "legend_item_size": 11, "legend_item_color": "#e2e8f0"}

def _font_stack(name):
    return FONT_STACKS.get(name or "Inter", FONT_STACKS["Inter"])

def _style_dict(family, font_style):
    s = str(font_style or "Normal").lower()
    bold = ("bold" in s)
    italic = ("italic" in s) or ("oblique" in s)
    w = 700 if bold else 400
    st = "italic" if italic else "normal"
    return {"family": _font_stack(family), "weight": w, "style": st}

def header_font_css(typo_opts):
    t = typo_opts or {}
    st = str(t.get("header_font_style", "Normal")).lower()
    w = "700" if ("bold" in st) else "400"
    it = "italic" if ("italic" in st) else "normal"
    fam = t.get("header_family", "Inter")
    sz = t.get("header_size", 28)
    col = t.get("header_color", "#6163df")
    return "font-family: " + str(fam) + "; font-size: " + str(sz) + "px; font-weight: " + w + "; font-style: " + it + "; color: " + str(col) + ";"

def apply_display_options(fig, layout_opts=None, typo_opts=None):
    f2 = copy.deepcopy(fig)
    lo = layout_opts or {}
    t = typo_opts or {}
    if lo.get("title"):
        f2.update_layout(title=dict(text=lo["title"]))
    if lo.get("x_label"):
        f2.update_layout(xaxis=dict(title=dict(text=lo["x_label"])))
    if lo.get("y_label"):
        f2.update_layout(yaxis=dict(title=dict(text=lo["y_label"])))
    if lo.get("legend_title"):
        f2.update_layout(legend=dict(title=dict(text=lo["legend_title"])))
    labels = lo.get("legend_labels") or {}
    if labels:
        for tr in f2.data:
            nm = getattr(tr, "name", None)
            if nm in labels:
                tr.name = labels[nm]
    bm = lo.get("bar_mode")
    if bm:
        f2.update_layout(barmode=bm)
    try:
        f2.update_layout(bargap=float(lo.get("bar_gap", 0.28)))
    except Exception:
        pass
    sg = lo.get("show_grid", True)
    f2.update_layout(xaxis=dict(showgrid=bool(sg)), yaxis=dict(showgrid=bool(sg)))
    lp = lo.get("legend_position", "")
    pos = {"Top Right": (1, 1), "Top Left": (0, 1), "Bottom Right": (1, 0), "Bottom Left": (0, 0)}.get(lp)
    if pos:
        f2.update_layout(legend=dict(x=pos[0], y=pos[1]))
    if lo.get("show_value_labels"):
        for tr in f2.data:
            try:
                tr.textposition = lo.get("label_position", "outside")
                tr.textfont = dict(color=lo.get("value_label_color", "#ffffff"))
            except Exception:
                pass
    fam = t.get("family", "Inter")
    sd = _style_dict(fam, t.get("font_style", "Normal"))
    f2.update_layout(font=dict(family=sd["family"]))
    return f2
