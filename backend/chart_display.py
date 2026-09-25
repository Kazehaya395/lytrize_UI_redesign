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
    # show_value_labels defaults to True so freshly generated charts keep
    # their numeric value labels (matches the checked box in the design
    # mockups); unchecking it hides the labels again.
    return {"title": "", "x_label": "", "y_label": "", "legend_title": "", "legend_labels": {}, "show_value_labels": True, "value_label_color": "#ffffff", "label_position": "outside", "bar_mode": "group", "bar_gap": 0.28, "show_grid": True, "legend_position": "Top Right"}

def default_typo_opts():
    # Sizes mirror the readouts on the Typography Options tab of
    # chart_designs.pdf (header 28 / title 16 / axis 12 / legend 11).
    return {"family": "Inter", "font_style": "Normal", "header_size": 28, "header_color": "#6163df", "header_family": "Inter", "header_font_style": "Normal", "axis_title_size": 16, "axis_title_color": "#cbd5e1", "axis_tick_size": 12, "axis_tick_color": "#94a3b8", "legend_title_size": 12, "legend_title_color": "#cbd5e1", "legend_item_size": 11, "legend_item_color": "#e2e8f0"}

def _font_stack(name):
    if not name:
        return FONT_STACKS["Inter"]
    if name in FONT_STACKS:
        return FONT_STACKS[name]
    # The settings UI offers many more families than we have explicit
    # stacks for -- pass them through so the browser can resolve them
    # (with a generic fallback) instead of silently rendering Inter.
    return f"{name}, sans-serif"

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

def _font_kwargs(size=None, color=None):
    """Build a plotly font dict from an optional size + color."""
    d = {}
    if size not in (None, ""):
        try:
            d["size"] = int(size)
        except Exception:
            pass
    if color:
        d["color"] = str(color)
    return d


def _weight_style(font_style):
    """Map a UI font style ('Bold Italic', 'Normal', ...) to plotly's
    (weight, style) pair -- plotly's style enum is only normal|italic,
    boldness travels on the separate weight property."""
    s = str(font_style or "Normal").lower()
    weight = 700 if "bold" in s else 400
    style = "italic" if "italic" in s else "normal"
    return weight, style


def apply_display_options(fig, layout_opts=None, typo_opts=None):
    """Apply Layout + Typography options to a copy of *fig*.

    Every call re-derives from the figure it is given, so callers should
    always pass their original (pre-display-options) figure -- applying
    new options on top of an already-styled figure would compound.
    """
    f2 = copy.deepcopy(fig)
    lo = dict(default_layout_opts())
    lo.update(layout_opts or {})
    t = dict(default_typo_opts())
    t.update(typo_opts or {})

    # -- Titles / labels --------------------------------------------------
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

    # -- Bar geometry -----------------------------------------------------
    bm = lo.get("bar_mode")
    if bm:
        f2.update_layout(barmode=bm)
    try:
        f2.update_layout(bargap=float(lo.get("bar_gap", 0.28)))
    except Exception:
        pass

    # -- Grid -------------------------------------------------------------
    sg = lo.get("show_grid", True)
    f2.update_layout(xaxis=dict(showgrid=bool(sg)), yaxis=dict(showgrid=bool(sg)))

    # -- Legend position --------------------------------------------------
    lp = lo.get("legend_position", "")
    pos = {"Top Right": (1, 1), "Top Left": (0, 1), "Bottom Right": (1, 0), "Bottom Left": (0, 0)}.get(lp)
    if pos:
        f2.update_layout(legend=dict(x=pos[0], y=pos[1]))

    # -- Value labels (show/hide must both work so the toggle is a no-op
    #    in either direction when re-applied repeatedly) -------------------
    if lo.get("show_value_labels"):
        position = lo.get("label_position", "outside")
        vcolor = lo.get("value_label_color", "#ffffff")
        for tr in f2.data:
            try:
                tr.textposition = position
            except Exception:
                pass
            try:
                tr.textfont = dict(color=vcolor)
            except Exception:
                pass
    else:
        for tr in f2.data:
            try:
                # 'none' keeps the trace's text data intact (so switching
                # back on can restore it) while hiding it from the plot.
                tr.textposition = "none"
            except Exception:
                pass

    # -- Typography -------------------------------------------------------
    # Header = the chart title (per the design's "Header size/color/font").
    hdr = _font_kwargs(t.get("header_size"), t.get("header_color"))
    if t.get("header_family"):
        hdr["family"] = _font_stack(t["header_family"])
    w, st = _weight_style(t.get("header_font_style"))
    hdr["weight"] = w
    hdr["style"] = st
    try:
        f2.update_layout(title=dict(font=hdr))
    except Exception:
        pass

    # Axis titles ("Title size/color" in the design).
    at = _font_kwargs(t.get("axis_title_size"), t.get("axis_title_color"))
    if at:
        try:
            f2.update_xaxes(title_font=at)
            f2.update_yaxes(title_font=at)
        except Exception:
            pass

    # Axis tick labels ("Axis size/color").
    tf = _font_kwargs(t.get("axis_tick_size"), t.get("axis_tick_color"))
    if tf:
        try:
            f2.update_xaxes(tickfont=tf)
            f2.update_yaxes(tickfont=tf)
        except Exception:
            pass

    # Legend items + legend title ("Legend size/color").
    lf = _font_kwargs(t.get("legend_item_size"), t.get("legend_item_color"))
    ltf = _font_kwargs(t.get("legend_title_size"), t.get("legend_title_color"))
    leg = {}
    if lf:
        leg["font"] = lf
    if ltf:
        leg["title"] = {"font": ltf}
    if leg:
        try:
            f2.update_layout(legend=leg)
        except Exception:
            pass

    # Body font family/style (global).
    sd = _style_dict(t.get("family", "Inter"), t.get("font_style", "Normal"))
    try:
        f2.update_layout(font=dict(family=sd["family"], weight=sd["weight"], style=sd["style"]))
    except Exception:
        f2.update_layout(font=dict(family=sd["family"]))
    return f2
