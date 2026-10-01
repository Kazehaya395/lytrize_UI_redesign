"""modules/analysis/scatter_plot.py -- Scatter plot runner."""
import logging
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from modules.charts import chart_layout, COLORS
from services.column_roles import role_pools
from modules.utils.perf import sample_for_plot


# ---------------------------------------------------------------------------
# Trendline computation — prefer scipy when available, fall back to pure
# numpy for OLS (ordinary least squares).  LOWESS requires scipy and will
# be silently downgraded to OLS when scipy is absent.
# ---------------------------------------------------------------------------
_HAS_SCIPY = False
try:
    import scipy
    _HAS_SCIPY = True
except ImportError:
    pass


def _ols_trendline(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Return (slope, intercept, r_value) for a simple linear regression.
    
    Pure numpy implementation — no scipy required.
    """
    n = len(x)
    sx = x.sum()
    sy = y.sum()
    sxx = (x * x).sum()
    sxy = (x * y).sum()
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    intercept = (sy - slope * sx) / n
    # Pearson r
    r_num = n * sxy - sx * sy
    r_den = np.sqrt((n * sxx - sx * sx) * (n * (y * y).sum() - sy * sy))
    r_val = r_num / r_den if r_den != 0 else 0.0
    return slope, intercept, r_val


def _add_trendline(fig, plot_df, x: str, y: str, tl_type: str) -> None:
    """Add a trendline trace to the figure.
    
    Uses scipy when available (supports both 'ols' and 'lowess').
    Falls back to pure numpy OLS when scipy is absent.
    
    The computed trendline metadata (slope, intercept, r_value, r_squared,
    n_points, type) is stored on fig._lytrize_trendline for the
    engine to consume, so results always reflect the actual rendered trend.
    """
    x_vals = pd.to_numeric(plot_df[x], errors="coerce").dropna().values
    y_vals = pd.to_numeric(plot_df[y], errors="coerce").dropna().values
    # Align lengths
    min_len = min(len(x_vals), len(y_vals))
    x_vals = x_vals[:min_len]
    y_vals = y_vals[:min_len]
    if min_len < 3:
        fig._lytrize_trendline = {"type": tl_type, "n_points": 0, "active": False}
        return

    trend_meta: dict = {"type": tl_type, "n_points": min_len, "active": True}

    if tl_type == "lowess" and _HAS_SCIPY:
        from scipy.interpolate import interp1d
        from scipy.ndimage import gaussian_filter1d
        # Simple LOWESS approximation using gaussian filter
        order = np.argsort(x_vals)
        xs = x_vals[order]
        ys = y_vals[order]
        ys_smooth = gaussian_filter1d(ys, sigma=len(xs) * 0.05, mode="nearest")
        fig.add_trace(go.Scatter(
            x=xs, y=ys_smooth,
            mode="lines",
            name="lowess trend",
            line=dict(width=2, dash="dot", color="#ef4444"),
            hovertemplate=f"<b>{y} (trend):</b> %{{y:,.3f}}<extra></extra>",
        ))
        # Approximate linear-equivalent r for LOWESS by comparing smoothed
        # values vs actual — this gives a rough goodness-of-fit indication.
        y_actual_interp = np.interp(xs, x_vals, y_vals)
        residuals = y_actual_interp - ys_smooth
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((y_actual_interp - np.mean(y_actual_interp)) ** 2)
        trend_meta["r_squared_raw"] = 1.0 - (ss_res / ss_tot) if ss_tot != 0 else 0.0
    else:
        # OLS — works with or without scipy
        slope, intercept, r_val = _ols_trendline(x_vals, y_vals)
        r_sq = r_val ** 2
        trend_y = slope * x_vals + intercept
        # Residual analysis: count points within 1 standard error of the line
        residuals = y_vals - trend_y
        std_err = np.std(residuals)
        within_one_sigma = int(np.sum(np.abs(residuals) <= std_err))
        trend_meta.update({
            "slope": slope,
            "intercept": intercept,
            "r_value": r_val,
            "r_squared": r_sq,
            "std_err": std_err,
            "within_one_sigma": within_one_sigma,
        })
        fig.add_trace(go.Scatter(
            x=x_vals, y=trend_y,
            mode="lines",
            name=f"y={slope:.3g}x+{intercept:.3g}",
            line=dict(width=2, dash="dot", color="#ef4444"),
            hovertemplate=f"<b>{y} (trend):</b> %{{y:,.3f}}<extra></extra>",
        ))

    fig._lytrize_trendline = trend_meta




def _pearson_r(a, b):
    try:
        s1 = pd.to_numeric(a, errors="coerce").dropna()
        s2 = pd.to_numeric(b, errors="coerce").dropna()
        idx = s1.index.intersection(s2.index)
        if len(idx) < 3:
            return None
        return float(np.corrcoef(s1[idx], s2[idx])[0, 1])
    except Exception:
        return None




def _opacity(n: int) -> float:
    if n < 300:    return 0.90
    if n < 1_500:  return 0.75
    if n < 3_500:  return 0.55
    return 0.40




def _normalise_size(series: pd.Series, lo: float = 4, hi: float = 28) -> pd.Series:
    """Map size column to [lo, hi] pixel range."""
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series([float(lo + (hi - lo) / 2)] * len(series), index=series.index)
    return lo + (series - mn) / (mx - mn) * (hi - lo)




def _adaptive_sample_limit(n_color_groups: int, base_limit: int = 3_500) -> int:
    """Reduce sample limit when many color groups multiply trace count."""
    if n_color_groups <= 3:
        return base_limit
    # Each color group becomes a separate trace; cap total to keep SVG fast
    return max(500, base_limit // n_color_groups)


def run_scatter_plot(df, x_col=None, y_col=None, color_col=None, size_col=None,
                     trendline=None, palette=None, render_mode="svg",
                     roles=None, **kwargs):
    charts = []
    # Shared role pools: the axis defaults are the user's metric family.
    num = role_pools(df, roles=roles)["num"]
    pal = palette or COLORS


    x = x_col or (num[0] if num else None)
    y = y_col or (num[1] if len(num) > 1 else num[0] if num else None)
    if not x or not y or x not in df.columns or y not in df.columns:
        return []


    color = color_col if color_col and color_col in df.columns else None
    tl    = trendline.lower() if trendline and trendline.lower() != "none" else None


    # Adaptive sampling: lower cap for SVG stability, allow higher only for WebGL
    _render = (render_mode or "svg").lower()
    if _render not in ("svg", "webgl"):
        _render = "svg"
    _sample_limit = 8_000 if _render == "webgl" else _adaptive_sample_limit(
        n_color_groups=df[color].nunique() if color else 1
    )
    plot_df, sampled = sample_for_plot(df, n=_sample_limit)
    n_pts   = len(plot_df)
    opacity = _opacity(n_pts)


    size_arr = None
    if size_col and size_col in df.columns:
        try:
            raw = pd.to_numeric(df[size_col], errors="coerce").reindex(plot_df.index)
            if raw.dropna().nunique() > 1:
                size_arr = _normalise_size(raw.fillna(raw.median()))
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass


    r_val = None
    if not color:
        try:
            r_val = float(plot_df[x].corr(plot_df[y]))
            if r_val != r_val:
                r_val = None
        except Exception:
            r_val = _pearson_r(plot_df[x], plot_df[y])


    r_str      = f"  ·  r = {r_val:+.3f}" if r_val is not None else ""
    sample_str = f"  ({n_pts:,} of {len(df):,} rows)" if sampled else ""
    title      = f"Scatter: {x} vs {y}{r_str}{sample_str}"


    extra_cols = [c for c in [color, size_col] if c and c in plot_df.columns and c not in (x, y)]
    
    # Minimal hover by default for performance; only include extra data if requested
    _hover = f"<b>{x}:</b> %{{x:,}}<br><b>{y}:</b> %{{y:,}}<extra></extra>"
    _custom_data = None
    if kwargs.get("show_value_labels") and extra_cols:
        # Only build rich hover when value labels are explicitly enabled
        ht_lines = [f"<b>{x}:</b> %{{x:,}}", f"<b>{y}:</b> %{{y:,}}"]
        for i, col in enumerate(extra_cols):
            ht_lines.append(f"<b>{col}:</b> %{{customdata[{i}]:,}}")
        _hover = "<br>".join(ht_lines) + "<extra></extra>"
        _custom_data = extra_cols if _render == "svg" else None

    # Disable marker edge lines by default (adds SVG rendering cost)
    _marker_line = None
    if _render == "svg" and kwargs.get("marker_size", 6) > 10:
        # Only add edges for large markers where they actually help visibility
        _marker_line = dict(width=0.3, color="rgba(255,255,255,0.15)")

    fig = px.scatter(
        plot_df, x=x, y=y,
        color=color,
        title=title,
        color_discrete_sequence=pal,
        opacity=opacity,
        # trendline is NOT passed to px.scatter here — Plotly Express
        # silently requires scipy for trendline computation, and without it
        # the parameter is simply ignored with no error or warning.
        # Instead, we add the trendline manually below via _add_trendline()
        # which uses a pure-numpy OLS fallback when scipy is absent.
        custom_data=_custom_data,
        render_mode=_render,
    )
    # Manually add trendline trace — works with or without scipy
    if tl:
        _add_trendline(fig, plot_df, x, y, tl)


    if size_arr is not None:
        for trace in fig.data:
            if hasattr(trace, "marker") and getattr(trace, "mode", "") != "lines":
                try:
                    if color and hasattr(trace, "name") and trace.name is not None:
                        mask = plot_df[color].astype(str) == str(trace.name)
                        trace.marker.size = size_arr[mask].values
                    else:
                        trace.marker.size = size_arr.values
                except Exception as exc:
                    logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
                    pass


    fig.update_traces(
        selector=dict(mode="markers"),
        hovertemplate=_hover,
        marker=dict(line=_marker_line),
    )
    if tl:
        fig.update_traces(
            selector=lambda t: (
                getattr(t, "mode", "") == "lines"
                and "trend" in str(getattr(t, "name", "")).lower()
            ),
            line=dict(width=2, dash="dot"),
            hovertemplate=f"<b>{y} (trend):</b> %{{y:,.3f}}<extra></extra>",
        )


    axis_style = dict(
        tickfont=dict(color="#94a3b8", size=11),
        title=dict(font=dict(color="#cbd5e1", size=12)),
        showgrid=False,
        zeroline=False,
        linecolor="rgba(100,116,139,0.3)",
        zerolinecolor="rgba(100,116,139,0.25)",
        automargin=True,
    )
    fig.update_layout(**chart_layout())
    fig.update_layout(
        xaxis_title=x,
        yaxis_title=y,
        xaxis=axis_style,
        yaxis=axis_style,
        legend=dict(orientation="v", x=1.01, y=1),
    )


    if r_val is not None:
        strength  = "strong" if abs(r_val) >= 0.7 else "moderate" if abs(r_val) >= 0.4 else "weak"
        direction = "positive" if r_val > 0 else "negative"
        fig.add_annotation(
            text=f"r = {r_val:+.3f}  ({strength} {direction})",
            xref="paper", yref="paper", x=0.01, y=0.99,
            showarrow=False, xanchor="left", yanchor="top",
            font=dict(size=11, color="#94a3b8"),
            bgcolor="rgba(15,23,42,0.55)", borderpad=4,
        )


    
    # Store render mode in meta so chart_card / export can inspect it
    fig._lytrize_meta = {
        "analysis_type": "scatter",
        "x_axis": x,
        "y_axis": y,
        "legend": color,
        "supports_notes": True,
        "supports_axis_editing": True,
        "supports_legend_editing": True,
        "render_mode": _render,
        "n_points": n_pts,
    }


    charts.append((f"Scatter: {x} vs {y}", fig))


    return charts




