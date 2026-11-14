"""Per-analysis-type settings builders registry."""
from __future__ import annotations
from typing import Dict, Optional, Type
from PySide6.QtWidgets import QWidget
from .base import AnalysisSettingsBase
from .statistical import StatisticalSettings
from .distribution import DistributionSettings
from .correlation import CorrelationSettings
from .categorical import CategoricalSettings
from .pie_chart import PieChartSettings
from .time_series import TimeSeriesSettings
from .scatter_plot import ScatterPlotSettings
from .matrix_heatmap import MatrixHeatmapSettings
from .matrix_table import MatrixTableSettings
from .map_plot import MapPlotSettings
from .advanced import (SankeySettings, SunburstSettings, CalendarHeatmapSettings,
                       GlobeMapSettings, WaterfallSettings, FunnelSettings,
                       RoseSettings)

# Every analysis type the old app declared, in ``chart_settings.py`` order. The
# chain is deliberate: each builder inherits the nearest one whose tab shape it
# shares, so a shared control is defined once (see each module's docstring).
_REGISTRY: Dict[str, Type[AnalysisSettingsBase]] = {}
_REGISTRY['statistical'] = StatisticalSettings
_REGISTRY['distribution'] = DistributionSettings
_REGISTRY['correlation'] = CorrelationSettings
_REGISTRY['categorical'] = CategoricalSettings
_REGISTRY['pie_chart'] = PieChartSettings
_REGISTRY['time_series'] = TimeSeriesSettings
_REGISTRY['scatter_plot'] = ScatterPlotSettings
_REGISTRY['matrix_heatmap'] = MatrixHeatmapSettings
_REGISTRY['matrix_table'] = MatrixTableSettings
_REGISTRY['map_plot'] = MapPlotSettings
_REGISTRY['nightingale_rose'] = RoseSettings
_REGISTRY['sankey'] = SankeySettings
_REGISTRY['sunburst'] = SunburstSettings
_REGISTRY['calendar_heatmap'] = CalendarHeatmapSettings
_REGISTRY['waterfall'] = WaterfallSettings
_REGISTRY['funnel'] = FunnelSettings
def register(aid: str, cls: Type[AnalysisSettingsBase]) -> None:
    _REGISTRY[aid] = cls
def get_builder(aid: str, parent: QWidget | None = None, **ctx):
    cls = _REGISTRY.get(aid)
    if cls is None:
        return None
    try:
        b = cls(parent)
    except TypeError:
        b = cls()
        try:
            b.setParent(parent)
        except Exception:
            pass
    try:
        b.set_context(**{k: v for k, v in ctx.items() if v is not None})
    except Exception:
        pass
    return b
def registered() -> list:
    return sorted(_REGISTRY.keys())
