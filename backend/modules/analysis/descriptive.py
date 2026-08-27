"""modules/analysis/descriptive.py -- Descriptive statistics table runner."""

import plotly.graph_objects as go

from modules.charts import num_cols as _num_cols, chart_layout, COLORS


def run_descriptive(df, **kwargs):
    """Return a descriptive statistics table for all numeric columns in *df*.

    Pure function: takes the DataFrame explicitly and returns a Plotly
    table figure so it fits the same ``(title, fig)`` contract as every
    other analysis runner and renders through the existing
    PlotlyView/ChartCard pipeline. No Streamlit, no session state.
    """
    num = _num_cols(df)
    if not num:
        return []

    # For very large files, describe() on a 100K sample is representative
    # enough for the summary table and avoids scanning 300 MB of data.
    if len(df) > 100_000:
        desc = df[num].sample(min(100_000, len(df)), random_state=42).describe().T.reset_index()
    else:
        desc = df[num].describe().T.reset_index()

    desc.columns = [c.title() if c != "index" else "Column" for c in desc.columns]

    num_desc_cols = desc.select_dtypes("number").columns
    desc[num_desc_cols] = desc[num_desc_cols].round(4)

    fig = go.Figure(data=[go.Table(
        header=dict(
            values=[f"<b>{c}</b>" for c in desc.columns],
            fill_color=COLORS[0],
            font=dict(color="white", size=12),
            align="left",
        ),
        cells=dict(
            values=[desc[c] for c in desc.columns],
            align="left",
        ),
    )])
    fig.update_layout(**chart_layout(), title="Descriptive Statistics")

    return [("Descriptive Statistics", fig)]
