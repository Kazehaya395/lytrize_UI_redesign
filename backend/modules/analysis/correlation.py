"""modules/analysis/correlation.py -- Pearson correlation heatmap runner.

Column sourcing
---------------
The columns come from the shared role pools (``services.column_roles``), NOT
from a local ``select_dtypes`` call. The old numeric fallback had a concrete
failure: a user who reclassified an id-like numeric as a dimension on the
Upload page still saw it here as a correlation candidate, because this module
re-derived "numeric" from the raw dtype. Passing ``roles`` through
``services.analysis_service`` means the Columns list is exactly the metric
pool the rest of the app shows.

A correlation coefficient is only defined over real numbers, so the chosen
columns are additionally filtered through ``numeric_subset``. That is a
safety net against a mislabelled column, not a second source of truth: the
role pool still decides what is *offered*.
"""

import plotly.express as px
from modules.charts import chart_layout, COLORS
from modules.analysis.apply_lytrize_standard import apply_lytrize_standard
from services.column_roles import numeric_subset, role_pools




def run_correlation(df, x_cols=None, y_cols=None, palette=None,
                    roles=None, **kwargs):
    """Generate a Pearson correlation heatmap."""
    charts = []

    # Shared role pools: `num` is the user's metric family, so a column they
    # reclassified as a dimension is no longer offered here.
    pools = role_pools(df, roles=roles)
    num = numeric_subset(df, list(dict.fromkeys(
        (x_cols or []) + (y_cols or []) or pools["num"])))
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
