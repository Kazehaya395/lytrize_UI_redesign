"""Versioned, renderer-neutral dashboard document contract.

The current session stores charts, KPI cards, slicers, canvas styling and
geometry in separate session keys. This module defines the JSON-only shape
that those pieces can migrate into. It deliberately knows nothing about
Plotly, ECharts, pandas, Polars or Qt: a renderer may consume a validated
document without becoming the source of its meaning.

This is a contract and normalization boundary, not a session migration. Old
session payloads remain readable through their existing code until an
explicit, loss-aware converter is introduced.
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, Optional

BOARD_VERSION = 1
OBJECT_KINDS = frozenset({"chart", "kpi", "slicer", "text", "image"})
_GEOMETRY_KEYS = ("x", "y", "w", "h")


class BoardModelError(ValueError):
    """Raised when a board document is malformed or not JSON-safe."""


def _json_value(value: Any, path: str = "value") -> Any:
    """Return a detached JSON-safe value, rejecting implicit object encoding."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BoardModelError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item, f"{path}[{index}]")
                for index, item in enumerate(value)]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BoardModelError(f"{path} has a non-string key")
            result[key] = _json_value(item, f"{path}.{key}")
        return result
    raise BoardModelError(
        f"{path} contains unsupported value {type(value).__name__}; "
        "convert it to a JSON primitive first"
    )


def _geometry(value: Any, path: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise BoardModelError(f"{path} must be an object")
    geometry = {}
    for key in _GEOMETRY_KEYS:
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise BoardModelError(f"{path}.{key} must be numeric")
        number = float(raw)
        if not math.isfinite(number):
            raise BoardModelError(f"{path}.{key} must be finite")
        if key in ("w", "h") and number <= 0:
            raise BoardModelError(f"{path}.{key} must be greater than zero")
        geometry[key] = number
    return geometry


def make_board_object(object_id: str, kind: str, geometry: dict,
                      properties: Optional[dict] = None) -> dict:
    """Build one canonical board object.

    ``geometry`` is expressed in logical board units rather than pixels, so a
    saved dashboard keeps its layout when the window or display scale changes.
    ``properties`` is object-specific JSON data: e.g. a chart recipe, a KPI
    calculation, a slicer binding, text styling or an image asset reference.
    """
    if not isinstance(object_id, str) or not object_id.strip():
        raise BoardModelError("object id must be a non-empty string")
    if kind not in OBJECT_KINDS:
        raise BoardModelError(f"unsupported board object kind: {kind!r}")
    if properties is not None and not isinstance(properties, dict):
        raise BoardModelError("object properties must be an object")
    return {
        "id": object_id,
        "kind": kind,
        "geometry": _geometry(geometry, f"objects[{object_id}].geometry"),
        "properties": _json_value(properties or {},
                                   f"objects[{object_id}].properties"),
    }


def make_board(objects: Iterable[dict], *, style: Optional[dict] = None,
               background: Optional[dict] = None,
               metadata: Optional[dict] = None) -> dict:
    """Create and validate a complete renderer-neutral dashboard document."""
    normalized = []
    seen_ids = set()
    for index, item in enumerate(objects):
        if not isinstance(item, dict):
            raise BoardModelError(f"objects[{index}] must be an object")
        obj = make_board_object(
            item.get("id"), item.get("kind"), item.get("geometry"),
            item.get("properties"),
        )
        if obj["id"] in seen_ids:
            raise BoardModelError(f"duplicate board object id: {obj['id']!r}")
        seen_ids.add(obj["id"])
        normalized.append(obj)

    for name, value in (("style", style), ("background", background),
                        ("metadata", metadata)):
        if value is not None and not isinstance(value, dict):
            raise BoardModelError(f"board {name} must be an object")

    return {
        "version": BOARD_VERSION,
        "objects": normalized,
        "style": _json_value(style or {}, "style"),
        "background": _json_value(background or {}, "background"),
        "metadata": _json_value(metadata or {}, "metadata"),
    }


def validate_board(document: Any) -> dict:
    """Validate an untrusted JSON document and return a normalized copy."""
    if not isinstance(document, dict):
        raise BoardModelError("board document must be an object")
    version = document.get("version")
    if version != BOARD_VERSION:
        raise BoardModelError(
            f"unsupported board version {version!r}; expected {BOARD_VERSION}"
        )
    objects = document.get("objects")
    if not isinstance(objects, list):
        raise BoardModelError("board objects must be a list")
    return make_board(
        objects,
        style=document.get("style"),
        background=document.get("background"),
        metadata=document.get("metadata"),
    )


def board_to_json(document: dict) -> str:
    """Serialize a validated board deterministically for backups/sharing."""
    normalized = validate_board(document)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, allow_nan=False)


def board_from_json(payload: str) -> dict:
    """Parse and validate a serialized board document."""
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise BoardModelError(f"invalid board JSON: {exc}") from exc
    return validate_board(decoded)
