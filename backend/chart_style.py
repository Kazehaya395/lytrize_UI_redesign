"""backend/chart_style.py -- Chart styling engine (Phase 1 of the
"granular chart controls" work).

Headless port of the *apply* half of the old Streamlit
``modules/ui/chart_settings.py`` (1641 lines). This module does not
attempt full parity with that file in one pass -- it covers the
controls that matter most for day-to-day chart building: palette,
legend, gridlines, bar gap/mode, value labels, font family/size, and
marker size/opacity. Deferred for a later pass: per-trace fill
options, dual-axis-specific typography, matrix-heatmap view toggling,
and the full custom-per-element (title/axis/legend/notes) typography
breakdown the old panel had. See PHASED_PLAN.md for what's covered.

Design choice carried over directly from the old file: every call
takes the chart's ORIGINAL, unstyled figure and a style dict, and
returns a fresh figure -- it never mutates or builds on a previously
styled figure. This is what makes toggling a control on/off always
give a clean, correct result instead of compounding stale mutations
from earlier toggles (the old file did the same via
``copy.deepcopy(fig)`` on every call).
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from modules.charts import PALETTES

DEFAULT_PALETTE = "🔵 Default Blue-Purple"

# Controls that only make sense for certain trace types -- the Qt panel
# uses this to decide which widgets to show for a given chart_type,
# mirroring the old codebase's get_chart_type_capabilities()/has_control().
MARKER_CHART_TYPES = {"scatter_plot", "map_plot"}
BAR_CHART_TYPES = {"categorical", "time_series"}
VALUE_LABEL_CHART_TYPES = {"categorical", "pie_chart", "scatter_plot", "time_series"}

FONT_FAMILIES = [
    "Inter", "Arial", "Helvetica", "Georgia", "Times New Roman",
    "Courier New", "Verdana", "Trebuchet MS", "Roboto", "Calibri",
]


def default_style() -> dict:
    """The style dict every chart starts with -- matches what
    modules/charts.chart_layout() already produces by default, just
    made explicit and user-editable."""
    return {
        "palette": DEFAULT_PALETTE,
        "show_legend": True,
        "show_gridlines": False,
        "bar_gap": 0.28,
        "show_value_labels": False,
        "value_label_color": "#ffffff",
        "font_family": "Inter",
        "font_size": 13,
        "title_font_size": 16,
        "marker_size": 8,
        "marker_opacity": 0.85,
    }


def _resolve_font_stack(name: str) -> str:
    """Fall back to a safe cross-platform stack, same intent as the old
    resolve_font_stack() -- keeps text readable even if the exact
    family isn't installed on the user's system."""
    return f"{name}, Inter, system-ui, -apple-system, sans-serif"


def apply_chart_style(fig, style: Optional[dict], chart_type: str = "") -> Any:
    """Return a NEW figure with ``style`` applied to a copy of ``fig``.

    ``fig`` is never mutated -- callers should keep the original
    figure around (see ChartCard._base_fig) and call this fresh every
    time a style control changes, not chain calls on an
    already-styled figure.
    """
    style = {**default_style(), **(style or {})}
    f2 = copy.deepcopy(fig)

    # -- Palette: reassign trace colors cyclically from the chosen palette --
    palette = PALETTES.get(style.get("palette"), PALETTES[DEFAULT_PALETTE])
    for i, tr in enumerate(f2.data):
        color = palette[i % len(palette)]
        ttype = type(tr).__name__.lower()
        try:
            if ttype == "pie":
                tr.marker.colors = [palette[j % len(palette)] for j in range(len(tr.labels))] if getattr(tr, "labels", None) is not None else None
            elif hasattr(tr, "marker") and tr.marker is not None:
                tr.marker.color = color
            if hasattr(tr, "line") and tr.line is not None and ttype in ("scatter", "scattergl"):
                tr.line.color = color
        except Exception:
            pass  # trace type doesn't support this attribute; skip rather than fail the whole render

    # -- Legend --
    f2.update_layout(showlegend=bool(style.get("show_legend", True)))

    # -- Gridlines --
    show_grid = bool(style.get("show_gridlines", False))
    f2.update_layout(
        xaxis=dict(showgrid=show_grid),
        yaxis=dict(showgrid=show_grid),
    )

    # -- Bar gap (bar/histogram charts) --
    if chart_type in BAR_CHART_TYPES or any(type(tr).__name__.lower() == "bar" for tr in f2.data):
        try:
            f2.update_layout(bargap=float(style.get("bar_gap", 0.28)))
        except (TypeError, ValueError):
            pass

    # -- Font family / size (global, applies to axis/legend/hover text) --
    font_family = _resolve_font_stack(style.get("font_family", "Inter"))
    try:
        font_size = int(style.get("font_size", 13))
    except (TypeError, ValueError):
        font_size = 13
    f2.update_layout(font=dict(family=font_family, size=font_size))

    # -- Title font size (title text itself, separate from body font size) --
    try:
        title_size = int(style.get("title_font_size", 16))
        if f2.layout.title and f2.layout.title.text:
            f2.update_layout(title=dict(font=dict(size=title_size, family=font_family)))
    except (TypeError, ValueError):
        pass

    # -- Value labels (bar/scatter/pie) --
    if style.get("show_value_labels") and chart_type in VALUE_LABEL_CHART_TYPES:
        label_color = style.get("value_label_color", "#ffffff")
        for tr in f2.data:
            ttype = type(tr).__name__.lower()
            try:
                if ttype == "bar":
                    tr.textposition = "outside"
                    tr.texttemplate = "%{y}"
                    tr.textfont = dict(color=label_color)
                elif ttype in ("scatter", "scattergl") and "markers" in (getattr(tr, "mode", "") or ""):
                    tr.mode = (tr.mode or "markers") + ("+text" if "text" not in tr.mode else "")
                    tr.textposition = "top center"
                    tr.textfont = dict(color=label_color)
                elif ttype == "pie":
                    tr.textinfo = "label+percent"
                    tr.textfont = dict(color=label_color)
            except Exception:
                pass

    # -- Marker size / opacity (scatter / map charts) --
    if chart_type in MARKER_CHART_TYPES:
        try:
            marker_size = int(style.get("marker_size", 8))
            marker_opacity = float(style.get("marker_opacity", 0.85))
        except (TypeError, ValueError):
            marker_size, marker_opacity = 8, 0.85
        for tr in f2.data:
            if hasattr(tr, "marker") and tr.marker is not None:
                try:
                    tr.marker.size = marker_size
                    tr.marker.opacity = marker_opacity
                except Exception:
                    pass

    return f2
