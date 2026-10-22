"""Native Polars scatter analysis runner."""
from modules.analysis._native_compat import run_native

def run_scatter_plot(df, **kwargs):
    return run_native("scatter_plot", df, **kwargs)
