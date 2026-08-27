"""modules/analysis/data_quality.py

Headless module: every function is pure (DataFrame in, results/new
DataFrame out). No Streamlit, no session state. Summary chart generation
(``run_data_quality``) matches the standard ``(title, fig)`` contract
used by every other analysis runner. Cleaning actions (drop NA, drop
duplicates, delete specific rows) are exposed as separate pure functions
so the Qt data-cleaning UI can call them directly and push the result
through ``SessionBridge.set_df()`` itself.
"""


import plotly.express as px
import pandas as pd
from modules.charts import chart_layout, DANGER


# ── Pure summary/detection helpers ──────────────────────────────────────

def missing_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Return a table of (Column, Missing Count, Missing %) for columns with NAs."""
    mc = df.isnull().sum().reset_index()
    mc.columns = ["Column", "Missing Count"]
    mc["Missing %"] = (mc["Missing Count"] / len(df) * 100).round(2) if len(df) else 0.0
    mc = mc[mc["Missing Count"] > 0].sort_values("Missing Count", ascending=False)
    return mc.reset_index(drop=True)


def missing_row_mask(df: pd.DataFrame) -> pd.Series:
    """Return a boolean mask of rows that contain at least one missing value."""
    return df.isnull().any(axis=1)


def duplicate_count(df: pd.DataFrame, pk_col: str = None) -> int:
    """Return the number of duplicate rows, optionally scoped to *pk_col*."""
    subset = [pk_col] if pk_col else None
    return int(df.duplicated(subset=subset).sum())


def duplicate_rows(df: pd.DataFrame, pk_col: str = None) -> pd.DataFrame:
    """Return all rows involved in a duplicate group, optionally scoped to *pk_col*."""
    subset = [pk_col] if pk_col else None
    mask = df.duplicated(subset=subset, keep=False)
    return df[mask].sort_values(by=df.columns.tolist())


# ── Pure cleaning actions (return a NEW DataFrame; caller applies it) ──

def clean_dropna(df: pd.DataFrame, subset: list = None) -> pd.DataFrame:
    """Return a copy of *df* with rows containing NA dropped (optionally scoped to *subset*)."""
    return df.dropna(subset=subset).reset_index(drop=True)


def drop_duplicate_rows(df: pd.DataFrame, subset: list = None, keep: str = "first") -> pd.DataFrame:
    """Return a copy of *df* with duplicate rows removed."""
    return df.drop_duplicates(subset=subset, keep=keep).reset_index(drop=True)


def delete_rows(df: pd.DataFrame, indices: list) -> pd.DataFrame:
    """Return a copy of *df* with the given row indices removed."""
    return df.drop(index=list(indices)).reset_index(drop=True)


# ── Chart builders (pure) ───────────────────────────────────────────────

def _chart_missing_bar(mc: pd.DataFrame):
    fig = px.bar(
        mc, x="Column", y="Missing %",
        title="Missing % by Column",
        color="Missing %",
        color_continuous_scale=DANGER,
        text_auto=".1f",
    )
    fig.update_layout(**chart_layout())
    return fig


def _chart_missing_heatmap(df: pd.DataFrame):
    sample = df.sample(min(100, len(df)), random_state=42) if len(df) > 100 else df
    fig = px.imshow(
        sample.isnull().astype(int),
        title="Missing Values Map",
        color_continuous_scale=["rgba(0,0,0,0)", "#ef4444"],
        aspect="auto",
    )
    fig.update_layout(**chart_layout())
    return fig


def _chart_dup_donut(total: int, dup_count: int):
    fig = px.pie(
        values=[max(total - dup_count, 0), dup_count],
        names=["Unique", "Duplicate"],
        title=f"Row Uniqueness — {dup_count:,} duplicates of {total:,}",
        color_discrete_sequence=["#6163df", "#ef4444"],
        hole=0.48,
    )
    fig.update_layout(**chart_layout())
    return fig


# ── Standard runner entry point (used by services/analysis_service.py) ──

def run_data_quality(df: pd.DataFrame, **kwargs) -> list:
    """Return summary charts for missing values and duplicate rows.

    Pure: no rendering, no cleaning actions -- this only returns
    ``(title, fig)`` summary charts. Interactive cleaning (dropping NA,
    deleting duplicates/rows) is handled by the standalone functions
    above, called directly from the Qt data-cleaning page.
    """
    charts = []

    miss_total = int(df.isnull().sum().sum())
    if miss_total > 0:
        mc = missing_summary(df)
        charts.append(("Missing % by Column", _chart_missing_bar(mc)))
        charts.append(("Missing Values Map", _chart_missing_heatmap(df)))

    n_dups = duplicate_count(df, None)
    charts.append(("Duplicate Rows Summary", _chart_dup_donut(len(df), n_dups)))

    return charts
