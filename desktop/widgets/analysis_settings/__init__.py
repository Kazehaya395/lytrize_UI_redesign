"""Per-analysis-type settings builders registry."""
from __future__ import annotations
from typing import Dict, Optional, Type
from PySide6.QtWidgets import QWidget
from .base import AnalysisSettingsBase
from .statistical import StatisticalSettings
_REGISTRY: Dict[str, Type[AnalysisSettingsBase]] = {}
_REGISTRY['statistical'] = StatisticalSettings
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
