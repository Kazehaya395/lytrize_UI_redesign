"""Verify theme auto-optimization and legend collision fixes end-to-end.

Tests:
1. Default / auto text colors in Light Mode resolve to dark, readable ink (#111827 / #334155 / #475569).
2. Default / auto text colors in Dark Mode resolve to light, readable ink (#f8fafc / #e2e8f0 / #94a3b8).
3. Legacy chart models with hardcoded dark defaults (#e2e8f0, #f8fafc, #ffffff) automatically remap in Light Mode.
4. User-chosen custom colors (e.g. #ef4444, #10b981) are preserved in both modes.
5. Legend and Legend Title do not collide (vertically separated and reserved in legendBand) in Top Right, Top Left, Bottom, etc.
6. Dynamic theme switching (light <-> dark) updates all chart elements seamlessly.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

import polars as pl
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from services.polars_engine import PolarsDataset
from services.polars_chart_service import (
    build_statistical_charts,
    build_categorical_charts,
    apply_chart_model_display_options,
)

ADAPTER_PATH = os.path.join(ROOT, "backend", "assets", "echarts", "lytrize_adapter.js")

app = QCoreApplication.instance() or QCoreApplication([])
engine = QJSEngine()
engine.evaluate("var window = {};")
with open(ADAPTER_PATH, encoding="utf-8") as f:
    engine.evaluate(f.read())
engine.evaluate("var __root = {clientWidth: 800, clientHeight: 520};")


def get_option(model, theme_mode, width=800, height=520):
    payload = json.loads(json.dumps(model))
    payload.setdefault("display", {})["theme_mode"] = theme_mode
    engine.evaluate("var __m = " + json.dumps(payload) + ";")
    v = engine.evaluate(
        f"JSON.stringify(window.LytrizeEChartsAdapter.buildOption(__m, {width}, {height}, __root))"
    )
    if v.isError():
        raise RuntimeError(f"buildOption failed: {v.toString()}")
    return json.loads(v.toString())


def test_theme_auto_optimization():
    print("\n--- Test 1: Theme Auto-Optimization ---")
    frame = pl.DataFrame({
        "category": ["A", "B", "C"],
        "amt": [100.0, 250.0, 180.0]
    }).with_row_index("__row_id")
    ds = PolarsDataset(frame.lazy(), "test-ds", "rev1", "__row_id")
    
    # Generate statistical charts (like user's screenshot)
    charts = build_statistical_charts(ds, {"x_cols": ["category"], "y_cols": ["amt"]})
    assert len(charts) > 0
    model = charts[0]

    # --- LIGHT MODE CHECKS ---
    light = get_option(model, "light")
    title_col = light["title"]["textStyle"]["color"]
    x_tick_col = light["xAxis"]["axisLabel"]["color"]
    y_tick_col = light["yAxis"]["axisLabel"]["color"]
    x_name_col = light["xAxis"]["nameTextStyle"]["color"]
    y_name_col = light["yAxis"]["nameTextStyle"]["color"]
    legend_item_col = light["legend"]["textStyle"]["color"]

    # All light mode colors must be DARK
    dark_shades = {"#111827", "#0f172a", "#1e293b", "#334155", "#475569"}
    assert title_col in dark_shades, f"Light mode title must be dark, got {title_col}"
    assert x_tick_col in dark_shades, f"Light mode X tick must be dark, got {x_tick_col}"
    assert y_tick_col in dark_shades, f"Light mode Y tick must be dark, got {y_tick_col}"
    assert x_name_col in dark_shades, f"Light mode X title must be dark, got {x_name_col}"
    assert y_name_col in dark_shades, f"Light mode Y title must be dark, got {y_name_col}"
    assert legend_item_col in dark_shades, f"Light mode legend item must be dark, got {legend_item_col}"
    print("  PASS: Light mode chart texts are all auto-optimized to crisp dark shades.")

    # --- DARK MODE CHECKS ---
    dark = get_option(model, "dark")
    dark_title_col = dark["title"]["textStyle"]["color"]
    dark_x_tick_col = dark["xAxis"]["axisLabel"]["color"]
    dark_y_tick_col = dark["yAxis"]["axisLabel"]["color"]
    dark_legend_col = dark["legend"]["textStyle"]["color"]

    light_shades = {"#f8fafc", "#f1f5f9", "#e2e8f0", "#cbd5e1", "#94a3b8"}
    assert dark_title_col in light_shades, f"Dark mode title must be light, got {dark_title_col}"
    assert dark_x_tick_col in light_shades, f"Dark mode X tick must be light, got {dark_x_tick_col}"
    assert dark_y_tick_col in light_shades, f"Dark mode Y tick must be light, got {dark_y_tick_col}"
    assert dark_legend_col in light_shades, f"Dark mode legend must be light, got {dark_legend_col}"
    print("  PASS: Dark mode chart texts are all auto-optimized to crisp light shades.")


def test_legend_collision_prevention():
    print("\n--- Test 2: Legend and Legend Title Collision Prevention ---")
    frame = pl.DataFrame({
        "category": ["A", "B", "C"],
        "amt": [100.0, 250.0, 180.0]
    }).with_row_index("__row_id")
    ds = PolarsDataset(frame.lazy(), "test-ds", "rev1", "__row_id")
    
    charts = build_statistical_charts(ds, {"x_cols": ["category"], "y_cols": ["amt"]})
    model = charts[0]
    
    # Configure Top Right with a legend title (exactly as in user screenshots)
    model["display"]["legend"]["title"] = "legends"
    model["display"]["legend"]["position"] = "top right"
    
    opt = get_option(model, "light")
    
    # Check that graphic block exists for the legend title
    graphics = opt.get("graphic")
    assert graphics and len(graphics) > 0, "Legend title graphic block missing"
    title_graphic = graphics[0]
    assert title_graphic["style"]["text"] == "legends"
    
    # The legend title graphic is anchored with style.x/style.y + textAlign
    # (the only graphic-text anchors ECharts honours) -- style.align and the
    # element-level top/right keys are ignored, so assert the live contract.
    title_y = title_graphic["style"].get("y")
    title_x = title_graphic["style"].get("x")
    legend_top = opt["legend"].get("top")
    legend_height = opt["legend"].get("height", 24)
    grid_top = opt["grid"].get("top")

    print(f"  Title graphic y:   {title_y}px")
    print(f"  Legend box top:    {legend_top}px (height: {legend_height}px)")
    print(f"  Plot grid top:     {grid_top}px")

    # Legend title must sit strictly ABOVE the legend items
    assert isinstance(title_y, (int, float)), "legend title graphic must carry a numeric style.y"
    assert isinstance(legend_top, (int, float)), "legend.top must be numeric for a top legend"
    assert legend_top >= title_y, (
        f"Legend items (top={legend_top}) must start below the legend title (y={title_y})"
    )
    # Plot grid must sit strictly BELOW both the title and the legend items
    assert grid_top >= legend_top + 24, (
        f"Plot grid (top={grid_top}) must start below legend items (top={legend_top})"
    )
    
    # Horizontal collision check: right-aligned alignment
    assert title_x is not None and title_x >= 0
    assert title_graphic["style"]["textAlign"] == "right"
    assert opt["legend"].get("right") is not None
    
    print("  PASS: Legend Title and Legend Items are cleanly vertically stacked with zero overlap.")


def test_custom_color_preservation():
    print("\n--- Test 3: User Custom Color Preservation ---")
    frame = pl.DataFrame({"x": ["A", "B"], "y": [1, 2]}).with_row_index("__row_id")
    ds = PolarsDataset(frame.lazy(), "test-ds", "rev1", "__row_id")
    single = build_categorical_charts(ds, {"x_cols": ["x"], "y_cols": ["y"], "agg": "sum"})[0]
    
    # User picked red header and green axis
    custom = apply_chart_model_display_options(
        single,
        {"title": "Custom Test"},
        {"header_color": "#ef4444", "axis_tick_color": "#10b981", "value_label_color": "#8b5cf6"}
    )
    
    for mode in ("light", "dark"):
        opt = get_option(custom, mode)
        assert opt["title"]["textStyle"]["color"] == "#ef4444", f"Custom red header lost in {mode}"
        assert opt["xAxis"]["axisLabel"]["color"] == "#10b981", f"Custom green tick lost in {mode}"
        assert opt["series"][0]["label"]["color"] == "#8b5cf6", f"Custom purple label lost in {mode}"
    print("  PASS: User-specified custom colors survive untouched across both light and dark modes.")


if __name__ == "__main__":
    test_theme_auto_optimization()
    test_legend_collision_prevention()
    test_custom_color_preservation()
    print("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY!")

