"""Verify categorical legend theming end to end (backend model + adapter).

Run with the project venv:
    venv/bin/python scripts/_verify_categorical_legend.py

Backend checks assert what the builders and the live-edit path put into
``display.legend`` (visibility plus the plumbed Typography colours). Adapter
checks execute the real ``backend/assets/echarts/lytrize_adapter.js`` inside
Qt's JS engine and assert the emitted ECharts option: one legend row per
category with a swatch equal to that category's bar colour for single-series
bars, one colour per series for statistical bars, dual-axis overlay rows, and
theme-aware legend ink following theme_figure's dark-default remap.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

import polars as pl  # noqa: E402

from services.polars_engine import PolarsDataset  # noqa: E402
from services.polars_chart_service import (  # noqa: E402
    apply_chart_model_display_options,
    build_categorical_charts,
    build_correlation_chart,
    build_distribution_charts,
    build_statistical_charts,
    build_time_series_chart,
)

ADAPTER_PATH = os.path.join(ROOT, "backend", "assets", "echarts",
                            "lytrize_adapter.js")
TYPO = {"header_size": 20, "family": "Inter",
        "legend_item_size": 11, "legend_title_size": 12,
        # The stored DARK defaults the Typography tab ships with: they must
        # remap to readable ink on a light card and survive on a dark one.
        "legend_item_color": "#e2e8f0", "legend_title_color": "#e2e8f0",
        "axis_tick_color": "#94a3b8"}

PASSED = []


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    PASSED.append(label)


def make_dataset():
    frame = pl.DataFrame({
        "region": ["A", "A", "B", "B", "C", "C"],
        "value": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "extra": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "date": ["2024-01-01", "2024-01-02", "2024-01-03",
                 "2024-01-04", "2024-01-05", "2024-01-06"],
    }).with_row_index("__row_id")
    return PolarsDataset(frame.lazy(), "categorical-legend-harness", "rev0", "__row_id")


# --------------------------------------------------------------------------
# Backend: builders and live-edit paths
# --------------------------------------------------------------------------

def backend_checks(ds):
    single = build_categorical_charts(
        ds, {"x_cols": ["region"], "y_cols": ["value"], "agg": "sum"},
        typography=TYPO)
    check("categorical build returns one model", len(single) == 1, len(single))
    legend = single[0]["display"]["legend"]
    check("single-series categorical legend shows", legend["show"] is True, legend)
    check("legend item colour plumbed", legend.get("item_color") == "#e2e8f0", legend)
    check("legend title colour plumbed", legend.get("title_color") == "#e2e8f0", legend)
    check("legend title size plumbed", legend.get("title_font_size") == 12, legend)
    # Give the single model a legend title so the adapter emits the graphic
    # block whose fill is theme-remapped; exercised under both themes below.
    single[0]["display"]["legend"]["title"] = "Regions"

    dual = build_categorical_charts(
        ds, {"x_cols": ["region"], "y_cols": ["value"], "agg": "sum",
             "dual_y": "extra", "dual_y_agg": "mean"},
        typography=TYPO)
    check("dual-axis build returns one model", len(dual) == 1, len(dual))
    check("dual-axis legend shows", dual[0]["display"]["legend"]["show"] is True)
    check("dual-axis flagged", dual[0]["display"].get("dual_axis") is True)

    # Live edit must keep the categorical legend visible and refresh the
    # legend geometry from the layout.
    edited = apply_chart_model_display_options(
        single[0], {"legend_position": "bottom left"}, TYPO)
    edited_legend = edited["display"]["legend"]
    check("categorical live edit keeps legend shown",
          edited_legend["show"] is True, edited_legend)
    check("categorical live edit moves legend",
          edited_legend.get("position") == "bottom left", edited_legend)

    statistical = build_statistical_charts(
        ds, {"x_cols": ["region"], "y_cols": ["value"]}, typography=TYPO)
    check("statistical build returns a model", len(statistical) == 1, len(statistical))
    check("statistical legend shows", statistical[0]["display"]["legend"]["show"] is True)
    stat_edited = apply_chart_model_display_options(statistical[0], {}, TYPO)
    # Regression: the legend used to vanish the moment the chart was edited.
    check("statistical live edit keeps legend shown",
          stat_edited["display"]["legend"]["show"] is True,
          stat_edited["display"]["legend"])

    time_series = build_time_series_chart(
        ds, {"x": "date", "y": "value"}, typography=TYPO)
    check("time series builds", time_series is not None)
    check("time series legend hidden", time_series["display"]["legend"]["show"] is False)
    ts_edited = apply_chart_model_display_options(time_series, {}, TYPO)
    check("time series live edit keeps legend hidden",
          ts_edited["display"]["legend"]["show"] is False,
          ts_edited["display"]["legend"])

    plain_hist = build_distribution_charts(
        ds, {"x_cols": ["value"]}, typography=TYPO)
    check("plain histogram legend hidden",
          plain_hist[0]["display"]["legend"]["show"] is False)
    grouped_hist = build_distribution_charts(
        ds, {"x_cols": ["value"], "color": "region"}, typography=TYPO)
    check("grouped histogram legend shows",
          grouped_hist[0]["display"]["legend"]["show"] is True)

    correlation = build_correlation_chart(
        ds, {"x_cols": ["value", "extra"]}, typography=TYPO)
    check("correlation builds", correlation is not None)
    check("correlation legend hidden",
          correlation["display"]["legend"]["show"] is False)

    return single[0], dual[0], statistical[0], time_series


# --------------------------------------------------------------------------
# Adapter: run the real lytrize_adapter.js in QJSEngine
# --------------------------------------------------------------------------

def make_engine():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine
    app = QCoreApplication.instance() or QCoreApplication([])
    engine = QJSEngine()
    engine.evaluate("var window = {};")
    with open(ADAPTER_PATH, encoding="utf-8") as handle:
        result = engine.evaluate(handle.read())
    if result.isError():
        raise RuntimeError(f"adapter failed to load: {result.toString()}")
    engine.evaluate("var __root = {clientWidth: 800, clientHeight: 520};")
    return engine


def option_for(engine, model, theme_mode):
    payload = json.loads(json.dumps(model))
    payload.setdefault("display", {})["theme_mode"] = theme_mode
    engine.evaluate("var __model = " + json.dumps(payload) + ";")
    value = engine.evaluate(
        "JSON.stringify(window.LytrizeEChartsAdapter.buildOption("
        "__model, 800, 520, __root))")
    if value.isError():
        raise RuntimeError(f"buildOption threw: {value.toString()}")
    return json.loads(value.toString())


def datum_colors(series_entry):
    return [(item or {}).get("itemStyle", {}).get("color")
            for item in series_entry.get("data", [])]


def adapter_checks(engine, single, dual, statistical, time_series):
    # --- single-series categorical: one legend row per category, each swatch
    # equal to that category's bar colour.
    light = option_for(engine, single, "light")
    palette = single["display"]["palette"]
    categories = [pair[0] for pair in single["data"]["series"][0]["values"]]
    rows = light["legend"]["data"]
    check("single-series legend has one row per category",
          [row["name"] for row in rows] == categories, rows)
    bars = datum_colors(light["series"][0])
    check("single-series bars keep per-category colours",
          bars == palette[:len(categories)], (bars, palette))
    check("legend swatches equal the bar colours",
          [row["itemStyle"]["color"] for row in rows] == bars, rows)
    # Dark-default legend ink remaps for light cards, keeps the stored grey on
    # dark cards, and a user-picked colour passes through untouched.
    check("light-mode legend text remapped",
          light["legend"]["textStyle"]["color"] == "#334155",
          light["legend"]["textStyle"])
    check("light-mode legend title remapped",
          light["graphic"][0]["style"]["fill"] == "#334155", light["graphic"])
    dark = option_for(engine, single, "dark")
    check("dark-mode legend text kept",
          dark["legend"]["textStyle"]["color"] == "#e2e8f0",
          dark["legend"]["textStyle"])
    picked = json.loads(json.dumps(single))
    picked["recipe"]["typography"]["legend_item_color"] = "#ff6600"
    picked_light = option_for(engine, picked, "light")
    check("user-picked legend colour passes through",
          picked_light["legend"]["textStyle"]["color"] == "#ff6600",
          picked_light["legend"]["textStyle"])
    hidden = json.loads(json.dumps(single))
    hidden["display"]["legend"]["show"] = False
    check("hidden legend stays hidden in the option",
          option_for(engine, hidden, "light").get("legend", {}).get("show") is False)

    # --- statistical bars: one row per statistic, one colour per series.
    stat_opt = option_for(engine, statistical, "light")
    names = [series["name"] for series in statistical["data"]["series"]]
    check("statistical legend lists the series",
          stat_opt["legend"]["data"] == names, stat_opt["legend"]["data"])
    for index, series_entry in enumerate(stat_opt["series"]):
        colors = set(datum_colors(series_entry))
        check(f"statistical series {index} uses one colour",
              colors == {palette[index % len(palette)]}, colors)

    # --- dual-axis: category rows plus the overlay line's own row.
    dual_opt = option_for(engine, dual, "light")
    dual_categories = [pair[0] for pair in dual["data"]["series"][0]["values"]]
    dual_rows = dual_opt["legend"]["data"]
    line_name = dual["data"]["series"][1]["name"]
    check("dual-axis legend rows = categories + overlay line",
          [row["name"] for row in dual_rows] == dual_categories + [line_name],
          dual_rows)
    dual_bars = datum_colors(dual_opt["series"][0])
    check("dual-axis swatches equal the bar colours",
          [row["itemStyle"]["color"] for row in dual_rows[:len(dual_categories)]] == dual_bars)
    check("dual-axis line row carries the overlay colour",
          dual_rows[-1]["itemStyle"]["color"] == dual["data"]["series"][1]["style"]["color"],
          dual_rows[-1])

    # --- families without a legend render no legend.
    ts_opt = option_for(engine, time_series, "light")
    check("time-series option keeps the legend off",
          ts_opt.get("legend", {}).get("show") is False, ts_opt.get("legend"))


def main():
    ds = make_dataset()
    single, dual, statistical, time_series = backend_checks(ds)
    engine = make_engine()
    adapter_checks(engine, single, dual, statistical, time_series)
    for label in PASSED:
        print(f"PASS  {label}")
    print(f"\nAll {len(PASSED)} checks passed.")


if __name__ == "__main__":
    main()

