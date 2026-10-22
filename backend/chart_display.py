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

    A family the app ships in ``backend/assets/fonts`` counts as available even
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

    1. **Bundled** fonts (``backend/assets/fonts``, registered at startup) always
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
# A domain figure (pie/donut/treemap/sunburst/funnel) has no axes, so the
# legend plate is the ONLY thing sharing the reserved strip. The shared
# ``LEGEND_STRIP_MAX`` cap can therefore leave the strip narrower than the
# plate on a compact tile with long labels (a 380px card, ten categories),
# which parks the plate back over the marks. For a domain figure the strip is
# allowed to grow a little further -- the pie simply takes a smaller box, it
# is never crushed to nothing the way a cartesian plot's width would be -- so
# the plate always fits beside it instead of on top of it.
LEGEND_STRIP_MAX_DOMAIN = 0.62
# Breathing room kept between a domain figure's marks and its legend plate, in
# pixels, when the strip has to be widened to hold the plate (see
# ``reserve_legend_column``). Without it the plate sits flush against the
# pie's box edge, which reads as touching.
_LEGEND_DOMAIN_GAP_PX = 10
# Belt-and-braces outer margin floor on the legend's side.  The strip
# reservation already carves the tick labels and axis title out of the PLOT
# AREA (they are drawn inside the strip), so this margin is no longer the only
# thing keeping them off the card edge -- but it is kept as a floor so a
# stripped-down saved layout can never crush the outer chrome to nothing.
LEGEND_AXIS_MARGIN = 72
# A pie/donut/treemap/sunburst draws NO axes, so nothing competes for the
# outer margin on the legend's side.  Widening it to LEGEND_AXIS_MARGIN there
# leaves a dead slab at the card edge AND -- because the domain fraction
# resolves against the card minus margins -- shrinks the box the pie is sized
# from, which is half of the "oversized legend strip + tiny pie" pair on the
# dashboard.  Domain-only figures get breathing room instead.
LEGEND_AXIS_MARGIN_DOMAIN = 12
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
# Cap on how much vertical space a horizontal band may claim on a SIDE dock
# (Top/Bottom Left/Right).  Past this the plot area stops being worth
# protecting, so the reservation is capped -- a side dock always has a column to
# fall back to, so an over-tall band is never the answer there.  A share of the
# PLOT-AREA height (card minus the top/bottom margins), not the card height.
# Centred positions are handled separately in ``reserve_legend_band`` (they have
# no column to fall back to, so their band is reserved in full).
LEGEND_BAND_MAX_SHARE = 0.30

# ---------------------------------------------------------------------------
# Card-aware type scaling.
#
# A dashboard tile can be handed anything from a ~300px sliver to a ~1900px
# full-width card, but the legend / slice-label sizes are absolute pixels
# (11px by default). Pinned at 11px, a Horizontal legend on a narrow card has
# nowhere to shrink: it wraps to two rows and collides with itself (the "I set
# legends horizontal but they don't stay horizontal unless I widen the card"
# report), and on a small card the outside slice labels + legend text simply
# overflow the shrunken plot (the "resize the card small and the legend/label
# text doesn't adjust" report). Power BI scales the type with the visual.
#
# Both reports are HORIZONTAL-fit problems -- a legend row wraps on width, and
# an outside slice label runs off the plot's width -- so the factor is derived
# from the card WIDTH. It is clamped so it only ever SHRINKS text on a narrow
# card and never grows it: the factor is 1.0 at or above ``_TYPE_SCALE_FULL_PX``
# (so a full-width card, and every half-card and up, keep the exact size the
# user chose -- the wide-card look is unchanged) and falls to
# ``_TYPE_SCALE_MIN`` at ``_TYPE_SCALE_MIN_PX`` and below.
_TYPE_SCALE_FULL_PX = 640
_TYPE_SCALE_MIN_PX = 300
_TYPE_SCALE_MIN = 0.72


def _type_scale(width_px=None, height_px=None):
    """Font-size multiplier for the card, in [ ``_TYPE_SCALE_MIN``, 1.0 ].

    Derived from the card WIDTH (the binding constraint on a wrapped legend row
    and on an outside slice label). Returns 1.0 when no width is known, so
    headless callers / export keep their exact, un-scaled sizes, and for any
    card at least ``_TYPE_SCALE_FULL_PX`` wide.
    """
    try:
        w = float(width_px or 0)
    except Exception:
        return 1.0
    if w <= 0:
        return 1.0
    if w >= _TYPE_SCALE_FULL_PX:
        return 1.0
    if w <= _TYPE_SCALE_MIN_PX:
        return _TYPE_SCALE_MIN
    # Linear in the card width between the two anchors.
    span = float(_TYPE_SCALE_FULL_PX - _TYPE_SCALE_MIN_PX)
    frac = (w - _TYPE_SCALE_MIN_PX) / span
    return _TYPE_SCALE_MIN + (1.0 - _TYPE_SCALE_MIN) * frac


def _scale_size(size, factor):
    """Scale one font size by *factor*, rounded and floored at a legible 7px.

    A missing size (``None``) is passed through untouched so a caller that
    never set one keeps Plotly's own default.
    """
    if size is None:
        return None
    try:
        v = float(size) * float(factor)
    except Exception:
        return size
    return int(max(7, round(v)))


# Floor for the horizontal-legend fit shrink (see ``_horizontal_fit_scale``).
# A legend may shrink well below the card-scale floor to stay on ONE row --
# that is the whole point of choosing "Horizontal". 0.52 was measured against
# a ~630px dashboard tile carrying nine long category names plus a legend
# title: at the old 0.70 floor the row still wrapped (the "not horizontal
# single row" report) and the wrapped rows sat on the pie's outside slice
# labels. The JS re-reserve pass in ``modules.export`` mirrors this floor.
_HLEGEND_FIT_MIN = 0.52


def _horizontal_fit_scale(names, fig, width_px, legend_title=""):
    """Font factor that makes a HORIZONTAL legend fit on a single row.

    A horizontal legend lays its entries left-to-right and wraps; at the default
    size a nine-entry legend with long category names overflows one row, wraps
    to two, and (with the band reserved for the wrapped height) the rows collide
    -- the "I set legends horizontal but they only stay horizontal if I widen
    the card" report.  The user's intent in picking Horizontal is ONE row, so
    the font is shrunk until the entries fit the plot width on one row.

    The measurement covers the whole row: every entry PLUS the legend title
    (which sits inline in the same row), sized at the font actually drawn
    (``_LEGEND_CHAR_PX`` was measured at 12px, so other sizes scale it).

    Returns a factor in [ ``_HLEGEND_FIT_MIN``, 1.0 ]: 1.0 when the entries
    already fit (or there is no width / no text to measure), down to the floor
    when they never quite fit (in which case the legend wraps, but the band is
    reserved for the wrapped rows so they never overlap).
    """
    items = [str(n) for n in (names or []) if str(n or "").strip()]
    try:
        w = float(width_px or 0)
    except Exception:
        w = 0.0
    if not items or w <= 0:
        return 1.0
    try:
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    avail = max(80.0, w - _margin_px(m, "l") - _margin_px(m, "r"))
    n = len(items)
    # Include Plotly's item padding as well as the colour swatch. The previous
    # 24px allowance was optimistic for compact legends and selected a band
    # that then wrapped in the actual renderer.
    fixed = 32.0 * n
    # The title rides on the same row, to the left of the entries, so it costs
    # width too -- a 21-character title is ~170px of the row at 13px.
    text = sum(len(x) for x in items) + len(str(legend_title or ""))
    if text <= 0:
        return 1.0
    # Per-character width tracks the font that will actually be drawn.
    try:
        meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
        fsize = float(meta.get("lytrize_base_legend_font")
                       or fig.layout.legend.font.size or 12)
    except Exception:
        fsize = 12.0
    char_px = max(9.0, _LEGEND_CHAR_PX) * max(0.5, fsize / 12.0)
    # Need: fixed + (char_px * s) * text <= avail  ->  s <= (avail-fixed)/(char*text)
    room = avail - fixed
    if room <= 0:
        return _HLEGEND_FIT_MIN
    fit = room / (char_px * text)
    return max(_HLEGEND_FIT_MIN, min(1.0, fit))








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
    need = _legend_plate_px(names, legend_title)
    if axis_px is None:
        # No figure to measure: keep the historic allowance on the left, and
        # still give the right a gap so a secondary axis is never painted over.
        need += _LEGEND_AXIS_PX if side == "left" else _LEGEND_AXIS_GAP_PX
    else:
        need += max(0, int(axis_px))
    return need


def _legend_plate_px(names, legend_title=""):
    """Estimated pixel width of the legend PLATE itself, without axis furniture.

    Kept separate from ``_legend_need_px`` because the anchor clamp in
    ``reserve_legend_column`` needs the plate's own width (how far it extends
    from its anchor), not the width of the whole reserved column.
    """
    longest = 0
    for n in list(names or []) + ([legend_title] if legend_title else []):
        longest = max(longest, len(str(n)))
    return _LEGEND_CHROME_PX + _LEGEND_CHAR_PX * longest


def _legend_side_margin(fig, default=LEGEND_AXIS_MARGIN):
    """Outer-margin floor for the legend's side of *fig*.

    Axis-bearing charts need the wide floor (``LEGEND_AXIS_MARGIN``) because
    the tick labels and the rotated axis title share the legend's strip.  A
    domain-only figure (pie/donut/treemap/sunburst) draws no axes at all, so
    that width is dead space that also shrinks the plot box the pie is sized
    from -- it gets the narrow breathing-room floor instead.  Both the area
    probe in ``plan_legend_layout`` and the margin write in
    ``reserve_legend_column`` go through this, so the strip fraction is always
    resolved against the same margins that are actually written.
    """
    try:
        doms = _domain_traces(fig)
    except Exception:
        doms = []
    if doms:
        data = list(getattr(fig, "data", None) or [])
        # Domain-only: every trace sizes itself from its own domain box.
        if len(doms) >= len(data):
            return float(min(float(default), LEGEND_AXIS_MARGIN_DOMAIN))
    return float(default)


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


def _legend_wrap_rows(names, legend_title, fig, width_px, type_scale=1.0):
    """How many ROWS a horizontal legend wraps into at this card width.

    A horizontal legend lays its items left-to-right and WRAPS, so the height
    it actually occupies is the number of wrapped rows -- NOT the item count.
    Sizing the band from ``len(names)`` (the old worst case) both over-reserved
    on a wide card and, once the result was clamped, still under-reserved on a
    narrow one; that under-reservation is what let the plate spill onto a
    centred pie/donut.  Measuring the wrap against the real width gives the
    height the legend will really take, so the reserved band matches it.

    ``type_scale`` shrinks the per-character width to match a smaller legend
    font (see ``_type_scale``), so the row count reflects the text that is
    actually drawn -- a smaller font fits more items per row and the band is
    not over-reserved for rows that no longer exist.
    """
    items = [str(n) for n in (names or []) if str(n or "").strip()]
    if not items:
        return 1
    try:
        w = float(width_px or 0)
    except Exception:
        w = 0.0
    if w <= 0:
        # No width to wrap against: keep the safe worst case (one item per
        # row) so a headless caller never under-reserves and overlaps.
        return len(items)
    # The row wraps within the plot area's horizontal extent -- the card width
    # minus the outer margins the legend is drawn between.
    try:
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    avail = max(80.0, w - _margin_px(m, "l") - _margin_px(m, "r"))
    # This function only describes HORIZONTAL bands, so the single-row fit
    # shrink is part of the measurement: when ``_horizontal_fit_scale`` will
    # pull the font down to make one row, the wrap is computed against THAT
    # text width and the band is reserved for one row, not the wrapped count
    # the unscaled font would have needed.
    fit = _horizontal_fit_scale(items, fig, width_px, legend_title)
    try:
        char_px = _LEGEND_CHAR_PX * float(type_scale or 1.0) * fit
    except Exception:
        char_px = _LEGEND_CHAR_PX * fit
    total = 0.0
    for n in items:
        # swatch + gap + text (per-character width from _LEGEND_CHAR_PX).
        total += 24 + char_px * len(n)
    rows = int(-(-total // avail))  # ceil, without importing math
    return max(1, rows)


def _band_plan(fig, names, legend_title, edge, side, axis_px, width_px=None,
               type_scale=1.0):
    """Describe the horizontal-band layout for a legend.

    Band height follows the WRAPPED ROW COUNT -- the items sit side by side and
    wrap, so a long series name costs width, not height -- plus a row for the
    legend title when one is set. ``edge`` is "top" or "bottom"; "middle" (the
    Left / Right docks) has no band to reserve and falls back to a plain column.

    ``type_scale`` is the card's font factor (see ``_type_scale``); it scales
    both the row height and the per-character wrap width so the band matches
    the smaller text actually drawn on a compact card.
    """
    rows = _legend_wrap_rows(names, legend_title, fig, width_px,
                             type_scale=type_scale)
    try:
        row_px = _LEGEND_ROW_PX * float(type_scale or 1.0)
    except Exception:
        row_px = _LEGEND_ROW_PX
    band_px = rows * row_px + _LEGEND_BAND_GAP_PX
    if legend_title:
        band_px += row_px
    return {"mode": "band", "side": side, "top": edge == "top",
            "band_px": band_px, "rows": rows,
            "need_px": _legend_need_px(names, legend_title, side or "right",
                                       axis_px)}


def plan_legend_layout(fig, legend_position, names=None, legend_title="",
                       width_px=None, legend_orientation="Auto",
                       type_scale=1.0):
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

    ``legend_orientation`` lets the user's explicit Auto / Vertical /
    Horizontal choice WIN over that pixel heuristic: "Horizontal" on a corner
    (Top/Bottom Left/Right) must be a top/bottom band, and "Vertical" must be
    a side column. Without it a later orientation pass would flip a horizontal
    row into the side strip a column reserved (or the reverse) and the items
    would land on the axis -- the "horizontal legend overlaps" report. "Auto"
    (the default) keeps the pixel-based decision.
    """
    place = legend_anchor(legend_position)
    if not place:
        return None
    side = place["side"]
    edge = place["edge"]
    if not side:
        # A centered Top/Bottom legend normally uses a horizontal band. If a
        # requested one-row legend cannot fit at a readable size, move it to a
        # right-side column instead of letting Plotly wrap it over the pie.
        # Keep the requested position in metadata so a later resize can switch
        # back to the band when there is room again.
        orient = str(legend_orientation or "Auto").strip().lower()
        if orient == "vertical":
            axis_px = _legend_axis_need_px(fig, "right")
            area = _plot_area_width(fig.layout.margin.to_plotly_json() or {},
                                    width_px)
            return {"mode": "column", "side": "right", "effective_position": "Right",
                    "need_px": _legend_need_px(names, legend_title, "right", axis_px),
                    "axis_px": axis_px,
                    "strip": _legend_strip(names, legend_title, "right",
                                           area if width_px else None,
                                           axis_px=axis_px)}
        if orient == "horizontal" and width_px:
            fit = _horizontal_fit_scale(names, fig, width_px, legend_title)
            if fit < 0.78:
                axis_px = _legend_axis_need_px(fig, "right")
                try:
                    margins = fig.layout.margin.to_plotly_json() or {}
                except Exception:
                    margins = {}
                area = _plot_area_width(margins, width_px)
                return {"mode": "column", "side": "right",
                        "effective_position": "Right",
                        "need_px": _legend_need_px(names, legend_title,
                                                   "right", axis_px),
                        "axis_px": axis_px,
                        "strip": _legend_strip(names, legend_title, "right",
                                               area if area > 0 else None,
                                               axis_px=axis_px)}
        return _band_plan(fig, names, legend_title, edge, side, None,
                          width_px=width_px, type_scale=type_scale)
    try:
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    key = "l" if side == "left" else "r"
    # Measure against the margins that WILL exist, i.e. after the legend's own
    # side is widened -- otherwise the plot area is over-estimated and the
    # column comes out short by the margin.  The widening floor is
    # axis-bearing vs domain-only aware (``_legend_side_margin``), so a pie
    # is not measured against a 72px margin it will never get.
    probe = dict(m)
    probe[key] = max(_margin_px(probe, key), _legend_side_margin(fig))
    area = _plot_area_width(probe, width_px)
    axis_px = _legend_axis_need_px(fig, side)
    need = _legend_need_px(names, legend_title, side, axis_px)

    # An explicit orientation overrides the pixel heuristic for CORNER docks --
    # a Top/Bottom Left/Right position, which carries BOTH a side and an edge
    # (centred positions already returned above, and the Left/Right docks have
    # edge "middle" and are handled further down):
    #   Horizontal -> a top/bottom band, so a wide row is laid across the edge
    #                  instead of being crammed into a narrow side column (the
    #                  "horizontal legend overlaps the plot" report).
    #   Vertical   -> keep the side column even when it is wider than a band
    #                  would be, simply capped so the plot is never crushed.
    # "Auto" (and every headless caller, which passes the default) falls
    # through to the pixel decision below, so exports are unchanged.
    orient = str(legend_orientation or "Auto").strip().lower()
    if edge in ("top", "bottom"):
        if orient == "horizontal":
            return _band_plan(fig, names, legend_title, edge, side, axis_px,
                              width_px=width_px, type_scale=type_scale)
        if orient == "vertical":
            sized_area = (area if (float(width_px or 0) > 0 and area > 0)
                          else None)
            # Exactly what the labels need, never more.  This used to be
            # ``max(strip, LEGEND_COLUMN_MAX_SHARE)`` -- a 34% FLOOR that
            # reserved a third of the card for a two-item legend and left a
            # slab of empty strip around the pie (the "oversized legend
            # strip" report).  The cap the comment always described is already
            # inside ``_legend_strip`` (``LEGEND_STRIP_MAX``), so the floor
            # only ever over-reserved.
            return {"mode": "column", "side": side, "need_px": need,
                    "axis_px": axis_px,
                    "strip": _legend_strip(names, legend_title, side,
                                           sized_area, axis_px=axis_px)}

    # A CORNER position (Top/Bottom Left/Right) on a DOMAIN figure must not
    # collapse to a horizontal band when its labels are wide.  The band<->column
    # switch is decided by one pixel cliff -- ``need / area`` crossing
    # ``LEGEND_COLUMN_MAX_SHARE`` -- so the SAME corner flips layout as the card
    # is resized, and because the left carries a little more furniture than the
    # right, "Top Left" and "Top Right" flip at DIFFERENT widths (asymmetric at
    # one size).  That flip is the "the pie jumps / looks weird when I resize"
    # report.  A pie/donut has no axes, so a side column is always the clean,
    # stable, overlap-free answer: keep it for every corner at every width.
    # Centred Top/Bottom positions carry no side and keep the band (returned
    # above); cartesian corners keep the pixel heuristic -- test [N].
    if side and edge in ("top", "bottom") and _domain_traces(fig):
        sized_area = (area if (float(width_px or 0) > 0 and area > 0)
                      else None)
        return {"mode": "column", "side": side, "need_px": need,
                "axis_px": axis_px,
                "strip": _legend_strip(names, legend_title, side,
                                       sized_area, axis_px=axis_px)}

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
        # column is kept and simply sized from the labels -- exactly what they
        # need, capped inside ``_legend_strip`` at ``LEGEND_STRIP_MAX`` so an
        # over-long legend on a narrow card can never crush the plot to
        # nothing.  (The old ``max(strip, LEGEND_COLUMN_MAX_SHARE)`` was that
        # cap inverted into a 34% floor, which over-reserved for short labels
        # and left the empty strip around the pie.)
        return {"mode": "column", "side": side, "need_px": need,
                "axis_px": axis_px,
                "strip": _legend_strip(names, legend_title, side, area,
                                       axis_px=axis_px)}

    # Too wide for a column: go horizontal, keeping the requested edge and the
    # requested left/right alignment of the row.
    return _band_plan(fig, names, legend_title, edge, side, axis_px,
                      width_px=width_px, type_scale=type_scale)


def _pie_label_pad_frac(fig, area_h):
    """Fraction of the plot height a pie's OUTSIDE slice labels need beyond
    its domain box, so a reserved band is never painted over by them.

    Plotly draws a pie's outside slice labels OUTSIDE the trace domain --
    projecting below/above the circle and past the box edge. Reserving the
    legend band at exactly the legend's measured height therefore still let
    the bottom/top slice labels land on the legend rows: the "legend overlaps
    the pie in bottom & top centre" report. This returns the extra clearance
    (in plot-height fractions) the caller adds past the band on its edge.

    Returns 0.0 for figures that draw no pie labels, and never more than 0.2
    so a small card is not crushed.
    """
    if area_h <= 0:
        return 0.0
    pad_px = 0.0
    # Every pie counts, not just those with an explicit trace domain: a pie
    # without one follows ``yaxis.domain`` and its outside labels project past
    # that box exactly the same way.
    for tr in (getattr(fig, "data", None) or []):
        try:
            if str(getattr(tr, "type", "")).lower() != "pie":
                continue
            pos = str(getattr(tr, "textposition", None) or "auto").lower()
            if "inside" in pos and "outside" not in pos and "auto" not in pos:
                continue  # labels never leave the box
            size = 0
            for src in (getattr(tr, "outsidetextfont", None),
                        getattr(tr, "textfont", None)):
                try:
                    if src is not None and src.size:
                        size = int(src.size)
                        break
                except Exception:
                    pass
            # ~1.6 line heights of label text plus Plotly's own standoff.
            pad_px = max(pad_px, (size or 12) * 1.6 + 6.0)
        except Exception:
            continue
    return max(0.0, min(0.2, pad_px / float(area_h)))


def reserve_legend_band(fig, legend_position, band_px, height_px=None):
    """Reserve a horizontal legend band along the top or bottom edge.

    The mirror image of ``reserve_legend_column``: instead of shrinking
    ``xaxis.domain`` it shrinks ``yaxis.domain``, so the plot keeps its FULL
    width and gives up only the height the legend rows actually need. That is
    what makes a compact dashboard tile readable -- a side column on a 450px
    card leaves the bars ~150px, while a band costs ~30px of height.

    The legend's own anchor is written here too (``orientation="h"`` and the
    matching x/y/anchor), because in this mode it is a genuinely different
    placement rather than just a different reserved strip.

    ``height_px`` is the live view height, used when the figure carries no
    ``layout.height`` (responsive preview) so the band fraction is resolved
    against the real plot height instead of the reference constant.

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
    try:
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    # The domain fraction is measured against the PLOT-AREA height (card minus
    # the top/bottom margins), NOT the card height and NOT a fixed constant.
    # Converting ``band_px`` against the old ``_LEGEND_BAND_REF_HEIGHT`` (400)
    # constant under-reserved on any card shorter than 400px: the wrapped
    # horizontal legend is taller than the strip the fraction freed, so it
    # overflowed straight down onto a centred pie/donut -- the reported
    # overlap.  The card height is known on the export/dashboard path (the
    # figure carries ``layout.height``); when it is absent (headless build)
    # fall back to the reference so those callers are unchanged.
    base_t = _margin_px(m, "t")
    base_b = _margin_px(m, "b")
    try:
        card_h = float(getattr(fig.layout, "height", 0) or 0)
    except Exception:
        card_h = 0.0
    if card_h <= 0:
        # Responsive preview: no layout.height of its own, so fall back to the
        # live view height the caller measured.
        try:
            card_h = float(height_px or 0)
        except Exception:
            card_h = 0.0
    area_h = (card_h - base_t - base_b) if card_h > (base_t + base_b) \
        else float(_LEGEND_BAND_REF_HEIGHT)
    area_h = max(1.0, area_h)
    # Reserve exactly the band the rows need.  The band lives ENTIRELY in the
    # domain reservation below; the outer margin is deliberately left at its
    # base so the plot area is not shrunk twice (inflating the margin AND the
    # domain ate the band's own space, which is what made the reserved strip
    # too short for the plate -- the overlap).
    #
    # The ``LEGEND_BAND_MAX_SHARE`` cap exists so a band never crushes the plot
    # to nothing.  It is only safe where the caller has a column to fall back
    # to -- a SIDE dock (Top/Bottom Left/Right).  A CENTRED legend (Top/Bottom
    # Center) has no side strip to collapse into, so capping it would leave the
    # plate taller than the band and drop it straight onto the centred pie,
    # which is the exact reported bug.  For a centred position the band is
    # reserved in full (the plot shrinks, but the legend never overlaps it).
    frac = band_px / area_h
    if side:
        frac = min(LEGEND_BAND_MAX_SHARE, frac)
    # A valid domain always needs to leave SOME plot: never reserve the whole
    # card, even for a centred legend with no column fallback.  Past this the
    # legend is genuinely too tall for the card and a sliver of overlap is the
    # least-bad outcome (a zero-height plot is worse).
    frac = min(0.85, frac)
    # A pie/donut's OUTSIDE slice labels are drawn BEYOND its domain box. With
    # the band reserved at exactly the legend's own height, the slice labels at
    # the band edge painted straight over the legend rows -- the "legend
    # overlaps the pie in bottom & top centre" report. Keep an extra
    # allowance clear past the band, sized from the font the labels use
    # (see ``_pie_label_pad_frac``). The legend itself stays anchored in the
    # band proper; only the PLOT is pushed the extra pad away from it.
    pad = _pie_label_pad_frac(fig, area_h)
    total = min(0.95, frac + pad)
    if top:
        m["t"] = int(base_t)
        domain = [0.0, 1.0 - total]
    else:
        m["b"] = int(base_b)
        domain = [total, 1.0]
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
            # Anchor INSIDE the band the domain reservation just freed, at the
            # plot edge -- not at the paper edge. A top band freed the strip
            # y in [1-frac, 1] and a bottom band freed y in [0, frac]; centring
            # the row in that strip puts it flush against the plot border. The
            # old anchor (y=1.0 / y=0.0 with yanchor bottom/top) parked it in
            # the outer MARGIN, straight on top of the x tick labels / axis
            # title -- the "bottom centre legend overlays the axis" report.
            y=(1.0 - frac / 2.0) if top else (frac / 2.0),
            xanchor=place["xanchor"] if place["xanchor"] == "center"
            else ("left" if side == "left" else "right"),
            yanchor="middle",
        ))
    except Exception:
        pass
    return True


# Margins Plotly.js applies when the JSON leaves a side unset.  Measured by
# rendering a figure with no margin block and probing the spine positions:
# l=76/r=80/t=100/b=80 (the 4px on l is the spine's own half-width, so 80 is
# the right default to assume).  Reading unset sides as 0 -- the old
# behaviour -- over-estimated the plotting area by up to 160px, which
# under-sized every strip and mis-anchored every docked plate.
_PLOTLY_DEFAULT_MARGIN = {"l": 80, "r": 80, "t": 100, "b": 80}


def _margin_px(margins, side):
    """One margin side in px, falling back to Plotly.js's own default."""
    try:
        v = (margins or {}).get(side)
        if v is None:
            return float(_PLOTLY_DEFAULT_MARGIN.get(side, 80))
        return float(v)
    except Exception:
        return float(_PLOTLY_DEFAULT_MARGIN.get(side, 80))


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
        l = _margin_px(margin, "l")
        r = _margin_px(margin, "r")
        # Never let a large margin invert or zero out the area; a card that
        # narrow is degenerate and any sane fraction still beats crashing.
        return max(1.0, w - l - r)
    except Exception:
        return width_px


def _domain_edge_paper(fig, side, strip, width_px, axis_px=None):
    """Paper x where a docked frame (legend plate / colour bar) STARTS.

    Two facts pin this down; both were verified by rendering figures and
    measuring the actual pixels:

    1. ``legend.x`` / ``colorbar.x`` are paper coordinates over the PLOTTING
       AREA -- the box inside the outer margins -- which is the SAME ruler an
       ``xaxis.domain`` fraction is measured on (a domain edge at 0.807 and a
       legend.x of 0.807 land on the same pixel column).  So the domain edge
       IS the paper coordinate ``1 - strip`` (Right) / ``strip`` (Left), with
       no margin conversion at all.  The old conversion -- ``(l + edge*area)
       / card_width`` -- mixed the two rulers and parked the plate tens of
       pixels off the plot: ON the axis furniture on some widths, inside the
       plot on others.  That is the "legend still overlaps the right axis
       title" report that survived the first two fixes.

    2. The axis furniture on the docked side -- tick labels and the rotated
       axis title -- is drawn FROM the domain edge OUTWARDS, inside the strip
       the reservation carved.  So the frame must start past it: the
       furniture width (px, measured by ``_legend_axis_need_px``, passed in
       as ``axis_px`` or measured here) is added to the paper coordinate as
       a plotting-area fraction.

    Returns None only when the side is unrecognised.
    """
    try:
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    s = max(0.0, min(1.0, float(strip)))
    try:
        area = float(width_px or 0)
    except Exception:
        area = 0.0
    if area <= 0:
        # Headless (export / dashboard generate): fall back to the reference
        # width, the same basis ``_legend_strip`` sized the strip fraction
        # against, so the anchor and the reservation stay on one ruler.
        area = float(_LEGEND_REF_WIDTH)
    else:
        area = float(_plot_area_width(m, width_px) or area)
    if axis_px is None:
        try:
            axis_px = _legend_axis_need_px(fig, side)
        except Exception:
            axis_px = 0
    try:
        furn = max(0.0, float(int(axis_px or 0))) / max(1.0, area)
    except Exception:
        furn = 0.0
    if side == "right":
        return min(1.0, (1.0 - s) + furn)
    if side == "left":
        return max(0.0, s - furn)
    return None


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


def _domain_square_frac(fig, width_px, height_px=None):
    """Fraction of the plot-area WIDTH to give a domain trace so its box is
    about as wide as the plot is tall -- a square, which is what a pie/donut
    wants -- plus a little room for outside text labels.

    Returns None when the figure has no domain traces or no width to measure
    against (the caller then keeps the full-width domain, the old behaviour).
    A domain trace is centred in its domain box and sized to the box's smaller
    side, so a square box anchored against the legend's side keeps the pie
    right next to the legend at every card width instead of letting it drift to
    the middle of a wide box (the "big gap between chart and legend" report).

    ``height_px`` is the live view height, for the responsive preview where the
    figure deliberately carries NO ``layout.height`` (plotly.js fills the
    container).  Without it the square could never be computed there -- the
    box stayed full-width and the pie centred in it, which is the "gap around
    the pie in the preview" report.  Export/dashboard figures set
    ``layout.height`` before the reservation and are unaffected.
    """
    if not _domain_traces(fig):
        return None
    try:
        w = float(width_px or 0)
    except Exception:
        w = 0.0
    if w <= 0:
        return None
    try:
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
    plot_w = _plot_area_width(m, width_px)
    try:
        card_h = float(getattr(fig.layout, "height", 0) or 0)
    except Exception:
        card_h = 0.0
    if card_h <= 0:
        # Responsive preview: the figure has no height of its own, so use the
        # pixels the caller measured off the view.
        try:
            card_h = float(height_px or 0)
        except Exception:
            card_h = 0.0
    if card_h <= 0:
        return None
    plot_h = max(1.0, card_h - _margin_px(m, "t") - _margin_px(m, "b"))
    try:
        plot_w = float(plot_w or 0)
    except Exception:
        plot_w = 0.0
    if plot_w <= 0:
        return None
    # Square box + ~35% for outside percent/label text; never wider than the
    # whole plot area, and never so thin the pie disappears.
    frac = min(1.0, (plot_h * 1.35) / plot_w)
    return max(0.2, frac)


def reserve_legend_column(fig, legend_position, strip=None,
                          axis_margin=LEGEND_AXIS_MARGIN, names=None,
                          legend_title="", width_px=None, axis_px=None,
                          height_px=None):
    """Carve a dedicated legend column out of *fig*'s plot domain.

    Shrinks the x-domain on the legend's side and widens the matching outer
    margin, so the legend, the data and the axis labels each get their own
    non-overlapping area.  The reserved fraction grows with the longest
    series name (and, on the left, with the axis furniture that shares that
    side) so the legend cannot spill back over the plot or the axis text.

    Pass ``width_px`` (the live view width) to size the column against the
    pixels the labels actually need; without it the reservation falls back to
    a fixed fraction of the card, which is what left the wide-card gap.
    ``height_px`` is the live view height, used only by the pie/donut square
    box when the figure carries no ``layout.height`` of its own (responsive
    preview) -- see ``_domain_square_frac``.

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
    # Unset sides default to Plotly's own margins (see ``_margin_px``) rather
    # than 0, so writing the floor never SHRINKS a margin Plotly would have
    # applied anyway and invalidates every area computed below.
    # The floor itself is domain-aware: a pie has no axis labels to house, so
    # it gets breathing room instead of the 72px axis slab (see
    # ``_legend_side_margin``).
    _floor = _legend_side_margin(fig, default=axis_margin)
    if side == "left":
        m["l"] = int(max(_margin_px(m, "l"), _floor))
    else:
        m["r"] = int(max(_margin_px(m, "r"), _floor))
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
    # A domain figure (pie/donut/treemap/sunburst/funnel) has no axes, so the
    # legend plate is the ONLY thing sharing the reserved strip.  The plan caps
    # the strip at ``LEGEND_STRIP_MAX`` (0.50) -- tuned for a cartesian plot,
    # whose width must never be crushed -- but a compact tile with long labels
    # can need more than that just to HOLD the plate, and a plate wider than its
    # strip is anchored back over the marks (the narrow-tile overlap).  A pie
    # only shrinks within its own box when the strip grows, so for a domain
    # figure the strip is allowed to widen to whatever the plate needs (up to
    # ``LEGEND_STRIP_MAX_DOMAIN``), keeping the marks and the plate disjoint.
    if _domain_traces(fig) and float(width_px or 0) > 0:
        _area0 = _plot_area_width(m, width_px)
        if _area0 and _area0 > 0:
            _plate_frac = ((_legend_plate_px(names, legend_title)
                            + _LEGEND_DOMAIN_GAP_PX) / float(_area0))
            strip = float(max(float(strip),
                              min(LEGEND_STRIP_MAX_DOMAIN, _plate_frac)))
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
    #
    # A domain trace is CENTRED in its domain box and sized to the box's smaller
    # side.  Spanning the box all the way to the far edge ([strip, 1.0]) makes it
    # as wide as the whole remaining card, so on a wide card the pie stays
    # height-limited and drifts to the MIDDLE of that wide box -- leaving a big
    # empty gap between the legend and the pie that grows with the card width
    # (the "big gap between chart & legend" report).  Instead, give the trace a
    # box that is about as wide as the plot is tall (a square, which is what a
    # pie wants) and anchor that box against the legend's side, so the pie sits
    # right next to the legend at every width.  A little extra width is left for
    # outside text labels.
    _sq = _domain_square_frac(fig, width_px, height_px)
    if _sq is not None:
        if side == "left":
            domain = [float(strip), min(1.0, float(strip) + _sq)]
        else:
            domain = [max(0.0, 1.0 - float(strip) - _sq), 1.0 - float(strip)]
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
    # Anchor the plate clear of the plot for EVERY column reservation, not just
    # the squeezed-trace case.  This is the single owner of the legend anchor,
    # so the build-time draw and the later ``refit_legend_width`` (which only
    # re-runs this reservation at the real width) always agree.  legend.x is a
    # paper coordinate over the PLOTTING AREA -- the same ruler the domain
    # fraction is on -- and the axis furniture (tick labels + rotated title)
    # is drawn from the domain edge outwards INSIDE the strip, so the anchor
    # is placed past the furniture (``_domain_edge_paper``); the plate then
    # grows AWAY from the plot, never over the axis text.
    try:
        edge_x = _domain_edge_paper(
            fig, side, strip, width_px,
            axis_px=(axis_px if axis_px is not None
                     else _legend_axis_need_px(fig, side)))
    except Exception:
        edge_x = None
    try:
        if edge_x is not None and float(width_px or 0) > 0:
            _area = _plot_area_width(m, width_px)
            if _area and _area > 0:
                _pw = _legend_plate_px(names, legend_title) / float(_area)
                if _domain_traces(fig):
                    # A domain figure's marks live entirely to one side of the
                    # reserved strip, so the plate must sit at or BEYOND the
                    # domain edge -- never pulled back toward the plot.  The
                    # generic clamp below (``min(edge_x, 1 - plate)``) does the
                    # opposite for a too-wide plate: it drags the anchor back
                    # over the marks to keep the plate on-card, which is exactly
                    # the narrow-tile overlap.  Clamp the anchor to the strip
                    # side instead so the plate always grows AWAY from the pie,
                    # even if its far end then runs a little past the paper edge
                    # (a clipped legend corner is strictly better than a legend
                    # painted over the chart).
                    edge_x = (max(edge_x, 1.0 - float(strip)) if side == "right"
                              else min(edge_x, float(strip)))
                else:
                    # A strip clamped smaller than the labels need (very long
                    # series name on a narrow card) would push the plate past the
                    # paper edge; cap the anchor so the estimated plate still fits
                    # in paper [0, 1] instead of being clipped at the card border.
                    edge_x = (min(edge_x, 1.0 - _pw) if side == "right"
                              else max(edge_x, _pw))
    except Exception:
        pass
    try:
        if edge_x is None:
            x, xanchor = (0.0, "left") if side == "left" else (1.0, "right")
        else:
            x, xanchor = edge_x, ("right" if side == "left" else "left")
        y, yanchor = place["y"], place["yanchor"]
        fig.update_layout(legend=dict(x=x, xanchor=xanchor,
                                      y=y, yanchor=yanchor))
    except Exception:
        log.debug("could not anchor the legend for a column reservation",
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


def _record_legend_layout(fig, position, mode, orientation="Auto"):
    """Remember the position/mode/orientation on the figure so a refit can
    reproduce it."""
    try:
        meta = fig.layout.meta
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["lytrize_legend_position"] = str(position or "")
        meta["lytrize_legend_mode"] = str(mode or "")
        meta["lytrize_legend_orientation"] = str(orientation or "Auto")
        fig.update_layout(meta=meta)
    except Exception:
        log.debug("could not record the legend layout on the figure",
                  exc_info=True)


def infer_legend_orientation(fig):
    """The legend orientation the figure was laid out with ("Auto" if unset).

    ``reserve_legend_band`` writes ``orientation="h"`` and a column leaves
    Plotly's default ``"v"``, so the live anchor alone cannot tell an explicit
    "Vertical" from an "Auto" that happened to pick a column. The name the
    layout was built with is recorded in ``layout.meta`` by
    ``_record_legend_layout`` -- the same authority ``infer_legend_position``
    uses -- so a refit re-plans with the orientation the user actually chose
    instead of snapping a horizontal row back into a side column.
    """
    try:
        meta = fig.layout.meta
        if isinstance(meta, dict):
            orient = str(meta.get("lytrize_legend_orientation") or "").strip()
            if orient:
                return orient
    except Exception:
        pass
    return "Auto"


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
        fig.update_layout(legend=dict(orientation="v"))
    except Exception:
        pass


def apply_legend_layout(fig, legend_position, names=None, legend_title="",
                        width_px=None, legend_orientation="Auto",
                        height_px=None):
    """Reserve space for the legend, choosing column or band from the pixels.

    The single entry point both ``apply_display_options`` (no view width yet)
    and ``refit_legend_width`` (the widget's real width) go through, so the two
    always agree on the rules.

    With no ``width_px`` this is the classic column reservation, which is what
    every headless/export path keeps. With a width it also switches to the
    compact horizontal band when a column would take more than
    ``LEGEND_COLUMN_MAX_SHARE`` of the plot area.

    ``legend_orientation`` ("Auto" / "Vertical" / "Horizontal") lets an
    explicit user choice win over that pixel heuristic on a corner dock, so a
    Horizontal legend is given a top/bottom band instead of being squeezed into
    a side column.  It defaults to "Auto" so every headless caller keeps its
    current output.

    ``height_px`` is the live view height (responsive preview figures carry no
    ``layout.height``); it is only consulted by the pie/donut square box and
    the band fraction, never by a figure that already knows its own height.

    Returns the plan dict (or False) so the caller can tell whether the band
    mode was chosen -- the band writes its own x/y/anchors, which the caller's
    column anchors would otherwise fight.
    """
    if fig is None:
        return False
    _restore_legend_reservation(fig)
    # Same card type factor the typography pass applies to the legend font, so
    # a band reserved here matches the (possibly shrunken) text that is drawn
    # on a compact card -- see ``_type_scale``.
    _tscale = _type_scale(width_px, height_px)
    plan = plan_legend_layout(fig, legend_position, names=names,
                              legend_title=legend_title, width_px=width_px,
                              legend_orientation=legend_orientation,
                              type_scale=_tscale)
    if not plan:
        return False
    if plan.get("mode") == "band":
        ok = reserve_legend_band(fig, legend_position, plan.get("band_px"),
                                 height_px=height_px)
    else:
        ok = reserve_legend_column(
            fig, plan.get("effective_position", legend_position),
            names=names, legend_title=legend_title,
            width_px=width_px,
            strip=plan.get("strip"),
            axis_px=plan.get("axis_px"),
            height_px=height_px,
        )
    if ok:
        _record_legend_layout(fig, legend_position, plan.get("mode"),
                              legend_orientation)
        return plan
    return False


def _reapply_type_scale(fig, width_px, height_px=None):
    """Re-scale the legend + pie-label fonts to the card's current width.

    ``refit_legend_width`` is called on every resize and only re-lays the
    legend's GEOMETRY; the text stays at whatever size it was generated with.
    That is the "resize the card small and the legend/label text doesn't
    adjust" report -- the words keep the large font and spill off the smaller
    plot.  This re-derives the type factor for the new width and rewrites the
    legend font and the pie slice-label font to match (see ``_type_scale`` and
    ``_horizontal_fit_scale``).

    Idempotent: the ORIGINAL (un-scaled) sizes are stashed in ``layout.meta``
    on the first call and every later call re-scales from that stash, so a
    resize storm converges instead of compounding shrinks.  A figure that was
    never typography-styled (``legend.font.size`` missing) is left alone, so
    this never invents a font the figure did not have.
    """
    if fig is None:
        return False
    try:
        w = float(width_px or 0)
    except Exception:
        return False
    if w <= 0:
        return False
    try:
        legend = fig.layout.legend
        if legend is None or legend.font is None or legend.font.size is None:
            return False  # no styled legend font to scale
        meta = fig.layout.meta
        if not isinstance(meta, dict):
            meta = {} if meta is None else None
        if meta is None:
            return False
        # Stash the original sizes once so a later resize re-scales from the
        # user's chosen size, not from an already-shrunk one.
        if "lytrize_base_legend_font" not in meta:
            meta["lytrize_base_legend_font"] = int(legend.font.size)
            try:
                if legend.title is not None and legend.title.font is not None \
                        and legend.title.font.size is not None:
                    meta["lytrize_base_legend_title_font"] = int(legend.title.font.size)
            except Exception:
                pass
        base = int(meta.get("lytrize_base_legend_font") or legend.font.size)

        tscale = _type_scale(w, height_px)
        lscale = tscale
        try:
            orient = infer_legend_orientation(fig)
        except Exception:
            orient = "Auto"
        if str(orient or "Auto").strip().lower() == "horizontal":
            try:
                _ltitle = ""
                try:
                    if legend.title is not None:
                        _ltitle = str(getattr(legend.title, "text", "") or "")
                except Exception:
                    pass
                lscale = tscale * _horizontal_fit_scale(_legend_names(fig), fig, w,
                                                        legend_title=_ltitle)
            except Exception:
                lscale = tscale

        f = {"size": _scale_size(base, lscale)}
        # Preserve the family/colour already on the legend so only the size
        # changes; a font dict carrying only size resets those to defaults.
        try:
            if legend.font.family:
                f["family"] = legend.font.family
            if legend.font.color:
                f["color"] = legend.font.color
        except Exception:
            pass
        fig.update_layout(legend=dict(font=f))

        if "lytrize_base_legend_title_font" in meta:
            try:
                tf = {"size": _scale_size(
                    int(meta["lytrize_base_legend_title_font"]), lscale)}
                fig.update_layout(legend=dict(title=dict(font=tf)))
            except Exception:
                pass

        # Pie/donut slice labels scale with the card too.
        try:
            for tr in fig.data:
                if str(getattr(tr, "type", "")).lower() != "pie":
                    continue
                if getattr(tr, "insidetextfont", None) is not None \
                        and tr.insidetextfont.size is not None:
                    tr.insidetextfont.size = _scale_size(tr.insidetextfont.size,
                                                         tscale)
                if getattr(tr, "outsidetextfont", None) is not None \
                        and tr.outsidetextfont.size is not None:
                    tr.outsidetextfont.size = _scale_size(tr.outsidetextfont.size,
                                                          tscale)
        except Exception:
            pass
        return True
    except Exception:
        log.debug("could not re-apply type scale", exc_info=True)
        return False


def refit_legend_width(fig, width_px, height_px=None):
    """Re-lay the legend out against the real view width.

    Figures are built on a worker thread with no view, so the first reservation
    has to assume a card width. Once the widget is on screen its true width is
    known, and the legend can be sized against the pixels the labels actually
    need -- that is what removes the wide gap between the plot and the legend on
    a full-width chart -- and can also switch to the compact band layout when
    the card is too narrow for a column at all.

    ``height_px`` is the live view height, passed on for the pie/donut square
    box and the band fraction: responsive preview figures carry no
    ``layout.height`` of their own, so without it those two fall back to the
    reference constants and the preview pie is not squared to its box.

    Idempotent and reversible: the previous reservation is cleared first, so
    repeated calls (a resize storm, a re-gridded dashboard, a full-width
    toggle) converge on the layout the current width calls for instead of
    compounding strips or leaving a stale band behind.

    Returns True when the layout was rewritten. A figure with no legend box is
    left untouched.
    """
    if fig is None or not width_px or width_px <= 0:
        return False
    # Re-scale the legend + pie-label fonts to the new width too, so a resize
    # shrinks the text with the card (see ``_reapply_type_scale``) instead of
    # leaving the large font spilling off a smaller plot.
    _reapply_type_scale(fig, width_px, height_px=height_px)
    pos = infer_legend_position(fig)
    if not pos:
        return False
    try:
        title = ""
        legend = fig.layout.legend
        if legend is not None and legend.title is not None:
            title = str(getattr(legend.title, "text", "") or "")
        names = _legend_names(fig)
        orient = infer_legend_orientation(fig)
    except Exception:
        return False
    return bool(apply_legend_layout(fig, pos, names=names,
                                    legend_title=title,
                                    width_px=width_px,
                                    legend_orientation=orient,
                                    height_px=height_px))


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
    """Retired Plotly hook; map colors are now ChartModel display data."""
    return None

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
    # Only carve the strip when a colour bar is actually drawn. This ran
    # unconditionally for every heatmap/map, so a chart whose colour bar is
    # hidden (showscale/show_colorbar off) still reserved a side strip for a
    # bar that never appears -- the "big empty gap beside the chart" report.
    # ``real_colorbars`` is the authoritative "will Plotly draw one" check,
    # and ``show_colorbar`` (Map Plot) is honoured as an explicit override.
    _cb_wanted = lo.get("show_colorbar", True)
    if _cb_wanted is not False and list(real_colorbars(fig)):
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
        # ``dict(fig.layout.margin)`` raises ValueError on some Plotly versions
        # (see the note in ``reserve_legend_column``); that made the colour-bar
        # strip overwrite l/r/t/b with bare values and reset the chart's
        # margins.  Read ONCE, up front: the strip fraction below resolves
        # against the plot area these margins define.
        m = fig.layout.margin.to_plotly_json() or {}
    except Exception:
        m = {}
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
        # The axis furniture on the bar's side -- tick labels AND the rotated
        # axis title -- is drawn FROM the domain edge outwards, i.e. inside
        # the very strip being freed.  On a heatmap's Left the labels are the
        # COLUMN NAMES ("transaction_qty"), so they are long: without leaving
        # room for them the strip is only wide enough for the bar itself and
        # the labels (and the title) end up underneath it.  The extra
        # separating gap matters on the Left only: Right has the plot edge to
        # sit against, but on Left the labels sit BETWEEN the bar and the
        # cells, so without a gap the longest label is drawn flush against
        # the bar.  ``_domain_edge_paper`` skips the same measurement when it
        # anchors the bar, so sizing and anchoring always agree.
        need_px += _legend_axis_need_px(fig, side)
        if side == "left":
            need_px += _COLORBAR_LABEL_GAP_PX
        # A strip is an xaxis.domain fraction, i.e. a share of the PLOT AREA
        # (card minus the outer margins), not of the whole card -- resolve the
        # pixel need against that area, exactly like the legend column does.
        _area = _plot_area_width(m, width_px)
        if int(width_px or 0) > 0 and _area and float(_area) > 0:
            strip = need_px / float(_area)
        else:
            # No width to measure against: fall back to a fraction that covers
            # the bar plus a generous axis allowance.
            strip = min(0.28, need_px / 300.0)
        # Clamp so a narrow card cannot be squeezed to nothing and a very wide
        # one does not leave an absurd gap.
        strip = max(0.05, min(0.30, strip))
    except Exception:
        strip = 0.10
    if side == "left":
        m["l"] = int(max(_margin_px(m, "l"), 60.0))
        domain = [strip, 1.0]
    else:
        m["r"] = int(max(_margin_px(m, "r"), 60.0))
        domain = [0.0, 1.0 - strip]
    try:
        fig.update_layout(margin=m, xaxis=dict(domain=domain))
    except Exception:
        return False
    # Anchor the bar clear of the axis furniture.  colorbar.x is a paper
    # coordinate over the plotting area -- the same ruler the domain fraction
    # is on -- and the furniture (tick labels + rotated title) is drawn from
    # the domain edge outwards INSIDE the freed strip, so ``_domain_edge_paper``
    # places the bar past it.  The old bare ``x=1.0`` (Right) / ``x=0.0``
    # (Left) parked the bar in the outer margin and left the whole reserved
    # strip as a dead gap beside the chart -- the same "keep it close to the
    # chart border" report the legend had.
    try:
        cb_pos = _domain_edge_paper(fig, side, strip, width_px)
    except Exception:
        cb_pos = None
    if cb_pos is None:
        cb_pos = 0.0 if side == "left" else 1.0
    # The bar grows AWAY from the plot: on the Right it sits left of the plot
    # edge and extends right (xanchor left); on the Left it sits right of the
    # plot edge and extends left (xanchor right).
    cb_anchor = "right" if side == "left" else "left"
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

# Trace types whose legend entries come from a per-trace ``labels`` ARRAY
# rather than from one trace per series. A pie / donut / funnel-area is a
# SINGLE trace carrying every category, and its ``name`` is normally empty --
# so a name-only test reports "no legend" for the one chart type that always
# has one. That is why every legend option on the Pie & Donut page was inert.
# Sunburst / treemap / icicle also carry ``labels`` but have no ``showlegend``
# property at all (Plotly never gives them a legend box), so they are excluded
# by the ``showlegend`` check below rather than listed here.
_LABEL_LEGEND_TYPES = ("pie", "funnelarea")


def _legend_names(fig):
    """The legend-entry labels *fig* will actually paint, in order.

    One entry per trace name for the usual cartesian types; one entry per
    ``labels`` item for a pie / donut / funnel-area, whose single trace holds
    every category and usually has no name at all.

    The names are what the legend reservation is MEASURED against, so this
    has to be the real entry list: sizing a pie's column from ``[""]``
    reserves a strip far too narrow for the swatches it is about to hold and
    drops the plate back over the marks.
    """
    out = []
    for tr in getattr(fig, "data", None) or []:
        if str(getattr(tr, "type", "")).lower() in _NO_LEGEND_TYPES:
            continue
        if getattr(tr, "showlegend", None) is False:
            continue
        if str(getattr(tr, "type", "")).lower() in _LABEL_LEGEND_TYPES:
            labels = getattr(tr, "labels", None)
            if labels is not None:
                out.extend(str(x) for x in labels)
            continue
        nm = str(getattr(tr, "name", "") or "").strip()
        if nm:
            out.append(nm)
    return out


def _figure_has_legend(fig):
    """True when at least one trace will actually paint a visible legend entry.

    A heatmap has no legend at all. Just as important, a figure whose only
    visible traces carry no ``name`` paints an EMPTY legend box -- Plotly
    draws nothing in it -- so reserving a column/band for it only steals
    width from the plot and leaves the "big empty gap" beside a single
    unnamed series. So a legend counts only when some visible, non-heatmap
    trace names itself, or (for a pie / funnel-area) carries ``labels``.

    Reads through ``_legend_names`` so the entry LIST and the yes/no answer
    can never disagree: the names are also what the reservation is sized
    against.
    """
    return bool(_legend_names(fig))


def _apply_pie_options(fig, lo, type_scale=1.0):
    """Apply the Pie & Donut Layout options to every pie trace on *fig*.

    Guarded per trace exactly like the histogram and heatmap blocks: one Layout
    tab serves every chart type, and a pie trace is the only thing that has a
    ``hole``, so a bar or line trace is never handed one.

    ``donut_hole`` is absolute rather than relative. The runner picks 0.45 for a
    donut, but the option defaults to 0, and writing that default is correct --
    it is what turns a donut back into a full pie, which is exactly what the
    control is for.

    ``type_scale`` shrinks the slice-label font on a small card so outside
    labels don't overflow the shrunken plot (see ``_type_scale``); 1.0 leaves
    the chosen size untouched.
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
            _vsize = _scale_size(lo.get("pie_value_size"), type_scale)
            tr.insidetextfont = _font_kwargs(_vsize, lo.get("pie_value_color"))
            tr.outsidetextfont = _font_kwargs(_vsize, lo.get("pie_value_color"))
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


def _contrast_on(bg):
    """Readable header text for a band painted *bg*: white on dark, ink on light."""
    try:
        s = str(bg).strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
        # Rec.601 luma without gamma -- enough to split a swatch picker's
        # colours into "needs white text" and "needs dark text".
        return "#1e293b" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#ffffff"
    except Exception:
        return "#ffffff"


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
        # -- Body font: table_font_color IS a text colour -----------------
        sz, col = lo.get("table_font_size"), lo.get("table_font_color")
        if sz is not None or col is not None:
            try:
                d = _font_kwargs(sz, col)
                d["family"] = fam
                cells.font = dict(d)
            except Exception:
                pass
        # -- Header band + header text ------------------------------------
        # ``table_header_color`` is the header BAND colour: the runner reads
        # the same key as fill, the Layout swatch sits with the stripe
        # colours, and the old app wrote it to ``header.fill``. Writing it
        # into ``header.font`` instead painted #6163df text on the #6163df
        # band -- invisible in BOTH modes, with only the odd palette column
        # (a pale alternate fill) showing a murky glyph. The band takes the
        # swatch now; the TEXT colour is re-derived from the band's luminance
        # so any swatch stays legible.
        try:
            hdr_font = dict(hdr.font.to_plotly_json() or {})
            hsz = lo.get("table_header_font_size")
            if hsz is not None:
                hdr_font["size"] = int(hsz)
            hdr_font["family"] = fam
            hc = lo.get("table_header_color")
            n_hdr = len(getattr(hdr, "values", None) or [])
            if hc and n_hdr:
                hdr.fill = dict(color=[str(hc)] * n_hdr)
                hdr_font["color"] = _contrast_on(str(hc))
            hdr.font = hdr_font
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
                    # Mirror the cells: the index header sits over the
                    # left-aligned index column, each data header over its
                    # right-aligned numbers. The flat string flattened the
                    # whole band to one side.
                    hdr.align = [str(ia or "left")] + [str(da or "right")] * (n - 1)
            except Exception:
                pass
        ev, od = lo.get("table_stripe_even_color"), lo.get("table_stripe_odd_color")
        if ev or od:
            try:
                # Plotly paints cells.fill.color COLUMN-major ([col][row]),
                # so the old flat two-colour list gave column 0 the odd
                # stripe and every later column the even one -- a two-tone
                # column effect with no row stripes at all. Build the
                # alternation per ROW and repeat it across the columns
                # (the same shape the old app wrote).
                cols_vals = list(getattr(cells, "values", None) or [])
                n_rows = max((len(c) for c in cols_vals), default=0)
                n_cols = max(len(cols_vals), 1)
                row_fill = [str(ev or "#1e293b") if i % 2 == 0
                            else str(od or "#0f172a") for i in range(n_rows)]
                cells.fill = dict(color=[list(row_fill) for _ in range(n_cols)])
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

        # -- Column widths follow the content ------------------------------
        # Recomputed AFTER the number-format rebuild above so the weights
        # match the strings now on screen ("72,667.00" is wider than
        # "72,667"). One owner for the rule: the same helper the runner uses,
        # so build time and re-style time always agree.
        try:
            from modules.analysis.matrix_table import fit_columnwidth
            cw = fit_columnwidth(list(getattr(cells, "values", None) or []),
                                 list(getattr(hdr, "values", None) or []))
            if cw:
                tr.columnwidth = cw
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
    # the legend AND of the colour bar/colour range.  It defaults to False:
    # an unframed legend/colorbar is the clean production look, and the
    # toggle still re-enables the frame per chart. Saved sessions that
    # inherited the old True default are migrated by
    # database._migrate_show_border_default.
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
    return {"title": "", "x_label": "", "y_label": "", "legend_title": "", "legend_labels": {}, "show_value_labels": True, "value_label_color": "#ffffff", "label_position": "outside", "bar_mode": "group", "bar_gap": 0.28, "histogram_bins": 30, "histogram_opacity": 0.80, "show_grid": True, "show_border": False, "legend_position": "Top Right", "legend_orientation": "Auto", "legend_order": "Normal", "legend_itemclick": "toggle", "heatmap_show_text": True, "heatmap_colorscale": "RdBu", "heatmap_annotation_size": 10, "heatmap_annotation_precision": 2, "heatmap_annotation_color": "#ffffff", "colorbar_position": "Right", "colorbar_orientation": "Auto", "colorbar_title": "", "colorbar_tick_size": 10, "colorbar_tick_color": "#94a3b8", "colorbar_title_size": 11, "colorbar_title_color": "#cbd5e1", "donut_hole": 0.0, "pie_textinfo": "label+percent", "pull_slices": 0.0, "pie_rotation": 0, "pie_direction": "clockwise", "pie_value_size": 11, "pie_value_color": "#e2e8f0", "line_shape": "linear", "line_fill": "none", "show_markers": True, "marker_opacity": 0.8, "marker_size": 6, "render_mode": "svg", "table_font_size": 11, "table_header_font_size": 12, "table_row_height": 26, "table_header_height": 28, "table_index_align": "left", "table_data_align": "right", "table_number_format": ",.2f", "table_font_color": "#f1f5f9", "table_header_color": "#6163df", "table_stripe_even_color": "#1e293b", "table_stripe_odd_color": "#0f172a", "show_colorbar": True, "size_by_value": False}

def default_typo_opts():
    # Sizes mirror the readouts on the Typography Options tab of
    # chart_designs.pdf (header 28 / title 16 / axis 12 / legend 11).
    # header_align places the header across the full canvas width (see
    # _HEADER_ANCHORS); "left" is the default so existing charts are
    # unchanged, and it auto-adjusts to whatever the chart area is.
    return {"family": "Inter", "font_style": "Normal", "header_size": 28, "header_color": "#6163df", "header_family": "Inter", "header_font_style": "Normal", "header_align": "left", "axis_title_size": 16, "axis_title_color": "#cbd5e1", "axis_tick_size": 12, "axis_tick_color": "#94a3b8", "legend_title_size": 12, "legend_title_color": "#cbd5e1", "legend_item_size": 11, "legend_item_color": "#e2e8f0", "value_label_size": 11, "value_label_color": "auto", "tooltip_size": 12, "tooltip_color": "auto"}

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
                          width_px=None, height_px=None):
    """Apply Layout + Typography options to a copy of *fig*.

    Every call re-derives from the figure it is given, so callers should
    always pass their original (pre-display-options) figure -- applying
    new options on top of an already-styled figure would compound.

    ``width_px`` / ``height_px`` are the live view size (the preview widget
    knows both); they size the reserved legend column/band and the pie/donut
    square box against the pixels the card really has.  Headless callers pass
    neither and keep the classic fixed-fraction layout.
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
    # The card's type scale is computed once here and shared by the pie slice
    # labels and the legend text below, so everything shrinks together on a
    # small tile (see ``_type_scale``).
    _tscale = _type_scale(width_px, height_px)
    _apply_pie_options(f2, lo, type_scale=_tscale)
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
    # legend plate and the colour bar / colour range.  Default False is the
    # clean production look; this only draws a frame when checked.
    show_border = bool(lo.get("show_border", False))
    # Only reserve a legend column when a legend is really drawn. A heatmap
    # (correlation) has none, yet ``legend_position`` still carries a default,
    # so this used to reserve a column for a legend that never appears -- and
    # because it writes xaxis.domain it also clobbered the colour-bar strip
    # reserved moments earlier, which is why a left-hand colour bar ended up
    # on top of the y tick labels.
    if place and (_figure_has_legend(f2) or lo.get("legend_title")):
        # Reserve the column AFTER the xaxis showgrid/title updates above,
        # so this domain is not clobbered by a later update_layout.
        #
        # ``apply_legend_layout`` is the shared entry point (the same one the
        # widget's resize refit calls), so a chart first drawn at an assumed
        # width and then refit to its real width always ends up on the same
        # rule rather than two different ones.  It -- via reserve_legend_column
        # / reserve_legend_band -- is the SINGLE owner of the legend geometry
        # (x/y/anchors): it converts the reserved domain strip into the paper
        # coordinate the plate is anchored at, so the plate hugs the plot edge
        # instead of floating in the card margin or sitting on the marks.  All
        # that is left here is the plate's own look.
        _plan = apply_legend_layout(
            f2, lp,
            # The entry list, not just trace names: a pie/donut is ONE trace
            # whose legend rows are its ``labels``, so a name-only list is
            # ``[""]`` and the column gets sized for nothing at all.
            names=_legend_names(f2),
            legend_title=lo.get("legend_title", ""),
            width_px=width_px,
            # The user's Auto/Vertical/Horizontal choice: on a corner dock an
            # explicit Horizontal needs a top/bottom band, not a side column a
            # later orientation pass would squeeze a horizontal row into.
            legend_orientation=lo.get("legend_orientation", "Auto"),
            # Live view height for the pie/donut square box and the band
            # fraction (responsive preview figures carry no layout.height).
            height_px=height_px,
        )
        # Transparent by default for any chart: no plate at all, so the card
        # and grid show through exactly as they do around the plot.  Only the
        # look is set here; the position is owned by the reservation above.
        f2.update_layout(legend=dict(
            bgcolor="rgba(0, 0, 0, 0)",
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

    # Outside text on a multi-slice pie piles up around small wedges, even
    # when the legend has its own reserved band. Keep the requested label
    # content and hover data, but place it inside and let Plotly hide labels
    # that cannot fit. The legend remains the readable full category key.
    if (lo.get("show_value_labels")
            and str(lo.get("label_position", "outside")).lower() == "outside"):
        for tr in f2.data:
            if str(getattr(tr, "type", "")).lower() != "pie":
                continue
            try:
                labels = list(getattr(tr, "labels", None) or [])
                if len(labels) >= 7:
                    tr.textposition = "inside"
                    f2.update_layout(uniformtext=dict(minsize=8, mode="hide"))
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

    # Legend items + legend title ("Legend size/color").  Both are scaled by the
    # card's type factor so a small card's legend text doesn't overflow (see
    # ``_type_scale``).  A HORIZONTAL legend is scaled FURTHER by
    # ``_horizontal_fit_scale`` -- the point of choosing Horizontal is a single
    # row, so the font shrinks until the entries fit one row instead of wrapping
    # and colliding (the "only stays horizontal if I widen the card" report).
    # A card at/above the full-size anchor with a fitting legend keeps the exact
    # chosen size, so wide cards and headless export are unchanged.
    _lorient = str(lo.get("legend_orientation", "Auto") or "Auto").strip().lower()
    _lscale = _tscale
    if _lorient == "horizontal" and width_px:
        try:
            _lscale = _tscale * _horizontal_fit_scale(
                _legend_names(f2), f2, width_px,
                legend_title=lo.get("legend_title", ""))
        except Exception:
            _lscale = _tscale
    lf = _font_kwargs(_scale_size(t.get("legend_item_size"), _lscale),
                      t.get("legend_item_color"))
    ltf = _font_kwargs(_scale_size(t.get("legend_title_size"), _lscale),
                       t.get("legend_title_color"))
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
        _mode = legend_layout_mode(f2)
        if _mode in ("column", "band"):
            # The selected geometry is authoritative. In particular, a
            # horizontal preference can be adapted to a vertical side column
            # when the available card width cannot hold one readable row.
            f2.update_layout(legend=dict(orientation="h" if _mode == "band" else "v"))
        elif _want_orient in ("vertical", "horizontal"):
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
        # Transparent by default for any chart (the requested look): the
        # legend draws no plate in either mode -- only the border below is
        # mode-aware, and only when show_border draws a frame at all.
        "legend_bg": "rgba(0, 0, 0, 0)",
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
        "legend_bg": "rgba(0, 0, 0, 0)",
        "legend_border": "rgba(100, 116, 139, 0.45)",
    },
}

# Dark-mode default text colours -> their light-mode equivalents.  Matched
# case-insensitively on the exact string, so anything not listed here (i.e. a
# colour chosen from the Typography swatches) survives the switch untouched.
_LIGHT_TEXT_REMAP = {
    "#f1f5f9": "#1e293b",   # Pivot Table body text (table_font_color default)
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

    # -- Legend background (transparent by default) + legend text ------------
    try:
        leg = dict(f2.layout.legend.to_plotly_json() or {})
        # Transparent for ANY chart: no plate is drawn, so the card/grid
        # shows through. The token carries it so both modes say the same
        # thing -- there is nothing mode-dependent left to paint.
        leg["bgcolor"] = tok["legend_bg"]
        if int(leg.get("borderwidth") or 0) > 0:
            leg["bordercolor"] = tok["legend_border"]
        # Legend item/legend title text has to follow the mode WITHOUT a
        # plate to sit on: it lands directly on the card, so a near-white
        # default would vanish on a light card (and dark ink on a dark one).
        # Same rule as the axes: a known dark default is remapped, a
        # user-picked colour is not.
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
        # A pie / donut / funnel-area labels its slices from ``textinfo`` and
        # picks the font from ``insidetextfont`` / ``outsidetextfont``, NOT
        # from the generic ``textfont`` -- and the pie Layout options write
        # the DARK default (``pie_value_color`` = #e2e8f0) into exactly those
        # two. The generic pass below therefore never saw the colour that is
        # actually drawn, so a light-mode pie painted near-white labels on a
        # white canvas: the "values are washed out" report. Same rule as
        # everywhere else -- a known dark default is remapped, a colour the
        # user picked from the Typography swatches is not.
        for attr in ("insidetextfont", "outsidetextfont"):
            try:
                tf = getattr(tr, attr, None)
                new = _themed_text(tf.color if tf is not None else None, key)
                if tf is not None and new is not None and new != tf.color:
                    tf.color = new
            except Exception:
                pass
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

    # -- Pivot Table (go.Table) -----------------------------------------
    # The table runner hard-codes a DARK palette (near-black row stripes
    # #1e293b/#0f172a, near-white body text #f1f5f9) and never re-reads the
    # mode, so a light-mode table rendered as a dark slab with washed text --
    # the "defaults to dark even in light mode / unreadable header" report.
    # theme_figure is the one render-time hook every other chart type already
    # flows through, so the table is re-tinted here too: in light mode the
    # body becomes a white/near-white canvas with dark text, while the indigo
    # header band keeps its fill (indigo + white header text stays legible on
    # both modes). A user-picked header/stripe colour is NOT a known dark
    # default, so it is left untouched, exactly like every other swatch.
    _theme_table(f2, key)
    return f2


# Row-stripe fills the pivot runner writes by default (matrix_table.py), mapped
# to their light-mode equivalents. Anything else (a colour the user picked from
# the Layout swatches) is not in this table and survives the switch unchanged.
_LIGHT_TABLE_FILL_REMAP = {
    "#1e293b": "#f1f5f9",   # even stripe -> light slate
    "#0f172a": "#ffffff",   # odd stripe  -> white
    "#0f2a3a": "#e2e8f0",   # value column even
    "#0a1e2a": "#f8fafc",   # value column odd
}


def _remap_table_fill(color):
    """Map a fill colour -- or a nested [col][row] array -- to its light twin."""
    if isinstance(color, (list, tuple)):
        return [_remap_table_fill(c) for c in color]
    return _LIGHT_TABLE_FILL_REMAP.get(str(color).strip().lower(), color)


def _theme_table(fig, key):
    """Re-tint a Pivot Table's body fills/text for the active app mode.

    Only the DARK DEFAULTS are remapped; a colour chosen in the Layout tab is
    passed through, matching how every axis/legend/colorbar swatch behaves.
    """
    if key != "light":
        return
    for tr in getattr(fig, "data", []) or []:
        if str(getattr(tr, "type", "")).lower() != "table":
            continue
        # -- Body text: near-white default -> dark on a light canvas -------
        try:
            cells = tr.cells
            cf = dict(cells.font.to_plotly_json() or {})
            new = _themed_text(cf.get("color"), key)
            if new and new != cf.get("color"):
                cf["color"] = new
                cells.font = cf
        except Exception:
            pass
        # -- Row-stripe fills -> light equivalents -------------------------
        # The stripe pass writes the fill array COLUMN-major ([col][row]), so
        # the remap walks nested arrays as well as the old flat form.
        try:
            cells = tr.cells
            fills = cells.fill.to_plotly_json() or {}
            color = fills.get("color")
            if isinstance(color, list):
                mapped = _remap_table_fill(color)
                if mapped != color:
                    cells.fill = dict(fills, color=mapped)
        except Exception:
            pass

