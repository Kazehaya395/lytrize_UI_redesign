"""backend/chart_display.py -- Headless Layout + Typography applier."""
from __future__ import annotations
import copy
import logging
from typing import Iterable, Optional

log = logging.getLogger(__name__)

FONT_STACKS = {"Inter": "Inter, system-ui, sans-serif", "Arial": "Arial, Helvetica, sans-serif", "Georgia": "Georgia, serif"}
FONT_FAMILIES = ["Inter", "Arial", "Georgia", "Times New Roman", "Verdana", "Courier New"]
FONT_STYLES = ["Normal", "Bold", "Italic", "Bold Italic", "Underline"]
LABEL_POSITIONS = ["outside", "inside", "auto"]
BAR_MODES = ["group", "stack", "relative", "overlay"]
LEGEND_POSITIONS = ["Top Right", "Top Left", "Bottom Right", "Bottom Left"]

# Fonts the settings UI offers as choices. Most are NOT installed on a typical
# box, and asking the browser for a missing family makes it silently substitute
# a default -- so the picker used to look broken ("several fonts do not work",
# the same heading rendering for every choice). available_fonts() filters this
# list down to what fontconfig can actually resolve, so every option shown is a
# font that will really change the chart.
_FONT_CHOICES = [
    ("Inter", "sans-serif"), ("Source Sans 3", "sans-serif"),
    ("Noto Sans", "sans-serif"), ("DejaVu Sans", "sans-serif"),
    ("Lato", "sans-serif"), ("Ubuntu", "sans-serif"),
    ("Arial", "sans-serif"), ("Calibri", "sans-serif"),
    ("Verdana", "sans-serif"), ("Oswald", "sans-serif"),
    ("Barlow Condensed", "sans-serif"),
    ("Noto Serif", "serif"), ("DejaVu Serif", "serif"),
    ("EB Garamond", "serif"), ("Georgia", "serif"),
    ("Times New Roman", "serif"),
    ("Fira Code", "monospace"), ("DejaVu Sans Mono", "monospace"),
    ("JetBrains Mono", "monospace"), ("Liberation Mono", "monospace"),
    ("Courier New", "monospace"), ("Comic Sans MS", "cursive"),
    ("Impact", "sans-serif"), ("Trebuchet MS", "sans-serif"),
    ("Helvetica", "sans-serif"), ("Palatino Linotype", "serif"),
    ("Candara", "sans-serif"), ("Corbel", "sans-serif"),
    ("Constantia", "serif"), ("Lucida Console", "monospace"),
    ("Consolas", "monospace"), ("Sora", "sans-serif"),
    ("Cambria", "serif"),
]
# Families the app falls back to when nothing else resolves, so the list is
# never empty on a bare system.
_FONT_FALLBACKS = [
    ("Liberation Sans", "sans-serif"), ("DejaVu Sans", "sans-serif"),
    ("Liberation Serif", "serif"), ("DejaVu Serif", "serif"),
    ("Liberation Mono", "monospace"), ("DejaVu Sans Mono", "monospace"),
]

_installed_families_cache: Optional[set] = None


def installed_font_families() -> set:
    """Family names fontconfig can actually resolve on this machine.

    Queried once and cached: the settings panel asks for this while it is
    being built, and an ``fc-list`` subprocess is far too slow to run per
    combo. Falls back to an empty set (meaning "cannot tell") if fontconfig
    is unavailable, so a missing fc-list never breaks the UI.
    """
    global _installed_families_cache
    if _installed_families_cache is not None:
        return _installed_families_cache

    families: set = set()
    try:
        import subprocess
        out = subprocess.run(
            ["fc-list", "--format", "%{family}\n"],
            capture_output=True, timeout=10,
        ).stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            # Each line may hold a comma-separated family list.
            for part in line.split(","):
                part = part.strip()
                if part:
                    families.add(part.lower())
    except Exception:
        log.debug("fc-list unavailable; font choices will not be filtered",
                  exc_info=True)
        families = set()

    _installed_families_cache = families
    return families


def font_is_available(name: str) -> bool:
    """True when *name* can really be drawn: bundled by the app, or installed.

    A family the app ships in ``app_assets/fonts`` counts as available even
    though fontconfig has never heard of it -- it is registered with Qt at
    startup and offered to the chart shell as @font-face, so it renders
    without any system package being installed.
    """
    if not name:
        return False
    key = str(name).strip().lower()
    if key in installed_font_families():
        return True
    try:
        from font_bundle import bundled_families
        if key in {f.lower() for f in bundled_families()}:
            return True
    except Exception:
        pass
    # A proprietary name counts as available if its bundled metric-compatible
    # stand-in is present, because that is what will actually be drawn.
    try:
        from font_bundle import SUBSTITUTES
        sub = SUBSTITUTES.get(str(name).strip())
        if sub and sub.lower() in {f.lower() for f in bundled_families()}:
            return True
    except Exception:
        pass
    return False


def available_fonts(prefer: Optional[Iterable[str]] = None) -> list:
    """The font choices to offer, restricted to families that really exist.

    Order of authority:

    1. **Bundled** fonts (``app_assets/fonts``, registered at startup) always
       qualify -- the app ships them, so they work on any machine.
    2. **System** families that fontconfig can resolve are also offered, so a
       user who has installed extra fonts still sees them.
    3. Anything else is dropped, because a family the machine cannot render
       silently produces no visible change -- the "half the fonts do not
       work" complaint.

    ``prefer`` keeps a stable leading order for named families. The list is
    never empty: if neither source yields anything, the bundled set is used
    unconditionally.
    """
    try:
        from font_bundle import bundled_families
        bundled = bundled_families()
    except Exception:
        bundled = []

    ordered: list = []

    def _add(name):
        # font_is_available now covers bundled + installed + the bundled
        # stand-in for a proprietary name, so it is the single gate.
        if name and name not in ordered and font_is_available(name):
            ordered.append(name)

    for name in (prefer or ()):
        _add(name)
    for name in bundled:
        _add(name)
    for name, _generic in _FONT_CHOICES:
        _add(name)

    if not ordered:
        # Nothing discoverable: offer the bundled set rather than an empty
        # picker, since those files are guaranteed present.
        ordered = list(bundled)
    if not ordered:
        installed_now = installed_font_families()
        ordered = [n for n, _g in _FONT_FALLBACKS if n.lower() in installed_now]
    if not ordered:
        ordered = [n for n, _g in _FONT_FALLBACKS]

    return ordered


def _generic_for(name: str) -> str:
    for fam, generic in _FONT_CHOICES:
        if fam == name:
            return generic
    return "sans-serif"


# The legend needs a section of its own, and it cannot get one from
# margin.autoexpand alone.  Auto-expand grows the outer margin to fit
# whatever needs room, but on the LEFT that same margin must also hold the
# y-axis tick labels and the rotated y-axis title -- so the legend and the
# axis furniture end up stacked in one shared strip (legend painted over
# the axis text).  The right side always looked right precisely because
# nothing else competes for that margin.
#
# So each position declares which side it occupies, and we do two things:
#   1. reserve a slice of the PLOT DOMAIN for the legend, so the data area
#      shrinks and the legend box never sits over the marks, and
#   2. widen the outer margin on that side so the axis labels/title keep
#      their own space, clear of the legend.
#
# Anchors still matter: the box has to grow *away* from the plot.
#   left  -> x=0, xanchor "left"   (box extends right, into the strip)
#   right -> x=1, xanchor "right"  (box extends left, into the strip)
_LEGEND_ANCHORS = {
    "Top Right":    ("right", 1.0, "top"),
    "Top Left":     ("left",  1.0, "top"),
    "Bottom Right": ("right", 0.0, "bottom"),
    "Bottom Left":  ("left",  0.0, "bottom"),
}

# Fraction of the plot width handed to the legend column.  Proportional so
# it scales with the card instead of being a fixed slab on small charts.
LEGEND_STRIP = 0.26
LEGEND_STRIP_MIN = 0.20
LEGEND_STRIP_MAX = 0.50
# Extra outer margin on the legend's side, so the y-axis title has somewhere
# to live that is NOT the legend's column.
LEGEND_AXIS_MARGIN = 96
# The reserved column has to hold the legend box AND, on the left, the y-axis
# tick labels -- those are drawn just outside the axis, i.e. at the RIGHT edge
# of the column, so a column sized for the box alone leaves the labels under
# the plate (measured at 700px: ~130 label pixels dimmed by the plate).
#   chrome  = plate padding + swatch + legend title
#   char    = per-character width of a legend item (11px Inter, rounded up)
#   axis    = room for the tick labels next to the axis (left side only)
_LEGEND_CHAR_PX = 8
_LEGEND_CHROME_PX = 44
_LEGEND_AXIS_PX = 30
# Plot width the estimate is calibrated against.  Deliberately smaller than a
# typical card so the estimate errs on the generous side at small widths; the
# strip is a fraction, so this only has to be the right order of magnitude.
_LEGEND_REF_WIDTH = 500


def _legend_need_px(names, legend_title="", side="left"):
    """Pixels the reserved legend column actually has to be, for these labels.

    This is the real measurement the fraction below is derived from: plate
    padding + swatch + the longest label actually drawn, plus room for the
    axis tick labels when the column is on the left.
    """
    longest = 0
    for n in list(names or []) + ([legend_title] if legend_title else []):
        longest = max(longest, len(str(n)))
    need = _LEGEND_CHROME_PX + _LEGEND_CHAR_PX * longest
    if side == "left":
        need += _LEGEND_AXIS_PX
    return need


def _legend_strip(names, legend_title="", side="left", width_px=None):
    """Fraction of the plot width to reserve for a legend with these labels.

    A pure fraction of the card width is what produced the "big gap between
    the chart and the legend" report: the column is fixed at 20% of whatever
    the card happens to be, so a 1900px card reserved ~380px for a legend box
    that needs ~76px. The reservation has to track the *pixel* need instead,
    so pass ``width_px`` (the live view width) and the strip shrinks as the
    card grows.

    Without a width (headless callers, export) the old fixed-fraction
    behaviour is kept, so nothing depends on a size it cannot know.
    """
    need = _legend_need_px(names, legend_title, side)
    if width_px and width_px > 0:
        # Never wider than the label needs, never so thin the box cannot fit.
        frac = need / float(width_px)
        return max(0.0, min(LEGEND_STRIP_MAX, frac))
    return max(LEGEND_STRIP_MIN,
               min(LEGEND_STRIP_MAX, need / float(_LEGEND_REF_WIDTH)))


def legend_anchor(legend_position):
    """Return the (side, y, yanchor) placement for a legend position.

    ``side`` is the reserved strip ("left"/"right"); the x coordinate and
    xanchor follow from it, so callers only need the side to lay out the
    rest of the figure.
    """
    return _LEGEND_ANCHORS.get(str(legend_position or "").strip())


def reserve_legend_column(fig, legend_position, strip=None,
                          axis_margin=LEGEND_AXIS_MARGIN, names=None,
                          legend_title="", width_px=None):
    """Carve a dedicated legend column out of *fig*'s plot domain.

    Shrinks the x-domain on the legend's side and widens the matching outer
    margin, so the legend, the data and the axis labels each get their own
    non-overlapping area.  The reserved fraction grows with the longest
    series name (and, on the left, with the axis furniture that shares that
    side) so the legend cannot spill back over the plot or the axis text.

    Pass ``width_px`` (the live view width) to size the column against the
    pixels the labels actually need; without it the reservation falls back to
    a fixed fraction of the card, which is what left the wide-card gap.

    Returns the side that was reserved, or None when the position is unknown
    (figure left untouched).
    """
    place = legend_anchor(legend_position)
    if not place:
        return None
    side = place[0]
    try:
        m = dict(fig.layout.margin or {})
    except Exception:
        m = {}
    if strip is None:
        strip = _legend_strip(names, legend_title, side, width_px)
    if side == "left":
        m["l"] = max(int(m.get("l") or 0), int(axis_margin))
        domain = [float(strip), 1.0]
    else:
        m["r"] = max(int(m.get("r") or 0), int(axis_margin))
        domain = [0.0, 1.0 - float(strip)]
    try:
        fig.update_layout(margin=m, xaxis=dict(domain=domain))
    except Exception:
        return None
    return side




def infer_legend_position(fig):
    """Recover the legend position name from a figure that already has one.

    A figure carries only the resolved anchors (``x``/``y``/``xanchor``/
    ``yanchor``), not the friendly name the user picked. When the column has
    to be re-sized later -- after the view finally knows its pixel width --
    the name has to be recovered from those anchors to pick the same side.

    Returns the position name, or None if the figure has no legend box.
    """
    try:
        legend = fig.layout.legend
    except Exception:
        return None
    if legend is None:
        return None
    try:
        anchor = str(legend.xanchor or "")
        yanchor = str(legend.yanchor or "")
    except Exception:
        return None
    if not anchor:
        return None
    side = "left" if anchor == "left" else "right"
    if yanchor == "top":
        return "Top Left" if side == "left" else "Top Right"
    if yanchor == "bottom":
        return "Bottom Left" if side == "left" else "Bottom Right"
    return None


def refit_legend_width(fig, width_px):
    """Re-size an already-reserved legend column against the real view width.

    Figures are built on a worker thread with no view, so the reservation
    falls back to a flat fraction of an assumed card width. Once the widget
    is on screen its true width is known, and the column can be sized against
    the pixels the labels actually need -- that is what removes the wide gap
    between the plot and the legend on a full-width chart.

    Returns True when the domain was rewritten. A figure without a reserved
    legend column (legend off, or overlaid rather than in its own strip) is
    left untouched.
    """
    if fig is None or not width_px or width_px <= 0:
        return False
    pos = infer_legend_position(fig)
    if not pos:
        return False
    try:
        title = ""
        legend = fig.layout.legend
        if legend is not None and legend.title is not None:
            title = str(getattr(legend.title, "text", "") or "")
        names = [getattr(tr, "name", "") for tr in (fig.data or [])
                 if getattr(tr, "showlegend", True) is not False]
    except Exception:
        return False
    return bool(reserve_legend_column(fig, pos, names=names,
                                      legend_title=title,
                                      width_px=width_px))


# Colours shared by the legend plate and the colour-bar frame when their
# border is switched on (theme_figure() re-tints them per mode).
LEGEND_BORDER_COLOR = "rgba(148, 163, 184, 0.35)"


def _apply_colorbar_border(fig, show_border):
    """Turn the colour bar / colour range frame on or off on every trace.

    Heatmaps, matrix tables and choropleths draw their own ``colorbar`` and
    never go through the legend block, so "Show border" has to reach them
    separately or the toggle would look like it only half worked.  Both a
    per-trace ``colorbar`` and a shared ``layout.coloraxis.colorbar`` are
    covered; anything a trace doesn't have is skipped.
    """
    width = 1 if show_border else 0
    targets = []
    for tr in getattr(fig, "data", []) or []:
        cb = getattr(tr, "colorbar", None)
        if cb is not None:
            targets.append(cb)
    try:
        ca = getattr(fig.layout, "coloraxis", None)
        if ca is not None and getattr(ca, "colorbar", None) is not None:
            targets.append(ca.colorbar)
    except Exception:
        pass
    for cb in targets:
        try:
            cb.borderwidth = width
            if show_border:
                cb.bordercolor = LEGEND_BORDER_COLOR
        except Exception:
            pass


def default_layout_opts():
    # show_value_labels defaults to True so freshly generated charts keep
    # their numeric value labels (matches the checked box in the design
    # mockups); unchecking it hides the labels again.
    #
    # show_border (Layout tab, right under "Show grid") draws the frame of
    # the legend AND of the colour bar/colour range.  It defaults to True
    # because that is what the charts already looked like before the toggle
    # existed -- it is a control over the current look, not a new style.
    return {"title": "", "x_label": "", "y_label": "", "legend_title": "", "legend_labels": {}, "show_value_labels": True, "value_label_color": "#ffffff", "label_position": "outside", "bar_mode": "group", "bar_gap": 0.28, "show_grid": True, "show_border": True, "legend_position": "Top Right"}

def default_typo_opts():
    # Sizes mirror the readouts on the Typography Options tab of
    # chart_designs.pdf (header 28 / title 16 / axis 12 / legend 11).
    # header_align places the header across the full canvas width (see
    # _HEADER_ANCHORS); "left" is the default so existing charts are
    # unchanged, and it auto-adjusts to whatever the chart area is.
    return {"family": "Inter", "font_style": "Normal", "header_size": 28, "header_color": "#6163df", "header_family": "Inter", "header_font_style": "Normal", "header_align": "left", "axis_title_size": 16, "axis_title_color": "#cbd5e1", "axis_tick_size": 12, "axis_tick_color": "#94a3b8", "legend_title_size": 12, "legend_title_color": "#cbd5e1", "legend_item_size": 11, "legend_item_color": "#e2e8f0"}

def _font_stack(name):
    if not name:
        return FONT_STACKS["Inter"]
    # Resolve FIRST, against the fonts the app bundles. A proprietary name the
    # user picked (Georgia, Arial, ...) maps to its metric-compatible open
    # stand-in, so the familiar label still produces a real, visually matching
    # family instead of whatever Chromium substitutes. The original name stays
    # in the stack as a later fallback, so a machine that genuinely has the
    # real thing installed still uses it.
    try:
        from font_bundle import resolve
        resolved = resolve(name)
    except Exception:
        resolved = name
    if resolved == name and name in FONT_STACKS:
        return FONT_STACKS[name]
    if resolved == name:
        return f"{name}, sans-serif"
    return f"{resolved}, {name}, sans-serif"

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
    # The heading is drawn by Qt (a plain QLabel in the card header), not by
    # Chromium, so it resolves through Qt's own font database. Map a
    # proprietary name onto the bundled metric-compatible family, otherwise Qt
    # silently falls back and the heading ignores the chosen font.
    try:
        from font_bundle import resolve
        fam = resolve(fam)
    except Exception:
        pass
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


def _title_band_px(header_size):
    """Top margin (px) needed to seat a header of *header_size* above the plot.

    The old value was a flat 34px, which happens to fit a 28px header and
    nothing else: at the 36px header the title was drawn taller than its own
    band and spilled down over the plot, and at 60px it was badly clipped.
    Scaling with the actual font keeps every size self-consistent.
    """
    try:
        size = float(header_size or 28)
    except Exception:
        size = 28.0
    # ~1.2em is Plotly's line box for a single-line title, +4px of breathing
    # room so the glyphs never touch the plot area.
    return int(round(size * 1.2)) + 4


# Header alignment. The title is anchored in CONTAINER coordinates (the whole
# canvas, margins included) rather than plot/paper coordinates, so "center"
# centres on the entire chart area -- including the strip reserved for the
# legend -- and "right" pins to the true right edge of the card. Anchoring in
# the plot domain instead would slide the header left by half the legend
# column, which is the misalignment the dropdown is meant to remove.
_HEADER_ANCHORS = {
    "left": (0.0, "left"),
    "center": (0.5, "center"),
    "right": (1.0, "right"),
}


def _apply_title_placement(fig, header_size=28, align="left"):
    """Seat the chart header at the very top of the canvas, per *align*.

    Two problems this fixes:

    * The empty band ABOVE the title. The title used to sit at ``y=0.98``,
      so 2% of the card height was dead space above it -- on a tall card that
      is a visible gap. It is now pinned at ``y=1.0``/``yanchor="top"``, the
      extreme top of the chart area.
    * The band was a fixed 34px regardless of the header size, so a 36px or
      60px header overflowed its own space. The margin is now derived from
      the font size (see ``_title_band_px``).

    Alignment is applied in container coordinates so the header spans the
    full canvas width, covering the legend's column as well as the plot.

    Idempotent: it only ever writes placement keys, never the title text
    or font, so re-running it (every live preview, every re-generate)
    cannot compound.
    """
    try:
        # Only reposition when a title is actually drawn; charts without
        # one should get their top margin back.
        if not fig.layout.title.text:
            return
        x, xanchor = _HEADER_ANCHORS.get(
            str(align or "left").strip().lower(), _HEADER_ANCHORS["left"])
        # y=1.0 parks the title's line box flush against the canvas edge, but
        # the box includes ascender space that sits a few px ABOVE the first
        # glyph, which showed up as a measurably negative top (-4px) and risked
        # shaving the tops off tall/italic headers. A 6px pad costs nothing
        # visually at this scale and guarantees no glyph is ever cut, while
        # still removing the dead band the old y=0.98 left behind.
        fig.update_layout(
            title=dict(x=x, xanchor=xanchor, y=1.0, yanchor="top",
                       xref="container", yref="container",
                       pad=dict(t=6, b=2, l=0, r=0)),
        )
        # NB: ``dict(fig.layout.margin)`` raises ValueError on some Plotly
        # versions (the Margin object is not iterable as key/value pairs) --
        # which the surrounding try/except would swallow, silently leaving
        # the top margin untouched. Go through to_plotly_json(), which is
        # the supported accessor and is already used elsewhere in this file.
        m = dict(fig.layout.margin.to_plotly_json() or {})
        m["t"] = _title_band_px(header_size)
        fig.update_layout(margin=m)
    except Exception:
        log.debug("title placement failed", exc_info=True)


def apply_display_options(fig, layout_opts=None, typo_opts=None,
                           width_px=None):
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
    # Two things are needed, not one.  The anchor stops the box growing
    # *across* the data; the reserved column stops it sharing the outer
    # margin with the y-axis labels/title (which is what made left-hand
    # positions unreadable while right-hand ones always looked fine).
    lp = lo.get("legend_position", "")
    place = legend_anchor(lp)
    # One toggle governs every frame the chart draws around a key: the
    # legend plate and the colour bar / colour range.  Default True keeps
    # the look charts already had, so this only changes when unchecked.
    show_border = bool(lo.get("show_border", True))
    if place:
        side, ly, lyanchor = place
        # Reserve the column AFTER the xaxis showgrid/title updates above,
        # so this domain is not clobbered by a later update_layout.
        #
        # The strip is sized from the labels that will actually be painted:
        # legend renames already happened above, so the trace names on f2 are
        # the final ones (no need to thread them in from the caller), and the
        # legend title comes from the layout options.
        reserve_legend_column(
            f2, lp,
            names=[getattr(tr, "name", "") for tr in f2.data
                   if getattr(tr, "showlegend", True) is not False],
            legend_title=lo.get("legend_title", ""),
            width_px=width_px,
        )
        f2.update_layout(legend=dict(
            x=0.0 if side == "left" else 1.0,
            y=ly,
            xanchor="left" if side == "left" else "right",
            yanchor=lyanchor,
            # A translucent plate keeps the legend readable wherever it
            # lands, without hiding the plot behind an opaque slab.
            bgcolor="rgba(15, 23, 42, 0.72)",
            bordercolor=(LEGEND_BORDER_COLOR if show_border
                         else "rgba(0, 0, 0, 0)"),
            borderwidth=1 if show_border else 0,
        ))

    # Colour bars (heatmaps, matrices, choropleths) draw their own frame and
    # are not reached by the legend block above, so the same toggle is applied
    # to them here -- "border of legend or colorbar or color range".
    _apply_colorbar_border(f2, show_border)


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
            # An "outside" label on the tallest bar is drawn ABOVE the bar,
            # i.e. outside the plot area, and Plotly clips it by default --
            # which is the half-cut number at the top of the chart in the
            # "clipping chart visual look" report. Unclipping lets the label
            # use the header band it already has instead of being sliced.
            if str(position) == "outside":
                try:
                    tr.cliponaxis = False
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

    # Applied LAST so the placement wins over the margin writes above
    # (legend reservation only ever widens l/r, never t).
    _apply_title_placement(f2, t.get("header_size", 28),
                           t.get("header_align", "left"))
    return f2


# ---------------------------------------------------------------------------
# Theme layer (light / dark canvas)
# ---------------------------------------------------------------------------
# A figure is generated once but shown on whichever canvas the app is using,
# so the canvas and everything that was coloured to sit ON that canvas have to
# follow the mode.  There are two kinds of colour here:
#
#   * canvas furniture (paper/plot background, gridlines, hover label, legend
#     plate) -- never edited in the Layout/Typography tabs, so the theme
#     simply owns it;
#   * text -- the Typography tab has swatches for these, so the theme only
#     rewrites values it recognises as the DARK DEFAULTS (the hexes the
#     analysis runners hard-code).  A colour the user picked by hand is not in
#     that table and is passed through untouched.
CHART_THEME = {
    "dark": {
        "canvas": "#0f172a",
        "text": "#e2e8f0",
        # Fallback for text the figure never coloured itself (an unset
        # colorbar tickfont/title falls back to Plotly's own default, which
        # is a mid grey that reads as "washed out" on the dark canvas and is
        # invisible on the light one when it is the wrong way round).
        "tick_text": "#94a3b8",
        "grid": "rgba(148, 163, 184, 0.18)",
        "hover_bg": "rgba(18, 26, 45, 0.96)",
        "hover_border": "rgba(133, 102, 252, 0.30)",
        "hover_text": "#f5f7ff",
        "legend_bg": "rgba(15, 23, 42, 0.72)",
        "legend_border": "rgba(148, 163, 184, 0.35)",
    },
    "light": {
        "canvas": "#ffffff",
        "text": "#334155",
        "tick_text": "#475569",
        "grid": "rgba(71, 85, 105, 0.20)",
        "hover_bg": "rgba(255, 255, 255, 0.97)",
        "hover_border": "rgba(79, 70, 229, 0.35)",
        "hover_text": "#0f172a",
        "legend_bg": "rgba(255, 255, 255, 0.88)",
        "legend_border": "rgba(100, 116, 139, 0.45)",
    },
}

# Dark-mode default text colours -> their light-mode equivalents.  Matched
# case-insensitively on the exact string, so anything not listed here (i.e. a
# colour chosen from the Typography swatches) survives the switch untouched.
_LIGHT_TEXT_REMAP = {
    "#e2e8f0": "#334155",
    "#cbd5e1": "#334155",
    "#94a3b8": "#475569",
    "#64748b": "#475569",
    "#f5f7ff": "#0f172a",
    "#ffffff": "#1e293b",
    "white": "#1e293b",
}


def theme_key(mode=None):
    """"dark" unless the caller explicitly asks for light."""
    return "light" if str(mode or "dark").strip().lower() == "light" else "dark"


def chart_canvas_color(mode=None):
    """Colour the plot page paints behind the figure (see PlotlyWebView)."""
    return CHART_THEME[theme_key(mode)]["canvas"]


def _themed_text(value, key):
    """Dark default -> light equivalent; user-picked colours unchanged."""
    if key != "light" or value is None:
        return value
    return _LIGHT_TEXT_REMAP.get(str(value).strip().lower(), value)


def _theme_one_colorbar(cb, key, tok):
    """Re-tint a colour bar's ticks/title and its frame.

    Both the tick labels and the colour-bar title are drawn on the canvas, so
    they have to follow the mode exactly like the axis ticks do. Two cases:

      * an EXPLICIT colour -- rewritten when it is one of the dark defaults
        (a colour the user picked in the Typography tab is left alone);
      * NO colour at all -- Plotly then falls back to its own template grey,
        which is a mid tone that is illegible on the dark canvas and washes
        out on the light one. The mode's ``tick_text`` is written instead.
    """
    try:
        ttf = dict(cb.tickfont.to_plotly_json() or {})
        new = _themed_text(ttf.get("color"), key) or tok["tick_text"]
        if new and new != ttf.get("color"):
            ttf["color"] = new
            cb.tickfont = ttf
    except Exception:
        pass
    try:
        ctf = dict(cb.title.font.to_plotly_json() or {})
        new = _themed_text(ctf.get("color"), key) or tok["tick_text"]
        if new and new != ctf.get("color"):
            ctf["color"] = new
            cb.title = {"font": ctf}
    except Exception:
        pass
    try:
        if int(cb.borderwidth or 0) > 0:
            cb.bordercolor = tok["legend_border"]
    except Exception:
        pass


def theme_figure(fig, mode=None):
    """Return a COPY of *fig* re-coloured for the given app mode.

    Called at RENDER time (not at generation time), so the figure stored in
    the session/export path keeps its own colours and a dark<->light switch
    only has to re-push the same figure through ``Plotly.react``.

    ``fig`` is never mutated -- the caller keeps owning the original.
    """
    key = theme_key(mode)
    tok = CHART_THEME[key]
    f2 = copy.deepcopy(fig)

    # -- Canvas -----------------------------------------------------------
    # The runners hand plotly a fully transparent canvas (rgba(0,0,0,0)) and
    # let the page colour show through.  That page colour used to be hard-
    # coded dark, which is exactly why a light-mode chart still looked dark;
    # painting the canvas here makes the figure self-contained.
    try:
        f2.update_layout(paper_bgcolor=tok["canvas"], plot_bgcolor=tok["canvas"])
    except Exception:
        pass

    # -- Global + title text ---------------------------------------------
    try:
        cur = (f2.layout.font.to_plotly_json() or {}).get("color")
        f2.update_layout(font=dict(color=_themed_text(cur, key) or tok["text"]))
    except Exception:
        pass
    try:
        tfont = dict(f2.layout.title.font.to_plotly_json() or {})
        tfont["color"] = _themed_text(tfont.get("color"), key) or tok["text"]
        f2.update_layout(title=dict(font=tfont))
    except Exception:
        pass

    # -- Axes: ticks, titles, gridlines ----------------------------------
    for ax_name in ("xaxis", "yaxis"):
        ax = getattr(f2.layout, ax_name, None)
        if ax is None:
            continue
        upd = {}
        tf = dict(ax.tickfont.to_plotly_json() or {})
        new = _themed_text(tf.get("color"), key)
        if new and new != tf.get("color"):
            tf["color"] = new
            upd["tickfont"] = tf
        atf = dict(ax.title.font.to_plotly_json() or {})
        new = _themed_text(atf.get("color"), key)
        if new and new != atf.get("color"):
            atf["color"] = new
            upd["title"] = {"font": atf}
        # None means "whatever the template says" (i.e. drawn), so only an
        # explicit False (grid switched off) skips the re-tint.
        if ax.showgrid is not False:
            upd["gridcolor"] = tok["grid"]
        if ax.zeroline:
            upd["zerolinecolor"] = tok["grid"]
        if upd:
            try:
                f2.update_layout(**{ax_name: upd})
            except Exception:
                pass

    # -- Hover label ------------------------------------------------------
    try:
        hfont = dict(f2.layout.hoverlabel.font.to_plotly_json() or {})
        hfont["color"] = tok["hover_text"]
        f2.update_layout(hoverlabel=dict(
            bgcolor=tok["hover_bg"],
            bordercolor=tok["hover_border"],
            font=hfont,
        ))
    except Exception:
        pass

    # -- Legend plate + legend text (frame re-tinted only when one is drawn) -
    try:
        leg = dict(f2.layout.legend.to_plotly_json() or {})
        leg["bgcolor"] = tok["legend_bg"]
        if int(leg.get("borderwidth") or 0) > 0:
            leg["bordercolor"] = tok["legend_border"]
        # Legend item/legend title text sits ON the plate, so it has to follow
        # the mode too -- it was the one piece of legend furniture the theme
        # never touched, which is why light mode showed near-white labels
        # ("mean"/"min"/"max") on a white plate. Same rule as the axes: a
        # known dark default is remapped, a user-picked colour is not.
        lf = leg.get("font")
        if isinstance(lf, dict):
            new = _themed_text(lf.get("color"), key)
            if new and new != lf.get("color"):
                lf = dict(lf)
                lf["color"] = new
                leg["font"] = lf
        ltitle = leg.get("title")
        if isinstance(ltitle, dict):
            ltf = ltitle.get("font")
            if isinstance(ltf, dict):
                new = _themed_text(ltf.get("color"), key)
                if new and new != ltf.get("color"):
                    ltitle = dict(ltitle)
                    ltf = dict(ltf)
                    ltf["color"] = new
                    ltitle["font"] = ltf
                    leg["title"] = ltitle
        f2.update_layout(legend=leg)
    except Exception:
        pass

    # -- Trace text: value labels + colour bars ---------------------------
    for tr in getattr(f2, "data", []) or []:
        try:
            tf = getattr(tr, "textfont", None)
            new = _themed_text(tf.color if tf is not None else None, key)
            if tf is not None and new is not None and new != tf.color:
                tr.textfont.color = new
        except Exception:
            pass
        try:
            cb = getattr(tr, "colorbar", None)
            if cb is not None:
                _theme_one_colorbar(cb, key, tok)
        except Exception:
            pass
    try:
        ca = getattr(f2.layout, "coloraxis", None)
        if ca is not None and ca.colorbar is not None:
            _theme_one_colorbar(ca.colorbar, key, tok)
    except Exception:
        pass
    return f2
