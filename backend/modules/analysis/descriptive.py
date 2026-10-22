"""Descriptive statistics builder backed by Polars and native table models."""

from __future__ import annotations

def run_descriptive(df, roles=None, layout=None, typography=None, **kwargs):
    """Build a semantic Descriptive Statistics table.

    ``df`` is accepted for the analysis-service compatibility API. New callers
    should pass ``polars_dataset`` and avoid crossing the pandas boundary.
    The return shape remains a list of ``(title, chart model)`` pairs, but no
    Plotly figure is created.
    """
    dataset = kwargs.get("polars_dataset")
    if dataset is None:
        from services.polars_engine import PolarsDataset
        dataset = PolarsDataset.from_pandas(df)
    from services.polars_chart_service import build_descriptive_table_chart
    model = build_descriptive_table_chart(
        dataset, roles=roles, layout=layout, typography=typography)
    return [(model["title"], model)] if model else []
