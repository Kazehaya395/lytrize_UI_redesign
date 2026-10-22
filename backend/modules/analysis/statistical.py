"""Native Polars statistical analysis runner."""
from modules.analysis._native_compat import run_native

def run_statistical(df, **kwargs):
    return run_native("statistical", df, **kwargs)
