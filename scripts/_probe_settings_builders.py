"""Headless reproduction: build every settings builder's 3 tabs.

Run with the project venv:
    QT_QPA_PLATFORM=offscreen venv/bin/python scripts/_probe_settings_builders.py

Each builder is built in isolation so a failure in one tab of one type
cannot hide the others. The output is the map of what the UI actually
shows: real controls, or the "options go here" placeholder.
"""
from __future__ import annotations

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from desktop.widgets.analysis_settings import get_builder, registered  # noqa: E402

CTX = dict(
    num_cols=["value", "count"],
    cat_cols=["category", "region"],
    dt_cols=["date"],
    palette_names=["Default", "Viridis"],
    all_cols=["value", "count", "category", "region", "date"],
    dim_cols=["category", "region", "date"],
)

failures = []
for aid in registered():
    b = get_builder(aid, None, **CTX)
    if b is None:
        failures.append((aid, "build", "get_builder returned None"))
        continue
    for tab in ("build_generate", "build_layout", "build_typography"):
        try:
            w = getattr(b, tab)()
            if w is None:
                raise RuntimeError("returned None")
        except Exception:
            failures.append((aid, tab, traceback.format_exc().strip().splitlines()[-1]))
            print(f"FAIL {aid}.{tab}: {traceback.format_exc()}")
        else:
            print(f"ok   {aid}.{tab}")

print("\n==== SUMMARY ====")
if not failures:
    print("all builders built cleanly")
for aid, tab, err in failures:
    print(f"  {aid}.{tab}: {err}")
sys.exit(1 if failures else 0)
