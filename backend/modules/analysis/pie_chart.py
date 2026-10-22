"""Native Polars pie and donut analysis runner."""
from modules.analysis._native_compat import run_native

def run_pie_chart(df, **kwargs):
    return run_native("pie_chart", df, **kwargs)
