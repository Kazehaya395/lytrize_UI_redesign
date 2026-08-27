import logging


"""modules/analysis/apply_lytrize_standard.py -- Universal chart standardiser."""


import importlib.metadata


from modules.charts import chart_layout




def apply_lytrize_standard(
    fig,
    *,
    title,
    xaxis=None,
    yaxis=None,
    legend=None,
    height=None,
    analysis_type="generic",
    notes_supported=True,
    axis_editing=True,
    legend_editing=True,
):
    """Universal Lytrize chart standardiser."""


    layout = chart_layout(height=height)


    title_dict = dict(text=title)


    fig.update_layout(
        **layout,
        title=title_dict,
        legend=dict(
            title=dict(text=legend or ""),
            bgcolor="rgba(15,23,42,0.35)",
            orientation="v"
        )
    )


    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=False, zeroline=False)


    if xaxis:
        fig.update_xaxes(title=xaxis)


    if yaxis:
        fig.update_yaxes(title=yaxis)


    for trace in fig.data:
        try:
            ht = getattr(trace, "hovertemplate", None)
            if ht and "%{fullData.name}" in ht:
                trace.hovertemplate = ht.replace(
                    "<extra>%{fullData.name}</extra>",
                    "<extra></extra>"
                )
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass


    fig._lytrize_meta = {
        "analysis_type": analysis_type,
        "x_axis": xaxis,
        "y_axis": yaxis,
        "legend": legend,
        "supports_notes": notes_supported,
        "supports_axis_editing": axis_editing,
        "supports_legend_editing": legend_editing,
        "is_correlation": (analysis_type == "correlation"),
        "x_label": xaxis or "",
        "y_label": yaxis or "",
        "legend_title": legend or "",
    }


    return fig
