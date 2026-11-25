#!/usr/bin/env python3
"""Desktop-side multi-page export harness.

Exercises the pieces of the multi-page feature that live in the Qt layer,
without needing a running app: DashboardPage.__new__ + a fake bridge.

    - _pages_payload: per-page charts/KPIs/slicers/geometry split
    - _snapshot_inputs: charts partition to the active page; unassigned uids
      are backfilled onto the active page and persisted
    - _switch_page: outgoing page's live keys are persisted, incoming page's
      keys are loaded
    - save/restore round-trip via page_state
    - full export through build_export_report with the produced payload:
      tab strip, compound KPI keys, per-page slicer uids, live meta
"""
import os, sys, json, re
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "backend"))

import polars as pl
from PySide6.QtWidgets import QApplication

from desktop.pages import dashboard_page as dp
from services import page_state
from modules.chart_model import make_chart_model

APP = QApplication.instance() or QApplication([])

df = pl.DataFrame({"segment": ["a", "b", "a", "b"], "sales": [10, 20, 30, 40]})

def model(uid, title):
    return make_chart_model(
        uid=uid, chart_type="bar", title=title,
        data={"dimensions": ["segment"], "series": [
            {"id": "s1", "name": "sales", "values": [25, 60]}]},
        recipe={"kind": "sum", "column": "sales", "group_by": "segment"},
        display={})

class FakeBridge:
    def __init__(self):
        self.data = {
            "charts": [("chartA", "A", model("chartA", "A")),
                       ("chartB", "B", model("chartB", "B"))],
            "kpis": [{"label": "TOTAL", "value": "100",
                      "_recipe": {"kpi_type": "sum", "kpi_key": "sum", "col": "sales"}}],
            "slicers": [
                {"uid": "s_global", "column": "segment", "kind": "in",
                 "widget": "dropdown", "scope": "report", "target": "",
                 "label": "segment", "page": "__global__",
                 "spec": {"kind": "in", "column": "segment", "values": []}},
                {"uid": "s_over", "column": "segment", "kind": "in",
                 "widget": "dropdown", "scope": "report", "target": "",
                 "label": "segment", "page": "overview",
                 "spec": {"kind": "in", "column": "segment", "values": []}},
            ],
            "tile_geometry": {
                "__title__": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 64.0},
                "chartA": {"x": 0.0, "y": 80.0, "w": 0.6, "h": 320.0},
                "0": {"x": 0.0, "y": 420.0, "w": 0.2, "h": 120.0},
                "slicer:0": {"x": 0.2, "y": 420.0, "w": 0.2, "h": 140.0},
                "slicer:1": {"x": 0.4, "y": 420.0, "w": 0.2, "h": 140.0},
                "__ly_mode__": "free",
            },
            "pages": [{"id": "overview", "name": "Overview"},
                      {"id": "finance", "name": "Finance"}],
            "active_page": "overview",
            "chart_pages": {"chartA": "overview", "chartB": "finance"},
            "page_geometry": {
                "finance": {
                    "__title__": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 64.0},
                    "chartB": {"x": 0.1, "y": 80.0, "w": 0.5, "h": 300.0},
                    "0": {"x": 0.3, "y": 400.0, "w": 0.25, "h": 110.0},
                    "slicer:0": {"x": 0.6, "y": 400.0, "w": 0.2, "h": 140.0},
                    "__ly_mode__": "free",
                },
            },
            "page_kpis": {"finance": [{"label": "BURN", "value": "-3"}]},
            "page_slicers": {},
            "grid_cols_n": 2,
            "file_name": "harness.csv",
        }
        self._df = df

    # -- SessionBridge surface used by the page code --
    def get(self, key, default=None):
        v = self.data.get(key, default)
        return v
    def set(self, key, value):
        self.data[key] = value
    def pop(self, key, default=None):
        return self.data.pop(key, default)
    @property
    def df(self):
        return self._df
    def polars_dataset(self, *, create_if_missing=True):
        return None
    def get_chart_meta(self, uid):
        return {"uid_meta": uid}
    def get_note(self, uid):
        return f"note-{uid}"

def make_holder(bridge):
    holder = dp.DashboardPage.__new__(dp.DashboardPage)
    holder.bridge = bridge
    holder._regen_token = 0
    holder._slicer_geom = {}
    holder._export_style_dict = lambda: {"ex_accent": "#6163df"}
    holder._current_geometry = lambda charts, kpis, cols=2: (
        dict(bridge.data.get("tile_geometry", {})),
        {k: v for k, v in (bridge.data.get("tile_geometry", {}) or {}).items()
         if str(k).isdigit()})
    holder._persist_geometry = lambda *a, **k: None
    holder._chart_recipe = lambda uid: {"kind": "sum", "column": "sales",
                                        "group_by": "segment"}
    class _SessionName:
        def text(self):
            return "Harness Board"
    class _Combo:
        def currentText(self):
            return "Portrait"
    class _Line:
        def text(self):
            return "Harness Board"
    holder.session_name = _SessionName()
    holder.layout_mode = _Combo()
    holder.dash_title = _Line()
    return holder

ok = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    ok.append(cond)
    print(f"  {status}  {name}" + (f"  [{detail}]" if detail and not cond else ""))

print("[1] _snapshot_inputs partitions charts to the active page")
b = FakeBridge()
h = make_holder(b)
snap = h._snapshot_inputs()
check("snap charts are the overview charts only",
      [c[0] for c in snap["charts"]] == ["chartA"], str([c[0] for c in snap["charts"]]))
check("snap raw_charts has both pages' charts",
      [c[0] for c in snap["raw_charts"]] == ["chartA", "chartB"])
check("snap carries active_page / page_list / chart_pages",
      snap["active_page"] == "overview"
      and [p["id"] for p in snap["page_list"]] == ["overview", "finance"]
      and snap["chart_pages"] == {"chartA": "overview", "chartB": "finance"})

print("[2] unassigned chart uid is backfilled to the active page")
b = FakeBridge()
b.data["chart_pages"] = {"chartA": "overview"}   # chartB unassigned
h = make_holder(b)
snap = h._snapshot_inputs()
check("chartB lands on the active page",
      snap["chart_pages"].get("chartB") == "overview", str(snap["chart_pages"]))
check("backfill persisted to bridge",
      b.data["chart_pages"].get("chartB") == "overview")
check("snap charts now include chartB",
      [c[0] for c in snap["charts"]] == ["chartA", "chartB"])

print("[3] _pages_payload splits everything per page")
b = FakeBridge()
h = make_holder(b)
state = h._page_state()
payload = h._pages_payload(list(b.data["charts"]), state)
check("two pages in payload", payload is not None and len(payload) == 2)
if payload:
    ov, fin = payload
    check("overview gets chartA only",
          [c[0] for c in ov["charts"]] == ["chartA"])
    check("finance gets chartB only",
          [c[0] for c in fin["charts"]] == ["chartB"])
    check("overview KPIs come from the live bridge slice",
          [k["label"] for k in ov["kpis"]] == ["TOTAL"])
    check("finance KPIs come from its page slot",
          [k["label"] for k in fin["kpis"]] == ["BURN"])
    check("both pages carry the global slicer first",
          ov["slicers"][0]["uid"] == "s_global" and fin["slicers"][0]["uid"] == "s_global")
    check("overview also carries its page-local slicer",
          [s["uid"] for s in ov["slicers"]] == ["s_global", "s_over"])
    check("finance has only the global slicer",
          [s["uid"] for s in fin["slicers"]] == ["s_global"])
    # finance slicer_geometry index 0 is the global slicer's slot there;
    # overview has 0 (global) and 1 (local).
    check("overview slicer_geometry keeps bare effective indices",
          set(ov["slicer_geometry"]) == {"0", "1"}, str(ov["slicer_geometry"]))
    check("finance slicer_geometry is its own effective map",
          set(fin["slicer_geometry"]) == {"0"}, str(fin["slicer_geometry"]))
    check("finance chart tile travelled with its page",
          fin["tile_geometry"].get("chartB", {}).get("x") == 0.1)
    check("finance kpi tile travelled with its page",
          fin["kpi_geometry"].get("0", {}).get("y") == 400.0)
    check("finance KPI keys are page-local indices",
          set(fin["kpi_geometry"]) == {"0"}, str(fin["kpi_geometry"]))
    check("slicer_values keyed by effective index",
          set(ov["slicer_values"]) == {"0", "1"}
          and set(fin["slicer_values"]) == {"0"}, str(ov["slicer_values"]))
    check("mode key did not leak into tile geometry",
          "__ly_mode__" not in ov["tile_geometry"]
          and "__ly_mode__" not in fin["tile_geometry"])

print("[4] single-page reports produce no payload (byte-identical exports)")
b = FakeBridge()
b.data["pages"] = [{"id": "overview", "name": "Overview"}]
b.data["active_page"] = "overview"
b.data["chart_pages"] = {"chartA": "overview", "chartB": "overview"}
h = make_holder(b)
payload = h._pages_payload(list(b.data["charts"]), h._page_state())
check("single page -> None", payload is None)

print("[5] _switch_page persists outgoing and loads incoming")
b = FakeBridge()
h = make_holder(b)
# simulate user edits on the overview page: one KPI dragged to y=999, a slicer removed
b.data["kpis"] = [{"label": "TOTAL2", "value": "101"}]
b.data["tile_geometry"] = dict(b.data["tile_geometry"])
b.data["tile_geometry"]["0"] = {"x": 0.0, "y": 999.0, "w": 0.2, "h": 120.0}
h._refresh_page_tabs = lambda: None
h._refresh_kpis = lambda: None
h._refresh_slicer_count = lambda: None
h._schedule_regen = lambda *a, **k: None
h._switch_page("finance")
check("active page is finance", b.data.get("active_page") == "finance")
check("outgoing page KPIs persisted into page_kpis[overview]",
      [k["label"] for k in b.data["page_kpis"]["overview"]] == ["TOTAL2"])
check("outgoing geometry persisted into page_geometry[overview]",
      b.data["page_geometry"]["overview"]["0"]["y"] == 999.0)
check("incoming page KPIs loaded into live bridge",
      [k["label"] for k in b.data["kpis"]] == ["BURN"])
check("finance slicer slot loaded (global only)",
      [s["uid"] for s in b.data["slicers"]] == ["s_global"])
check("finance charts still assigned (no uid drift)",
      b.data["chart_pages"]["chartB"] == "finance")
h._switch_page("overview")
check("switching back restores overview KPIs",
      [k["label"] for k in b.data["kpis"]] == ["TOTAL2"])

print("[6] save/restore round-trip through pages_json")
b = FakeBridge()
state = page_state.assemble_from_bridge(b.get)
blob = page_state.page_state_json(state)
fresh = FakeBridge()
fresh.data["tile_geometry"] = {"__ly_mode__": "free"}
fresh.data["kpis"] = []
fresh.data["slicers"] = []
fresh.data["pages"] = []
page_state.apply_to_bridge(fresh.set, blob)
check("pages survive the round-trip",
      [p["id"] for p in fresh.data["pages"]] == ["overview", "finance"])
check("active page survives", fresh.data["active_page"] == "overview")
check("active page KPIs mirrored into the live keys",
      [k["label"] for k in fresh.data["kpis"]] == ["TOTAL"])
check("active page geometry mirrored into tile_geometry",
      fresh.data["tile_geometry"].get("chartA", {}).get("x") == 0.0)
check("globals mirror into slicers on restore",
      [s["uid"] for s in fresh.data["slicers"]] == ["s_global", "s_over"])

print("[7] full export through build_export_report")
b = FakeBridge()
h = make_holder(b)
payload = h._pages_payload(list(b.data["charts"]), h._page_state())
from services import export_service
html, fname = export_service.build_export_report(
    list(b.data["charts"]), "Harness Board", {"ex_accent": "#6163df"},
    orientation="portrait", dashboard_title="Harness Board", grid_cols_n=2,
    slicers=b.data["slicers"], slicer_values={"0": ["a", "b"], "1": ["a", "b"]},
    report_payload={"columns": ["segment", "sales"],
                    "dtypes": {"segment": "string", "sales": "int64"},
                    "rows": [["a", "b", "a", "b"], [10, 20, 30, 40]],
                    "total_rows": 4},
    pages=payload)
check("tab strip rendered", 'id="ly-tabs"' in html and 'data-ly-page="finance"' in html)
check("finance tab present and named", '>Finance</button>' in html)
check("compound KPI index for finance", 'data-ly-kpi="finance:0"' in html)
check("compound KPI index for overview", 'data-ly-kpi="overview:0"' in html)
check("page-local slicer uid namespaced in tile", 'data-ly-uid="overview:s_over"' in html)
check("global slicer keeps bare uid", 'data-ly-uid="s_global"' in html)
m = re.search(r'<script type="application/json" id="lytrize-live-meta">(.*?)</script>', html, re.S)
meta = json.loads(m.group(1))
check("live meta pages map both charts",
      meta.get("pages") == {"chartA": "overview", "chartB": "finance"},
      json.dumps(meta.get("pages")))
check("live meta KPIs carry page + compound index",
      sorted((k.get("page"), k.get("index")) for k in meta.get("kpis") or []) ==
      [("finance", "finance:0"), ("overview", "overview:0")])
check("live meta slicers: global bare, local namespaced",
      sorted((s["uid"], s.get("page")) for s in meta.get("slicers") or []) ==
      [("overview:s_over", "overview"), ("s_global", "__global__")])
chart_ids = re.findall(r'id="chart-(\d+)"', html)
check("chart DOM ids unique across pages", len(chart_ids) == len(set(chart_ids)) == 2,
      str(chart_ids))
out = os.path.join(REPO, "pages_desktop_out.html")
open(out, "w", encoding="utf-8").write(html)
print(f"  wrote {out} ({len(html)} bytes)")

print()
n = len(ok)
passed = sum(1 for x in ok if x)
print(f"{passed}/{n} checks passed")
sys.exit(0 if passed == n else 1)
