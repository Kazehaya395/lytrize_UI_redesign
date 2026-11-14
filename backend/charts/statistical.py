"""backend/charts/statistical.py -- Statistical ECharts models (Boxplot, Histogram, Outlier, Correlation)."""

from services.polars_chart_service import (
    build_statistical_charts,
    build_distribution_charts,
    build_correlation_chart,
    build_matrix_heatmap_chart,
    build_matrix_table_chart,
    build_descriptive_table_chart,
    build_outlier_charts,
    build_data_quality_charts,
    build_scatter_chart,
)

__all__ = [
    "build_statistical_charts",
    "build_distribution_charts",
    "build_correlation_chart",
    "build_matrix_heatmap_chart",
    "build_matrix_table_chart",
    "build_descriptive_table_chart",
    "build_outlier_charts",
    "build_data_quality_charts",
    "build_scatter_chart",
]

