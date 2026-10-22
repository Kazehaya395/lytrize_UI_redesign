"""Native Polars time-series analysis runner."""
from modules.analysis._native_compat import run_native

def run_time_series(df, **kwargs):
    return run_native("time_series", df, **kwargs)
