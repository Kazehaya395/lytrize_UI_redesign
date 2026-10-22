"""Native Polars categorical analysis runner."""
from modules.analysis._native_compat import run_native

def run_categorical(df, **kwargs):
    return run_native("categorical", df, **kwargs)
