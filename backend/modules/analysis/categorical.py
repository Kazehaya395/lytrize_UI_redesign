"""modules/analysis/categorical.py -- Categorical bar chart runner.

The default dimension comes from the shared role pools
(``services.column_roles``) rather than a local ``select_dtypes("object")``
call, so a ``category``/``string``/``bool`` column and a datetime column are
both offered, and a numeric column the user reclassified as a dimension is
honoured. See ``correlation.py`` for the full rationale.
"""


import plotly.graph_objects as go
from plotly.subplots import make_subplots
from modules.charts import chart_layout, COLORS
from modules.analysis.apply_lytrize_standard import apply_lytrize_standard
from services.column_roles import role_pools




def _luminance(hex_color: str) -> float:
    """Perceived brightness of a hex colour via ITU-R BT.601 coefficients (0-255)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 128.0
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b




def _pick_contrast_color(pal: list, exclude_idx: int = 0) -> str:
    """Return the palette colour most visually distinct from pal[exclude_idx]."""
    if not pal:
        return "#f59e0b"
    base_lum   = _luminance(pal[exclude_idx])
    best_color = pal[0]
    best_dist  = -1.0
    for i, c in enumerate(pal):
        if i == exclude_idx:
            continue
        dist = abs(_luminance(c) - base_lum)
        if dist > best_dist:
            best_dist  = dist
            best_color = c
    return best_color if best_dist > 5 else "#f59e0b"




_SORT = {
    "Value (Desc)":   lambda d, vc: d.sort_values(vc, ascending=False),
    "Value (Asc)":    lambda d, vc: d.sort_values(vc, ascending=True),
    "Category (A-Z)": lambda d, vc: d.sort_values(d.columns[0], ascending=True),
    "Category (Z-A)": lambda d, vc: d.sort_values(d.columns[0], ascending=False),
}




def _sort(df, val_col: str, sort_by: str):
    """Apply the requested sort to an aggregated DataFrame."""
    fn = _SORT.get(sort_by)
    return fn(df, val_col) if fn else df




def _apply_plotly_sort(fig, cats: list, is_horiz: bool, sort_by: str):
    """Force Plotly to respect the DataFrame's pre-sorted category order."""
    if is_horiz:
        fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(cats)))
    else:
        fig.update_xaxes(categoryorder="array", categoryarray=cats)
    return fig




def run_categorical(df, x_cols=None, y_cols=None, agg="mean", sort_by=None,
                    palette=None, top_n=None, dual_y_col=None, dual_y_agg=None,
                    direction="Vertical (Column chart)", roles=None, **_):
    """Generate categorical bar / column charts."""
    charts   = []
    # `dim` (not `cat`): a datetime is a valid grouping key, and a column the
    # user reclassified as a dimension must be offered even if it is numeric.
    dims     = x_cols or role_pools(df, roles=roles)["dim"][:4]
    metrics  = y_cols
    agg_lbl  = agg.title()
    pal      = palette or COLORS
    is_horiz = "Horizontal" in str(direction)
    sec_agg     = dual_y_agg or agg
    sec_agg_lbl = sec_agg.title()


    for col in dims:


        if metrics:
            for metric in metrics:
                agg_df = df.groupby(col)[metric].agg(agg).reset_index()
                agg_df.columns = [col, "val"]
                agg_df = _sort(agg_df, "val", sort_by)
                if top_n and top_n > 0:
                    agg_df = agg_df.nlargest(top_n, "val")
                agg_df   = agg_df.reset_index(drop=True)
                top_sfx  = f" (Top {top_n})" if top_n else ""


                dual = dual_y_col
                if dual and dual in df.columns and dual != metric:
                    d2 = df.groupby(col)[dual].agg(sec_agg).reset_index()
                    d2.columns = [col, "val2"]
                    merged = agg_df.merge(d2, on=col, how="left")
                    cats   = merged[col].tolist()
                    v1     = merged["val"].tolist()
                    v2     = merged["val2"].tolist()


                    fig = make_subplots(specs=[[{"secondary_y": True}]])
                    bar_x, bar_y = (v1, cats) if is_horiz else (cats, v1)
                    fig.add_trace(go.Bar(
                        x=bar_x, y=bar_y,
                        orientation="h" if is_horiz else "v",
                        name=f"{agg_lbl} {metric}",
                        marker_color=pal[0],
                        text=[f"{v:,.1f}" for v in v1],
                        textposition="outside",
                        cliponaxis=False,
                        hovertemplate="%{x}<br>%{text}<extra></extra>" if not is_horiz else "%{y}<br>%{text}<extra></extra>",
                    ), secondary_y=False)
                    line_color = _pick_contrast_color(pal, 0)
                    fig.add_trace(go.Scatter(
                        x=cats, y=v2,
                        name=f"{sec_agg_lbl} {dual}",
                        mode="lines+markers+text",
                        line=dict(color=line_color, width=2),
                        marker=dict(size=8, color=line_color),
                        text=[f"{v:,.1f}" for v in v2],
                        textposition="top center",
                        textfont=dict(color=line_color),
                        texttemplate=None,
                        hovertemplate="%{x}<br>%{text}<extra></extra>",
                    ), secondary_y=True)
                    fig.data[-1].mode = "lines+markers"
                    fig.update_layout(
                        title=f"{agg_lbl} {metric} & {sec_agg_lbl} {dual} by {col}{top_sfx}",
                        **chart_layout())
                    fig.update_yaxes(title_text=f"{agg_lbl} {metric}", secondary_y=False)
                    fig.update_yaxes(title_text=f"{sec_agg_lbl} {dual}",   secondary_y=True)
                    if is_horiz:
                        fig.update_layout(margin=dict(l=20, r=100, t=56, b=20))
                    _apply_plotly_sort(fig, cats, is_horiz, sort_by)


                else:
                    cats   = agg_df[col].tolist()
                    vals   = agg_df["val"].tolist()
                    texts  = [f"{v:,.1f}" for v in vals]
                    bar_x, bar_y = (vals, cats) if is_horiz else (cats, vals)
                    colors = [pal[i % len(pal)] for i in range(len(cats))]


                    fig = go.Figure(go.Bar(
                        x=bar_x, y=bar_y,
                        orientation="h" if is_horiz else "v",
                        marker_color=colors,
                        text=texts,
                        textposition="outside",
                        cliponaxis=False,
                        hovertemplate="%{x}<br>%{text}<extra></extra>" if not is_horiz else "%{y}<br>%{text}<extra></extra>",
                    ))
                    d_lbl = "Bar" if is_horiz else "Column"
                    fig.update_layout(
                        title=f"{d_lbl}: {agg_lbl} {metric} by {col}{top_sfx}",
                        showlegend=False,
                        **chart_layout())
                    if is_horiz:
                        fig.update_layout(margin=dict(l=20, r=100, t=56, b=20))
                    else:
                        fig.update_layout(margin=dict(l=20, r=20, t=56, b=60))
                    _apply_plotly_sort(fig, cats, is_horiz, sort_by)


                _chart_title = f"{agg_lbl} {metric} by {col}"
                apply_lytrize_standard(fig, title=_chart_title,
                                       xaxis=col if not is_horiz else f"{agg_lbl} {metric}",
                                       yaxis=f"{agg_lbl} {metric}" if not is_horiz else col,
                                       analysis_type="categorical")
                charts.append((_chart_title, fig))


        else:
            vc = df[col].value_counts().reset_index()
            vc.columns = [col, "Count"]
            vc = _sort(vc, "Count", sort_by)
            if top_n and top_n > 0:
                vc = vc.nlargest(top_n, "Count")
            vc      = vc.reset_index(drop=True)
            top_sfx = f" (Top {top_n})" if top_n else ""


            cats   = vc[col].tolist()
            vals   = vc["Count"].tolist()
            texts  = [str(v) for v in vals]
            bar_x, bar_y = (vals, cats) if is_horiz else (cats, vals)
            colors = [pal[i % len(pal)] for i in range(len(cats))]
            d_lbl  = "Bar" if is_horiz else "Column"


            fig = go.Figure(go.Bar(
                x=bar_x, y=bar_y,
                orientation="h" if is_horiz else "v",
                marker_color=colors,
                text=texts,
                textposition="outside",
                cliponaxis=False,
            ))
            fig.update_layout(
                title=f"{d_lbl} Counts: {col}{top_sfx}",
                showlegend=False,
                **chart_layout())
            if is_horiz:
                fig.update_layout(margin=dict(l=20, r=100, t=56, b=20))
            _apply_plotly_sort(fig, cats, is_horiz, sort_by)


            _counts_title = f"Counts: {col}"
            apply_lytrize_standard(fig, title=_counts_title,
                                   xaxis=col if not is_horiz else "Count",
                                   yaxis="Count" if not is_horiz else col,
                                   analysis_type="categorical")
            charts.append((_counts_title, fig))


    return charts
