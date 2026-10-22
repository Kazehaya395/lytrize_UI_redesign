"""Native Polars matrix heatmap and pivot table runners."""
from modules.analysis._native_compat import run_native

def run_matrix_heatmap(df, **kwargs):
    return run_native("matrix_heatmap", df, **kwargs)

def run_matrix_table(df, **kwargs):
    return run_native("matrix_table", df, **kwargs)
