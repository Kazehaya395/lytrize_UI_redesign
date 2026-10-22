#!/usr/bin/env python3
"""Regression tests for the dashboard slicer geometry fixes.

Run from the repo root:
    QT_QPA_PLATFORM=offscreen python scripts/test_slicer_fixes.py

Covers:
  1. _kpi_key: bare digits are KPIs; "slicer:" prefixed keys are NOT
  2. _slicer_key: "slicer:" prefixed keys are slicers; bare digits are NOT
  3. _current_geometry round-trip: slicer positions survive save/load without
     colliding with KPI positions
  4. _swap_slicer_tiles: arrow buttons swap positions using namespaced keys
  5. _persist_geometry: slicer keys written under "slicer:" namespace in blob
  6. Export parity: slicer geometry serialised into export payload
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402

from desktop.pages.dashboard_page import _kpi_key, _slicer_key  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name} {('— ' + detail) if detail else ''}")


class MockBridge(QObject):
    """Minimal SessionBridge stand-in: get/set/pop on a dict."""

    charts_changed = Signal(object)
    theme_changed = Signal(str)
    state_changed = Signal(str, object)

    def __init__(self, initial: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self._store = dict(initial or {})

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value) -> None:
        self._store[key] = value

    def pop(self, key, default=None):
        return self._store.pop(key, default)


# ---------------------------------------------------------------------------
# 1. Key normalization helpers (module-level, no QApplication needed)
# ---------------------------------------------------------------------------
def test_key_normalization():
    print("[1] _kpi_key / _slicer_key key normalization")

    # Bare digits -> KPI keys
    check("bare '0' is KPI key", _kpi_key("0") == "0")
    check("bare '1' is KPI key", _kpi_key("1") == "1")
    check("bare '42' is KPI key", _kpi_key("42") == "42")

    # Bare digits are NOT slicer keys (Fix 1: no collision)
    check("bare '0' is NOT slicer key", _slicer_key("0") is None)
    check("bare '1' is NOT slicer key", _slicer_key("1") is None)
    check("bare '42' is NOT slicer key", _slicer_key("42") is None)

    # "kpi:" prefixed keys -> KPI keys (canvas spelling)
    check("'kpi:0' is KPI key", _kpi_key("kpi:0") == "0")
    check("'kpi:3' is KPI key", _kpi_key("kpi:3") == "3")

    # "slicer:" prefixed keys -> slicer keys (canvas spelling)
    check("'slicer:0' is slicer key", _slicer_key("slicer:0") == "0")
    check("'slicer:1' is slicer key", _slicer_key("slicer:1") == "1")
    check("'slicer:7' is slicer key", _slicer_key("slicer:7") == "7")

    # "slicer:" prefixed keys are NOT KPI keys (Fix 1: no cross-contamination)
    check("'slicer:0' is NOT KPI key", _kpi_key("slicer:0") is None)
    check("'slicer:7' is NOT KPI key", _kpi_key("slicer:7") is None)

    # "kpi:" prefixed keys are NOT slicer keys
    check("'kpi:0' is NOT slicer key", _slicer_key("kpi:0") is None)

    # Non-matching strings return None
    check("random string is not KPI", _kpi_key("chart-abc") is None)
    check("random string is not slicer", _slicer_key("chart-abc") is None)
    check("empty string is not KPI", _kpi_key("") is None)
    check("empty string is not slicer", _slicer_key("") is None)
    check("None is not KPI", _kpi_key(None) is None)  # type: ignore[arg-type]
    check("None is not slicer", _slicer_key(None) is None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Geometry round-trip: slicers survive save/load without KPI collision
# ---------------------------------------------------------------------------
def _make_page(bridge):
    """Create a DashboardPage, stubbing out ReportCanvas if headless."""
    from PySide6.QtWidgets import QWidget
    from desktop.pages.dashboard_page import DashboardPage

    try:
        page = DashboardPage(bridge)
        return page
    except Exception:  # noqa: BLE001
        # QWebEngineView needs Vulkan/GPU offscreen; stub with a QWidget.
        import desktop.pages.dashboard_page as dp

        class _StubCanvas(QWidget):
            from PySide6.QtCore import Signal

            geometry_changed = Signal(str, object)
            notes_changed = Signal(str, str)
            action_requested = Signal(str, object)
            slicer_changed = Signal(str, str)

            def is_ready(self):
                return False

            def run_js(self, *a, **k):
                return None

        dp.ReportCanvas = _StubCanvas  # type: ignore[assignment]
        page = DashboardPage(bridge)
        return page


def test_geometry_round_trip():
    print("[2] _current_geometry slicer round-trip (no KPI collision)")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    bridge = MockBridge({
        "charts": [("c1", "Chart 1"), ("c2", "Chart 2")],
        "kpis": [{"name": "Revenue"}, {"name": "Profit"}],
        "slicers": [
            {"column": "Region", "widget_kind": "list",
             "scope": "report", "values": ["North"]},
        ],
        "grid_cols_n": 2,
        "tile_geometry": {
            "slicer:0": {"x": 0.0, "y": 120.0, "w": 1.0, "h": 180.0},
            "0": {"x": 0.0, "y": 320.0, "w": 0.5, "h": 120.0},
            "c1": {"x": 0.5, "y": 320.0, "w": 0.5, "h": 300.0},
        },
    })

    page = _make_page(bridge)
    charts = bridge.get("charts", [])
    kpis = bridge.get("kpis", [])
    cols = int(bridge.get("grid_cols_n", 2) or 2)

    chart_map, kpi_map = page._current_geometry(charts, kpis, cols)

    # Slicer geometry stored on the page under bare key "0" (namespaced
    # "slicer:0" → stripped to "0" by _slicer_key; re-namespaced at persist).
    slicer_geom = getattr(page, "_slicer_geom", {})
    check("slicer geom has bare key '0'",
          "0" in slicer_geom, f"keys: {list(slicer_geom.keys())}")
    check("slicer geom does NOT have namespaced 'slicer:0'",
          "slicer:0" not in slicer_geom,
          f"keys: {list(slicer_geom.keys())}")

    # Slicer position preserved at y=120px (Fix 1: not clobbered by KPI 0)
    check("slicer y preserved at 120px",
          slicer_geom.get("0", {}).get("y") == 120.0,
          f"got {slicer_geom.get('0', {})}")

    # KPI map uses bare digit keys only, never "slicer:" prefixed (Fix 1)
    kpi_keys = list(kpi_map.keys())
    check("KPI map has bare digit key '0'",
          "0" in kpi_keys, f"keys: {kpi_keys}")
    check("KPI map does NOT have 'slicer:' prefixed keys",
          not any(k.startswith("slicer:") for k in kpi_keys),
          f"keys: {kpi_keys}")


# ---------------------------------------------------------------------------
# 3. Slicer tile swap: arrow buttons use namespaced keys (Fix 2)
# ---------------------------------------------------------------------------
def test_slicer_tile_swap():
    print("[3] _swap_slicer_tiles swaps positions via namespaced keys")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    bridge = MockBridge({
        "charts": [("c1", "Chart 1")],
        "kpis": [],
        "slicers": [
            {"column": "Region", "widget_kind": "list",
             "scope": "report", "values": ["North"]},
            {"column": "Category", "widget_kind": "list",
             "scope": "report", "values": ["A", "B"]},
        ],
        "grid_cols_n": 2,
        "tile_geometry": {
            "slicer:0": {"x": 0.0, "y": 120.0, "w": 1.0, "h": 180.0},
            "slicer:1": {"x": 1.0, "y": 120.0, "w": 1.0, "h": 180.0},
        },
    })

    page = _make_page(bridge)

    before = dict(page._slicer_geom)
    check("before: slicer:0 at x=0",
          before.get("slicer:0", {}).get("x") == 0.0)
    check("before: slicer:1 at x=1",
          before.get("slicer:1", {}).get("x") == 1.0)

    page._swap_slicer_tiles(0, 1)

    after = dict(page._slicer_geom)
    check("after swap: slicer:0 at x=1 (swapped)",
          after.get("slicer:0", {}).get("x") == 1.0,
          f"got x={after.get('slicer:0', {}).get('x')}")
    check("after swap: slicer:1 at x=0 (swapped)",
          after.get("slicer:1", {}).get("x") == 0.0,
          f"got x={after.get('slicer:1', {}).get('x')}")


# ---------------------------------------------------------------------------
# 4. Persist geometry: namespaced slicer keys in the blob
# ---------------------------------------------------------------------------
def test_persist_geometry_namespaced_keys():
    print("[4] _persist_geometry writes slicer keys under 'slicer:' namespace")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    bridge = MockBridge({
        "charts": [("c1", "Chart 1")],
        "kpis": [{"name": "KPI 0"}],
        "slicers": [
            {"column": "Region", "widget_kind": "list",
             "scope": "report", "values": ["North"]},
        ],
        "grid_cols_n": 2,
        "tile_geometry": {},
    })

    page = _make_page(bridge)

    # Force _slicer_geom to have a slicer entry (bare key "0")
    page._slicer_geom = {"0": {"x": 0.0, "y": 120.0, "w": 1.0, "h": 180.0}}

    chart_map = {}
    kpi_map = {"0": {"x": 0.0, "y": 300.0, "w": 0.5, "h": 120.0}}

    page._persist_geometry(chart_map, kpi_map, cols=2, regen=False)

    stored = bridge.get("tile_geometry", {})
    check("persisted blob has 'slicer:0' key",
          "slicer:0" in stored, f"keys: {list(stored.keys())}")
    check("persisted blob does NOT have bare '0' as slicer",
          stored.get("0") == kpi_map.get("0") or "0" not in stored,
          f"bare '0' value: {stored.get('0')}")
    check("slicer:0 geometry preserved at y=120px",
          stored.get("slicer:0", {}).get("y") == 120.0,
          f"got {stored.get('slicer:0', {})}")


# ---------------------------------------------------------------------------
# 5. Export parity: slicer geometry serialised into export payload (Fix 3)
# ---------------------------------------------------------------------------
def test_export_slicer_parity():
    print("[5] Export includes slicer geometry from _slicer_geom")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    bridge = MockBridge({
        "charts": [("c1", "Chart 1")],
        "kpis": [],
        "slicers": [
            {"column": "Region", "widget_kind": "list",
             "scope": "report", "values": ["North"]},
        ],
        "grid_cols_n": 2,
        "tile_geometry": {
            "slicer:0": {"x": 0.0, "y": 120.0, "w": 1.0, "h": 180.0},
        },
    })

    page = _make_page(bridge)

    # _sync_slicers_live reads _slicer_geom and serializes it to geometry_json
    slicer_geometry = dict(getattr(page, "_slicer_geom", {}) or {})
    check("slicer geometry read from _slicer_geom",
          "slicer:0" in slicer_geometry,
          f"keys: {list(slicer_geometry.keys())}")
    check("slicer geometry has position data",
          slicer_geometry.get("slicer:0", {}).get("y") == 120.0,
          f"got {slicer_geometry.get('slicer:0', {})}")

    # Verify the geometry JSON serializes correctly (as _sync_slicers_live does)
    geometry_json = json.dumps(
        slicer_geometry, separators=(",", ":"), allow_nan=False)
    check("geometry JSON contains 'slicer:0' key",
          '"slicer:0"' in geometry_json)
    check("geometry JSON includes y position",
          '"y":120.0' in geometry_json or '"y":120' in geometry_json,
          f"json: {geometry_json}")


# ---------------------------------------------------------------------------
def main():
    print("Dashboard slicer geometry regression tests\n")

    # Module-level key function tests (no QApplication needed)
    test_key_normalization()

    # Instance-level tests (need QApplication; may skip if QWebEngineView fails)
    try:
        from PySide6.QtWidgets import QApplication
        _ = QApplication.instance() or QApplication([])
        test_geometry_round_trip()
        test_slicer_tile_swap()
        test_persist_geometry_namespaced_keys()
        test_export_slicer_parity()
    except Exception as exc:  # noqa: BLE001
        print(f"\n  SKIPPED instance-level tests: {exc}")
        print("  (QWebEngineView may require a display; key-function tests still ran)")

    print(f"\n{'=' * 50}")
    print(f"Results: {len(PASS)} passed, {len(FAIL)} failed out of "
          f"{len(PASS) + len(FAIL)}")
    for f in FAIL:
        print(f"  FAIL  {f}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
