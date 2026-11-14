"""backend/chart_display.py -- ECharts-native Layout + Typography applier.

Replaces the Plotly-specific layout/typography logic with ECharts-native
equivalents. All styling is expressed through the ChartModel ``display``
dict, which the ECharts JavaScript adapter (``lytrize_adapter.js``) reads
at render time.

Public API is intentionally kept compatible with the old Plotly module so
existing callers (analysis settings widgets, theme, tests) continue to work.
"""
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
_FONT_FALLBACKS = [
    ("Liberation Sans", "sans-serif"), ("DejaVu Sans", "sans-serif"),
    ("Liberation Serif", "serif"), ("DejaVu Serif", "serif"),
    ("Liberation Mono", "monospace"), ("DejaVu Sans Mono", "monospace"),
]

_installed_families_cache: Optional[set] = None


def installed_font_families() -> set:
    """Family names fontconfig can actually resolve on this machine."""
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
            name = line.strip()
            if name:
                families.add(name)
    except Exception:
        pass
    _installed_families_cache = families
    return families


def font_is_available(name: str) -> bool:
    """True when *name* resolves via fontconfig."""
    return name in installed_font_families()


def available_fonts(prefer: Optional[Iterable[str]] = None) -> list:
    """Return installable font choices, filtered to what fontconfig resolves."""
    prefer = set(prefer or [])
    seen: set = set()
    out: list = []
    for family, generic in _FONT_CHOICES + _FONT_FALLBACKS:
        if family in seen:
            continue
        seen.add(family)
        if font_is_available(family) or not prefer:
            out.append({"family": family, "generic": generic})
    # Prefer requested fonts first
    if prefer:
        pref_out = [f for f in out if f["family"] in prefer]
        rest = [f for f in out if f["family"] not in prefer]
        out = pref_out + rest
    return out


def _generic_for(name: str) -> str:
    for family, generic in _FONT_CHOICES + _FONT_FALLBACKS:
        if family == name:
            return generic
    return "sans-serif"


# ── ECharts layout constants (replacing Plotly-specific ones) ──────────────

LEGEND_STRIP = 0.26
LEGEND_STRIP_MIN = 0.20
LEGEND_STRIP_MAX = 0.50
LEGEND_STRIP_MAX_DOMAIN = 0.62
LEGEND_AXIS_MARGIN = 72
LEGEND_AXIS_MARGIN_DOMAIN = 12
LEGEND_COLUMN_MAX_SHARE = 0.34
LEGEND_BAND_MAX_SHARE = 0.30


def _type_scale(width_px=None, height_px=None):
    """Return a font-size scale factor based on canvas size."""
    if width_px and width_px < 400:
        return 0.85
    if width_px and width_px < 600:
        return 0.92
    return 1.0


def _scale_size(size, factor):
    return max(7, int(size * factor))


# ── Default options (API-compatible with old module) ───────────────────────

def default_layout_opts() -> dict:
    """Return default layout options for an ECharts chart model."""
    return {
        "title": "",
        "x_label": "",
        "y_label": "",
        "y2_label": "",
        "legend_title": "",
        "legend_position": "Top Right",
        "legend_orientation": "Auto",
        "legend_order": "Normal",
        "legend_itemclick": "toggle",
        "show_value_labels": True,
        "label_position": "outside",
        "bar_mode": "group",
        "bar_gap": 0.28,
        "line_width": 2,
        "show_grid": True,
        "show_border": False,
        "border_color": "#1e293b",
        "border_width": 1,
        "border_radius": 10,
        "padding": 12,
        "title_align": "left",
        "title_position": "top",
        "title_gap": 16,
        "show_legend": True,
        "show_colorbar": False,
        "colorbar_position": "right",
        "x_axes": "bottom",
        "y_axes": "left",
        "grid_left": "10%",
        "grid_right": "10%",
        "grid_top": "15%",
        "grid_bottom": "15%",
    }


def default_typo_opts() -> dict:
    """Return default typography options for an ECharts chart model."""
    return {
        "family": "Inter",
        "header_family": "Inter",
        "header_size": 20,
        "header_font_style": "Bold",
        "header_color": "auto",
        "header_align": "left",
        "font_size": 12,
        "font_style": "Normal",
        "font_color": "auto",
        "axis_title_size": 14,
        "axis_title_color": "auto",
        "axis_tick_size": 11,
        "axis_tick_color": "auto",
        "legend_item_size": 11,
        "legend_item_color": "auto",
        "legend_title_size": 11,
        "legend_title_color": "auto",
        "value_label_size": 11,
        "value_label_color": "auto",
        "tooltip_size": 12,
        "tooltip_color": "auto",
        # Legacy aliases
        "header_font_family": "Inter",
        "header_font_size": 20,
        "header_font_color": "auto",
        "font_family": "Inter",
        "axis_label_font_size": 11,
        "tick_label_font_size": 11,
        "legend_font_size": 11,
        "data_label_font_size": 11,
    }


# ── Font helpers ───────────────────────────────────────────────────────────

def _font_stack(name: str) -> str:
    return FONT_STACKS.get(name, name)


def _style_dict(family, font_style):
    return {"family": family, "style": font_style}


def header_font_css(typo_opts):
    family = typo_opts.get("header_font_family", "Inter")
    size = typo_opts.get("header_font_size", 20)
    style = typo_opts.get("header_font_style", "Normal")
    weight = "700" if "Bold" in style else "400"
    italic = "italic" if "Italic" in style else "normal"
    return f"'{family}', {_generic_for(family)}; font-size: {size}px; font-weight: {weight}; font-style: {italic};"


def _font_kwargs(size=None, color=None):
    d = {}
    if size is not None:
        d["fontSize"] = size
    if color is not None:
        d["color"] = color
    return d


def _weight_style(font_style):
    weight = "bold" if "Bold" in font_style else "normal"
    style = "italic" if "Italic" in font_style else "normal"
    return weight, style


# ── Theme (ECharts-native) ─────────────────────────────────────────────────

CHART_THEME = {
    "dark": {
        "canvas": "#0f172a",
        "text": "#f8fafc",
        "tick_text": "#94a3b8",
        "card": "#1e293b",
        "border": "#334155",
        "accent": "#6163df",
    },
    "light": {
        "canvas": "#ffffff",
        "text": "#334155",
        "tick_text": "#64748b",
        "card": "#f8fafc",
        "border": "#e2e8f0",
        "accent": "#6163df",
    },
}


def chart_canvas_color(mode=None):
    """Return the canvas background color for *mode*."""
    from desktop.theme import theme_key
    key = theme_key(mode or "dark")
    return CHART_THEME.get(key, CHART_THEME["dark"])["canvas"]


# ── apply_display_options (ECharts-native) ─────────────────────────────────

def apply_display_options(model: dict, layout_opts: dict, typo_opts: dict,
                          width_px: Optional[int] = None,
                          height_px: Optional[int] = None) -> dict:
    """Apply layout + typography to an ECharts ChartModel's ``display`` dict.

    This replaces the old Plotly ``apply_display_options(fig, ...)``.
    The result is stored in ``model["display"]`` and consumed by the
    ECharts JavaScript adapter at render time.
    """
    display = dict(model.get("display") or {})

    # Layout
    display.update({
        "show_border": bool(layout_opts.get("show_border", True)),
        "border_color": layout_opts.get("border_color", "#1e293b"),
        "border_width": int(layout_opts.get("border_width", 1)),
        "border_radius": int(layout_opts.get("border_radius", 10)),
        "padding": int(layout_opts.get("padding", 12)),
        "title_align": layout_opts.get("title_align", "left"),
        "title_position": layout_opts.get("title_position", "top"),
        "title_gap": int(layout_opts.get("title_gap", 16)),
        "show_legend": bool(layout_opts.get("show_legend", True)),
        "legend_position": layout_opts.get("legend_position", "right"),
        "legend_orientation": layout_opts.get("legend_orientation", "vertical"),
        "legend_order": layout_opts.get("legend_order", "normal"),
        "show_colorbar": bool(layout_opts.get("show_colorbar", False)),
        "colorbar_position": layout_opts.get("colorbar_position", "right"),
        "grid_left": str(layout_opts.get("grid_left", "10%")),
        "grid_right": str(layout_opts.get("grid_right", "10%")),
        "grid_top": str(layout_opts.get("grid_top", "15%")),
        "grid_bottom": str(layout_opts.get("grid_bottom", "15%")),
    })

    # Typography
    display.update({
        "header_font_family": typo_opts.get("header_font_family", "Inter"),
        "header_font_size": int(typo_opts.get("header_font_size", 20)),
        "header_font_style": typo_opts.get("header_font_style", "Bold"),
        "header_font_color": typo_opts.get("header_font_color", "#f8fafc"),
        "font_family": typo_opts.get("font_family", "Inter"),
        "font_size": int(typo_opts.get("font_size", 12)),
        "font_style": typo_opts.get("font_style", "Normal"),
        "font_color": typo_opts.get("font_color", "#cbd5e1"),
        "axis_title_font_size": int(typo_opts.get("axis_title_font_size", 12)),
        "axis_label_font_size": int(typo_opts.get("axis_label_font_size", 11)),
        "tick_label_font_size": int(typo_opts.get("tick_label_font_size", 11)),
        "legend_font_size": int(typo_opts.get("legend_font_size", 11)),
        "data_label_font_size": int(typo_opts.get("data_label_font_size", 11)),
    })

    if width_px:
        display["width_px"] = width_px
    if height_px:
        display["height_px"] = height_px

    model["display"] = display
    return model


# ── theme_figure (ECharts-native) ──────────────────────────────────────────

def theme_figure(model: dict, mode: str = "dark") -> dict:
    """Apply theme colors to a ChartModel's display dict."""
    from desktop.theme import theme_key
    key = theme_key(mode or "dark")
    theme = CHART_THEME.get(key, CHART_THEME["dark"])

    display = dict(model.get("display") or {})
    display["canvas_color"] = theme["canvas"]
    display["text_color"] = theme["text"]
    display["tick_text_color"] = theme["tick_text"]
    display["card_color"] = theme["card"]
    display["border_color"] = theme["border"]
    display["accent_color"] = theme["accent"]
    display["theme_mode"] = key
    model["display"] = display
    return model


# ── Legacy-compatible function stubs (for callers that import them) ──────────

def _legend_need_px(names, legend_title="", side="left", axis_px=None):
    """ECharts legend sizing is handled client-side; return a sensible default."""
    return 120


def _legend_plate_px(names, legend_title=""):
    return 100


def _legend_side_margin(fig, default=LEGEND_AXIS_MARGIN):
    return default


def _legend_strip(names, legend_title="", side="left", width_px=None,
                  axis_px=None, scale=1.0):
    return LEGEND_STRIP


def _legend_axis_title_px(fig, side):
    return 0


def legend_anchor(legend_position):
    return {"right": "end", "left": "start", "top": "start", "bottom": "end"}.get(
        str(legend_position).lower(), "end")


def _legend_wrap_rows(names, legend_title, fig, width_px, type_scale=1.0):
    return 1


def plan_legend_layout(fig, legend_position, names=None, legend_title="",
                       width_px=None, height_px=None):
    return {"mode": "column", "side": legend_position, "need_px": 120,
            "axis_px": 0, "strip": None}


def reserve_legend_band(fig, legend_position, band_px, height_px=None):
    return None


def _margin_px(margins, side):
    return 40


def _plot_area_width(margin, width_px):
    return int(width_px * 0.8) if width_px else 400


def _domain_edge_paper(fig, side, strip, width_px, axis_px=None):
    return 0.0


def _domain_traces(fig) -> list:
    return []


def _domain_square_frac(fig, width_px, height_px=None):
    return 1.0


def reserve_legend_column(fig, legend_position, strip=None,
                          width_px=None, height_px=None):
    return None


def infer_legend_position(fig):
    return "right"


def legend_layout_mode(fig):
    return "column"


def _record_legend_layout(fig, position, mode, orientation="Auto"):
    pass


def infer_legend_orientation(fig):
    return "vertical"


def _restore_legend_reservation(fig):
    pass


def apply_legend_layout(fig, legend_position, names=None, legend_title="",
                        width_px=None, height_px=None):
    return None


def _reapply_type_scale(fig, width_px, height_px=None):
    pass


def refit_legend_width(fig, width_px, height_px=None):
    pass


LEGEND_BORDER_COLOR = "rgba(148, 163, 184, 0.35)"


def _shows_scale(container_json):
    return False


def real_colorbars(fig):
    return []


def colorbar_json(fig) -> list:
    return []


def _remap_discrete_map_colours(fig, cs):
    pass


def _apply_heatmap_options(fig, lo, typo, width_px=None):
    pass


COLORBAR_THICKNESS_PX = 30
COLORBAR_GAP_PX = 8


def _axis_label_need_px(fig, side="left"):
    return 40


def _legend_axis_need_px(fig, side):
    return 40


def reserve_colorbar_strip(fig, side, traces=(), width_px=None):
    return None


def _legend_names(fig):
    return []


def _figure_has_legend(fig):
    return bool(fig.get("display", {}).get("show_legend", True))


def _apply_pie_options(fig, lo, type_scale=1.0):
    pass


def _line_mode_with_markers(mode, want):
    return mode


def _fmt_cell(v, prec):
    return str(v)


def _retext_at_precision(text, z, prec):
    return text


def _apply_line_options(fig, lo):
    pass


def _format_table_number(v, spec):
    return str(v)


def _contrast_on(bg):
    return "#ffffff"


def _apply_table_options(fig, lo, t):
    pass


def _apply_colorbar_border(fig, show_border):
    pass


# ── Real colorbars (stub for ECharts) ──────────────────────────────────────

def real_colorbars_v2(fig):
    return []


# ── Aliases for callers ────────────────────────────────────────────────────

theme_figure_alias = theme_figure
apply_display_options_alias = apply_display_options

# Export commonly-used names for test compatibility
__all__ = [
    "FONT_STACKS", "FONT_FAMILIES", "FONT_STYLES", "LABEL_POSITIONS",
    "BAR_MODES", "LEGEND_POSITIONS", "LEGEND_ORIENTATIONS", "LEGEND_ORDERS",
    "COLORBAR_POSITIONS", "CHART_THEME", "installed_font_families",
    "font_is_available", "available_fonts", "default_layout_opts",
    "default_typo_opts", "apply_display_options", "theme_figure",
    "chart_canvas_color", "_font_stack", "_style_dict", "header_font_css",
    "_font_kwargs", "_weight_style", "_type_scale", "_scale_size",
    "LEGEND_STRIP", "LEGEND_STRIP_MIN", "LEGEND_STRIP_MAX",
    "LEGEND_COLUMN_MAX_SHARE", "LEGEND_BAND_MAX_SHARE",
    "_legend_need_px", "_legend_plate_px", "_legend_side_margin",
    "_legend_strip", "_legend_axis_title_px", "legend_anchor",
    "_legend_wrap_rows", "plan_legend_layout", "reserve_legend_band",
    "_margin_px", "_plot_area_width", "_domain_edge_paper",
    "_domain_traces", "_domain_square_frac", "reserve_legend_column",
    "infer_legend_position", "legend_layout_mode", "_record_legend_layout",
    "infer_legend_orientation", "_restore_legend_reservation",
    "apply_legend_layout", "_reapply_type_scale", "refit_legend_width",
    "LEGEND_BORDER_COLOR", "_shows_scale", "real_colorbars", "colorbar_json",
    "_remap_discrete_map_colours", "_apply_heatmap_options",
    "COLORBAR_THICKNESS_PX", "COLORBAR_GAP_PX", "_axis_label_need_px",
    "_legend_axis_need_px", "reserve_colorbar_strip", "_legend_names",
    "_figure_has_legend", "_apply_pie_options", "_line_mode_with_markers",
    "_fmt_cell", "_retext_at_precision", "_apply_line_options",
    "_format_table_number", "_contrast_on", "_apply_table_options",
    "_apply_colorbar_border",
]
