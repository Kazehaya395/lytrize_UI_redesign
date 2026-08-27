"""modules/analysis/distribution.py -- Distribution histogram runner."""


import logging

import plotly.express as px
from modules.charts import chart_layout, COLORS, num_cols as _num_cols
from modules.utils.perf import sample_for_histogram
from modules.analysis.apply_lytrize_standard import apply_lytrize_standard

log = logging.getLogger(__name__)




def run_distribution(df, x_cols=None, y_cols=None, palette=None, **kwargs):
    """Generate histogram + box-plot marginal charts for numeric columns.

    Pure function: no UI side effects. If the caller wants to surface the
    "sampled for performance" notice to the user, it should check the row
    count itself before calling, or log.info below is available in logs.
    """
    df, was_sampled = sample_for_histogram(df)
    if was_sampled:
        log.info("Distribution chart: sampled 50,000 rows for performance.")


    charts    = []
    num       = x_cols or _num_cols(df)[:6]
    pal       = palette or COLORS
    color_col = y_cols[0] if y_cols else None


    for i, col in enumerate(num):
        if color_col and color_col in df.columns:
            fig = px.histogram(
                df,
                x=col,
                color=color_col,
                nbins=35,
                barmode="overlay",
                opacity=0.75,
                title=f"Distribution: {col} by {color_col}",
                color_discrete_sequence=pal,
            )
        else:
            fig = px.histogram(
                df,
                x=col,
                nbins=35,
                title=f"Distribution: {col}",
                color_discrete_sequence=[pal[i % len(pal)]],
            )


        fig.update_layout(**chart_layout())
        label = f"Dist: {col} by {color_col}" if color_col else f"Dist: {col}"
        apply_lytrize_standard(fig, title=label, xaxis=col, yaxis="Count",
                               analysis_type="distribution")

        charts.append((label, fig))


    return charts
