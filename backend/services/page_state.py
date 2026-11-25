"""services/page_state.py -- The multi-page dashboard model.

One dashboard, many pages (a company board pack: Sales / Finance /
Operations...). The state is split deliberately between what every widget
already persists today and one new ``pages_json`` blob on the session row:

* ``pages``         ordered ``[{id, name}]`` -- identity and tab order.
* ``active_page``   which page the in-memory bridge is currently editing.
* ``chart_pages``   ``{chart_uid: page_id}`` -- which page owns each chart.
* ``page_geometry`` ``{page_id: tile_geometry blob}`` -- per-page placement.
                    Each blob uses the page's own LOCAL KPI/slicer indices,
                    exactly the format the single-page canvas always wrote.
* ``page_kpis``     ``{page_id: [kpi dict, ...]}`` -- KPI lists per page.
* ``page_slicers``  ``{page_id: [local slicer dict, ...],
                       "__global__": [report-wide slicers]}`` -- slicer
                    dicts carry ``page`` (the owning page, or ``"__global__"``).

Design rule that keeps this safe: the bridge always holds the ACTIVE page in
its legacy keys (``tile_geometry``, ``kpis``, ``slicers``) so every existing
widget, handler and export path keeps working unchanged. ``assemble()``
recomputes the persisted maps from the bridge at save/switch time, so the
maps are never the source of drift -- the bridge is.

Legacy sessions have no ``pages_json`` at all. Every ``normalize_*`` helper
falls back to a single "Overview" page carrying the entire current state,
which is byte-for-byte the pre-pages behaviour.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

PAGES_VERSION = 1
DEFAULT_PAGE_ID = "overview"
DEFAULT_PAGE_NAME = "Overview"
GLOBAL_SLICER_KEY = "__global__"

MAX_PAGE_NAME = 80


def new_page_id() -> str:
    return "p" + uuid.uuid4().hex[:10]


def _clean_name(value) -> str:
    name = str(value or "").strip()
    return (name or DEFAULT_PAGE_NAME)[:MAX_PAGE_NAME]


def normalize_pages(raw) -> list[dict]:
    """Validate the ordered page list; always returns at least one page."""
    pages: list[dict] = []
    seen = set()
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("id") or "").strip()
            # Legacy "__global__" is a reserved key, never a page.
            if not pid or pid == GLOBAL_SLICER_KEY:
                pid = new_page_id()
            if pid in seen:
                pid = new_page_id()
            seen.add(pid)
            pages.append({"id": pid, "name": _clean_name(entry.get("name"))})
    if not pages:
        pages = [{"id": DEFAULT_PAGE_ID, "name": DEFAULT_PAGE_NAME}]
    return pages


def _valid_page_ids(pages: list[dict]) -> set[str]:
    return {p["id"] for p in pages}


def normalize_page_state(raw) -> dict:
    """Validate/repair a persisted ``pages_json`` blob.

    Tolerates anything: missing keys, wrong types, charts pointing at dead
    pages. Whatever cannot be attributed to a page lands on the first page,
    so a torn write never loses objects.
    """
    raw = raw or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}

    pages = normalize_pages(raw.get("pages"))
    ids = _valid_page_ids(pages)
    first = pages[0]["id"]

    active = str(raw.get("active") or first)
    if active not in ids:
        active = first

    chart_pages = {}
    raw_cp = raw.get("chart_pages")
    if isinstance(raw_cp, dict):
        for uid, pid in raw_cp.items():
            pid = str(pid)
            chart_pages[str(uid)] = pid if pid in ids else first

    def _geom(raw_map):
        out = {}
        if isinstance(raw_map, dict):
            for pid, blob in raw_map.items():
                pid = str(pid)
                if pid in ids and isinstance(blob, dict):
                    out[pid] = dict(blob)
        return out

    def _list_map(raw_map, key=None):
        out = {}
        if isinstance(raw_map, dict):
            for pid, items in raw_map.items():
                pid = str(pid)
                if pid not in ids and pid != GLOBAL_SLICER_KEY:
                    continue
                if not isinstance(items, list):
                    continue
                out[pid] = [dict(i) for i in items if isinstance(i, dict)]
        return out

    page_geometry = _geom(raw.get("page_geometry"))
    page_kpis = _list_map(raw.get("page_kpis"))
    page_slicers = _list_map(raw.get("page_slicers"))
    for pid in [p["id"] for p in pages]:
        page_geometry.setdefault(pid, {})
        page_kpis.setdefault(pid, [])
        page_slicers.setdefault(pid, [])
    page_slicers.setdefault(GLOBAL_SLICER_KEY, [])

    return {
        "version": PAGES_VERSION,
        "active": active,
        "pages": pages,
        "chart_pages": chart_pages,
        "page_geometry": page_geometry,
        "page_kpis": page_kpis,
        "page_slicers": page_slicers,
    }


def assemble_from_bridge(get) -> dict:
    """Recompute the full page state from live bridge values.

    ``get`` is ``bridge.get``. The active page's slices are always taken from
    the live bridge keys -- the persisted maps may be stale everywhere else.
    """
    pages = normalize_pages(get("pages"))
    ids = _valid_page_ids(pages)
    first = pages[0]["id"]
    active = str(get("active_page") or first)
    if active not in ids:
        active = first

    chart_pages = {}
    raw_cp = get("chart_pages")
    if isinstance(raw_cp, dict):
        for uid, pid in raw_cp.items():
            if uid:
                chart_pages[str(uid)] = str(pid) if str(pid) in ids else first

    page_geometry = {}
    raw_geom = get("page_geometry")
    if isinstance(raw_geom, dict):
        for pid, blob in raw_geom.items():
            if str(pid) in ids and isinstance(blob, dict):
                page_geometry[str(pid)] = dict(blob)
    page_geometry[active] = dict(get("tile_geometry") or {})

    page_kpis = {}
    raw_kpis = get("page_kpis")
    if isinstance(raw_kpis, dict):
        for pid, items in raw_kpis.items():
            if str(pid) in ids and isinstance(items, list):
                page_kpis[str(pid)] = [dict(i) for i in items if isinstance(i, dict)]
    page_kpis[active] = [dict(k) for k in (get("kpis") or []) if isinstance(k, dict)]

    page_slicers = {}
    raw_slicers = get("page_slicers")
    if isinstance(raw_slicers, dict):
        for pid, items in raw_slicers.items():
            pid = str(pid)
            if (pid in ids or pid == GLOBAL_SLICER_KEY) and isinstance(items, list):
                page_slicers[pid] = [dict(i) for i in items if isinstance(i, dict)]
    live = [dict(s) for s in (get("slicers") or []) if isinstance(s, dict)]
    page_slicers[GLOBAL_SLICER_KEY] = [s for s in live if slicer_page_id(s) is None]
    active_locals = [s for s in live if slicer_page_id(s) == active]
    adopted = []
    for s in live:
        pid = slicer_page_id(s)
        if pid and pid != active and pid not in ids:
            # Adopted by the active page rather than dropped on the floor.
            s["page"] = active
            adopted.append(s)
    page_slicers[active] = active_locals + adopted

    return {
        "version": PAGES_VERSION,
        "active": active,
        "pages": pages,
        "chart_pages": chart_pages,
        "page_geometry": page_geometry,
        "page_kpis": page_kpis,
        "page_slicers": page_slicers,
    }


def page_state_json(state: dict) -> str:
    """Serialise the page state for pages_json (bounded, single-line)."""
    st = normalize_page_state(state)
    return json.dumps(st, ensure_ascii=False, sort_keys=False,
                      separators=(",", ":"), allow_nan=False)


def _informationless(st: dict) -> bool:
    """True when the persisted state carries no page organisation at all
    (a legacy single-Overview default produced from an absent/empty blob).

    In that case the live bridge keys are the only surviving description of
    the layout, so the mirror step must NOT blank them.
    """
    pages = st["pages"]
    if len(pages) != 1 or st["active"] != pages[0]["id"]:
        return False
    pid = pages[0]["id"]
    if st["chart_pages"]:
        return False
    if st["page_geometry"].get(pid):
        return False
    for items in st["page_kpis"].values():
        if items:
            return False
    for items in st["page_slicers"].values():
        if items:
            return False
    return True


def apply_to_bridge(set_fn, state_raw) -> dict:
    """Push a persisted page state into the live bridge.

    ``set_fn`` is ``bridge.set``. The mirrored maps are written first so a
    reader that snapshots mid-restore never observes half-applied pages.

    A legacy blob with no page organisation at all (the default single
    Overview) never wipes the live ``tile_geometry``/``kpis``/``slicers`` --
    it only installs the page bookkeeping around them. Real multi-page (or
    real single-page, post-pages) blobs apply fully.
    """
    st = normalize_page_state(state_raw)
    if _informationless(st):
        set_fn("pages", st["pages"])
        set_fn("active_page", st["active"])
        set_fn("chart_pages", st["chart_pages"])
        set_fn("page_geometry", st["page_geometry"])
        set_fn("page_kpis", st["page_kpis"])
        set_fn("page_slicers", st["page_slicers"])
        return st
    pid = st["active"]
    set_fn("page_geometry", st["page_geometry"])
    set_fn("page_kpis", st["page_kpis"])
    set_fn("page_slicers", st["page_slicers"])
    set_fn("pages", st["pages"])
    set_fn("active_page", st["active"])
    set_fn("chart_pages", st["chart_pages"])
    set_fn("tile_geometry", st["page_geometry"].get(pid, {}))
    set_fn("kpis", st["page_kpis"].get(pid, []))
    set_fn("slicers", list(st["page_slicers"].get(GLOBAL_SLICER_KEY, []))
                     + list(st["page_slicers"].get(pid, [])))
    return st


def _is_global(slicer: dict) -> bool:
    page = slicer.get("page")
    return not page or page == GLOBAL_SLICER_KEY


def slicer_page_id(slicer: dict) -> Optional[str]:
    """The page a slicer belongs to, or None for report-wide."""
    page = slicer.get("page")
    if not page or page == GLOBAL_SLICER_KEY:
        return None
    return str(page)


def with_page_assignment(charts, chart_pages, first_page_id):
    """Partition charts by page, unassigned ones land on the first page."""
    pages_map: dict[str, list] = {}
    for chart in charts or []:
        uid = chart[0] if chart else None
        pid = (chart_pages or {}).get(str(uid)) if uid else first_page_id
        pages_map.setdefault(pid or first_page_id, []).append(chart)
    return pages_map
