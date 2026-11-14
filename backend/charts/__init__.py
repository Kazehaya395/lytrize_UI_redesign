"""backend/charts -- Modular Chart Architecture for Lytrize (Apache ECharts)."""

from services.polars_chart_service import (
    build_pie_chart,
    build_categorical_charts,
    build_statistical_charts,
    build_correlation_chart,
    build_matrix_heatmap_chart,
    build_matrix_table_chart,
    build_descriptive_table_chart,
    build_outlier_charts,
    build_data_quality_charts,
    build_choropleth_chart,
    build_map_plot_chart,
    build_nightingale_rose_chart,
    build_advanced_chart,
    build_time_series_chart,
    build_distribution_charts,
    build_scatter_chart,
    apply_chart_model_display_options,
)

__all__ = [
    "build_pie_chart",
    "build_categorical_charts",
    "build_statistical_charts",
    "build_correlation_chart",
    "build_matrix_heatmap_chart",
    "build_matrix_table_chart",
    "build_descriptive_table_chart",
    "build_outlier_charts",
    "build_data_quality_charts",
    "build_choropleth_chart",
    "build_map_plot_chart",
    "build_nightingale_rose_chart",
    "build_advanced_chart",
    "build_time_series_chart",
    "build_distribution_charts",
    "build_scatter_chart",
    "apply_chart_model_display_options",
]

