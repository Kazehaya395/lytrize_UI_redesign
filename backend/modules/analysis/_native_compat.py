"""Compatibility entry point for historical analysis runner imports."""

from __future__ import annotations


def run_native(analysis_id: str, df, **kwargs):
    """Keep the historical ``[(title, chart)]`` API on native ChartModels."""
    from services.analysis_service import run_analysis

    roles = kwargs.pop("roles", None)
    layout = kwargs.pop("layout", None)
    typography = kwargs.pop("typography", None)
    results = run_analysis(analysis_id, df, roles=roles, layout=layout,
                           typography=typography, **kwargs)
    return [(title, model) for _uid, title, model in results]
