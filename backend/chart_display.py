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
LEGEND_POSITIONS = ["Top Right", "Top Left", "Top Center", "Bottom Right", "Bottom Left", "Bottom Center", "Left", "Right"]
LEGEND_ORIENTATIONS = ["Auto", "Vertical", "Horizontal"]
LEGEND_ORDERS = ["Normal", "Reversed"]
COLORBAR_POSITIONS = ["Right", "Left", "Top", "Bottom"]

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
    # name          x     y    xanchor  yanchor  x-strip   edge
    "Top Right":    (1.0, 1.0, "right",  "top",    "right",  "top"),
    "Top Left":     (0.0, 1.0, "left",   "top",    "left",   "top"),
    "Top Center":   (0.5, 1.0, "center", "top",    "",       "top"),
    "Bottom Right": (1.0, 0.0, "right",  "bottom", "right",  "bottom"),
    "Bottom Left":  (0.0, 0.0, "left",   "bottom", "left",   "bottom"),
    "Bottom Center":(0.5, 0.0, "center", "bottom", "",       "bottom"),
    # Explicit side docks. These keep a reserved COLUMN on their own side; the
    # four corner names can also collapse to a horizontal band when the card is
    # too narrow for a column (see ``plan_legend_layout``).
    "Right":        (1.0, 0.5, "right",  "middle", "right",  "middle"),
    "Left":         (0.0, 0.5, "left",   "middle", "left",   "middle"),
}

# Fraction of the plot width handed to the legend column.  Proportional so
# it scales with the card instead of being a fixed slab on small charts.
LEGEND_STRIP = 0.26
LEGEND_STRIP_MIN = 0.20
LEGEND_STRIP_MAX = 0.50
# Extra outer margin on the legend's side, so the y-axis title has somewhere
# to live that is NOT the legend's column.
LEGEND_AXIS_MARGIN = 72
# The reserved column has to hold the legend box AND, on the left, the y-axis
# tick labels -- those are drawn just outside the axis, i.e. at the RIGHT edge
# of the column, so a column sized for the box alone leaves the labels under
# the plate (measured at 700px: ~130 label pixels dimmed by the plate).
#   chrome  = plate padding + swatch + legend title
#   char    = per-character width of a legend item (11px Inter, rounded up)
#   axis    = room for the tick labels that share the column.  Measured from the
#             figure by ``_axis_label_need_px`` (see ``reserve_legend_column``);
#             this is only the fallback for callers that pass no figure.
_LEGEND_CHAR_PX = 8
_LEGEND_CHROME_PX = 44
_LEGEND_AXIS_PX = 30
# Gap kept between the legend plate and the axis furniture beside it.  The tick
# labels are measured (see ``_axis_label_need_px``) but the plate has a border
# and its own padding, so without a separating gap the two still touch.
_LEGEND_AXIS_GAP_PX = 8
# Plot width the estimate is calibrated against.  Deliberately smaller than a
# typical card so the estimate errs on the generous side at small widths; the
# strip is a fraction, so this only has to be the right order of magnitude.
_LEGEND_REF_WIDTH = 300

# ---------------------------------------------------------------------------
# The compact / dashboard rule: a vertical legend column stops being the right
# answer once the card is narrow.
#
# A dashboard packs 2-3 charts per row and every card auto-sizes to the number
# of visuals on screen, so a chart can easily be handed a 380-700px card. On the
# Categorical dual-axis figure the legend column needs ~260px of that (long
# series names + the y tick labels + the rotated y-axis title, all sharing the
# strip). That is 35-70% of the plot area: the bars get crushed and the legend
# still sits on top of the axis. This is the *position not dynamically adjusted
# as per viewpoint area* report -- the layout was sized for a full-width preview
# pane and then carried, unchanged, into a compact grid tile.
#
# So the orientation is now CHOSEN from the pixels available rather than fixed:
# below this share of the plot area a side column can take, the legend switches
# to a horizontal band across the top/bottom edge (which reserves HEIGHT, not
# width, so the plot keeps its full width and stays readable). Above it the
# vertical column is kept, because there is room and it is what the user picked
# on a wide card.
LEGEND_COLUMN_MAX_SHARE = 0.34
# A horizontal legend row, and the gap under it. One row of 11px items is ~20px
# tall; the constant is deliberately generous so a wrapped second row still
# fits inside the band that was reserved for it.
_LEGEND_ROW_PX = 22
_LEGEND_BAND_GAP_PX = 10
# Cap on how much vertical space a horizontal band may claim. Past this the
# plot area stops being worth protecting, so the column is used instead.
LEGEND_BAND_MAX_SHARE = 0.30


def _legend_need_px(names, legend_title="", side="left", axis_px=None):
    """Pixels the reserved legend column actually has to be, for these labels.

    This is the real measurement the fraction below is derived from: plate
    padding + swatch + the longest label actually drawn, plus room for the
    axis tick labels that share the column.

    ``axis_px`` is that tick-label allowance, measured from the figure by the
    caller.  It is a parameter rather than being recomputed here because this
    function only sees label strings, not the axes they are competing with.
    When it is not supplied the flat fallback constants apply.
    """
    longest = 0
    for n in list(names or []) + ([legend_title] if legend_title else []):
        longest = max(longest, len(str(n)))
    need = _LEGEND_CHROME_PX + _LEGEND_CHAR_PX * longest
    if axis_px is None:
        # No figure to measure: keep the historic allowance on the left, and
        # still give the right a gap so a secondary axis is never painted over.
        need += _LEGEND_AXIS_PX if side == "left" else _LEGEND_AXIS_GAP_PX
    else:
        need += max(0, int(axis_px))
    return need


def _legend_strip(names, legend_title="", side="left", width_px=None,
                  axis_px=None):
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
    need = _legend_need_px(names, legend_title, side, axis_px)
    if width_px and width_px > 0:
        # Never wider than the label needs, never so thin the box cannot fit.
        frac = need / float(width_px)
        return max(0.0, min(LEGEND_STRIP_MAX, frac))
    return max(LEGEND_STRIP_MIN,
               min(LEGEND_STRIP_MAX, need / float(_LEGEND_REF_WIDTH)))


def _legend_axis_title_px(fig, side):
    """Pixels the rotated y-axis TITLE takes on *side*, or 0 when it has none.

    The tick labels are not the only axis furniture in the strip. Plotly also
    draws the (rotated) axis title outboard of them, and with no `automargin`
    that title sits in the same reserved column -- so a column sized for the
    box + ticks still had the title painted under the legend plate. A 16px
    title is ~22px of horizontal thickness.
    """
    try:
        name = "yaxis2" if side == "right" else "yaxis"
        ax = getattr(fig.layout, name, None)
        if ax is None:
            return 0
        title = getattr(ax, "title", None)
        if title is None or not str(getattr(title, "text", "") or "").strip():
            return 0
        size = 16
        try:
            size = int(getattr(title.font, "size", None) or 16)
        except Exception:
            pass
        # Rotated 90deg, so it is the title's THICKNESS (not its length) that
        # occupies the column: ~1.35em plus Plotly's default standoff.
        return int(round(size * 1.35)) + 5
    except Exception:
        return 0


def legend_anchor(legend_position):
    """Return the full placement dict for a legend position, or None.

    ``{"x", "y", "xanchor", "yanchor", "side", "edge"}``:

    * ``side`` is the x-strip to reserve out of ``xaxis.domain`` ("left",
      "right", or "" for the centred Top/Bottom positions, which reserve no
      side strip at all), so callers only need it to lay out the rest of the
      figure.
    * ``edge`` is "top" / "bottom" / "middle" -- which vertical band (if any) a
      horizontal legend has to be given.
    """
    row = _LEGEND_ANCHORS.get(str(legend_position or "").strip())
    if not row:
        return None
    x, y, xanchor, yanchor, side, edge = row
    return {"x": x, "y": y, "xanchor": xanchor, "yanchor": yanchor,
            "side": side, "edge": edge}


# Nominal height a reserved band is expressed against, when converting the
# measured pixel need into a ``yaxis.domain`` fraction. Only the ratio matters
# (the band is a share of the plot height), so this just has to be a plausible
# card height.
_LEGEND_BAND_REF_HEIGHT = 400


def _band_plan(fig, names, legend_title, edge, side, axis_px):
    """Describe the horizontal-band layout for a legend.

    Band height follows the ITEM COUNT -- the items now sit side by side, so a
    long series name costs width, not height -- plus a row for the legend
    title when one is set. ``edge`` is "top" or "bottom"; "middle" (the Left /
    Right docks) has no band to reserve and falls back to a plain column.
    """
    rows = max(1, len([n for n in (names or []) if str(n or "").strip()]))
    band_px = rows * _LEGEND_ROW_PX + _LEGEND_BAND_GAP_PX
    if legend_title:
        band_px += _LEGEND_ROW_PX
    return {"mode": "band", "side": side, "top": edge == "top",
            "band_px": band_px, "rows": rows,
            "need_px": _legend_need_px(names, legend_title, side or "right",
                                       axis_px)}


def plan_legend_layout(fig, legend_position, names=None, legend_title="",
                       width_px=None):
    """Decide HOW the legend is laid out for the pixels this card really has.

    Returns a dict describing the plan, or None when the position is unknown:

        {"mode": "column", "side": ..., "need_px": int, "strip": float}
        {"mode": "band", "side": ..., "top": bool, "band_px": int,
         "rows": int, "need_px": int}

    "column" is the historical behaviour -- a vertical strip carved out of
    ``xaxis.domain``. "band" is the compact fallback -- a horizontal legend in
    a reserved strip along the top/bottom edge, costing the plot HEIGHT rather
    than width.

    The choice is made from the measured pixel need against the plot area that
    will actually exist, so one figure lays out as a column on a wide preview
    pane and as a band on a compact dashboard tile. That is what "position
    should adjust as per the viewport area" asks for. With no ``width_px``
    there is nothing to adapt to, so the column is kept and every headless
    caller (export, background generation) keeps its current output.
    """
    place = legend_anchor(legend_position)
    if not place:
        return None
    side = place["side"]
    edge = place["edge"]
    if not side:
        # A centred Top/Bottom legend has no side strip to reserve; it always
        # uses a horizontal band so the plot keeps its full width.
        return _band_plan(fig, names, legend_title, edge, side, None)
    try:
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    key = "l" if side == "left" else "r"
    # Measure against the margins that WILL exist, i.e. after the legend's own
    # side is widened -- otherwise the plot area is over-estimated and the
    # column comes out short by the margin.
    probe = dict(m)
    probe[key] = max(int(probe.get(key) or 0), int(LEGEND_AXIS_MARGIN))
    area = _plot_area_width(probe, width_px)
    axis_px = _legend_axis_need_px(fig, side)
    need = _legend_need_px(names, legend_title, side, axis_px)

    w = float(width_px or 0)
    if w <= 0 or area <= 0:
        # No width to measure against. For a cartesian chart the historic fixed
        # fraction is fine. For a pie/treemap it is not: the card width in the
        # report is decided by CSS at RENDER time and is unknowable here, so any
        # fraction can come out too small and put the legend back over the marks.
        # Over-reserving only wastes some width; under-reserving is the reported
        # bug, so these figures take the safe floor.
        strip = (LEGEND_STRIP_MAX if _domain_traces(fig)
                 else _legend_strip(names, legend_title, side, None,
                                    axis_px=axis_px))
        return {"mode": "column", "side": side, "need_px": need,
                "axis_px": axis_px, "strip": strip}

    if need / area <= LEGEND_COLUMN_MAX_SHARE:
        return {"mode": "column", "side": side, "need_px": need,
                "axis_px": axis_px,
                "strip": _legend_strip(names, legend_title, side, area,
                                       axis_px=axis_px)}

    if edge == "middle":
        # A Left/Right dock has no top/bottom band to collapse into, so the
        # column is kept and simply capped -- the plot must not be crushed to
        # nothing by an over-long legend on a narrow card.
        return {"mode": "column", "side": side, "need_px": need,
                "axis_px": axis_px,
                "strip": max(_legend_strip(names, legend_title, side, area,
                                           axis_px=axis_px),
                             LEGEND_COLUMN_MAX_SHARE)}

    # Too wide for a column: go horizontal, keeping the requested edge and the
    # requested left/right alignment of the row.
    return _band_plan(fig, names, legend_title, edge, side, axis_px)


def reserve_legend_band(fig, legend_position, band_px):
    """Reserve a horizontal legend band along the top or bottom edge.

    The mirror image of ``reserve_legend_column``: instead of shrinking
    ``xaxis.domain`` it shrinks ``yaxis.domain``, so the plot keeps its FULL
    width and gives up only the height the legend rows actually need. That is
    what makes a compact dashboard tile readable -- a side column on a 450px
    card leaves the bars ~150px, while a band costs ~30px of height.

    The legend's own anchor is written here too (``orientation="h"`` and the
    matching x/y/anchor), because in this mode it is a genuinely different
    placement rather than just a different reserved strip.

    Returns True when the layout was rewritten.
    """
    place = legend_anchor(legend_position)
    if not place:
        return False
    side = place["side"]
    top = place["edge"] == "top"
    try:
        band_px = max(0, int(band_px or 0))
    except Exception:
        band_px = 0
    if band_px <= 0:
        return False
    # Never claim so much height that the plot stops being a plot.
    band_px = min(band_px, int(LEGEND_BAND_MAX_SHARE * _LEGEND_BAND_REF_HEIGHT))
    frac = band_px / float(_LEGEND_BAND_REF_HEIGHT)
    try:
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    if top:
        m["t"] = max(int(m.get("t") or 0), band_px)
        domain = [frac, 1.0]
    else:
        m["b"] = max(int(m.get("b") or 0), band_px)
        domain = [0.0, 1.0 - frac]
    try:
        fig.update_layout(margin=m, yaxis=dict(domain=domain))
        # A secondary y axis created by make_subplots OVERLAYS 'y' and simply
        # follows the primary domain, so writing it would be rejected as
        # ignored; only a real, independently-placed yaxis2 needs the write.
        ax2 = getattr(fig.layout, "yaxis2", None)
        if ax2 is not None and not getattr(ax2, "overlaying", None):
            fig.update_layout(yaxis2=dict(domain=domain))
    except Exception:
        return False
    # Same gap as the column reservation: a pie/donut/treemap has no yaxis, so
    # ``yaxis.domain`` moves nothing and the horizontal band is reserved in
    # name only. This is the centred-position case ("Top Center" / "Bottom
    # Center"), which is where a dashboard pie actually lands -- the row is
    # drawn across the middle-bottom of the card and sits on the pie. Squeeze
    # the trace's own y domain so the band is genuinely free.
    for tr in _domain_traces(fig):
        try:
            tr.domain.y = list(domain)
        except Exception:
            log.debug("could not lift a trace domain for the legend band",
                      exc_info=True)
    try:
        fig.update_layout(legend=dict(
            orientation="h",
            # A centred position keeps the row centred; otherwise the row is
            # aligned to the requested side so "Top Left" / "Bottom Right" still
            # mean what they say once they collapse to a band.
            x=0.5 if place["xanchor"] == "center"
            else (0.0 if side == "left" else 1.0),
            y=1.0 if top else 0.0,
            xanchor=place["xanchor"] if place["xanchor"] == "center"
            else ("left" if side == "left" else "right"),
            yanchor="bottom" if top else "top",
        ))
    except Exception:
        pass
    return True


def _plot_area_width(margin, width_px):
    """Width an ``xaxis.domain`` fraction actually spans, in pixels.

    ``layout.xaxis.domain`` is a fraction of the plot AREA -- the card minus
    the left/right outer margins -- not of the full card.  Resolving a pixel
    need against the card width therefore under-reserves by exactly the
    margins, which is why a legend sized "correctly" still overlapped the axis
    labels: the strip came out ~40px short on a typical card.

    Returns the card width unchanged when no width is known.
    """
    try:
        w = float(width_px or 0)
        if w <= 0:
            return width_px
        l = float(margin.get("l") or 0)
        r = float(margin.get("r") or 0)
        # Never let a large margin invert or zero out the area; a card that
        # narrow is degenerate and any sane fraction still beats crashing.
        return max(1.0, w - l - r)
    except Exception:
        return width_px


def _domain_traces(fig) -> list:
    """Traces positioned by their OWN ``domain`` rather than by xaxis/yaxis.

    Pie/donut, treemap, sunburst, funnel and icicle all size themselves from a
    per-trace ``domain`` box and have no cartesian axes at all. For those,
    shrinking ``xaxis.domain`` moves nothing -- which is why the legend
    reservation silently did nothing for them and the legend plate ended up
    painted straight over the marks.
    """
    out = []
    for tr in (getattr(fig, "data", None) or []):
        dom = getattr(tr, "domain", None)
        if dom is None:
            continue
        # A pie's domain is a real range pair; cartesian traces have no
        # per-trace domain at all, so anything that has one is what we want.
        try:
            if getattr(dom, "x", None) is not None:
                out.append(tr)
        except Exception:
            continue
    return out


def reserve_legend_column(fig, legend_position, strip=None,
                          axis_margin=LEGEND_AXIS_MARGIN, names=None,
                          legend_title="", width_px=None, axis_px=None):
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
    side = place["side"]
    if not side:
        # Centred Top/Bottom: nothing to carve out of the x-domain.
        return None
    try:
        # NB: ``dict(fig.layout.margin)`` raises ValueError on some Plotly
        # versions (the Margin object iterates keys, not pairs), which silently
        # discarded the real margins and made the strip resolve against the
        # full card width.  ``to_plotly_json()`` is the supported accessor.
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    # Widen the outer margin on the legend's side FIRST, so the plot area the
    # domain fraction is resolved against is the one that will actually exist.
    if side == "left":
        m["l"] = max(int(m.get("l") or 0), int(axis_margin))
    else:
        m["r"] = max(int(m.get("r") or 0), int(axis_margin))
    if strip is None:
        # The tick labels of the axis on the legend's side are drawn INSIDE the
        # strip being freed (they sit just outside the plot edge), so the column
        # has to hold them too -- otherwise the plate paints over them.  This is
        # what put the legend on top of "Packaged Chocolate" on the left, and
        # over the secondary y scale of a dual-axis chart on the right.
        #
        # The fraction is resolved against the PLOT AREA, not the card: an
        # ``xaxis.domain`` fraction is a fraction of the width left after the
        # outer margins, so dividing the pixel need by the full card width
        # under-reserves by exactly the margin and leaves the plate overlapping.
        strip = _legend_strip(names, legend_title, side,
                              _plot_area_width(m, width_px),
                              axis_px=(_legend_axis_need_px(fig, side)
                                       if axis_px is None else axis_px))
    if side == "left":
        domain = [float(strip), 1.0]
    else:
        domain = [0.0, 1.0 - float(strip)]

    # Pie / treemap / sunburst / funnel size themselves from a per-TRACE domain
    # and have no xaxis, so writing ``xaxis.domain`` there moves nothing at all
    # -- the reservation used to return "left"/"right" as if it had worked while
    # the legend plate stayed exactly where it was, on top of the marks. Squeeze
    # the trace domain instead, and keep writing the xaxis for the cartesian
    # case so existing charts are unaffected.
    squeezed = 0
    for tr in _domain_traces(fig):
        try:
            dom = tr.domain
            dom.x = list(domain)
            squeezed += 1
        except Exception:
            log.debug("could not squeeze a trace domain for the legend",
                      exc_info=True)
    try:
        fig.update_layout(margin=m, xaxis=dict(domain=domain))
    except Exception:
        return None
    if squeezed:
        # The legend is only guaranteed to sit in the freed strip if it is
        # anchored there. A pie drawn with no explicit anchor gets Plotly's
        # default (inside the plot), so the domain squeeze alone would leave the
        # plate on the pie -- which is the reported overlap.
        try:
            x, xanchor = (0.0, "left") if side == "left" else (1.0, "right")
            y, yanchor = place["y"], place["yanchor"]
            fig.update_layout(legend=dict(x=x, xanchor=xanchor,
                                          y=y, yanchor=yanchor))
        except Exception:
            log.debug("could not anchor the legend for a domain-squeezed figure",
                      exc_info=True)
    return side




def infer_legend_position(fig):
    """Recover the legend position name from a figure that already has one.

    A figure normally carries only the resolved anchors (``x``/``y``/
    ``xanchor``/``yanchor``), not the friendly name the user picked, so the
    name has to be recovered from those anchors to pick the same side when the
    layout is re-fitted later.

    ``apply_display_options`` records the name in ``layout.meta`` when it lays
    the legend out, and that record is the authority. The anchors alone are
    ambiguous once the compact band mode exists: there a TOP band is drawn with
    ``yanchor="bottom"`` (so it grows downward out of the reserved strip), which
    the anchor reading would report as "Bottom ...". Returns the position name,
    or None when the figure has no legend box.
    """
    try:
        meta = fig.layout.meta
        if isinstance(meta, dict):
            pos = str(meta.get("lytrize_legend_position") or "").strip()
            if pos in _LEGEND_ANCHORS:
                return pos
    except Exception:
        pass
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
    # A centred row is unambiguous -- there are only the two centred names.
    if anchor == "center":
        return "Top Center" if yanchor == "top" else "Bottom Center"
    side = "left" if anchor == "left" else "right"
    if yanchor == "top":
        return "Top Left" if side == "left" else "Top Right"
    if yanchor == "bottom":
        return "Bottom Left" if side == "left" else "Bottom Right"
    if yanchor == "middle":
        return "Left" if side == "left" else "Right"
    return None


def legend_layout_mode(fig):
    """Which layout the legend on *fig* was given: "column", "band" or None.

    Recorded by ``reserve_legend_column``/``reserve_legend_band`` so a later
    re-fit knows whether it is re-sizing a column or a band, instead of having
    to re-derive it from the anchors.
    """
    try:
        meta = fig.layout.meta
        if isinstance(meta, dict):
            mode = str(meta.get("lytrize_legend_mode") or "").strip()
            if mode in ("column", "band"):
                return mode
    except Exception:
        pass
    return None


def _record_legend_layout(fig, position, mode):
    """Remember the position/mode on the figure so a refit can reproduce it."""
    try:
        meta = fig.layout.meta
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["lytrize_legend_position"] = str(position or "")
        meta["lytrize_legend_mode"] = str(mode or "")
        fig.update_layout(meta=meta)
    except Exception:
        log.debug("could not record the legend layout on the figure",
                  exc_info=True)


def _restore_legend_reservation(fig):
    """Undo the previous legend reservation, returning the figure to baseline.

    The layout must be recalculated from the figure's ORIGINAL margins and
    domains, never from the already-reserved ones, or every pass compounds:
    a column would grow a little further each resize and a band would never be
    released when the card grew back. This restores a full-width domain on both
    axes and puts the legend back to the vertical orientation, which is the
    baseline both modes are measured against.
    """
    try:
        fig.update_layout(xaxis=dict(domain=[0.0, 1.0]),
                          yaxis=dict(domain=[0.0, 1.0]))
        ax2 = getattr(fig.layout, "yaxis2", None)
        if ax2 is not None and not getattr(ax2, "overlaying", None):
            fig.update_layout(yaxis2=dict(domain=[0.0, 1.0]))
    except Exception:
        log.debug("could not reset the legend reservation", exc_info=True)
    # Undo any per-trace squeeze from reserve_legend_column as well. Leaving the
    # pie's domain where the last pass put it means the next pass measures
    # against an already-reserved trace and shrinks it again, so a resize storm
    # would march the pie off the card.
    for tr in _domain_traces(fig):
        try:
            tr.domain.x = [0.0, 1.0]
            tr.domain.y = [0.0, 1.0]
        except Exception:
            log.debug("could not reset a trace domain", exc_info=True)
    try:
        if legend_layout_mode(fig) == "band":
            fig.update_layout(legend=dict(orientation="v"))
    except Exception:
        pass


def apply_legend_layout(fig, legend_position, names=None, legend_title="",
                        width_px=None):
    """Reserve space for the legend, choosing column or band from the pixels.

    The single entry point both ``apply_display_options`` (no view width yet)
    and ``refit_legend_width`` (the widget's real width) go through, so the two
    always agree on the rules.

    With no ``width_px`` this is the classic column reservation, which is what
    every headless/export path keeps. With a width it also switches to the
    compact horizontal band when a column would take more than
    ``LEGEND_COLUMN_MAX_SHARE`` of the plot area.

    Returns the plan dict (or False) so the caller can tell whether the band
    mode was chosen -- the band writes its own x/y/anchors, which the caller's
    column anchors would otherwise fight.
    """
    if fig is None:
        return False
    _restore_legend_reservation(fig)
    plan = plan_legend_layout(fig, legend_position, names=names,
                              legend_title=legend_title, width_px=width_px)
    if not plan:
        return False
    if plan.get("mode") == "band":
        ok = reserve_legend_band(fig, legend_position, plan.get("band_px"))
    else:
        ok = reserve_legend_column(
            fig, legend_position, names=names, legend_title=legend_title,
            width_px=width_px,
            strip=plan.get("strip"),
            axis_px=plan.get("axis_px"),
        )
    if ok:
        _record_legend_layout(fig, legend_position, plan.get("mode"))
        return plan
    return False


def refit_legend_width(fig, width_px):
    """Re-lay the legend out against the real view width.

    Figures are built on a worker thread with no view, so the first reservation
    has to assume a card width. Once the widget is on screen its true width is
    known, and the legend can be sized against the pixels the labels actually
    need -- that is what removes the wide gap between the plot and the legend on
    a full-width chart -- and can also switch to the compact band layout when
    the card is too narrow for a column at all.

    Idempotent and reversible: the previous reservation is cleared first, so
    repeated calls (a resize storm, a re-gridded dashboard, a full-width
    toggle) converge on the layout the current width calls for instead of
    compounding strips or leaving a stale band behind.

    Returns True when the layout was rewritten. A figure with no legend box is
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
    return bool(apply_legend_layout(fig, pos, names=names,
                                    legend_title=title,
                                    width_px=width_px))


# Colours shared by the legend plate and the colour-bar frame when their
# border is switched on (theme_figure() re-tints them per mode).
LEGEND_BORDER_COLOR = "rgba(148, 163, 184, 0.35)"


# Trace types whose colour bar is drawn BY DEFAULT, i.e. plotly renders one
# even when the figure's JSON never mentions "colorbar". Verified by rendering:
# heatmap, contour, surface, mesh3d, histogram2d and choropleth all show a
# colour ramp out of the box. Everything else (bar, scatter, line, pie, table)
# shows one only when asked to.
_COLORBAR_TRACE_TYPES = {
    "heatmap", "heatmapgl", "contour", "surface", "mesh3d",
    "histogram2d", "histogram2dcontour", "choropleth",
}


def _shows_scale(container_json: dict) -> bool | None:
    """``showscale`` as the figure states it, or None when unstated."""
    value = container_json.get("showscale")
    return None if value is None else bool(value)


def real_colorbars(fig):
    """The colour bars *fig* genuinely draws -- and nothing else.

    Why this exists
    ---------------
    Plotly builds a fresh default sub-object for ANY container you read with
    ``getattr``: ``trace.marker.colorbar`` and ``layout.coloraxis.colorbar``
    both hand back a live ``ColorBar`` even when the figure never declared one.
    Reading is harmless, but the moment anything is ASSIGNED to that object it
    is written into the figure's JSON -- and ``colorbar`` is one of the keys
    ``plotly.js`` treats as "this chart HAS a colour bar" (its ``hasColorbar``
    is literally ``isPlainObject(t.colorbar)``).

    So a styling helper that does the natural-looking
    ``cb = getattr(holder, "colorbar", None); cb.tickfont = ...`` silently
    ADDS a colour bar to every chart it touches. A plain bar chart then grew a
    full colour ramp with a bogus title and tick labels down its right-hand
    side -- the "ghost colour bar" reported on the dashboard.

    A colour bar counts as real when plotly would actually draw one:

    * trace-level (heatmap / contour / surface / mesh3d / histogram2d /
      choropleth) -- these default to showing a scale, so they count unless
      ``showscale`` was explicitly turned off;
    * marker-level (scatter / geo) -- the default is OFF, so these count only
      when ``showscale`` is explicitly on or a ``colorbar`` was declared;
    * the shared ``layout.coloraxis`` -- only when a trace actually binds
      itself to it, which is what ``px.imshow`` and ``px.scatter`` with a
      continuous colour both do (the latter via ``marker.coloraxis``).

    Yields the real ``ColorBar`` objects so callers can style them exactly as
    before; an empty iterator simply means "this figure draws no colour bar",
    which is the normal case for bar, line, pie and table charts.
    """
    bound_to_coloraxis = False

    for tr in (getattr(fig, "data", None) or []):
        try:
            tr_json = tr.to_plotly_json() or {}
        except Exception:
            continue
        ttype = str(tr_json.get("type") or "").lower()

        marker = getattr(tr, "marker", None)
        try:
            m_json = marker.to_plotly_json() or {} if marker is not None else {}
        except Exception:
            m_json = {}

        # A trace can bind itself to the shared colour axis EITHER at trace
        # level (``coloraxis="coloraxis"``) or, for scatter/bubble, on its
        # MARKER (``marker.coloraxis``) -- which is what ``px.scatter`` with a
        # continuous colour emits. Missing the marker case made px bubbles lose
        # their colour bar, so both are checked here.
        bound = bool(tr_json.get("coloraxis")) or bool(m_json.get("coloraxis"))
        if bound:
            bound_to_coloraxis = True

        declared = isinstance(tr_json.get("colorbar"), dict)
        showscale = _shows_scale(tr_json)
        # A trace bound to the shared colour axis (what px does) draws its scale
        # from THAT axis only -- its own colorbar is ignored -- so it must not be
        # yielded here or the bar would be styled twice.
        trace_level = not bound and (
            declared
            or (showscale is not False and ttype in _COLORBAR_TRACE_TYPES))
        if trace_level:
            try:
                yield tr.colorbar
            except Exception:
                pass

        if marker is not None:
            # Marker-level default is OFF, so only an explicit ask counts --
            # and a marker bound to the shared axis is that axis's business.
            if (not bound
                    and (isinstance(m_json.get("colorbar"), dict)
                         or _shows_scale(m_json) is True)):
                try:
                    yield marker.colorbar
                except Exception:
                    pass

    # The shared colour axis, but only while something is bound to it --
    # ``layout.coloraxis`` otherwise always "exists" and styling it would
    # conjure a colour bar onto charts that never had one.
    if bound_to_coloraxis:
        try:
            ca = fig.layout.coloraxis
            if (ca.to_plotly_json() or {}).get("showscale") is not False:
                yield ca.colorbar
        except Exception:
            pass


def colorbar_json(fig) -> list:
    """``real_colorbars`` as plain dicts -- for tests and diagnostics."""
    out = []
    for cb in real_colorbars(fig):
        try:
            out.append(cb.to_plotly_json() or {})
        except Exception:
            out.append({})
    return out


def _remap_discrete_map_colours(fig, cs) -> None:
    """Repaint a DISCRETE map's markers from the chosen colour scale.

    Bug: "Colorscale not working" on a map.

    A ``scattermap`` paints a continuous measure through the shared
    ``layout.coloraxis``, and ``update_coloraxes(colorscale=...)`` below reaches
    that.  But when the map's Colour column is binary or categorical, the runner
    deliberately emits FLAT palette colours instead (``color_discrete_map`` /
    ``color_discrete_sequence``) and binds nothing to the colour axis -- and
    ``px`` then splits the map into ONE TRACE PER CATEGORY, each carrying a single
    flat colour.  There is therefore no colour axis to write to and no per-trace
    colour array to remap, so the Colorscale dropdown silently did nothing at all:
    exactly the reported symptom, and the reason the control looked connected while
    repainting the same colours.

    The fix keeps the discrete ENCODING (one flat colour per category, so the
    legend stays a legend) but derives those flat colours FROM the selected scale
    instead of from the app palette.  Categories are matched to scale stops in
    first-appearance order, which is stable and idempotent, so picking a scale
    visibly repaints the map and switching back switches back.

    No-ops on continuous traces (their colour is bound to the shared axis, which
    the block below owns) and on any chart without a discrete map trace, so it can
    never damage a figure it does not understand.
    """
    if not cs:
        return
    try:
        from plotly.colors import sample_colorscale

        map_traces = [tr for tr in (getattr(fig, "data", None) or [])
                      if str(getattr(tr, "type", "")).lower()
                      in ("scattermap", "scattermapbox")]
        if not map_traces:
            return

        # Distinct flat colours across the whole map, in first-appearance order.
        # Every discrete map trace px emits is a candidate; a continuous one is
        # skipped and left entirely to ``layout.coloraxis``.
        distinct: list = []
        for tr in map_traces:
            marker = getattr(tr, "marker", None)
            if marker is None or getattr(marker, "coloraxis", None):
                continue  # continuous -> owned by the shared colour axis
            colors = marker.to_plotly_json().get("color")
            if isinstance(colors, str):
                colors = [colors]
            if not isinstance(colors, (list, tuple)):
                continue
            for c in colors:
                # Only real colours are candidates; a numeric array is not
                # something a discrete scale can be sampled onto.
                if isinstance(c, str) and c and c not in distinct:
                    distinct.append(c)
        if len(distinct) < 2:
            # Nothing discrete to restyle (a single category cannot show a scale).
            return

        mapping = dict(zip(distinct,
                           sample_colorscale(str(cs), len(distinct))))
        for tr in map_traces:
            marker = getattr(tr, "marker", None)
            if marker is None or getattr(marker, "coloraxis", None):
                continue
            colors = marker.to_plotly_json().get("color")
            if isinstance(colors, str):
                marker.color = mapping.get(colors, colors)
            elif isinstance(colors, (list, tuple)):
                marker.color = [mapping.get(c, c) if isinstance(c, str) else c
                                for c in colors]
    except Exception:
        # A colour remap is cosmetic; never let it take a chart down.
        pass


def _apply_heatmap_options(fig, lo, typo, width_px=None):
    """Apply the heatmap / colour-bar Layout options to *fig* in place.

    Only heatmap and choropleth traces are touched. The behaviour mirrors the
    old app's ``chart_settings.py``: the colour scale and the cell annotations
    are per-trace, the colour-bar title/sizes/colours go through
    ``update_coloraxes`` because ``px.imshow`` puts them on a shared colour
    axis rather than on the trace.

    Annotation text is turned OFF by clearing both ``text`` and
    ``texttemplate`` (not just one), which is what actually removes the numbers
    from a heatmap; a trace left with a stale ``texttemplate`` keeps drawing
    them.
    """
    scales = ["heatmap", "choropleth", "scattermap", "scattermapbox"]
    traces = [tr for tr in getattr(fig, "data", []) or []
              if str(getattr(tr, "type", "")).lower() in scales]
    if not traces:
        return

    # A map scatter (scattermap / scatter_mapbox) is colour-driven through the
    # SHARED colour axis, not through a trace-level ``colorscale`` -- px binds
    # it via ``marker.coloraxis``.  So the per-trace block below must skip map
    # traces: ``tr.coloraxis = None`` would detach the colour bar entirely and
    # ``tr.colorscale = ...`` is rejected outright by Plotly for these traces
    # (verified: ValueError).  They are routed through ``update_coloraxes``
    # below instead, which is what actually repaints them.
    map_traces = {"scattermap", "scattermapbox"}

    # -- Per-trace: colour scale + in-cell annotations ---------------------
    cs = lo.get("heatmap_colorscale")

    # A DISCRETE map (binary / categorical colour column) is px-split into one
    # trace per category with a flat colour and no colour axis, so neither the
    # per-trace writes below nor ``update_coloraxes`` can reach it -- which is
    # why the Colorscale control did nothing there.  Repaint its flat colours
    # from the chosen scale first, figure-wide (the categories are spread over
    # several traces, so this cannot be done one trace at a time).
    _remap_discrete_map_colours(fig, cs)

    show_text = lo.get("heatmap_show_text", True)
    try:
        prec = max(0, int(round(float(
            lo.get("heatmap_annotation_precision", 2)))))
    except Exception:
        prec = 2
    ann_size = lo.get("heatmap_annotation_size", 10)
    ann_color = lo.get("heatmap_annotation_color", "#ffffff")

    for tr in traces:
        if str(getattr(tr, "type", "")).lower() in map_traces:
            # See the note above: a map scatter carries no per-trace colour
            # scale.  Skip the unbind/annotate writes entirely and let the
            # shared-axis block below style it.
            continue
        if cs:
            try:
                # Detach the trace from the shared colour axis FIRST. A
                # px.imshow heatmap is bound to layout.coloraxis, and that
                # axis carries its own colorscale -- while the trace is bound
                # to the axis, the axis value is what Plotly paints, so
                # writing colorscale on the trace alone had no visible effect
                # and the chart kept the app's brand palette. Unbinding first
                # makes the trace's own colorscale authoritative.
                tr.coloraxis = None
            except Exception:
                pass
            try:
                tr.colorscale = str(cs)
            except Exception:
                pass
        try:
            if not show_text:
                tr.text = None
                tr.texttemplate = None
            else:
                # Replace only the format specifier, so a runner's own
                # hovertemplate/texttemplate structure is preserved.
                tmpl = getattr(tr, "texttemplate", None)
                if isinstance(tmpl, str) and "%{z" in tmpl:
                    import re as _re
                    tr.texttemplate = _re.sub(
                        r"%\{z:[^}]*\}", f"%{{z:.{prec}f}}", tmpl)
                elif isinstance(tmpl, str) and "%{text" in tmpl:
                    # The matrix heatmap does NOT use a %{z} template: the runner
                    # pre-formats every cell into ``text`` (abbreviating to 1.2M
                    # / 3,400) and paints it with "%{text}". Rewriting only a
                    # %{z:..f} specifier therefore never matched, the precision
                    # slider did nothing at all, and it stayed silently dead.
                    # Re-derive the labels from z at the requested precision,
                    # keeping the same abbreviations.
                    rebuilt = _retext_at_precision(
                        getattr(tr, "text", None), getattr(tr, "z", None), prec)
                    if rebuilt is not None:
                        tr.text = rebuilt
        except Exception:
            pass
        try:
            # Always write the font, even when the trace has none yet. On a
            # fresh px.imshow heatmap ``textfont`` is None, so guarding on
            # ``if tf is not None`` skipped the write entirely -- the slider
            # and the colour swatch both looked connected but did nothing,
            # and the annotation kept whatever the generic value-label pass
            # gave it later in this function.
            tf = getattr(tr, "textfont", None)
            new_tf = dict(size=int(ann_size) if ann_size is not None else 10,
                          color=str(ann_color or "#ffffff"))
            fam = getattr(tf, "family", None) if tf is not None else None
            if fam:
                new_tf["family"] = fam
            tr.textfont = new_tf
        except Exception:
            pass

    # -- Shared colour axis: colour-bar title / sizes / colours -------------
    # Both routes are used because a choropleth built with px carries its
    # colour bar on the trace while a px.imshow heatmap carries it on the
    # colour axis, and the old app updated both. The family/weight/style come
    # from the same _style_dict helper the axis/legend text uses, so the
    # colour bar cannot end up in a different typeface from the chart.
    font = _style_dict(typo.get("family", ""), typo.get("font_style", "Normal"))
    try:
        cb_kwargs = {
            "tickfont": dict(size=int(lo.get("colorbar_tick_size", 10)),
                             color=str(lo.get("colorbar_tick_color", "#94a3b8")),
                             family=font["family"], weight=font["weight"],
                             style=font["style"]),
        }
    except Exception:
        cb_kwargs = {}
    cb_title = str(lo.get("colorbar_title", "") or "")
    try:
        # The title's FONT is written even when its TEXT is empty, or
        # "CB title sz" / "CB title clr" do nothing until a title is typed --
        # a control that only comes alive after an unrelated field is filled
        # reads as broken (the audit this is named after caught exactly this).
        # No runner sets a colour-bar title of its own, so the Layout option
        # is the single authority for it: empty text means no title.
        cb_kwargs["title"] = dict(
            text=cb_title,
            font=dict(size=int(lo.get("colorbar_title_size", 11)),
                      color=str(lo.get("colorbar_title_color", "#cbd5e1")),
                      family=font["family"], weight=font["weight"],
                      style=font["style"]))
    except Exception:
        pass
    if cs:
        # Traces that could not be unbound (a choropleth, or a plotly build
        # that refuses to drop the axis) still read the shared axis, so set
        # it too. Both are written rather than one, so the selection is
        # honoured whichever route Plotly ends up rendering.
        try:
            fig.update_coloraxes(colorscale=str(cs))
        except Exception:
            pass
    if cb_kwargs:
        try:
            fig.update_coloraxes(colorbar=cb_kwargs)
        except Exception:
            pass
    # -- Colour-bar placement ----------------------------------------------
    # Left/Right reserve a strip out of the plot domain instead of nudging the
    # bar with a negative x, which would land it on the y tick labels.
    # See reserve_colorbar_strip for the full reason.
    cbpos = str(lo.get("colorbar_position", "Right") or "Right").strip().lower()
    cborient = str(lo.get("colorbar_orientation", "Auto") or "Auto").strip().lower()
    if cbpos in ("top", "bottom") and cborient == "auto":
        cborient = "horizontal"
    elif cborient == "auto":
        cborient = "vertical"
    reserve_colorbar_strip(fig, cbpos, traces=traces, width_px=width_px)
    # Horizontal Top/Bottom bars: flip the Plotly colorbar itself so it
    # renders under/over the plot instead of as a side strip.
    try:
        _horiz = (cborient == "horizontal")
        for _tr in traces:
            _cb = getattr(_tr, "colorbar", None)
            if _cb is None:
                continue
            try:
                _cb.orientation = "h" if _horiz else "v"
                if _horiz:
                    _cb.x = 0.5
                    _cb.xanchor = "center"
                    _cb.y = 1.08 if cbpos == "top" else -0.30
                    _cb.yanchor = "bottom" if cbpos == "top" else "top"
                    _cb.len = 0.6
                    try:
                        _cb.thickness = 14
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            _ca = getattr(fig.layout, "coloraxis", None)
            if _ca is not None and getattr(_ca, "colorbar", None) is not None:
                _ca.colorbar.orientation = "h" if _horiz else "v"
                if _horiz:
                    _ca.colorbar.x = 0.5
                    _ca.colorbar.xanchor = "center"
                    _ca.colorbar.y = 1.08 if cbpos == "top" else -0.30
                    _ca.colorbar.len = 0.6
        except Exception:
            pass
    except Exception:
        pass
    for tr in traces:
        cb = getattr(tr, "colorbar", None)
        if cb is None:
            continue
        for key, val in cb_kwargs.items():
            try:
                setattr(cb, key, val)
            except Exception:
                pass


COLORBAR_THICKNESS_PX = 30
COLORBAR_GAP_PX = 8
# Extra breathing room between the bar and the y tick labels on the
# Left position (see reserve_colorbar_strip).
_COLORBAR_LABEL_GAP_PX = 6


# Per-character width for the y tick labels, at the ~12px size the axis ticks
# are drawn at. Rounded up deliberately: over-reserving costs a little width,
# under-reserving puts text under the colour bar.
_AXIS_CHAR_PX = 8
_AXIS_TICK_PAD_PX = 12


def _axis_label_need_px(fig, side="left"):
    """Pixels the reserved column must also leave for the tick labels.

    Measures whichever y axis is drawn on *side*: ``yaxis`` on the left, and
    ``yaxis2`` on the right when the chart has a secondary axis (the dual-axis
    Categorical bar+line chart is the case that matters).  Returns 0 when that
    side has no axis, or the axis has its tick labels switched off.
    """
    try:
        name = "yaxis2" if side == "right" else "yaxis"
        ax = getattr(fig.layout, name, None)
        if ax is None:
            return 0
        if getattr(ax, "showticklabels", None) is False:
            return 0
        vals = getattr(ax, "tickvals", None)
        longest = 0
        if vals:
            longest = max((len(str(v)) for v in vals), default=0)
        else:
            for tr in getattr(fig, "data", []) or []:
                # Only traces actually bound to THIS axis; a secondary axis
                # would otherwise be measured using the primary axis values.
                if str(getattr(tr, "yaxis", "") or "y") != name.replace(
                        "yaxis", "y"):
                    continue
                y = getattr(tr, "y", None)
                if y is None:
                    continue
                try:
                    longest = max((len(str(v)) for v in y), default=longest)
                except Exception:
                    continue
        return _AXIS_CHAR_PX * longest + _AXIS_TICK_PAD_PX
    except Exception:
        return _AXIS_CHAR_PX * 10 + _AXIS_TICK_PAD_PX


def _legend_axis_need_px(fig, side):
    """Total axis furniture a reserved strip must leave on *side*.

    Two things live in the strip beside the legend, not one. The tick labels
    (see ``_axis_label_need_px``) AND the rotated axis title
    (``_legend_axis_title_px``), both drawn just outside the axis. Measuring
    only the labels left the title under the legend plate, which is the
    "Sum transaction_qty" text visibly running through the Top Left plate in
    the Categorical preview.

    A separating gap is added on top so the plate's border never sits flush
    against the axis text.
    """
    try:
        return (_axis_label_need_px(fig, side)
                + _legend_axis_title_px(fig, side)
                + _LEGEND_AXIS_GAP_PX)
    except Exception:
        return _LEGEND_AXIS_GAP_PX


def reserve_colorbar_strip(fig, side, traces=(), width_px=None):
    """Carve a dedicated colour-bar strip out of *fig*'s plot domain.

    Same idea as ``reserve_legend_column``: shrink the x-domain on the chosen
    side so the colour bar, the cells and the axis labels each get their own
    non-overlapping area.

    Placing the bar with a bare ``colorbar.x = -0.12`` does NOT work. Paper
    coordinates place x=0 at the left edge of the plotting area, and the y
    tick labels are drawn in the margin immediately to the left of that edge
    -- so a negative x drops the bar straight on top of them. That is the
    "colour bar and the correlation chart meshed together" report: with
    Colorbar pos = Left the bar sat against "transaction_qty" / "unit_price".

    Reserving the domain instead moves the axis labels along with the plot, so
    the freed strip is genuinely empty and the bar can be laid in it. The
    strip is measured from the bar's own thickness, so a thicker bar gets a
    proportionally wider strip.
    """
    if side not in ("left", "right", "top", "bottom"):
        return False
    # Top/Bottom bars are horizontal and live in the margins, not in a side
    # strip -- keep the full plot width and reserve vertical room instead so
    # there is no side gap (the Image-4 complaint).
    if side in ("top", "bottom"):
        try:
            m = fig.layout.margin.to_plotly_json() or {}
        except Exception:
            m = {}
        try:
            if side == "top":
                m["t"] = max(int(m.get("t") or 0), 80)
            else:
                m["b"] = max(int(m.get("b") or 0), 80)
            fig.update_layout(margin=m)
        except Exception:
            pass
        return True
    try:
        thick = COLORBAR_THICKNESS_PX
        for tr in traces or ():
            cb = getattr(tr, "colorbar", None)
            if cb is not None and getattr(cb, "thickness", None):
                thick = int(cb.thickness)
                break
        ca = getattr(fig.layout, "coloraxis", None)
        if ca is not None and getattr(ca.colorbar, "thickness", None):
            thick = int(ca.colorbar.thickness)
        need_px = float(thick) + COLORBAR_GAP_PX
        if side == "left":
            # The y tick labels are drawn immediately left of the axis, i.e.
            # inside the very strip being freed. On a heatmap they are the
            # column NAMES ("transaction_qty"), so they are long: without
            # measuring them the strip is only wide enough for the bar itself
            # and the labels end up underneath it. Same reasoning as
            # _legend_need_px adding _LEGEND_AXIS_PX on the left.
            # The separating gap matters on this side only: Right has the
            # plot edge to sit against, but on Left the labels sit BETWEEN
            # the bar and the cells, so without a gap the longest label is
            # drawn flush against the bar.
            need_px += _axis_label_need_px(fig, side) + _COLORBAR_LABEL_GAP_PX
        w = int(width_px or 0)
        if w > 0:
            strip = need_px / float(w)
        else:
            # No width to measure against: fall back to a fraction that covers
            # the bar plus a generous axis allowance.
            strip = min(0.28, need_px / 300.0)
        # Clamp so a narrow card cannot be squeezed to nothing and a very wide
        # one does not leave an absurd gap.
        strip = max(0.05, min(0.30, strip))
    except Exception:
        strip = 0.10
    try:
        # ``dict(fig.layout.margin)`` raises ValueError on some Plotly versions
        # (see the note in ``reserve_legend_column``); that made the colour-bar
        # strip overwrite l/r/t/b with bare values and reset the chart's margins.
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    if side == "left":
        m["l"] = max(int(m.get("l") or 0), 60)
        domain = [strip, 1.0]
        cb_pos, cb_anchor = 0.0, "left"
    else:
        m["r"] = max(int(m.get("r") or 0), 60)
        domain = [0.0, 1.0 - strip]
        cb_pos, cb_anchor = 1.0, "right"
    try:
        fig.update_layout(margin=m, xaxis=dict(domain=domain))
    except Exception:
        return False
    try:
        fig.update_coloraxes(colorbar=dict(x=cb_pos, xanchor=cb_anchor))
    except Exception:
        pass
    for tr in traces or ():
        try:
            tr.colorbar.x = cb_pos
            tr.colorbar.xanchor = cb_anchor
        except Exception:
            pass
    return True


# Trace types Plotly never draws a legend box for. A heatmap has no legend,
# so reserving a legend column for one only steals width from the plot.
_NO_LEGEND_TYPES = ("heatmap",)


def _figure_has_legend(fig):
    """True when at least one trace will actually paint a legend box."""
    for tr in getattr(fig, "data", []) or []:
        if str(getattr(tr, "type", "")).lower() in _NO_LEGEND_TYPES:
            continue
        if getattr(tr, "showlegend", None) is False:
            continue
        return True
    return False


def _apply_pie_options(fig, lo):
    """Apply the Pie & Donut Layout options to every pie trace on *fig*.

    Guarded per trace exactly like the histogram and heatmap blocks: one Layout
    tab serves every chart type, and a pie trace is the only thing that has a
    ``hole``, so a bar or line trace is never handed one.

    ``donut_hole`` is absolute rather than relative. The runner picks 0.45 for a
    donut, but the option defaults to 0, and writing that default is correct --
    it is what turns a donut back into a full pie, which is exactly what the
    control is for.
    """
    for tr in getattr(fig, "data", []) or []:
        if str(getattr(tr, "type", "")).lower() != "pie":
            continue
        try:
            tr.hole = max(0.0, min(0.9, float(lo.get("donut_hole", 0.0) or 0.0)))
        except Exception:
            pass
        try:
            tr.textinfo = str(lo.get("pie_textinfo", "label+percent") or "label+percent")
        except Exception:
            pass
        try:
            tr.insidetextfont = _font_kwargs(lo.get("pie_value_size"),
                                             lo.get("pie_value_color"))
            tr.outsidetextfont = _font_kwargs(lo.get("pie_value_size"),
                                              lo.get("pie_value_color"))
        except Exception:
            pass
        try:
            tr.sort = (False if str(lo.get("pie_direction", "clockwise")) == "clockwise"
                       else True)
            tr.direction = str(lo.get("pie_direction", "clockwise") or "clockwise")
        except Exception:
            pass
        try:
            tr.rotation = int(lo.get("pie_rotation", 0) or 0)
        except Exception:
            pass
        # "Pull" is a per-slice fraction; a scalar pulls every slice, which is
        # what the old slider implied.
        try:
            tr.pull = max(0.0, min(0.5, float(lo.get("pull_slices", 0.0) or 0.0)))
        except Exception:
            pass


# The trace types that carry a ``marker`` object the Layout marker controls may
# touch. The geo scatter types are here because the MAP settings page offers
# Marker size / opacity (old CONTROLS_ADVANCED["map_plot"]); without them the
# map's own marker controls matched no trace and were dead on the map page
# while working on a plain scatter.
_MARKER_TRACE_TYPES = ("scatter", "scattergl", "scattermap", "scattermapbox",
                       "scattergeo")


def _line_mode_with_markers(mode, want):
    """Return *mode* with its "markers" token added or removed.

    Plotly's ``mode`` is an ordered set of tokens, so toggling has to rewrite
    the string rather than append blindly: "lines+markers" with markers off is
    "lines" (not "lines+"), and a "markers"-only trace with them on must not
    gain a stray "lines".
    """
    mode = str(mode or "")
    has = "markers" in mode
    if has == bool(want):
        return mode
    if has and not want:
        out = mode.replace("+markers", "").replace("markers+", "")
        out = out.replace("markers", "")
        if not out:
            # A markers-ONLY trace (a plain px.scatter) would be left with an
            # empty mode, which Plotly ignores -- so the checkbox silently did
            # nothing. The old app fell back to drawing the points as a line
            # instead (chart_settings.py ~868), and that is what a user who
            # unticks "Markers" actually expects to see.
            out = "lines"
    elif "lines" in mode:
        # Append after "lines" so the token order stays canonical.
        out = mode + "+markers" if mode.startswith("lines") else "markers+" + mode
    else:
        out = mode
    return out.strip("+")


def _fmt_cell(v, prec):
    """One heatmap cell label at *prec* decimals, abbreviated like the runner.

    Mirrors ``modules.analysis.matrix_table._fmt`` but takes the precision from
    the Layout control instead of hard-coding 2, so the slider and the label
    cannot disagree.
    """
    import math
    if v is None:
        return "\u2014"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(f):
        return "\u2014"
    if abs(f) >= 1_000_000:
        return f"{f / 1_000_000:,.{prec}f}M"
    if abs(f) >= 1_000:
        return f"{f:,.{max(0, prec - 2)}f}"
    return f"{f:,.{prec}f}"


def _retext_at_precision(text, z, prec):
    """Rebuild a heatmap's ``text`` array from ``z`` at *prec* decimals.

    Returns None when the shape cannot be matched, so a trace whose text is
    not a z-shaped grid is left exactly as the runner drew it.
    """
    if z is None or text is None:
        return None
    try:
        return [[_fmt_cell(v, prec) for v in row] for row in z]
    except (TypeError, ValueError):
        return None


def _apply_line_options(fig, lo):
    """Apply the line / marker Layout options (Time Series, Scatter).

    Every write is guarded on the trace actually having the attribute: a
    trendline is a "lines"-only trace with no markers, and a bar or heatmap
    trace has neither.
    """
    shape = lo.get("line_shape")
    fill = lo.get("line_fill")
    show_markers = lo.get("show_markers")
    for tr in getattr(fig, "data", []) or []:
        if str(getattr(tr, "type", "")).lower() not in _MARKER_TRACE_TYPES:
            continue
        if show_markers is not None:
            try:
                new = _line_mode_with_markers(getattr(tr, "mode", ""), show_markers)
                if new:
                    tr.mode = new
            except Exception:
                pass
        if shape and "lines" in str(getattr(tr, "mode", "") or ""):
            # "spline" is Plotly's name for a smooth curve; the rest map 1:1.
            try:
                tr.line.shape = str(shape)
            except Exception:
                pass
        if fill:
            try:
                tr.fill = str(fill)
            except Exception:
                pass
    # Marker size / opacity are shared by every point, and only matter when
    # something actually draws markers.
    ms = lo.get("marker_size")
    if ms is not None:
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            ms = None
    mo = lo.get("marker_opacity")
    if mo is not None:
        try:
            mo = float(mo)
        except (TypeError, ValueError):
            mo = None
    if ms is None and mo is None:
        return
    for tr in getattr(fig, "data", []) or []:
        if str(getattr(tr, "type", "")).lower() not in _MARKER_TRACE_TYPES:
            continue
        if "markers" not in str(getattr(tr, "mode", "") or ""):
            continue
        # A size-by-value scatter already carries a per-point size ARRAY from its
        # runner, which is what encodes the magnitude visually. Replacing it with
        # one scalar would flatten that encoding, so only opacity is applied.
        if ms is not None:
            try:
                if not isinstance(getattr(tr.marker, "size", None), (list, tuple)):
                    tr.marker.size = ms
            except Exception:
                pass
        if mo is not None:
            try:
                tr.marker.opacity = mo
            except Exception:
                pass


def _format_table_number(v, spec):
    """One Pivot Table cell at the Layout tab's chosen number *spec*.

    Missing values keep the em-dash the runner used, so switching the format
    never turns a gap into the text "nan".
    """
    import math
    if v is None:
        return "\u2014"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(f):
        return "\u2014"
    try:
        return format(f, spec)
    except (TypeError, ValueError):
        return str(f)


def _apply_table_options(fig, lo, t):
    """Apply the Pivot Table Layout options to every table trace on *fig*.

    Plotly draws a table with two independent blocks -- ``header`` and
    ``cells`` -- so every size, colour and height has to be written to the
    right one. The stripe colours are the cell ``fill`` alternation, and
    alignment is per COLUMN, not per trace, so both need the column layout.
    """
    fam = _font_stack((t or {}).get("family", "Inter"))
    for tr in getattr(fig, "data", []) or []:
        if str(getattr(tr, "type", "")).lower() != "table":
            continue
        try:
            hdr, cells = tr.header, tr.cells
        except Exception:
            continue
        for block, size_key, col_key in ((hdr, "table_header_font_size",
                                          "table_header_color"),
                                         (cells, "table_font_size",
                                          "table_font_color")):
            sz, col = lo.get(size_key), lo.get(col_key)
            if sz is None and col is None:
                continue
            try:
                d = _font_kwargs(sz, col)
                d["family"] = fam
                block.font = dict(d)
            except Exception:
                pass
        for block, key in ((hdr, "table_header_height"), (cells, "table_row_height")):
            val = lo.get(key)
            if val is None:
                continue
            try:
                block.height = int(val)
            except Exception:
                pass
        # The first column is the row index, so it takes index align while every
        # data column takes data align. The column COUNT comes from the data,
        # not from the existing `align` array: the runner never sets one, so
        # reading its length would report a single column and left-align the
        # whole table.
        ia, da = lo.get("table_index_align"), lo.get("table_data_align")
        if ia or da:
            n = 0
            for vals in (getattr(cells, "values", None) or [],
                         getattr(hdr, "values", None) or []):
                try:
                    n = max(n, len(vals))
                except TypeError:
                    continue
            n = max(n, 1)
            try:
                cells.align = [str(ia or "left")] + [str(da or "right")] * (n - 1)
            except Exception:
                pass
            try:
                if hdr is not None and hdr.values is not None:
                    hdr.align = str(ia or "left")
            except Exception:
                pass
        ev, od = lo.get("table_stripe_even_color"), lo.get("table_stripe_odd_color")
        if ev or od:
            try:
                cells.fill = dict(color=[str(od or "#0f172a"), str(ev or "#1e293b")])
            except Exception:
                pass
        # -- Number format -------------------------------------------------
        # The runner pre-formatted every cell, and a formatted string cannot be
        # re-formatted, so this re-renders the labels from the raw numbers the
        # runner stashed on the figure meta. Applied only when a format is
        # actually requested AND the raw values are present, so a figure built
        # without them keeps the labels it already has.
        fmt = lo.get("table_number_format")
        if fmt:
            meta = getattr(fig, "_lytrize_meta", None) or {}
            raw_cols = meta.get("table_raw_number_cols")
            idx_labels = meta.get("table_index_labels")
            if raw_cols:
                try:
                    spec = str(fmt)
                    rebuilt = [list(idx_labels or [])]
                    for col in raw_cols:
                        rebuilt.append([_format_table_number(v, spec)
                                        for v in col])
                    cells.values = rebuilt
                except Exception:
                    pass


def _apply_colorbar_border(fig, show_border):
    """Turn the colour bar / colour range frame on or off on every trace.

    Heatmaps, matrix tables and choropleths draw their own ``colorbar`` and
    never go through the legend block, so "Show border" has to reach them
    separately or the toggle would look like it only half worked.  Both a
    per-trace ``colorbar`` and a shared ``layout.coloraxis.colorbar`` are
    covered.

    Only colour bars the figure ALREADY declared are touched -- see
    ``real_colorbars``. Assigning to a ``colorbar`` reached by ``getattr``
    would *create* one, so this used to give every bar, line and pie chart a
    ghost colour bar with a frame and no data behind it.
    """
    width = 1 if show_border else 0
    for cb in real_colorbars(fig):
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
    # histogram_bins / histogram_opacity (Distribution, Layout tab) control the
    # histogram bars only -- they come from the old app's histogram_bins /
    # histogram_opacity controls (5-200 step 1 default 30; 0.1-1.0 step 0.05
    # default 0.80). They live in the shared layout dict because there is one
    # Layout tab for every chart type, but apply_layout() only touches traces
    # that are actually histograms, so a box plot in the same chart is
    # unaffected.
    # The heatmap_* / colorbar_* options (Correlation, Layout tab) control a
    # heatmap or matrix view. They come from the old app's control metadata
    # (chart_settings.py _CONTROL_META): heatmap_show_text "Annotations"
    # default True; heatmap_colorscale "RdBu"; heatmap_annotation_size 6-24
    # step 1 default 10; heatmap_annotation_precision 0-10 step 1 default 2;
    # heatmap_annotation_color #ffffff; colorbar_title ""; colorbar_tick_size
    # 6-18 step 1 default 10; colorbar_tick_color #94a3b8; colorbar_title_size
    # 8-24 step 1 default 11; colorbar_title_color #cbd5e1. As with
    # histogram_bins/opacity, they live in the shared layout dict because
    # there is one Layout tab for every chart type, but apply_display_options()
    # only touches traces that are actually heatmaps/choropleths.
    # The line/pie/marker/table/map option groups below come from the old app's
    # _CONTROL_META for the chart types that were migrated after the first four.
    # Same reasoning as the histogram and heatmap groups: one Layout tab serves
    # every chart type, so they share the layout dict and are applied under a
    # per-trace guard that only matches the trace types they can affect.
    #
    # Pie & Donut (pie_chart): donut_hole 0-0.9 default 0; pie_textinfo one of
    # [label, percent, value, label+percent, label+value] default label+percent;
    # pull_slices 0-0.5 step 0.01 default 0; pie_rotation 0-360 step 1 default
    # 0; pie_direction clockwise/counterclockwise default clockwise;
    # pie_value_size 6-24 default 11; pie_value_color #e2e8f0.
    #
    # Time Series / Scatter: line_shape linear/spline/hv/vh/hvh/vhv default
    # linear; line_fill none/tozeroy/tozerox/tonexty/tonextx default none;
    # show_markers default True; marker_opacity 0.1-1.0 step 0.05 default 0.8;
    # marker_size 2-30 step 1 default 6; render_mode svg/webgl default svg.
    #
    # Matrix Table: table_font_size 8-24 default 11; table_header_font_size 8-24
    # default 12; table_row_height 16-60 default 26; table_header_height 16-60
    # default 28; table_index_align left/center/right default left;
    # table_data_align left/center/right default right; table_number_format one
    # of the old d3-format strings default ",.2f"; table_font_color #f1f5f9;
    # table_header_color #6163df; table_stripe_even_color #1e293b;
    # table_stripe_odd_color #0f172a.
    #
    # Map Plot: show_colorbar default True and size_by_value default False, both
    # per old _CONTROL_META. map_plot already routes its own colour scale
    # through the shared colorbar_* / heatmap_colorscale keys, which is why they
    # are not repeated here.
    return {"title": "", "x_label": "", "y_label": "", "legend_title": "", "legend_labels": {}, "show_value_labels": True, "value_label_color": "#ffffff", "label_position": "outside", "bar_mode": "group", "bar_gap": 0.28, "histogram_bins": 30, "histogram_opacity": 0.80, "show_grid": True, "show_border": True, "legend_position": "Top Right", "legend_orientation": "Auto", "legend_order": "Normal", "legend_itemclick": "toggle", "heatmap_show_text": True, "heatmap_colorscale": "RdBu", "heatmap_annotation_size": 10, "heatmap_annotation_precision": 2, "heatmap_annotation_color": "#ffffff", "colorbar_position": "Right", "colorbar_orientation": "Auto", "colorbar_title": "", "colorbar_tick_size": 10, "colorbar_tick_color": "#94a3b8", "colorbar_title_size": 11, "colorbar_title_color": "#cbd5e1", "donut_hole": 0.0, "pie_textinfo": "label+percent", "pull_slices": 0.0, "pie_rotation": 0, "pie_direction": "clockwise", "pie_value_size": 11, "pie_value_color": "#e2e8f0", "line_shape": "linear", "line_fill": "none", "show_markers": True, "marker_opacity": 0.8, "marker_size": 6, "render_mode": "svg", "table_font_size": 11, "table_header_font_size": 12, "table_row_height": 26, "table_header_height": 28, "table_index_align": "left", "table_data_align": "right", "table_number_format": ",.2f", "table_font_color": "#f1f5f9", "table_header_color": "#6163df", "table_stripe_even_color": "#1e293b", "table_stripe_odd_color": "#0f172a", "show_colorbar": True, "size_by_value": False}

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
    # Plotly's Figure does not deepcopy its custom attributes, so the runners'
    # ``_lytrize_meta`` is dropped by the copy. The Pivot Table's raw numbers
    # ride in there, and without this the "Num format" control had nothing to
    # re-render from the moment it was applied.
    _meta = getattr(fig, "_lytrize_meta", None)
    if _meta is not None and getattr(f2, "_lytrize_meta", None) is None:
        f2._lytrize_meta = copy.deepcopy(_meta)
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
    # The dual-axis (secondary) label. Categorical -- and time series later --
    # can draw a right-hand axis; the old display layer wrote y2_label to
    # ``yaxes(secondary_y=True)`` (chart_settings.py ~1113). Only written when
    # non-empty so a chart whose runner set its own right-axis title keeps it.
    if lo.get("y2_label"):
        f2.update_layout(yaxis2=dict(title=dict(text=lo["y2_label"])))
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

    # -- Histogram geometry (Distribution only) ----------------------------
    # Guarded per trace, exactly like the old app's `if ttype == "histogram"`
    # check: a distribution chart can also draw box plots, and bins/opacity
    # are meaningless (and Plotly-invalid) on those.
    _bins = lo.get("histogram_bins")
    if _bins is not None:
        try:
            _bins = max(1, int(round(float(_bins))))
        except Exception:
            _bins = None
    if _bins is not None:
        try:
            _op = float(lo.get("histogram_opacity", 0.80))
        except Exception:
            _op = 0.80
        for tr in f2.data:
            # The trace name "histogram" is how Plotly names a Histogram
            # trace type; checking it is safer than isinstance() alone
            # because figure copies can come back as BaseTraceType.
            if type(tr).__name__ != "Histogram":
                continue
            try:
                # Plotly removed the old `nbins` shorthand: the bin count now
                # lives on nbinsx / nbinsy, chosen by the axis the data sits
                # on. Setting the right one matters -- assigning the other
                # would bin against the wrong axis.
                has_x = getattr(tr, "x", None) is not None
                has_y = getattr(tr, "y", None) is not None
                if has_x and not has_y:
                    tr.nbinsx = _bins
                    tr.nbinsy = None
                elif has_y and not has_x:
                    tr.nbinsy = _bins
                    tr.nbinsx = None
                else:
                    tr.nbinsx = _bins
                    tr.nbinsy = _bins
            except Exception:
                pass
            try:
                tr.opacity = _op
            except Exception:
                pass

    # -- Heatmap / colour-bar options (Correlation, Matrix Heatmap) ----------
    # Guarded per trace exactly like the histogram block above, and for the
    # same reason: one Layout tab serves every chart type, and these options
    # are meaningless (and largely Plotly-invalid) on a bar or line trace.
    _apply_heatmap_options(f2, lo, t, width_px=width_px)

    # -- Pie / line / marker / table options (Pie, Time Series, Scatter, Pivot)
    # Same per-trace guarding as the two blocks above, for the same reason.
    _apply_pie_options(f2, lo)
    _apply_line_options(f2, lo)
    _apply_table_options(f2, lo, t)

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
    # Only reserve a legend column when a legend is really drawn. A heatmap
    # (correlation) has none, yet ``legend_position`` still carries a default,
    # so this used to reserve a column for a legend that never appears -- and
    # because it writes xaxis.domain it also clobbered the colour-bar strip
    # reserved moments earlier, which is why a left-hand colour bar ended up
    # on top of the y tick labels.
    if place and _figure_has_legend(f2):
        # Reserve the column AFTER the xaxis showgrid/title updates above,
        # so this domain is not clobbered by a later update_layout.
        #
        # The strip is sized from the labels that will actually be painted:
        # legend renames already happened above, so the trace names on f2 are
        # the final ones (no need to thread them in from the caller), and the
        # legend title comes from the layout options.
        #
        # ``apply_legend_layout`` is the shared entry point (the same one the
        # widget's resize refit calls), so a chart first drawn at an assumed
        # width and then refit to its real width always ends up on the same
        # rule rather than two different ones. When a width is known it may
        # also return a horizontal band instead of a column; the band writes
        # its own x/y/anchors, so those are only written here for a column.
        _plan = apply_legend_layout(
            f2, lp,
            names=[getattr(tr, "name", "") for tr in f2.data
                   if getattr(tr, "showlegend", True) is not False],
            legend_title=lo.get("legend_title", ""),
            width_px=width_px,
        )
        _leg = dict(
            # A translucent plate keeps the legend readable wherever it
            # lands, without hiding the plot behind an opaque slab.
            bgcolor="rgba(15, 23, 42, 0.72)",
            bordercolor=(LEGEND_BORDER_COLOR if show_border
                         else "rgba(0, 0, 0, 0)"),
            borderwidth=1 if show_border else 0,
        )
        if not _plan or _plan.get("mode") != "band":
            _leg.update(x=place["x"], y=place["y"],
                        xanchor=place["xanchor"], yanchor=place["yanchor"])
        f2.update_layout(legend=_leg)

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
                # A heatmap's in-cell text is sized and coloured by the
                # heatmap_* Layout options, which were applied EARLIER in this
                # same function. This generic pass must not undo them, so
                # heatmap/choropleth traces are left alone entirely. Every
                # other trace type has no annotation options of its own and
                # still takes the shared value-label colour.
                if str(getattr(tr, "type", "")).lower() not in ("heatmap",
                                                                "choropleth"):
                    tr.textfont = dict(color=str(vcolor))
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

    # -- Line width (Layout tab, categorical + the line-drawing types) ------
    # Old _CONTROL_META: slider 1..10 default 2, applied to every trace whose
    # mode contains "lines" (chart_settings.py ~862). For categorical that is
    # the Secondary Y-Axis overlay the runner draws as lines+markers; a
    # bar-only figure has no line trace and is untouched -- exactly how the
    # old app behaved. Kept as its own guarded pass so a bar/heatmap trace
    # never sees an attribute it does not have.
    lw = lo.get("line_width")
    if lw is not None:
        try:
            lw = int(lw)
        except (TypeError, ValueError):
            lw = None
    if lw is not None:
        for tr in f2.data:
            if str(getattr(tr, "type", "")).lower() not in _MARKER_TRACE_TYPES:
                continue
            if "lines" not in str(getattr(tr, "mode", "") or ""):
                continue
            try:
                tr.line.width = lw
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
    # -- Legend orientation / order / click (PowerBI parity) ------------------
    # Orientation Auto keeps the engine decision (side=vertical, band=
    # horizontal); explicit Vertical/Horizontal overrides it. Applied in the
    # typography pass so it wins over apply_layout placement.
    try:
        _lo2 = dict(lo or {})
        _want_orient = str(_lo2.get("legend_orientation", "Auto") or "Auto").strip().lower()
        if _want_orient in ("vertical", "horizontal"):
            f2.update_layout(legend=dict(orientation="v" if _want_orient == "vertical" else "h"))
    except Exception:
        pass
    try:
        _want_order = str((lo or {}).get("legend_order", "Normal") or "Normal").strip().lower()
        f2.update_layout(legend=dict(traceorder="reversed" if _want_order == "reversed" else "normal"))
    except Exception:
        pass
    try:
        _want_click = str((lo or {}).get("legend_itemclick", "toggle") or "toggle").strip().lower()
        if _want_click in ("toggle", "toggleothers", "false"):
            f2.update_layout(legend=dict(itemclick=False if _want_click == "false" else _want_click))
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
    # Real colour bars only. Reading ``.colorbar`` always yields a default
    # object, and styling it would ADD a colour bar to a chart that never had
    # one -- so a themed bar chart grew a ghost colour ramp in dark mode.
    # See ``real_colorbars``.
    for cb in real_colorbars(f2):
        try:
            _theme_one_colorbar(cb, key, tok)
        except Exception:
            pass
    return f2
