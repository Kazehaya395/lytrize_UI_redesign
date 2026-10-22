"""Native Polars distribution analysis runner."""
from modules.analysis._native_compat import run_native

def run_distribution(df, **kwargs):
    return run_native("distribution", df, **kwargs)
