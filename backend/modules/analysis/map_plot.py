"""Compatibility shim for the native offline Map Plot implementation.

All map calculation and rendering now flows through Polars ChartModels and
Apache ECharts. This module intentionally contains no Plotly or network tile
code; it remains as a stable import point for downstream integrations.
"""
from __future__ import annotations

from services.polars_engine import PolarsDataset
from services.polars_chart_service import build_map_plot_chart

RENDER_MODES = ["Density (all points)", "Sample 5,000"]
MAP_STYLES = ["Local boundaries (Light)", "Local boundaries (Dark)"]


def run_map_plot(df, **config):
    """Return a native ChartModel tuple for integrations using the old API."""
    dataset = df if isinstance(df, PolarsDataset) else PolarsDataset.from_pandas(
        df, source="map-compatibility-boundary")
    model = build_map_plot_chart(dataset, config)
    return [(model["uid"], model["title"], model)] if model else []
