"""modules/analysis/pie_chart.py -- Pie / Donut chart runner.

The default dimension comes from the shared role pools
(``services.column_roles``) rather than a local ``select_dtypes("object")``
call, so ``category``/``string``/``bool`` columns and a column the user
reclassified as a dimension are both offered. See ``correlation.py`` for the
full rationale.
"""


import plotly.express as px
import pandas as pd
from modules.charts import chart_layout, COLORS
from modules.analysis.apply_lytrize_standard import apply_lytrize_standard
from services.column_roles import role_pools




def _sort_df(df_target, col_x: str, col_y: str, sort_by: str):
    """Apply the requested sort order to an aggregated DataFrame."""
    if sort_by == "Value (Desc)":   return df_target.sort_values(col_y, ascending=False)
    if sort_by == "Value (Asc)":    return df_target.sort_values(col_y, ascending=True)
    if sort_by == "Category (A-Z)": return df_target.sort_values(col_x, ascending=True)
    if sort_by == "Category (Z-A)": return df_target.sort_values(col_x, ascending=False)
    return df_target




def _apply_top_n(df_agg, name_col: str, val_col: str, top_n, group_others: bool = True):
    """Keep the top N rows by value; bundle remaining rows into an "Other" slice."""
    if not top_n or top_n <= 0 or len(df_agg) <= top_n:
        return df_agg


    top  = df_agg.nlargest(top_n, val_col)
    rest = df_agg[~df_agg[name_col].isin(top[name_col])]


    if group_others and len(rest) > 0:
        other_row = pd.DataFrame({name_col: ["Other"], val_col: [rest[val_col].sum()]})
        top = pd.concat([top, other_row], ignore_index=True)


    return top




def run_pie_chart(df, x_cols=None, y_cols=None, agg="mean", sort_by=None,
                  palette=None, top_n=None, roles=None, **kwargs):
    """Generate donut charts for categorical dimensions."""
    charts    = []
    dims      = x_cols or role_pools(df, roles=roles)["dim"][:2]
    metrics   = y_cols
    agg_label = agg.title()
    pal       = palette or COLORS


    for col in dims:


        if metrics:
            for metric in metrics:
                agg_vals = df.groupby(col)[metric].agg(agg).reset_index()
                agg_vals.columns = [col, "Value"]
                agg_vals = _sort_df(agg_vals, col, "Value", sort_by)
                agg_vals = _apply_top_n(agg_vals, col, "Value", top_n)


                title_suffix = (
                    f" (Top {top_n})" if top_n and len(df[col].unique()) > top_n else "")
                fig = px.pie(
                    agg_vals, names=col, values="Value",
                    title=f"{agg_label} {metric} Split by {col}{title_suffix}",
                    color_discrete_sequence=pal,
                    hole=0.45)
                fig.update_layout(**chart_layout())
                fig.update_traces(
                    hovertemplate="%{label}<br>Value=%{value:.2f}<br>Percent=%{percent:.2%}<extra></extra>"
                )
                apply_lytrize_standard(fig, title=f"{agg_label} {metric} Split by {col}{title_suffix}",
                                       legend=col, analysis_type="pie_chart",
                                       axis_editing=False)
                charts.append((f"Pie: {col}", fig))


        else:
            vc = df[col].value_counts().reset_index()
            vc.columns = [col, "Count"]
            vc = _sort_df(vc, col, "Count", sort_by)
            vc = _apply_top_n(vc, col, "Count", top_n)


            title_suffix = (
                f" (Top {top_n})" if top_n and len(df[col].unique()) > top_n else "")
            fig = px.pie(
                vc, names=col, values="Count",
                title=f"Distribution of {col}{title_suffix}",
                color_discrete_sequence=pal,
                hole=0.45)
            fig.update_layout(**chart_layout())
            fig.update_traces(
                hovertemplate="%{label}<br>Value=%{value:.2f}<br>Percent=%{percent:.2%}<extra></extra>"
            )
            apply_lytrize_standard(fig, title=f"Distribution of {col}{title_suffix}",
                                   legend=col, analysis_type="pie_chart",
                                   axis_editing=False)
            charts.append((f"Pie Counts: {col}", fig))


    return charts
