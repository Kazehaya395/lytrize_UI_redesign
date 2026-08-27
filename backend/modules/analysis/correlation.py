"""modules/analysis/correlation.py -- Pearson correlation heatmap runner."""


import plotly.express as px
from modules.charts import chart_layout, COLORS, num_cols as _num_cols
from modules.analysis.apply_lytrize_standard import apply_lytrize_standard




def run_correlation(df, x_cols=None, y_cols=None, palette=None, **kwargs):
    """Generate a Pearson correlation heatmap."""
    charts = []


    num = list(dict.fromkeys((x_cols or []) + (y_cols or []) or _num_cols(df)))
    if len(num) < 2:
        return charts


    pal  = palette or COLORS
    corr = df[num].corr()


    fig = px.imshow(
        corr,
        title="Correlation Heatmap",
        color_continuous_scale=pal,
        color_continuous_midpoint=0,
        aspect="auto",
        zmin=-1, zmax=1)
    for tr in fig.data:
        if str(getattr(tr, "type", "")).lower() == "heatmap":
            tr.texttemplate = "%{z:.2f}"
            tr.hovertemplate = "x: %{x}<br>y: %{y}<br>correlation: %{z:.3f}<extra></extra>"
    fig.update_layout(**chart_layout())
    apply_lytrize_standard(fig, title="Correlation Heatmap",
                           analysis_type="correlation")
    fig._lytrize_meta = {
        "analysis_type": "correlation",
        "x_axis": None, "y_axis": None,
        "legend": None,
        "supports_axis_editing": False, "supports_legend_editing": True,
    }
    charts.append(("Correlation", fig))


    return charts
