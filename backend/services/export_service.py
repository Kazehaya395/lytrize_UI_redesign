"""services/export_service.py -- Headless export service.

Re-exports the pure HTML report generator from ``modules.export`` and
adds convenience helpers for building export charts and themes
(de-duplicated from the Streamlit dashboard page).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Optional

import pandas as pd

from modules.export import generate_html_report, DEFAULT_THEME, _merge_theme
from modules.charts import apply_hover_format
from services.chart_service import safe_filename

log = logging.getLogger(__name__)


# -- Re-exports -----------------------------------------------------------

generate_html_report = generate_html_report


# -- Export chart builder (headless, no session state) ----------------------

def build_export_charts(
    charts: list,
    meta_map: Optional[dict] = None,
    notes: Optional[dict] = None,
    full_width: Optional[dict] = None,
) -> list:
    """Build (uid, title, fig, notes, chart_type, meta) tuples for export.

    Each input chart may be a 3-tuple ``(uid, title, fig)`` or a longer
    tuple that includes desc/chart_type/meta.

    Parameters
    ----------
    charts : list
        List of ``(uid, title, fig)`` tuples.
    meta_map : dict, optional
        Map of ``{uid: meta_dict}``.
    notes : dict, optional
        Map of ``{uid: note_text}``.
    full_width : dict, optional
        Map of ``{uid: bool}`` for full-width spans.
    """
    meta_map = meta_map or {}
    notes = notes or {}
    full_width = full_width or {}

    out = []
    for chart in charts:
        uid = chart[0]
        title = chart[1]
        fig = chart[2]
        meta = dict(chart[5]) if len(chart) > 5 else dict(meta_map.get(uid, {}))
        if full_width.get(uid):
            meta["full_width"] = True
        desc = chart[3] if len(chart) > 3 else notes.get(uid, "")
        chart_type = chart[4] if len(chart) > 4 else ""
        out.append((uid, title, fig, desc, chart_type, meta))
    return out


def _apply_axes(fig, x_lbl: str, y_lbl: str, text_style: Optional[dict] = None,
                *, _inplace: bool = False):
    """Apply axis titles and styling to a chart (mirrors dashboard helper)."""
    from modules.export import _apply_axes as _export_apply_axes
    return _export_apply_axes(fig, x_lbl, y_lbl, text_style)


def _apply_legend_names(fig, legend_names: dict, legend_title: str = "",
                        text_style: Optional[dict] = None, *, _inplace: bool = False):
    """Rename traces and style the legend (mirrors dashboard helper)."""
    from modules.export import _apply_legend_names as _export_apply_legend_names
    return _export_apply_legend_names(fig, legend_names, legend_title, text_style)


# -- Theme builder (headless, no session state) -------------------------------

# Default export theme values (same as the dashboard's _EX_DEFAULTS)
EXPORT_DEFAULTS = {
    "ex_bg": "#121a2e", "ex_card": "#1b2245", "ex_kpi": "#1b2245",
    "ex_accent": "#6163df", "ex_border": "#2c3564", "ex_text": "#f5f7ff",
    "ex_not_bg": "#1a1732", "ex_not_bd": "#8566fc",
    "ex_density": "Comfortable", "ex_radius": 12, "ex_chart_h": 400,
    "ex_width": "Auto", "ex_meta": True,
    "ex_kpi_text_color": "#f5f7ff", "ex_kpi_val_size": 26,
    # KPI customisation. ``ex_kpi_val_color`` empty means "use the accent",
    # which is what every report looked like before these existed.
    "ex_kpi_val_color": "", "ex_kpi_label_size": 12,
    "ex_kpi_label_upper": True,
    # Slicer theming (note 3: a slicer has its own design section). Empty
    # means "inherit the KPI chrome", which is what every slicer looked like
    # before these existed -- so a report without slicer colours is unchanged.
    "ex_slicer_bg": "", "ex_slicer_bd": "", "ex_slicer_text": "",
    "ex_slicer_accent": "",
    # Background image. The picker stores a DATA URI here (see
    # ``desktop.pages.dashboard_page._image_to_data_uri``) rather than a path,
    # so the saved HTML is self-contained and the same document renders on a
    # machine that has never seen the file the user chose.
    "ex_bg_image": "", "ex_bg_image_opacity": 35, "ex_bg_blend": "normal",
    # Per-CARD background image, same data-URI contract as ``ex_bg_image``:
    # the picker embeds the file so the exported HTML carries the picture
    # rather than a path to it. ``ex_card_image_groups`` is the comma-separated
    # list of card kinds it applies to -- "charts", "kpi", "title" -- so one
    # picture can go behind only the charts, or only the KPI strip, instead of
    # every card at once.
    "ex_card_image": "", "ex_card_image_opacity": 35, "ex_card_blend": "normal",
    "ex_card_image_groups": "charts,kpi,title",
    "ex_title_font": "Inter", "ex_title_style": "Normal",
    "ex_title_size": 28, "ex_title_color": "#6163df",
    "ex_notes_font": "Inter", "ex_notes_style": "Italic",
    "ex_notes_size": 14, "ex_notes_color": "#f5f7ff",
}

_DENSITY = {
    "Compact": {"gap": 14, "padding": 20},
    "Comfortable": {"gap": 24, "padding": 32},
    "Spacious": {"gap": 34, "padding": 44},
}

_WIDTHS = {
    "Auto": "",
    "Narrow": "960px",
    "Wide": "1440px",
    "Full": "100%",
}


# -- Quick theme presets ----------------------------------------------------
# Ported from the old dashboard's ``_PRESETS`` (modules/pages/dashboard.py:1203),
# which the Qt rewrite dropped entirely -- the report could only be styled one
# colour-picker at a time. Every preset sets the SAME set of keys, so applying
# one is a straight merge over the current style rather than a partial look that
# leaves, say, the notes box on the previous theme's colour.
#
# Keys are the export style keys only. Anything the preset does not mention
# (density, radius, chart height, fonts) is deliberately left as the user has it:
# a colour theme should not silently reset the layout they arranged.
EXPORT_PRESETS: dict[str, dict[str, str]] = {
    "\u26a1 Midnight Pulse": {
        "ex_bg": "#08111f", "ex_card": "#101a35", "ex_kpi": "#0c1530",
        "ex_accent": "#8b5cf6", "ex_border": "#31406e", "ex_text": "#f8fbff",
        "ex_not_bg": "#1b1736", "ex_not_bd": "#f472b6",
        "ex_title_color": "#8b5cf6", "ex_notes_color": "#f8fbff",
        "ex_kpi_text_color": "#f8fbff",
    },
    "\U0001f30a Electric Lagoon": {
        "ex_bg": "#061a24", "ex_card": "#0f2437", "ex_kpi": "#0b1f30",
        "ex_accent": "#38bdf8", "ex_border": "#0ea5e9", "ex_text": "#ecfeff",
        "ex_not_bg": "#102a43", "ex_not_bd": "#a855f7",
        "ex_title_color": "#38bdf8", "ex_notes_color": "#ecfeff",
        "ex_kpi_text_color": "#ecfeff",
    },
    "\U0001f496 Prism Pop": {
        "ex_bg": "#1b1030", "ex_card": "#251544", "ex_kpi": "#22113f",
        "ex_accent": "#ec4899", "ex_border": "#8b5cf6", "ex_text": "#fff7ff",
        "ex_not_bg": "#211638", "ex_not_bd": "#60a5fa",
        "ex_title_color": "#ec4899", "ex_notes_color": "#fff7ff",
        "ex_kpi_text_color": "#fff7ff",
    },
    "\u2600\ufe0f Solar Flare": {
        "ex_bg": "#20130a", "ex_card": "#322012", "ex_kpi": "#2f180f",
        "ex_accent": "#f97316", "ex_border": "#f59e0b", "ex_text": "#fff7ed",
        "ex_not_bg": "#2d1e0b", "ex_not_bd": "#fb7185",
        "ex_title_color": "#f97316", "ex_notes_color": "#fff7ed",
        "ex_kpi_text_color": "#fff7ed",
    },
    "\u2744\ufe0f Arctic Neon": {
        "ex_bg": "#07131f", "ex_card": "#0f2235", "ex_kpi": "#0b1b2d",
        "ex_accent": "#60a5fa", "ex_border": "#22d3ee", "ex_text": "#eff6ff",
        "ex_not_bg": "#14213d", "ex_not_bd": "#34d399",
        "ex_title_color": "#60a5fa", "ex_notes_color": "#eff6ff",
        "ex_kpi_text_color": "#eff6ff",
    },
    "\U0001f33f Forest Electric": {
        "ex_bg": "#071a12", "ex_card": "#0f281b", "ex_kpi": "#0b2217",
        "ex_accent": "#22c55e", "ex_border": "#34d399", "ex_text": "#effaf3",
        "ex_not_bg": "#182817", "ex_not_bd": "#f59e0b",
        "ex_title_color": "#22c55e", "ex_notes_color": "#effaf3",
        "ex_kpi_text_color": "#effaf3",
    },
    "\u2728 Aurora Bright": {
        "ex_bg": "#f7fbff", "ex_card": "#ffffff", "ex_kpi": "#eefcf8",
        "ex_accent": "#14b8a6", "ex_border": "#b7e4d3", "ex_text": "#0f172a",
        "ex_not_bg": "#fff7e6", "ex_not_bd": "#f59e0b",
        "ex_title_color": "#14b8a6", "ex_notes_color": "#0f172a",
        "ex_kpi_text_color": "#0f172a",
    },
    "\U0001f305 Sunset Bloom": {
        "ex_bg": "#fff7fb", "ex_card": "#ffffff", "ex_kpi": "#fff0f5",
        "ex_accent": "#ec4899", "ex_border": "#f6b3d4", "ex_text": "#3f1634",
        "ex_not_bg": "#fff6ea", "ex_not_bd": "#fb923c",
        "ex_title_color": "#ec4899", "ex_notes_color": "#3f1634",
        "ex_kpi_text_color": "#3f1634",
    },
    "\U0001f30a Ocean Breeze": {
        "ex_bg": "#f4fbff", "ex_card": "#ffffff", "ex_kpi": "#e6f5ff",
        "ex_accent": "#0284c7", "ex_border": "#9fd7f5", "ex_text": "#0c4a6e",
        "ex_not_bg": "#effcf8", "ex_not_bd": "#14b8a6",
        "ex_title_color": "#0284c7", "ex_notes_color": "#0c4a6e",
        "ex_kpi_text_color": "#0c4a6e",
    },
    "\U0001f34b Citrus Glow": {
        "ex_bg": "#fffdf3", "ex_card": "#ffffff", "ex_kpi": "#fff4cc",
        "ex_accent": "#f59e0b", "ex_border": "#f7d46b", "ex_text": "#713f12",
        "ex_not_bg": "#fff7e8", "ex_not_bd": "#ef4444",
        "ex_title_color": "#f59e0b", "ex_notes_color": "#713f12",
        "ex_kpi_text_color": "#713f12",
    },
    "\U0001f338 Blossom Pop": {
        "ex_bg": "#fff7fb", "ex_card": "#ffffff", "ex_kpi": "#ffeaf3",
        "ex_accent": "#db2777", "ex_border": "#f3b0cf", "ex_text": "#7a1f55",
        "ex_not_bg": "#f4f0ff", "ex_not_bd": "#8b5cf6",
        "ex_title_color": "#db2777", "ex_notes_color": "#7a1f55",
        "ex_kpi_text_color": "#7a1f55",
    },
    "\U0001fab4 Mint Fresh": {
        "ex_bg": "#f4fff7", "ex_card": "#ffffff", "ex_kpi": "#e3fcef",
        "ex_accent": "#16a34a", "ex_border": "#a7e6b8", "ex_text": "#14532d",
        "ex_not_bg": "#eefcf4", "ex_not_bd": "#10b981",
        "ex_title_color": "#16a34a", "ex_notes_color": "#14532d",
        "ex_kpi_text_color": "#14532d",
    },
}

# The swatches the preset gallery paints, in the order they are shown.
PRESET_SWATCH_KEYS = ("ex_bg", "ex_card", "ex_accent", "ex_kpi")


def apply_preset(state: dict, preset_name: str) -> dict:
    """Return *state* with *preset_name* merged over it.

    Unknown names return the state untouched rather than raising: a saved
    session can name a preset from a build that had one and this one does not,
    and losing the whole style because of a name would be a far worse outcome
    than keeping the colours the user can see.
    """
    preset = EXPORT_PRESETS.get(str(preset_name or ""))
    if not preset:
        return dict(state or {})
    out = dict(state or {})
    out.update(preset)
    return out



def build_export_theme(state: dict) -> dict:
    """Build the export theme dict from a state mapping.

    Parameters
    ----------
    state : dict
        Mapping of keys like ``ex_bg``, ``ex_card``, etc. Missing keys
        use defaults from ``EXPORT_DEFAULTS``.
    """
    def g(key: str, default: Any = None) -> Any:
        return state.get(key, default if default is not None else EXPORT_DEFAULTS[key])

    density = _DENSITY.get(g("ex_density"), {"gap": 24, "padding": 32})
    width = _WIDTHS.get(g("ex_width"), "")

    return {
        "bg_color":       g("ex_bg"),
        "card_bg":        g("ex_card"),
        "kpi_bg":         g("ex_kpi"),
        "accent_color":   g("ex_accent"),
        "card_border":    g("ex_border"),
        "text_color":     g("ex_text"),
        "notes_bg":       g("ex_not_bg"),
        "notes_border":   g("ex_not_bd"),
        "card_radius":    g("ex_radius"),
        "gap":            density["gap"],
        "body_padding":   density["padding"],
        "chart_height":   g("ex_chart_h"),
        "max_width":      width,
        "show_meta":      g("ex_meta"),
        "kpi_text_color": g("ex_kpi_text_color"),
        "kpi_val_size":   g("ex_kpi_val_size"),
        "kpi_val_color":  g("ex_kpi_val_color"),
        "kpi_label_size": g("ex_kpi_label_size"),
        "kpi_label_upper": g("ex_kpi_label_upper"),
        "slicer_bg":      g("ex_slicer_bg"),
        "slicer_bd":      g("ex_slicer_bd"),
        "slicer_text":    g("ex_slicer_text"),
        "slicer_accent":  g("ex_slicer_accent"),
        "bg_image":       g("ex_bg_image"),
        "bg_image_opacity": g("ex_bg_image_opacity"),
        "bg_blend":       g("ex_bg_blend"),
        "card_image":       g("ex_card_image"),
        "card_image_opacity": g("ex_card_image_opacity"),
        "card_blend":       g("ex_card_blend"),
        "card_image_groups": g("ex_card_image_groups"),
        "title_font":     g("ex_title_font"),
        "title_style":    g("ex_title_style"),
        "title_size":     g("ex_title_size"),
        "title_color":    g("ex_title_color"),
        "notes_font":     g("ex_notes_font"),
        "notes_style":    g("ex_notes_style"),
        "notes_size":     g("ex_notes_size"),
        "notes_color":    g("ex_notes_color"),
    }


def build_export_report(
    charts,
    session_name: str,
    state: dict,
    *,
    orientation: str = "portrait",
    kpis: Optional[list] = None,
    dashboard_title: str = "",
    grid_cols_n: int = 2,
    tile_geometry: Optional[dict] = None,
    kpi_geometry: Optional[dict] = None,
    slicers: Optional[list] = None,
    slicer_geometry: Optional[dict] = None,
    slicer_values: Optional[dict] = None,
    geometry_mode: str = "free",
    inline_plotly: bool = True,
    extra_css: str = "",
    editor: bool = False,
    report_specs: Optional[dict] = None,
    report_payload: Optional[dict] = None,
) -> tuple[str, str]:
    """Generate an export-ready HTML report and a safe file name.

    Parameters
    ----------
    charts : list
        List of ``(uid, title, fig)`` tuples (3-tuples).
    session_name : str
        Session name used as the report title fallback.
    state : dict
        Mapping of export theme settings (``ex_bg``, ``ex_density``, etc.).
    orientation : str
        ``"portrait"`` or ``"landscape"``.
    kpis : list, optional
        KPI dicts.
    dashboard_title : str, optional
        Override title.
    grid_cols_n : int
        Number of grid columns.
    tile_geometry : dict, optional
        Free-form dashboard arrangement, ``{uid: {x, y, w, h}}`` with ``x``/``w``
        as fractions of the canvas width and ``y``/``h`` in pixels, as held by
        the live canvas. Pass it so the exported HTML reproduces the board the
        user arranged.
    kpi_geometry : dict, optional
        The same, for the KPI cards: ``{index: {x, y, w, h}}``.
    slicers : list, optional
        Slicer definitions (see ``services.slicer_service``). Rendered as
        cards on the board exactly like KPIs.
    slicer_geometry : dict, optional
        Placement for those cards, keyed by BARE index (``{"0": {...}}``) --
        the same spelling ``kpi_geometry`` uses. ``generate_html_report``
        namespaces them internally as it merges them into the board's map, so
        handing it ``"slicer:0"`` here would make both of its lookups miss and
        the stored placement would be silently discarded.
    slicer_values : dict, optional
        ``{index: [values]}`` -- the candidate values each list/dropdown slicer
        offers. Computed by the caller from the dataset rather than here: the
        export path is deliberately headless and receives no DataFrame.
    geometry_mode : str
        ``"free"`` (default) for the absolutely-positioned canvas the app edits
        in; ``"grid"`` for the older cell-based placement, used only to render a
        session saved before the free canvas existed.
    inline_plotly : bool
        Embed the plotly bundle in the file (default, so the saved report is
        standalone) or emit a ``<script src="lytrize_plotly.js">`` reference.
        The live in-app canvas passes ``False`` and writes the shared bundle
        itself -- re-embedding ~4.5 MB on every settings tweak is not viable.
    extra_css : str
        Extra CSS injected before ``</style>``. The canvas uses it to offer the
        app's bundled fonts to Chromium, which cannot see Qt's font database.
    editor : bool
        Append the in-app drag/resize/notes overlay. Off for the saved file.
    report_specs : dict, optional
        ``{uid: spec}`` from ``modules.chartspec.chart_spec`` -- one JSON spec
        per chart, so the in-browser engine knows each chart's columns and
        aggregation. When omitted and the chart tuples carry meta (6-tuples),
        ``generate_html_report`` derives them itself.
    report_payload : dict, optional
        The column-pruned dataset from ``modules.report_data.build_report_payload``,
        emitted as ``window.LYTRIZE_DATA`` alongside the slicers. A payload over
        ``report_data.MAX_INLINE_BYTES`` is dropped here rather than embedded:
        the file still opens and reads, it just does not re-aggregate.

    Returns
    -------
    (html, safe_file_name)
    """
    dash_title = dashboard_title or session_name
    safe_file = safe_filename(dash_title)

    export_theme = build_export_theme(state)

    if report_payload is not None:
        try:
            from modules.report_data import MAX_INLINE_BYTES, payload_json_size
            size = payload_json_size(report_payload)
            if size > MAX_INLINE_BYTES:
                log.warning(
                    "export payload %d bytes > MAX_INLINE_BYTES %d; "
                    "static export without live slicers", size, MAX_INLINE_BYTES)
                report_payload = None
        except Exception:
            log.debug("payload size check failed", exc_info=True)

    html = generate_html_report(
        charts, session_name,
        orientation=orientation,
        kpis=kpis,
        dashboard_title=dash_title,
        grid_cols_n=grid_cols_n,
        theme=export_theme,
        tile_geometry=tile_geometry,
        kpi_geometry=kpi_geometry,
        slicers=slicers,
        slicer_geometry=slicer_geometry,
        slicer_values=slicer_values,
        geometry_mode=geometry_mode,
        inline_plotly=inline_plotly,
        extra_css=extra_css,
        editor=editor,
        report_specs=report_specs,
        report_payload=report_payload,
    )
    return html, safe_file