"""modules/analysis/outlier.py -- IQR outlier detection.

Headless module: every function here is pure (DataFrame in, results or a
new DataFrame out) so it can be called from the analysis service or the
Qt data-cleaning UI without any Streamlit/session-state dependency.
Row deletion is exposed via ``remove_outlier_rows()``; callers (Qt page /
worker) are responsible for taking the returned DataFrame and pushing it
back through ``SessionBridge.set_df()``, and for calling
``modules.database.log_activity`` themselves if they want an audit trail.
"""


import pandas as pd
import plotly.graph_objects as go


from modules.charts import chart_layout, COLORS
from services.column_roles import role_pools




def _df_fingerprint(df: pd.DataFrame) -> str:
    """Cheap fingerprint that changes when rows are added or deleted."""
    try:
        parts = [str(df.shape), str(df.iloc[0].tolist()), str(df.iloc[-1].tolist())]
    except Exception:
        parts = [str(df.shape)]
    return str(hash("".join(parts)))




def _compute_outliers(df: pd.DataFrame, cols: list, multiplier: float,
                      num_available=None) -> dict:
    """Run IQR outlier detection on the given columns.

    ``num_available`` is the set of columns arithmetic is safe on. It comes
    from the caller's role pools (see ``run_outlier``) so the same metric
    family the user sees drives detection; when omitted it is derived from
    the frame for the benefit of direct callers of this helper.
    """
    results = {}
    if num_available is None:
        num_available = set(role_pools(df)["num"])


    for col in cols:
        if col not in num_available:
            continue
        s = df[col].dropna()
        if len(s) == 0:
            continue


        q1  = s.quantile(0.25)
        q3  = s.quantile(0.75)
        iqr = q3 - q1
        lo  = q1 - multiplier * iqr
        hi  = q3 + multiplier * iqr


        out_mask  = (df[col] < lo) | (df[col] > hi)
        out_count = int(out_mask.sum())
        pct       = round(out_count / len(df) * 100, 2) if len(df) else 0.0


        results[col] = {
            "q1":          q1,
            "q3":          q3,
            "iqr":         iqr,
            "lo":          lo,
            "hi":          hi,
            "out_count":   out_count,
            "pct":         pct,
            "out_indices": df.index[out_mask].tolist(),
        }


    return results




def _make_outlier_fig(df: pd.DataFrame, col: str, info: dict) -> go.Figure:
    """Build a Plotly scatter figure visualising outliers for one column."""
    live_mask = (df[col] < info["lo"]) | (df[col] > info["hi"])
    nrm = df[~live_mask]
    out = df[live_mask]


    _OUTLIER_NORMAL_MAX = 8_000
    was_sampled = False
    if len(nrm) > _OUTLIER_NORMAL_MAX:
        nrm = nrm.sample(n=_OUTLIER_NORMAL_MAX, random_state=42)
        was_sampled = True


    fig = go.Figure()


    fig.add_trace(go.Scatter(
        x=nrm.index,
        y=nrm[col].values,
        mode="markers",
        name="Normal",
        marker=dict(color=COLORS[0], size=4, opacity=0.45),
        hovertemplate=(
            f"<b>Row:</b> %{{x}}<br>"
            f"<b>{col}:</b> %{{y:,.2f}}<br>"
            "<extra>Normal</extra>"
        ),
    ))


    if len(out):
        fig.add_trace(go.Scatter(
            x=out.index,
            y=out[col].values,
            mode="markers",
            name="Outlier ⚠️",
            marker=dict(color="#ef4444", size=9, symbol="x"),
            hovertemplate=(
                f"<b>Row:</b> %{{x}}<br>"
                f"<b>{col}:</b> %{{y:,.2f}}<br>"
                "<extra>⚠️ Outlier</extra>"
            ),
        ))


    fig.add_hline(
        y=info["hi"], line_dash="dash", line_color="#f59e0b",
        annotation_text=f"Upper fence: {info['hi']:.4g}",
        annotation_position="top right",
    )
    fig.add_hline(
        y=info["lo"], line_dash="dash", line_color="#f59e0b",
        annotation_text=f"Lower fence: {info['lo']:.4g}",
        annotation_position="bottom right",
    )


    n_out = int(live_mask.sum())
    _sample_note = (
        f"  ·  background sampled to {_OUTLIER_NORMAL_MAX:,} pts"
        if was_sampled else ""
    )
    fig.update_layout(
        title=f"Outliers — {col}  ({n_out} outlier{'s' if n_out != 1 else ''} detected{_sample_note})",
        xaxis_title="Row Index (hover to see exact row number)",
        yaxis_title=col,
        **chart_layout(),
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=False, zeroline=False)
    return fig




def detect_outliers(df: pd.DataFrame, cols: list, multiplier: float = 1.5) -> dict:
    """Pure detection entry point for the Qt data-cleaning UI.

    Returns the same per-column results dict as ``_compute_outliers``
    (q1/q3/iqr/lo/hi/out_count/pct/out_indices). Callers build their own
    summary table/UI from this; no rendering happens here.
    """
    return _compute_outliers(df, cols, multiplier)


def remove_outlier_rows(df: pd.DataFrame, indices: list) -> pd.DataFrame:
    """Return a new DataFrame with *indices* dropped and the index reset.

    Pure: does not mutate *df* in place and does not touch any global
    state. The caller (Qt page/worker) is responsible for pushing the
    result to ``SessionBridge.set_df()`` and logging the action via
    ``modules.database.log_activity`` if desired.
    """
    return df.drop(index=list(indices)).reset_index(drop=True)


def run_outlier(df, x_cols=None, y_cols=None, palette=None, roles=None,
                **kwargs):
    """IQR outlier scatter plots -- legacy runner."""
    from modules.charts import COLORS


    charts = []
    # Shared role pools: a column the user reclassified as a dimension is no
    # longer a candidate here, matching every other chart type.
    num_pool    = role_pools(df, roles=roles)["num"]
    num         = x_cols or num_pool[:6]
    pal         = palette or COLORS


    for col in num:
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if len(s) == 0:
            continue


        q1  = s.quantile(0.25)
        q3  = s.quantile(0.75)
        iqr = q3 - q1
        lo  = q1 - 1.5 * iqr
        hi  = q3 + 1.5 * iqr


        out_mask = (df[col] < lo) | (df[col] > hi)
        out      = df[out_mask]
        nrm      = df[~out_mask]


        _LEGACY_NORMAL_MAX = 8_000
        _was_sampled = len(nrm) > _LEGACY_NORMAL_MAX
        if _was_sampled:
            nrm = nrm.sample(n=_LEGACY_NORMAL_MAX, random_state=42)


        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=nrm.index, y=nrm[col].values,
            mode="markers", name="Normal",
            marker=dict(color=pal[0], size=4, opacity=0.45),
            hovertemplate=(
                f"<b>Row:</b> %{{x}}<br>"
                f"<b>{col}:</b> %{{y:,.2f}}<br>"
                "<extra>Normal</extra>"
            ),
        ))
        if len(out):
            fig.add_trace(go.Scatter(
                x=out.index, y=out[col].values,
                mode="markers", name="Outlier ⚠️",
                marker=dict(color="#ef4444", size=9, symbol="x"),
                hovertemplate=(
                    f"<b>Row:</b> %{{x}}<br>"
                    f"<b>{col}:</b> %{{y:,.2f}}<br>"
                    "<extra>⚠️ Outlier</extra>"
                ),
            ))
        fig.add_hline(y=hi, line_dash="dash", line_color="#f59e0b",
                      annotation_text=f"Upper IQR boundary: {hi:.2f}",
                      annotation_position="top right")
        fig.add_hline(y=lo, line_dash="dash", line_color="#f59e0b",
                      annotation_text=f"Lower IQR boundary: {lo:.2f}",
                      annotation_position="bottom right")


        n_out = int(out_mask.sum())
        _s_note = f"  ·  background sampled to {_LEGACY_NORMAL_MAX:,} pts" if _was_sampled else ""
        fig.update_layout(
            title=f"Outliers — {col}  ({n_out} outlier{'s' if n_out != 1 else ''} detected{_s_note})",
            xaxis_title="Row Index (hover to see exact row number)",
            yaxis_title=col,
            **chart_layout(),
        )
        fig.update_xaxes(showgrid=False, zeroline=False)
        fig.update_yaxes(showgrid=False, zeroline=False)
        charts.append((f"Outliers: {col}", fig))


    return charts




OUTLIER_HELP = (
    "**📊 How to read Outlier charts:**  "
    "Each dot is one row in your dataset. "
    "**🔴 Red × marks** are outliers — values that fall outside the IQR boundaries (dashed lines). "
    "**Hover** over any point to see its exact Row Index and value. "
    "The row index maps directly to the row number in your raw data table.\n\n"
    "**Business use:** Outliers often signal data-entry errors, fraud, returns, or exceptional events. "
    "Investigate red points before running predictive models — they can skew results significantly."
)
