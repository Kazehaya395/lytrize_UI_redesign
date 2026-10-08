"""modules/export.py -- HTML export engine for Lytrize dashboards."""


import re
import copy
import datetime
from html import escape
from typing import Optional




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
        "axis_title_size": 16,
        "axis_title_color": "#cbd5e1",
        "axis_tick_size": 12,
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
    "kpi_val_size":   26,
    # KPI customisation. The value colour used to be hard-coded to the accent
    # and the label to a fixed 0.72rem uppercase, so "make the number the
    # important thing" was not expressible -- the two halves of a KPI could not
    # be styled apart at all.
    "kpi_val_color":   "",
    "kpi_label_size":  12,
    "kpi_label_upper": True,
    # Slicer colours. Empty = inherit the KPI chrome, so a report that never
    # touched the slicer design section renders exactly as it did before.
    "slicer_bg":       "",
    "slicer_bd":       "",
    "slicer_text":     "",
    "slicer_accent":   "",
    # Background image, layered ON TOP of the theme colour rather than instead
    # of it, so it works with every theme and never fights the text.
    "bg_image":        "",
    "bg_image_opacity": 35,
    "bg_blend":        "normal",
    # Per-card background image. Same data-URI contract as ``bg_image`` above,
    # but applied to the CARDS rather than the page, so a report can have a
    # picture behind its charts, its KPIs or its title band -- or any
    # combination -- without the page itself changing.
    "card_image":      "",
    "card_image_opacity": 35,
    "card_blend":      "normal",
    # Which card kinds carry the image, as a comma-separated list of
    # "charts" / "kpi" / "title".
    "card_image_groups": "charts,kpi,title",
    "title_font":     "Inter",
    "title_style":    "Normal",
    "title_size":     28,
    "title_color":    "#6163df",
    "notes_font":     "Inter",
    # "Italic" is the default because that is what every report looked like
    # before the Style control was wired up: the CSS forced italics and ignored
    # this key, so defaulting it to "Normal" would have silently changed every
    # existing report the first time it was honoured.
    "notes_style":    "Italic",
    "notes_size":     14,
    "notes_color":    "#f5f7ff",
}




# Keys whose value may legitimately be an EMPTY STRING that must NOT be
# treated as "unset" and replaced by the default.
#
# Most keys are unaffected by this -- their default is "" anyway, so dropping
# an empty value changes nothing. ``card_image_groups`` is the exception that
# matters: its default is "charts,kpi,title", so a user who unticks every
# card kind wrote "", and treating that as unset silently put the picture back
# on ALL of them. "Apply to nothing" is a real answer, not a missing value.
_EMPTY_MEANS_SET = frozenset({"card_image_groups"})


def _merge_theme(user_theme: dict) -> dict:
    """Merge user overrides into the default theme. Missing keys use defaults."""
    t = dict(DEFAULT_THEME)
    if user_theme:
        t.update({k: v for k, v in user_theme.items()
                  if v is not None and (v != "" or k in _EMPTY_MEANS_SET)})
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




def _luminance(colour: str) -> float:
    """Relative luminance 0..1 of a ``#rgb``/``#rrggbb`` colour.

    Parsed by hand rather than through matplotlib/Qt so it is available in the
    headless exporter with no display and no Qt import.
    """
    try:
        h = str(colour).lstrip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, IndexError, TypeError):
        return 0.0

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _hex_to_rgb(colour: str) -> Optional[tuple]:
    """``(r, g, b)`` 0-255 for a hex colour, or None if it is not one."""
    try:
        h = str(colour).lstrip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return None
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, TypeError):
        return None


def _rgba(colour: str, alpha: float) -> str:
    """``rgba(r, g, b, a)`` for *colour*, falling back to the input unchanged."""
    rgb = _hex_to_rgb(colour)
    if rgb is None:
        return str(colour)
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {float(alpha):.3f})"


# The chart text colours the analysis runners hard-code for a DARK canvas. They
# are the only ones the report is allowed to re-tint: a colour the user picked in
# a chart's own Typography panel is not in this table and passes through.
_DARK_CANVAS_TEXT = {
    "#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b", "#f5f7ff", "#f8fafc",
    "#ffffff", "#ffffff", "white",
}


def _report_text_colour(value, light: bool) -> Optional[str]:
    """Map a hard-coded dark-canvas text colour onto a light card, else None.

    Same rule ``chart_display`` uses for the app canvas: a colour that is one of
    the runners' dark DEFAULTS follows the report palette, anything the user
    chose is left exactly as they chose it.
    """
    if value is None:
        return None
    if not light:
        return None
    return {
        "#e2e8f0": "#334155",
        "#cbd5e1": "#334155",
        "#94a3b8": "#475569",
        "#64748b": "#475569",
        "#f5f7ff": "#0f172a",
        "#f8fafc": "#0f172a",
        "#ffffff": "#1e293b",
        "white": "#1e293b",
    }.get(str(value).strip().lower())


def _theme_chart_furniture(fig, card_bg: str, text_colour: str):
    """Re-tint a figure's legend plate and colour-bar furniture for the report.

    The legend plate and the colour bar are the two pieces of chart furniture
    that are NOT part of the report's own CSS, so they are also the two that
    kept whatever the chart generator hard-coded -- a ``rgba(15, 23, 42, ...)``
    slate plate and near-white tick labels -- regardless of the theme in use.
    That is why a light preset (Citrus Glow, Aurora Bright) showed a dark legend
    box floating on a white card, and why the dark presets looked fine by
    accident rather than by design.

    The plate is derived from the CARD the chart sits on, not from a fixed
    dark/light switch, so it is right for every preset -- and for a hand-picked
    palette, which no mode flag could ever be right about.
    """
    light = _luminance(card_bg) >= 0.5
    # Readable on the card: dark ink on a light card, near-white on a dark one.
    fallback_text = "#1e293b" if light else "#e2e8f0"
    plate = _rgba("#ffffff" if light else "#0b1220", 0.86)
    border = _rgba("#94a3b8" if light else "#cbd5e1", 0.45)

    def _tint(value):
        return _report_text_colour(value, light) or (
            str(value) if value else fallback_text)

    try:
        leg = dict(fig.layout.legend.to_plotly_json() or {})
        leg["bgcolor"] = plate
        leg["bordercolor"] = border
        lf = leg.get("font")
        lf = dict(lf) if isinstance(lf, dict) else {}
        lf["color"] = _tint(lf.get("color"))
        leg["font"] = lf
        ltitle = leg.get("title")
        if isinstance(ltitle, dict):
            ltitle = dict(ltitle)
            ltf = dict(ltitle.get("font") or {})
            ltf["color"] = _tint(ltf.get("color"))
            ltitle["font"] = ltf
            leg["title"] = ltitle
        fig.update_layout(legend=leg)
    except Exception:
        pass

    def _one_colorbar(cb) -> None:
        try:
            tf = dict(cb.tickfont.to_plotly_json() or {})
            tf["color"] = _tint(tf.get("color"))
            cb.tickfont = tf
        except Exception:
            pass
        try:
            tf = dict(cb.title.font.to_plotly_json() or {})
            tf["color"] = _tint(tf.get("color"))
            cb.title = {"font": tf}
        except Exception:
            pass
        try:
            if int(cb.borderwidth or 0) > 0:
                cb.bordercolor = border
        except Exception:
            pass

    # Only colour bars the figure ALREADY has. Reading ``.colorbar`` off a
    # trace or off ``layout.coloraxis`` always succeeds -- Plotly hands back a
    # default object -- and styling that object writes a real ``colorbar`` key
    # into the figure, which plotly.js then draws. That is the "ghost colour
    # bar" on the dashboard: a plain bar chart grew a colour ramp with tick
    # labels down its right edge. ``real_colorbars`` yields only bars that were
    # declared, so a chart with none is left untouched.
    try:
        from chart_display import real_colorbars
        for cb in real_colorbars(fig):
            _one_colorbar(cb)
    except Exception:
        pass
    try:
        # Axis ticks/labels the RUNNERS coloured for a dark canvas, same rule.
        for ax_name in ("xaxis", "yaxis"):
            ax = getattr(fig.layout, ax_name, None)
            if ax is None:
                continue
            tf = dict(ax.tickfont.to_plotly_json() or {})
            tf["color"] = _tint(tf.get("color"))
            fig.update_layout(**{ax_name: {"tickfont": tf}})
    except Exception:
        pass
    return fig


def _font_style(value) -> str:
    """CSS ``font-style`` for a Typography panel's Style choice.

    The panel offers Normal / Italic / Bold. Those three do not map onto CSS
    one-to-one, because "Bold" is a weight and not a style -- emitting
    ``font-style: bold`` is silently ignored by every browser, which is exactly
    how the Style combo came to be a control that appeared to do nothing.
    """
    return "italic" if str(value or "").strip().lower() == "italic" else "normal"


def _font_weight(value, bold_weight: int = 700) -> str:
    """CSS ``font-weight`` for a Typography panel's Style choice.

    "Bold" is a weight and not a style, so emitting ``font-style: bold`` for it
    is silently ignored by every browser -- which is how the Style combo came to
    be a control that appeared to do nothing. Anything other than Bold is
    normal weight; the heading passes its own base weight so its established
    look is unchanged.
    """
    style = str(value or "").strip().lower()
    return str(bold_weight) if style == "bold" else "400"


def _heading_weight(value, base: int = 800) -> str:
    """Weight for the report heading.

    A heading is already heavy, so "Bold" means heavier still rather than
    turning weight on; "Normal" and "Italic" keep the established look. That
    last part matters: the default is "Normal", so reading it as weight 400
    would silently un-bold every existing report the moment this was wired up.
    """
    style = str(value or "").strip().lower()
    return str(base + 100) if style == "bold" else str(base)


def _background_image_css(t: dict) -> str:
    """CSS for the report's optional background image, or ``""`` for none.

    The image is a fixed pseudo-element BEHIND the content, painted over the
    theme colour at the chosen opacity -- never instead of it. Three properties
    make that work, and each exists because of a way it visibly did not:

    * ``position: fixed`` so the picture does not scroll away and leave a bare
      colour underneath the second screen of a tall board.
    * ``z-index: -1`` so it can never sit over a card, a note or the metadata.
    * ``pointer-events: none`` so it cannot swallow a click meant for a card --
      which on the editing canvas would make half the board ungrabbable.

    The value is expected to be a data URI or a ``file://``/http URL that the
    caller has already resolved; the exporter never reads the filesystem, so the
    saved HTML stays a single self-contained document when the caller embeds one.
    """
    src = str(t.get("bg_image") or "").strip()
    if not src:
        return ""
    try:
        opacity = max(0.0, min(100.0, float(t.get("bg_image_opacity", 35))))
    except (TypeError, ValueError):
        opacity = 35.0
    if opacity <= 0.0:
        return ""
    blend = str(t.get("bg_blend") or "normal").strip().lower()
    if blend not in ("normal", "multiply", "screen", "overlay", "soft-light",
                     "luminosity", "darken", "lighten"):
        blend = "normal"
    # Escaped because this is injected into a <style> block: an unescaped quote
    # or angle bracket in a user-supplied string would end the declaration (or
    # the block) early.
    safe = (src.replace("\\", "\\\\").replace('"', '\\"')
               .replace("<", "\\3c ").replace(">", "\\3e "))
    return (
        f"body::before {{ content: ''; position: fixed; inset: 0; "
        f"z-index: -1; pointer-events: none; "
        f"background-image: url(\"{safe}\"); "
        f"background-size: cover; background-position: center center; "
        f"background-repeat: no-repeat; "
        f"opacity: {opacity / 100.0:.3f}; mix-blend-mode: {blend}; }}"
    )


def _card_image_groups(t: dict) -> list:
    """Which card kinds carry the per-card image: charts, kpi and/or title."""
    raw = str(t.get("card_image_groups") or "").strip().lower()
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    return [g for g in ("charts", "kpi", "title") if g in wanted]


def _card_image_css(t: dict) -> str:
    """Per-card background-image layer.

    Same philosophy as the page background: the image is composited OVER the
    card's own colour at a chosen opacity, never instead of it, so one picture
    is readable on a near-black card and on a cream one.

    It is drawn on each card's ``::before`` rather than as a second background
    layer for two reasons. First, ``mix-blend-mode`` and ``opacity`` on a
    pseudo-element blend against the card it sits on, which is what makes one
    picture work across themes; a ``background-blend-mode`` on the card would
    blend against everything painted into the card, including the Plotly
    canvas, and the result would change per chart. Second, a pseudo-element is
    painted BELOW the card's children, so the heading, the plot and the notes
    stay on top of the picture instead of being buried by it.

    ``z-index: 0`` on the pseudo-element plus ``isolation: isolate`` on the card
    is what keeps the layer from escaping: without the isolation a blended
    layer could paint over a neighbouring card, and without the ``z-index`` the
    card's own background would cover the layer entirely.

    The card keeps ``overflow: hidden`` (already true for chart and KPI cards),
    so the picture is clipped to the rounded corners rather than bleeding out.
    """
    src = str(t.get("card_image") or "").strip()
    if not src:
        return ""
    try:
        opacity = max(0.0, min(100.0, float(t.get("card_image_opacity", 35))))
    except (TypeError, ValueError):
        opacity = 35.0
    if opacity <= 0.0:
        return ""
    blend = str(t.get("card_blend") or "normal").strip().lower()
    if blend not in ("normal", "multiply", "screen", "overlay", "soft-light",
                     "luminosity", "darken", "lighten"):
        blend = "normal"
    groups = _card_image_groups(t)
    if not groups:
        return ""
    # Same escaping as the page layer: this string is injected into a <style>
    # block, so an unescaped quote or angle bracket would end the declaration.
    safe = (src.replace("\\", "\\\\").replace('"', '\\"')
               .replace("<", "\\3c ").replace(">", "\\3e "))
    rules: list = []
    for group in groups:
        sel = {"charts": ".chart-card", "kpi": ".kpi-card",
               "title": ".title-card"}[group]
        rules.append(
            # `position: relative` is a no-op on the free canvas (those cards
            # are already absolutely positioned) and is exactly what a GRID-mode
            # card needs, since it has no positioning of its own to anchor the
            # pseudo-element. `isolation` gives the card a stacking context so
            # the blended layer cannot escape onto a neighbour.
            f"{sel} {{ position: relative; isolation: isolate; overflow: hidden; }}"
            f"{sel}::before {{ content: ''; position: absolute; inset: 0; "
            f"z-index: 0; pointer-events: none; "
            f"background-image: url(\"{safe}\"); "
            f"background-size: cover; background-position: center center; "
            f"background-repeat: no-repeat; "
            f"opacity: {opacity / 100.0:.3f}; mix-blend-mode: {blend}; }}"
            # The card's own content is lifted above the picture. Only DIRECT
            # children are raised, and the edit chrome is explicitly EXCLUDED
            # rather than swept up by it: .ly-sel / .ly-grip / .ly-handle /
            # .ly-bar are absolutely positioned siblings that share those same
            # `> *` positions, so raising them here would replace
            # `position: absolute` with `position: relative` and collapse the
            # selection ring, the grip and the resize handles onto the card's
            # top-left corner. They need no help anyway -- they are positioned
            # descendants of the isolated card and carry z-index 30-35, so they
            # are already above a z-index 0 pseudo-element.
            f"{sel} > :not([class^=\"ly-\"]) "
            f"{{ position: relative; z-index: 1; }}"
        )
    return " ".join(rules)


def _is_domain_figure(fig) -> bool:
    """True for a figure whose traces are sized by their OWN ``domain`` box.

    Pie/donut, treemap, sunburst, funnel and icicle all draw themselves into a
    per-trace domain and have no cartesian axes, so the legend space that the
    axis-based reservation carves out does not exist for them. Detected here
    rather than by trace type so a new Plotly domain trace is covered the day it
    is added instead of silently regressing.
    """
    try:
        for tr in (getattr(fig, "data", None) or []):
            dom = getattr(tr, "domain", None)
            if dom is not None and getattr(dom, "x", None) is not None:
                return True
    except Exception:
        return False
    return False


def _num(value, default: float = 0.0) -> float:
    """A finite float for an HTML attribute, else *default*.

    Slider ``min``/``max``/``value`` are emitted from whatever the slicer was
    stored with, which may be ``None`` or a string. ``"min=None"`` makes the
    browser treat the slider as broken rather than simply falling back, so a
    bad value has to become a real number before it reaches the markup.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float(default)
    if f != f or f in (float("inf"), float("-inf")):
        return float(default)
    return f


def _normalise_selected(slicer: dict) -> list:
    """The slicer's currently-selected values, as a flat list.

    A one-value selection is stored as a scalar rather than a one-element
    list when it round-trips through JSON, and comparing a string against a
    list of candidates silently selects nothing -- so both spellings land here.
    """
    spec = (slicer or {}).get("spec") or {}
    values = spec.get("values", (slicer or {}).get("values"))
    if values is None:
        return []
    if isinstance(values, (list, tuple, set)):
        return list(values)
    return [values]


def _is_selected(value, selected) -> bool:
    """Is *value* in *selected*, comparing by string form.

    String comparison on both sides: a slicer stored from a categorical column
    holds numpy/`Categorical` scalars in some paths and plain strings in
    others, and ``"EU" in [np.str_("EU")]`` is True while the reverse
    membership test against mixed types is not always symmetric.
    """
    return str(value) in {str(v) for v in (selected or [])}


def _fmt_value(value) -> str:
    """A readable label for one slicer value.

    NaN/NaT render as an em dash rather than ``nan`` -- ``_h()`` would happily
    pass that through and the reader would see a literal "nan" in the list.
    """
    try:
        if value is None or (isinstance(value, float) and value != value):
            return "\u2014"
    except Exception:
        pass
    if hasattr(value, "isoformat"):
        try:
            text = str(value.isoformat())
            return text[:10] if "T" in text else text
        except Exception:
            pass
    return str(value)


def _fmt_bounds(slicer: dict) -> str:
    """Label for a slider's current range, e.g. ``"10 - 90"``."""
    spec = (slicer or {}).get("spec") or {}
    low, high = spec.get("low"), spec.get("high")
    if low in (None, "") and high in (None, ""):
        return "\u2014"
    return f"{_fmt_value(low)} \u2013 {_fmt_value(high)}"


def _slicer_status_line(slicer: dict) -> str:
    """Under-the-control hint, or "" when nothing needs saying.

    An inactive list slicer gets "Select to filter" -- a control with no
    indication of what it expects is the main reason a user adds a slicer and
    then decides the dashboard is broken.
    """
    from services.slicer_service import slicer_is_active
    try:
        if not slicer_is_active(slicer):
            return "Select to filter"
    except Exception:
        return ""
    return ""


def generate_html_report(
    charts,
    session_name,
    orientation="portrait",
    kpis=None,
    dashboard_title="",
    grid_cols_n=2,
    inline_plotly=True,
    theme: dict = None,
    tile_geometry: dict = None,
    kpi_geometry: dict = None,
    slicers=None,
    slicer_geometry: dict = None,
    slicer_values: dict = None,
    geometry_mode: str = "free",
    extra_css: str = "",
    editor: bool = False,
    report_specs: dict = None,
    report_payload: dict = None,
) -> str:
    """Generate a fully self-contained HTML dashboard file.

    ``tile_geometry`` is the dashboard canvas's free-form arrangement
    (``{uid: {x, y, w, h}}``; ``x``/``w`` are fractions of the canvas width,
    ``y``/``h`` are pixels -- see ``modules.canvas_geometry``). ``kpi_geometry``
    is the same for the KPI cards, keyed by index.

    ``geometry_mode`` selects how a placement becomes CSS:

    ``"free"``
        One absolutely-positioned canvas holding the KPIs *and* the charts.
        Cards go exactly where they were put, may overlap, and the canvas grows
        to fit its lowest card. This is the mode the in-app canvas edits in, so
        the board and the file stay the same document.
    ``"grid"``
        The older CSS-grid placement, in grid cells. Kept so a session saved
        before the free canvas still renders the arrangement it was saved with,
        rather than being reinterpreted through the new units and landing in
        nonsense. The dashboard page converts such a blob on load and writes
        ``"free"`` back, so this is a load-time path rather than a live one.

    In ``"free"`` mode a card with no geometry is placed by
    ``canvas_geometry.auto_place``, so a report with nothing saved still reads as
    a deliberate two-up board rather than one full-bleed stack.

    ``inline_plotly=False`` emits ``<script src="lytrize_plotly.js">`` instead
    of embedding the ~4.5 MB plotly bundle, for consumers that write the bundle
    next to the report themselves. The live in-app preview regenerates on every
    settings tweak, and re-embedding megabytes of JavaScript each time is not
    viable; the saved file keeps ``inline_plotly=True`` and stays standalone.

    ``extra_css`` is injected just before ``</style>``. The preview uses it to
    offer the app's bundled fonts to Chromium (which cannot see Qt's font
    database), so the canvas shows the fonts that will actually be used.

    ``editor=True`` appends the drag/resize/notes overlay used by the in-app
    dashboard canvas. It is off for the saved file, which must stay a clean
    document with no editing affordances in it.
    """
    from modules import canvas_geometry
    free = str(geometry_mode or "free").lower() != "grid"
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
    bg_image_css = _background_image_css(t)
    # Per-card layer. Emitted AFTER the page layer so a card picture always
    # wins over a page picture on the card it covers -- they are independent
    # settings, and "both on" is a legitimate (if unusual) combination.
    card_image_css = _card_image_css(t)

    def _free_style(g: dict) -> str:
        """Absolute placement for one card on the free canvas.

        Horizontal is a PERCENTAGE of the canvas and vertical is PIXELS. That
        split is what makes a free canvas still worth exporting: narrow the
        window and every card keeps its relative width, but a card stays exactly
        as tall as the user made it -- a chart's height must not scale with the
        viewport, or the plot would distort.
        """
        return (f"left:{g['x'] * 100:.4f}%;top:{g['y']:.2f}px;"
                f"width:{g['w'] * 100:.4f}%;height:{g['h']:.2f}px;")

    tiles = {u: g for u, g in (tile_geometry or {}).items()
             if isinstance(g, dict)}

    # ONE arrangement for the whole board. In free mode the KPIs and the charts
    # are absolutely positioned against the same canvas, so they have to be
    # resolved together: auto-placed relative to each other, then walked in a
    # single visual order. Resolving them separately is what previously forced
    # the KPI strip into its own container above the chart grid.
    free_geom: dict = {}
    title_geom: dict = {}
    if free:
        stored = dict(tiles)
        for i, _k in enumerate(kpis or []):
            key = f"kpi:{i}"
            g = (kpi_geometry or {}).get(str(i)) or (kpi_geometry or {}).get(i)
            if isinstance(g, dict):
                stored[key] = g
        # The title band. It is a card on the canvas, so it takes part in the
        # SAME arrangement as everything else -- and on a board saved before it
        # existed, ensure_title_band shifts the existing cards down by one band
        # so the new title does not land on top of the first chart. That shift
        # is the whole reason this is one function and not two blocks: doing it
        # here means the canvas, the preview and the saved file all move the
        # cards by exactly the same amount.
        slicer_default_h = max(canvas_geometry.MIN_H, 180.0)
        slicer_wanted = [f"slicer:{i}" for i in range(len(slicers or []))]
        for i, _s in enumerate(slicers or []):
            g = (slicer_geometry or {}).get(str(i)) or \
                (slicer_geometry or {}).get(i)
            if isinstance(g, dict):
                # Namespaced for the same reason chart uids and __title__ are:
                # a slicer keyed "0" would collide with KPI 0 and one would
                # silently overwrite the other's placement.
                stored[f"slicer:{i}"] = g
        # ...and join the title band's shift, so a board saved before slicers
        # existed moves everything down together rather than landing the first
        # slicer on top of the heading.
        stored, title_geom = canvas_geometry.ensure_title_band(
            stored,
            height=canvas_geometry.title_band_height(
                t.get("title_size", 28), bool(t.get("show_meta", True))),
            gap_px=gap_px,
        )
        # A KPI card is a number and a label, not a plot. Left to the shared
        # default it would be auto-placed at the chart height -- 400px of
        # mostly-empty card, twice, above the first row of charts.
        kpi_default_h = max(canvas_geometry.MIN_H, 120.0)
        kpi_wanted = [f"kpi:{i}" for i in range(len(kpis or []))]
        # EVERY KPI is a wanted uid, whether or not it has stored geometry.
        # Building `wanted` from stored.keys() alone left a KPI with no saved
        # placement out of the arrangement entirely, so it was emitted with an
        # empty style -- an unpositioned block that ignored the canvas and
        # stacked on top of the first chart.
        #
        # They are auto-placed FIRST, at their own height, and the charts are
        # placed against the result. They used to be pre-seeded with a literal
        # {"y": 0} placeholder instead, which auto_place then treated as an
        # existing placement and left alone -- so a KPI with no saved geometry
        # always landed on the title band instead of below the cards.
        # Slicers are placed FIRST, against the title band, so they sit between
        # the heading and the KPIs -- which is where the eye looks for a filter
        # that controls the numbers below. Their height is 180px rather than
        # the 400px chart default, so a row of them does not push the board
        # down by half a screen.
        if slicer_wanted:
            free_geom = canvas_geometry.auto_place(
                slicer_wanted, stored, gap_px=gap_px, h=slicer_default_h)
        else:
            free_geom = stored
        free_geom = canvas_geometry.auto_place(
            kpi_wanted, free_geom, gap_px=gap_px, h=kpi_default_h)
        free_geom = canvas_geometry.auto_place(
            [c[0] for c in charts], free_geom, gap_px=gap_px, h=chart_height)
    else:
        for i in range(len(kpis or [])):
            g = (kpi_geometry or {}).get(str(i)) or (kpi_geometry or {}).get(i)
            if isinstance(g, dict):
                free_geom[f"kpi:{i}"] = g
        # Grid mode has no free canvas to drag cards on, but a slicer must
        # still APPEAR rather than vanish from the report. It is emitted
        # unpositioned (place = "") and flows with the content, which is the
        # same treatment a KPI gets there.
        for i in range(len(slicers or [])):
            g = (slicer_geometry or {}).get(str(i)) or \
                (slicer_geometry or {}).get(i)
            if isinstance(g, dict):
                free_geom[f"slicer:{i}"] = g

    def _visual_key(item):
        uid = item[0]
        g = free_geom.get(uid) or tiles.get(uid)
        if g is None:
            return (0, 0, 0, uid)          # unplaced charts keep bridge order
        return (1, float(g.get("y", 0)), float(g.get("x", 0)), uid)

    charts = sorted(charts, key=_visual_key) if (tiles or free_geom) else charts

    kpi_html = ""
    kpi_cards: dict = {}
    if kpis:
        # KPI cards join the chart grid only when the user has actually
        # arranged them as tiles. Absent geometry they stay in their own flex
        # strip, so every session saved before this existed exports exactly as
        # it did before rather than silently changing shape.
        kpi_tiles = {
            int(i): g for i, g in (kpi_geometry or {}).items()
            if isinstance(g, dict)
        } if kpi_geometry else {}

        def _tile_style(g: dict) -> str:
            """Placement CSS for one KPI tile, clamped.

            Free mode positions absolutely against the canvas (left/top in
            percent+px, width/height sized); grid mode keeps the older explicit
            line placement. Both are clamped through the one authority in
            ``canvas_geometry``, so a malformed stored value cannot reach the
            document in either -- a negative or NaN coordinate becomes CSS the
            browser discards, and the card silently vanishes.
            """
            if free:
                c = canvas_geometry.clamp(g)
                return _free_style(c)
            cols = max(1, int(grid_cols_n))
            x = max(0, min(int(g.get("x", 0)), cols - 1))
            span_w = max(1, min(int(g.get("w", 1)), cols - x))
            y = max(0, int(g.get("y", 0)))
            span_h = max(1, int(g.get("h", 1)))
            return (f"grid-column: {x + 1} / span {span_w};"
                    f"grid-row: {y + 1} / span {span_h};")

        def _change_style(k):
            # Only the comparison types carry change_pct, so a card without it
            # gets the plain accent colour. Keying off ``in`` rather than
            # truthiness matters for a 0.0% change, which is a real (flat) trend
            # and must not be drawn as "no trend".
            if k.get("change_pct") is None:
                return ""
            clr = "#10b981" if k.get("change_pct", 0) >= 0 else "#ef4444"
            return f"color:{clr};font-weight:700"

        def _arrow(k):
            if k.get("change_pct") is None:
                return ""
            return "▲ " if k.get("change_pct", 0) >= 0 else "▼ "

        def _kpi_val_style(k, base, card_h=None):
            full = f'{k.get("prefix","")}{k.get("value","--")}{k.get("suffix","")}'
            base_size = t.get("kpi_val_size", 26)
            # Same three tiers and the same subtraction as KpiCard.
            # _apply_value_size, so the card in the report breaks and shrinks at
            # exactly the same points as the one on the board.
            size = f"{max(base_size - 4, 10)}px" if len(full) > 16 else (
                   f"{max(base_size - 2, 10)}px" if len(full) > 12 else f"{base_size}px")
            # On the free canvas the card's height is whatever the user dragged,
            # so the value is sized FROM that height rather than from a fixed
            # setting. Without this, shrinking a KPI card clipped the label and
            # the number ran out of the bottom -- the card stopped being a KPI
            # and became a cropped rectangle.
            if card_h:
                # Reserve room for the icon above and the label below, then take
                # the rest for the number. A KPI squeezed to its minimum is still
                # a readable KPI rather than a cropped rectangle.
                room = max(10.0, float(card_h) - 56.0)
                size = f"{max(10.0, min(float(base_size), room * 0.62)):.0f}px"
            # The value's own colour is a setting. It used to be the accent
            # colour unconditionally, so the only way to change the number was
            # to change the theme -- which restyled every chart along with it.
            # A change arrow still wins (it carries meaning, not decoration),
            # and an unset value falls back to the accent as before.
            kpi_col = t.get("kpi_val_color") or t.get("accent_color", "#6163df")
            wrap = "white-space:normal;word-break:break-word;overflow-wrap:anywhere;" if len(full) > 12 else ""
            colour_part = base if base else f"color:{kpi_col}"
            return f"font-size:{size};{wrap}{colour_part}"

        def _kpi_label_style() -> str:
            """Label size and case, both settings now.

            A KPI label is the line that says what the number means, so it is
            the first thing that needs to shrink when a card is short and the
            first thing that should stop shouting when the user asks it to. It
            was a fixed 0.72rem uppercase in CSS with no way to change either.
            """
            size = max(7, int(t.get("kpi_label_size", 12) or 12))
            upper = t.get("kpi_label_upper", True)
            case = "uppercase" if upper in (True, "true", "True", 1, "1") else "none"
            return (f"font-size:{size}px;"
                    f"text-transform:{case};"
                    f"letter-spacing:{'0.06em' if case == 'uppercase' else '0'};"
                    f"font-weight:{'600' if case == 'uppercase' else '500'}")

        def _kpi_card(k, index: int = 0):
            # A card whose column vanished after a re-upload says so, exactly as
            # KpiCard does. A bare "--" is indistinguishable from a genuinely
            # empty result, which is how a wrong number survives unnoticed.
            stale = k.get("stale")
            label = k.get("label", "KPI")
            if stale:
                # The em dash is a literal character, not an entity: the label
                # goes through _h(), so an "&mdash;" here would reach the reader
                # as the escaped text "&mdash;".
                label = f'{label} \u2014 pick "{stale}" again'
                base = "color:#f59e0b;font-weight:700"
            else:
                base = _change_style(k)
            # Free mode reads the RESOLVED placement, not the stored blob:
            # a KPI with no saved geometry is auto-placed like any other card,
            # and emitting an empty style string for it would drop it out of
            # flow entirely -- an unpositioned block stacked above the canvas.
            tile = (free_geom.get(f"kpi:{index}") if free
                    else kpi_tiles.get(index))
            place = _tile_style(tile) if tile else ""
            card_h = (canvas_geometry.clamp(tile)["h"]
                      if (free and tile) else None)
            # data-kpi gives the card a stable handle for the in-app canvas to
            # drag, resize and target; it is inert in the saved file.
            return (
                f'<div class="kpi-card" data-kpi="{int(index)}" style="{place}">'
                f'<div class="kpi-icon">{_h(k.get("icon","📊"))}</div>'
                f'<div class="kpi-value" style="{_kpi_val_style(k, base, card_h)}">'
                f'{_h(_arrow(k))}{_h(k.get("prefix",""))}'
                f'{_h(k.get("value","--"))}{_h(k.get("suffix",""))}</div>'
                f'<div class="kpi-label" '
                f'style="color:{_h(t.get("kpi_text_color", "#f5f7ff"))};'
                f'{_kpi_label_style()}">'
                f'{_h(label)}</div>'
                f'</div>'
            )

        # Keyed, not concatenated: in free mode the KPI cards and the chart
        # cards share ONE coordinate space and have to be emitted in a single
        # visual order, so they are collected together and sorted once at the
        # end. The key is namespaced "kpi:<i>" because a chart uid and a KPI
        # index are both bare strings and could otherwise collide.
        kpi_cards = {f"kpi:{i}": _kpi_card(k, i) for i, k in enumerate(kpis)}
        kpi_items = "".join(kpi_cards.values())

        if free:
            # Not a strip above the board: the user places and sizes a KPI
            # exactly like any other card, which means one canvas, not two
            # coordinate spaces to reconcile.
            kpi_html = ""
        elif kpi_tiles:
            # Tiled KPIs are emitted as their own grid ABOVE the chart grid,
            # rather than folded into it. They share the identical column
            # template and gap, so the two grids line up pixel-for-pixel and a
            # KPI at x=0 sits exactly over the first column of charts -- while
            # each grid keeps its own row numbering, so a chart can never be
            # pushed down by a KPI and the saved chart geometry stays valid.
            kpi_html = f'<div class="grid kpi-grid">{kpi_items}</div>'
        else:
            kpi_html = f'<div class="kpi-row">{kpi_items}</div>'


    def _slicer_body(s: dict, index: int) -> str:
        """The control itself -- one per widget kind.

        Deliberately plain HTML with no script of its own: the same markup
        has to render in the live canvas (where a QWebChannel bridge exists)
        and in the saved file (where it does not). Interactivity is attached
        by the one delegated listener in the page shell, which finds a
        bridge if there is one and does nothing otherwise -- so the exported
        file shows an accurate, static control instead of a dead one that
        looks like it should work.
        """
        kind = str(s.get("widget") or s.get("kind") or "list").lower()
        values = (slicer_values or {}).get(str(index)) or []
        selected = _normalise_selected(s)
        name = f"lys{index}"

        if kind == "slider":
            lo, hi = s.get("low"), s.get("high")
            try:
                step = (float(hi) - float(lo)) / 100.0
            except (TypeError, ValueError):
                step = 1.0
            step = step if step > 0 else 1.0
            return (
                '<div class="slicer-slider">'
                f'<input type="range" name="{name}a" '
                f'min="{_num(lo, 0)}" max="{_num(hi, 100)}" '
                f'step="{step:.6g}" value="{_num(lo, 0)}" '
                'data-ly-slicer="'+str(index)+'" data-ly-role="low">'
                f'<input type="range" name="{name}b" '
                f'min="{_num(lo, 0)}" max="{_num(hi, 100)}" '
                f'step="{step:.6g}" value="{_num(hi, 100)}" '
                'data-ly-slicer="'+str(index)+'" data-ly-role="high">'
                f'<span class="slicer-range">{_h(_fmt_bounds(s))}</span>'
                '</div>'
            )

        if kind == "search":
            return (f'<input type="search" class="slicer-search" '
                    f'placeholder="Search {_h(s.get("label", ""))}" '
                    f'data-ly-slicer="{index}" data-ly-role="value">')

        if kind == "top_n":
            return (f'<div class="slicer-topn">'
                    f'<label>Top <input type="number" min="1" '
                    f'value="{int(s.get("n") or 10)}" '
                    f'data-ly-slicer="{index}" data-ly-role="n"> '
                    f'{_h(str(s.get("column") or ""))}</label></div>')

        opts = "".join(
            f'<option value="{_h(v)}"'
            f'{" selected" if _is_selected(v, selected) else ""}>'
            f'{_h(_fmt_value(v))}</option>'
            for v in values)
        # dropdown -- multi-select with a usable row count. size=1 showed one
        # row for a many-value control, which reads as a dead single box.
        if kind == "dropdown":
            n_rows = max(4, min(8, len(values) or 4))
            return (f'<select class="slicer-select" '
                    f'data-ly-slicer="{index}" data-ly-role="values" '
                    f'multiple size="{n_rows}">{opts}</select>'
                    f'<button type="button" class="slicer-clear" '
                    f'data-ly-slicer="{index}" data-ly-role="clear">'
                    f'Clear</button>')

        # list (default) -- a checkbox column, scrollable when the card is
        # short. Every value is a real <input>, so it stays clickable in
        # Preview and is skipped by the card's drag guard.
        boxes = "".join(
            f'<label class="slicer-opt">'
            f'<input type="checkbox" value="{_h(v)}"'
            f'{" checked" if _is_selected(v, selected) else ""} '
            f'data-ly-slicer="{index}" data-ly-role="value"> '
            f'<span>{_h(_fmt_value(v))}</span></label>'
            for v in values)
        if not boxes:
            boxes = '<div class="slicer-empty">No values</div>'
        return f'<div class="slicer-opts">{boxes}</div>'

    def _slicer_card(s: dict, index: int):
        """A slicer is a card like any other: heading, body, same tile."""
        if not isinstance(s, dict):
            return ""
        # Placement via _free_style directly, NOT _tile_style: the latter is
        # nested inside ``if kpis:``, so reaching it from here would make a
        # dashboard with slicers but no KPIs raise NameError. In grid mode the
        # card is emitted into a CSS grid container, which does the placing.
        place = ""
        if free:
            tile = free_geom.get(f"slicer:{index}")
            if isinstance(tile, dict):
                place = _free_style(canvas_geometry.clamp(tile))
        status = _slicer_status_line(s)
        return (
            f'<div class="slicer-card" data-slicer="{int(index)}" '
            f'style="{place}">'
            f'<div class="slicer-title">'
            f'{_h(s.get("label") or s.get("column") or "Slicer")}</div>'
            f'<div class="slicer-body">{_slicer_body(s, index)}</div>'
            + (f'<div class="slicer-status">{status}</div>'
               if status else "")
            + "</div>"
        )

    slicer_cards = {f"slicer:{i}": _slicer_card(s, i)
                    for i, s in enumerate(slicers or [])
                    if isinstance(s, dict)}

    # Free-form tiling: walk the charts in VISUAL order (top-to-bottom,
    # left-to-right) rather than the stored order, otherwise a board where the
    # user dragged cards around would export with its charts in the wrong
    # sequence for any reader not using CSS grid placement.

    chart_cards: dict = {}
    for idx, item in enumerate(charts):
        uid, chart_title, fig, notes = item[:4]
        meta          = item[5] if len(item) > 5 else {}


        display_title = meta.get("custom_title") or chart_title
        # Explicit placement from the canvas geometry. The card is given
        # grid-row: span <h> so a tall tile really is tall, and grid-column: span
        # <w> so the exported widths match what the user dragged -- this is what
        # makes the standalone HTML a true preview of the live dashboard.
        # Cards with no saved geometry fall back to auto-flow, and a full-width
        # span when the chart has no geometry at all (legacy sessions).
        g = tiles.get(uid)
        if free:
            # Always placed in free mode: an unplaced card is auto-placed rather
            # than left to auto-flow, because auto-flow would put it somewhere
            # the canvas never showed it.
            geom = canvas_geometry.clamp(free_geom.get(uid) or g or {})
            col_span = _free_style(geom)
            card_height = geom["h"]
        elif g is not None:
            # Clamp here as well as in the canvas: export can be handed geometry
            # straight from stored session JSON, so a negative or oversized
            # span must not reach CSS as "grid-column: -4 / span 2", which the
            # browser discards (silently full-bleed the card).
            cols = max(1, int(grid_cols_n))
            x = max(0, min(int(g.get("x", 0)), cols - 1))
            span_w = max(1, min(int(g.get("w", 1)), cols - x))
            y = max(0, int(g.get("y", 0)))
            span_h = max(1, int(g.get("h", 1)))
            # Both tracks are given an explicit START line. Emitting only a
            # span for grid-row would leave the row to auto-placement, which
            # can drop a card back onto an occupied cell when its column is
            # explicitly placed.
            col_span = (
                f"grid-column: {x + 1} / span {span_w};"
                f"grid-row: {y + 1} / span {span_h};"
            )
            card_height = float(chart_height * span_h)
        else:
            col_span = "grid-column: 1 / -1;" if meta.get("full_width") else ""
            card_height = float(chart_height)
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

        # Pie / donut / treemap / sunburst / funnel carry their legend INSIDE
        # the plot: they have no xaxis, so nothing above reserved any space for
        # it and Plotly drew the plate straight across the marks. That is the
        # legend-on-top-of-the-pie in the report. Reserve a real strip for them.
        #
        # Deliberately scoped to domain-based figures only. Every cartesian
        # chart already went through the widget-side reservation and its stored
        # layout is what the tests pin, so touching it here would change saved
        # files that are correct today.
        if _is_domain_figure(fig_r):
            try:
                from chart_display import apply_legend_layout, default_layout_opts
                _pos = str(meta.get("legend_position")
                           or default_layout_opts().get("legend_position")
                           or "Top Right")
                _names = []
                for _tr in fig_r.data:
                    if getattr(_tr, "showlegend", True) is False:
                        continue
                    # A pie keeps its categories in the trace's ``labels``, not
                    # one trace per slice, so trace names alone measure nothing.
                    _labels = getattr(_tr, "labels", None)
                    if _labels is not None:
                        _names.extend(str(x) for x in _labels)
                    elif getattr(_tr, "name", None):
                        _names.append(str(_tr.name))
                # No width_px: the card width is settled by CSS at render time,
                # so plan_legend_layout takes its safe over-reserving floor.
                if _names:
                    apply_legend_layout(
                        fig_r, _pos, names=_names,
                        legend_title=str(meta.get("legend_title") or ""),
                    )
            except Exception:
                # A chart that cannot be re-laid-out must still export.
                pass


        # The figure has to fit INSIDE the card, not match it. The card also
        # holds a heading and possibly a notes block, so the plot gets the card
        # height minus an allowance for them. Getting this wrong is how a resized
        # card ends up with its chart spilling past the rounded bottom edge.
        #
        # This is an ESTIMATE, and deliberately so: the heading wraps to a
        # different number of lines at every card width, and a notes block's
        # height depends on its text. The CSS below makes the plot area a flex
        # child that absorbs whatever is left, and the page's relayout pass
        # hands Plotly the exact measured height once it is in the DOM -- so the
        # estimate only has to be right for the frame or two before that.
        this_height = card_height
        if free:
            notes_allow = 64.0 if str(notes or "").strip() else 0.0
            this_height = max(120.0, card_height - 96.0 - notes_allow)
        # A Plotly figure paints its own opaque background. With no card picture
        # that MUST match the card colour -- it is what makes the plot read as
        # part of the card rather than a rectangle dropped on it. But when the
        # user has put a picture behind the chart cards, an opaque paper colour
        # would hide that picture completely, so the paper and plot go
        # TRANSPARENT and the card's ::before layer shows through instead. The
        # card colour is still what decides light-vs-dark chart furniture (see
        # _theme_chart_furniture below), which is right: the picture is layered
        # over that colour, so the colour is still the base the text sits on.
        chart_card_picture = (
            str(t.get("card_image") or "").strip()
            and float(t.get("card_image_opacity", 35) or 0) > 0
            and "charts" in _card_image_groups(t))
        fig_r.update_layout(
            autosize=True, width=None, height=this_height,
            paper_bgcolor=("rgba(0,0,0,0)" if chart_card_picture
                           else t["card_bg"]),
            plot_bgcolor=("rgba(0,0,0,0)" if chart_card_picture
                          else t["card_bg"]),
        )
        # LAST, so nothing above can leave a dark plate or near-white tick
        # labels behind: the legend box and the colour bar are drawn by Plotly
        # inside the SVG, not by the report's CSS, so they are the only chart
        # furniture that does not follow the palette on its own.
        fig_r = _theme_chart_furniture(fig_r, t["card_bg"], t["text_color"])


        # Only the FIRST chart carries the plotly bundle, and only at all when
        # it is inlined. With inline_plotly=False the page loads the shared
        # file instead, so no chart needs to embed anything.
        include_js = bool(inline_plotly) and idx == 0
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


        # data-uid is the stable handle the in-app canvas uses to drag, resize and
        # target this card. It carries no styling and is inert in the saved file,
        # which is written with editor=False and gets no overlay at all.
        chart_cards[uid] = (
            f'<div class="chart-card" data-uid="{_h(uid)}" style="{col_span}">'
            f'<h2 style="font-size:{text_style["header_size"] / 16:.2f}rem;'
            f'font-family:{text_style["family"]};color:{text_style["header_color"]};">'
            f'{_h(display_title)}</h2>'
            + f'<div class="chart-wrap">{chart_html}</div>'
            + notes_html
            + "</div>"
        )


    # Assemble the board. In free mode the KPIs and the charts go into ONE
    # absolutely-positioned canvas, emitted in a single visual order, because
    # they share one coordinate space and a card may overlap any other. DOM
    # order is also z-order, so later cards sit on top -- which is what makes
    # "drag this one over that one" behave like a window manager rather than
    # silently hiding the card underneath.
    canvas_html = ""
    if free:
        ordered = sorted(
            set(chart_cards) | set(kpi_cards) | set(slicer_cards),
            key=lambda u: (float(free_geom.get(u, {}).get("y", 0)),
                           float(free_geom.get(u, {}).get("x", 0)), str(u)))
        # Slicers are read straight through: they are part of the same visual
        # order, so a filter control the user dragged above a chart stays above
        # it in the file as well.
        body = "".join(kpi_cards.get(u, "") + chart_cards.get(u, "")
                       + slicer_cards.get(u, "")
                       for u in ordered)
        # The heading is a CARD. It is emitted inside the same absolutely
        # positioned canvas as the charts and KPIs, with the same grip, the same
        # three resize handles and the same place in the saved arrangement --
        # which is the only way "the title behaves like every other card" can be
        # true. Left outside the canvas it was the one element on the board the
        # overlay could not see, so it could never be moved or resized.
        #
        # data-uid carries the reserved title key, so the overlay reports
        # "__title__" and the page stores it in the same blob as everything else.
        title_card = (
            f'<div class="report-header title-card" '
            f'data-uid="{canvas_geometry.TITLE_UID}" '
            f'style="{_free_style(title_geom)}">'
            f'<h1>{safe_title}</h1>'
            + (f'<div class="meta">{_h(now_str)}</div>'
               if t.get("show_meta", True) else "")
            + "</div>"
        )
        # The canvas must be told how tall it is: absolutely positioned cards
        # are out of flow, so without this everything below the first screen is
        # unreachable and unscrollable.
        height = canvas_geometry.canvas_height(free_geom, pad=0.0)
        canvas_html = (f'<div class="ly-canvas" style="height:{height:.2f}px;">'
                       f'{title_card}{body}</div>')
        # The metadata line is emitted once in free mode: inside the title card,
        # where it belongs now, and NOT again in the fixed footer below.
        meta_html = ""
        header_html = ""
    else:
        canvas_html = f'<div class="grid">{"".join(chart_cards.values())}</div>'
        # Grid mode (a session saved before the free canvas) has no canvas to
        # put the heading on, so it keeps the old in-flow header. Only reached by
        # that load path -- the dashboard itself always renders free.
        header_html = (
            f'<div class="report-header"><h1>{safe_title}</h1>'
            + (f'<div class="meta">{_h(now_str)}</div>'
               if t.get("show_meta", True) else "")
            + "</div>"
        )
        # Grid mode has no free canvas to position a slicer on, so it joins the
        # KPI strip: both are controls that belong ABOVE the chart grid and
        # share the same column template, so they line up rather than landing
        # one full-width block over the first chart.
        if slicer_cards:
            grid_slicers = (f'<div class="grid slicer-grid">'
                            f'{"".join(slicer_cards.values())}</div>')
            kpi_html = grid_slicers + (kpi_html or "")

    # With inline_plotly=False nothing in the body carries the bundle, so the
    # shared file has to be requested explicitly. The saved file leaves
    # inline_plotly=True and stays fully self-contained.
    plotly_tag = "" if inline_plotly else (
        '<script src="lytrize_plotly.js" charset="utf-8"></script>')

    # The editing overlay. Only emitted for the live canvas; the saved file must
    # stay a clean document with no grips, outlines or scripts.
    from modules.report_editor import editor_css, editor_js
    editor_css_block = editor_css(t["accent_color"]) if editor else ""
    editor_script = (editor_js(grid_cols_n, gap_px,
                                mode="free" if free else "grid")
                     if editor else "")


    # The live canvas document starts in Edit mode (chrome visible); Preview just
    # removes this class. The saved file is written with editor=False and gets
    # a plain <body>, so no editing chrome exists in it at all.
    body_class = ' class="ly-editing"' if editor else ""

    # -- live slicer engine ------------------------------------------------
    # The column-pruned payload, one spec per chart and the slicer list are
    # embedded so the saved file can filter and re-aggregate in the browser
    # with no server behind it. Only emitted when there is something to
    # filter: a report with no slicers must not carry megabytes of data it
    # will never use.
    lytrize_block = ""
    if report_payload and slicers:
        import json as _json

        def _embed(obj) -> str:
            # "</" escaped so a string cell containing "</script>" cannot
            # close the script element early and truncate the payload.
            return _json.dumps(obj, default=str).replace("</", "<\\/")

        if report_specs is None:
            # Bare 3-tuple sessions carry no meta; 6-tuples do (see
            # chart_service.serialize_chart). Build what we can from them.
            from modules.chartspec import chart_spec
            built = {}
            for item in charts:
                try:
                    if len(item) < 6 or not isinstance(item[5], dict):
                        continue
                    recipe = dict(item[5])
                    recipe.setdefault("uid", str(item[0]))
                    recipe.setdefault("chart_type", str(item[4] or ""))
                    built[str(item[0])] = chart_spec(recipe)
                except Exception:
                    continue
            report_specs = built
        from modules.report_slicer import slicer_js
        lytrize_block = (
            "<script>\n"
            "window.__lyPending = 0;\n"
            f"window.LYTRIZE_DATA = {_embed(report_payload or {})};\n"
            f"window.LYTRIZE_SPECS = {_embed(report_specs or {})};\n"
            f"window.LYTRIZE_SLICERS = {_embed(list(slicers))};\n"
            "</script>\n"
            f"<script>\n{slicer_js()}\n</script>"
        )

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
    /* A background IMAGE, layered on top of the theme colour rather than
       replacing it. That is the whole design: the theme colour stays the base
       layer and the image is composited over it at a chosen opacity, so the
       same picture reads correctly on a near-black preset and on a cream one
       without the user having to pick a matching overlay colour per theme. The
       theme's own text colour therefore always sits on something in its own
       family. */
    {bg_image_css}
    .wrapper {{
      max-width: {max_width};
      margin: 0 auto;
      width: 100%;
      box-sizing: border-box;
    }}


    .report-header {{ text-align: center; margin-bottom: 2rem; }}
    .report-header h1 {{ font-size: {t['title_size'] / 16:.2f}rem; font-weight: {_heading_weight(t.get('title_style'))}; font-style: {_font_style(t.get('title_style'))}; color: {t['title_color']}; font-family: {t['title_font']}, 'DejaVu Sans', Arial, sans-serif; }}
    .report-header .meta {{ font-size: 0.8rem; color: {t['text_color']}; }}
    .ly-canvas > .title-card h1 {{
      font-size: {t['title_size'] / 16:.2f}rem;
      font-weight: {_heading_weight(t.get('title_style'))};
      font-style: {_font_style(t.get('title_style'))};
      color: {t['title_color']};
      font-family: {t['title_font']}, 'DejaVu Sans', Arial, sans-serif;
    }}

    /* The heading as a CARD on the free canvas. Same box as a chart card, so it
       reads as one object with the same border, radius and shadow; the text
       inside is centred, which is what a report heading should be. Overflow is
       hidden so a heading that no longer fits its (resized) band crops rather
       than bleeding over the first row of charts. */
    .ly-canvas > .title-card {{
      background: {t['card_bg']};
      border: 1px solid {t['card_border']};
      border-radius: {card_radius}px;
      box-shadow: 0 2px 16px rgba(0,0,0,0.07);
      /* A title band is a sized box like any other card. Padding is reduced to
         the 0.35rem that keeps the text off the border: the old 0.6rem top and
         bottom on a short band was most of the box, which is what made the
         heading look like it was floating in an oversized card. */
      padding: 0.35rem 0.6rem;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      text-align: center; overflow: hidden;
    }}
    .ly-canvas > .title-card h1 {{
      margin: 0; min-width: 0; width: 100%;
      overflow-wrap: break-word; word-break: break-word; line-height: 1.2;
    }}
    /* The timestamp line sits directly under the heading -- no gap band. */
    .ly-canvas > .title-card .meta {{ margin-top: 0; }}


    /* ── KPI strip ──
       PADDING AND GAPS ARE DELIBERATELY ZERO.
       The KPI card used to carry `padding: 1rem 1.4rem`, a 0.25rem gap under
       the icon and a 0.2rem gap above the label. On a card the user has sized
       to 120px that is a third of the box spent on air: the number floated in
       the middle with dead space above and below it, and the emoji sat in its
       own band. The card is now a tight stack -- icon, number, label, nothing
       between them -- so the whole box is the content. */
    .kpi-row {{
      display: flex; gap: {gap_px}px; flex-wrap: wrap;
      margin-bottom: 1.5rem; justify-content: center;
    }}
    .kpi-card {{
      background: {t['kpi_bg']};
      border-radius: {card_radius}px;
      padding: 0.25rem 0.5rem;
      text-align: center;
      box-shadow: 0 2px 12px rgba(0,0,0,0.07);
      min-width: 130px; flex: 1; max-width: 220px;
      border: 1px solid {t['card_border']};
    }}
    /* No margin under the icon: the gap between the emoji and the value is
       exactly the "KPI emoji & values gap" that was reported as dead space. */
    .kpi-icon  {{ font-size: 1.4rem; margin: 0; }}
    /* Defaults only. Every card carries its own inline style for the value's
       colour and size and the label's size and case, because those are
       per-report settings now; this block is what a card with no inline style
       (and the print fallback) falls back to. */
    .kpi-value {{
      font-size: 1.4rem; font-weight: 800;
      color: {t.get('kpi_val_color') or t['accent_color']};
      line-height: 1.2; overflow: visible; margin: 0;
    }}
    .kpi-label {{
      font-size: {max(7, int(t.get('kpi_label_size', 12) or 12))}px;
      color: {t['kpi_text_color']};
      margin-top: 0; font-weight: 600;
      text-transform: {'uppercase' if t.get('kpi_label_upper', True) else 'none'};
      letter-spacing: {'0.06em' if t.get('kpi_label_upper', True) else '0'};
    }}

    /* Slicer cards share the KPI chrome -- a control card, not a plot -- so
       a board of filters and KPIs reads as one instrument panel. */
    .slicer-card {{
      background: {t.get('slicer_bg') or t['kpi_bg']};
      border: 1px solid {t.get('slicer_bd') or t['card_border']};
      border-radius: {card_radius}px;
      padding: 0.5rem 0.75rem;
      box-shadow: 0 2px 12px rgba(0,0,0,0.07);
      display: flex; flex-direction: column;
      overflow: hidden;
      box-sizing: border-box;
      /* NO min-width here: on the free canvas the card's width comes from
         canvas_geometry's clamp (12% of the canvas, which on a narrow board
         is under 160px), and a min-width larger than that tile would make the
         card spill past the placement the user dragged it to. */
      min-width: 0;
    }}
    .slicer-title {{
      font-size: 0.78rem; font-weight: 700;
      color: {t.get('slicer_text') or t['kpi_text_color']};
      text-transform: uppercase;
      letter-spacing: 0.06em;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      margin-bottom: 0.35rem;
    }}
    /* flex:1 + overflow lets the value list scroll INSIDE a short card,
       which is what note 4 asks for: the slicer never grows to dictate the
       chart layout. */
    .slicer-body {{ flex: 1; min-height: 0; overflow: auto; }}
    .slicer-opts {{
      display: flex; flex-direction: column; gap: 2px;
      font-size: 0.8rem; color: {t.get('slicer_text') or t['text_color']};
    }}
    .slicer-opt {{
      display: flex; align-items: center; gap: 0.4rem;
      cursor: pointer; padding: 2px 0;
      user-select: none;
    }}
    .slicer-opt:hover {{ opacity: 0.8; }}
    .slicer-opt input {{ margin: 0; flex: none; }}
    .slicer-opt span {{
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }}
    .slicer-select, .slicer-search {{
      width: 100%; box-sizing: border-box;
      font-size: 0.8rem; padding: 4px 6px;
      border: 1px solid {t.get('slicer_bd') or t['card_border']};
      border-radius: 6px;
      background: {t.get('slicer_bg') or t['bg_color']};
      color: {t.get('slicer_text') or t['text_color']};
      font-family: inherit;
    }}
    .slicer-slider {{ display: flex; flex-direction: column; gap: 4px; }}
    .slicer-slider input[type=range] {{ width: 100%; }}
    .slicer-range {{
      font-size: 0.72rem; color: {t['text_color']};
      opacity: 0.85; text-align: center;
    }}
    .slicer-topn {{ font-size: 0.8rem; color: {t['text_color']}; }}
    .slicer-topn input {{
      width: 4.5em; padding: 3px 4px;
      border: 1px solid {t['card_border']};
      border-radius: 5px;
      background: {t['bg_color']}; color: {t['text_color']};
    }}
    .slicer-status {{
      font-size: 0.7rem; color: {t.get('slicer_accent') or t['accent_color']};
      margin-top: 0.3rem; opacity: 0.9;
      font-style: italic;
    }}
    .slicer-empty {{
      font-size: 0.75rem; opacity: 0.6; font-style: italic;
      color: {t['text_color']};
    }}


    hr {{ border: none; border-top: 1px solid {t['card_border']}; margin: 1.5rem 0; }}


    /* ── Chart grid ── */
    .grid {{
      display: grid;
      grid-template-columns: {grid_css};
      gap: {gap_px}px;
      width: 100%;
    }}

    /* ── Free canvas ──
       One relatively-positioned box; every card inside is absolutely placed by
       the inline left/top/width/height the geometry produced. The inline height
       on the container comes from canvas_geometry.canvas_height(), because
       absolutely positioned children are out of flow and contribute nothing to
       their parent's height -- without it the canvas would collapse to nothing
       and the board would be an unreachable pile of overlapping cards. */
    .ly-canvas {{
      position: relative;
      width: 100%;
      min-height: 1px;
    }}
    .ly-canvas > .chart-card,
    .ly-canvas > .kpi-card,
    .ly-canvas > .title-card,
    /* The slicer card belongs here with the others: without this selector it
       stays `position: static`, so its inline left/top are IGNORED -- the card
       never leaves the flow (it sits attached under the title, whatever the
       user dragged) and the overlay's resize handles resize an element whose
       position cannot change. It is the "slicer is stuck to the title card
       and not movable" report. */
    .ly-canvas > .slicer-card {{
      position: absolute;
      /* box-sizing matters here: the inline height is the CARD's height, and
         without this the padding would be added on top of it, so every card
         would be ~2.4rem taller than the user dragged it and would overlap its
         neighbour below. */
      box-sizing: border-box;
    }}
    /* The plot is a flex child so it absorbs whatever the heading and notes
       leave behind. A card resized to 200px really is 200px tall with a smaller
       chart in it, rather than a 200px box with the old 400px plot spilling out
       of the bottom. */
    .ly-canvas > .chart-card {{
      display: flex;
      flex-direction: column;
    }}
    .ly-canvas .chart-wrap {{
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
    }}
    /* A KPI on the free canvas is a sized box the user chose, not a strip that
       sizes itself. Padding is ZERO (see the KPI strip block above for why):
       the card's height is the user's number, and every rem of padding plus
       every rem of inner gap is height the value does not get. Content is
       centred and overflow is hidden, so a card squeezed below its content
       crops rather than spilling over its neighbour. */
    .ly-canvas > .kpi-card {{
      padding: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      min-width: 0;
      max-width: none;
    }}
    .ly-canvas .chart-wrap > div,
    .ly-canvas .js-plotly-plot,
    .ly-canvas .plot-container {{
      flex: 1 1 auto;
      min-height: 0;
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
      /* AUTO-FIT. The card width is decided by CSS at render time -- a dragged
         card can be any width -- so a fixed-size heading either collides with the
         edit chrome (grip / toolbar, which sit in the top corners) or runs off
         the card. Let the heading claim the full width and wrap instead. */
      min-width: 0; overflow-wrap: break-word; word-break: break-word;
      line-height: 1.25;
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
      font-style: {_font_style(t.get('notes_style'))};
      font-weight: {_font_weight(t.get('notes_style'))};
      white-space: pre-wrap;
      word-break: break-word;
    }}

    /* KPI tiles share the chart grid's column template and gap exactly, so a
       KPI at column 0 lines up pixel-for-pixel with the first column of
       charts. They are their own grid (not a child of the chart grid) so that
       row numbering stays independent -- a chart's saved row can therefore
       never be invalidated by a KPI added or resized above it. */
    .kpi-grid {{ margin-bottom: 1.5rem; align-items: stretch; }}
    .kpi-grid .kpi-card {{ min-width: 0; }}

    /* In-app editing chrome. Present only in the live canvas document (the
       saved file is written with editor=False and gets none of it); everything
       is scoped to `.ly-editing`, and Preview simply removes that class from
       <body> to hide all of it without re-rendering. */
    /* Per-card background image. Emitted last (just before the editor chrome)
       so its `position` / `isolation` / `::before` rules come after every base
       card rule above. Note the free canvas still wins on positioning: its
       `.ly-canvas > .chart-card` selector is MORE specific than the bare
       `.chart-card` used here, so a placed card keeps its absolute placement
       and only gains the stacking context the picture needs. */
    {card_image_css}

    {editor_css_block}

    /* Caller-supplied CSS, used by the live canvas to offer the app's bundled
       fonts to Chromium (it cannot see Qt's font database). */
    {extra_css}


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
      /* The free canvas is a SCREEN layout. Absolute pixel placement is
         meaningless across paginated paper -- page after page would show
         overlapping cards printing on top of each other, and a reader would see
         a fraction of the board. So print falls back to ordinary document flow
         in the same visual order: every card appears, legibly, instead of a
         faithful reproduction of a window-manager arrangement that does not
         survive the trip to paper. The fixed heights go with the positioning, or
         a card would keep a screen height on a page sized for text. */
      .ly-canvas {{
        display: block !important;
        position: static !important;
        height: auto !important;
        min-height: 0 !important;
      }}
      .ly-canvas > .chart-card,
      .ly-canvas > .kpi-card,
      .ly-canvas > .title-card,
      .ly-canvas > .slicer-card {{
        /* `relative`, not `static`. Both put the card back in ordinary document
           flow -- an offset-less relative box lays out identically to a static
           one, so the reflow above is unchanged -- but relative also makes the
           card a containing block for its absolutely positioned children. That
           matters because the per-card background image is drawn on the card's
           own `::before`: with `static` that pseudo-element would escape and
           anchor to the page, printing one full-page picture over the whole
           sheet instead of one picture per card. */
        position: relative !important;
        left: auto !important;
        top: auto !important;
        width: 100% !important;
        height: auto !important;
        margin-bottom: 1rem !important;
      }}
      /* A heading is a screen-sized band; on paper it just needs to be as tall
         as its own text, so the same fixed pixel height would print as a mostly
         empty box. */
      .ly-canvas > .title-card {{ min-height: 0; padding: 0.6rem 0.9rem !important; }}
      /* A KPI is a number and a label; it does not need a chart's worth of
         paper. Giving it the chart minimum height printed rows of blank card. */
      .ly-canvas > .kpi-card {{ min-height: 0; }}
      .ly-canvas .chart-wrap {{
        flex: none !important;
        min-height: 260px !important;
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
      /* Full-width chart cards keep their expanded width. Exclude cards with
         an explicit grid-column START (free-form tiles) -- those carry a real
         column index and must keep it, or every tiled card would print as
         full-bleed and stacked. */
      .chart-card[style*="grid-column: 1 / -1"] {{
        width: 100% !important;
      }}
      .kpi-row {{
        break-inside: avoid;
        page-break-inside: avoid;
        margin-bottom: 1rem;
      }}
      /* Tiled KPIs keep their explicit grid placement in print, exactly as
         chart cards do -- otherwise every KPI would full-bleed the page. */
      .kpi-grid {{ display: grid !important; gap: 1rem !important; }}
      .kpi-card {{
        box-shadow: none !important;
        border: 1px solid {t['card_border']} !important;
      }}
      .chart-wrap svg.main-svg {{ width: 100% !important; height: auto !important; }}
      hr {{ border-top: 1px solid {t['card_border']} !important; }}
    }}
  </style>
  {plotly_tag}
</head>
<body{body_class}>
  <div class="wrapper">
    {header_html}
    {kpi_html}
    {canvas_html}
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
          var height = w ? w.offsetHeight : el.offsetHeight;
          /* Height is measured too, not just width. On the free canvas the
             card's height is whatever the user dragged, and the plot has to
             fill whatever the heading and notes leave -- which cannot be
             known when the figure is built, because the heading wraps to a
             different number of lines at every card width. Measuring here is
             what makes a resized card show a resized chart instead of its
             original one spilling past the bottom edge. */
          if (width > 0) Plotly.relayout(el, {{width: width, autosize: true,
                                             height: height > 0 ? height : undefined}});
        }} catch(e) {{}}
      }});
    }}
    window.addEventListener('load',        function() {{ setTimeout(relayoutAll, 150); }});
    window.addEventListener('resize',      function() {{ setTimeout(relayoutAll,  80); }});
    window.addEventListener('beforeprint', function() {{ relayoutAll(); }});
  </script>
  <div style="position:fixed; bottom:0; left:0; width:100%; text-align:center; padding: 1rem 0; background:{t['bg_color']}; color:{t['text_color']};">{meta_html}</div>
  {editor_script}
  {lytrize_block}
</body>
</html>"""
