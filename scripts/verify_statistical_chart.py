#!/usr/bin/env python3
"""End-to-end verification for the Statistical chart (chart_designs.pdf).

Run from the repo root:
    QT_QPA_PLATFORM=offscreen python scripts/verify_statistical_chart.py

Covers:
  1. Backend : apply_display_options -- every Layout/Typography option lands
               on the figure, toggles are reversible, no compounding.
  2. Worker  : ChartGenerationWorker applies display options end to end.
  3. Panel   : settings-builder wiring, signal kinds, dirty flag.
  4. Page    : Generate appends with config/layout/typography snapshot,
               Re-Generate dirty-gate + in-place replace, live preview
               write-through, tab-select prefill, edit/new/delete flows.
  5. Reload  : second AnalysisPage re-prefills from stored chart meta.
"""
from __future__ import annotations

import os
import json
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LYTRIZE_DB_PATH", "/tmp/lytrize_verify_statistical.db")

import pandas as pd
from PySide6.QtWidgets import QApplication

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name} {('— ' + detail) if detail else ''}")


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_df() -> pd.DataFrame:
    return pd.DataFrame({
        "region": ["E", "W", "E", "W", "N", "E"],
        "sales":  [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "profit": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })


# -----------------------------------------------------------------------
# 1. Backend: apply_display_options
# -----------------------------------------------------------------------

def test_apply_display_options(df):
    print("== chart_display.apply_display_options ==")
    from chart_display import (apply_display_options, default_layout_opts,
                               default_typo_opts)
    from modules.analysis.statistical import run_statistical

    base = run_statistical(df, x_cols=["region"], y_cols=["sales"])[0][1]
    base_snapshot = base.to_json()

    # Reversible value-label toggle.
    lo = default_layout_opts()
    lo["show_value_labels"] = False
    off = apply_display_options(base, lo, None)
    check("value labels hide -> textposition none",
          all(tr.textposition == "none" for tr in off.data))
    lo["show_value_labels"] = True
    lo["label_position"] = "outside"
    on = apply_display_options(base, lo, None)
    check("value labels show -> textposition outside",
          all(tr.textposition == "outside" for tr in on.data),
          str([tr.textposition for tr in on.data]))
    check("apply never mutates the source figure",
          base.to_json() == base_snapshot, "")

    # Titles / labels / legend.
    lo = default_layout_opts()
    lo.update(title="Custom Title", x_label="Group", y_label="Value",
              legend_title="Statistic", legend_position="Bottom Left",
              bar_mode="stack", bar_gap=0.5, show_grid=False)
    f = apply_display_options(base, lo, None)
    check("layout: title", f.layout.title.text == "Custom Title", f.layout.title.text)
    check("layout: x label", f.layout.xaxis.title.text == "Group",
          str(f.layout.xaxis.title.text))
    check("layout: y label", f.layout.yaxis.title.text == "Value",
          str(f.layout.yaxis.title.text))
    check("layout: legend title", f.layout.legend.title.text == "Statistic",
          str(f.layout.legend.title.text))
    check("layout: legend bottom-left",
          f.layout.legend.x == 0 and f.layout.legend.y == 0,
          f"x={f.layout.legend.x} y={f.layout.legend.y}")
    check("layout: barmode stack", f.layout.barmode == "stack", str(f.layout.barmode))
    check("layout: bargap 0.5", abs((f.layout.bargap or 0) - 0.5) < 1e-9,
          str(f.layout.bargap))
    check("layout: grid off",
          f.layout.xaxis.showgrid is False and f.layout.yaxis.showgrid is False,
          f"x={f.layout.xaxis.showgrid} y={f.layout.yaxis.showgrid}")

    # Typography.
    to = default_typo_opts()
    to.update(header_size=30, header_color="#ff0000", header_family="Georgia",
              header_font_style="Bold Italic",
              axis_title_size=15, axis_title_color="#00ff00",
              axis_tick_size=9, axis_tick_color="#0000ff",
              legend_item_size=13, legend_item_color="#123456",
              legend_title_size=16, legend_title_color="#654321",
              family="Lato", font_style="Bold Italic")
    f = apply_display_options(base, None, to)
    tf = f.layout.title.font
    check("typo: header size/color/family",
          tf.size == 30 and tf.color == "#ff0000" and "Georgia" in str(tf.family),
          f"size={tf.size} color={tf.color} family={tf.family}")
    check("typo: header bold-italic", tf.weight == 700 and tf.style == "italic",
          f"weight={tf.weight} style={tf.style}")
    check("typo: axis title size/color",
          f.layout.xaxis.title.font.size == 15
          and f.layout.xaxis.title.font.color == "#00ff00"
          and f.layout.yaxis.title.font.size == 15,
          str(f.layout.xaxis.title.font))
    check("typo: axis tick size/color",
          f.layout.xaxis.tickfont.size == 9
          and f.layout.xaxis.tickfont.color == "#0000ff",
          str(f.layout.xaxis.tickfont))
    check("typo: legend item size/color",
          f.layout.legend.font.size == 13 and f.layout.legend.font.color == "#123456",
          str(f.layout.legend.font))
    check("typo: legend title size/color",
          f.layout.legend.title.font.size == 16
          and f.layout.legend.title.font.color == "#654321",
          str(f.layout.legend.title.font))
    check("typo: unknown family passed through",
          str(f.layout.font.family) == "Lato, sans-serif",
          str(f.layout.font.family))
    check("typo: body bold-italic",
          f.layout.font.weight == 700 and f.layout.font.style == "italic",
          f"weight={f.layout.font.weight} style={f.layout.font.style}")

    # Re-applying the same options must not compound. Compare parsed JSON:
    # plotly may serialize identical content with different key order.
    once = apply_display_options(base, lo, to)
    twice = apply_display_options(once, lo, to)
    check("apply is idempotent (no compounding)",
          json.loads(once.to_json()) == json.loads(twice.to_json()), "")

    plain = apply_display_options(base, default_layout_opts(),
                                  default_typo_opts())
    check("default apply keeps labels visible",
          all(tr.textposition != "none" for tr in plain.data),
          str([tr.textposition for tr in plain.data]))
    nogrp = run_statistical(df, x_cols=None, y_cols=["sales", "profit"])[0][1]
    nogrp2 = apply_display_options(nogrp, default_layout_opts(), None)
    check("no-group chart applies without error", len(nogrp2.data) > 0, "")

    # -- "Show border" (Layout tab, under "Show grid") --------------------
    import plotly.graph_objects as go
    bordered = apply_display_options(base, {"show_border": True}, None)
    borderless = apply_display_options(base, {"show_border": False}, None)
    check("layout: Show border ON -> legend frame drawn",
          int(bordered.layout.legend.borderwidth or 0) == 1,
          str(bordered.layout.legend.borderwidth))
    check("layout: Show border OFF -> legend frame removed",
          int(borderless.layout.legend.borderwidth or 0) == 0,
          str(borderless.layout.legend.borderwidth))
    check("layout: Show border defaults to OFF (clean production look)",
          int(apply_display_options(base, default_layout_opts(), None)
              .layout.legend.borderwidth or 0) == 0, "")
    # The colour bar / colour range is reached by the same toggle.
    heat = go.Figure(go.Heatmap(z=[[1, 2], [3, 4]],
                                colorbar=dict(borderwidth=1, bordercolor="#123456")))
    check("layout: Show border OFF -> colour bar frame removed",
          int(apply_display_options(heat, {"show_border": False}, None)
              .data[0].colorbar.borderwidth or 0) == 0, "")
    check("layout: Show border ON -> colour bar frame drawn",
          int(apply_display_options(heat, {"show_border": True}, None)
              .data[0].colorbar.borderwidth or 0) == 1, "")

    # -- Legend column adapts to the labels that will be painted ----------
    short_lbl = go.Figure()
    long_lbl = go.Figure()
    for nm in ("Mean", "Min"):
        short_lbl.add_trace(go.Bar(x=["a"], y=[1], name=nm))
    for nm in ("Very Long Series Name Alpha", "Very Long Series Name Beta"):
        long_lbl.add_trace(go.Bar(x=["a"], y=[1], name=nm))
    d_short = list(apply_display_options(short_lbl, {"legend_position": "Top Left"},
                                         None).layout.xaxis.domain)[0]
    d_long = list(apply_display_options(long_lbl, {"legend_position": "Top Left"},
                                        None).layout.xaxis.domain)[0]
    check("legend: reserved column grows with long labels", d_long > d_short,
          f"short={d_short:.3f} long={d_long:.3f}")

    # -- Theme layer (light mode needs its own canvas + text colours) -----
    # Themed from a figure that carries the Typography defaults, since those
    # are exactly the dark-mode values the theme is expected to re-map.
    from chart_display import theme_figure
    themed_src = plain
    light = theme_figure(themed_src, "light")
    dark = theme_figure(themed_src, "dark")
    check("theme: light canvas is white",
          light.layout.paper_bgcolor == "#ffffff"
          and light.layout.plot_bgcolor == "#ffffff",
          f"{light.layout.paper_bgcolor}/{light.layout.plot_bgcolor}")
    check("theme: dark canvas stays dark",
          dark.layout.paper_bgcolor == "#0f172a", str(dark.layout.paper_bgcolor))
    check("theme: dark default tick colour flips for light mode",
          str(light.layout.xaxis.tickfont.color) == "#475569",
          str(light.layout.xaxis.tickfont.color))
    check("theme: dark mode keeps its tick colour",
          str(dark.layout.xaxis.tickfont.color) == "#94a3b8",
          str(dark.layout.xaxis.tickfont.color))
    check("theme: legend background is transparent in light mode",
          str(light.layout.legend.bgcolor).replace(" ", "") == "rgba(0,0,0,0)",
          str(light.layout.legend.bgcolor))
    check("theme: legend background is transparent in dark mode",
          str(dark.layout.legend.bgcolor).replace(" ", "") == "rgba(0,0,0,0)",
          str(dark.layout.legend.bgcolor))
    # With no plate the legend TEXT lands directly on the canvas, so it has
    # to follow the mode too: light mode must re-tint the near-white default
    # (#e2e8f0) to dark ink or the labels vanish on the white canvas.
    check("theme: light mode re-tints the legend item text",
          str(light.layout.legend.font.color) == "#334155",
          str(light.layout.legend.font.color))
    check("theme: light mode re-tints the legend title text",
          str(light.layout.legend.title.font.color) == "#334155",
          str(light.layout.legend.title.font.color))
    check("theme: dark mode keeps the legend item text",
          str(dark.layout.legend.font.color) == "#e2e8f0",
          str(dark.layout.legend.font.color))
    # A user-picked legend colour is not a dark DEFAULT, so it must survive.
    check("theme: user-picked legend colour survives the light switch",
          str(theme_figure(apply_display_options(
              base, None, {"legend_item_color": "#ff00ff"}),
              "light").layout.legend.font.color) == "#ff00ff", "")

    # Colour-bar tick labels / title are drawn on the canvas too. An
    # UNSET colour falls back to Plotly's own mid-grey template value, which
    # is illegible in one mode or the other, so the theme has to supply one.
    from chart_display import CHART_THEME
    for mode, want in (("dark", CHART_THEME["dark"]["tick_text"]),
                       ("light", CHART_THEME["light"]["tick_text"])):
        cb_fig = theme_figure(go.Figure(go.Heatmap(z=[[1, 2]])), mode)
        check(f"theme: {mode} colorbar ticks get a readable colour",
              str(cb_fig.data[0].colorbar.tickfont.color).lower() == want.lower(),
              str(cb_fig.data[0].colorbar.tickfont.color))
        check(f"theme: {mode} colorbar title gets a readable colour",
              str(cb_fig.data[0].colorbar.title.font.color).lower() == want.lower(),
              str(cb_fig.data[0].colorbar.title.font.color))
    # ... and an explicit dark default on the bar is remapped, not blanked.
    cb_dark = go.Figure(go.Heatmap(
        z=[[1, 2]], colorbar=dict(tickfont=dict(color="#94a3b8"),
                                  title=dict(font=dict(color="#cbd5e1")))))
    cb_light = theme_figure(cb_dark, "light")
    check("theme: light mode flips an explicit colorbar tick colour",
          str(cb_light.data[0].colorbar.tickfont.color) == "#475569",
          str(cb_light.data[0].colorbar.tickfont.color))
    check("theme: light mode flips an explicit colorbar title colour",
          str(cb_light.data[0].colorbar.title.font.color) == "#334155",
          str(cb_light.data[0].colorbar.title.font.color))
    check("theme: value labels flip for light mode",
          str(theme_figure(go.Figure(go.Bar(x=["a"], y=[1],
                                            textfont=dict(color="white"))),
                           "light").data[0].textfont.color) == "#1e293b", "")
    # A colour the user picked is not a dark DEFAULT, so it must survive.
    custom = apply_display_options(base, None, {"axis_tick_color": "#00ff00"})
    check("theme: user-picked colour survives the light switch",
          str(theme_figure(custom, "light").layout.xaxis.tickfont.color) == "#00ff00",
          str(theme_figure(custom, "light").layout.xaxis.tickfont.color))
    check("theme: never mutates the source figure",
          base.to_json() == base_snapshot, "")


# -----------------------------------------------------------------------
# 2. Worker integration
# -----------------------------------------------------------------------

def test_worker(df):
    print("== ChartGenerationWorker (display options) ==")
    from desktop.workers import ChartGenerationWorker

    got = []
    w = ChartGenerationWorker(
        "statistical", df, {"x": "region", "y": ["sales"], "palette": None},
        None,
        layout_opts={"title": "Worker Title", "x_label": "Region",
                     "show_value_labels": False},
        typography_opts={"header_size": 33},
    )
    w.finished_ok.connect(lambda res: got.extend(res))
    w.run()  # synchronous (no thread started)

    check("worker: one chart produced", len(got) == 1, str(len(got)))
    if got:
        fig = got[0][2]
        check("worker: layout title applied",
              fig.layout.title.text == "Worker Title",
              str(fig.layout.title.text))
        check("worker: x label applied", fig.layout.xaxis.title.text == "Region",
              str(fig.layout.xaxis.title.text))
        check("worker: value labels hidden",
              all(tr.textposition == "none" for tr in fig.data), "")
        check("worker: header size applied", fig.layout.title.font.size == 33,
              str(fig.layout.title.font.size))


# -----------------------------------------------------------------------
# 3. Panel wiring
# -----------------------------------------------------------------------

def _select_analysis(page, aid: str) -> None:
    for i in range(page.analysis_list.count()):
        it = page.analysis_list.item(i)
        if it.data(0x0100) == aid:  # Qt.UserRole
            page.analysis_list.setCurrentItem(it)
            return
    raise AssertionError(f"analysis type {aid} not in list")


def test_panel(df):
    print("== AnalysisConfigPanel / settings builder ==")
    from PySide6.QtWidgets import QComboBox, QCheckBox
    from desktop.session_bridge import SessionBridge
    from desktop.pages.analysis_page import AnalysisPage
    from services.analysis_service import WIDGET_SPEC

    b = SessionBridge()
    b.set_df(df)
    page = AnalysisPage(b)
    panel = page.config_panel

    _select_analysis(page, "statistical")
    check("statistical: builder registered", panel.has_builder(), "")
    check("statistical: generate tab has combos",
          len(panel.findChildren(QComboBox)) >= 3,
          str(len(panel.findChildren(QComboBox))))
    check("statistical: layout+typo tabs have checkboxes",
          len(panel.findChildren(QCheckBox)) >= 2,
          str(len(panel.findChildren(QCheckBox))))
    check("statistical: action row inside Generate tab",
          panel.action_button() is not None
          and panel.action_button().parent() is not None
          and panel.action_button().parent().parent() is panel.body_generate, "")
    check("statistical: Re-Generate disabled on fresh form",
          not panel.regenerate_button().isEnabled(), "")

    _select_analysis(page, "distribution")
    panel = page.config_panel
    check("distribution: builder registered", panel.has_builder(), "")
    check("distribution: generate tab has fields",
          len(panel.findChildren(QComboBox)) >= 1
          and len(panel.findChildren(QCheckBox)) >= 1,
          f"{len(panel.findChildren(QComboBox))} combos / "
          f"{len(panel.findChildren(QCheckBox))} checks")
    check("distribution: action row still present",
          panel.action_button() is not None
          and panel.action_button().parent() is not None
          and panel.action_button().parent().parent() is panel.body_generate, "")

    _select_analysis(page, "statistical")
    panel = page.config_panel
    events = []
    panel.settings_changed.connect(lambda k, p: events.append(k))
    bldr = panel._builder

    check("panel: form starts clean", not panel.is_dirty(), "")
    bldr._combo_groupby.setCurrentText("region")
    bldr._on_groupby("region")
    check("values change emits kind=values", "values" in events, str(events))
    check("values change marks dirty", panel.is_dirty(), "")
    check("collect x", panel.collect_values().get("x") == "region",
          str(panel.collect_values()))

    bldr._set_layout(title="T1")
    check("layout change emits kind=layout", "layout" in events, str(events))
    bldr._set_typo(header_size=21)
    check("typo change emits kind=typography", "typography" in events, str(events))
    check("layout/typo changes mark dirty", panel.is_dirty(), "")

    panel.prefill({"x": None, "y": ["profit"], "palette": None})
    panel.prefill_layout({"title": "Restored"})
    panel.reset_dirty()
    check("prefill+reset -> clean", not panel.is_dirty(), "")
    check("prefill layout visible in collect",
          panel.collect_layout().get("title") == "Restored",
          str(panel.collect_layout().get("title")))
    check("prefill values visible in collect",
          panel.collect_values().get("y") == ["profit"],
          str(panel.collect_values().get("y")))

    ok_all = True
    for aid in WIDGET_SPEC:
        try:
            _select_analysis(page, aid)
        except Exception:
            ok_all = False
    check("all analysis types build the panel without errors", ok_all, "")
    return page, b


# -----------------------------------------------------------------------
# 4. Page flows (real worker thread)
# -----------------------------------------------------------------------

def _drain(page, timeout_s: float = 15.0) -> None:
    """Wait for the generation worker and deliver its queued signals."""
    w = getattr(page, "_gen_worker", None)
    if w is not None:
        w.wait(int(timeout_s * 1000))
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.01)


def _set_form(page, group, metrics) -> None:
    bldr = page.config_panel._builder
    if group is None:
        bldr._combo_groupby.setCurrentIndex(0)
        bldr._on_groupby("None")
    else:
        bldr._combo_groupby.setCurrentText(group)
        bldr._on_groupby(group)
    bldr._combo_metrics.set_selection(list(metrics))
    bldr._on_metrics(list(metrics))


def test_page_flows(page, b):
    print("== AnalysisPage generate / re-generate / live preview ==")
    panel = page.config_panel
    _select_analysis(page, "statistical")

    # -- dirty gate with NO chart selected: never enabled ----------------
    _set_form(page, "region", ["sales"])
    check("no target: change does NOT enable Re-Generate",
          not panel.regenerate_button().isEnabled(), "")

    # -- GENERATE appends with full meta snapshot -------------------------
    page._on_generate()
    _drain(page)
    charts = b.get("charts", [])
    check("generate: one chart for one metric", len(charts) == 1,
          str([t for _, t, _ in charts]))
    uid1 = charts[0][0]
    meta1 = b.get_chart_meta(uid1)
    check("generate: meta has config/layout/typography",
          {"config", "layout", "typography"} <= set(meta1.keys()),
          str(sorted(meta1.keys())))
    check("generate: config snapshot matches form",
          meta1["config"].get("x") == "region"
          and meta1["config"].get("y") == ["sales"],
          str(meta1["config"]))
    check("generate: form bound to new chart",
          page._settings_target_uid == uid1, str(page._settings_target_uid))
    check("generate: clean after generate -> Re-Generate disabled",
          not panel.is_dirty() and not panel.regenerate_button().isEnabled(), "")

    # -- dirty gate AFTER a chart is selected ------------------------------
    _set_form(page, "region", ["sales", "profit"])
    check("target bound + change -> Re-Generate enabled",
          panel.regenerate_button().isEnabled(), "")

    # -- RE-GENERATE replaces in place, extras append ----------------------
    page._on_regenerate()
    _drain(page)
    charts = b.get("charts", [])
    check("re-generate: replace + 1 extra appended", len(charts) == 2,
          str([t for _, t, _ in charts]))
    check("re-generate: target replaced in place (same uid, position 0)",
          charts[0][0] == uid1, str(charts[0][0]))
    check("re-generate: clean -> Re-Generate disabled again",
          not panel.is_dirty() and not panel.regenerate_button().isEnabled(), "")
    meta1 = b.get_chart_meta(uid1)
    check("re-generate: meta config updated",
          meta1["config"].get("y") == ["sales", "profit"],
          str(meta1["config"]))

    # -- Live preview: layout title ----------------------------------------
    card1 = page._chart_cards.get(uid1)
    panel.tab_bar.setCurrentIndex(1)
    panel._builder._le_title.setText("My Custom Title")
    page._preview_timer.stop()
    page._apply_live_preview()
    charts = b.get("charts", [])
    check("preview: bridge chart title updated", charts[0][1] == "My Custom Title",
          str(charts[0][1]))
    check("preview: card figure title updated",
          card1._fig.layout.title.text == "My Custom Title",
          str(card1._fig.layout.title.text))
    check("preview: card title/tooltip updated",
          card1._title == "My Custom Title"
          and card1.toolTip() == "My Custom Title",
          f"{card1._title!r} / {card1.toolTip()!r}")
    check("preview: meta layout persisted",
          b.get_chart_meta(uid1).get("layout", {}).get("title")
          == "My Custom Title",
          str(b.get_chart_meta(uid1).get("layout")))
    check("preview: layout edit enables Re-Generate",
          panel.regenerate_button().isEnabled(), "")

    # -- Live preview: typography ------------------------------------------
    panel.tab_bar.setCurrentIndex(2)
    panel._builder._set_typo(header_size=42)
    page._preview_timer.stop()
    page._apply_live_preview()
    check("preview: header size lands on card figure",
          card1._fig.layout.title.font.size == 42,
          str(card1._fig.layout.title.font.size))
    check("preview: typography meta persisted",
          b.get_chart_meta(uid1).get("typography", {}).get("header_size") == 42,
          str(b.get_chart_meta(uid1).get("typography")))
    check("preview: card fig changed but base fig untouched",
          card1._fig.layout.title.font.size == 42
          and card1._base_fig.layout.title.font.size != 42, "")

    # -- GENERATE appends (does not overwrite) ------------------------------
    panel.tab_bar.setCurrentIndex(0)
    n_before = len(b.get("charts", []))
    page._on_generate()
    _drain(page)
    charts = b.get("charts", [])
    check("generate: appends without touching existing",
          len(charts) == n_before + 2 and charts[0][0] == uid1,
          f"n={len(charts)} first={charts[0][0]}")
    new_uids = {u for u, _, _ in charts[n_before:]}
    check("generate: form rebinds to a NEW chart",
          page._settings_target_uid in new_uids,
          str(page._settings_target_uid))
    check("generate: rebinding cleared the dirty gate",
          not panel.is_dirty() and not panel.regenerate_button().isEnabled(), "")

    # -- Tab select prefills that chart's own settings ----------------------
    page.charts_tabs.setCurrentIndex(0)
    page._on_tab_changed()
    vals = panel.collect_values()
    check("tab select: chart 1 form prefilled",
          vals.get("x") == "region" and vals.get("y") == ["sales", "profit"],
          str(vals))
    check("tab select: layout title prefilled",
          panel.collect_layout().get("title") == "My Custom Title",
          str(panel.collect_layout().get("title")))
    check("tab select: clean after prefill -> Re-Generate disabled",
          not panel.is_dirty() and not panel.regenerate_button().isEnabled(), "")

    # -- 🛠 edit binds the target ------------------------------------------
    _select_analysis(page, "distribution")
    check("type switch: form unbound",
          page._settings_target_uid is None, str(page._settings_target_uid))
    page._on_edit_chart(uid1)
    check("edit: type switched back + target bound",
          page._active_analysis == "statistical"
          and page._settings_target_uid == uid1,
          f"{page._active_analysis} / {page._settings_target_uid}")
    check("edit: form prefilled from stored config",
          panel.collect_values().get("x") == "region",
          str(panel.collect_values()))

    # -- The tab strip is SCOPED to the selected analysis type --------------
    # Selecting another type empties the strip (no charts of that type exist
    # yet) and switching back re-attaches the same card widgets -- no reload.
    # The form then follows whichever chart tab is selected.
    _select_analysis(page, "distribution")
    check("type scope: distribution strip hides the statistical charts",
          len(page._tab_order) == 0, str(page._tab_order))
    _select_analysis(page, "statistical")
    check("type scope: switching back re-attaches the statistical tabs",
          len(page._tab_order) > 1, str(page._tab_order))
    other_idx = 1 if len(page._tab_order) > 1 else 0
    if page.charts_tabs.currentIndex() == other_idx and len(page._tab_order) > 1:
        other_idx = 1 - other_idx
    page.charts_tabs.setCurrentIndex(other_idx)
    page._on_tab_changed()
    check("tab select: form follows the tab's chart",
          page._active_analysis == "statistical"
          and page._settings_target_uid == page._tab_order[other_idx],
          f"{page._active_analysis} / {page._settings_target_uid}")

    # -- Generate always appends (no blank-form flow) ------------------------
    check("no blank-form flow: _on_add_chart removed",
          not hasattr(page, "_on_add_chart"), "")

    # -- Delete selected chart clears the binding ---------------------------
    n_charts = len(page._tab_order)
    page.charts_tabs.setCurrentIndex(0)
    page._on_tab_changed()
    page._on_delete_chart(page._tab_order[0])
    check("delete: chart removed", len(page._tab_order) == n_charts - 1, "")
    if page._tab_order:
        check("delete: binding moved to a surviving chart",
              page._settings_target_uid in page._tab_order,
              str(page._settings_target_uid))
    else:
        check("delete: binding cleared when no charts left",
              page._settings_target_uid is None, str(page._settings_target_uid))


# -----------------------------------------------------------------------
# 5. Reload round trip
# -----------------------------------------------------------------------

def test_reload(df, b):
    print("== Reload: second page re-prefills from stored meta ==")
    from desktop.pages.analysis_page import AnalysisPage

    page2 = AnalysisPage(b)
    _select_analysis(page2, "statistical")
    panel = page2.config_panel
    vals = panel.collect_values()
    check("reload: generate tab restored", vals.get("x") == "region", str(vals))
    check("reload: form bound to a chart + clean",
          page2._settings_target_uid is not None and not panel.is_dirty()
          and not panel.regenerate_button().isEnabled(),
          str(page2._settings_target_uid))


def test_settings_tabs_fit_design(df):
    """The Layout/Typography tabs must mirror chart_designs.pdf: option rows
    laid out in columns (2 / 3) so every control fits the 360px-tall default
    top pane of the splitter instead of clipping or scrolling."""
    print("== Settings tabs: design layout (columns, no clipping) ==")
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel
    from desktop.session_bridge import SessionBridge
    from desktop.pages.analysis_page import AnalysisPage
    from desktop.widgets.analysis_settings import base as sbase

    b = SessionBridge()
    b.set_df(df)
    page = AnalysisPage(b)
    page.resize(1560, 980)
    page.show()
    _drain(page)
    _select_analysis(page, "statistical")
    panel = page.config_panel

    def _rows(col):
        """Label/checkbox text of each option row in a column stack, in order."""
        out = []
        lay = col.layout()
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is None:
                continue
            out.extend(l.text() for l in w.findChildren(QLabel))
            out.extend(c.text() for c in w.findChildren(QCheckBox))
        return out

    for idx, name, want_cols in ((1, "layout", 2), (2, "typography", 3)):
        panel.tab_bar.setCurrentIndex(idx)
        for _ in range(3):
            QApplication.processEvents()
        body = panel.current_body()
        col_row = None
        # The builder's page root is a child of the tab body, so look one
        # level down for the row that holds the option columns.
        for i in range(body.layout().count()):
            page_root_w = body.layout().itemAt(i).widget()
            if page_root_w is None or page_root_w.layout() is None:
                continue
            for j in range(page_root_w.layout().count()):
                w = page_root_w.layout().itemAt(j).widget()
                if w is not None and isinstance(w.layout(), QHBoxLayout) \
                        and w.layout().count() == want_cols:
                    col_row = w
                    break
            if col_row is not None:
                break
        check(f"{name}: {want_cols} option columns (design)", col_row is not None,
              "column row not found")
        if col_row is not None:
            stacks = [col_row.layout().itemAt(i).widget() for i in range(want_cols)]
            check(f"{name}: every column is a vertical stack",
                  all(s.layout() is not None and s.layout().count() >= 2
                      for s in stacks),
                  str([s.layout().count() if s.layout() else None for s in stacks]))
            if name == "layout":
                # chart_designs.pdf puts the title/label fields in the left
                # column and the display rows in the right one. The legend
                # arrangement rows (orientation/order + click behaviour) live
                # with the other legend fields on the left.
                check("layout: titles in the left column (design)",
                      _rows(stacks[0]) == ["Chart title", "X-axis", "Y-axis",
                                           "Legend title", "Legend position",
                                           "Legend layout", "Click"],
                      str(_rows(stacks[0])))
                check("layout: display rows in the right column (design)",
                      _rows(stacks[1]) == ["Show value labels", "Label pos",
                                           "Bar mode", "Bar gap", "Show grid",
                                           "Show border"],
                      str(_rows(stacks[1])))
        scroll = panel.row1.widget(idx)
        check(f"{name}: fits the default pane (no scrolling)",
              scroll.verticalScrollBar().maximum() == 0,
              f"max={scroll.verticalScrollBar().maximum()}")
    # -- Design row set + order -------------------------------------------
    order = [w.text() for w in panel.body_layout.findChildren(QLabel)]
    boxes = [c.text() for c in panel.body_layout.findChildren(QCheckBox)]
    order += boxes
    check("layout: design rows present",
          all(t in order for t in ("Show value labels", "Label pos", "Bar mode",
                                   "Bar gap", "Show grid", "Show border")), str(order))
    check("layout: checkbox rows carry their own label",
          boxes == ["Show value labels", "Show grid", "Show border"], str(boxes))
    typo = [w.text() for w in panel.body_typo.findChildren(QLabel)]
    check("typography: design rows present",
          all(t in typo for t in
              ("Font family", "Font style", "Header size", "Header color",
               "Header font", "Header font style", "Title size", "Title color",
               "Axis size", "Axis color", "Legend size", "Legend color")),
          str(typo))
    # -- Typography readouts mirror the mockup (28 / 16 / 12 / 11) ---------
    bldr0 = panel._builder
    got = (bldr0._hs_spin.value(), bldr0._ts_spin.value(),
           bldr0._axs_spin.value(), bldr0._ls_spin.value())
    check("typography: default sizes match the design readouts",
          got == (28, 16, 12, 11), str(got))
    # -- Every control is wired to the live preview ------------------------
    bldr = panel._builder
    panel.tab_bar.setCurrentIndex(0)
    bldr._combo_groupby.setCurrentText("region")
    bldr._on_groupby("region")
    bldr._combo_metrics.set_selection(["sales"])
    bldr._on_metrics(["sales"])
    page._on_generate()
    _drain(page)
    uid = page._settings_target_uid
    card = page._chart_cards.get(uid)
    check("live: chart generated for the wiring checks", card is not None, str(uid))

    def _live():
        page._preview_timer.stop()
        page._apply_live_preview()
        return card._fig

    panel.tab_bar.setCurrentIndex(1)
    bldr._cb_bmode.setCurrentText("stack")
    check("live: Bar mode widget -> barmode", _live().layout.barmode == "stack",
          str(card._fig.layout.barmode))
    bldr._ck_vals.setChecked(False)
    check("live: Show value labels widget -> labels hidden",
          all(t.textposition == "none" for t in _live().data), "")
    bldr._ck_vals.setChecked(True)
    bldr._gap_spin.setValue(0.55)
    check("live: Bar gap spin -> bargap",
          abs(float(_live().layout.bargap) - 0.55) < 1e-6,
          str(card._fig.layout.bargap))
    bldr._ck_grid.setChecked(False)
    check("live: Show grid widget -> xaxis.showgrid off",
          _live().layout.xaxis.showgrid is False, "")
    bldr._ck_border.setChecked(False)
    check("live: Show border widget -> legend frame off",
          int(_live().layout.legend.borderwidth or 0) == 0,
          str(card._fig.layout.legend.borderwidth))
    bldr._ck_border.setChecked(True)
    check("live: Show border widget -> legend frame back on",
          int(_live().layout.legend.borderwidth or 0) == 1,
          str(card._fig.layout.legend.borderwidth))
    bldr._cb_legpos.setCurrentText("Bottom Left")
    # Anchored at the edge of its RESERVED strip rather than paper 0,0 -- the
    # reservation converts the column the legend sits in to a paper
    # coordinate, so the plate hugs the plot edge instead of floating in the
    # card margin. It must land bottom-left: x on the left half, y at 0.
    _lx = float(_live().layout.legend.x)
    _ly = float(_live().layout.legend.y)
    check("live: Legend position widget -> legend moved (bottom left)",
          0.0 <= _lx < 0.5 and _ly == 0.0,
          f"({card._fig.layout.legend.x}, {card._fig.layout.legend.y})")
    panel.tab_bar.setCurrentIndex(2)
    bldr._hs_spin.setValue(44)
    check("live: Header size spin -> title font size",
          _live().layout.title.font.size == 44,
          str(card._fig.layout.title.font.size))
    bldr._axs_spin.setValue(15)
    check("live: Axis size spin -> tick font size",
          _live().layout.xaxis.tickfont.size == 15,
          str(card._fig.layout.xaxis.tickfont.size))
    bldr._ls_spin.setValue(14)
    check("live: Legend size spin -> legend font size",
          _live().layout.legend.font.size == 14,
          str(card._fig.layout.legend.font.size))
    bldr._cb_fam.setCurrentText("Lato")
    check("live: Font family combo -> plot font",
          "Lato" in str(_live().layout.font.family),
          str(card._fig.layout.font.family))
    # Colour swatches: stub the picker so the real click path runs.
    _orig = sbase.QColorDialog.getColor
    try:
        sbase.QColorDialog.getColor = staticmethod(lambda *a, **k: QColor("#00ff00"))
        for attr, probe in (
            ("_sw_hcol", lambda f: f.layout.title.font.color),
            ("_sw_tcol", lambda f: f.layout.xaxis.title.font.color),
            ("_sw_acol", lambda f: f.layout.xaxis.tickfont.color),
            ("_sw_lcol", lambda f: f.layout.legend.font.color),
        ):
            getattr(bldr, attr).click()
            got = str(probe(_live()))
            check(f"live: {attr} picker -> figure colour", got.lower() == "#00ff00", got)
    finally:
        sbase.QColorDialog.getColor = _orig

    # Axis size is persisted/reloaded like the other typography values.
    meta_typo = b.get_chart_meta(uid).get("typography", {})
    check("live: axis size persisted in meta",
          meta_typo.get("axis_tick_size") == 15, str(meta_typo))
    bldr.prefill_typography(meta_typo)
    check("prefill: Axis size spin restored", bldr._axs_spin.value() == 15,
          str(bldr._axs_spin.value()))
    bldr.prefill_layout(b.get_chart_meta(uid).get("layout", {}))
    check("prefill: Bar gap restored",
          abs(bldr._gap_spin.value() - 0.55) < 1e-6, str(bldr._gap_spin.value()))
    check("prefill: Legend position combo restored",
          bldr._cb_legpos.currentText() == "Bottom Left",
          bldr._cb_legpos.currentText())


# -----------------------------------------------------------------------
# 6. Generate / Re-Generate must not rebuild the chart tiles
# -----------------------------------------------------------------------

def test_incremental_refresh(df):
    """Guards the "the app closed and reopened itself" Generate bug.

    Every ChartCard owns a QWebEngineView; rebuilding the tiles makes each
    one reload the plotly.js shell and repaint from scratch, which reads as
    the whole chart area flashing.  Re-Generate has to reuse the SAME card
    (and therefore the same loaded web view) and only push the new figure,
    so the number of views built is pinned here.
    """
    print("== AnalysisPage: chart tiles are reused on refresh ==")
    from desktop.session_bridge import SessionBridge
    import desktop.widgets.chart_card as _cc
    from desktop.pages.analysis_page import AnalysisPage

    created = []

    class _CountingView(_cc.PlotlyWebView):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            created.append(self)

    real_view = _cc.PlotlyWebView
    _cc.PlotlyWebView = _CountingView
    try:
        b = SessionBridge()
        b.set_df(df)
        page = AnalysisPage(b)
        _select_analysis(page, "statistical")

        _set_form(page, "region", ["sales"])
        page._on_generate()
        _drain(page)
        uid = page._settings_target_uid
        card = page._chart_cards.get(uid)
        check("reuse: first Generate builds one view", len(created) == 1,
              str(len(created)))

        # Change a LAYOUT option (still one chart) and Re-Generate in place.
        bldr = page.config_panel._builder
        bldr._set_layout(title="Reuse Title")
        check("reuse: a change enables Re-Generate",
              page.config_panel.regenerate_button().isEnabled(), "")
        page._on_regenerate()
        _drain(page)
        check("reuse: Re-Generate still holds one chart",
              len(b.get("charts", [])) == 1, str(len(b.get("charts", []))))
        check("reuse: Re-Generate keeps the same ChartCard",
              page._chart_cards.get(uid) is card, "")
        check("reuse: Re-Generate builds NO extra web view", len(created) == 1,
              str(len(created)))
        bfig = None
        for c_uid, _t, c_fig in b.get("charts", []):
            if c_uid == uid:
                bfig = c_fig
        check("reuse: the re-generated figure reached the card",
              bfig is not None and card._fig is bfig, "")
        check("reuse: Re-Generate leaves the form clean (gate off)",
              not page.config_panel.is_dirty()
              and not page.config_panel.regenerate_button().isEnabled(), "")

        # A genuinely new chart still needs (and gets) its own tile.
        _set_form(page, "region", ["profit"])
        page._on_generate()
        _drain(page)
        check("reuse: a brand-new chart adds exactly one view",
              len(created) == 2, str(len(created)))

        # And deleting a chart disposes only that tile.
        victim = next(u for u in list(page._chart_cards))
        page.bridge.remove_chart(victim)
        page._refresh_charts()
        check("reuse: deleted chart's tile is dropped",
              victim not in page._chart_cards and victim not in page._tab_order,
              str(list(page._chart_cards)))
        check("reuse: other tiles survive the delete",
              len(page._chart_cards) == len(page._tab_order)
              and len(page._chart_cards) == 1, str(list(page._chart_cards)))
    finally:
        _cc.PlotlyWebView = real_view


def test_dashboard_reuse(df):
    """The board is the report document now -- no per-chart Qt tiles.

    The old checks counted ``PlotlyWebView`` tile construction through
    ``DashboardPage._chart_cards``; that page was replaced by the report
    canvas (the board IS the exported document), so the same guarantees are
    asserted against the document the canvas renders: one chart-card per
    bridge chart, add/delete reflected in it, the canvas widget itself never
    rebuilt, and the theme-switch path staying clean.
    """
    print("== DashboardPage: the canvas document tracks the chart list ==")
    import plotly.graph_objects as go
    from desktop.session_bridge import SessionBridge
    from desktop.pages.dashboard_page import DashboardPage
    from services.export_service import build_export_charts, build_export_report

    b = SessionBridge()
    b.set_df(df)
    for uid in ("d1", "d2"):
        b.add_chart(uid, f"Chart {uid}",
                    go.Figure(go.Bar(x=["a", "b"], y=[1, 2], name=uid)),
                    chart_type="statistical", meta={})
    page = DashboardPage(b)
    canvas = page.report_canvas
    check("dashboard: report canvas built", canvas is not None, "")

    def _doc() -> str:
        snap = page._snapshot_inputs()
        export_charts = build_export_charts(
            snap["charts"], meta_map=snap["meta_map"], notes=snap["notes"])
        html, _ = build_export_report(
            export_charts, snap["session_name"], snap["state"],
            orientation=snap["orientation"], kpis=snap["kpis"],
            dashboard_title=snap["dashboard_title"],
            grid_cols_n=snap["grid_cols_n"],
            tile_geometry=snap["tile_geometry"],
            kpi_geometry=snap["kpi_geometry"],
            slicers=snap["slicers"], slicer_geometry=snap["slicer_geometry"],
            slicer_values={}, geometry_mode=snap["geometry_mode"],
            inline_plotly=False, editor=True)
        return html

    html = _doc()
    check("dashboard: one chart-card per chart in the document",
          'data-uid="d1"' in html and 'data-uid="d2"' in html, "")
    page._refresh_charts()
    check("dashboard: refresh reuses the same canvas widget",
          page.report_canvas is canvas, "")
    b.add_chart("d3", "Chart d3",
                go.Figure(go.Bar(x=["a"], y=[3], name="d3")),
                chart_type="statistical", meta={})
    page._refresh_charts()
    html = _doc()
    check("dashboard: a new chart joins the document, others stay",
          all(f'data-uid="{u}"' in html for u in ("d1", "d2", "d3")), "")
    b.remove_chart("d2")
    page._refresh_charts()
    html = _doc()
    check("dashboard: delete drops only that chart's card",
          'data-uid="d2"' not in html
          and 'data-uid="d1"' in html and 'data-uid="d3"' in html, "")
    # A theme switch routes through the page handler; it must not raise and
    # must leave the canvas in place.
    from desktop import theme as _t
    try:
        _t.apply_theme(QApplication.instance(), "light")
        b.theme_changed.emit("light")
        for _ in range(5):
            QApplication.processEvents()
        check("dashboard: theme switch keeps the canvas alive",
              page.report_canvas is canvas, "")
    finally:
        _t.apply_theme(QApplication.instance(), "dark")
        b.theme_changed.emit("dark")
    page._regen_fast.stop()
    page._regen_slow.stop()


def main() -> int:
    # Chart deletes ask first via QMessageBox.question(), which blocks for
    # user input that never arrives in an offscreen run -- the delete-flow
    # checks below would hang. Delete logic itself is exercised, not the
    # confirmation dialog, so disable it for this script.
    import desktop.pages.analysis_page as _ap
    _ap.CONFIRM_CHART_DELETE = False
    a = app()
    df = make_df()

    test_apply_display_options(df)
    test_worker(df)
    _page, _b = test_panel(df)
    test_page_flows(_page, _b)
    test_reload(df, _b)
    test_settings_tabs_fit_design(df)
    test_incremental_refresh(df)
    test_dashboard_reuse(df)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())



