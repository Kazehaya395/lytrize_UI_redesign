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
    "ex_kpi_text_color": "#f5f7ff", "ex_kpi_val_size": 14,
    "ex_title_font": "Inter", "ex_title_style": "Normal",
    "ex_title_size": 28, "ex_title_color": "#6163df",
    "ex_notes_font": "Inter", "ex_notes_style": "Normal",
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
        Free-form dashboard arrangement, ``{uid: {x, y, w, h}}`` in grid units,
        as held by the live tiling canvas. Pass it so the exported HTML
        reproduces the board the user arranged.

    Returns
    -------
    (html, safe_file_name)
    """
    dash_title = dashboard_title or session_name
    safe_file = safe_filename(dash_title)

    export_theme = build_export_theme(state)

    html = generate_html_report(
        charts, session_name,
        orientation=orientation,
        kpis=kpis,
        dashboard_title=dash_title,
        grid_cols_n=grid_cols_n,
        theme=export_theme,
        tile_geometry=tile_geometry,
    )
    return html, safe_file