"""modules/analysis/time_series.py -- Time series line chart runner."""
import logging


import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from modules.charts import chart_layout, COLORS
from modules.analysis.apply_lytrize_standard import apply_lytrize_standard




_MONTH = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}


_WEEKDAY = {d: i for i, d in enumerate(
    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}




def run_time_series(df, x_cols=None, y_cols=None, agg="mean", date_part=None,
                    palette=None, dual_y_col=None, dual_y_agg=None, **_):
    """Generate time-series line charts for selected numeric metrics."""
    charts       = []
    df           = df.copy()
    dt_col       = (x_cols or [None])[0]
    agg_lbl      = agg.title()
    pal          = palette or COLORS
    dual_agg     = dual_y_agg or agg
    dual_agg_lbl = dual_agg.title()


    if not dt_col:
        for c in df.columns:
            try:
                df[c] = pd.to_datetime(df[c])
                dt_col = c
                break
            except Exception as exc:
                logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
                pass


    if dt_col and not pd.api.types.is_datetime64_any_dtype(df[dt_col]):
        df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")


    num       = y_cols or []
    plot_x    = dt_col
    x_label   = dt_col or "Index"
    order_map = None


    if dt_col and date_part:
        tmp = pd.to_datetime(df[dt_col].astype(str), errors="coerce")


        if date_part == "month_name":
            df["_p"] = tmp.dt.month_name()
            order_map = _MONTH
            plot_x, x_label = "_p", "Month"


        elif date_part == "weekday_name":
            df["_p"] = tmp.dt.day_name()
            order_map = _WEEKDAY
            plot_x, x_label = "_p", "Weekday"


        else:
            try:
                df["_p"] = tmp.dt.to_period(date_part).astype(str)
                plot_x, x_label = "_p", f"{dt_col} ({date_part})"
            except Exception as exc:
                logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
                pass


    for i, col in enumerate(num):
        c_pri = pal[i % len(pal)]
        c_sec = pal[(i + 1) % len(pal)]


        if dt_col and plot_x == "_p":
            g = df.groupby("_p")[col].agg(agg).reset_index()
            g.columns = ["_p", col]
            if order_map:
                g["_s"] = g["_p"].map(order_map)
                g = g.sort_values("_s").drop(columns="_s")
            else:
                g = g.sort_values("_p")
            x_vals = g["_p"].tolist()
            y_vals = g[col].tolist()


        elif dt_col:
            sd = df.sort_values(dt_col)
            x_vals = sd[dt_col].tolist()
            y_vals = sd[col].tolist()


        else:
            r = df.reset_index()
            x_vals = r["index"].tolist()
            y_vals = r[col].tolist()


        dual        = dual_y_col
        dual_valid  = (dual and dual != col and dual in df.columns)
        y2_vals     = None


        if dual_valid:
            if dt_col and plot_x == "_p":
                g2 = df.groupby("_p")[dual].agg(dual_agg).reset_index()
                g2.columns = ["_p", dual]
                if order_map:
                    g2["_s"] = g2["_p"].map(order_map)
                    g2 = g2.sort_values("_s").drop(columns="_s")
                else:
                    g2 = g2.sort_values("_p")
                y2_vals = g2[dual].tolist()
            elif dt_col:
                y2_vals = df.sort_values(dt_col)[dual].tolist()
            else:
                y2_vals = df.reset_index()[dual].tolist()


        if dual_valid and y2_vals is not None:
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            fig.add_trace(go.Scatter(
                x=x_vals, y=y_vals, mode="lines+markers",
                name=f"{agg_lbl} {col}",
                line=dict(color=c_pri, width=2), marker=dict(size=5),
                hovertemplate=f"<b>{agg_lbl} {col}</b><br>" + "%{x}<br>Value: <b>%{y:,.2f}</b><extra></extra>",
            ), secondary_y=False)
            fig.add_trace(go.Scatter(
                x=x_vals, y=y2_vals, mode="lines+markers",
                name=f"{dual_agg_lbl} {dual}",
                line=dict(color=c_sec, width=2, dash="dash"),
                marker=dict(size=5),
                hovertemplate=f"<b>{dual_agg_lbl} {dual}</b><br>" + "%{x}<br>Value: <b>%{y:,.2f}</b><extra></extra>",
            ), secondary_y=True)
            _ts_title = f"TS: {col} & {dual}"
            fig.update_layout(
                title=f"{agg_lbl} {col} & {dual_agg_lbl} {dual} over {x_label}",
                **chart_layout())
            if plot_x == "_p":
                fig.update_xaxes(type="category", title_text=x_label)
            fig.update_yaxes(title_text=f"{agg_lbl} {col}", secondary_y=False)
            fig.update_yaxes(title_text=f"{dual_agg_lbl} {dual}", secondary_y=True)
            fig.update_layout(hovermode='x unified')
            apply_lytrize_standard(fig, title=_ts_title,
                                   xaxis=dt_col or "Index", yaxis=col,
                                   analysis_type="time_series")


        else:
            if dt_col and plot_x == "_p":
                fig = px.line(
                    x=x_vals, y=y_vals,
                    title=f"{agg_lbl} {col} by {x_label}",
                    color_discrete_sequence=[c_pri], markers=True)
                fig.update_xaxes(type="category", title_text=x_label)
            elif dt_col:
                fig = px.line(
                    x=x_vals, y=y_vals,
                    title=f"Time Series: {col}",
                    color_discrete_sequence=[c_pri])
            else:
                fig = px.line(
                    x=x_vals, y=y_vals,
                    title=f"Trend: {col}",
                    color_discrete_sequence=[c_pri])
            fig.update_layout(**chart_layout())
            fig.update_traces(
                hovertemplate=f"<b>{agg_lbl} {col}</b><br>" + "%{x}<br>Value: <b>%{y:,.2f}</b><extra></extra>"
            )
            _ts_title = f"TS: {col}"
            apply_lytrize_standard(fig, title=_ts_title,
                                   xaxis=dt_col or "Index", yaxis=col,
                                   analysis_type="time_series")
        charts.append((_ts_title, fig))


    return charts
