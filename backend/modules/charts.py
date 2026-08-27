"""modules/charts.py -- Shared chart utilities and palettes.

Headless module: no Streamlit dependency. Chart serialisation/session
concerns live in ``services/chart_service.py`` and
``services/session_service.py`` respectively -- this module only holds
pure helpers (palettes, layout defaults, formatting, column-type
detection) that are shared across the analysis runners.
"""
import logging


import json
import re
import pandas as pd
import plotly.io as pio


# ---------------------------------------------------------------------------
# JSON-safe key sanitizer
# ---------------------------------------------------------------------------
def _json_safe(obj):
    """Recursively walk obj and convert any non-JSON-safe dict keys to strings.

    Plotly meta attributes can produce tuple keys (e.g. ``{(x, y): val}``),
    which crash ``json.dumps``.  This helper guarantees every dict key in the
    output is a ``str``, ``int``, ``float``, or ``bool`` so serialisation
    always succeeds.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, (str, int, float, bool)):
                k = str(k)
            out[k] = _json_safe(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_json_safe(i) for i in obj]
    # Primitive types (str, int, float, bool, None) are safe as-is.
    # Everything else is converted to a string repr for safety.
    if obj is not None and not isinstance(obj, (str, int, float, bool)):
        return str(obj)
    return obj


# NOTE: chart <-> JSON (de)serialisation used to live here, keyed off
# ``st.session_state``. It has been fully replaced by the headless,
# explicit-argument versions in ``services/chart_service.py``
# (``charts_to_json`` / ``charts_from_json`` / ``serialize_chart``).
# Nothing in this module should read or write global session state.


# ---------------------------------------------------------------------------
# Existing utilities (unchanged)
# ---------------------------------------------------------------------------
COLORS = ["#6163df", "#8566fc", "#3390c8", "#f59e0b",
          "#ef4444", "#10b981", "#ec4899", "#f97316"]


DANGER = ["#bbf7d0", "#fbbf24", "#ef4444"]


PALETTES = {
    "🔵 Default Blue-Purple": [
        "#6163df", "#8566fc", "#3390c8", "#f59e0b",
        "#ef4444", "#10b981", "#ec4899", "#f97316"
    ],
    "🌈 Vibrant": [
        "#e63946", "#f4a261", "#2a9d8f", "#457b9d",
        "#e9c46a", "#264653", "#a8dadc", "#f1faee"
    ],
    "🍃 Nature Green": [
        "#2d6a4f", "#40916c", "#52b788", "#74c69d",
        "#95d5b2", "#b7e4c7", "#d8f3dc", "#1b4332"
    ],
    "🌅 Warm Sunset": [
        "#e76f51", "#f4a261", "#e9c46a", "#264653",
        "#2a9d8f", "#e63946", "#f1faee", "#457b9d"
    ],
    "🩷 Pink & Coral": [
        "#ff6b6b", "#feca57", "#48dbfb", "#ff9ff3",
        "#54a0ff", "#5f27cd", "#01abc6", "#ff9f43"
    ],
    "🌊 Ocean Blues": [
        "#03045e", "#0077b6", "#00b4d8", "#90e0ef",
        "#caf0f8", "#023e8a", "#0096c7", "#ade8f4"
    ],
    "🟣 Monochrome Purple": [
        "#3c096c", "#5a189a", "#7b2fbe", "#9d4edd",
        "#c77dff", "#e0aaff", "#240046", "#10002b"
    ],
    "🔆 Pastel Light": [
        "#ffadad", "#ffd6a5", "#fdffb6", "#caffbf",
        "#9bf6ff", "#a0c4ff", "#bdb2ff", "#ffc6ff"
    ],
    "✨ Neo Mint": [
        "#14b8a6", "#06b6d4", "#22c55e", "#a3e635",
        "#f59e0b", "#fb7185", "#818cf8", "#0f766e"
    ],
    "⚡ Tech Neon": [
        "#00d1ff", "#7c3aed", "#22c55e", "#f97316",
        "#eab308", "#ec4899", "#38bdf8", "#1d4ed8"
    ],
    "🏙️ Urban Slate": [
        "#0f172a", "#334155", "#475569", "#14b8a6",
        "#3b82f6", "#8b5cf6", "#f59e0b", "#f43f5e"
    ],
    "🌇 Sunset Pro": [
        "#f97316", "#fb7185", "#f59e0b", "#7c3aed",
        "#2563eb", "#14b8a6", "#e11d48", "#0f172a"
    ],
    "🌌 Aurora Glow": [
        "#22c55e", "#06b6d4", "#3b82f6", "#8b5cf6",
        "#ec4899", "#f43f5e", "#f59e0b", "#14b8a6"
    ],
    "💼 Luxe Indigo": [
        "#1e3a8a", "#4338ca", "#6366f1", "#0f766e",
        "#14b8a6", "#d97706", "#db2777", "#334155"
    ],
    "🍊 Tropical Punch": [
        "#f97316", "#f43f5e", "#22c55e", "#06b6d4",
        "#3b82f6", "#a855f7", "#eab308", "#14b8a6"
    ],
    "🎯 Executive Accent": [
        "#1d4ed8", "#0f766e", "#7c3aed", "#d97706",
        "#be123c", "#2563eb", "#059669", "#334155"
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def chart_layout(height: int | None = None) -> dict:
    """Return a dict of Plotly layout kwargs used by every chart in Lytrize."""
    layout = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=48, b=20),
        bargap=0.28,
        bargroupgap=0.1,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=True),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=True),
        hovermode="closest",
        hoverlabel=dict(
            bgcolor="rgba(18, 26, 45, 0.96)",
            bordercolor="rgba(133, 102, 252, 0.30)",
            font=dict(size=13, color="#f5f7ff", family="Inter, system-ui, sans-serif"),
            namelength=-1,
        ),
    )
    if height is not None:
        layout["height"] = int(height)
    return layout


def apply_hover_format(fig) -> None:
    """Apply K/M/B-formatted hovertemplates to every trace in a Plotly figure."""
    for trace in fig.data:
        existing = getattr(trace, "hovertemplate", None) or ""
        if "customdata" in existing:
            continue

        ttype = type(trace).__name__.lower()

        if ttype == "bar":
            orient = getattr(trace, "orientation", "v") or "v"
            if orient == "h":
                trace.hovertemplate = (
                    "<b>%{y}</b><br>"
                    "Value: <b>%{x:.3~s}</b>"
                    "<extra></extra>"
                )
            else:
                trace.hovertemplate = (
                    "<b>%{x}</b><br>"
                    "Value: <b>%{y:.3~s}</b>"
                    "<extra></extra>"
                )

        elif ttype == "scatter":
            trace_name = getattr(trace, "name", "") or ""
            if trace_name:
                trace.hovertemplate = (
                    f"<b>{trace_name}</b><br>"
                    "%{x}<br>"
                    "Value: <b>%{y:,.2f}</b>"
                    "<extra></extra>"
                )
            else:
                trace.hovertemplate = (
                    "<b>%{x}</b><br>"
                    "Value: <b>%{y:,.2f}</b>"
                    "<extra></extra>"
                )

        elif ttype == "histogram":
            trace.hovertemplate = (
                "Range: <b>%{x}</b><br>"
                "Count: <b>%{y:.3~s}</b>"
                "<extra></extra>"
            )

        elif ttype in ("pie", "sunburst"):
            trace.hovertemplate = (
                "<b>%{label}</b><br>"
                "Value: <b>%{value:.3~s}</b><br>"
                "Share: %{percent}"
                "<extra></extra>"
            )

        elif ttype == "heatmap":
            trace.hovertemplate = (
                "%{x} × %{y}<br>"
                "r = <b>%{z:.3f}</b>"
                "<extra></extra>"
            )


def num_cols(df: pd.DataFrame) -> list:
    """Return the list of numeric column names in *df*. Pure; no session state."""
    if df is None:
        return []
    return df.select_dtypes(include="number").columns.tolist()


def cat_cols(df: pd.DataFrame) -> list:
    """Return the list of categorical (object) column names in *df*. Pure; no session state."""
    if df is None:
        return []
    return df.select_dtypes(include="object").columns.tolist()


def dt_cols(df: pd.DataFrame) -> list:
    """Return the list of datetime column names in *df*. Pure; no session state."""
    if df is None:
        return []
    return df.select_dtypes(include="datetime").columns.tolist()


def _fmt_num(value) -> str:
    """Format a numeric value with K/M/B suffixes and thousand separators."""
    try:
        v = float(value)
    except Exception:
        return str(value)
    if pd.isna(v):
        return "n/a"
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1_000_000_000: return f"{sign}{av / 1_000_000_000:.1f}B"
    if av >= 1_000_000:     return f"{sign}{av / 1_000_000:.1f}M"
    if av >= 1_000:         return f"{sign}{av / 1_000:.1f}K"
    if av == int(av):       return f"{int(v):,}"
    return f"{v:,.2f}"


def _fmt_pct(value) -> str:
    """Format a value as a signed percentage string."""
    try:
        return f"{float(value):+.1f}%"
    except Exception:
        return "n/a"


def _plural(count, singular: str, plural: str = None) -> str:
    """Return singular or plural form based on count."""
    return singular if int(count) == 1 else (plural or f"{singular}s")


def _fmt_label(value) -> str:
    """Format a value as a human-readable date/time label if possible."""
    try:
        ts = pd.to_datetime(value, errors="coerce")
        if pd.notna(ts):
            if ts.hour or ts.minute or ts.second:
                return ts.strftime("%d %b %Y %H:%M")
            return ts.strftime("%d %b %Y")
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass
    return str(value)


def _as_number_series(values) -> pd.Series:
    """Convert values to a numeric Series, coercing errors to NaN and dropping them."""
    return pd.to_numeric(pd.Series(values), errors="coerce").dropna()


def _as_list(values) -> list:
    """Convert any iterable to a list, returning an empty list on failure."""
    if values is None:
        return []
    try:
        return list(values)
    except Exception:
        return []


