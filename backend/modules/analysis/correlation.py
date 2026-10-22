"""Native Polars correlation analysis runner."""
from modules.analysis._native_compat import run_native

def run_correlation(df, **kwargs):
    return run_native("correlation", df, **kwargs)
