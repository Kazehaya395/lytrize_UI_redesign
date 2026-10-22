#!/usr/bin/env python3
"""Regression tests for the seven runtime-screenshot bug fixes.

Run from the repo root:
    QT_QPA_PLATFORM=offscreen python scripts/test_screenshot_fixes.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS: list = []
FAIL: list = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(detail) if (detail and not cond) else ""))


def test_tables_readable():
    print("[1] Preview tables readable and scrollable")
    from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QHeaderView
    from PySide6.QtCore import Qt
    from desktop.widgets.resizable_table import (
        ResizableTableView, apply_table_defaults, fit_columns_to_content,
        MIN_SECTION_WIDTH, MAX_SECTION_WIDTH,
    )
    from desktop.widgets.dataframe_model import DataFrameModel
    app = QApplication.instance() or QApplication([])
    import pandas as pd
    m = DataFrameModel(pd.DataFrame({"a": range(100)}))
    m.set_row_limit(10)
    check("model row-limit caps count", m.rowCount() == 10)
    m.set_row_limit(None)
    check("model row-limit clears", m.rowCount() == 100)
    df = pd.DataFrame({"c%d" % i: ["v%d_%d" % (i, r) for r in range(5)] for i in range(23)})
    t = ResizableTableView()
    t.set_dataframe(df)
    h = t.horizontalHeader()
    check("header policy is Interactive", h.sectionResizeMode(0) == QHeaderView.Interactive)
    check("last section not stretched", h.stretchLastSection() is False)
    check("horizontal scrollbar available", t.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded)
    ok_w = all(MIN_SECTION_WIDTH <= h.sectionSize(c) <= MAX_SECTION_WIDTH for c in range(23))
    check("every column within readable bounds", ok_w)
    q = QTableWidget(3, 4)
    for r in range(3):
        for c in range(4):
            q.setItem(r, c, QTableWidgetItem("cell-%d-%d" % (r, c)))
    q.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    apply_table_defaults(q)
    fit_columns_to_content(q)
    check("QTableWidget migrated off Stretch", q.horizontalHeader().sectionResizeMode(0) == QHeaderView.Interactive)


def test_show_border_default():
    print("[2] show_border default OFF + migration")
    import chart_display as cd
    check("default_layout_opts show_border is False", cd.default_layout_opts().get("show_border") is False)
    tmp = tempfile.mkdtemp()
    os.environ["LYTRIZE_DB_PATH"] = os.path.join(tmp, "t.db")
    from modules import database as db
    db.init_db()
    conn = db._connect()
    uid = conn.execute("SELECT id FROM users WHERE username=?", (db._GUEST_USERNAME,)).fetchone()[0]
    charts = [{"uid": "a", "meta": {"layout": {"show_border": True, "title": "x"}}}, {"uid": "b", "meta": {"layout": {"show_border": False}}}]
    conn.execute("INSERT INTO sessions (user_id, session_uuid, session_name, file_name, rows_count, cols_count, analysis_types, charts_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (uid, "u1", "S", "f.csv", 5, 2, "[]", json.dumps(charts)))
    conn.commit()
    # init_db already ran (and stamped) the migration on an empty table, so
    # reset its flag: this exercises the rewrite path for pre-existing rows.
    conn.execute("DELETE FROM app_meta WHERE key='migrated_show_border_default'")
    conn.commit()
    n = db._migrate_show_border_default(conn)
    conn.commit()
    check("migration rewrites one session", n == 1, n)
    items = json.loads(conn.execute("SELECT charts_json FROM sessions").fetchone()[0])
    check("migration clears inherited True", items[0]["meta"]["layout"]["show_border"] is False)
    check("migration keeps title", items[0]["meta"]["layout"]["title"] == "x")
    check("migration leaves explicit False alone", items[1]["meta"]["layout"]["show_border"] is False)
    check("migration is idempotent", db._migrate_show_border_default(conn) == 0)
    conn.close()
    os.environ.pop("LYTRIZE_DB_PATH", None)


def test_slicer_static_note():
    print("[3] Slicer static note is hover badge, export-only")
    import inspect
    from modules import export as ex
    src = inspect.getsource(ex.generate_html_report)
    check("note gated on not editor", "not report_payload and not editor" in src)
    check("note is span badge", "slicer-static-note\" title=" in src)
    check("no block static preview div", '<div class="slicer-static-note">Static preview' not in src)
    check("badge anchored to card", "position: relative;" in src)
    from services import export_service as es
    for k in ("ex_slicer_label_size", "ex_slicer_label_color", "ex_slicer_value_size", "ex_slicer_value_color"):
        check("EXPORT_DEFAULTS has " + k, k in es.EXPORT_DEFAULTS)
    theme = es.build_export_theme({})
    for k in ("slicer_label_size", "slicer_label_color", "slicer_value_size", "slicer_value_color"):
        check("theme carries " + k, k in theme)


def test_slicer_slider_for_dates():
    print("[4] Slicer offers slider for continuous and date columns")
    import pandas as pd
    import numpy as np
    from services import slicer_service as ss
    from services.filter_service import numeric_bounds
    from services.slicer_service import bounds_are_degenerate
    df = pd.DataFrame({"amt": np.linspace(1, 100, 50), "cat": ["a", "b"] * 25,
                       "when": pd.date_range("2020-01-01", periods=50, freq="D"),
                       "when_txt": pd.date_range("2020-01-01", periods=50, freq="D").strftime("%Y-%m-%d %H:%M:%S"),
                       "const": [7] * 50})
    check("numeric column offers slider", "slider" in ss.available_widgets(df, "amt"))
    check("datetime column offers slider", "slider" in ss.available_widgets(df, "when"))
    check("date-as-text offers slider", "slider" in ss.available_widgets(df, "when_txt"))
    check("categorical column has no slider", "slider" not in ss.available_widgets(df, "cat"))
    check("constant column has no slider", "slider" not in ss.available_widgets(df, "const"))
    b = numeric_bounds(df, "when_txt")
    check("date-as-text reports datetime bounds", b.get("kind") == "datetime" and bool(b.get("min")) and bool(b.get("max")))
    check("degenerate bounds detected", bounds_are_degenerate(numeric_bounds(df, "const")) is True)


def test_map_hover_all_aggs():
    print("[5] Map hover keeps every requested dim/value aggregation")
    import pandas as pd
    import numpy as np
    from modules.analysis.map_plot import _hover_agg_extras
    df = pd.DataFrame({"merchant": ["m1", "m1", "m2", "m2"] * 5,
                       "category": ["net", "net", "pos", "pos"] * 5,
                       "amt": np.linspace(10, 200, 20)})
    triples = _hover_agg_extras(df, "merchant", ["category"], [("amt", "max"), ("amt", "min")], skip_cols=())
    disps = [t[2] for t in triples]
    check("hover keeps the dim", "category" in disps)
    check("hover keeps MAX(amt)", "MAX(amt)" in disps)
    check("hover keeps MIN(amt)", "MIN(amt)" in disps)
    check("hover does not duplicate the dim", disps.count("category") == 1)


def test_worker_cancellation():
    print("[6] Canvas regen workers cancel stale builds")
    import inspect
    from desktop import workers as w
    src_prev = inspect.getsource(w.ReportPreviewWorker.run)
    src_inputs = inspect.getsource(w.ReportInputsWorker.run)
    check("preview worker bails when interrupted", "isInterruptionRequested" in src_prev)
    check("inputs worker bails when interrupted", "isInterruptionRequested" in src_inputs)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    job = w.ReportPreviewWorker([], "S", {}, 1, parent=None)
    fired = {"ready": False}
    job.ready.connect(lambda *a: fired.__setitem__("ready", True))
    job.start()
    job.requestInterruption()
    job.wait(5000)
    check("interrupted preview worker does not emit", fired["ready"] is False)


def test_session_bundle():
    print("[7] Session persists + reopens dataset and slicers")
    tmp = tempfile.mkdtemp()
    os.environ["LYTRIZE_DB_PATH"] = os.path.join(tmp, "t.db")
    import numpy as np
    import pandas as pd
    from modules import database as db
    from services import session_bundle as sb
    from services import slicer_service as ss
    df = pd.DataFrame({"cat": ["a", "b", "c"] * 1000, "amt": np.linspace(1, 1e6, 3000)})
    h, uri = sb.save_dataset(df)
    from modules.database import DB_PATH as _DBP
    check("bundle writes parquet", bool(uri) and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(_DBP)), "datasets", uri)))
    check("bundle dedupes same content", sb.save_dataset(df)[1] == uri)
    loaded = sb.load_dataset(uri)
    check("bundle reloads all rows/cols", loaded is not None and len(loaded) == len(df) and list(loaded.columns) == list(df.columns))
    db.init_db()
    conn = db._connect()
    uid = conn.execute("SELECT id FROM users WHERE username=?", (db._GUEST_USERNAME,)).fetchone()[0]
    slicers = [ss.make_slicer("cat", widget_kind="list", scope="report", values=["a"])]
    sid = db.save_session_db(user_id=uid, session_name="S", file_name="f.csv", rows=len(df), cols=2, analysis_types=[], charts_json="[]", dataset_uri=uri, dataset_hash=h, slicers_json=json.dumps(slicers))
    sm = db.get_session_meta(sid, uid)
    check("session meta carries dataset uri", sm.get("dataset_uri") == uri)
    check("session meta carries slicers", json.loads(sm.get("slicers_json", "[]"))[0]["column"] == "cat")
    db.update_session_db(session_id=sid, user_id=uid, session_name="S2", charts_json="[]", analysis_types=[], dataset_uri=uri, dataset_hash=h, slicers_json=json.dumps(slicers))
    sm2 = db.get_session_meta(sid, uid)
    check("session update keeps bundle", sm2.get("dataset_uri") == uri and sm2.get("dashboard_title", "") is not None)
    conn.close()
    os.environ.pop("LYTRIZE_DB_PATH", None)


def test_datetime_slider_end_to_end():
    print("[8] Datetime slider renders + filters end to end")
    import pandas as pd
    from modules.report_slicer import SLICER_JS
    check("JS range parses ISO bounds", "Date.parse(b)" in SLICER_JS)
    check("JS datetime slider writes back ISO", "new Date(ms).toISOString()" in SLICER_JS)
    from modules import export as ex
    import inspect
    src = inspect.getsource(ex.generate_html_report)
    check("exporter emits epoch-ms datetime slider", 'data-kind="datetime"' in src)
    from services.filter_service import apply_spec
    df = pd.DataFrame({"when": pd.date_range("2020-01-01", periods=10, freq="D")})
    out = apply_spec(df, {"kind": "range", "column": "when",
                          "low": "2020-01-03T00:00:00", "high": "2020-01-05T00:00:00"})
    check("python datetime range filters rows", int(out.sum()) == 3, int(out.sum()))


def test_title_card_and_timestamp():
    print("[9] Title card edge-to-edge, timestamp at bottom edge with tick")
    import re
    import plotly.graph_objects as go
    from services.export_service import build_export_report, EXPORT_DEFAULTS
    from modules import canvas_geometry
    fig = go.Figure(go.Bar(x=["a", "b"], y=[1, 2]))
    charts = [("u1", "B", fig, "", "categorical", {})]
    st = dict(EXPORT_DEFAULTS)
    check("timestamp tick defaults ON", st.get("ex_meta") is True)
    h1, _ = build_export_report(charts, "S", st, dashboard_title="T", editor=False, inline_plotly=False)
    cm = re.search(r'<div class="report-header title-card"[^>]*>(.*?)</div>\s*</div>', h1, re.S)
    check("title card carries heading only", bool(cm) and "<h1>T</h1>" in cm.group(1) and 'class="meta"' not in cm.group(1))
    check("no branding prefix", "Generated by" not in h1)
    check("footer has bare timestamp", bool(re.search(r'<div class="report-footer">.*?\d{4}-\d{2}-\d{2}.*?</div>', h1, re.S)))
    st2 = dict(st); st2["ex_meta"] = False
    h2, _ = build_export_report(charts, "S", st2, dashboard_title="T", editor=False, inline_plotly=False)
    check("unticked emits no footer", '<div class="report-footer">' not in h2 and '<div class="meta">' not in h2)
    check("title padding zero", "padding: 0;" in h1 and "padding: 0 !important;" in h1)
    check("title band sized for text only", canvas_geometry.title_band_height(28, True) < 60)
    # The live canvas IS this same document with editor chrome -- the
    # screenshot complaint was against the live board, so assert the editor
    # variant too, including that the bottom-edge footer follows the tick.
    h3, _ = build_export_report(charts, "S", st, dashboard_title="T",
                                editor=True, inline_plotly=False)
    cm3 = re.search(r'<div class="report-header title-card"[^>]*>(.*?)</div>\s*</div>', h3, re.S)
    check("live canvas title card is heading-only",
          bool(cm3) and "<h1>T</h1>" in cm3.group(1) and 'class="meta"' not in cm3.group(1))
    check("live canvas footer strip carries the timestamp",
          '<div class="report-footer">' in h3)
    st4 = dict(st); st4["ex_meta"] = False
    h4, _ = build_export_report(charts, "S", st4, dashboard_title="T",
                                editor=True, inline_plotly=False)
    check("unticked live canvas drops the footer and its space",
          '<div class="report-footer">' not in h4)


def test_home_banner_compact():
    print("[10] Home banner compact so sessions stay above the fold")
    import inspect
    from desktop.pages import home_page as hp
    src = inspect.getsource(hp.HomePage._build_ui)
    check("banner min lowered", "min_height=90" in src)
    check("banner max lowered", "max_height=150" in src)


def test_legend_transparent_default():
    print("[11] Legend background transparent by default for any chart")
    import plotly.express as px
    from chart_display import apply_display_options, theme_figure
    from modules.export import _theme_chart_furniture
    from services.export_service import (EXPORT_DEFAULTS, EXPORT_PRESETS,
                                         build_export_report)

    def _transparent(value):
        return str(value).replace(" ", "") == "rgba(0,0,0,0)"

    def _fig():
        return px.bar(px.data.iris(), x="species", y="sepal_length",
                      color="species")

    # Layout layer: the plate the legend got whenever a position was chosen --
    # the dark box the screenshot shows on the combo and pie charts.
    out = apply_display_options(_fig(), {"legend_position": "Top Right"}, None)
    check("layout layer gives the legend no plate",
          _transparent(out.layout.legend.bgcolor), str(out.layout.legend.bgcolor))
    check("no frame is drawn when show_border is off",
          int(out.layout.legend.borderwidth or 0) == 0,
          out.layout.legend.borderwidth)
    framed = apply_display_options(
        _fig(), {"legend_position": "Top Right", "show_border": True}, None)
    check("show_border still draws the legend frame",
          int(framed.layout.legend.borderwidth or 0) == 1,
          framed.layout.legend.borderwidth)

    # Theme layer (live canvas + report): BOTH modes land on transparent.
    for mode in ("dark", "light"):
        themed = theme_figure(_fig(), mode)
        check("theme_figure(%s) legend has no plate" % mode,
              _transparent(themed.layout.legend.bgcolor),
              str(themed.layout.legend.bgcolor))

    # Export layer: a runner-supplied plate is erased, text still follows the card.
    runner = _fig()
    runner.update_layout(legend=dict(bgcolor="rgba(15, 23, 42, 0.35)",
                                     font=dict(color="#e2e8f0")))
    _theme_chart_furniture(runner, "#ffffff", "#334155")
    check("export theming erases a runner-supplied plate",
          _transparent(runner.layout.legend.bgcolor),
          str(runner.layout.legend.bgcolor))
    check("export theming still re-tints the legend text for the card",
          str(runner.layout.legend.font.color) == "#334155",
          str(runner.layout.legend.font.color))

    # Every preset is a palette users pick from: none may paint a plate.
    bad = []
    for name, colours in EXPORT_PRESETS.items():
        f = _fig()
        f.update_layout(legend=dict(bgcolor="rgba(15, 23, 42, 0.35)"))
        _theme_chart_furniture(f, colours["ex_card"], "#334155")
        if not _transparent(f.layout.legend.bgcolor):
            bad.append(name)
    check("every quick theme keeps its legend plate-free", not bad, str(bad))

    # And the document the report is made of carries the transparent legend.
    html, _ = build_export_report(
        [("u1", "B", _fig(), "", "categorical", {})], "S",
        dict(EXPORT_DEFAULTS), dashboard_title="T",
        editor=False, inline_plotly=False)
    check("report HTML carries a transparent legend",
          "rgba(0, 0, 0, 0)" in html, "")
    check("report HTML carries no runner slate plate",
          "rgba(15, 23, 42, 0.35)" not in html, "")


def test_kpi_slicer_add_flow():
    """The "Add KPI / Add Slicer button does nothing" screenshots.

    Drives the REAL page handlers end to end -- dialog result, service
    calculation, bridge writes, snapshot of the shared regen entry point, and
    the document the canvas would render -- on a board pre-seeded like a
    LOADED session (existing KPI, two slicers, legacy marker-less grid
    geometry), because that is the state both screenshots were taken in.
    Only the modal ``exec()`` is short-circuited: everything the click leads
    to afterwards runs for real, so a raise anywhere in that chain fails here.
    """
    print("[12] Add KPI / Add Slicer: dialog -> bridge -> snapshot -> report")
    os.environ["LYTRIZE_DB_PATH"] = os.path.join(tempfile.mkdtemp(),
                                                 "add_flow.db")
    import pandas as pd
    import plotly.graph_objects as go
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])
    from desktop.session_bridge import SessionBridge
    from desktop.pages.dashboard_page import DashboardPage

    bridge = SessionBridge()
    df = pd.DataFrame({
        "product_category": ["Coffee", "Tea", "Bakery"] * 40,
        "transaction_qty": list(range(1, 121)),
        "unit_price": [i * 0.5 for i in range(1, 121)],
    })
    bridge.set_df(df)
    bridge.add_chart("u1", "Sum transaction_qty by product_category",
                     go.Figure(go.Bar(x=["Coffee", "Tea", "Bakery"],
                                      y=[89, 69, 23])),
                     chart_type="categorical", meta={})
    from services.slicer_service import make_slicer
    bridge.set("kpis", [{"icon": "🔢", "label": "Seeded KPI", "value": "123",
                         "prefix": "", "suffix": "", "change_pct": None,
                         "_recipe": {"kpi_type": "Total (Sum)",
                                     "kpi_key": "sum",
                                     "col": "transaction_qty"}}])
    bridge.set("slicers", [
        make_slicer("product_category", widget_kind="list", label="Category"),
        make_slicer("product_category", widget_kind="dropdown",
                    label="Category DD"),
    ])
    # Marker-less legacy grid blob: the regen entry point must run the grid
    # migration + title-band path exactly as on a session reopened now.
    bridge.set("tile_geometry", {"u1": {"x": 0, "y": 1, "w": 2, "h": 1}})

    page = DashboardPage(bridge)
    import desktop.widgets.kpi_dialog as kd
    import desktop.widgets.slicer_dialog as sd
    kpi_exec, slicer_exec = kd.KpiDialog.exec, sd.SlicerDialog.exec
    kd.KpiDialog.exec = lambda self: QDialog.Accepted
    sd.SlicerDialog.exec = lambda self: QDialog.Accepted
    try:
        page._on_add_kpi()
        page._on_add_slicer()
        kpis = bridge.get("kpis", [])
        slicers = bridge.get("slicers", []) or []
        check("KPI card appended to the bridge", len(kpis) == 2, len(kpis))
        check("slicer card appended to the bridge", len(slicers) == 3,
              len(slicers))

        # The shared regen entry point: both buttons funnel through here, so
        # a raise in here is exactly "the button does nothing".
        snap = page._snapshot_inputs()
        check("snapshot carries both KPIs", len(snap["kpis"]) == 2,
              len(snap["kpis"]))
        check("snapshot carries all slicers", len(snap["slicers"]) == 3,
              len(snap["slicers"]))
        check("title band ensured on the snapshot",
              "__title__" in snap["tile_geometry"],
              sorted(snap["tile_geometry"]))

        # The document the canvas would show, built from that snapshot with
        # the same helper chain the workers use.
        from services.filter_service import distinct_values
        from services.export_service import build_export_charts, build_export_report
        export_charts = build_export_charts(
            snap["charts"], meta_map=snap["meta_map"], notes=snap["notes"])
        svals = {}
        for i, s in enumerate(snap["slicers"]):
            if str(s.get("widget") or "") in ("list", "dropdown"):
                try:
                    svals[str(i)] = distinct_values(
                        df, str(s.get("column") or ""))
                except Exception:
                    svals[str(i)] = []
        html, _ = build_export_report(
            export_charts, snap["session_name"], snap["state"],
            orientation=snap["orientation"], kpis=snap["kpis"],
            dashboard_title=snap["dashboard_title"],
            grid_cols_n=snap["grid_cols_n"],
            tile_geometry=snap["tile_geometry"],
            kpi_geometry=snap["kpi_geometry"],
            slicers=snap["slicers"],
            slicer_geometry=snap["slicer_geometry"],
            slicer_values=svals,
            geometry_mode=snap["geometry_mode"],
            inline_plotly=False, editor=True)
        check("report renders the seeded KPI card", 'data-kpi="0"' in html)
        check("report renders the ADDED KPI card", 'data-kpi="1"' in html)
        check("report renders both seeded slicer cards",
              'data-slicer="0"' in html and 'data-slicer="1"' in html)
        check("report renders the ADDED slicer card", 'data-slicer="2"' in html)
        added_label = str(kpis[-1].get("label", "")) if kpis else ""
        check("added KPI label reaches the report",
              bool(added_label) and added_label in html, added_label)
        check("report still carries the title band",
              'data-uid="__title__"' in html)
    finally:
        kd.KpiDialog.exec = kpi_exec
        sd.SlicerDialog.exec = slicer_exec
        page._regen_fast.stop()
        page._regen_slow.stop()


def main():
    for fn in (test_tables_readable, test_show_border_default, test_slicer_static_note, test_slicer_slider_for_dates, test_map_hover_all_aggs, test_worker_cancellation, test_session_bundle, test_datetime_slider_end_to_end, test_title_card_and_timestamp, test_home_banner_compact, test_legend_transparent_default, test_kpi_slicer_add_flow):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            FAIL.append(fn.__name__ + " raised " + str(exc))
    print("")
    print("========== RESULT ==========")
    print("  Passed: %d" % len(PASS))
    print("  Failed: %d" % len(FAIL))
    for f in FAIL:
        print("    - " + str(f))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
