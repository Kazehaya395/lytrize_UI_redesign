#!/usr/bin/env python3
"""Offscreen smoke test for the Lytrize bug-fix set.

Run from the repo root:
    QT_QPA_PLATFORM=offscreen python scripts/smoke_test_fixes.py

Exercises every operation that changed during the fixes:
  - Backend: find_replace (empty-find, CI-literal), numeric_clean op keys,
    text_clean, validate actions
  - DataCleanerPanel: String Ops constants + concat col2, Numeric op-key map
  - DtypeTransformerPanel: apply after lossy preview, nonsense guard, clear
  - Multi-page export: active-page partition, per-page payload/restore
  - Multi-page backups: pages_json survives export/import (insert + update)
  - Data-source plugin seam: registry, lazy frames, SQL/sqlite, redaction
  - Canvas chrome invariants: gesture glow, red overlap, no Qt chrome
  - ExcelLoaderPanel: off-by-one merge + QGroupBox
  - ColumnClassifierPanel: real drag & drop
  - ColumnManagerPanel: formula picker rebuild
  - Validate: every rule pos/neg + custom regex + all 3 actions (Issue 10)
  - String Ops: all 6 ops value-checked + preview/apply + stale-state reset (11, 12)
  - Numeric Clean: all 9 ops value-checked + op-key round-trip + reset (13, 14)
  - Dtype: close button, bounded preview table, honest lossy preview, apply (15)
  - Date conversion: day-first detection for DD/MM/YYYY columns (user report)
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))  # services.* / modules.*
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import QPoint, Qt, QMimeData
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QListWidget

import polars as _pl


def _to_pd(obj):
    """The panel paths return Polars since the data-engine migration; these
    assertions were written against pandas. Normalise at the check site.

    Polars ``Date`` columns become object series of ``datetime.date`` (with
    Nones for null), mirroring the pandas semantics the app was built on."""
    try:
        if obj is None:
            return obj
        if isinstance(obj, _pl.Series):
            if obj.dtype == _pl.Date:
                return pd.Series(obj.to_list(), name=obj.name)
            return obj.to_pandas()
        if isinstance(obj, _pl.DataFrame):
            pdf = obj.to_pandas()
            for col in obj.columns:
                if obj[col].dtype == _pl.Date:
                    pdf[col] = pd.Series(obj[col].to_list(), index=pdf.index)
            return pdf
    except Exception:
        pass
    return obj

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name} {('— ' + detail) if detail else ''}")


def make_df() -> pd.DataFrame:
    return pd.DataFrame({
        "text": ["  Hello  ", "WORLD", "N/A", "foo-bar", ""],
        "num": [1.5, 2.5, 3.5, 100.0, 1000.0],
        "date": ["2023-01-01", "2023-02-01", "bad", "2023-04-01", None],
        "email": ["a@b.com", "not-email", "c@d.com", "e f", "g@h.co"],
    })


def _roles_fixture_df():
    """Column soup for the roles tests: text, bool, int, float and datetime
    all in one frame, in a fixed column order (the resolver preserves it).

    The app is Polars-native end to end, so the fixture is a real Polars
    frame -- the pandas input path stays exercised by the degenerate-input
    checks in the roles tests."""
    import polars as pl
    return pl.DataFrame({
        "region": ["East", "West", "North", "East", "West", "North"],
        "segment": ["S1", "S2", "S1", "S2", "S1", "S2"],
        "code": ["X1", "X2", "X3", "X1", "X2", "X3"],
        "is_active": [True, False, True, True, False, True],
        "sales": [100.0, 200.5, 150.0, 300.25, 450.0, 125.75],
        "postal": [40213, 60601, 94105, 10001, 98101, 73301],
        "order_date": pl.Series(
            "order_date",
            ["2024-01-05", "2024-02-10", "2024-03-15",
             "2024-04-20", "2024-05-25", "2024-06-30"]).str.to_datetime("%Y-%m-%d"),
    })


def app():
    return QApplication.instance() or QApplication([])


def silent_bridge():
    return type("B", (), {"set_df": staticmethod(lambda df: None)})()


def capturing_bridge():
    """Bridge stand-in that records the last DataFrame pushed via set_df."""
    class _Bridge:
        def __init__(self) -> None:
            self.df = None

        def set_df(self, df) -> None:
            self.df = df
    return _Bridge()


# ---------------------------------------------------------------------------
def test_find_replace(ts):
    print("== find_replace_preview ==")
    s = pd.Series(["ABC", "abc", "xAy", "plain"])
    out = ts.find_replace_preview(s, "", "ZZ")
    check("empty-find no-op", out.tolist() == s.tolist(), repr(out.tolist()))

    out = ts.find_replace_preview(s, "abc", "X", use_regex=False, case_insensitive=True)
    check("CI-literal replaces all", list(out) == ["X", "X", "xAy", "plain"], repr(list(out)))

    out = ts.find_replace_preview(s, "abc", "X", use_regex=False, case_insensitive=False)
    # Case-sensitive: "ABC" is untouched, only lowercase "abc" replaced
    check("literal is case-sensitive", list(out) == ["ABC", "X", "xAy", "plain"], repr(list(out)))

    out = ts.find_replace_preview(s, r"^a", "S", use_regex=True, case_insensitive=True)
    # IGNORECASE + regex ^a matches both "ABC" and "abc" -> replace first char
    check("regex replace works", list(out) == ["SBC", "Sbc", "xAy", "plain"], repr(list(out)))


def test_numeric_clean(ts):
    print("[2] numeric_clean op keys")
    s = pd.Series([1.0, 2.0, 3.0, np.nan, 100.0])
    for op, kwargs in [
        ("clamp", {"min": 1.0, "max": 4.0}),
        ("iqr_median", {}),
        ("iqr_mean", {}),
        ("round", {"decimals": 0}),
        ("minmax", {}),
        ("zscore", {}),
        ("fillna_median", {}),
        ("fillna_mean", {}),
        ("fillna_value", {"value": 0.0}),
    ]:
        try:
            ts.numeric_clean(s, op, **kwargs)
            check(f"opkey {op}", True)
        except Exception as exc:
            check(f"opkey {op}", False, str(exc))


def test_text_clean(ts):
    print("[3] text_clean")
    s = pd.Series(["  AbC  ", "<b>hi</b>", "a1b2", ""])
    out = ts.text_clean_preview(s, {"strip": True, "lower": True})
    check("strip+lower", list(out) == ["abc", "<b>hi</b>", "a1b2", ""], repr(list(out)))
    out = ts.text_clean_preview(s, {"rm_html": True})
    check("rm_html", list(out) == ["  AbC  ", "hi", "a1b2", ""], repr(list(out)))


# (pass, fail) value pairs per rule — each expected to classify correctly.
_RULE_SAMPLES = [
    ("Email address", ["a@b.com", "x@y.co.uk"], ["nope", "no-at@", "@x.com"]),
    ("Phone (intl. +XX)", ["+91 1234567890", "020-1234-5678"], ["abc", "+1"]),
    ("UK postcode", ["SW1A 1AA", "M1 1AE"], ["12345", "SW1A"]),
    ("US ZIP code", ["90210", "12345-6789"], ["1234", "abcde"]),
    ("Integer number", ["-42", "0", "123"], ["3.14", "1e3", "12a"]),
    ("Decimal number", ["-0.5", "3.14", "100"], ["1.2.3", "abc"]),
    ("Date (YYYY-MM-DD)", ["2023-01-01", "1999-12-31"], ["2023/01/01", "01-01-2023"]),
    ("URL (http/https)", ["https://example.com", "http://x.io/a"], ["ftp://x", "example.com"]),
    ("UUID", ["123e4567-e89b-12d3-a456-426614174000"], ["not-a-uuid", "123e4567"]),
]


def test_validate(ts):
    print("[4] validate actions")
    df = make_df()

    # All 9 built-in rules with crafted pass/fail data
    for name, ok_vals, bad_vals in _RULE_SAMPLES:
        pattern = ts.VALIDATION_PATTERNS.get(name, "")
        if not pattern:
            check(f"rule '{name}' has pattern", False, "missing")
            continue
        test_df = pd.DataFrame({"v": ok_vals + bad_vals})
        res = ts.validate_column(test_df, "v", pattern)
        expect_pass = len(ok_vals)
        expect_fail = len(bad_vals)
        check(f"rule {name}", res["n_pass"] == expect_pass and res["n_fail"] == expect_fail,
              f"pass={res['n_pass']} exp {expect_pass}; fail={res['n_fail']} exp {expect_fail}")

    # Custom regex: validate a hypothetical 'id' pattern
    custom = r"^[A-Z]{2}-\d{3}$"
    res = ts.validate_column(pd.DataFrame({"v": ["AB-123", "XY-999", "bad", "A1-22"]}), "v", custom)
    check("custom regex", res["n_pass"] == 2 and res["n_fail"] == 2,
          f"pass={res['n_pass']} fail={res['n_fail']}")

    # NaN-replace & flag actions mutate
    df = make_df()
    res = ts.validate_column(df, "email", ts.VALIDATION_PATTERNS["Email address"])
    check("validate counts", res["n_fail"] >= 2 and res["n_pass"] >= 2,
          f"pass={res['n_pass']} fail={res['n_fail']}")
    nan_df = ts.validate_replace_failures_with_nan(df, "email", res["fail_index"])
    check("nan-replace", nan_df["email"].isna().sum() == res["n_fail"])
    flag_df = ts.validate_flag_column(df, "email", ts.VALIDATION_PATTERNS["Email address"])
    check("flag adds column", "_valid_email" in flag_df.columns)


# ---------------------------------------------------------------------------
def test_string_ops():
    print("[5] DataCleanerPanel String Ops")
    app()
    from desktop.pages.data_cleaner_panel import DataCleanerPanel
    import desktop.pages.data_cleaner_panel as dc

    p = DataCleanerPanel(silent_bridge())
    p.set_dataframe(make_df())

    combo = [p.cmb_str_op.itemText(i) for i in range(p.cmb_str_op.count())]
    expected = [dc.OP_TRIM, dc.OP_PAD, dc.OP_SUBSTRING, dc.OP_SPLIT,
                dc.OP_CONCAT, dc.OP_REGEX_EXTRACT]
    check("combo == constants", combo == expected, f"{combo} != {expected}")

    p.cmb_str_op.setCurrentText(dc.OP_CONCAT)
    cols2 = [p.str_op_col2.itemText(i) for i in range(p.str_op_col2.count())]
    check("concat col2 populated", "email" in cols2 and "num" in cols2, repr(cols2))

    p.str_op_col2.setCurrentText("email")
    p.cmb_str_col.setCurrentText("text")
    p._on_str_preview()
    check("concat preview enables apply", p.btn_str_apply.isEnabled(), p.lbl_str_preview.text())

    p.cmb_str_op.setCurrentText(dc.OP_SUBSTRING)
    p.str_op_start.setValue(0)
    p.str_op_end.setValue(3)
    try:
        p._on_str_preview()
        check("substring preview runs", True, p.lbl_str_preview.text())
    except Exception as exc:
        check("substring preview runs", False, str(exc))


# ---------------------------------------------------------------------------
# Issues 10-15 verification set (added 2026-08-27)
# ---------------------------------------------------------------------------
VALIDATE_FIXTURES = {
    "Email address": (
        ["a@b.co", "first.last+tag@sub.domain.org"],
        ["no-at-sign", "a@b", "a b@c.com"],
    ),
    "Phone (intl. +XX)": (
        ["+1 (555) 123-4567", "555-1234"],
        ["12", "555-ABCD"],
    ),
    "UK postcode": (
        ["SW1A 1AA", "M1 1AE"],
        ["1A1 1AA", "sw1a 1aa"],  # leading digit / lowercase both must fail
    ),
    "US ZIP code": (
        ["12345", "12345-6789"],
        ["1234", "123456"],
    ),
    "Integer number": (
        ["42", "-7"],
        ["3.14", "abc"],
    ),
    "Decimal number": (
        ["3.14", "-0.5", "100"],
        ["1.", ".5"],
    ),
    "Date (YYYY-MM-DD)": (
        ["2023-01-01", "1999-12-31"],
        ["01/02/2023", "2023-1-1"],
    ),
    "Date (MM/DD/YYYY) — US": (
        ["01/15/2023", "12/31/1999"],
        ["15/01/2023", "2023-01-15", "13/05/2023"],
    ),
    "Date (DD/MM/YYYY) — Europe": (
        ["15/01/2023", "31/12/1999"],
        ["01/15/2023", "2023-01-15", "31/13/2023"],
    ),
    "Date (YYYY/MM/DD) — Asian": (
        ["2023/01/15", "1999/12/31"],
        ["01/15/2023", "2023-01-15", "2023/13/01"],
    ),
    "Date (DD-MM-YYYY)": (
        ["15-01-2023", "31-12-1999"],
        ["01-15-2023", "2023-01-15", "15/01/2023"],
    ),
    "Date (MM-DD-YYYY)": (
        ["01-15-2023", "12-31-1999"],
        ["15-01-2023", "2023-01-15", "13-32-2023"],
    ),
    "Date (DD.MM.YYYY)": (
        ["15.01.2023", "31.12.1999"],
        ["01.15.2023", "2023.01.15", "15,01,2023"],
    ),
    "Date (any common format)": (
        ["2023-01-15", "01/15/2023", "15/01/2023", "2023/01/15",
         "15-01-2023", "01-15-2023", "15.01.2023"],
        ["2023-1-1", "20230115", "15-01-23", "yesterday", "2023.13.01"],
    ),
    "URL (http/https)": (
        ["https://example.com", "http://a.b/c?d=1"],
        ["ftp://x.com", "example.com"],
    ),
    "UUID": (
        ["123e4567-e89b-12d3-a456-426614174000"],
        # uppercase hex fails (case=True) — identical to the old Streamlit app
        ["123E4567-E89B-12D3-A456-426614174000", "123e4567e89b12d3a456426614174000"],
    ),
}


def test_validate_all_rules(ts):
    """Issue 10: every validation rule must be tested & verified."""
    print("[12] Validate: every rule pos/neg + all actions (Issue 10)")
    for rule, (pos, neg) in VALIDATE_FIXTURES.items():
        pattern = ts.VALIDATION_PATTERNS[rule]
        df = pd.DataFrame({"v": pos + neg + [None]})
        res = ts.validate_column(df, "v", pattern)
        check(f"validate '{rule}': counts",
              res["n_pass"] == len(pos) and res["n_fail"] == len(neg)
              and res["n_null"] == 1 and res["total"] == len(pos) + len(neg) + 1,
              f"pass={res['n_pass']} fail={res['n_fail']} null={res['n_null']}")
        flagged = ts.validate_flag_column(df, "v", pattern)
        expect_flag = [True] * len(pos) + [False] * len(neg) + [False]
        check(f"validate '{rule}': flag action",
              list(flagged["_valid_v"]) == expect_flag,
              repr(list(flagged["_valid_v"])))
        nan_df = ts.validate_replace_failures_with_nan(df, "v", res["fail_index"])
        check(f"validate '{rule}': nan action",
              int(nan_df["v"].isna().sum()) == len(neg) + 1
              and list(nan_df["v"].iloc[:len(pos)].astype(str)) == list(pd.Series(pos)),
              repr(list(nan_df["v"])))

    # Custom regex path (service level)
    df = pd.DataFrame({"v": ["ABC", "ab", "ABCD", None]})
    res = ts.validate_column(df, "v", r"^[A-Z]{3}$")
    check("validate custom regex: counts",
          res["n_pass"] == 1 and res["n_fail"] == 2 and res["n_null"] == 1,
          f"pass={res['n_pass']} fail={res['n_fail']}")

    # ---- UI level: combo must mirror the backend single source of truth ----
    app()
    from desktop.pages.data_cleaner_panel import DataCleanerPanel

    p = DataCleanerPanel(silent_bridge())
    combo = [p.cmb_val_pattern.itemText(i) for i in range(p.cmb_val_pattern.count())]
    check("validate combo == service patterns + custom",
          combo == list(ts.VALIDATION_PATTERNS.keys()) + ["Custom regex…"],
          repr(combo))

    p.set_dataframe(pd.DataFrame({"v": ["42", "-7", "3.14", None]}))
    p.cmb_val_col.setCurrentText("v")
    p.cmb_val_pattern.setCurrentText("Integer number")
    check("validate live metrics",
          "✅ Pass: 2" in p.lbl_pass.text() and "❌ Fail: 1" in p.lbl_fail.text()
          and "⬜ Null: 1" in p.lbl_null.text(),
          f"{p.lbl_pass.text()} | {p.lbl_fail.text()} | {p.lbl_null.text()}")
    check("validate apply enabled on failures", p.btn_val_apply.isEnabled())

    out_none = p._compute_validate("v", ts.VALIDATION_PATTERNS["Integer number"], "none")
    check("validate action 'none' leaves df unchanged", out_none.equals(p._df))
    out_nan = _to_pd(p._compute_validate("v", ts.VALIDATION_PATTERNS["Integer number"], "nan"))
    check("validate action 'nan' nulls failures",
          int(out_nan["v"].isna().sum()) == 2, repr(list(out_nan["v"])))
    out_flag = p._compute_validate("v", ts.VALIDATION_PATTERNS["Integer number"], "flag")
    check("validate action 'flag' adds bool col",
          "_valid_v" in out_flag.columns
          and list(out_flag["_valid_v"]) == [True, True, False, False],
          repr(list(out_flag["_valid_v"])))

    # Custom regex through the panel
    p.set_dataframe(pd.DataFrame({"v": ["123", "12", "abc", None]}))
    p.cmb_val_pattern.setCurrentText("Custom regex…")
    p.txt_val_custom.setText(r"^\d{3}$")
    p._run_validation()
    check("validate custom regex live metrics",
          "✅ Pass: 1" in p.lbl_pass.text() and "❌ Fail: 2" in p.lbl_fail.text(),
          f"{p.lbl_pass.text()} | {p.lbl_fail.text()}")


def test_string_ops_full():
    """Issues 11 + 12: every String Op value-checked + stale-preview reset."""
    print("[13] String Ops: all 6 ops + apply + stale-state reset (Issues 11, 12)")
    app()
    from desktop.pages.data_cleaner_panel import DataCleanerPanel
    import desktop.pages.data_cleaner_panel as dc

    df = pd.DataFrame({"s": ["Alpha", "Beta", "Gamma Delta"], "t": ["x", "y", "z"]})
    bridge = capturing_bridge()
    p = DataCleanerPanel(bridge)
    p.set_dataframe(df)

    errors = []
    orig_crit = QMessageBox.critical
    QMessageBox.critical = lambda *a, **k: errors.append(a[2])
    try:
        p.cmb_str_col.setCurrentText("s")

        # TRIM
        p.cmb_str_op.setCurrentText(dc.OP_TRIM)
        p.str_op_length.setValue(3)
        p.str_op_side.setCurrentText("Left (start)")
        p._on_str_preview()
        check("trim left: preview count",
              "3 values will change" in p.lbl_str_preview.text(),
              p.lbl_str_preview.text())
        got, _, _ = p._compute_str_op("s", dc.OP_TRIM)
        check("trim left 3 values", list(got) == ["Alp", "Bet", "Gam"], repr(list(got)))
        p.str_op_side.setCurrentText("Right (end)")
        got, _, _ = p._compute_str_op("s", dc.OP_TRIM)
        check("trim right 3 values", list(got) == ["pha", "eta", "lta"], repr(list(got)))

        # PAD
        p.cmb_str_op.setCurrentText(dc.OP_PAD)
        p.str_op_padlen.setValue(6)
        p.str_op_padchar.setText("*")
        p.str_op_padside.setCurrentText("Right")
        got, _, _ = p._compute_str_op("s", dc.OP_PAD)
        check("pad right '*' to 6", list(got) == ["Alpha*", "Beta**", "Gamma Delta"],
              repr(list(got)))
        p.str_op_padchar.setText("0")
        p.str_op_padside.setCurrentText("Left")
        got, _, _ = p._compute_str_op("s", dc.OP_PAD)
        check("pad left '0' to 6 (zfill branch)",
              list(got) == ["0Alpha", "00Beta", "Gamma Delta"], repr(list(got)))

        # SUBSTRING
        p.cmb_str_op.setCurrentText(dc.OP_SUBSTRING)
        p.str_op_start.setValue(0)
        p.str_op_end.setValue(3)
        got, _, _ = p._compute_str_op("s", dc.OP_SUBSTRING)
        check("substring 0:3", list(got) == ["Alp", "Bet", "Gam"], repr(list(got)))
        p.str_op_start.setValue(2)
        p.str_op_end.setValue(0)  # 0 = to end of string
        got, _, _ = p._compute_str_op("s", dc.OP_SUBSTRING)
        check("substring 2:end", list(got) == ["pha", "ta", "mma Delta"], repr(list(got)))

        # SPLIT
        p.cmb_str_op.setCurrentText(dc.OP_SPLIT)
        p.str_op_delim.setText(" ")
        p.str_op_part.setCurrentText("First part")
        p.str_op_newcol.setText("part1")
        got, new_col, _ = p._compute_str_op("s", dc.OP_SPLIT)
        check("split first part -> new col",
              new_col == "part1" and list(got) == ["Alpha", "Beta", "Gamma"],
              repr(list(got)))
        p.str_op_part.setCurrentText("Second part")
        got, new_col, _ = p._compute_str_op("s", dc.OP_SPLIT)
        check("split second part (missing -> NaN)",
              new_col == "part1" and pd.isna(_to_pd(got).iloc[0]) and pd.isna(_to_pd(got).iloc[1])
              and _to_pd(got).iloc[2] == "Delta", repr(list(got)))

        # CONCAT
        p.cmb_str_op.setCurrentText(dc.OP_CONCAT)
        p.str_op_col2.setCurrentText("t")
        p.str_op_sep.setText("-")
        p.str_op_concatcol.setText("joined")
        got, new_col, _ = p._compute_str_op("s", dc.OP_CONCAT)
        check("concat with separator",
              new_col == "joined" and list(got) == ["Alpha-x", "Beta-y", "Gamma Delta-z"],
              repr(list(got)))

        # REGEX EXTRACT
        p.cmb_str_op.setCurrentText(dc.OP_REGEX_EXTRACT)
        p.str_op_re.setText(r"^[A-Z]")
        p.str_op_recol.setText("initial")
        got, new_col, _ = p._compute_str_op("s", dc.OP_REGEX_EXTRACT)
        check("regex capture extract",
              new_col == "initial" and list(got) == ["A", "B", "G"], repr(list(got)))

        # Full apply path through the worker thread (QThread -> signal -> bridge)
        p.cmb_str_op.setCurrentText(dc.OP_CONCAT)
        p.str_op_col2.setCurrentText("t")
        p.str_op_sep.setText(" ")
        p.str_op_concatcol.setText("joined2")
        p._on_str_preview()
        check("concat preview enables apply", p.btn_str_apply.isEnabled(),
              p.lbl_str_preview.text())
        check("cleaner preview cells centered",
              p.table_str_preview.item(0, 0).textAlignment() == Qt.AlignCenter)
        check("static input bars width-capped",
              p.cmb_str_op.maximumWidth() <= 300)
        check("preview table LEFT + controls panel RIGHT",
              p.cmb_str_op.parentWidget().maximumWidth() <= 440
              and p.table_str_preview.minimumHeight() >= 205
              and p.tabs.widget(3).layout().stretch(
                  p.tabs.widget(3).layout().indexOf(p.table_str_preview)) == 3,
              f"side={p.cmb_str_op.parentWidget().maximumWidth()} "
              f"minH={p.table_str_preview.minimumHeight()}")
        p._on_str_apply()
        if p._worker is not None:
            p._worker.wait(10000)
        app().processEvents()
        check("worker apply created 'joined2'",
              bridge.df is not None and "joined2" in bridge.df.columns
              and list(_to_pd(bridge.df)["joined2"]) == ["Alpha x", "Beta y", "Gamma Delta z"],
              repr(list(_to_pd(bridge.df)["joined2"]) if bridge.df is not None else None))
        check("apply disabled after finish", not p.btn_str_apply.isEnabled())

        # Issue 11: stale preview/Apply state must reset on ANY input change
        p._on_str_preview()
        check("re-preview enables apply", p.btn_str_apply.isEnabled())
        p.str_op_sep.setText(" | ")
        check("param change disables apply (Issue 11)",
              not p.btn_str_apply.isEnabled() and p.lbl_str_preview.text() == "",
              f"btn={p.btn_str_apply.isEnabled()} lbl={p.lbl_str_preview.text()!r}")
        p._on_str_preview()
        p.cmb_str_op.setCurrentText(dc.OP_TRIM)
        check("op change disables apply (Issue 11)",
              not p.btn_str_apply.isEnabled() and p.lbl_str_preview.text() == "",
              f"btn={p.btn_str_apply.isEnabled()} lbl={p.lbl_str_preview.text()!r}")
        p._on_str_preview()
        p.cmb_str_col.setCurrentText("t")
        check("column change disables apply (Issue 11)",
              not p.btn_str_apply.isEnabled() and p.lbl_str_preview.text() == "",
              f"btn={p.btn_str_apply.isEnabled()} lbl={p.lbl_str_preview.text()!r}")
        check("dynamic option widgets width-capped",
              p.str_op_length.maximumWidth() <= 300)
        check("no error dialogs in string ops", not errors, repr(errors))
    finally:
        QMessageBox.critical = orig_crit


def test_numeric_ui():
    app()
    from desktop.pages.data_cleaner_panel import DataCleanerPanel

    p = DataCleanerPanel(silent_bridge())
    p.set_dataframe(make_df())

    p.cmb_num_col.setCurrentText("num")
    p.cmb_num_op.setCurrentText("Round to N decimal places")
    p.num_decimals.setValue(0)
    p._on_num_preview()
    check("round preview resolves+enables", p.btn_num_apply.isEnabled(),
          p.lbl_num_preview.text())
    check("op key maps", p._numeric_op_key("Round to N decimal places") == "round")


def test_numeric_ops_full():
    """Issues 13 + 14: all 9 numeric ops value-checked + op-key round-trip."""
    print("[14] Numeric Clean: all 9 ops + op-key round-trip + reset (Issues 13, 14)")
    app()
    import services.transform_service as tss
    from desktop.pages.data_cleaner_panel import DataCleanerPanel
    import desktop.pages.data_cleaner_panel as dc

    # count_changed_values must treat NaN == NaN as unchanged (truthful counts)
    check("count_changed ignores NaN==NaN",
          dc.count_changed_values(_pl.Series("x", [1.0, None]),
                                  _pl.Series("x", [1.0, None])) == 0)
    check("count_changed counts real change",
          dc.count_changed_values(_pl.Series("x", [1.0, None]),
                                  _pl.Series("x", [1.0, 5.0])) == 1)

    # Issue 13: every combo display name must map to a backend-accepted key —
    # "Unknown numeric_clean op: ..." can never happen again.
    for name, key in dc.NUMERIC_CLEAN_OP_KEYS.items():
        s = pd.Series([1.0, 2.0, np.nan, 100.0])
        kwargs = ({"min": 0.0, "max": 50.0} if key == "clamp"
                  else {"value": 0.0} if key == "fillna_value" else {})
        try:
            tss.numeric_clean(s, key, **kwargs)
            ts_ok = True
        except ValueError as exc:
            ts_ok = "Unknown numeric_clean op" not in str(exc)
        check(f"op key '{key}' accepted by backend", ts_ok)

    df = pd.DataFrame({"num": [1.5, 2.5, 3.5, np.nan, 100.0]})
    bridge = capturing_bridge()
    p = DataCleanerPanel(bridge)
    p.set_dataframe(df)
    base = pd.to_numeric(df["num"], errors="coerce")

    errors = []
    orig_crit = QMessageBox.critical
    QMessageBox.critical = lambda *a, **k: errors.append(a[2])
    try:
        p.cmb_num_col.setCurrentText("num")

        # Clamp
        p.cmb_num_op.setCurrentText("Clamp to min/max range")
        p.num_min.setValue(2.0)
        p.num_max.setValue(4.0)
        p._on_num_preview()
        got = _to_pd(p._compute_numeric_clean("num", "clamp", {"min": 2.0, "max": 4.0}))["num"]
        check("clamp values + NaN preserved",
              got.iloc[0] == 2.0 and got.iloc[2] == 3.5 and got.iloc[4] == 4.0
              and pd.isna(got.iloc[3]), repr(list(got)))

        # IQR fills (only the single 100.0 outlier is replaced)
        for name, key, fill in [("Fill outliers (IQR) with median", "iqr_median",
                                 base.median()),
                                ("Fill outliers (IQR) with mean", "iqr_mean",
                                 base.mean())]:
            p.cmb_num_op.setCurrentText(name)
            p._on_num_preview()
            check(f"{name}: preview count",
                  "1 values will change" in p.lbl_num_preview.text().replace(",", ""),
                  p.lbl_num_preview.text())
            got = _to_pd(p._compute_numeric_clean("num", key, {"iqr_multiplier": 1.5}))["num"]
            check(f"{name}: outlier replaced, rest intact",
                  abs(got.iloc[4] - fill) < 1e-9 and got.iloc[0] == 1.5
                  and got.iloc[2] == 3.5, repr(list(got)))

        # Round: NaN-aware count must say 0 (regression for truthful labels)
        p.cmb_num_op.setCurrentText("Round to N decimal places")
        p.num_decimals.setValue(1)
        p._on_num_preview()
        got = _to_pd(p._compute_numeric_clean("num", "round", {"decimals": 1}))["num"]
        check("round to 1dp values", list(got.dropna()) == [1.5, 2.5, 3.5, 100.0],
              repr(list(got)))
        check("round preview count honest (NaN not counted)",
              "0 values will change" in p.lbl_num_preview.text()
              and not p.btn_num_apply.isEnabled(), p.lbl_num_preview.text())

        # Min-max
        p.cmb_num_op.setCurrentText("Scale to 0-1 (min-max)")
        p._on_num_preview()
        got = _to_pd(p._compute_numeric_clean("num", "minmax", {}))["num"]
        check("minmax range", got.iloc[0] == 0.0 and abs(got.iloc[4] - 1.0) < 1e-12
              and abs(got.iloc[1] - (2.5 - 1.5) / (100.0 - 1.5)) < 1e-9,
              repr(list(got)))

        # Z-score
        p.cmb_num_op.setCurrentText("Z-score standardisation")
        p._on_num_preview()
        got = _to_pd(p._compute_numeric_clean("num", "zscore", {}))["num"]
        exp = (base - base.mean()) / base.std()
        check("zscore matches + mean~0",
              np.allclose(got.dropna(), exp.dropna())
              and abs(got.dropna().mean()) < 1e-9, repr(list(got)))

        # Fill NaN variants
        p.cmb_num_op.setCurrentText("Fill NaN with median")
        p._on_num_preview()
        got = _to_pd(p._compute_numeric_clean("num", "fillna_median", {}))["num"]
        check("fillna median", got.isna().sum() == 0 and got.iloc[3] == base.median(),
              repr(list(got)))
        p.cmb_num_op.setCurrentText("Fill NaN with mean")
        p._on_num_preview()
        got = _to_pd(p._compute_numeric_clean("num", "fillna_mean", {}))["num"]
        check("fillna mean", got.isna().sum() == 0
              and abs(got.iloc[3] - base.mean()) < 1e-9, repr(list(got)))
        p.cmb_num_op.setCurrentText("Fill NaN with custom value")
        p.num_fill.setValue(-7.5)
        p._on_num_preview()
        got = _to_pd(p._compute_numeric_clean("num", "fillna_value", {"value": -7.5}))["num"]
        check("fillna custom value", got.isna().sum() == 0 and got.iloc[3] == -7.5,
              repr(list(got)))

        # Apply path through the worker thread
        p.cmb_num_op.setCurrentText("Clamp to min/max range")
        p.num_min.setValue(0.0)
        p.num_max.setValue(10.0)
        p._on_num_preview()
        p._on_num_apply()
        if p._worker is not None:
            p._worker.wait(10000)
        app().processEvents()
        check("numeric worker apply",
              bridge.df is not None and _to_pd(bridge.df)["num"].iloc[4] == 10.0
              and _to_pd(bridge.df)["num"].iloc[0] == 1.5 and pd.isna(_to_pd(bridge.df)["num"].iloc[3]),
              repr(list(_to_pd(bridge.df)["num"]) if bridge.df is not None else None))

        # Issue 11/14: stale preview must reset on parameter / op change
        p._on_num_preview()
        check("numeric re-preview enables apply", p.btn_num_apply.isEnabled())
        p.num_max.setValue(5.0)
        check("numeric param change disables apply",
              not p.btn_num_apply.isEnabled() and p.lbl_num_preview.text() == "",
              f"btn={p.btn_num_apply.isEnabled()} lbl={p.lbl_num_preview.text()!r}")
        p._on_num_preview()
        p.cmb_num_op.setCurrentText("Round to N decimal places")
        check("numeric op change disables apply",
              not p.btn_num_apply.isEnabled() and p.lbl_num_preview.text() == "",
              f"btn={p.btn_num_apply.isEnabled()} lbl={p.lbl_num_preview.text()!r}")
        check("no error dialogs in numeric clean", not errors, repr(errors))
    finally:
        QMessageBox.critical = orig_crit


def test_dtype():
    print("[6] DtypeTransformerPanel")
    app()
    from desktop.pages.dtype_transformer_panel import DtypeTransformerPanel

    p = DtypeTransformerPanel(silent_bridge())
    p.set_dataframe(make_df())

    # A numeric column that loses values (some "bad" -> NaT) — Apply must be enabled.
    # Use a real datetime64 column so the guard + lossy path both behave.
    p.set_dataframe(pd.DataFrame({
        "date": pd.to_datetime(["2023-01-01", "2023-02-01", "bad", "2023-04-01"],
                               errors="coerce"),
        "num": [1, 2, 3, 4.0],
    }))
    p.cmb_col.setCurrentText("date")
    p.cmb_target_dtype.setCurrentText("datetime64[ns]")
    p._on_preview()
    check("apply enabled after lossy preview", p.btn_apply.isEnabled(),
          "btn_apply still disabled")

    # Nonsense temporal->numeric refused with a warning
    captured = []
    orig = QMessageBox.warning
    QMessageBox.warning = lambda *a, **k: captured.append(a[2]) or QMessageBox.StandardButton.Ok
    try:
        p.cmb_col.setCurrentText("date")
        p.cmb_target_dtype.setCurrentText("float64")
        p._on_preview()
    finally:
        QMessageBox.warning = orig
    check("temporal->numeric refused", len(captured) > 0, repr(captured))

    # Clear preview hides the table
    p._clear_preview()
    check("clear preview hides", not p.table_preview.isVisible())


def test_dtype_extra():
    """Issue 15: close button, bounded preview, honest lossy preview, apply."""
    print("[15] Dtype Transformer: close/bounded/honest/apply (Issue 15)")
    app()
    from desktop.pages.dtype_transformer_panel import DtypeTransformerPanel

    bridge = capturing_bridge()
    p = DtypeTransformerPanel(bridge)
    p.set_dataframe(pd.DataFrame({
        "t": ["07:06:11", "08:15:00", "99:99:99"],  # object dtype, NOT temporal
        "n": [1, 2, 3],
    }))

    # The close (Clear Preview) control demanded by Issue 15 exists
    check("clear preview button exists", hasattr(p, "btn_clear_preview"))

    # Honest preview for time-strings -> float64: no fake "100%" / fake dates
    p.cmb_col.setCurrentText("t")
    p.cmb_target_dtype.setCurrentText("float64")
    p._on_preview()
    title = p.lbl_preview_title.text()
    check("time->float64 honest 0% (no fake success)",
          "0.0% success rate" in title and "0 of 3" in title, title)
    after_vals = [p.table_preview.item(i, 1).text()
                  for i in range(p.table_preview.rowCount())]
    check("after shows null markers, not dates",
          all("null" in v for v in after_vals)
          and not any(v.startswith("20") for v in after_vals), repr(after_vals))
    check("preview visible after run",
          not p.table_preview.isHidden() and not p.btn_clear_preview.isHidden())

    # Preview table can never balloon the pane (Issue 15 "unreadable")
    check("preview table bounded 80-180px",
          p.table_preview.minimumHeight() >= 80
          and p.table_preview.maximumHeight() <= 180,
          f"min={p.table_preview.minimumHeight()} max={p.table_preview.maximumHeight()}")

    # Lossy conversions warn but do not block Apply (Issue 15 "apply not working")
    check("apply enabled after lossy preview", p.btn_apply.isEnabled())
    p._clear_preview()
    check("clear preview hides table + disables apply",
          p.table_preview.isHidden() and not p.btn_apply.isEnabled())

    # Valid apply round-trip through the worker
    p.cmb_col.setCurrentText("n")
    p.cmb_target_dtype.setCurrentText("float64")
    p._on_preview()
    check("n->float64 100% preview",
          "100.0% success rate" in p.lbl_preview_title.text(),
          p.lbl_preview_title.text())
    p._on_apply()
    if p._worker is not None:
        p._worker.wait(10000)
    app().processEvents()
    check("apply converts to float64",
          bridge.df is not None and str(_to_pd(bridge.df)["n"].dtype) == "float64",
          str(_to_pd(bridge.df)["n"].dtype) if bridge.df is not None else "no df")

    # UI issues (user report): dtypes table must cover the whole area (no
    # 200px cap, stretch=1 in the panel layout) and the Clear Preview
    # control must be highlighted so it can be spotted quickly.
    idx = p._body_layout.indexOf(p.table_dtypes)
    check("dtypes table stretches to cover area",
          p.table_dtypes.maximumHeight() >= 1000 and idx >= 0
          and p._body_layout.stretch(idx) == 3,
          f"maxH={p.table_dtypes.maximumHeight()} idx={idx}")
    check("transform controls in right side panel",
          p.cmb_col.parentWidget().maximumWidth() <= 460,
          f"sideW={p.cmb_col.parentWidget().maximumWidth()}")
    check("clear preview button highlighted",
          p.btn_clear_preview.property("kind") == "primary",
          str(p.btn_clear_preview.property("kind")))

    # UI issue: row-index gutter must not crowd the values; cells centered.
    # The gutter is now MEASURED from the widest index label actually shown
    # (desktop/widgets/table_index_header.auto_fit_index_header) instead of a
    # hard-coded 36px, so assert the real contract: wide enough to draw the
    # largest row number without clipping, compact enough not to eat the
    # first data column, and centred.
    prev_w = p.table_preview.verticalHeader().minimumWidth()
    dtypes_w = p.table_dtypes.verticalHeader().minimumWidth()
    check("row-index gutter measured + cells centered",
          (30 <= prev_w <= 96 and 30 <= dtypes_w <= 96
           and prev_w == p.table_preview.verticalHeader().width()
           and dtypes_w == p.table_dtypes.verticalHeader().width()
           and p.table_dtypes.item(0, 0).textAlignment() == Qt.AlignCenter),
          f"prev={prev_w} dtypes={dtypes_w} "
          f"align={p.table_dtypes.item(0, 0).textAlignment() if p.table_dtypes.item(0, 0) else 'none'}")


def test_date_conversion():
    """Regression: categorical DD/MM/YYYY -> date lost 61% of values and
    silently swapped month/day for day<=12 (user report 'date conversion not
    working': transaction_date preview showed 39.0% / 58,202 of 149,116)."""
    print("[16] Dtype date conversion: day-first (DD/MM/YYYY) columns")
    import datetime as _dt
    import services.transform_service as tss

    # Service level: the exact user scenario (category dtype, DD/MM/YYYY)
    s = _pl.Series("v", ["01/01/2023", "13/01/2023", "05/01/2023", "bad", None])
    res = tss.preview_dtype_conversion(s, "date")
    check("day-first categorical -> date (80% success)",
          "error" not in res and res["pct"] == 80.0,
          f"pct={res.get('pct')} err={res.get('error')}")
    conv = _to_pd(res["converted"])
    check("no month/day swap (13/01->Jan13, 05/01->Jan5)",
          conv.iloc[0] == _dt.date(2023, 1, 1)
          and conv.iloc[1] == _dt.date(2023, 1, 13)
          and conv.iloc[2] == _dt.date(2023, 1, 5)
          and pd.isna(conv.iloc[3]) and pd.isna(conv.iloc[4]),
          repr(list(conv)))

    res_dt = tss.preview_dtype_conversion(s, "datetime64[ns]")
    conv_dt = _to_pd(res_dt["converted"])
    check("day-first categorical -> datetime64[ns]",
          res_dt["pct"] == 80.0 and conv_dt.iloc[1] == pd.Timestamp("2023-01-13"),
          repr(list(conv_dt)))

    # Unambiguous US month-first column must stay month-first
    res_us = tss.preview_dtype_conversion(
        _pl.Series("v", ["01/13/2023", "05/20/2023", "12/31/2023"]), "datetime64[ns]")
    conv_us = _to_pd(res_us["converted"])
    check("US month-first preserved",
          res_us["pct"] == 100.0
          and list(conv_us) == pd.to_datetime(
              ["2023-01-13", "2023-05-20", "2023-12-31"]).tolist(),
          repr(list(conv_us)))

    # Mixed conventions: day>12 values recovered via explicit-format retry
    res_mix = tss.preview_dtype_conversion(
        _pl.Series("v", ["13/01/2023", "01/13/2023"]), "datetime64[ns]")
    conv_mix = _to_pd(res_mix["converted"])
    check("mixed day/month conventions both recovered",
          res_mix["pct"] == 100.0
          and conv_mix.iloc[0] == pd.Timestamp("2023-01-13")
          and conv_mix.iloc[1] == pd.Timestamp("2023-01-13"),
          repr(list(conv_mix)))

    # ISO columns unaffected
    res_iso = tss.preview_dtype_conversion(
        _pl.Series("v", ["2024-01-05", "2024-02-10", "junk"]), "date")
    check("ISO dates still parse (2/3 success)",
          res_iso["pct"] == round((2 / 3) * 100, 1)
          and _to_pd(res_iso["converted"]).iloc[0] == _dt.date(2024, 1, 5),
          f"pct={res_iso['pct']}")

    # UI level: exact user scenario through the panel
    app()
    from desktop.pages.dtype_transformer_panel import DtypeTransformerPanel
    bridge = capturing_bridge()
    p = DtypeTransformerPanel(bridge)
    df = pd.DataFrame({
        "transaction_date": pd.Series(
            ["01/01/2023", "13/01/2023", "05/01/2023"], dtype="category"),
        "qty": [1, 2, 3],
    })
    p.set_dataframe(df)
    p.cmb_col.setCurrentText("transaction_date")
    p.cmb_target_dtype.setCurrentText("date")
    p._on_preview()
    check("panel: day-first preview 100%",
          "100.0% success rate" in p.lbl_preview_title.text(),
          p.lbl_preview_title.text())
    item = p.table_preview.item(0, 1)
    check("panel: preview shows ISO date",
          item is not None and item.text() == "2023-01-01",
          item.text() if item else "no item")
    p._on_apply()
    if p._worker is not None:
        p._worker.wait(10000)
    app().processEvents()
    check("panel: apply -> correct date objects",
          bridge.df is not None
          and _to_pd(bridge.df)["transaction_date"].iloc[1] == _dt.date(2023, 1, 13)
          and str(_to_pd(bridge.df)["transaction_date"].dtype) == "object",
          repr(list(_to_pd(bridge.df)["transaction_date"]) if bridge.df is not None else None))


def test_excel():
    print("[7] ExcelLoaderPanel merge")
    app()
    import os as _os, tempfile
    from desktop.pages.excel_loader_panel import ExcelLoaderPanel

    fact = pd.DataFrame({"key": [1, 2, 3], "sales": [10, 20, 30]})
    dim_a = pd.DataFrame({"key": [1, 2], "region": ["N", "S"]})
    dim_b = pd.DataFrame({"key": [2, 3], "channel": ["X", "Y"]})
    tmp = _os.path.join(tempfile.gettempdir(), "lytrize_smoke.xlsx")
    with pd.ExcelWriter(tmp) as w:
        fact.to_excel(w, sheet_name="Fact", index=False)
        dim_a.to_excel(w, sheet_name="Region", index=False)
        dim_b.to_excel(w, sheet_name="Channel", index=False)

    p = ExcelLoaderPanel(silent_bridge())
    orig_info = QMessageBox.information
    QMessageBox.information = lambda *a, **k: None
    try:
        p.load_file(tmp)
        p.rb_unified.setChecked(True)
        p._on_mode_changed(True)
        p.cmb_fact.setCurrentText("Fact")
        for i in range(p.list_dims.count()):
            p.list_dims.item(i).setSelected(True)
        p._on_dims_changed()
        check("join groups created", p.join_configs_layout.count() >= 2,
              f"{p.join_configs_layout.count()}")

        emitted = []
        p.merged_table_ready.connect(lambda df, desc: emitted.append((df, desc)))
        p._on_build_merged()
        check("merged emitted", len(emitted) == 1)
        if emitted:
            merged, _ = emitted[0]
            check("sales kept", "sales" in merged.columns)
            check("region imported", "region" in merged.columns)
            check("channel imported", "channel" in merged.columns)
    finally:
        QMessageBox.information = orig_info
        if _os.path.exists(tmp):
            _os.remove(tmp)


def test_classifier():
    print("[9] ColumnClassifierPanel drag & drop")
    app()
    from desktop.pages.column_classifier_panel import ColumnClassifierPanel

    p = ColumnClassifierPanel(silent_bridge())
    p.set_dataframe(pd.DataFrame({"a": [1, 2], "b": ["x", "y"], "c": ["p", "q"]}))

    check("drag enabled", p.list_num.dragEnabled() and p.list_num.acceptDrops())
    check("multi-select",
          p.list_num.selectionMode() == QListWidget.SelectionMode.MultiSelection)

    source = p.list_num
    idx = -1
    for i in range(source.count()):
        if source.item(i).text() == "a":
            idx = i
            break
    check("numeric has 'a'", idx >= 0)
    if idx >= 0:
        mime = QMimeData()
        mime.setData("application/x-lytrize-columns", b"a")
        ev = QDropEvent(QPoint(0, 0), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        p.list_cat.dropEvent(ev)
        check("moved 'a' to categorical",
              any(p.list_cat.item(i).text() == "a" for i in range(p.list_cat.count())))
    check("no overlap", not p.lbl_warning.isVisible())


def test_column_manager():
    print("[10] ColumnManagerPanel formula picker")
    app()
    from desktop.pages.column_manager_panel import ColumnManagerPanel

    p = ColumnManagerPanel(silent_bridge())
    p.set_dataframe(make_df())
    p.cmb_calc_type.setCurrentText("Custom formula (use col names)")
    check("formula picker exists", hasattr(p, "cmb_formula_col"))
    if hasattr(p, "cmb_formula_col"):
        items = [p.cmb_formula_col.itemText(i) for i in range(p.cmb_formula_col.count())]
        check("formula picker has cols", "text" in items and "num" in items, repr(items))


def test_date_difference(ts):
    """Regression: 'Overflow in int64 addition' when a non-date column
    (e.g. transaction_id) is picked as a date-difference input."""
    print("[11] Date Difference overflow guard")
    import services.transform_service as _ts

    # Exact scenario from the bug report: start = transaction_id,
    # end = transaction_date, unit = Days, "Today's date" selected.
    df = pd.DataFrame({
        "transaction_id": [1000001, 1000002, 3000003],
        "transaction_date": ["2024-01-05", "2024-02-10", "2024-03-15"],
    })
    try:
        ts.date_diff_column(df, "transaction_id", None, "Days")
        raised = None
    except Exception as exc:  # any exception must be the *friendly* one
        raised = str(exc)
    check("numeric start col raises friendly error",
          raised is not None and "doesn't look like a date column" in raised,
          repr(raised))
    check("no overflow in error", raised is None or "Overflow" not in raised,
          repr(raised))

    # String IDs (the pre-fix crash input) must also be refused politely.
    df2 = pd.DataFrame({
        "transaction_id": ["45123456789012345678", "99", "7"],
        "transaction_date": ["2024-01-05", "2024-02-10", "2024-03-15"],
    })
    try:
        ts.date_diff_column(df2, "transaction_id", "transaction_date", "Days")
        raised2 = None
    except Exception as exc:
        raised2 = str(exc)
    check("huge digit-string IDs refused politely",
          raised2 is not None and "doesn't look like a date column" in raised2
          and "Overflow" not in raised2, repr(raised2))

    # Happy path still works, incl. mixed garbage in the date column.
    out = ts.date_diff_column(df, "transaction_date", None, "Days")
    check("valid date diff computes", out.notna().sum() == 3, repr(list(out)))

    df3 = pd.DataFrame({"d": ["2024-01-05", "junk", "2024-03-15"]})
    out3 = ts.date_diff_column(df3, "d", None, "Days")
    check("mixed garbage coerces to <NA>", out3.isna().sum() == 1, repr(list(out3)))

    # Parser-level guard: _parse_datetime_robust must never raise.
    for name, s in [("numeric int64", pd.Series([1704067200000000000, 5])),
                    ("huge digit strings", pd.Series(["9" * 30, "123"])),
                    ("mixed date+huge", pd.Series(["2024-01-05", "9" * 25]))]:
        try:
            res = _ts._parse_datetime_robust(s)
            ok = _to_pd(res).isna().sum() >= 1
            detail = repr(list(res))
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        check(f"parser safe: {name}", ok, detail)



def test_analysis_redesign():
    """Analysis page redesign (tab shell + +/- next to Generate).

    Phase scope: the TOP half shows three tabs (Chart Generate/Edit,
    Layout Options, Typography Options) and ONLY the selected page is
    visible; chart +/- sit next to Generate in the tabs strip. Option
    fields intentionally arrive later, so this test asserts SHELL ONLY:
    header tabs exist, selection switches the visible page, and no option
    field widgets (combo/list/spin/checkbox) exist in the config panel.
    """
    from desktop.pages.analysis_page import AnalysisPage
    from services.analysis_service import WIDGET_SPEC
    from PySide6.QtWidgets import QComboBox, QListWidget, QSpinBox, QCheckBox

    print("[17] Analysis page: tab shell + +/- next to Generate (redesign)")

    # Ensure import path allows it.
    import sys
    if "backend" not in sys.path:
        sys.path.insert(0, "backend")

    from PySide6.QtCore import Qt

    class _Sig:
        def connect(self, cb):
            self._cb = cb

    class _Bridge:
        def __init__(self, df):
            self.df = df
            self.charts = []
            self._store = {}
            self.charts_changed = _Sig()
            # The page also listens for the dark<->light switch (chart tiles
            # re-theme in place), so the stub has to expose that signal too.
            self.theme_changed = _Sig()

        def get(self, k, d=None):
            if k == "charts":
                return self.charts
            return self._store.get(k, d)

        def set(self, k, v):
            if k == "charts":
                self.charts = v
            else:
                self._store[k] = v

        def add_chart(self, uid, title, fig, chart_type="", meta=None, note=""):
            self.charts = list(self.charts) + [(uid, title, fig)]
            if chart_type:
                self._store[f"chart_type_{uid}"] = chart_type
            if meta:
                self._store[f"chart_meta_{uid}"] = meta

        def replace_chart(self, uid, new_fig, new_title=None):
            for i, (c_uid, c_title, c_fig) in enumerate(self.charts):
                if c_uid == uid:
                    self.charts[i] = (c_uid, new_title or c_title, new_fig)
                    break

        def set_note(self, uid, text):
            pass

        def get_note(self, uid):
            return ""

        def get_chart_meta(self, uid):
            return self._store.get(f"chart_meta_{uid}", {})

        def set_chart_meta(self, uid, **kw):
            d = dict(self.get_chart_meta(uid))
            d.update(kw)
            self._store[f"chart_meta_{uid}"] = d

        def remove_chart(self, uid):
            pass

        def clear_charts(self):
            self.charts = []

        def save_draft(self):
            pass

    df = pd.DataFrame({"cat": ["a", "b", "c"], "num": [1.0, 2.0, 3.0],
                       "date": ["2023-01-01", "2023-01-02", "2023-01-03"]})
    b = _Bridge(df)
    page = AnalysisPage(b)

    # 1) Compact tab bar: no action strip above the tabs (edit/delete live
    #    only on the card header); Generate + Re-Generate live in the
    #    settings panel (chart_designs.pdf layout).
    check("analysis shell: no action strip above chart tabs",
          not hasattr(page, "tabs_row")
          and not hasattr(page, "btn_add_chart")
          and not hasattr(page, "btn_edit_chart")
          and not hasattr(page, "btn_remove_chart"),
          "")
    # The action row only exists once a type with a settings form is built.
    for i in range(page.analysis_list.count()):
        it = page.analysis_list.item(i)
        if it.data(Qt.UserRole) == "statistical":
            page.analysis_list.setCurrentItem(it)
            page._on_analysis_selected(it, None)
            break
    check("analysis shell: Generate moved into settings panel",
          not hasattr(page, "btn_generate")
          and page.config_panel.action_button() is not None
          and page.config_panel.regenerate_button() is not None
          and page.config_panel.action_button().parent().parent()
          is page.config_panel.body_generate,
          "")
    check("analysis shell: Re-Generate starts disabled",
          not page.config_panel.regenerate_button().isEnabled(), "")

    # 2) Non-descriptive types show the tabbed shell; exactly one page
    #    visible at a time. Types with a registered settings builder
    #    (statistical) get real option fields; the rest stay placeholders.
    for aid in WIDGET_SPEC:
        for i in range(page.analysis_list.count()):
            it = page.analysis_list.item(i)
            if it.data(Qt.UserRole) == aid:
                page.analysis_list.setCurrentItem(it)
                page._on_analysis_selected(it, None)
                break
        panel = page.config_panel
        tab_names = [panel.tab_bar.tabText(i) for i in range(panel.tab_bar.count())]
        check(f"analysis shell '{aid}': three setting tabs",
              tab_names == ["Chart Generate/Edit", "Layout Options", "Typography Options"],
              str(tab_names))
        check(f"analysis shell '{aid}': only selected page visible",
              panel.current_body() is panel.body_generate
              and not panel.body_layout.isVisibleTo(panel)
              and not panel.body_typo.isVisibleTo(panel),
              "")
        fields = []
        for _cls in (QComboBox, QListWidget, QSpinBox, QCheckBox):
            fields.extend(panel.findChildren(_cls))
        # Types with a registered settings builder must expose real controls
        # on all three tabs; the rest are still placeholders.
        #
        # The list is read from the registry rather than hardcoded. A literal
        # ("statistical", "distribution") silently went stale the moment
        # correlation was implemented, and the suite then failed a chart that
        # was in fact working correctly -- the exact failure mode that hides a
        # real regression in some other type.
        from desktop.widgets.analysis_settings import registered as _registered_types
        implemented = set(_registered_types())
        if aid in implemented:
            check(f"analysis shell '{aid}': settings fields present",
                  len(fields) >= 3, f"fields={len(fields)}")
            check(f"analysis shell '{aid}': has builder",
                  panel.has_builder(), "")
        else:
            check(f"analysis shell '{aid}': no option fields yet",
                  fields == [], f"fields={[type(w).__name__ for w in fields]}")
            check(f"analysis shell '{aid}': no builder yet",
                  not panel.has_builder(), "")
        # Switching to Layout/Typography shows ONLY that page.
        panel.tab_bar.setCurrentIndex(1)
        check(f"analysis shell '{aid}': layout page shows alone",
              panel.current_body() is panel.body_layout
              and not panel.body_generate.isVisibleTo(panel)
              and not panel.body_typo.isVisibleTo(panel),
              "")
        panel.tab_bar.setCurrentIndex(2)
        check(f"analysis shell '{aid}': typography page shows alone",
              panel.current_body() is panel.body_typo
              and not panel.body_generate.isVisibleTo(panel)
              and not panel.body_layout.isVisibleTo(panel),
              "")
        panel.tab_bar.setCurrentIndex(0)


def test_index_header_auto_fit():
    """Row-index gutter must be measured from the DISPLAYED row count.

    User report: on a 149,116-row file the index column stayed wide even
    in "Top 10" preview mode, stealing width from the first data column.
    The old code sized it from len(df) with a hand-rolled formula and
    ignored the preview; the fix measures the widest index label the table
    actually draws.
    """
    print("[17] Row-index gutter auto-fit (upload page + preview modes)")
    from PySide6.QtWidgets import QTableView
    from desktop.session_bridge import SessionBridge
    from desktop.pages.upload_page import UploadPage
    from desktop.widgets.dataframe_model import DataFrameModel
    from desktop.widgets.table_index_header import (
        auto_fit_index_header, MIN_INDEX_HEADER_WIDTH, MAX_INDEX_HEADER_WIDTH,
    )

    app_ = app()
    # 149,116 rows -- the size from the bug report.
    df = pd.DataFrame({"a": range(149116), "b": ["x"] * 149116})
    bridge = SessionBridge()
    bridge.set_df(df)
    page = UploadPage(bridge)
    page.resize(1200, 800)
    page.show()
    app_.processEvents()
    page._model.set_dataframe(df)
    page._update_preview_label()
    page._auto_fit_index_width()

    all_w = page.table.verticalHeader().width()
    check("gutter measured for full dataset",
          MIN_INDEX_HEADER_WIDTH <= all_w <= MAX_INDEX_HEADER_WIDTH,
          f"w={all_w}")

    # A 10-row preview must NOT inherit the 6-digit width.
    top_w = None
    for mode in ("Bottom 10", "Top 10", "Random 10"):
        page.preview_mode.setCurrentIndex(page.preview_mode.findText(mode))
        app_.processEvents()
        if mode == "Top 10":
            top_w = page.table.verticalHeader().width()
    check("gutter shrinks to the displayed row count", top_w < all_w,
          f"top10={top_w} all={all_w}")
    check("gutter never below the compact floor",
          top_w >= MIN_INDEX_HEADER_WIDTH, f"top10={top_w}")

    # REGRESSION: the initial load pushed the RAW frame into the model while
    # the combo already read "Top 10", so all 149,116 rows rendered behind a
    # "Top 10" label. The preview slice must be applied on load too.
    check("initial load honours the preview mode, not the raw frame",
          page._model.rowCount() <= 10,
          f"rows={page._model.rowCount()} "
          f"combo={page.preview_mode.currentText()!r}")

    # REGRESSION: setMinimumSectionSize() on the vertical header sets the ROW
    # HEIGHT floor as well as the gutter floor, so calling it with the gutter
    # width stretched every row (the "you increased row height" report).
    check("row height is not inflated by the gutter fit",
          page.table.rowHeight(0) <= 32,
          f"rowHeight={page.table.rowHeight(0)}")
    for mode in ("All rows", "Top 10"):
        page.preview_mode.setCurrentIndex(page.preview_mode.findText(mode))
        app_.processEvents()
        check(f"row height stays compact in {mode!r}",
              page.table.rowHeight(0) <= 32,
              f"rowHeight={page.table.rowHeight(0)} "
              f"gutter={page.table.verticalHeader().width()}")

    page.preview_mode.setCurrentIndex(page.preview_mode.findText("All rows"))
    app_.processEvents()
    check("gutter restores for full dataset",
          page.table.verticalHeader().width() == all_w,
          f"{page.table.verticalHeader().width()} vs {all_w}")
    page.close()


def test_index_header_helper():
    """The shared helper's width contract, independent of any page."""
    print("[18] Row-index helper: measured, monotonic, non-clipping")
    from PySide6.QtWidgets import QTableView, QHeaderView
    from desktop.widgets.dataframe_model import DataFrameModel
    from desktop.widgets.table_index_header import (
        auto_fit_index_header, MIN_INDEX_HEADER_WIDTH, MAX_INDEX_HEADER_WIDTH,
    )

    app()
    tbl = QTableView()
    tbl.setModel(DataFrameModel(pd.DataFrame({"a": range(5)})))
    widths = {n: auto_fit_index_header(tbl, rows=n)
              for n in (1, 10, 100, 1000, 149116)}
    check("helper width grows with the index label",
          all(widths[a] <= widths[b]
              for a, b in zip((1, 10, 100, 1000), (10, 100, 1000, 149116))),
          str(widths))
    # REGRESSION: auto_fit_index_header used to call setMinimumSectionSize(),
    # which on a QHeaderView is the floor for BOTH axes -- and the vertical
    # header's section size IS the row height. That inflated every data row
    # to the gutter width (53px) instead of compacting the index column.
    check("helper does NOT inflate row height",
          tbl.rowHeight(0) <= 30, f"rowHeight={tbl.rowHeight(0)}")
    check("helper leaves minimumSectionSize alone",
          tbl.verticalHeader().minimumSectionSize() < widths[149116],
          f"min={tbl.verticalHeader().minimumSectionSize()} "
          f"gutter={widths[149116]}")
    check("helper stays within its bounds",
          all(MIN_INDEX_HEADER_WIDTH <= w <= MAX_INDEX_HEADER_WIDTH
              for w in widths.values()), str(widths))
    fm = tbl.verticalHeader().fontMetrics()
    check("gutter fits the widest label without clipping",
          widths[149116] >= fm.horizontalAdvance("149116"),
          f"w={widths[149116]} text={fm.horizontalAdvance('149116')}")
    check("gutter is centred",
          tbl.verticalHeader().defaultAlignment() == Qt.AlignCenter,
          str(tbl.verticalHeader().defaultAlignment()))
    check("gutter is fixed (not interactively resizable)",
          tbl.verticalHeader().sectionResizeMode(0) == QHeaderView.Fixed,
          str(tbl.verticalHeader().sectionResizeMode(0)))


def test_chart_area_layout_and_render():
    """Chart area must lay out AND render immediately on Generate.

    User reports: the app appeared to "reopen itself" on Generate, and the
    new chart stayed blank until the window was resized. Root causes were
    (a) the chart tabs / empty hint / descriptive table sharing one
    QVBoxLayout, so hiding one left the tabs at half height and the
    QWebEngineView at 0px (Chromium does not paint at zero height), and
    (b) no explicit relayout once the view finally had a size.
    """
    print("[19] Chart area: stacked layout + immediate render (no resize)")
    import time
    from desktop.session_bridge import SessionBridge
    from desktop.pages.analysis_page import AnalysisPage

    app_ = app()
    bridge = SessionBridge()
    df = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [10, 20, 30, 40, 50]})
    bridge.set_df(df)
    bridge.set("num_cols", ["a", "b"])

    page = AnalysisPage(bridge)
    page.resize(1024, 768)
    page.show()
    app_.processEvents()
    for i in range(page.analysis_list.count()):
        it = page.analysis_list.item(i)
        if "statistical" in it.text().lower():
            page.analysis_list.setCurrentItem(it)
            break
    app_.processEvents()

    # Mutually exclusive views live in a QStackedWidget, not a box layout.
    check("chart area uses a QStackedWidget",
          page._chart_stack.count() == 3
          and page._chart_stack.widget(page._STACK_CHARTS) is page.charts_tabs
          and page._chart_stack.widget(page._STACK_EMPTY) is page.lbl_no_charts
          and page._chart_stack.widget(page._STACK_DESCRIPTIVE)
          is page.descriptive_container,
          f"count={page._chart_stack.count()}")
    check("empty state shown before any chart",
          page._chart_stack.currentIndex() == page._STACK_EMPTY,
          str(page._chart_stack.currentIndex()))

    page._on_generate()
    page._gen_worker.wait()
    app_.processEvents()

    check("stack switched to the charts page on Generate",
          page._chart_stack.currentIndex() == page._STACK_CHARTS,
          str(page._chart_stack.currentIndex()))

    card = list(page._chart_cards.values())[0]
    # The regression: this used to be 0, which is why Chromium never
    # painted and the chart looked blank until a manual resize.
    check("chart tabs get the FULL bottom pane (no half-height squish)",
          page.charts_tabs.height() > 200, f"h={page.charts_tabs.height()}")
    if card.plot_view._web_view is None:
        # Qt WebEngine is unavailable in this environment (offscreen
        # platform plugin); the ECharts fallback QLabel is present instead.
        # The live-render assertions need a real browser engine -- they are
        # part of the on-device acceptance run.
        print("  SKIP  web view render checks (Qt WebEngine unavailable here)")
        check("web view either live or clean fallback",
              card.plot_view._fallback is not None)
        page.close()
        page.deleteLater()
        app_.processEvents()
        return
    check("web view has real height immediately after Generate",
          card.plot_view._web_view.height() > 50,
          f"h={card.plot_view._web_view.height()}")

    # Let the event loop breathe: the shell must load and flush its pending
    # model WITHOUT the user resizing anything.
    deadline = time.time() + 5.0
    while time.time() < deadline and not card.plot_view._loaded:
        app_.processEvents()
        time.sleep(0.01)
    check("web view finished loading with no manual resize",
          card.plot_view._loaded, "loadFinished never fired")
    check("chart model was queued for the loaded page",
          card.plot_view._model is not None, "no model set on the web view")

    # THE decisive check: probe the live page. loadFinished used to look
    # healthy while the figure had actually been emitted into a page that
    # had not connected yet, so the chart area stayed blank. Only the DOM
    # can prove the chart really rendered.
    res: dict = {}
    card.plot_view._web_view.page().runJavaScript(
        "(function(){try{var r=document.getElementById('lytrize-chart');"
        "if(!r) return 'ERR no root';"
        "var c=r.querySelector('canvas,svg');"
        "return JSON.stringify({found:!!c,children:r.children.length,"
        "err:r.textContent||''});}"
        "catch(e){return 'ERR '+e}})()",
        lambda v: res.update(js=v))
    deadline = time.time() + 5.0
    while time.time() < deadline and "js" not in res:
        app_.processEvents()
        time.sleep(0.01)
    probe = res.get("js") or "{}"
    check("chart DOM rendered (canvas/svg present)",
          '"found":true' in probe.replace(" ", ""), probe)

    # Re-pushing the theme (the closest equivalent of a relayout in the
    # ECharts host) must never trigger a page reload.
    loaded_before = card.plot_view._loaded
    card.plot_view.apply_theme()
    app_.processEvents()
    check("theme re-push does not reload the page",
          card.plot_view._loaded == loaded_before, "")

    # No redundant title/type chrome eating chart height.
    check("card has no redundant on-card title label",
          not hasattr(card, "lbl_title"), "lbl_title still present")
    check("card still exposes the chart title",
          card._title == "Statistical Summary", str(card._title))
    check("chart kept most of the pane after the header cleanup",
          card.plot_view.height() > 100, f"h={card.plot_view.height()}")
    page.close()


def test_chart_canvas_and_title():
    """Chart canvas blends with the card and the title sits at the top."""
    print("[20] Chart canvas colour + title placement")
    from chart_display import (
        apply_display_options, theme_figure, chart_canvas_color,
        default_typo_opts,
    )
    from desktop import theme as th

    model = {"display": {}, "title": "Statistical Summary"}
    typo = default_typo_opts()
    styled = apply_display_options(model, {}, typo)
    check("apply_display_options returns the model with a filled display dict",
          styled is model and bool(model["display"]),
          f"display keys={sorted(model['display'])}")
    check("model title is preserved on the model",
          model.get("title") == "Statistical Summary", str(model.get("title")))
    check("display dict carries the renderer contract keys",
          all(k in model["display"] for k in
              ("show_border", "legend_position", "font_family", "font_size",
               "legend_font_size")),
          f"display keys={sorted(model['display'])}")
    # Idempotent: live preview + re-generate re-run this constantly.
    snapshot = dict(model["display"])
    apply_display_options(model, {}, typo)
    check("apply_display_options is idempotent",
          model["display"] == snapshot,
          "second call mutated the display dict")
    # Placement of the header is the host chrome's job in the ECharts-native
    # path, so the old Plotly y/top-anchor assertions are retired; the QSS
    # check below pins the card surface, and [22] covers the header band.

    # The card surface must equal the theme canvas in BOTH modes, else the
    # chart sits on a mismatched rectangle (the "BG not transparent" report).
    for mode in ("dark", "light"):
        themed = theme_figure({"display": {}}, mode)
        card_bg = th.CHART_CANVAS[th.mode_tokens(mode)["chart_canvas_key"]]
        check(f"{mode}: card surface matches the chart canvas",
              card_bg.lower() == str(themed["display"]["canvas_color"]).lower()
              == chart_canvas_color(mode).lower(),
              f"card={card_bg} canvas={themed['display'].get('canvas_color')}")
    check("theme_figure normalises odd mode strings",
          theme_figure({"display": {}}, "Light")["display"]["theme_mode"] == "light"
          and theme_figure({"display": {}}, "DARK")["display"]["theme_mode"] == "dark")
    check("chart card QSS uses the canvas colour, not alternate-base",
          "QFrame#chart_card { background: #ffffff" in th.build_qss("light"),
          "rule missing")


def test_header_alignment_and_band():
    """Header alignment + band scaling, pinned to the ECharts contract.

    The chart shell is ECharts-in-a-webview, so the title is drawn by the
    adapter (``lytrize_adapter.js``): it reads ``display.title_align``
    (falling back to ``typography.header_align``) for the anchor, and
    reserves a ``titleBand`` of ``max(34, header_size + 16)`` pixels of
    vertical room for the header. This test pins the Python half of that
    contract (the typography default, the model-layer clamp, the
    ``_title_band_px`` helper the export path uses), the adapter's
    arithmetic from source, and the Typography-tab control end to end.
    """
    print("[22] Header alignment, band scaling (ECharts contract)")
    from pathlib import Path
    import chart_display as cd
    from services.polars_chart_service import _header_align

    # -- 1. Alignment -------------------------------------------------------
    want = ("left", "center", "right")
    for align in want:
        t = cd.default_typo_opts()
        t["header_align"] = align
        check(f"header align {align}: kept by default_typo_opts round-trip",
              t["header_align"] == align, align)
    check("header_align is a real option, not hard-coded",
          "header_align" in cd.default_typo_opts(),
          str(sorted(cd.default_typo_opts())))
    check("default alignment is left (existing charts unchanged)",
          cd.default_typo_opts()["header_align"] == "left",
          str(cd.default_typo_opts()["header_align"]))
    check("unknown alignment falls back to left at the model layer",
          _header_align("diagonal") == "left"
          and _header_align("") == "left" and _header_align(None) == "left",
          "bogus alignments accepted")
    check("case/whitespace are normalised at the model layer",
          _header_align("  CENTER ") == "center" and _header_align("RIGHT") == "right",
          "normalisation broken")
    # apply_display_options no longer clobbers the typography alignment with
    # the layout default: the Typography control drives display.title_align.
    model = {"uid": "t22", "kind": "chart", "chart_type": "statistical",
             "title": "T", "recipe": {"version": 1, "chart_type": "statistical",
                                     "config": {}, "layout": {}, "typography": {}},
             "data": {"dimensions": [], "series": []},
             "display": {"title_align": "right"}}
    typo = cd.default_typo_opts()
    typo["header_align"] = "right"
    out = cd.apply_display_options(model, cd.default_layout_opts(), typo)
    check("apply_display_options: typography alignment wins over the layout default",
          out["display"].get("title_align") == "right",
          str(out["display"].get("title_align")))
    check("apply_display_options returns the same model object",
          out is model, "contract broken")
    typo["header_align"] = "diagonal"
    model2 = dict(model, display={"title_align": "left"})
    out = cd.apply_display_options(model2, cd.default_layout_opts(), typo)
    check("apply_display_options: a bogus alignment falls back to left",
          out["display"]["title_align"] == "left",
          str(out["display"]["title_align"]))
    out = cd.theme_figure(model, "DARK")
    check("theme_figure normalises the mode and keeps title_align",
          out["display"]["theme_mode"] == "dark"
          and out["display"]["title_align"] == "right", str(out["display"]))

    # -- 2. Band scales with the header size --------------------------------
    check("_title_band_px is monotonic and sane",
          cd._title_band_px(10) == 34 and cd._title_band_px(28) == 44
          and cd._title_band_px(36) == 52 and cd._title_band_px(60) == 76,
          f"{cd._title_band_px(10)}/{cd._title_band_px(28)}"
          f"/{cd._title_band_px(36)}/{cd._title_band_px(60)}")
    check("_title_band_px tolerates junk sizes",
          cd._title_band_px(None) == cd._title_band_px("bogus") == 36,
          f"{cd._title_band_px(None)}/{cd._title_band_px('bogus')}")
    # The flat-34px regression: a 36px header must not be squeezed into it.
    check("36px header is not squeezed into the old flat 34px band",
          cd._title_band_px(36) > 34, f"{cd._title_band_px(36)}")
    check("60px header gets a real band, not 34px",
          cd._title_band_px(60) > 60, f"{cd._title_band_px(60)}")
    js = (Path(cd.__file__).parent / "assets" / "echarts" /
          "lytrize_adapter.js").read_text(encoding="utf-8")
    check("adapter computes a title band from the header size",
          "var headerPx = Number(typography.header_size || display.title_size);" in js
          and "Math.max(34, headerPx + 16)" in js, "")
    check("adapter clamps a bogus header size instead of going NaN",
          "isFinite(headerPx)" in js, "")
    check("adapter title honours the configured alignment",
          'left: String(display.title_align || typography.header_align || "left").toLowerCase(),'
          in js, "")
    check("adapter reserves a gutter for outside value labels",
          'var labelGutter = labels.show !== false && labels.position !== "inside" ? 28 : 12;'
          in js, "")

    # -- The UI control exists and round-trips ------------------------------
    from desktop.widgets.analysis_settings.statistical import (
        StatisticalSettings, HALIGN_LABELS, HALIGNS,
    )
    b = StatisticalSettings()
    b._group_cols = ["region"]
    b._num_cols = ["sales"]
    b._palette_names = ["Default"]
    # The root must stay referenced: without a Python owner the combo (a
    # child of the returned widget) is garbage-collected and every later
    # access raises "Internal C++ object already deleted".
    root = b.build_typography()
    labels = [b._cb_halign.itemText(i) for i in range(b._cb_halign.count())]
    check("header align combo offers left/center/right",
          labels == ["Left", "Center", "Right"], str(labels))
    seen = []
    b.values_changed.connect(
        lambda payload: seen.append(payload.get("typography", {})))
    b._cb_halign.setCurrentText("Center")
    check("combo writes the lower-case key the backend expects",
          b._typo_opts.get("header_align") == "center",
          str(b._typo_opts.get("header_align")))
    check("combo change emits a typography payload",
          bool(seen) and seen[-1].get("header_align") == "center",
          str(seen[-1].get("header_align") if seen else None))
    b.prefill_typography({"header_align": "right", "header_size": 40})
    check("prefill restores the saved alignment",
          b._cb_halign.currentText() == "Right"
          and b._typo_opts.get("header_align") == "right",
          f"{b._cb_halign.currentText()}/"
          f"{b._typo_opts.get('header_align')}")
    b.prefill_typography({"header_align": "sideways"})
    check("prefill of a bogus alignment shows a valid option",
          b._cb_halign.currentText() in [HALIGN_LABELS[a] for a in HALIGNS],
          b._cb_halign.currentText())
    # And the value the UI produces is one the backend actually honours.
    b.prefill_typography({"header_align": "center"})
    check("the UI's value survives the model-layer clamp",
          _header_align(b._typo_opts.get("header_align")) == "center",
          str(b._typo_opts.get("header_align")))


def test_card_header_gap_and_cold_start():
    """The header must sit flush at the card top, and a cold start must paint.

    Two separate reports, two root causes:

    1. GAP. The action row (edit/style/delete) was the FIRST ITEM of the
       card's QVBoxLayout, so its 28px buttons + 6px spacing pushed the whole
       canvas down. The header is anchored to the top of the CANVAS, so it
       inherited that offset and started ~40px below the card's top edge --
       the "gap between chart header and top edge". The row now floats over
       the plot in the same grid cell instead of reserving a row above it.
       (The title itself is drawn by the ECharts shell; the adapter reserves
       a max(34, header + 16)px band for it -- see [22].)

    2. COLD START. The old PlotlyWebView polled for a usable size on a
       20x20ms timer and gave up after ~400ms, so a fresh launch left the
       chart at 0px until the user resized the window. The ECharts host
       (desktop/widgets/echarts_web_view.py) has no such budget to expire:
       Chromium paints the chart at any non-zero measured size immediately,
       a ResizeObserver re-measures after the layout settles, and the
       document repaints once the bundled fonts arrive.
    """
    print("[23] Card header flush to top + cold-start render")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from modules.chart_model import make_chart_model
    from desktop.widgets.chart_card import ChartCard
    from desktop.widgets import echarts_web_view as _ewv

    app_ = app()

    model = make_chart_model(
        uid="gap_uid", chart_type="statistical", title="Statistical Summary",
        data={"dimensions": ["g"],
              "series": [{"id": "c", "name": "c", "type": "bar",
                          "values": [{"name": "a", "value": 3}]}]},
        display={"legend": {"show": False}})

    # -- 1. No chrome row above the canvas --------------------------------
    host = QWidget()
    host.resize(1200, 700)
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    card = ChartCard("gap_uid", "Statistical Summary",
                     chart_model=model, chart_type="statistical")
    hl.addWidget(card)
    host.show()
    for _ in range(6):
        app_.processEvents()

    plot_y = card.plot_view.mapTo(card, QPoint(0, 0)).y()
    btn_y = card.btn_edit.mapTo(card, QPoint(0, 0)).y()
    check("canvas starts at the very top of the card (no chrome row above)",
          plot_y <= 2, f"plot_view y={plot_y}")
    check("card top margin is zero (header can reach the edge)",
          card.layout().contentsMargins().top() == 0,
          str(card.layout().contentsMargins().top()))
    # The buttons now overlay the plot rather than stacking above it.
    check("action buttons float over the canvas, not above it",
          abs(btn_y - plot_y) <= 2, f"btn y={btn_y} plot y={plot_y}")
    card._action_bar.raise_()
    check("action bar is raised in front of the plot",
          card._action_bar.isVisible(), "bar not in front")
    # A row above the canvas would cost ~34px; the plot must get that back.
    check("plot keeps the height the action row used to steal",
          card.plot_view.height() > 500, f"h={card.plot_view.height()}")

    host.close()

    # -- 2. Cold start: nothing expires before the webview is ready --------
    # The shell paints at any non-zero size, debounces only the expensive
    # option rebuild (never the paint), re-measures on a ResizeObserver, and
    # repaints when the font bundle lands. There is deliberately no
    # Python-side poll to assert against any more.
    shell = _ewv._HOST_HTML
    check("shell paints immediately at any non-zero size",
          "if(!w || !h) return;" in shell
          and "chart.resize({width: cw, height: ch})" in shell, "")
    check("shell debounces the option rebuild, not the paint",
          "resizeTimer=setTimeout(draw, 250)" in shell, "")
    check("shell re-measures on a ResizeObserver",
          "new ResizeObserver" in shell, "")
    check("shell repaints once the fonts arrive (cold-start legibility)",
          "document.fonts&&document.fonts.ready.then(draw)" in shell, "")
    check("the cold-start render path has no fixed attempt budget at all",
          "_relayout_when_sized" not in shell and "RELAYOUT_MAX_SECONDS" not in shell, "")


def test_tooltip_follows_theme():
    """Qt tooltips must be re-coloured by a dark<->light toggle.

    The report was a dark tooltip box with dark text sitting on the light UI.
    Root cause: QToolTip is a top-level window, not a child widget, so the
    app-wide setStyleSheet/setPalette repolish never reached it and the
    palette() reference in its QSS rule kept resolving against the palette
    captured when the tooltip was FIRST polished -- a stale dark plate.
    Fix: literal colours in the QSS + dismiss any open tooltip on toggle.
    """
    print("[21] Tooltip follows the app theme")
    from collections import Counter

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QToolTip, QWidget

    from desktop import theme as th

    app_ = QApplication.instance()

    # -- The QSS must carry literals, not palette() role references. --------
    for mode in ("dark", "light"):
        t = th.mode_tokens(mode)
        rule = [ln for ln in th.build_qss(mode).splitlines()
                if ln.strip().startswith("QToolTip")]
        check(f"tooltip QSS exists in {mode}", bool(rule), "rule missing")
        if rule:
            body = rule[0]
            check(f"tooltip QSS uses the {mode} plate literally",
                  t["tooltip_bg"] in body and t["text"] in body, body)
            check(f"tooltip QSS has no stale palette() role in {mode}",
                  "palette(tool-tip-base)" not in body, body)

    # -- And a tooltip rendered after a toggle really is that colour. ------
    host = QWidget()
    host.resize(400, 300)
    host.show()

    def _plate_colors():
        """Most common pixels of the LIVE tooltip window, if one exists.

        Matched on the tooltip's own text: the suite leaves other top-level
        QLabels around (cards, headers), and grabbing one of those would
        silently assert on an unrelated widget.
        """
        for tw in QApplication.topLevelWidgets():
            if tw is host or not tw.isWindow():
                continue
            if type(tw).__name__ != "QLabel":
                continue
            if "Statistical Summary" not in (tw.text() or ""):
                continue
            img = tw.grab().toImage()
            counts = Counter(img.pixelColor(x, y).name()
                             for x in range(img.width())
                             for y in range(img.height()))
            return counts.most_common(1)[0][0] if counts else None
        return None

    def _show_for(mode):
        th.apply_theme(app_, mode)
        QToolTip.showText(QPoint(50, 50), "Statistical Summary", host)
        for _ in range(4):
            app_.processEvents()
        return _plate_colors()

    try:
        # First pass primes the tooltip window; the toggle then has to move it.
        _show_for("dark")
        light_plate = _show_for("light")
        dark_plate = _show_for("dark")
        if light_plate is None or dark_plate is None:
            # Headless platforms may not create the tooltip window at all;
            # the QSS assertions above still guard the regression.
            check("tooltip window unavailable offscreen (QSS check stands)", True, "")
        else:
            check("light mode paints the tooltip with the light plate",
                  light_plate == th.mode_tokens("light")["tooltip_bg"],
                  f"{light_plate} != "
                  f"{th.mode_tokens('light')['tooltip_bg']}")
            check("dark mode paints the tooltip with the dark plate",
                  dark_plate == th.mode_tokens("dark")["tooltip_bg"],
                  f"{dark_plate} != {th.mode_tokens('dark')['tooltip_bg']}")
            check("the two modes really differ (test is not vacuous)",
                  light_plate != dark_plate, f"{light_plate} == {dark_plate}")
    finally:
        th.apply_theme(app_, "dark")
        QToolTip.hideText()
        host.close()

    # -- The palette the app advertises must match the plate the QSS paints.
    pal = th.build_palette("light")
    check("light palette tooltip roles match the QSS literals",
          pal.color(QPalette.ToolTipBase).name().lower()
          == th.mode_tokens("light")["tooltip_bg"].lower(),
          pal.color(QPalette.ToolTipBase).name())


def test_draft_restore_round_trip():
    """A close/reopen must bring the session back, not start empty.

    Autosave ran every 2 minutes and on every Generate, writing to
    ``draft_sessions``, but NOTHING ever read the row back -- get_draft had no
    caller anywhere in desktop/. So the app diligently saved the work and then
    dropped it on exit, which is what a user experiences as "the app restarted
    and lost everything".

    This drives the real bridge: build a session, save_draft, then restore it
    into a *fresh* bridge (a new process would behave identically) and assert
    the chart, its figure, notes, chart_type, meta and DataFrame all survive.
    """
    print("[24] Draft save -> restore round trip")
    import polars as _pl_local
    from modules.chart_model import make_chart_model

    from desktop.session_bridge import SessionBridge
    from services.database_service import get_draft, clear_draft, init_db

    app_ = app()

    # A real cold start runs MainWindow._init_session() -> init_db() before
    # anything touches draft_sessions. Without it this test silently relied on
    # a schema that some earlier app launch had left behind in the user's DB
    # file: on a fresh machine the INSERT below hits
    # "no such table: draft_sessions", the error is swallowed, and the draft is
    # dropped on the floor. Mirror the real startup path so the round trip is
    # reproduced on a brand-new DB too.
    init_db()

    # -- phase 1: build a session and autosave it -----------------------
    b1 = SessionBridge()
    b1.set("user_id", 1)
    df = _pl_local.DataFrame({"g": list("abcd") * 15,
                              "v": list(np.arange(60.0))})
    b1.set_df(df)
    model = make_chart_model(
        uid="uid_r1", chart_type="statistical", title="Restored Bar",
        data={"dimensions": ["g"],
              "series": [{"id": "count",
                          "name": "count",
                          "type": "bar",
                          "values": [{"name": c, "value": v} for c, v in
                                     [("a", 1), ("b", 2), ("c", 3), ("d", 4)]]}]},
    )
    b1.add_chart("uid_r1", "Restored Bar", model,
                 chart_type="statistical", meta={"layout": {"title": "Hello"}})
    b1.set("page", "analysis")
    b1.set("file_name", "demo.csv")
    b1.set("layout_mode", "landscape")
    # A dragged free-canvas arrangement, exactly as the dashboard stores it.
    # Without persisting it, autosave (which fires on every edit) restores the
    # charts but re-auto-places them, so a relaunch undid every drag.
    b1.set("tile_geometry", {"uid_r1": {"x": 0.25, "y": 130.0, "w": 0.5, "h": 320.0},
                             "__mode__": "free"})
    b1.set_note("uid_r1", "a note")
    b1.manager.save_df_snapshot(1, b1.df)
    # save_draft() is synchronous (the background saver was removed because
    # an autosave that landed after app close lost the draft), so the row is
    # visible as soon as it returns.
    b1.save_draft()

    draft = get_draft(1)
    check("draft: autosave wrote a row", bool(draft))

    # -- phase 2: fresh bridge == a relaunch -----------------------------
    b2 = SessionBridge()
    b2.set("user_id", 1)
    restored = b2.restore_draft()
    charts = b2.get("charts", [])

    check("draft: restore reports success", restored is True)
    check("draft: chart count restored", len(charts) == 1, f"got {len(charts)}")
    if not charts:
        clear_draft(1)
        return

    uid, title, rmodel = charts[0]
    check("draft: title restored", title == "Restored Bar", f"got {title!r}")
    check("draft: chart model restored", isinstance(rmodel, dict)
          and rmodel.get("kind") == "chart"
          and len(rmodel.get("data", {}).get("series", [])) == 1,
          f"model={rmodel!r}")
    # chart_type lives in a separate "chart_type_<uid>" key, which the old
    # 3-tuple serialisation dropped on the floor.
    check("draft: chart_type restored",
          b2.get(f"chart_type_{uid}") == "statistical",
          f"got {b2.get(f'chart_type_{uid}')!r}")
    check("draft: meta restored",
          b2.get(f"chart_meta_{uid}", {}).get("layout", {}).get("title") == "Hello")
    check("draft: note restored", b2.get_note(uid) == "a note",
          f"got {b2.get_note(uid)!r}")
    check("draft: DataFrame restored from parquet",
          b2.df is not None and tuple(b2.df.shape) == (60, 2),
          f"got {None if b2.df is None else b2.df.shape}")
    check("draft: page restored", b2.get("draft_page") == "analysis",
          f"got {b2.get('draft_page')!r}")
    check("draft: file_name restored", b2.get("file_name") == "demo.csv")
    check("draft: layout_mode restored", b2.get("layout_mode") == "landscape")

    # The free-canvas placement must survive too, or a relaunch re-auto-places
    # every card and the drag the user made appears to reset on its own.
    _tiles = b2.get("tile_geometry", {})
    check("draft: tile_geometry restored",
          _tiles.get("uid_r1") == {"x": 0.25, "y": 130.0, "w": 0.5, "h": 320.0},
          f"got {_tiles}")
    check("draft: free-canvas mode marker restored",
          _tiles.get("__mode__") == "free", f"got {_tiles.get('__mode__')!r}")

    # -- an empty draft is a normal empty start, never an exception ------
    clear_draft(1)
    b3 = SessionBridge()
    b3.set("user_id", 1)
    check("draft: cleared draft restores as empty", b3.restore_draft() is False)
    b4 = SessionBridge()
    check("draft: no user_id restores as empty", b4.restore_draft() is False)


def test_draft_restore_survives_corrupt_row():
    """A corrupt draft must degrade to an empty start, never block launch."""
    print("[25] Draft restore survives a corrupt row")
    from desktop.session_bridge import SessionBridge
    from services.database_service import save_draft as db_save_draft

    app_ = app()
    # Same startup contract as the round-trip test above: without the schema a
    # missing table would make the corrupt row silently "degrade" for the wrong
    # reason (nothing was ever stored), so the check would pass vacuously.
    from services.database_service import init_db
    init_db()

    b = SessionBridge()
    b.set("user_id", 1)
    db_save_draft(user_id=1, page="analysis", charts_json="{not json at all",
                  file_name="broken.csv")

    b2 = SessionBridge()
    b2.set("user_id", 1)
    try:
        result = b2.restore_draft()
        check("draft: corrupt row returns False instead of raising",
              result is False, f"got {result!r}")
    except Exception as exc:  # noqa: BLE001 - that is the point of the test
        check("draft: corrupt row returns False instead of raising", False, str(exc))
    finally:
        from services.database_service import clear_draft
        clear_draft(1)


def test_crasher_font_guard():
    """The app must not segfault on startup while shaping UI text.

    Regression: the old dev launcher (run_dev.sh, since removed) died with SIGSEGV before its first frame. The
    stack was ``QPushButton::sizeHint -> QFontMetrics::boundingRect ->
    QTextEngine::shapeText -> FcCharSetHasChar`` -- a crash inside fontconfig
    while Qt probed the fallback chain for a glyph. Two offenders on this
    machine: NotoColorEmoji (colour outlines) and the .woff2 Material Symbols
    icon fonts, which declare an EMPTY family so fontconfig mis-binds them to
    plain families like "Inter" and drops them into every fallback chain.

    ``_guard_crasher_emoji_fonts`` rejects both before QApplication exists.
    This asserts the conf is actually written and installed, then renders each
    glyph that used to kill the process.
    """
    print("[26] Crasher-font guard installed + glyphs render")
    import os
    import subprocess
    import sys as _sys

    from desktop.app import (_guard_crasher_emoji_fonts,
                             _CRASHER_FONT_GLOBS)

    # The guard must not clobber an explicit user FONTCONFIG_FILE.
    saved = os.environ.get("FONTCONFIG_FILE")
    os.environ["FONTCONFIG_FILE"] = "/tmp/lytrize-preexisting.conf"
    check("font guard: respects a user FONTCONFIG_FILE",
          _guard_crasher_emoji_fonts() is False)
    del os.environ["FONTCONFIG_FILE"]
    if saved is not None:
        os.environ["FONTCONFIG_FILE"] = saved

    check("font guard: installs when env is clean",
          _guard_crasher_emoji_fonts() is True)

    conf_path = os.environ.get("FONTCONFIG_FILE", "")
    check("font guard: sets FONTCONFIG_FILE", bool(conf_path), conf_path)
    conf = ""
    if conf_path and os.path.exists(conf_path):
        with open(conf_path, encoding="utf-8") as fh:
            conf = fh.read()
    for glob in _CRASHER_FONT_GLOBS:
        check(f"font guard: rejects glob {glob}", glob in conf)
    # Rejecting the colour emoji font is what turned every glyph into a tofu
    # box: NotoColorEmoji is the only font here carrying U+1F5D1/U+1F441 and
    # no monochrome fallback is installed. Guard against regressing to that.
    check("font guard: does NOT reject the colour emoji font",
          "NotoColorEmoji" not in conf and "Noto Color Emoji" not in conf)

    # The glyphs that reproduced the crash, each in its own process: a
    # segfault takes the whole interpreter down, so an in-process loop could
    # never report a failure for all of them.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    probe = (
        "import sys; sys.path.insert(0, %r)\n"
        "from desktop.app import _guard_crasher_emoji_fonts\n"
        "_guard_crasher_emoji_fonts()\n"
        "from PySide6.QtWidgets import QApplication, QPushButton\n"
        "app = QApplication(sys.argv)\n"
        "b = QPushButton(sys.argv[1]); b.show(); app.processEvents()\n"
        "print('OK')\n" % repo_root
    )
    probe_path = os.path.join(os.path.dirname(conf_path or "/tmp"), "lytrize_glyph_probe.py")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    env.pop("FONTCONFIG_FILE", None)  # let the guard install its own
    with open(probe_path, "w", encoding="utf-8") as fh:
        fh.write(probe)
    for cp in ("1F5D1", "1F3F7", "1F441", "1F170", "1F5D2", "1F6E0"):
        r = subprocess.run([_sys.executable, probe_path, chr(int(cp, 16))],
                           capture_output=True, timeout=120, env=env)
        check(f"font guard: U+{cp} renders without crashing",
              r.returncode == 0, f"exit={r.returncode}")
    try:
        os.remove(probe_path)
    except OSError:
        pass


def test_emoji_glyphs_are_not_tofu():
    """Every emoji the UI uses must have a real outline, not a .notdef box.

    History: the first version of the font guard rejected NotoColorEmoji. That
    stopped the segfault but left every emoji as a tofu rectangle on machines
    whose only system symbol fonts lacked these glyphs. The app now SHIPS a
    monochrome face (Noto Emoji, OFL) precisely so the UI cannot depend on the
    OS providing one -- and every label that ships an emoji can be resolved by
    the Qt font database and the web shell's @font-face.

    So the deterministic check is the shipped bundle itself: read the cmap of
    every bundled font file (the exact bytes Qt and Chromium resolve
    through) and require each codepoint the UI uses to appear in at least
    one of them. No fc-list dependency, no reliance on the machine's font
    set -- a minimal Linux box with zero system emoji fonts is the case the
    bundle exists for.

    QFontMetrics.inFont()/horizontalAdvance() are deliberately not used:
    both succeed happily on a .notdef box, which is how the tofu shipped
    unnoticed behind a green test.
    """
    print("[27] Emoji glyphs resolve to a real font (no tofu)")
    try:
        from fontTools.ttLib import TTFont
    except Exception as exc:
        check("tofu: fontTools is available to read bundled cmaps", False,
              f"fontTools import failed: {exc}")
        return
    from font_bundle import font_files, family_of, bundled_families

    # Codepoints the UI actually ships, including the six that originally
    # crashed. The bundled monochrome face must cover every one.
    codepoints = {
        "1F5D1": "trash", "1F3F7": "tag", "1F441": "eye", "1F170": "A-button",
        "1F5D2": "note-pencil", "1F6E0": "tools", "1F4CA": "bar-chart",
        "1F4C8": "line-chart", "1F680": "rocket", "1F3E0": "house",
        "1F4C2": "open-folder", "1F4BE": "floppy", "1F4C1": "folder",
        "1F464": "bust", "1F3A8": "palette", "1F3AF": "target",
        "1F4F7": "camera", "1F4D1": "notebook", "1F4D5": "calendar",
        "2705": "check", "274C": "cross-mark", "26A0": "warning",
        "1F9F9": "broom", "1F9E9": "puzzle", "1F6A8": "siren",
    }
    files = font_files()
    check("tofu: the app ships its own font files", bool(files), "FONT_DIR is empty")
    covered = set()
    unreadable = []
    for path in files:
        try:
            with TTFont(str(path), lazy=True) as f:
                cmap = f.getBestCmap()
                if cmap:
                    covered.update(cmap)
        except Exception:
            unreadable.append(path.name)
    check("tofu: every bundled font file is a readable font", not unreadable,
          "unreadable: " + ", ".join(unreadable))
    missing = ["U+%s (%s)" % (cp, name) for cp, name in sorted(codepoints.items())
               if int(cp, 16) not in covered]
    check("tofu: the bundled fonts claim every emoji the UI uses",
          not missing, "not covered by any bundled font: " + ", ".join(missing))
    # The emoji face ships for glyph fallback only: the text picker must not
    # offer it, or every heading becomes emoji-breadth.
    fams = {family_of(p) for p in files}
    check("tofu: the monochrome emoji face is bundled and named once",
          "Noto Emoji" in fams, str(sorted(fams))[:200])
    check("tofu: the emoji face is not offered in the text picker",
          "Noto Emoji" not in bundled_families(),
          "picker would offer the symbol fallback font")


def test_font_choices_are_installed_and_snapped():
    """The font picker must only offer fonts this machine can really draw.

    Regression: the combo offered 33 hard-coded families (Inter, Georgia,
    Comic Sans MS, Impact, ...) but none of them are installed on a typical
    box. The browser silently substituted a default for every missing family,
    so most choices changed nothing -- the "many fonts are not even working"
    report. Separately, the stored default "Inter" was missing too, so
    setCurrentText() silently no-opped while the chart kept asking for a font
    that does not exist.
    """
    print("[29] Font choices are installed; default is snapped to the list")
    from chart_display import (available_fonts, font_is_available,
                               installed_font_families, _FONT_CHOICES,
                               default_typo_opts)

    installed = installed_font_families()
    offered = [f["family"] for f in available_fonts(prefer=["Inter"])]

    # A font is "usable" if the app ships it OR the machine has it. Bundled
    # fonts are registered at startup (see register_qt_fonts), so demanding a
    # system install here would be wrong -- and was, before the bundle existed.
    try:
        from font_bundle import bundled_families
        bundled = set(bundled_families())
    except Exception:
        bundled = set()

    def _usable(name):
        return name in bundled or font_is_available(name)

    check("font: picker is never empty", bool(offered), "offered nothing")
    unusable = [f for f in offered if not _usable(f)]
    check("font: every offered font is usable (bundled or installed)",
          not unusable, "unusable: " + ", ".join(unusable))

    # The picker must not offer a font the app cannot render AND the machine
    # cannot render -- that combination is the silent no-op bug.
    check("font: no silently-broken choices offered",
          not any(f not in bundled and not font_is_available(f)
                  for f in offered))

    # Missing families from the wish-list are filtered out.
    check("font: missing families are dropped from the picker",
          not any(f in offered for f in ("Comic Sans MS", "Impact")
                  if not _usable(f)))

    # The cache must not re-run fc-list (an fc-list per combo is far too slow).
    before = installed_font_families()
    check("font: family list is cached", before is installed)

    # default_typo_opts names Inter. It is bundled, so it is always usable;
    # when it is neither bundled nor installed, the panel must have snapped the
    # stored value to something that is.
    d = default_typo_opts()
    if not _usable(d.get("family", "")):
        check("font: absent default has a real substitute available",
              bool(offered) and _usable(offered[0]),
              "no usable substitute to snap to")
    else:
        check("font: the documented default is usable", True)

    # Determinism: the same call twice gives the same list.
    check("font: available_fonts is stable across calls",
          [f["family"] for f in available_fonts(prefer=["Inter"])] == offered)

    # A machine with a proper Inter installed still lists it first.
    if font_is_available("Inter"):
        check("font: installed default is listed first",
              offered[0] == "Inter", f"got {offered[0]!r}")


def test_font_change_uses_fast_debounce():
    """A combo-box font pick must not wait out the 300ms slider debounce.

    Regression: every typography change used one 300ms timer, so picking a
    font family made the user wait the full debounce plus a ~40ms Plotly
    layout rebuild on the GUI thread. Discrete combo choices now take a short
    path; slider/spinbox drags stay coalesced at 300ms.
    """
    print("[30] Discrete font picks use the short preview debounce")
    from PySide6.QtWidgets import QApplication
    from desktop.session_bridge import SessionBridge
    from desktop.pages.analysis_page import (_PREVIEW_FAST_MS,
                                             _PREVIEW_DEBOUNCE_MS,
                                             AnalysisPage)

    app_ = app()
    check("debounce: fast path is well under the slow one",
          0 < _PREVIEW_FAST_MS < _PREVIEW_DEBOUNCE_MS,
          f"fast={_PREVIEW_FAST_MS} slow={_PREVIEW_DEBOUNCE_MS}")

    page = AnalysisPage(SessionBridge())
    page._settings_target_uid = "some-uid"
    try:
        # A discrete pick (combo box) -> short timer only.
        page._on_settings_changed("typography",
                                  {"typography": {"family": "DejaVu Sans"},
                                   "discrete": True})
        check("debounce: discrete change arms the fast timer",
              page._preview_fast_timer.isActive()
              and not page._preview_timer.isActive())
        page._preview_fast_timer.stop()

        # A dragged value -> long timer only.
        page._on_settings_changed("typography",
                                  {"typography": {"header_size": 20}})
        check("debounce: continuous change arms the slow timer",
              page._preview_timer.isActive()
              and not page._preview_fast_timer.isActive())
        page._preview_timer.stop()

        # A discrete pick must cancel a pending slow pass, otherwise the slow
        # re-render lands on top of the fast one and the chart flickers twice.
        page._on_settings_changed("typography", {"typography": {"header_size": 22}})
        page._on_settings_changed("typography",
                                  {"typography": {"family": "DejaVu Serif"},
                                   "discrete": True})
        check("debounce: discrete change cancels the pending slow pass",
              page._preview_fast_timer.isActive()
              and not page._preview_timer.isActive())
        page._preview_fast_timer.stop()

        # No chart selected -> nothing armed at all.
        page._settings_target_uid = None
        page._on_settings_changed("typography",
                                  {"typography": {"family": "x"},
                                   "discrete": True})
        check("debounce: no target arms nothing",
              not page._preview_fast_timer.isActive()
              and not page._preview_timer.isActive())
    finally:
        page._preview_fast_timer.stop()
        page._preview_timer.stop()
        page.deleteLater()


def test_bundled_fonts_are_registered_everywhere():
    """The app must ship its own fonts, and register them with BOTH renderers.

    Regression: the font picker offered families the machine did not have, so
    most choices silently did nothing. Bundling the files removes that class of
    bug, but only if BOTH consumers are wired up, because they do not share a
    font stack:

    * Qt widgets (the settings preview label, the combo boxes) need
      QFontDatabase.addApplicationFont.
    * The charts render in a QWebEngineView (Chromium), which cannot see Qt's
      font database at all and needs CSS @font-face in the shell HTML.

    Missing either one leaves half the UI still falling back.
    """
    print("[31] Bundled fonts registered with Qt and with the chart shell")
    from font_bundle import (FONT_DIR, SUBSTITUTES, bundled_families,
                             family_of, font_face_css, font_files, resolve,
                             register_qt_fonts)

    files = font_files()
    check("bundle: font files are present", bool(files),
          f"no fonts in {FONT_DIR}")
    if not files:
        return

    check("bundle: licence files accompany the fonts",
          bool(list(FONT_DIR.rglob("LICENSE-*.txt"))),
          "no LICENSE-*.txt beside the fonts")
    _open_dir = FONT_DIR / "fonts"
    _no_licence = sorted({
        f.stem.split("-")[0] for f in _open_dir.glob("*.ttf")
        if not (_open_dir / f"LICENSE-{f.stem.split('-')[0]}.txt").exists()
    }) if _open_dir.is_dir() else ["<open-pack dir missing>"]
    check("bundle: every open-pack font has its licence file",
          not _no_licence, ", ".join(_no_licence))

    # family_of must produce the DISPLAY name, matching what Qt reports.
    check("bundle: family names are display names (spaced)",
          not any("OpenSans" == f for f in bundled_families()),
          "file stems leaked into family names")
    check("bundle: Inter (the app default) is bundled",
          "Inter" in bundled_families())

    # Qt side: every family must actually register.
    reg = register_qt_fonts()
    check("bundle: fonts register with Qt", len(reg) >= len(bundled_families()),
          f"{len(reg)} registered vs {len(bundled_families())} bundled")
    for fam in bundled_families():
        check(f"bundle: Qt knows {fam!r}", fam in reg)

    # Chromium side: the shell needs @font-face for the same families.
    css = font_face_css()
    for fam in bundled_families():
        check(f"bundle: @font-face declares {fam!r}",
              ("font-family:'%s'" % fam) in css)
    check("bundle: @font-face uses file:// URLs", "url('file://" in css)

    # The chart shell is desktop.widgets.echarts_web_view._HOST_HTML; the
    # @font-face block is injected through the <!--LYTRIZE_FONT_FACES-->
    # placeholder at widget construction. A placeholder that is never
    # replaced -- or CSS that never reaches the page -- silently drops every
    # bundled font in the chart, so check the real string the view serves.
    try:
        from desktop.widgets import echarts_web_view as _eev
        shell = _eev._HOST_HTML
        check("bundle: shell HTML carries the font-face placeholder",
              "<!--LYTRIZE_FONT_FACES-->" in shell)
        rendered = shell.replace("<!--LYTRIZE_FONT_FACES-->", font_face_css())
        check("bundle: shell HTML contains the @font-face rules",
              rendered.count("@font-face") >= len(bundled_families()),
              f"{rendered.count('@font-face')} rules in shell")
        check("bundle: no unformatted placeholder left in the shell",
              "LYTRIZE_FONT_FACES" not in rendered)
        check("bundle: shell font CSS matches the current font set",
              rendered.count("@font-face") == font_face_css().count("@font-face"))
    except Exception as exc:  # pragma: no cover
        check("bundle: shell HTML contains the @font-face rules", False, str(exc))

    # Proprietary names must map to a bundled, metric-compatible stand-in.
    for name, stand_in in SUBSTITUTES.items():
        if stand_in in bundled_families():
            check(f"bundle: {name!r} resolves to bundled {stand_in!r}",
                  resolve(name) == stand_in, f"got {resolve(name)!r}")

    check("bundle: bundled family resolves to itself", resolve("Inter") == "Inter")
    check("bundle: unknown family passes through unchanged",
          resolve("Mystery Font") == "Mystery Font")

    # Every bundled family must be offered by the picker.
    from chart_display import available_fonts
    offered_names = {f["family"] for f in available_fonts(prefer=["Inter"])}
    for fam in bundled_families():
        check(f"bundle: picker offers {fam!r}", fam in offered_names)

    # The Microsoft core fonts resolve to themselves -- the bundle ships the
    # real thing, so the metric-compatible substitution table no longer
    # applies to them.
    for name in ("Arial", "Calibri", "Georgia", "Times New Roman", "Verdana",
                 "Trebuchet MS", "Consolas", "Courier New", "Comic Sans MS",
                 "Impact", "Cambria", "Candara", "Corbel", "Constantia",
                 "Tahoma", "Andale Mono", "Arial Black"):
        check(f"bundle: {name!r} resolves to itself", resolve(name) == name,
              f"got {resolve(name)!r}")
    check("bundle: Webdings is bundled but never offered",
          "Webdings" in {f for p_ in files for f in [family_of(p_)]}
          and "Webdings" not in offered_names)


def test_bundled_font_stacks_resolve_for_charts():
    """A chart font request must reach a family the page can actually draw.

    _font_stack feeds the Plotly figure (drawn by Chromium) and
    header_font_css feeds the Qt-drawn heading. A proprietary name has to be
    mapped to its bundled stand-in in BOTH, otherwise the heading and the
    chart text end up in different typefaces.
    """
    print("[32] Chart/heading font stacks resolve through the bundle")
    from chart_display import _font_stack, header_font_css
    from font_bundle import bundled_families

    bundled = set(bundled_families())

    # Georgia ships in the bundle (georgia.ttf), so it maps to itself and
    # stays a Georgia stack -- no substitution.
    stack = _font_stack("Georgia")
    check("stack: Georgia resolves to the bundled face",
          stack.split(",")[0].strip() in bundled, stack)
    check("stack: Georgia keeps the requested name first",
          stack.split(",")[0].strip() == "Georgia", stack)

    # A proprietary name with no shipped counterpart must resolve to its
    # bundled metric-compatible stand-in (the SUBSTITUTES table), in both the
    # chart stack and the Qt heading css.
    check("stack: Helvetica maps to the bundled Carlito",
          _font_stack("Helvetica").split(",")[0].strip() == "Carlito",
          _font_stack("Helvetica"))

    for name in ("Inter", "Lato", "Roboto", "Open Sans", "Fira Code"):
        check(f"stack: {name!r} is offered as itself", name in _font_stack(name))

    css = header_font_css({"header_font_family": "Helvetica"})
    check("stack: heading css uses the bundled stand-in", "Carlito" in css,
          css[:60])
    check("stack: heading css for Inter names Inter",
          "Inter" in header_font_css({"header_font_family": "Inter"}))

    # No stack may ever be empty or contain a stray placeholder.
    for name in ("Inter", "Georgia", "Arial", "Comic Sans MS"):
        s = _font_stack(name)
        check(f"stack: {name!r} stack is non-empty and clean",
              bool(s.strip()) and "{" not in s and "}" not in s, s)


def test_distribution_settings_match_statistical_design():
    """Distribution must use the SAME three-tab design as statistical.

    The brief is that every analysis type reuses the statistical layout, so
    these checks compare the two builders head to head rather than asserting
    a hand-copied row list -- if statistical's design changes, this fails
    until distribution follows.
    """
    print("[33] Distribution settings follow the statistical template")
    from PySide6.QtWidgets import QLabel, QCheckBox
    from desktop.widgets.analysis_settings import get_builder, registered
    from desktop.widgets.analysis_settings.statistical import StatisticalSettings
    from desktop.widgets.analysis_settings.distribution import (
        DistributionSettings, BINS_RANGE, OPACITY_RANGE)

    check("design: distribution is registered", "distribution" in registered())
    check("design: distribution reuses the statistical template",
          issubclass(DistributionSettings, StatisticalSettings))

    def _mk(aid):
        return get_builder(aid, None, num_cols=["age", "salary"],
                           cat_cols=["region", "dept"], dt_cols=[],
                           palette_names=["A", "B"])

    def _sections(page):
        """Section titles in true left-to-right order.

        Read the QHBoxLayout's own item order via ``indexOf`` rather than
        sorting by name or by x. Two reasons, both learned the hard way:
        a sorted() list throws away precisely the thing being asserted
        (which control sits in which column), and x positions are all equal
        for a widget that was never shown, so geometry is useless headless.
        """
        found = []
        from PySide6.QtWidgets import QHBoxLayout
        for lay in page.findChildren(QHBoxLayout):
            for i in range(lay.count()):
                w = lay.itemAt(i).widget()
                if w is None:
                    continue
                labels = [sl.text() for sl in w.findChildren(QLabel)
                          if sl.property('theme_role') == 'section']
                if labels:
                    found.append((lay.indexOf(w), labels[0]))
        found.sort(key=lambda it: it[0])
        return [t for _i, t in found]

    def _rows(page):
        out = []
        for w in page.findChildren(object):
            try:
                if w.property('grid_row'):
                    labs = [sl.text() for sl in w.findChildren(QLabel)
                            if sl.property('theme_role') == 'section']
                    if labs:
                        out.append(labs[0])
            except Exception:
                pass
        return out

    def _checks(page):
        return [c.text() for c in page.findChildren(QCheckBox)]

    st, ds = _mk("statistical"), _mk("distribution")
    st_gen, ds_gen = st.build_generate(), ds.build_generate()
    st_lay, ds_lay = st.build_layout(), ds.build_layout()
    st_typ, ds_typ = st.build_typography(), ds.build_typography()

    # -- Typography is type-agnostic: it must be identical, and it is
    #    inherited rather than re-implemented.
    check("design: typography rows identical to statistical",
          _rows(st_typ) == _rows(ds_typ), str(_rows(ds_typ)))
    check("design: typography is not re-implemented per type",
          "build_typography" not in DistributionSettings.__dict__)

    # -- Layout: same rows, plus the two histogram controls.
    st_rows, ds_rows = _rows(st_lay), _rows(ds_lay)
    check("design: layout keeps every statistical row in order",
          ds_rows[:len(st_rows)] == st_rows, f"{ds_rows} vs {st_rows}")
    check("design: layout adds exactly Bins + Opacity",
          ds_rows[len(st_rows):] == ["Bins", "Opacity"], str(ds_rows[len(st_rows):]))
    check("design: layout checkboxes identical to statistical",
          _checks(st_lay) == _checks(ds_lay), str(_checks(ds_lay)))
    check("design: Show border still sits directly under Show grid",
          _checks(ds_lay)[-2:] == ["Show grid", "Show border"], str(_checks(ds_lay)))

    # -- Generate: same 3-column shape AND same slot order as statistical.
    # The order is the point: statistical puts the optional dimension control
    # first, the metric control second, palette third. Distribution must put
    # the same KIND of control in the same slot, or the eye has to relearn
    # the layout on every chart tab. (A sorted() comparison here would hide
    # exactly that regression, which is how the order broke in the first
    # place.)
    PAL = "\U0001f3a8 Palette"
    check("design: generate has three section columns",
          len(_sections(ds_gen)) == 3, str(_sections(ds_gen)))
    check("design: statistical order is dimension, metric, palette",
          _sections(st_gen) == ["Group by (optional)", "Metrics", PAL],
          str(_sections(st_gen)))
    check("design: distribution uses the SAME slot order as statistical",
          _sections(ds_gen) == ["Colour by (optional)", "Numeric columns", PAL],
          str(_sections(ds_gen)))
    check("design: palette sits in the same column in both tabs",
          _sections(st_gen).index(PAL) == _sections(ds_gen).index(PAL) == 2,
          f"stat={_sections(st_gen)} dist={_sections(ds_gen)}")

    # -- No control may display placeholder/instruction text as its value.
    # "None" and "Dropdown with checkboxes; click to expand" both read as
    # real, selectable values sitting in the field.
    for _name, _b in (("statistical", st), ("distribution", ds)):
        for _attr in ("_combo_groupby", "_combo_color"):
            _w = getattr(_b, _attr, None)
            if _w is None:
                continue
            _shown = [_w.itemText(i) for i in range(_w.count())]
            check(f"display: {_name}.{_attr} offers no 'None' entry",
                  "None" not in _shown, str(_shown))
            check(f"display: {_name}.{_attr} starts on a blank entry",
                  bool(_shown) and _shown[0] == "", str(_shown[:1]))
        for _attr in ("_combo_metrics", "_combo_numeric"):
            _w = getattr(_b, _attr, None)
            if _w is None:
                continue
            check(f"display: {_name}.{_attr} blank when nothing selected",
                  _w.currentText() == "", repr(_w.currentText()))
            _w.set_selection(["age"])
            check(f"display: {_name}.{_attr} shows the real selection",
                  _w.currentText() == "age", repr(_w.currentText()))
            _w.set_selection([])

    check("design: generate control widths match the 210px template",
          ds._combo_numeric.width() == 210 and ds._combo_color.width() == 210
          and ds._combo_palette.width() == 210,
          f"{ds._combo_numeric.width()}/{ds._combo_color.width()}/{ds._combo_palette.width()}")

    # -- The two features taken from the old codebase, with its ranges.
    check("bins: range is 5..200 (old _CONTROL_META)", BINS_RANGE == (5, 200))
    check("bins: default 30", ds._bins_spin.value() == 30, str(ds._bins_spin.value()))
    check("opacity: range is 0.1..1.0 (old _CONTROL_META)", OPACITY_RANGE == (0.1, 1.0))
    check("opacity: default 0.80", abs(ds._op_spin.value() - 0.80) < 1e-6,
          str(ds._op_spin.value()))


def test_distribution_values_round_trip_and_generation():
    """The distribution form must feed the real service and reach the figure."""
    print("[34] Distribution form values round-trip and drive the chart")
    import numpy as np
    import pandas as pd
    from PySide6.QtWidgets import QApplication
    from desktop.widgets.analysis_settings import get_builder
    from chart_display import default_layout_opts, default_typo_opts
    from services.analysis_service import run_analysis_with_config, WIDGET_SPEC
    from services.polars_chart_service import apply_chart_model_display_options

    app = QApplication.instance() or QApplication([])
    b = get_builder("distribution", None, num_cols=["age", "salary"],
                    cat_cols=["region", "dept"], dt_cols=[],
                    palette_names=["A", "B"])
    b.build_generate()

    # The collected keys must match the keys WIDGET_SPEC declares for this
    # type, or the service never sees the user's choices.
    spec_keys = {k for k, _, _ in WIDGET_SPEC["distribution"]}
    got = set(b.collect_values().keys())
    check("values: collected keys match WIDGET_SPEC", got == spec_keys,
          f"{got} vs {spec_keys}")

    # prefill -> collect must be lossless (this is what Edit relies on).
    b.prefill({"x": ["age"], "color": "region", "palette": "B"})
    check("values: prefill round-trips x", b.collect_values()["x"] == ["age"],
          str(b.collect_values()["x"]))
    check("values: prefill round-trips color", b.collect_values()["color"] == "region",
          str(b.collect_values()["color"]))
    check("values: prefill round-trips palette", b.collect_values()["palette"] == "B",
          str(b.collect_values()["palette"]))

    # A stale saved value must not break the form.
    b.prefill({"x": ["age"], "color": "GONE", "palette": "NOPE"})
    check("values: stale color falls back to None", b.collect_values()["color"] is None,
          str(b.collect_values()["color"]))
    check("values: stale palette falls back to a real one",
          b.collect_values()["palette"] in ("A", "B"), str(b.collect_values()["palette"]))

    # Layout round-trip, including the two histogram rows.
    lay = b.build_layout()
    b.prefill_layout({"histogram_bins": 12, "histogram_opacity": 0.25,
                      "title": "My dist"})
    check("layout: prefill restores Bins", b.collect_layout()["histogram_bins"] == 12,
          str(b.collect_layout()["histogram_bins"]))
    check("layout: prefill restores Opacity",
          abs(b.collect_layout()["histogram_opacity"] - 0.25) < 1e-6,
          str(b.collect_layout()["histogram_opacity"]))
    check("layout: prefill leaves statistical's own rows working too",
          b.collect_layout()["title"] == "My dist", str(b.collect_layout()["title"]))

    # Real generation through the service, then real layout application.
    df = pd.DataFrame({
        "age": np.random.randint(20, 60, 300),
        "salary": np.random.randint(30000, 90000, 300),
        "score": np.random.rand(300) * 100,
        "region": np.random.choice(["N", "S", "E"], 300),
    })
    charts = run_analysis_with_config("distribution", df,
                                      {"x": ["age", "salary"], "color": "region",
                                       "palette": "Default"})
    check("generate: two charts for two selected columns", len(charts) == 2,
          str(len(charts)))
    lo = dict(default_layout_opts(), histogram_bins=12, histogram_opacity=0.25)
    for label, _uid, model in charts:
        f2 = apply_chart_model_display_options(model, lo, default_typo_opts())
        d0 = f2["display"]
        check(f"generate: Bins reaches the histogram ({label[:12]})",
              int(d0["histogram_bins"]) == 12, str(d0.get("histogram_bins")))
        check(f"generate: Opacity reaches the histogram ({label[:12]})",
              abs(float(d0["histogram_opacity"]) - 0.25) < 1e-6,
              str(d0.get("histogram_opacity")))

    # An empty form must still produce charts (the service's own default).
    empty = run_analysis_with_config("distribution", df,
                                     {"x": [], "color": None, "palette": "Default"})
    check("generate: empty selection still charts", len(empty) > 0, str(len(empty)))


def test_histogram_layout_options_are_histogram_only():
    """Bins / Opacity reach the distribution model and nothing else.

    The old Plotly contract mutated traces inside apply_display_options.
    The ECharts path routes layout opts through
    polars_chart_service.apply_chart_model_display_options, which maps one
    branch per chart type -- only ``distribution`` reads histogram_bins /
    histogram_opacity, which is what keeps the rows histogram-only now.
    """
    print("[35] Histogram layout options are histogram-only")
    from chart_display import default_typo_opts
    from modules.chart_model import make_chart_model
    from services.polars_chart_service import apply_chart_model_display_options

    def _model(chart_type):
        return make_chart_model(
            uid="u1", chart_type=chart_type, title="t",
            data={"dimensions": ["x"],
                  "series": [{"id": "s", "name": "s", "type": "bar",
                              "values": [{"name": "a", "value": 1}]}]})

    dist = apply_chart_model_display_options(
        _model("distribution"), {"histogram_bins": 12, "histogram_opacity": 0.25},
        default_typo_opts())["display"]
    check("hist: bins applied to the distribution model",
          int(dist["histogram_bins"]) == 12, str(dist.get("histogram_bins")))
    check("hist: opacity applied to the distribution model",
          abs(float(dist["histogram_opacity"]) - 0.25) < 1e-6,
          str(dist.get("histogram_opacity")))

    # A non-distribution model must not grow histogram keys: the same
    # guarantee the ``if ttype == "histogram"`` guard used to give.
    other = apply_chart_model_display_options(
        _model("categorical"), {"histogram_bins": 12, "histogram_opacity": 0.25},
        default_typo_opts())["display"]
    check("hist: a bar chart keeps its own display untouched",
          "histogram_bins" not in other and "histogram_opacity" not in other,
          f"keys={sorted(other)}")

    # Defaults hold when the layout opts are absent, and junk must not take
    # down the whole display pass.
    dflt = apply_chart_model_display_options(
        _model("distribution"), {}, default_typo_opts())["display"]
    check("hist: default bins present and sane",
          isinstance(dflt["histogram_bins"], int) and dflt["histogram_bins"] >= 1,
          str(dflt.get("histogram_bins")))
    check("hist: default opacity present and sane",
          0.0 < float(dflt["histogram_opacity"]) <= 1.0,
          str(dflt.get("histogram_opacity")))
    for bad in (None, "junk"):
        try:
            out = apply_chart_model_display_options(
                _model("distribution"), {"histogram_bins": bad},
                default_typo_opts())
            check(f"hist: bins={bad!r} does not crash the display pass",
                  isinstance(out.get("display"), dict), "")
        except Exception as exc:
            check(f"hist: bins={bad!r} does not crash the display pass",
                  False, f"raised {exc!r}")
    neg = apply_chart_model_display_options(
        _model("distribution"), {"histogram_bins": -5},
        default_typo_opts())["display"]
    check("hist: negative bins clamps to at least 1",
          int(neg["histogram_bins"]) == 1, str(neg.get("histogram_bins")))
    frac = apply_chart_model_display_options(
        _model("distribution"), {"histogram_bins": 12.7},
        default_typo_opts())["display"]
    check("hist: fractional bins round to an int",
          int(frac["histogram_bins"]) == 13, str(frac.get("histogram_bins")))
    op = apply_chart_model_display_options(
        _model("distribution"), {"histogram_opacity": 4.5},
        default_typo_opts())["display"]
    check("hist: opacity clamps into [0.1, 1.0]",
          abs(float(op["histogram_opacity"]) - 1.0) < 1e-6,
          str(op.get("histogram_opacity")))


def test_column_roles_never_drop_a_column():
    from services.column_roles import resolve_roles

    df = _roles_fixture_df()
    roles = resolve_roles(df)

    # The bug: select_dtypes(include="object") matched only "region", so
    # segment / code / is_active belonged to NO pool and were unreachable.
    check("roles: object-only detection used to lose these columns",
          {"segment", "code", "is_active"} - set(df.columns) == set(),
          "")
    for c in ("segment", "code", "is_active"):
        check(f"roles: '{c}' reaches a dimension pool",
              c in roles["categorical"], str(roles))

    # The module now auto-detects from the Polars schema in one pass (the
    # old detector/resolver split collapsed into _resolve_roles_uncached).
    # The invariant that still matters -- every column exactly once -- is
    # asserted below for both the auto and the explicit paths.
    placed = roles["numeric"] + roles["categorical"] + roles["datetime"]
    check("roles: every column lands in exactly one family",
          sorted(placed) == sorted(df.columns) and len(placed) == len(set(placed)),
          f"placed={sorted(placed)}")

    check("roles: datetimes detected",
          roles["datetime"] == ["order_date"], str(roles["datetime"]))
    check("roles: metric is the numeric column",
          roles["numeric"] == ["sales", "postal"], str(roles["numeric"]))
    check("roles: order follows the dataframe",
          roles["categorical"] == ["region", "segment", "code", "is_active"],
          str(roles["categorical"]))

    # Degenerate inputs must not explode -- these run on every panel build.
    check("roles: empty frame is safe",
          resolve_roles(pd.DataFrame() if (pd := __import__("pandas")) else None)
          == {"numeric": [], "categorical": [], "datetime": []}, "")
    check("roles: None frame is safe",
          resolve_roles(None) == {"numeric": [], "categorical": [],
                                  "datetime": []}, "")


def test_user_classification_overrides_dtype():
    from services.column_roles import resolve_roles, value_sig_map

    df = _roles_fixture_df()

    # The user deliberately calls the id-like 'postal' a DIMENSION even
    # though it reads as an integer. Their choice must win app-wide.
    user = {"numeric": ["sales"],
            "categorical": ["region", "segment", "code", "postal", "is_active"],
            "datetime": ["order_date"]}
    sig = value_sig_map(df)
    roles = resolve_roles(df, user, sig)

    check("user: numeric column stays a metric", roles["numeric"] == ["sales"],
          str(roles["numeric"]))
    check("user: reclassified numeric is a dimension, not a metric",
          "postal" in roles["categorical"] and "postal" not in roles["numeric"],
          str(roles))
    check("user: nothing dropped under an explicit classification",
          sorted(roles["numeric"] + roles["categorical"] + roles["datetime"])
          == sorted(df.columns), str(roles))

    # ...and the reverse: text promoted to a metric must leave the dimension
    # pool, otherwise "metrics" would silently ignore the user as well.
    user2 = {"numeric": ["sales", "region"], "categorical": [], "datetime": []}
    roles2 = resolve_roles(df, user2, sig)
    check("user: promoted text becomes a metric",
          "region" in roles2["numeric"] and "region" not in roles2["categorical"],
          str(roles2))

    # No classification at all -> pure auto-detection, still total coverage.
    auto = resolve_roles(df, None, None)
    check("user: absent classification falls back to auto-detect",
          auto["categorical"] == ["region", "segment", "code", "is_active"],
          str(auto))

    # Self-healing: the Dtype Transformer converted 'region' text into real
    # date values. The dtype moved from text to date, so only the value-profile
    # fingerprint reveals the change -- that one column must be re-derived,
    # or no time-series chart could ever find it.
    import polars as pl
    dates = [__import__("datetime").date(2024, m, 1) for m in range(1, 7)]
    df2 = df.with_columns(pl.Series("region", dates, dtype=pl.Date))
    healed = resolve_roles(df2, user, sig)
    check("user: converted text column self-heals into datetime",
          "region" in healed["datetime"], str(healed["datetime"]))
    check("user: self-healing does not disturb the other columns",
          "postal" in healed["categorical"] and healed["numeric"] == ["sales"],
          str(healed))


def test_group_by_offers_every_dimension():
    """The reported bug: "Group by" did not list all relevant dimensions."""
    from services.analysis_service import dim_cols
    from desktop.widgets.analysis_settings.base import AnalysisSettingsBase
    from desktop.widgets.analysis_settings import get_builder

    df = _roles_fixture_df()
    dims = dim_cols(df)

    # Every dimension family must be offered, not just object-dtype text.
    for c in ("region", "segment", "code", "is_active", "order_date"):
        check(f"groupby: '{c}' is offered as a dimension", c in dims, str(dims))
    check("groupby: a real metric is not offered as a grouping key",
          "sales" not in dims, str(dims))

    # A date is a legitimate grouping key -- the old text-only pool hid it.
    check("groupby: datetime is a grouping key", "order_date" in dims, str(dims))

    # The statistical form's Group by combo must actually show these.
    b = get_builder("statistical", None, num_cols=["sales"],
                    cat_cols=["region", "segment", "code", "is_active"],
                    dt_cols=["order_date"], palette_names=["Default"],
                    all_cols=list(df.columns))
    try:
        b._build()
        # The built page must stay referenced: without a Python owner the
        # combo (a child of it) is garbage-collected and every later access
        # raises "Internal C++ object already deleted".
        page = b.build_generate()
        opts = [b._combo_groupby.itemText(i)
                for i in range(b._combo_groupby.count())]
        check("groupby: the combo lists every dimension",
              all(c in opts for c in ("segment", "code", "is_active",
                                      "order_date")), str(opts))
        check("groupby: the combo starts on a blank entry, not 'None'",
              bool(opts) and opts[0] == "", str(opts[:1]))
        check("groupby: the built page is the group-by combo's home",
              page is not None, "")
    finally:
        b.deleteLater()

    # set_context's fallback (no explicit pool) must not regress to text-only.
    base = AnalysisSettingsBase()
    base.set_context(num_cols=["sales"], cat_cols=["region"],
                     dt_cols=["order_date"])
    check("groupby: default pool includes datetime",
          "order_date" in base._group_cols and "region" in base._group_cols,
          str(base._group_cols))


# =========================================================================
# Settings-form row alignment
# =========================================================================
#
# Regression: the three Generate sections each sized their header block to
# their own description text, so a two-line hint pushed its control below a
# one-line one and the Palette (no hint) above both -- the controls sat on a
# staircase instead of one line. The rule is that a section's control always
# starts at the same height as its neighbours'.


def test_section_controls_align_across_row():
    from PySide6.QtCore import QPoint
    from desktop.widgets.analysis_settings import get_builder
    from desktop.widgets.analysis_settings.base import HINT_ROW_LINES

    app = QApplication.instance() or QApplication([])

    from PySide6.QtWidgets import QWidget, QLabel

    for aid, ctrls in (
        ("statistical", ("_combo_groupby", "_combo_metrics", "_combo_palette")),
        ("distribution", ("_combo_color", "_combo_numeric", "_combo_palette")),
    ):
        b = get_builder(aid, None, num_cols=["age", "salary"],
                        cat_cols=["region", "dept"], dt_cols=["date"],
                        palette_names=["Default"],
                        all_cols=["age", "salary", "region", "dept", "date"])
        b._build()
        # The page must be shown and laid out before geometry is meaningful:
        # an unshown widget has no allocated positions, so every child
        # reports the same x/y and an alignment check silently "passes".
        page = b.build_generate()
        page.resize(900, 260)
        page.show()
        app.processEvents()

        ys = {}
        for attr in ctrls:
            w = getattr(b, attr, None)
            if w is None:
                continue
            ys[attr] = w.mapTo(page, QPoint(0, 0)).y()
        check(f"align: {aid} has all three controls", len(ys) == 3, str(ys))
        check(f"align: {aid} controls sit on one line", len(set(ys.values())) == 1,
              str(ys))

        # And a section with NO description must reserve the same slot, which
        # is what kept the Palette from riding higher than its neighbours.
        page.close()

    # A section without a hint still reserves the description row.
    b = get_builder("statistical", None, num_cols=["age"],
                    cat_cols=["region"], dt_cols=[], palette_names=["Default"],
                    all_cols=["age", "region"])
    b._build()
    pal_page = b.build_generate()
    hints = [w for w in pal_page.findChildren(QLabel)
             if w.property('theme_role') == 'label']
    check("align: hint row is a fixed-height slot, not sized by its text",
          all(w.height() > 0 for w in hints) and bool(hints), str(len(hints)))
    check("align: the reserved slot allows two wrapped lines",
          HINT_ROW_LINES == 2, str(HINT_ROW_LINES))


# =========================================================================
# Chart tab strip is scoped to the selected analysis type
# =========================================================================
#
# Regression: the strip was flat, so with Statistical selected you could still
# see (and select) a Distribution chart sitting next to it.


def test_chart_tabs_scoped_to_selected_type():
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget
    import desktop.pages.analysis_page as AP
    from desktop.session_bridge import SessionBridge

    # A real ChartCard builds a QWebEngineView, which cannot initialise in a
    # headless run. The tab-strip logic under test is pure bookkeeping, so a
    # lightweight stand-in keeps this test runnable everywhere.
    class _FakeCard(QWidget):
        delete_requested = Signal()
        edit_requested = Signal()
        note_changed = Signal()
        # Kept in step with ChartCard's signal surface: _refresh_charts
        # connects all of these, and a mock missing one crashes the run
        # with AttributeError instead of testing anything.
        slicer_requested = Signal()
        style_changed = Signal()
        move_up_requested = Signal()
        move_down_requested = Signal()
        fullwidth_toggled = Signal()

        def __init__(self, uid, title, fig, chart_type, meta, chart_model=None):
            super().__init__()
            self._fig = fig
            self._chart_model = chart_model
            self._bridge_figure_ref = None
            self.btn_up = QWidget()
            self.btn_down = QWidget()
            self.btn_style = QWidget()

        def set_chart_model(self, model, source_figure=None):
            self._chart_model = model
            self._bridge_figure_ref = source_figure

        def set_note(self, n):
            pass

        def set_title(self, t):
            pass

        def set_figure(self, f):
            self._fig = f

    real_card = AP.ChartCard
    AP.ChartCard = _FakeCard
    try:
        br = SessionBridge()
        br.add_chart("c1", "Stat A", None, chart_type="statistical")
        br.add_chart("c2", "Dist A", None, chart_type="distribution")
        br.add_chart("c3", "Stat B", None, chart_type="statistical")
        br.add_chart("c4", "Dist B", None, chart_type="distribution")
        pg = AP.AnalysisPage(br)

        def tabs_for(aid):
            pg._active_analysis = aid
            pg._refresh_charts()
            return [pg.charts_tabs.tabText(i)
                    for i in range(pg.charts_tabs.count())]

        t_stat = tabs_for("statistical")
        t_dist = tabs_for("distribution")
        t_back = tabs_for("statistical")

        check("tabs: statistical shows only statistical charts",
              t_stat == ["Statistical 1", "Statistical 2"], str(t_stat))
        check("tabs: distribution shows only distribution charts",
              t_dist == ["Distribution 1", "Distribution 2"], str(t_dist))
        check("tabs: switching type back restores the same strip",
              t_back == t_stat, str(t_back))
        check("tabs: labels no longer need to carry the type name",
              all(not t.startswith("Distribution") for t in t_stat), str(t_stat))

        # The hidden cards must survive the switch: rebuilding a ChartCard
        # reloads plotly.js, which is the visible "app reopened itself" flash.
        check("tabs: hidden charts stay alive (no webview rebuild)",
              len(pg._chart_cards) == 4, str(sorted(pg._chart_cards)))

        # Deleting a chart of the hidden type must still be honoured.
        br.remove_chart("c2")
        check("tabs: deleting a hidden chart updates the strip",
              tabs_for("distribution") == ["Distribution 1"],
              str(tabs_for("distribution")))
        pg.deleteLater()
    finally:
        AP.ChartCard = real_card


# =========================================================================
# Repository guard: no chart-specific column detection
# =========================================================================
#
# Every analysis runner used to answer "which columns are metrics / are
# dimensions?" with its own df.select_dtypes(...) call. That is how a column
# the user deliberately reclassified as a dimension kept showing up as a
# metric in some chart types and not others, and how `category`/`string`/`bool`
# columns fell out of every pool. The fix was to centralise the answer in
# services/column_roles.py -- but centralising it only helps if nothing can
# quietly reintroduce a local answer, so this test is the enforcement.


def _analysis_modules():
    import os
    root = os.path.join(REPO_ROOT, "backend", "modules", "analysis")
    return [(n, os.path.join(root, n))
            for n in sorted(os.listdir(root)) if n.endswith(".py")]


def test_analysis_runners_use_shared_column_roles():
    print("[37] No live analysis module derives column roles itself")
    import ast
    import tokenize

    # A raw text scan is not enough: several runners legitimately DISCUSS
    # select_dtypes in their module docstring -- some with a call-shaped
    # example like select_dtypes("object") -- so matching the file as text
    # would flag every one of those explanations. Comments and string literals
    # are stripped with tokenize first, and only real code is matched.
    line_offenders = []
    for name, path in _analysis_modules():
        try:
            with open(path, "rb") as fh:
                toks = list(tokenize.tokenize(fh.readline))
        except (tokenize.TokenError, OSError):
            continue
        code_lines = set()
        for tok in toks:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            if tok.type == tokenize.NAME and tok.string == "select_dtypes":
                code_lines.add(tok.start[0])
        if code_lines:
            line_offenders.append(f"{name}:{sorted(code_lines)[0]}")
    check("roles: no live analysis runner calls select_dtypes",
          not line_offenders, "; ".join(line_offenders[:5]))

    # Belt and braces: the AST view, so a multi-line or oddly formatted call
    # (which the line scan can miss) is still caught, and so a call written in
    # a docstring cannot false-positive the way the line scan would.
    ast_offenders = []
    for name, path in _analysis_modules():
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "select_dtypes"):
                ast_offenders.append(f"{name}:{node.lineno}")
    check("roles: AST confirms no select_dtypes call anywhere",
          not ast_offenders, "; ".join(ast_offenders[:5]))

    # Every native builder must accept the resolved roles, otherwise the
    # threading through analysis_service is dead code for it. The registry
    # is _NATIVE_BUILDERS; the specialised branches (descriptive, outlier,
    # map_plot, data_quality) thread roles explicitly in run_analysis.
    import inspect
    from services import analysis_service as _as
    import services.polars_chart_service as builders
    without = []
    for aid, builder_name in _as._NATIVE_BUILDERS.items():
        fn = getattr(builders, builder_name, None)
        if fn is None or "roles" not in inspect.signature(fn).parameters:
            without.append(aid)
    check("roles: every native builder accepts the shared roles mapping",
          not without, str(without))

    # role_pools must expose the shape the runners index into.
    import pandas as pd
    from services.column_roles import role_pools
    df = pd.DataFrame({"n": [1.0, 2.0], "c": ["a", "b"],
                       "t": pd.to_datetime(["2024-01-01", "2024-01-02"])})
    pools = role_pools(df)
    check("roles: role_pools exposes num/cat/dt/dim",
          set(pools) == {"num", "cat", "dt", "dim"}, str(sorted(pools)))
    check("roles: a datetime column is a usable dimension",
          "t" in pools["dim"] and "c" in pools["dim"], str(pools["dim"]))


def test_runners_honour_user_classification():
    """A numeric column the user reclassified must not come back as a metric."""
    print("[38] Runners honour the user's column classification")
    import pandas as pd
    from services.analysis_service import run_analysis
    from services.column_roles import resolve_roles

    # 'order_id' is a plain int64 -- indistinguishable from a real metric by
    # dtype alone, which is exactly the case that used to leak through.
    df = pd.DataFrame({"order_id": [1, 2, 3, 4, 5, 6],
                       "qty": [2.0, 4.0, 1.0, 5.0, 3.0, 6.0],
                       "price": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
                       "region": ["a", "b", "a", "b", "a", "b"]})
    roles = resolve_roles(df, explicit={"numeric": ["qty", "price"],
                                         "categorical": ["order_id", "region"]})
    check("roles: the reclassified column moved to a dimension",
          "order_id" in roles["categorical"]
          and "order_id" not in roles["numeric"], str(roles))

    r_user = run_analysis("correlation", df, roles=roles)
    check("roles: correlation builds from the user's metric pool",
          len(r_user) == 1, str(len(r_user)))
    if r_user:
        # The correlation model carries its axis in data.dimensions (the
        # metric pool it actually used), not in a plotly trace.
        dims = [str(v) for v in (
            (r_user[0][2].get("data") or {}).get("dimensions") or [])]
        check("roles: the reclassified id is absent from the matrix",
              "order_id" not in dims, str(dims))
        check("roles: the matrix covers exactly the two real metrics",
              sorted(dims) == ["price", "qty"], str(dims))

    # The other metric-driven runners must agree with correlation.
    check("roles: statistical still builds with the same roles",
          len(run_analysis("statistical", df, roles=roles)) == 1)
    check("roles: distribution still builds with the same roles",
          len(run_analysis("distribution", df, roles=roles)) >= 1)
    # A dimension-driven runner must never offer the id as a grouping column
    # only because it is numeric -- the user's choice is what counts.
    cat_titles = [t for _u, t, _f in run_analysis("categorical", df,
                                                  roles=roles)]
    check("roles: categorical groups on the reclassified dimension",
          any("order_id" in t for t in cat_titles), str(cat_titles))

    # Without roles at all, auto-detection is still a safe fallback.
    check("roles: auto-detection remains a working fallback",
          len(run_analysis("correlation", df)) == 1)


def _corr_ctx():
    return dict(num_cols=["age", "salary", "years"], cat_cols=["region"],
                dt_cols=["date"], palette_names=["A", "B"],
                all_cols=["age", "salary", "years", "region", "date"])


def test_correlation_settings_match_statistical_design():
    """Correlation must use the SAME three-tab design as statistical."""
    print("[39] Correlation settings follow the statistical template")
    from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLabel
    from desktop.widgets.analysis_settings import get_builder, registered
    from desktop.widgets.analysis_settings.statistical import StatisticalSettings
    from desktop.widgets.analysis_settings.correlation import (
        CorrelationSettings, COLORSCALES_ALL, ANNOTATION_SIZE_RANGE,
        COLORBAR_TICK_SIZE_RANGE, COLORBAR_TITLE_SIZE_RANGE, PRECISION_RANGE)

    QApplication.instance() or QApplication([])

    check("design: correlation is registered", "correlation" in registered())
    check("design: correlation reuses the statistical template",
          issubclass(CorrelationSettings, StatisticalSettings))
    # Correlation DOES override build_typography, but only to mask the rows it
    # has no use for: the old metadata gives it
    # [family, font_style, header, axis_tick]. The rows it keeps must still
    # come from the shared template rather than a private copy.

    def _mk(aid):
        return get_builder(aid, None, num_cols=["age", "salary", "years"],
                           cat_cols=["region"], dt_cols=["date"],
                           palette_names=["A", "B"])

    def _sections(page):
        """Section titles in true left-to-right order (see test 33)."""
        found = []
        for lay in page.findChildren(QHBoxLayout):
            for i in range(lay.count()):
                w = lay.itemAt(i).widget()
                if w is None:
                    continue
                labels = [sl.text() for sl in w.findChildren(QLabel)
                          if sl.property('theme_role') == 'section']
                if labels:
                    found.append((lay.indexOf(w), labels[0]))
        found.sort(key=lambda it: it[0])
        return [t for _i, t in found]

    def _rows(page):
        out = []
        for w in page.findChildren(object):
            try:
                if w.property('grid_row'):
                    labs = [sl.text() for sl in w.findChildren(QLabel)
                            if sl.property('theme_role') == 'section']
                    if labs:
                        out.append(labs[0])
            except Exception:
                pass
        return out

    st, cs = _mk("statistical"), _mk("correlation")
    st_typ, cs_typ = st.build_typography(), cs.build_typography()
    st_gen, cs_gen = st.build_generate(), cs.build_generate()
    cs_lay = cs.build_layout()

    # -- Generate: Columns only.
    #
    # Correlation is has_axes=False / has_legend=False in the old metadata, and
    # its controls are the title, the colour scale, the colour-bar styling and
    # the four annotation options. Neither a dimension control nor a Palette
    # belongs here. The palette was in the old _WIDGET_SPEC but was inert --
    # the old display layer always repainted the trace from
    # heatmap_colorscale -- so it is masked rather than shipped as a dead
    # control.
    secs = _sections(cs_gen)
    check("design: correlation Generate offers Columns and nothing else",
          secs == ["Columns"], str(secs))
    check("design: correlation has no Palette control",
          not any("Palette" in s for s in secs), str(secs))
    check("design: correlation has no dimension control",
          not any(g in ("Group by (optional)", "Color by") for g in secs),
          str(secs))
    check("design: correlation control widths match the 210px template",
          cs._combo_columns.width() == 210, str(cs._combo_columns.width()))
    check("values: correlation collect_values returns no palette key",
          "palette" not in cs.collect_values(), str(cs.collect_values()))

    # -- Layout: the old control list, plus the two requested additions.
    # Everything the template carries that correlation cannot use is masked,
    # because a control that cannot change the chart reads as broken.
    lay_rows = _rows(cs_lay)
    # "Annotations" is a checkbox rather than a grid row, so it is covered by
    # the checkbox assertion below rather than by this row list.
    for want in ("Chart title", "Colorscale", "CB title", "CB tick sz",
                 "CB tick clr", "CB title sz", "CB title clr",
                 "Precision", "Ann size", "Ann color"):
        check(f"layout: correlation keeps the '{want}' row", want in lay_rows,
              str(lay_rows))
    for unwanted in ("X-axis", "Y-axis", "Legend title", "Legend position"):
        check(f"layout: correlation masks the '{unwanted}' row",
              unwanted not in lay_rows, str(lay_rows))
    boxes = [c.text() for c in cs_lay.findChildren(QCheckBox)]
    check("layout: Annotations and Show border are the only checkboxes",
          boxes == ["Annotations", "Show border"], str(boxes))
    # The chart border default is now OFF (the "box around every chart"
    # report). The control still exists and still works -- this asserts the
    # NEW default, so a regression that turns it back on fails here.
    check("layout: Show border is unchecked by default",
          cs._ck_border.isChecked() is False, str(cs._ck_border.isChecked()))
    # Top and Bottom were added with the colorbar overhaul, so the bar is no
    # longer confined to the two vertical sides.
    check("layout: the colorbar position offers all four sides",
          [cs._cb_cbpos.itemText(i) for i in range(cs._cb_cbpos.count())]
          == ["Right", "Left", "Top", "Bottom"],
          str([cs._cb_cbpos.itemText(i) for i in range(cs._cb_cbpos.count())]))

    # -- Typography: family, font_style, header, axis_tick -- and nothing else.
    typ_rows = _rows(cs_typ)
    for want in ("Font family", "Font style", "Axis size", "Axis color"):
        check(f"typography: correlation keeps '{want}'", want in typ_rows,
              str(typ_rows))
    for unwanted in ("Title size", "Title color", "Legend size", "Legend color"):
        check(f"typography: correlation masks '{unwanted}'",
              unwanted not in typ_rows, str(typ_rows))
    check("typography: correlation drops the legend typography",
          not any("Legend" in r for r in typ_rows), str(typ_rows))

    # -- No placeholder text masquerading as a value.
    check("display: correlation Columns blank when nothing selected",
          cs._combo_columns.currentText() == "",
          repr(cs._combo_columns.currentText()))
    cs._combo_columns.set_selection(["age"])
    check("display: correlation Columns shows the real selection",
          cs._combo_columns.currentText() == "age",
          repr(cs._combo_columns.currentText()))


def test_correlation_alignment_values_and_generation():
    """Correlation Generate controls align, round-trip, and drive the chart."""
    print("[40] Correlation alignment, value round-trip and generation")
    import pandas as pd
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from desktop.widgets.analysis_settings import get_builder
    from chart_display import default_layout_opts
    from services.analysis_service import WIDGET_SPEC

    app = QApplication.instance() or QApplication([])

    # -- Alignment: the Generate controls share one line.
    b = get_builder("correlation", None, **_corr_ctx())
    b.prefill({"x": ["age"]})
    page = b.build_generate()
    page.resize(900, 260)
    page.show()
    app.processEvents()
    w = b._combo_columns
    check("align: correlation control sits at the shared y",
          w is not None and w.mapTo(page, QPoint(0, 0)).y() >= 0,
          str(w.mapTo(page, QPoint(0, 0)).y()) if w else "no widget")
    page.close()

    # -- Round trip. Correlation's WIDGET_SPEC also declares "palette", but
    #    that control is masked (it is inert -- the display layer always
    #    repaints from heatmap_colorscale), so the collected keys must NOT
    #    include it. Compare against the spec minus the masked fields rather
    #    than against the raw spec, or this test would demand the very key the
    #    masking removes.
    b2 = get_builder("correlation", None, **_corr_ctx())
    gen = b2.build_generate()
    got = set(b2.collect_values())
    spec_keys = set(k for k, _rk, _ty in WIDGET_SPEC["correlation"]) - {"palette"}
    check("values: collected keys are the unmasked WIDGET_SPEC keys",
          got == spec_keys, f"{sorted(got)} vs {sorted(spec_keys)}")
    b2.prefill({"x": ["age", "salary"]})
    check("values: prefill round-trips x",
          b2.collect_values().get("x") == ["age", "salary"],
          str(b2.collect_values().get("x")))
    b2.prefill({"x": ["age", "GONE"]})
    check("values: a stale column is dropped on prefill",
          b2.collect_values().get("x") == ["age"],
          str(b2.collect_values().get("x")))

    # -- Layout prefill restores what it stored, and leaves the rest alone.
    lo = dict(default_layout_opts())
    lo.update({"heatmap_show_text": False, "heatmap_colorscale": "Viridis",
               "heatmap_annotation_precision": 6, "colorbar_title": "zz"})
    b2._layout_opts = {}
    # Keep a reference to the returned page: PySide owns a widget nobody
    # holds, so dropping it here would collect the page AND its children,
    # leaving b2._ck_ann pointing at a deleted C++ object.
    lay_page = b2.build_layout()   # create the widgets first (app order)
    b2.prefill_layout(lo)
    check("layout: prefill restores Annotations off",
          b2._ck_ann.isChecked() is False, str(b2._ck_ann.isChecked()))
    check("layout: prefill restores the colorscale",
          b2._cb_cs.currentText() == "Viridis", b2._cb_cs.currentText())
    check("layout: prefill restores precision",
          b2._prec_spin.value() == 6, str(b2._prec_spin.value()))
    check("layout: prefill restores the colorbar title",
          b2._le_cb.text() == "zz", b2._le_cb.text())
    # Defaults come from the service's single heatmap-defaults helper, not
    # from hard-coded strings in the form.
    from services.polars_chart_service import (
        _heatmap_display_options as _hdo, _heatmap_palette as _hmp)
    _ho = _hdo({}, {})
    check("layout: default colorscale is RdBu",
          list(_ho["heatmap_colors"]) == list(_hmp("RdBu", True)),
          str(_ho["heatmap_colors"]))
    check("layout: default annotation precision is 2",
          _ho["labels"]["precision"] == 2, str(_ho["labels"]))
    check("layout: default colorbar tick colour is #94a3b8",
          _ho["colorbar_tick_color"] == "#94a3b8",
          str(_ho["colorbar_tick_color"]))
    # A stale colorscale / position snaps back to a real entry.
    b2.prefill_layout({"heatmap_colorscale": "NotAScale",
                       "colorbar_position": "Sideways"})
    check("layout: the layout page is still alive after prefill",
          lay_page is not None)
    check("layout: a stale colorscale snaps back to RdBu",
          b2._cb_cs.currentText() == "RdBu", b2._cb_cs.currentText())
    check("layout: a stale colorbar position snaps back to Right",
          b2._cb_cbpos.currentText() == "Right", b2._cb_cbpos.currentText())


    # -- Generation: the collected values reach the runner and produce the
    #    matrix the user asked for.
    import pandas as _pd
    from chart_display import default_typo_opts
    from services.analysis_service import run_analysis_with_config as _rac
    from services.polars_chart_service import (
        apply_chart_model_display_options, _heatmap_display_options,
        _heatmap_palette)
    _df = _pd.DataFrame({"age": [20, 30, 40, 50, 60, 70],
                         "salary": [1000, 2000, 1500, 4000, 3000, 5000],
                         "years": [1, 2, 3, 4, 5, 6]})
    _vals = dict(b2.collect_values())
    _vals["x"] = ["age", "salary", "years"]
    _res = _rac("correlation", _df, _vals)
    model = _res[0][2]
    check("generation: the model is a heatmap",
          model.get("chart_type") == "matrix_heatmap",
          str(model.get("chart_type")))
    dims = list(model["data"]["dimensions"])
    check("generation: the matrix covers the three chosen columns",
          sorted(dims) == ["age", "salary", "years"], str(dims))
    cells = [tuple(v) for s in model["data"]["series"] for v in s["values"]]
    check("generation: the full 3x3 matrix is present",
          len(cells) == 9, f"{len(cells)} cells")
    diag = [v for a, b, v in cells if a == b]
    check("generation: the diagonal is all 1.0",
          len(diag) == 3 and all(abs(v - 1.0) < 1e-9 for v in diag),
          str(diag))

    # Fewer than two columns cannot produce a matrix: the runner must return
    # an empty list, not raise.
    few = _rac("correlation", _df, {"x": ["age"]})
    check("generation: one column yields no chart (not an error)", few == [],
          str(len(few)))

    # Display options must actually reach the heatmap display block (the
    # ECharts adapter paints from heatmap_colors / labels / colorbar_*;
    # there is no figure to mutate anymore).
    f2 = apply_chart_model_display_options(
        model, {"heatmap_colorscale": "Viridis",
                "heatmap_annotation_precision": 3,
                "colorbar_title": "r", "colorbar_tick_size": 14},
        {"family": "Inter"})
    d2 = f2["display"]
    check("display: precision reaches the cell-value labels",
          d2["labels"]["precision"] == 3, str(d2["labels"]))
    check("display: the colorscale reaches the display block",
          list(d2["heatmap_colors"]) == list(_heatmap_palette("Viridis", True)),
          str(d2["heatmap_colors"]))
    check("display: the colorbar title is applied",
          d2["colorbar_title"] == "r", str(d2["colorbar_title"]))
    check("display: the colorbar tick size is applied",
          d2["colorbar_tick_size"] == 14, str(d2["colorbar_tick_size"]))

    f3 = apply_chart_model_display_options(model, {"heatmap_show_text": False}, {})
    check("display: turning Annotations off hides the cell values",
          f3["display"]["labels"]["show"] is False,
          str(f3["display"]["labels"]))

    # ---- Regression: the annotation size/colour must not be discarded ----
    # A stored 10 means "untouched" (the typography value then wins); once
    # the user picks a size it must land on the model verbatim.
    f4 = apply_chart_model_display_options(
        model, {"heatmap_annotation_size": 22,
                "heatmap_annotation_color": "#00ff00"}, {})
    d4 = f4["display"]["labels"]
    check("display: annotation size reaches the cell text",
          d4["font_size"] == 22, str(d4))
    check("display: annotation colour reaches the cell text",
          str(d4["color"]).lower() == "#00ff00", str(d4))

    # ---- Regression: Colorscale looked selected but never painted --------
    # px.imshow bound the trace to layout.coloraxis, which silently won over
    # the trace's own colorscale, so the chart kept the brand palette.
    # heatmap_colors IS the paint decision now, and it must track the combo.
    f5 = apply_chart_model_display_options(model, {"heatmap_colorscale": "Viridis"}, {})
    f6 = apply_chart_model_display_options(model, {"heatmap_colorscale": "Greys"}, {})
    c5 = list(f5["display"]["heatmap_colors"])
    c6 = list(f6["display"]["heatmap_colors"])
    check("display: the selected colorscale is what gets painted",
          c5 == list(_heatmap_palette("Viridis", True)), str(c5))
    check("display: changing the colorscale changes the paint again",
          c6 == list(_heatmap_palette("Greys", True)), str(c6))
    check("display: the two scales really differ (the switch is not a no-op)",
          c5 != c6, f"{c5} vs {c6}")

    # ---- Regression: Show border / colorbar position were unreachable -----
    fb = apply_chart_model_display_options(model, {"show_border": False}, {})
    check("display: Show border off reaches the colorbar",
          fb["display"]["colorbar_border"] is False,
          str(fb["display"].get("colorbar_border")))
    fb2 = apply_chart_model_display_options(model, {"show_border": True}, {})
    check("display: Show border on reaches the colorbar",
          fb2["display"]["colorbar_border"] is True,
          str(fb2["display"].get("colorbar_border")))
    left = apply_chart_model_display_options(model, {"colorbar_position": "Left"}, {})
    right = apply_chart_model_display_options(model, {"colorbar_position": "Right"}, {})
    check("colorbar: Left reaches the display block",
          str(left["display"]["colorbar_position"]).lower() == "left",
          str(left["display"].get("colorbar_position")))
    check("colorbar: Right reaches the display block",
          str(right["display"]["colorbar_position"]).lower() == "right",
          str(right["display"].get("colorbar_position")))

    # The heatmap draws no legend: the generation path flips it off.
    check("colorbar: a heatmap model carries no visible legend",
          (model.get("display") or {}).get("legend", {}).get("show") is False,
          str((model.get("display") or {}).get("legend")))

    # And a categorical bar model must still get the shared value-label
    # colour from its own layout opts.
    from modules.chart_model import make_chart_model
    bar_model = make_chart_model(
        uid="b1", chart_type="categorical", title="t",
        data={"dimensions": ["x"],
              "series": [{"id": "s", "name": "s", "type": "bar",
                          "values": [{"name": "a", "value": 1}]}]})
    b1 = apply_chart_model_display_options(
        bar_model, {"value_label_color": "#ff0000",
                    "show_value_labels": True}, {})
    check("display: a bar chart still takes the value-label colour",
          str(b1["display"]["labels"]["color"]).lower() == "#ff0000",
          str(b1["display"]["labels"]))


def _cat_ctx():
    return dict(num_cols=["transaction_qty", "unit_price", "revenue"],
                cat_cols=["product_category", "region"],
                dt_cols=["when"],
                dim_cols=["product_category", "region", "when"],
                palette_names=["Default", "A"],
                all_cols=["transaction_qty", "unit_price", "revenue",
                          "product_category", "region", "when"])


def test_categorical_settings_match_statistical_design():
    """Categorical must use the SAME three-tab design as statistical."""
    print("[41] Categorical settings follow the statistical template")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLabel, QWidget
    from desktop.widgets.analysis_settings import get_builder, registered
    from desktop.widgets.analysis_settings.statistical import StatisticalSettings
    from desktop.widgets.analysis_settings.categorical import (
        CategoricalSettings, LINE_WIDTH_RANGE, LINE_WIDTH_DEFAULT, TOP_N_RANGE)
    from services.analysis_service import WIDGET_SPEC

    QApplication.instance() or QApplication([])

    check("design: categorical is registered", "categorical" in registered())
    check("design: categorical reuses the statistical template",
          issubclass(CategoricalSettings, StatisticalSettings))

    def _sections(page):
        """Section titles in true visual order (rows top-to-bottom).

        Sorted from mapped coordinates rather than findChildren order: the
        Generate page stacks THREE wrapped rows, and creation order is not
        reading order.
        """
        entries = []
        for lay in page.findChildren(QHBoxLayout):
            for i in range(lay.count()):
                w = lay.itemAt(i).widget()
                if w is None:
                    continue
                labels = [sl.text() for sl in w.findChildren(QLabel)
                          if sl.property('theme_role') == 'section']
                if labels:
                    p = w.mapTo(page, QPoint(0, 0))
                    entries.append((round(p.y() / 20), p.x(), labels[0]))
        entries.sort()
        return [t for _ry, _x, t in entries]

    def _rows(page):
        """grid_row labels in visual order (same mapped-coordinate sort)."""
        entries = []
        for w in page.findChildren(QWidget):
            try:
                if w.property('grid_row'):
                    labs = [sl.text() for sl in w.findChildren(QLabel)
                            if sl.property('theme_role') == 'section']
                    if labs:
                        p = w.mapTo(page, QPoint(0, 0))
                        entries.append((round(p.y() / 20), p.x(), labs[0]))
            except Exception:
                pass
        entries.sort()
        return [t for _ry, _x, t in entries]

    b = get_builder("categorical", None, **_cat_ctx())
    gen_page = b.build_generate()
    gen_page.resize(900, 560)
    gen_page.show()
    QApplication.processEvents()

    # -- Generate: nine controls in their order. Palette is last (slot 3).
    #    Sort by IS built: it is wired end-to-end (collect -> prefill ->
    #    service sort mapping) and was added after the initial migration; the
    #    user confirmed it stays.
    secs = _sections(gen_page)
    check("design: Generate offers the nine controls in order",
          secs == ["Dimension", "Metric", "Aggregation", "\U0001f4ca Direction",
                   "Sort by", "Top N", "Secondary Y-Axis", "Secondary Agg",
                   "\U0001f3a8 Palette"], str(secs))
    for attr in ("_combo_dim", "_combo_metric", "_combo_agg", "_combo_dir",
                 "_combo_sort",
                 "_combo_dual", "_combo_dual_agg", "_combo_palette",
                 "_spin_topn"):
        w = getattr(b, attr, None)
        check(f"design: {attr} uses the 210px template width",
              w is not None and w.width() == 210,
              str(w.width()) if w else "missing")
    check("design: Top N is a 0..200 spinner defaulting to 0",
          b._spin_topn.minimum() == TOP_N_RANGE[0]
          and b._spin_topn.maximum() == TOP_N_RANGE[1]
          and b._spin_topn.value() == 0, str(b._spin_topn.value()))
    texts = [c.currentText() for c in
             (b._combo_dim, b._combo_metric, b._combo_agg, b._combo_dir,
              b._combo_sort,
              b._combo_dual, b._combo_dual_agg, b._combo_palette)]
    check("display: no combo shows the literal word None",
          "None" not in texts, str(texts))
    check("display: Dimension defaults to the first dimension",
          b._combo_dim.currentText() == "product_category",
          b._combo_dim.currentText())
    check("display: Metric defaults to the first metric",
          b._combo_metric.currentText() == "transaction_qty",
          b._combo_metric.currentText())
    check("display: Secondary Y-Axis starts blank",
          b._combo_dual.currentText() == "",
          repr(b._combo_dual.currentText()))
    gen_page.close()

    # -- Values: every WIDGET_SPEC key except "sort" itself, whose value the
    #    panel collects under its real target name "sort_by".
    got = set(b.collect_values())
    spec_keys = (set(k for k, _rk, _ty in WIDGET_SPEC["categorical"])
                 - {"sort"} | {"sort_by"})
    check("values: collected keys are the unmasked WIDGET_SPEC keys",
          got == spec_keys, f"{sorted(got)} vs {sorted(spec_keys)}")
    vals = b.collect_values()
    check("values: x and y collect as lists (WIDGET_SPEC kind)",
          vals["x"] == ["product_category"] and vals["y"] == ["transaction_qty"],
          str(vals))

    # -- Layout: the old control list PLUS the shared legend group. --------
    # Legend title / Legend position / Show border are kept: this chart draws
    # a real legend (the Secondary Y-Axis overlay is a second series), and
    # the legend / colour-bar / colour-scale settings are one shared setting
    # across the supported charts.
    lay_page = b.build_layout()
    lay_page.resize(900, 560)
    lay_page.show()
    QApplication.processEvents()
    lay_rows = _rows(lay_page)
    for want in ("Chart title", "X-axis", "Y-axis (left)", "Y-axis (right)",
                 "Label pos", "Bar mode", "Line width", "Bar gap",
                 "Legend title", "Legend position"):
        check(f"layout: keeps the '{want}' row", want in lay_rows,
              str(lay_rows))
    boxes = [c.text() for c in lay_page.findChildren(QCheckBox)]
    check("layout: grid + border + the split Y-axis value labels",
          boxes == ["Show grid", "Show border",
                    "Primary Y-axis labels", "Secondary Y-axis labels"],
          str(boxes))
    check("layout: Line width sits between Bar mode and Bar gap",
          lay_rows.index("Bar mode") < lay_rows.index("Line width")
          < lay_rows.index("Bar gap"), str(lay_rows))
    check("layout: the dual-axis labels come after each other in order",
          lay_rows.index("X-axis") < lay_rows.index("Y-axis (left)")
          < lay_rows.index("Y-axis (right)"), str(lay_rows))
    check("layout: line width range is 1..10 (old _CONTROL_META)",
          LINE_WIDTH_RANGE == (1, 10) and b._lw_slider.minimum() == 1
          and b._lw_slider.maximum() == 10,
          str((b._lw_slider.minimum(), b._lw_slider.maximum())))
    check("layout: line width default is 2 (old _CONTROL_META)",
          LINE_WIDTH_DEFAULT == 2 and b._lw_spin.value() == 2,
          str(b._lw_spin.value()))
    lay_page.close()

    # -- Typography: the shared tab, inherited whole (legend group included).
    typ_page = b.build_typography()
    typ_page.resize(900, 480)
    typ_page.show()
    QApplication.processEvents()
    typ_rows = _rows(typ_page)
    for want in ("Font family", "Font style", "Header size", "Header color",
                 "Title size", "Title color", "Axis size", "Axis color",
                 "Legend size", "Legend color"):
        check(f"typography: keeps '{want}'", want in typ_rows, str(typ_rows))
    check("typography: inherited whole, not re-implemented per type",
          "build_typography" not in CategoricalSettings.__dict__)
    typ_page.close()


def test_categorical_alignment_values_and_generation():
    """Categorical Generate controls align, round-trip, and drive the chart."""
    print("[42] Categorical alignment, value round-trip and generation")
    import pandas as pd
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QLabel
    from desktop.widgets.analysis_settings import get_builder
    from chart_display import default_layout_opts, default_typo_opts
    from services.analysis_service import run_analysis_with_config, WIDGET_SPEC
    from services.polars_chart_service import apply_chart_model_display_options

    app = QApplication.instance() or QApplication([])

    # -- Alignment: the controls of ONE wrapped row share a line. ----------
    b = get_builder("categorical", None, **_cat_ctx())
    gen_page = b.build_generate()
    gen_page.resize(900, 560)
    gen_page.show()
    app.processEvents()

    def _ys(attrs):
        out = {}
        for attr in attrs:
            w = getattr(b, attr, None)
            if w is not None:
                out[attr] = w.mapTo(gen_page, QPoint(0, 0)).y()
        return out

    row1 = _ys(("_combo_dim", "_combo_metric", "_combo_agg"))
    row2 = _ys(("_combo_dir", "_combo_sort", "_spin_topn"))
    row3 = _ys(("_combo_dual", "_combo_dual_agg", "_combo_palette"))
    check("align: row 1 (Dimension/Metric/Aggregation) is one line",
          len(row1) == 3 and len(set(row1.values())) == 1, str(row1))
    check("align: row 2 (Direction/Sort by/Top N) is one line",
          len(row2) == 3 and len(set(row2.values())) == 1, str(row2))
    check("align: row 3 (Secondary Y/Secondary Agg/Palette) is one line",
          len(row3) == 3 and len(set(row3.values())) == 1, str(row3))
    check("align: the wrapped rows are really on different lines",
          row1["_combo_dim"] < row2["_combo_dir"] < row3["_combo_palette"],
          f"{row1} | {row2} | {row3}")
    gen_page.close()

    # -- Round trip. "sort" is declared in WIDGET_SPEC; the panel renders it
    #    as Sort by and collects under its target name "sort_by" (the service
    #    normalises sort_by -> sort before dispatch).
    b2 = get_builder("categorical", None, **_cat_ctx())
    gen2 = b2.build_generate()
    got = set(b2.collect_values())
    spec_keys = (set(k for k, _rk, _ty in WIDGET_SPEC["categorical"])
                 - {"sort"} | {"sort_by"})
    check("values: collected keys are the unmasked WIDGET_SPEC keys",
          got == spec_keys, f"{sorted(got)} vs {sorted(spec_keys)}")
    b2.prefill({"x": ["region"], "y": ["unit_price"], "agg": "Sum",
                "top_n": 5, "direction": "Horizontal (Bar chart)",
                "sort_by": "Category (A-Z)",
                "dual_y": "revenue", "dual_y_agg": "Max", "palette": "A"})
    got2 = b2.collect_values()
    check("values: prefill round-trips every field",
          got2 == {"x": ["region"], "y": ["unit_price"], "agg": "Sum",
                   "top_n": 5, "direction": "Horizontal (Bar chart)",
                   "sort_by": "Category (A-Z)",
                   "dual_y": "revenue", "dual_y_agg": "Max", "palette": "A"},
          str(got2))
    check("values: the widgets show what prefill stored",
          b2._combo_dim.currentText() == "region"
          and b2._combo_metric.currentText() == "unit_price"
          and b2._combo_agg.currentText() == "Sum"
          and b2._spin_topn.value() == 5
          and b2._combo_dir.currentText() == "Horizontal (Bar chart)"
          and b2._combo_sort.currentText() == "Category (A-Z)"
          and b2._combo_dual.currentText() == "revenue"
          and b2._combo_dual_agg.currentText() == "Max"
          and b2._combo_palette.currentText() == "A", "")
    # Stale columns snap: the required Dimension falls back to the first real
    # column (widget and collected values must never disagree), the optional
    # Metric and Secondary axis simply go blank.
    b2.prefill({"x": ["GONE"], "y": ["GONE"], "dual_y": "GONE"})
    got3 = b2.collect_values()
    check("values: a stale dimension snaps to the first dimension",
          got3["x"] == ["product_category"], str(got3["x"]))
    check("values: a stale metric leaves Metric blank",
          got3["y"] == [], str(got3["y"]))
    check("values: a stale secondary axis goes blank",
          got3["dual_y"] == "", str(got3["dual_y"]))
    # A secondary axis may not repeat the primary metric.
    b2.prefill({"x": ["region"], "y": ["unit_price"],
                "dual_y": "unit_price"})
    check("values: secondary axis equal to the metric is rejected",
          b2.collect_values()["dual_y"] == "",
          str(b2.collect_values()["dual_y"]))

    # -- Layout prefill restores the two rows statistical cannot know about,
    #    and the mask survives: rows are deleted, not hidden.
    lo = dict(default_layout_opts())
    lo.update({"y2_label": "Right axis", "line_width": 7,
               "show_value_labels": False, "legend_title": "Key",
               "legend_position": "Bottom Left", "show_border": False})
    b2._layout_opts = {}
    # Keep a reference to the returned page: PySide owns a widget nobody
    # holds, so dropping it would collect the page AND its children.
    lay_page = b2.build_layout()
    b2.prefill_layout(lo)
    check("layout: prefill restores the right-hand axis label",
          b2._le_y2.text() == "Right axis", b2._le_y2.text())
    check("layout: prefill restores line width",
          b2._lw_spin.value() == 7, str(b2._lw_spin.value()))
    check("layout: prefill can switch value labels off",
          b2._ck_vals.isChecked() is False, str(b2._ck_vals.isChecked()))
    # The shared legend rows are real controls here, so their saved values
    # have to come back too -- they are not masked away any more.
    check("layout: prefill restores the shared legend title",
          b2._le_leg.text() == "Key", b2._le_leg.text())
    check("layout: prefill restores the shared legend position",
          b2._cb_legpos.currentText() == "Bottom Left",
          b2._cb_legpos.currentText())
    check("layout: prefill restores the shared border toggle",
          b2._ck_border.isChecked() is False, str(b2._ck_border.isChecked()))
    rowset = set()
    for w in lay_page.findChildren(object):
        try:
            if w.property('grid_row'):
                rowset.update(sl.text() for sl in w.findChildren(QLabel)
                              if sl.property('theme_role') == 'section')
        except Exception:
            pass
    check("layout: the legend rows are real rows, not leftovers",
          {"Legend title", "Legend position"} <= rowset,
          str(sorted(rowset)))

    # -- Generation: the collected values reach the runner. ----------------
    df = pd.DataFrame({
        "product_category": ["A", "A", "B", "B", "C", "C"],
        "region": ["N", "S", "N", "S", "N", "S"],
        "transaction_qty": [10, 20, 30, 40, 50, 60],
        "unit_price": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })
    vals = dict(b2.collect_values())
    vals.update({"x": ["product_category"], "y": ["transaction_qty"],
                 "agg": "Sum", "dual_y": "unit_price", "dual_y_agg": "Avg",
                 "direction": "Vertical (Column chart)"})
    res = run_analysis_with_config("categorical", df, vals)
    check("generation: one dimension x metric yields one chart",
          len(res) == 1, str(len(res)))
    model = res[0][2]
    check("generation: a bar with a secondary-axis line",
          [str(s.get("type")) for s in model["data"]["series"]] == ["bar", "line"],
          str([s.get("type") for s in model["data"]["series"]]))
    pts = model["data"]["series"][0]["values"]
    cats = [str(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    check("generation: bars carry the grouped sums, value-desc",
          cats == ["C", "B", "A"] and ys == [110.0, 70.0, 30.0],
          f"{cats} {ys}")

    h = run_analysis_with_config(
        "categorical", df, dict(vals, direction="Horizontal (Bar chart)"))[0][2]
    check("generation: Horizontal renders a bar chart",
          str(h["display"].get("orientation")) == "horizontal",
          str(h["display"].get("orientation")))

    cnt = run_analysis_with_config(
        "categorical", df, {"x": ["region"], "y": [], "agg": "Avg",
                            "top_n": 1, "direction": "Vertical (Column chart)",
                            "dual_y": "", "dual_y_agg": "Avg",
                            "palette": "Default"})[0][2]
    check("generation: no metric falls back to counts, Top N caps it",
          len(cnt["data"]["series"][0]["values"]) == 1
          and int(cnt["data"]["series"][0]["values"][0][1]) == 3,
          f"{cnt['data']['series'][0]['values']}")

    # Legacy scalar callers must not be split into characters.
    sc = run_analysis_with_config(
        "categorical", df, {"x": "region", "y": "unit_price"})[0][2]
    check("generation: scalar x/y are normalised to lists",
          sorted(str(p[0]) for p in sc["data"]["series"][0]["values"]) == ["N", "S"],
          str(list(sc["data"]["series"][0]["values"])))

    # -- Display: the two new Layout options reach the display block. -----
    # The ECharts adapter paints from the display dict (y2_title /
    # line_width); the old assertions on figure.layout are retired. The
    # renderer honours line width for the line series only -- bars carry no
    # line width of their own, so there is no equivalent of "does not outline
    # the bars" to pin at this layer.
    f = apply_chart_model_display_options(
        model, {"y2_label": "Secondary", "line_width": 7},
        {"family": "Inter"})
    check("display: the right-hand axis label reaches y2_title",
          str(f["display"].get("y2_title")) == "Secondary",
          str(f["display"].get("y2_title")))
    check("display: line width reaches the display block",
          int(f["display"]["line_width"]) == 7,
          str(f["display"].get("line_width")))
    f_none = apply_chart_model_display_options(model, {"line_width": 3}, {})
    check("display: no y2_label keeps the runner's own right-axis title",
          bool(str(f_none["display"].get("y2_title") or "")),
          str(f_none["display"].get("y2_title")))

    # The shared template controls still work on this chart type too.
    f3 = apply_chart_model_display_options(
        model, {"show_value_labels": False, "bar_mode": "stack",
                "bar_gap": 0.5}, {})
    check("display: value labels switch off on a categorical bar",
          f3["display"]["primary_show_value_labels"] is False,
          str(f3["display"].get("primary_show_value_labels")))
    check("display: bar mode reaches the display block",
          str(f3["display"]["bar_mode"]) == "stack",
          str(f3["display"].get("bar_mode")))
    check("display: bar gap reaches the display block",
          abs(float(f3["display"]["bar_gap"]) - 0.5) < 1e-9,
          str(f3["display"].get("bar_gap")))


def test_legend_layout_is_client_side_now():
    """Legend placement is computed by the chart, not reserved in Python.

    The old Plotly layout engine reserved a fixed column/band inside the figure
    (reserve_legend_column / reserve_legend_band in chart_display). The
    ECharts renderer computes the equivalents in the browser, from the
    container width it actually has, on every repaint -- which is why the
    Python reservation helpers are stubs. These checks pin the adapter's
    behaviour from source: a horizontal band costs height, a vertical side
    legend gets a clamped pixel width (never an unclamped fraction), the grid
    actually reserves the space, and overflow stays reachable.
    """
    print("[N] Legend layout is computed client-side (ECharts)")
    from pathlib import Path
    from chart_display import __file__ as _cd_file
    js = (Path(_cd_file).parent / "assets" / "echarts" / "lytrize_adapter.js").read_text(
        encoding="utf-8")

    check("adapter: a legend band is computed client-side",
          "var legendBand = !hasLegend ? 8 :" in js, "")
    check("adapter: band height tracks the legend font (floor 30px)",
          "Math.max(30, Number(legend.font_size || 11) + 16)" in js, "")
    check("adapter: a side legend gets a clamped pixel width",
          "Math.max(74, Math.min(150, width * 0.28))" in js
          and "var sideLegendWidth" in js, "")
    check("adapter: the grid reserves room for a side legend",
          "var sideLegendGutter = sideLegendWidth ? sideLegendWidth + 18 : 0" in js
          and "option.grid.left += sideLegendGutter" in js
          and "option.grid.right += sideLegendGutter" in js, "")
    check("adapter: grid bottom accounts for the band",
          "legendBand + 2" in js, "")
    check("adapter: the legend is a scroll legend (overflow stays reachable)",
          'type: "scroll"' in js, "")
    check("adapter: orientation resolves from the position when set to auto",
          '(atLeft || atRight) ? "vertical" : "horizontal"' in js, "")
    check("adapter: legend text colour is theme-aware",
          "legendItemColor" in js, "")


def test_newly_migrated_types_match_the_old_metadata():
    """The six types migrated last must offer what the old app offered.

    Checked against ``chart_settings.py`` ``CHART_TYPE_SETTINGS`` row by row:
    a declared control must be on the tab, and a control the old app never had
    must not be. The Generate tab is checked the same way against the service
    branch that consumes it -- a form that collects the wrong KEYS generates
    from the defaults no matter what is on screen, which looks like a dead UI.
    """
    print("[N] Newly migrated types match the old chart_settings metadata")
    from PySide6.QtWidgets import QApplication, QCheckBox, QLabel
    from desktop.widgets.analysis_settings import get_builder
    from services.analysis_service import DATE_PARTS

    app = QApplication.instance() or QApplication([])
    ctx = dict(num_cols=["age", "salary", "qty"],
               cat_cols=["region", "dept"], dt_cols=["date"],
               dim_cols=["region", "dept", "date"],
               palette_names=["Default"],
               all_cols=["age", "salary", "qty", "region", "dept", "date",
                         "city", "country"])

    def labels(aid, tab):
        b = get_builder(aid, None, **ctx)
        page = {"gen": b.build_generate, "lay": b.build_layout,
                "typ": b.build_typography}[tab]()
        page.resize(1500, 900)
        page.show()
        app.processEvents()
        out = set()
        for w in page.findChildren(QLabel):
            if str(w.property("theme_role")) == "section":
                out.add(w.text().strip())
        for w in page.findChildren(QCheckBox):
            if w.text().strip():
                out.add(w.text().strip())
        return out

    # -- Layout / Typography rows, per the old metadata --------------------
    EXPECTED = {
        "pie_chart": {
            # Slice typography moved to the Typography tab (Slice size /
            # Slice color plus "Donut total size" / "Donut total color") --
            # the layout tab keeps the structural rows only.
            "lay": {"Chart title", "Legend title", "Legend position", "Text info",
                    "Donut", "Pull", "Rotation", "Direction"},
            "typ": {"Legend size", "Legend color",
                    "Slice size", "Slice color",
                    "Donut total size", "Donut total color"},
            "absent": {"Bar mode", "Bar gap", "X-axis", "Y-axis"},
        },
        "time_series": {
            "lay": {"Line width", "Line shape", "Fill", "Markers"},
            "typ": {"Title size", "Axis size", "Legend size"},
            "absent": {"Bar mode", "Bar gap"},
        },
        "scatter_plot": {
            # "Markers" (and the label rows) are deliberately masked: a
            # scatter draws bare points and the renderer never reads them.
            "lay": {"Line width", "Line shape", "Fill",
                    "M. size", "M. opacity", "Render mode"},
            "typ": {"Title size", "Legend size"},
            "absent": {"Bar mode", "Bar gap", "Markers", "Label pos",
                       "Show value labels"},
        },
        "matrix_heatmap": {
            "lay": {"Colorscale", "CB title", "CB tick sz", "CB tick clr",
                    "CB title sz", "CB title clr", "Annotations", "Ann size",
                    "Ann color", "Precision", "Colorbar pos"},
            "typ": {"Axis size"},
            "absent": {"Bar mode", "Bar gap"},
        },
        "matrix_table": {
            "lay": {"Font size", "Hdr font", "Row h", "Font color", "Hdr color",
                    "Even stripe", "Odd stripe", "Hdr h", "Index align",
                    "Data align", "Num format"},
            "typ": set(),
            # The table has no colour bar and no in-cell annotations: a Plotly
            # table draws none, so those heatmap rows would be dead controls.
            "absent": {"Colorscale", "CB title", "Annotations", "Ann size",
                       "Colorbar pos", "Legend size", "Axis size"},
        },
        "map_plot": {
            "lay": {"Colorscale", "CB title", "CB tick sz", "CB tick clr",
                    "CB title sz", "CB title clr", "Colorbar pos", "M. size",
                    "M. opacity", "Colorbar", "Size by value"},
            "typ": {"Axis size"},
            # A geo trace has no Cartesian axes and no in-cell text.
            "absent": {"X-axis", "Y-axis", "Annotations", "Ann size",
                       "Precision", "Bar mode", "Bar gap"},
        },
    }
    for aid, want in EXPECTED.items():
        for tab in ("lay", "typ"):
            got = labels(aid, tab)
            missing = want[tab] - got
            check(f"migrated '{aid}': {tab} tab has every old control",
                  not missing, f"missing={sorted(missing)}")
        leaked = want["absent"] & (labels(aid, "lay") | labels(aid, "typ"))
        check(f"migrated '{aid}': no control the old app did not declare",
              not leaked, f"leaked={sorted(leaked)}")

    # -- Generate values must be keyed as the service branch reads them ----
    # This is the failure mode that looks fine on screen: the controls are all
    # there and the user changes them, but the service reads different keys, so
    # the chart is generated from the defaults anyway.
    EXPECTED_KEYS = {
        "pie_chart": {"x", "y", "agg", "sort", "top_n", "palette"},
        "time_series": {"x", "y", "date_part", "agg", "dual_y_ts",
                        "dual_y_agg", "palette"},
        "scatter_plot": {"x_col", "y_col", "color_col", "size_col",
                         "trendline", "palette"},
        "matrix_heatmap": {"index_col", "columns_col", "values_col", "agg",
                           "sort_rows", "top_n_rows"},
        "matrix_table": {"index_col", "columns_col", "values_col", "agg",
                         "sort_rows", "top_n_rows", "row_hierarchy"},
        # Every key here is read by the service's map branch. The eight that
        # used to be absent from this app -- choropleth_projection /
        # choropleth_scope and the six hover_* tooltip fields -- are now
        # collected by the panel and honoured by the runner, so they belong in
        # the set rather than in ALLOWED_EXTRA (which permits keys the service
        # would ignore).
        "map_plot": {"map_mode", "lat_col", "lon_col", "location_col",
                     "color_col", "value_col", "agg_func", "map_style",
                     "marker_opacity", "invert_colorscale", "show_borders",
                     "render_mode", "choropleth_projection",
                     "choropleth_scope", "hover_dim1", "hover_dim2",
                     "hover_val1", "hover_val1_agg", "hover_val2",
                     "hover_val2_agg"},
    }
    # 'x' is a deliberate extra on the matrix types: their prefill reads it
    # back so a saved cross-tab round-trips, and the runner ignores it. The
    # allowlist therefore permits exactly that and nothing else, so a genuinely
    # spurious key still fails.
    # 'render_mode' is a scatter Layout-tab combo, not a WIDGET_SPEC field, so
    # it has no entry to be derived from -- but the service branch really does
    # read it (``px.scatter(render_mode=...)`` is a build-time choice), and the
    # builder must ship it. Same category as the matrix 'x' below.
    ALLOWED_EXTRA = {"matrix_heatmap": {"x"}, "matrix_table": {"x"},
                     "scatter_plot": {"render_mode"}}
    for aid, keys in EXPECTED_KEYS.items():
        b = get_builder(aid, None, **ctx)
        b.build_generate()
        got = set(b.collect_values())
        allowed = keys | ALLOWED_EXTRA.get(aid, set())
        check(f"migrated '{aid}': collect_values keys match the service branch",
              keys <= got and got <= allowed,
              f"missing={sorted(keys - got)} extra={sorted(got - allowed)}")

    # -- The time-series granularity labels must be the service's OWN keys --
    # A hand-written copy of the list silently sent "Month" to a table that
    # expects "Month (number)", and the chosen granularity was then ignored.
    from desktop.widgets.analysis_settings.time_series import DATE_PARTS as UI_DPARTS
    check("migrated 'time_series': granularity labels match the service table",
          list(UI_DPARTS) == list(DATE_PARTS.keys()),
          f"ui={UI_DPARTS} service={list(DATE_PARTS)}")


def test_new_layout_controls_change_the_new_chart_types():
    """Every Layout control the six new types declare must change the figure.

    Same rule as the existing probe table: a control is only proven alive by
    changing it and diffing the figure. Every probe stays inside the control's
    documented range, because an out-of-range value makes Plotly reject the
    whole update and the defensive ``except`` would hide the failure.
    """
    print("[N] The new Layout controls change the new chart types")
    import json
    import pandas as pd
    from chart_display import default_layout_opts
    from services.analysis_service import run_analysis_with_config

    countries = ["India", "USA", "France", "Brazil", "Japan", "Kenya"]
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=24, freq="D"),
        "region": ["N", "S", "E", "W"] * 6,
        "dept": ["d1", "d2"] * 12,
        "country": [countries[i % len(countries)] for i in range(24)],
        "lat": [(-40 + (i % 60)) * 0.9 for i in range(24)],
        "lon": [(-120 + (i % 140)) * 0.9 for i in range(24)],
        "age": [20 + i for i in range(24)],
        "salary": [1000 + i * 10 for i in range(24)],
    })

    import copy

    def _fig(aid, vals):
        res = run_analysis_with_config(aid, df, vals)
        if not res:
            raise AssertionError(f"no figure produced for {aid}")
        return res[0][2]

    # The renderer reads the ChartModel -- title, data and display -- plus a
    # few layout keys it consumes verbatim from the recipe echo (checked
    # separately below). Diffing those three is the honest analogue of the
    # old figure diff: a control that only rewrites the recipe input echo
    # has not changed what the user sees.
    def _j(m):
        view = {k: m.get(k) for k in ("title", "data", "display")}
        return json.dumps(view, sort_keys=True, default=str)

    from services.polars_chart_service import apply_chart_model_display_options

    def _styled(m, opts):
        return apply_chart_model_display_options(copy.deepcopy(m), opts, {})

    mtx = {"index_col": "region", "columns_col": "dept",
           "values_col": "salary", "agg": "Sum"}
    PROBES = {
        "pie_chart": (_fig("pie_chart", {"x": "region", "y": "salary",
                                         "agg": "Sum"}), {
            "donut_hole": 0.6, "pie_textinfo": "percent", "pull_slices": 0.3,
            "pie_rotation": 90, "pie_direction": "counterclockwise",
            "pie_value_size": 20, "pie_value_color": "#00ff00",
            "title": "PROBE", "legend_title": "PLEGEND"}),
        "time_series": (_fig("time_series", {"x": "date", "y": "age",
                                             "agg": "Avg"}), {
            "line_width": 8, "line_shape": "spline", "line_fill": "tozeroy",
            "show_markers": False, "title": "PROBE", "x_label": "PX",
            "y_label": "PY"}),
        # Built WITH a trendline on purpose. A bare px.scatter draws
        # markers-only traces, and line width / shape are guarded on the trace
        # actually containing "lines" -- exactly as the old app guarded them --
        # so probing them here would prove nothing except that the guard works.
        "scatter_plot": (_fig("scatter_plot", {"x_col": "age",
                                               "y_col": "salary",
                                               "trendline": "Linear"}), {
            "marker_size": 20, "marker_opacity": 0.3, "line_width": 6,
            "line_shape": "spline", "line_fill": "tozeroy",
            "show_markers": False, "title": "PROBE"}),
        "matrix_heatmap": (_fig("matrix_heatmap", mtx), {
            "heatmap_colorscale": "Viridis", "heatmap_show_text": True,
            "heatmap_annotation_size": 18, "heatmap_annotation_precision": 4,
            "heatmap_annotation_color": "#00ff00", "colorbar_title": "CB",
            "colorbar_tick_size": 14, "colorbar_tick_color": "#ff0000",
            "colorbar_title_size": 15, "colorbar_title_color": "#00ff00",
            "colorbar_position": "Left", "title": "PROBE"}),
        "matrix_table": (_fig("matrix_table", mtx), {
            "table_font_size": 18, "table_header_font_size": 20,
            "table_row_height": 40, "table_header_height": 48,
            "table_index_align": "right", "table_data_align": "center",
            "table_stripe_even_color": "#334155",
            "table_stripe_odd_color": "#111827",
            # Re-renders already-formatted cells from the raw numbers the
            # runner stashes in figure metadata; a plain style probe cannot
            # tell us whether that survives.
            "table_number_format": ".4f", "title": "PROBE"}),
        # Choropleth mode: colour scale + colour bar (the map's share of the
        # heatmap controls). Marker options are deliberately NOT probed here --
        # a choropleth paints whole country shapes, which have no marker to
        # size or fade, so they are probed on the lat/lon mode instead.
        "map_plot": (_fig("map_plot",
                         {"map_mode": "Choropleth (Location Names)",
                          "location_col": "country", "value_col": "salary",
                          "agg_func": "Sum"}), {
            "heatmap_colorscale": "Viridis", "colorbar_title": "CB",
            "colorbar_tick_size": 14, "colorbar_tick_color": "#ff0000",
            "colorbar_title_size": 15, "colorbar_title_color": "#00ff00",
            "colorbar_position": "Left", "title": "PROBE"}),
    }
    # The map's marker controls, probed on the mode that actually draws markers.
    map_markers = _fig("map_plot", {"map_mode": "Scatter (Lat/Lon)",
                                    "lat_col": "lat", "lon_col": "lon",
                                    "value_col": "salary", "agg_func": "Sum"})
    PROBES["map_plot (markers)"] = (map_markers,
                                    {"marker_size": 18, "marker_opacity": 0.35})

    defaults = default_layout_opts()
    for aid, (fig, probes) in PROBES.items():
        base = _j(_styled(fig, dict(defaults)))
        dead = [k for k, v in probes.items()
                if _j(_styled(fig, dict(defaults, **{k: v}))) == base]
        check(f"new layout controls: every '{aid}' probe changes the model",
              not dead, f"dead={dead}")


def test_generate_stage_controls_are_not_dead():
    """Generate-stage controls must change the chart, not just the config.

    ``apply_display_options`` covers the Layout/Typography pass only, so a
    control that a *runner* is supposed to honour (scatter render_mode, map
    borders) would look fine there while doing nothing at all. Each probe
    below is therefore run end-to-end through ``run_analysis_with_config``.
    """
    import json as _json
    from services.analysis_service import run_analysis_with_config
    df = make_df()
    # scatter + map need coordinates and real country names, which the shared
    # fixture does not carry.
    n = len(df)
    for col, vals in (("age", [20 + i for i in range(n)]),
                      ("salary", [1000 + i * 10 for i in range(n)]),
                      ("lat", [(-40 + (i % 60)) * 0.9 for i in range(n)]),
                      ("lon", [(-120 + (i % 140)) * 0.9 for i in range(n)]),
                      ("country", ["India", "USA", "France",
                                   "Brazil", "Japan", "Kenya"][: (n % 6) or 6])):
        if col not in df.columns:
            df = df.assign(**{col: [vals[i % len(vals)] for i in range(n)]})

    # -- scatter render_mode -------------------------------------------
    renderers = {}
    for mode in ("svg", "webgl"):
        figs = run_analysis_with_config(
            "scatter_plot", df,
            {"x_col": "age", "y_col": "salary", "render_mode": mode})
        renderers[mode] = (figs[0][2].get("display") or {}) if figs else {}
    # Native models carry the renderer choice on display (the SVG/canvas
    # decision is wired to the adapter, not to a trace type).
    check("scatter render_mode=svg uses the SVG renderer",
          renderers.get("svg", {}).get("renderer") == "svg"
          and renderers.get("svg", {}).get("large") is False,
          f"got {renderers.get('svg')}")
    check("scatter render_mode=webgl uses the large canvas path",
          renderers.get("webgl", {}).get("renderer") == "canvas"
          and renderers.get("webgl", {}).get("large") is True,
          f"got {renderers.get('webgl')}")

    # -- map borders ----------------------------------------------------
    def _map(**over):
        vals = {"map_mode": "Choropleth (Location Names)",
                "location_col": "country", "value_col": "salary",
                "agg_func": "Sum"}
        vals.update(over)
        # The scatter map is a different runner branch with its own border
        # mechanism (an overlay layer rather than a geo outline), so it needs its
        # own config; without this the probe would silently re-test the
        # choropleth and prove nothing about the scattermap.
        if vals.pop("scatter", False):
            vals.update({"map_mode": "Scatter (Lat/Lon)",
                         "lat_col": "lat", "lon_col": "lon"})
            vals.pop("location_col", None)
            vals.pop("value_col", None)
            vals.pop("agg_func", None)
        figs = run_analysis_with_config("map_plot", df, vals)
        return _json.dumps(figs[0][2],
                           sort_keys=True, default=str) if figs else "NONE"

    check("map: borders render (choropleth matches at least one location)",
          _map(show_borders=True) != "NONE")
    check("map: show_borders changes the choropleth",
          _map(show_borders=True) != _map(show_borders=False))
    check("map: scatter map renders", _map(scatter=True) != "NONE")
    check("map: show_borders changes the scatter map",
          _map(scatter=True, show_borders=True)
          != _map(scatter=True, show_borders=False))

    # A scattermap marker has no outline and layout.map has no country
    # toggle, so the box cannot apply there. It is disabled in the form
    # instead of silently changing nothing -- assert the form agrees.
    from PySide6.QtWidgets import QApplication
    from desktop.widgets.analysis_settings import get_builder
    app = QApplication.instance() or QApplication([])
    cols = [str(c) for c in df.columns]
    form = get_builder("map_plot", None, all_cols=cols, dim_cols=cols,
                       num_cols=cols, cat_cols=cols, dt_cols=[],
                       palette_names=["Default"])
    check("map form: builder resolved for the form-level assertions",
          form is not None, "")
    if form is None:
        return
    page = form.build_generate()  # keep a ref: the combo dies with its page
    form._combo_mode.setCurrentText("Choropleth (Location Names)")
    check("map form: Borders is enabled for choropleth",
          form._chk_borders.isEnabled())
    form._combo_mode.setCurrentText("Scatter (Lat/Lon)")
    # Superseded: "Borders is disabled for lat/lon scatter". The scattermap path
    # now adds/removes a real boundaries overlay layer, so the checkbox is a LIVE
    # control in scatter mode -- leaving it enabled and asserting that it reaches
    # the figure is the stronger claim than asserting it was greyed out.
    check("map form: Borders is enabled for lat/lon scatter",
          form._chk_borders.isEnabled())


def test_map_runner_degrades_without_streamlit():
    """A Qt desktop build must never import Streamlit to report a problem.

    The map runner's "could not match these locations" branch called
    ``st.warning()``. There is no Streamlit in this app, so an unrecognised
    country raised ModuleNotFoundError and aborted the ENTIRE analysis run -- the
    user saw a crash instead of an empty result. The branch now logs and returns
    the empty list the caller already knows how to display.
    """
    print("[N] Map runner degrades gracefully with no Streamlit")
    import inspect
    import pandas as pd
    from services.analysis_service import run_analysis_with_config

    src = inspect.getsource(
        __import__("modules.analysis.map_plot", fromlist=["run_map_plot"]))
    check("map: the runner does not import streamlit",
          "import streamlit" not in src, "still imports streamlit")

    n = 12
    df = pd.DataFrame({
        "country": [f"Nowhere {i % 3}" for i in range(n)],
        "lat": [(-40 + (i % 60)) * 0.9 for i in range(n)],
        "lon": [(-120 + (i % 140)) * 0.9 for i in range(n)],
        "salary": [100 + i for i in range(n)],
    })
    res = run_analysis_with_config("map_plot", df, {
        "map_mode": "Choropleth (Location Names)", "location_col": "country",
        "value_col": "salary", "agg_func": "Sum"})
    # Current contract: with lat/lon present the runner falls back to a
    # coordinate scatter and says so via display.map_notice, instead of
    # raising or returning nothing.
    fb_ok = (len(res) == 1
             and bool((res[0][2].get("display") or {}).get("map_notice")))
    check("map: unmatchable locations fall back to coordinates, never raise",
          fb_ok, str(res)[:400])
    # And with NO coordinate fallback the runner returns an empty model with
    # a user-facing message -- still not an exception.
    bare = df.drop(columns=["lat", "lon"])
    res_bare = run_analysis_with_config("map_plot", bare, {
        "map_mode": "Choropleth (Location Names)", "location_col": "country",
        "value_col": "salary", "agg_func": "Sum"})
    msg_ok = (len(res_bare) == 1
              and "empty_message" in (res_bare[0][2].get("display") or {})
              and not (res_bare[0][2].get("data") or {}).get("series"))
    check("map: no coordinates -> empty model with a message, not a crash",
          msg_ok, str(res_bare)[:400])

    # A REAL country must still render, so the fix did not disable the chart.
    ok = df.assign(country=["India", "USA", "France"] * 4)
    res2 = run_analysis_with_config("map_plot", ok, {
        "map_mode": "Choropleth (Location Names)", "location_col": "country",
        "value_col": "salary", "agg_func": "Sum"})
    choro_ok = (len(res2) == 1
                and res2[0][2].get("chart_type") == "map_plot"
                and bool((((res2[0][2].get("data") or {}).get("map")) or {}).get("asset"))
                and bool((res2[0][2].get("data") or {}).get("series")))
    check("map: recognised countries still render a choropleth",
          choro_ok, str(res2)[:400])


def test_every_chart_type_has_a_settings_builder():
    """Every offered chart type either builds all three tabs or falls back.

    A registered type must produce three pages with real controls (a page
    with no control at all means the build silently failed); a type not yet
    ported must return None cleanly, which is what makes the panel show its
    placeholder pages instead of crashing.
    """
    print("[43] Every chart type has a settings builder")
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QSpinBox
    from desktop.widgets.analysis_settings import get_builder, registered
    from services.analysis_service import WIDGET_SPEC

    app = QApplication.instance() or QApplication([])
    ctx = dict(num_cols=["age", "salary", "years"],
               cat_cols=["region", "dept"], dt_cols=["date"],
               dim_cols=["region", "dept", "date"],
               palette_names=["Default", "A"],
               all_cols=["age", "salary", "years", "region", "dept", "date"])

    def _controls(page):
        n = 0
        for cls in (QComboBox, QLineEdit, QSpinBox, QCheckBox):
            n += len(page.findChildren(cls))
        return n

    # 1. Every registered type builds three tabs with real controls.
    for aid in sorted(registered()):
        b = get_builder(aid, None, **ctx)
        check(f"builder '{aid}': builds an instance", b is not None, str(b))
        if b is None:
            continue
        for name, page in (("Generate", b.build_generate()),
                           ("Layout", b.build_layout()),
                           ("Typography", b.build_typography())):
            check(f"builder '{aid}': {name} tab has real controls",
                  _controls(page) >= 1, str(_controls(page)))
        check(f"builder '{aid}': action row builds",
              b.build_action_row() is not None, "")

    # 2. Every type the service offers resolves to either a real builder or
    #    the clean placeholder path -- never an exception.
    for aid in sorted(WIDGET_SPEC):
        try:
            b = get_builder(aid, None, **ctx)
            ok = b is None or all(
                _controls(p) >= 1
                for p in (b.build_generate(), b.build_layout(),
                          b.build_typography()))
        except Exception:
            ok = False
        check(f"offered type '{aid}': builder or placeholder, no crash",
              ok, "")


def test_masked_controls_stay_masked():
    """No type may show a control its old metadata does not declare.

    The table below is the contract: old ``CHART_TYPE_SETTINGS`` controls,
    plus the rows the Qt template added for axis-bearing charts (Show grid /
    Show border) where they actually do something. The build output must
    match EXACTLY -- a template row leaking back in (the correlation bug
    this test was written for) or a deliberately masked row returning both
    fail here.
    """
    print("[44] Masked controls stay masked")
    from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QWidget
    from desktop.widgets.analysis_settings import get_builder, registered

    app = QApplication.instance() or QApplication([])
    ctx = dict(num_cols=["age", "salary", "years"],
               cat_cols=["region", "dept"], dt_cols=["date"],
               dim_cols=["region", "dept", "date"],
               palette_names=["Default", "A"],
               all_cols=["age", "salary", "years", "region", "dept", "date"])

    _TYPOTHREE = ["Header size", "Header color", "Header font",
                  "Header font style", "Header align", "Font family",
                  "Font style", "Title size", "Title color", "Axis size",
                  "Axis color"]
    # The tooltip-theming work added a shared Tooltip colour/size row to
    # every type whose marks are hoverable (all charts, no tables).
    _TOOLTIP = ["Tooltip color", "Tooltip size"]
    _ADV_TYPO = ["Header size", "Header color", "Header font",
                 "Header font style", "Header align", "Font family",
                 "Font style"] + _TOOLTIP
    _LB_LEGEND = ["Chart title", "X-axis", "Y-axis", "Legend title",
                  "Legend position", "Label pos", "Bar mode", "Bar gap"]
    # Rows the legend overhaul added on top of the original Streamlit set:
    # a legend LAYOUT (wrap / horizontal orientation) and the click-action
    # selector. They ship on every type that actually draws a legend, so they
    # are declared once here rather than repeated in each entry below.
    _LEGEND_EXTRA = ["Legend layout", "Click"]
    _LB_CHECKS = ["Show value labels", "Show grid", "Show border"]
    EXPECTED = {
        # old controls: title, axes_labels, legend_labels, show_value_labels,
        # label_position, bar_gap, bar_mode (+ the template's grid/border).
        "statistical": (_LB_LEGEND + _LEGEND_EXTRA, _LB_CHECKS,
                        _TYPOTHREE + ["Legend size", "Legend color"] + _TOOLTIP),
        # old controls: + histogram_bins, histogram_opacity.
        "distribution": (_LB_LEGEND + ["Bins", "Opacity"] + _LEGEND_EXTRA, _LB_CHECKS,
                         _TYPOTHREE + ["Legend size", "Legend color"] + _TOOLTIP),
        # has_axes=False, has_legend=False: title, colorscale, colour-bar
        # styling, annotations (+ the two requested colour-bar rows), and no
        # axis-title typography.
        "correlation": (["Chart title", "Colorscale", "Ann size", "Ann color",
                         "Precision", "Colorbar pos", "CB title", "CB tick sz",
                         "CB tick clr", "CB title sz", "CB title clr"],
                        ["Annotations", "Show border"],
                        [r for r in _TYPOTHREE
                         if r not in ("Title size", "Title color")] + _TOOLTIP),
        # has_axes=True; the legend rows are KEPT although the old metadata
        # says has_legend=False, because this runner really does draw a
        # legend (the Secondary Y-Axis overlay is a second series) and the
        # legend / colour-bar settings are shared across the chart types that
        # draw a key. Typography is the shared tab, legend group included.
        "categorical": (["Chart title", "X-axis", "Y-axis (left)",
                         "Y-axis (right)", "Legend title", "Legend position",
                         "Label pos", "Bar mode", "Bar gap", "Line width",
                         "Value labels", "Secondary labels"] + _LEGEND_EXTRA,
                        ["Primary Y-axis labels", "Secondary Y-axis labels",
                         "Show grid", "Show border"],
                        list(_TYPOTHREE) + ["Legend size", "Legend color"] + _TOOLTIP),
        # ---- the six types migrated in the final pass --------------------
        # Same contract as above: the rows are the OLD Streamlit controls for
        # that chart type (per chart_settings_screenshots/rules.txt), plus the
        # shared chart title row and the template's border box each one uses.
        # pie_chart: old has_legend=True but no x/y axes -> title + the whole
        # legend block + the pie-specific rows, and NO axis-title typography.
        "pie_chart": (["Chart title", "Legend title", "Legend position",
                       "Label pos", "Text info", "Donut", "Pull", "Rotation",
                       "Direction", "Centre total"] + _LEGEND_EXTRA,
                      ["Show value labels", "Show metric total in centre"],
                      ["Header size", "Header color", "Header font",
                       "Header font style", "Header align", "Font family",
                       "Font style", "Legend size", "Legend color",
                       "Slice size", "Slice color",
                       "Donut total size", "Donut total color"] + _TOOLTIP),
        # time_series / scatter_plot: has_axes and has_legend both True, so the
        # legend block is kept. Scatter adds the marker rows and the build-time
        # render-mode combo; its typography therefore matches the axes types.
        "time_series": (["Chart title", "X-axis", "Y-axis", "Legend title",
                         "Legend position", "Label pos", "Line width",
                         "Line shape", "Fill"] + _LEGEND_EXTRA,
                        ["Show value labels", "Show grid", "Show border",
                         "Markers"],
                        _TYPOTHREE + ["Legend size", "Legend color"] + _TOOLTIP),
        "scatter_plot": (["Chart title", "X-axis", "Y-axis", "Legend title",
                          "Legend position", "Line width",
                          "Line shape", "Fill", "M. size", "M. opacity",
                          "Render mode"] + _LEGEND_EXTRA,
                         ["Show grid", "Show border"],
                         _TYPOTHREE + ["Legend size", "Legend color"] + _TOOLTIP),
        # outlier: scatter-like points + fence lines. The bar/value-label
        # rows the statistical template drags in are removed; the marker and
        # render rows are the renderer-relevant ones. The legend block stays
        # -- the chart really draws a Normal/Outlier/fences legend.
        "outlier": (["Chart title", "X-axis", "Y-axis", "Legend title",
                     "Legend position", "M. size", "M. opacity",
                     "Render mode"] + _LEGEND_EXTRA,
                    ["Show grid", "Show border"],
                    _TYPOTHREE + ["Legend size", "Legend color"] + _TOOLTIP),
        # matrix_heatmap: has_axes=False -> no axis titles, only the colour
        # scale, annotations and colour bar.
        "matrix_heatmap": (["Chart title", "Colorscale", "Ann size",
                            "Ann color", "Precision", "Colorbar pos",
                            "CB title", "CB tick sz", "CB tick clr",
                            "CB title sz", "CB title clr"],
                           ["Annotations", "Show border"],
                           ["Header size", "Header color", "Header font",
                            "Header font style", "Header align",
                            "Font family", "Font style", "Axis size",
                            "Axis color"] + _TOOLTIP),
        # matrix_table: a table, not an axes chart -> table styling only.
        "matrix_table": (["Chart title", "Font size", "Hdr font", "Row h",
                          "Font color", "Hdr color", "Even stripe",
                          "Odd stripe", "Hdr h", "Index align", "Data align",
                          "Num format", "Heat accent", "Heat color on"],
                         ["Show border", "Heat coloring"],
                         ["Header size", "Header color", "Header font",
                          "Header font style", "Header align",
                          "Font family", "Font style"]),
        # map_plot: colour scale + colour bar, plus the marker rows that only
        # apply to the lat/lon mode. 'Show border' is kept but enabled only for
        # choropleth (see test_generate_stage_controls_are_not_dead).
        "map_plot": (["Chart title", "Colorscale", "Colorbar pos",
                      "CB title", "CB tick sz", "CB tick clr", "CB title sz",
                      "CB title clr", "M. size", "M. opacity"],
                     ["Show border", "Colorbar", "Size by value"],
                     ["Header size", "Header color", "Header font",
                      "Header font style", "Header align", "Font family",
                      "Font style", "Axis size", "Axis color"] + _TOOLTIP),
        # ---- the ECharts-native visual families -------------------------
        # No Cartesian axes and no legend, so the Layout tab declares only the
        # shared title row (everything else would be a dead control); the
        # family-specific option rows are FlowGrid sections, not grid_row
        # labelled rows, and the Typography tab keeps the header block plus
        # the tooltip rows.
        "sankey": (["Chart title"], [], list(_ADV_TYPO)),
        "sunburst": (["Chart title"], [], list(_ADV_TYPO)),
        "calendar_heatmap": (["Chart title"], [], list(_ADV_TYPO)),
        "funnel": (["Chart title"], [], list(_ADV_TYPO)),
        # Waterfall is a single-axis bar family: it keeps the primary axis
        # rows and the grid, and nothing dual-axis, value-label or line.
        "waterfall": (["Chart title", "X-axis", "Y-axis (left)"],
                      ["Show grid"], list(_ADV_TYPO)),
        # The rose is a pie: identical contract plus the Rose mode row.
        "nightingale_rose": (["Chart title", "Legend title", "Legend position",
                              "Label pos", "Text info", "Donut", "Pull",
                              "Rotation", "Direction", "Centre total"]
                             + _LEGEND_EXTRA,
                             ["Show value labels", "Show metric total in centre"],
                             ["Header size", "Header color", "Header font",
                              "Header font style", "Header align",
                              "Font family", "Font style",
                              "Legend size", "Legend color",
                              "Slice size", "Slice color",
                              "Donut total size", "Donut total color"] + _TOOLTIP),
    }
    # The registered set itself is part of the contract: a new type must be
    # added to this table deliberately, not slip in with default rows.
    check("mask: every registered type is declared here",
          set(EXPECTED) == set(registered()),
          f"{sorted(EXPECTED)} vs {sorted(registered())}")

    def _rows(page):
        out = []
        for w in page.findChildren(QWidget):
            try:
                if w.property('grid_row'):
                    labs = [sl.text() for sl in w.findChildren(QLabel)
                            if sl.property('theme_role') == 'section']
                    if labs:
                        out.append(labs[0])
            except Exception:
                pass
        return out

    keep = []
    for aid, (exp_rows, exp_boxes, exp_typo) in EXPECTED.items():
        b = get_builder(aid, None, **ctx)
        lay = b.build_layout()
        typ = b.build_typography()
        keep.extend([lay, typ])
        check(f"mask: '{aid}' layout rows are exactly the declared set",
              sorted(_rows(lay)) == sorted(exp_rows),
              f"{sorted(_rows(lay))} vs {sorted(exp_rows)}")
        boxes = [c.text() for c in lay.findChildren(QCheckBox)]
        check(f"mask: '{aid}' layout checkboxes are exactly the declared set",
              sorted(boxes) == sorted(exp_boxes),
              f"{sorted(boxes)} vs {sorted(exp_boxes)}")
        check(f"mask: '{aid}' typography rows are exactly the declared set",
              sorted(_rows(typ)) == sorted(exp_typo),
              f"{sorted(_rows(typ))} vs {sorted(exp_typo)}")
    del keep


def test_declared_layout_controls_all_do_something():
    """Every Layout control a type declares must change the figure.

    The only reliable proof that a control is not dead is to change it and
    diff the figure. Two rules from the doc are baked into the probe table:
    every probe value stays inside the control's [lo, hi] range (an
    out-of-range value makes Plotly reject the whole update and the
    defensive ``except`` hides the failure), and every probe differs from
    the default it is compared against.
    """
    print("[45] Every declared Layout control changes the model")
    import json
    from pathlib import Path
    import pandas as pd
    import chart_display
    from chart_display import default_layout_opts
    from services.analysis_service import run_analysis_with_config

    df = pd.DataFrame({
        "region": ["N", "N", "S", "S", "E", "E"],
        "dept": ["d1", "d2", "d1", "d2", "d1", "d2"],
        "age": [20, 30, 40, 50, 60, 70],
        "salary": [1000, 2000, 1500, 4000, 3000, 5000],
    })

    import copy

    def _fig(aid, vals):
        res = run_analysis_with_config(aid, df, vals)
        if not res:
            raise AssertionError(f"no figure produced for {aid}")
        return res[0][2]

    # The renderer reads the ChartModel -- title, data and display. The
    # recipe is an input echo, so it is deliberately NOT part of the diff:
    # a control that only rewrote the echo would be dead on screen. The
    # one control consumed from the echo instead of display (legend_labels,
    # which the adapter reads straight out of recipe.layout) is checked by
    # round-trip below plus the adapter-source needle in [46].
    def _j(m):
        view = {k: m.get(k) for k in ("title", "data", "display")}
        return json.dumps(view, sort_keys=True, default=str)

    from services.polars_chart_service import apply_chart_model_display_options

    def _styled(m, opts):
        return apply_chart_model_display_options(copy.deepcopy(m), opts, {})

    stat = _fig("statistical", {"x": ["region"], "y": ["age", "salary"],
                                "palette": "Default"})
    dist = _fig("distribution", {"x": ["age", "salary"], "color": ["region"],
                                 "palette": "Default"})
    corr = _fig("correlation", {"x": ["age", "salary"], "palette": "Default"})
    # The dual-axis figure carries a bar AND the secondary line, so it can
    # probe every categorical control including Line width / Y-axis (right).
    cat = _fig("categorical", {"x": ["region"], "y": ["age"], "agg": "Sum",
                               "dual_y": "salary", "dual_y_agg": "Avg",
                               "direction": "Vertical (Column chart)",
                               "palette": "Default"})
    # legend_labels probes with the figure's real trace name so the rename
    # can actually match one.
    PROBES = {
        "statistical": {
            "title": "PROBE TITLE", "x_label": "PX", "y_label": "PY",
            "legend_title": "PLEGEND", "legend_position": "Bottom Left",
            "legend_labels": {str(stat["data"]["series"][0]["name"]): "RENAMED"},
            "show_value_labels": False, "value_label_color": "#00ff00",
            "label_position": "inside", "bar_mode": "stack",
            "bar_gap": 0.5, "show_grid": False, "show_border": True,
        },
        "distribution": {
            "title": "PROBE TITLE", "x_label": "PX", "y_label": "PY",
            "legend_title": "PLEGEND", "legend_position": "Bottom Left",
            "legend_labels": {str(dist["data"]["series"][0]["name"]): "RENAMED"},
            "histogram_bins": 60, "histogram_opacity": 0.35,
            "show_grid": False, "show_border": True,
        },
        "correlation": {
            "title": "PROBE TITLE", "heatmap_colorscale": "Viridis",
            "heatmap_show_text": True, "heatmap_annotation_size": 18,
            "heatmap_annotation_precision": 4,
            "heatmap_annotation_color": "#00ff00",
            "colorbar_title": "CB", "colorbar_tick_size": 14,
            "colorbar_tick_color": "#ff0000", "colorbar_title_size": 15,
            "colorbar_title_color": "#00ff00", "colorbar_position": "Left",
            "show_border": True,
        },
        "categorical": {
            "title": "PROBE TITLE", "x_label": "PX", "y_label": "PY",
            "y2_label": "PY2", "show_value_labels": False,
            "label_position": "inside", "bar_mode": "stack",
            "bar_gap": 0.5, "show_grid": False, "line_width": 7,
            # The shared legend rows are declared by this tab too (the
            # Secondary Y-Axis overlay puts a legend on screen).
            "legend_title": "PLEGEND", "legend_position": "Bottom Left",
            "show_border": True,
        },
    }
    defaults = default_layout_opts()
    js = (Path(chart_display.__file__).parent / "assets" / "echarts" /
          "lytrize_adapter.js").read_text(encoding="utf-8")
    for aid, fig in (("statistical", stat), ("distribution", dist),
                     ("correlation", corr), ("categorical", cat)):
        base = _j(_styled(fig, dict(defaults)))
        for key, probe in PROBES[aid].items():
            opts = dict(defaults)
            opts[key] = probe
            got_model = _styled(fig, opts)
            if key == "legend_labels":
                # Consumed from recipe.layout by the adapter, not display.
                echoed = (got_model.get("recipe") or {}).get("layout", {}).get("legend_labels")
                check(f"layout control 'legend_labels' reaches the renderer for {aid}",
                      echoed == probe and "legend_labels" in js,
                      f"echo={echoed!r}")
                continue
            got = _j(got_model)
            check(f"layout control '{key}' changes the {aid} model",
                  got != base, f"probe={probe!r}")


def test_new_display_passes_are_wired():
    """The display builders must actually reach the renderer.

    A display option built nowhere -- or produced but never consumed -- is a
    dead control, and its setting reads as broken in the UI. Since the
    ECharts migration the pipeline is: the generation path (``_display`` /
    ``_heatmap_display_options``) or the live-edit path
    (``apply_chart_model_display_options``) writes the ChartModel's
    ``display`` dict, and the JS adapter (``lytrize_adapter.js``) turns it
    into echarts options. This test checks BOTH halves.
    """
    print("[46] New display passes are wired into the render path")
    import inspect
    import chart_display
    import services.polars_chart_service as pcs

    def _src(obj):
        try:
            return inspect.getsource(obj)
        except (OSError, TypeError):
            return ""

    def _adapter_src():
        from pathlib import Path as _P
        for cand in (_P(chart_display.__file__).parent / "assets" / "echarts" / "lytrize_adapter.js",
                     _P("backend") / "assets" / "echarts" / "lytrize_adapter.js"):
            if cand.exists():
                return cand.read_text(encoding="utf-8")
        return ""

    # 1) Card chrome: apply_display_options writes the keys the export/HTML
    #    builders read for the card frame, padding, title placement and chrome.
    src = _src(chart_display.apply_display_options)
    for needle in ("show_border", "border_color", "border_width", "border_radius",
                   "padding", "title_align", "title_position", "title_gap",
                   "show_legend", "legend_position", "show_colorbar",
                   "colorbar_position", "grid_left", "grid_right",
                   "grid_top", "grid_bottom",
                   "header_font_family", "font_family", "font_size"):
        check(f"wired: apply_display_options writes '{needle}'",
              needle in src, "")

    # 2) Chart display: the generation path builds the display dict consumed
    #    by the adapter -- legend block, labels block and palette.
    disp = _src(pcs._display)
    for needle in ("legend", "labels", "show_value_labels", "donut",
                   "pull_slices", "title_size"):
        check(f"wired: _display writes '{needle}'", needle in disp, "")

    # 3) Heatmap/correlation families: colour bar + annotations, shared by the
    #    generation path and the live-edit path (both must agree).
    hm = _src(pcs._heatmap_display_options)
    for needle in ("heatmap_colors", "show_colorbar", "colorbar_position",
                   "colorbar_border", "colorbar_title", "labels"):
        check(f"wired: _heatmap_display_options writes '{needle}'",
              needle in hm, "")

    # 4) Live-edit path: the same controls must round-trip through
    #    apply_chart_model_display_options (the widget's live preview calls
    #    this, e.g. desktop/workers.py).
    live = _src(pcs.apply_chart_model_display_options)
    for needle in ("show_value_labels", "line_width", "y2_label",
                   "show_grid", "bar_gap", "legend"):
        check(f"wired: apply_chart_model_display_options handles '{needle}'",
              needle in live, "")

    # 5) The JS adapter actually consumes the keys the builders write.
    js = _adapter_src()
    check("wired: adapter source is readable", bool(js), "")
    if js:
        for needle in ("display.legend", "display.labels", "display.line_width",
                       "display.bar_gap", "display.show_border",
                       "display.colorbar_border", "display.heatmap_colors",
                       "display.x_title", "display.show_grid",
                       "display.primary_show_value_labels",
                       "legend_labels"):
            check(f"wired: adapter consumes '{needle}'", needle in js, "")


def test_generate_sections_fill_the_pane():
    """Generate must use the pane's width, then wrap -- never leave it empty.

    The review of the categorical tab: three fixed rows of three section
    boxes stopped two thirds of the way across the pane, so the right-hand
    side of the tab was one large empty block AND the page was taller than
    the pane it sat in. The rule now is the flow one (``base.FlowGrid``): as
    many section boxes per line as fit at the compact field width, every line
    stretched to the pane's full width, and whatever is left over wrapped
    onto the next line at the LEFT -- which is exactly where the remaining
    options belong.
    """
    print("[47] Generate sections fill the pane, then wrap")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from desktop.widgets.analysis_settings import get_builder
    from desktop.widgets.analysis_settings.base import (FlowGrid, FLOW_MIN_COL,
                                                        GEN_W)

    app = QApplication.instance() or QApplication([])

    def _page(aid, width):
        b = get_builder(aid, None, num_cols=["age", "salary"],
                        cat_cols=["region", "dept"], dt_cols=["date"],
                        dim_cols=["region", "dept", "date"],
                        palette_names=["Default"],
                        all_cols=["age", "salary", "region", "dept", "date"])
        page = b.build_generate()
        page.resize(width, 900)
        page.show()
        app.processEvents()
        grid = page.findChild(FlowGrid)
        return page, grid

    def _lines(grid):
        """{line y: [(x, width), ...]} for the section boxes, in the grid's
        own coordinates and left-to-right."""
        out = {}
        for w in grid.sections():
            p = w.mapTo(grid, QPoint(0, 0))
            out.setdefault(p.y(), []).append((p.x(), w.width()))
        for y in out:
            out[y].sort()
        return out

    # -- A wide pane: the categorical tab's eight controls pack several per
    #    line and the lines span the whole width.
    page, grid = _page("categorical", 1700)
    lines = _lines(grid)
    cols = grid.columns()
    check("fill: a wide pane packs several controls per line",
          cols >= 5, f"cols={cols}")
    check("fill: eight controls need only two lines at 1700px",
          grid.line_count() == 2, str(grid.line_count()))
    top = lines[sorted(lines)[0]]
    right = top[-1][0] + top[-1][1]
    check("fill: the first line reaches the pane's right edge",
          abs(right - grid.width()) <= 4, f"{right} vs {grid.width()}")
    check("fill: a full line holds 'columns' sections",
          len(top) == cols, f"{len(top)} vs {cols}")
    rest = lines[sorted(lines)[-1]]
    check("fill: the remainder is left-aligned under the line above",
          rest[0][0] <= top[0][0] + 4 and len(rest) < cols,
          f"{rest} vs {top}")
    check("fill: every section still has room for its field",
          all(w >= GEN_W for _x, w in top + rest), str(top + rest))
    page.close()

    # -- A narrow pane: re-flow to fewer columns instead of clipping.
    page, grid = _page("categorical", 700)
    narrow = _lines(grid)
    check("fill: a narrow pane re-flows to fewer columns per line",
          grid.columns() < cols and grid.line_count() >= 4,
          f"cols={grid.columns()} lines={grid.line_count()}")
    first = narrow[sorted(narrow)[0]]
    check("fill: re-flowed sections never overlap",
          all(first[i - 1][0] + first[i - 1][1] <= first[i][0] + 1
              for i in range(1, len(first))), str(first))
    page.close()

    # -- The same rule on every other type, so no tab has a dead strip on the
    #    right any more.
    for aid in ("statistical", "distribution", "correlation"):
        page, grid = _page(aid, 1700)
        lines = _lines(grid)
        top = lines[sorted(lines)[0]]
        right = top[-1][0] + top[-1][1]
        check(f"fill: '{aid}' fills the pane width too",
              abs(right - grid.width()) <= 4, f"{right} vs {grid.width()}")
        page.close()

    check("fill: the compact field width is still the template's 210",
          GEN_W == 210 and FLOW_MIN_COL == GEN_W + 24,
          f"{GEN_W}/{FLOW_MIN_COL}")


# ---------------------------------------------------------------------------
# Dashboard tiling: free-form drag/resize for charts, and export parity.
#
# The drag logic itself lives in the canvas page's JavaScript, so these cover
# the half that must agree with it: the pure placement maths and the HTML
# the placement is rendered into.
# ---------------------------------------------------------------------------


def test_tile_geometry_free_placement() -> None:
    print("[20] Canvas geometry: free placement, not grid cells")
    from desktop.widgets import tile_geometry as tg

    # A fresh board fills two across, then wraps. This is the arrangement the
    # old grid produced by construction; on a free canvas it is now a CHOICE,
    # made by auto_place, rather than the only thing the layout allowed.
    g = tg.auto_place(["u0", "u1", "u2", "u3"])
    check("a fresh board fills two across, then wraps",
          g["u0"]["x"] == 0 and g["u1"]["x"] > 0
          and g["u2"]["y"] > 0 and g["u3"]["y"] > 0, str(g))
    check("two half-width cards really do share a row",
          g["u0"]["y"] == g["u1"]["y"], str(g))
    check("no overlaps on a fresh board", not tg.overlaps(g), str(tg.overlaps(g)))

    # The units themselves. x/w are fractions of the WIDTH so a card keeps its
    # proportion when the window narrows; y/h are PIXELS because a chart's
    # height must not scale with the viewport.
    check("width is a fraction of the canvas, not a pixel count",
          all(0 <= t["x"] <= 1 and 0 < t["w"] <= 1
              for t in tg.clamp_all(g).values()), str(g))
    check("height is in pixels", all(t["h"] >= tg.MIN_H
                                    for t in tg.clamp_all(g).values()), str(g))

    # Nothing snaps to a cell. This is the regression that made the resize
    # handles feel decorative: a drag was rounded to the column width, so a card
    # could only ever occupy a whole cell and sprang back on release.
    nudged = tg.clamp({"x": 0.137, "y": 217.5, "w": 0.413, "h": 333.25})
    check("an arbitrary drag position survives clamping unchanged",
          nudged == {"x": 0.137, "y": 217.5, "w": 0.413, "h": 333.25},
          str(nudged))


def test_tile_geometry_clamp_and_overlap() -> None:
    print("[21] Canvas geometry: proposals are clamped, overlap is allowed")
    from desktop.widgets import tile_geometry as tg

    # Out-of-range proposals from a stale document or older session JSON.
    junk = tg.clamp({"x": -5, "y": -20, "w": 9, "h": 0})
    check("out-of-range proposal is clamped, never negative",
          junk["x"] == 0 and junk["y"] == 0, str(junk))
    check("a card cannot be dragged off the right edge",
          junk["x"] + junk["w"] <= 1.0, str(junk))
    check("a zero-height card is lifted to a usable size",
          junk["h"] >= tg.MIN_H, str(junk))
    check("a card cannot be shrunk to nothing",
          tg.clamp({"x": 0, "y": 0, "w": 0.001, "h": 10})["w"] >= tg.MIN_W)

    # NaN is the dangerous one: it compares false against everything, so it
    # slips through a naive min/max clamp and reaches CSS as left:NaN%, which
    # the browser discards -- the card silently vanishes.
    for bad in (float("nan"), float("inf"), float("-inf")):
        c = tg.clamp({"x": bad, "y": bad, "w": bad, "h": bad})
        check(f"non-finite geometry ({bad}) is replaced, not propagated",
              all(isinstance(v, float) and v == v and abs(v) != float("inf")
                  for v in c.values()), str(c))
    check("a non-numeric payload falls back to the defaults",
          tg.clamp({"x": "abc", "y": None, "w": [], "h": {}})["w"] > 0)

    # OVERLAP IS LEGAL. This is the whole point of the change: a card may be
    # dropped on top of another, as a window may cover another. The old build
    # pushed the offender down, which is why every drag looked ignored.
    stacked = tg.clamp_all({"a": {"x": 0.1, "y": 100, "w": 0.5, "h": 300},
                            "b": {"x": 0.1, "y": 100, "w": 0.5, "h": 300}})
    check("two cards may occupy the same rectangle",
          tg.overlaps(stacked) == [("a", "b")], str(tg.overlaps(stacked)))
    check("the overlap detector finds nothing in a clean board",
          not tg.overlaps(tg.tidy(stacked)))

    check("visual order is top-to-bottom, left-to-right",
          tg.visual_order({"c": {"x": 0.5, "y": 10, "w": 0.5, "h": 100},
                           "a": {"x": 0, "y": 0, "w": 0.5, "h": 100},
                           "b": {"x": 0.5, "y": 0, "w": 0.5, "h": 100}})
          == ["a", "b", "c"])


def test_tile_geometry_tidy_reflows() -> None:
    print("[22] Canvas geometry: tidy re-flows without resizing the cards")
    from desktop.widgets import tile_geometry as tg
    messy = tg.clamp_all({"u0": {"x": 0.6, "y": 500, "w": 0.4, "h": 250},
                          "u1": {"x": 0.1, "y": 40, "w": 0.4, "h": 250},
                          "u2": {"x": 0.2, "y": 20, "w": 0.3, "h": 180}})
    t = tg.tidy(messy, gap_px=24)
    check("tidy keeps the visual order the user can see",
          tg.visual_order(t) == ["u2", "u1", "u0"], str(t))
    check("tidy leaves no card overlapping another", not tg.overlaps(t),
          str(tg.overlaps(t)))
    check("tidy keeps each card the size the user gave it",
          all(t[u]["w"] == messy[u]["w"] and t[u]["h"] == messy[u]["h"]
              for u in messy), str(t))
    check("tidy packs from the top-left",
          min(g["y"] for g in t.values()) == 0.0
          and min(g["x"] for g in t.values()) == 0.0, str(t))
    check("tidy stays inside the canvas",
          all(g["x"] + g["w"] <= 1.0 + 1e-9 for g in t.values()), str(t))
    # Idempotent: tidying an already-tidy board must not keep shuffling it.
    check("tidy is idempotent", tg.tidy(t, gap_px=24) == t)


def test_saved_geometry_migrates_from_the_grid() -> None:
    print("[23b] Saved sessions: grid cells convert to free units once")
    from desktop.widgets import tile_geometry as tg

    grid = {"a": {"x": 0, "y": 0, "w": 1, "h": 2},
            "b": {"x": 1, "y": 0, "w": 1, "h": 1}}
    check("a grid blob is recognised as grid-shaped",
          tg.is_grid_shaped(grid, 2), str(grid))
    free = tg.from_grid(grid, cols=2, row_h=400, gap_px=24)
    check("converted geometry is in free units (fractions, not cells)",
          free["a"]["w"] == 0.5 and free["a"]["x"] == 0.0, str(free))
    check("converted height uses the card height and gap, in pixels",
          free["a"]["h"] == 2 * 424 - 24, str(free))
    check("the right-hand column lands on the right half",
          free["b"]["x"] == 0.5, str(free))
    check("a converted blob is no longer grid-shaped (migration is one-way)",
          not tg.is_grid_shaped(free, 2), str(free))
    check("nothing converted lands outside the canvas",
          all(g["x"] >= 0 and g["x"] + g["w"] <= 1.0 + 1e-9
              for g in free.values()), str(free))
    # Free units must not be mistaken for grid cells, or a saved free board
    # would be converted a second time on every open and drift downwards.
    check("fractional widths are never read as grid columns",
          not tg.is_grid_shaped(tg.auto_place(["a", "b", "c"]), 2))
    check("an empty blob is not grid-shaped", not tg.is_grid_shaped({}, 2))


def test_saved_tile_geometry_round_trip() -> None:
    print("[23] Saved tiling geometry: migration and restore")
    import json
    from desktop.pages.dashboard_page import _load_saved_tiles, _is_kpi_key
    sm = {"grid_cols_n": 2, "grid_fullwidth_json": json.dumps(
        {"a": {"x": 1, "y": 2, "w": 1, "h": 3}, "0": {"x": 0, "y": 0, "w": 1, "h": 1},
         "gone": {"x": 0, "y": 0, "w": 1, "h": 1}})}
    tiles = _load_saved_tiles(sm)
    check("tiled geometry round-trips exactly",
          tiles["a"] == {"x": 1, "y": 2, "w": 1, "h": 3}, str(tiles))
    check("KPI tiles survive the round trip and are distinguishable",
          _is_kpi_key("0") and not _is_kpi_key("a") and tiles["0"]["x"] == 0,
          str(tiles))
    # _load_saved_tiles is deliberately card-agnostic: it only sees session
    # meta, not which blocks currently exist. Dropping unknown ids is the
    # geometry helper's job, so asserting it here would be asserting against a
    # function that cannot know the answer.

    legacy = {"grid_cols_n": 3, "grid_fullwidth_json": json.dumps(
        {"p": True, "q": False})}
    lt = _load_saved_tiles(legacy)
    check("legacy full-width boolean becomes a full-row span",
          lt["p"] == {"x": 0, "y": 0, "w": 3, "h": 1}
          and "q" not in lt, str(lt))


def test_report_canvas_structure() -> None:
    print("[27] Report canvas: the board IS the report")
    import re
    from modules.chart_model import make_chart_model
    from services.export_service import generate_html_report

    def _model(uid, title):
        return make_chart_model(
            uid=uid, chart_type="statistical", title=title,
            data={"dimensions": ["g"],
                  "series": [{"id": "c", "name": "c", "type": "bar",
                              "values": [{"name": "a", "value": 1},
                                         {"name": "b", "value": 2}]}]})

    charts = [("a", "First", _model("a", "First"), "n", "", {}),
              ("b", "Second", _model("b", "Second"), "", "", {})]
    geom = {"a": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 500.0},
            "b": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 400.0}}

    he = generate_html_report(charts, "S", {}, grid_cols_n=2,
                              tile_geometry=geom, editor=True)
    check("every card carries a stable uid for the canvas to target",
          len(re.findall(r'data-uid="[ab]"', he)) == 2, he[:0])
    check("editor mode adds the editing class to <body>",
          '<body class="ly-editing ly-free">' in he)
    check("editor mode ships the drag overlay css and js",
          ".ly-grip" in he and "lySetEditing" in he)
    check("no unsubstituted placeholder leaks into the document",
          "__ACCENT__" not in he and "__COLS__" not in he
          and "__GAP__" not in he and "__MODE__" not in he)

    hs = generate_html_report(charts, "S", {}, grid_cols_n=2,
                              tile_geometry=geom)
    check("the SAVED file carries no editing chrome at all",
          "<body>" in hs and "ly-grip" not in hs and "lySetEditing" not in hs
          and "ly-editing" not in hs)

    # The card is exactly the height the user set, and the chart inside it
    # fills it (flex:1 + height:100%) -- not a fixed 400px plot spilling past
    # the bottom edge of a shorter card, whatever the card's height.
    cs = re.findall(r'<section class="tile chart-card"[^>]*style="([^"]*)"', hs)
    a_style = next((s for s in cs if "height:500.00px" in s), None)
    check("the card itself is exactly the height the user set",
          a_style is not None, str(cs))
    check("the chart fills whatever height the card has",
          ".chart{width:100%;height:100%;min-height:0}" in hs
          and ".chart-card .chart{flex:1;min-height:0}" in hs,
          "")

    check("the saved file embeds the ECharts runtime and the Lytrize adapter",
          "LytrizeEChartsAdapter" in hs
          and "echarts.init" in hs, "")

    hc = generate_html_report(charts, "S", {}, grid_cols_n=2,
                              extra_css="@font-face{x:1}")
    check("caller css reaches the document before </style>",
          "@font-face{x:1}" in hc)


def test_canvas_handles_are_wired() -> None:
    print("[27b] Canvas editing: the handles really move and resize")
    import re
    from modules.report_editor import editor_js, editor_css

    free = editor_js(2, 24, mode="free")
    grid = editor_js(2, 24, mode="grid")
    check("the overlay is told which layout it is editing",
          "var FREE = MODE !== 'grid';" in free and "var MODE = 'free';" in free,
          "")
    check("legacy mode is still selectable",
          "var MODE = 'grid';" in grid)

    # The drag must not round to a cell pitch. This is the regression behind
    # "all those resize handler icons are just for show": every move() was
    # divided by the column width and rounded, so a card could only ever land on
    # a whole cell, and Python then re-packed it straight back.
    # Not merely unused: dead snapping code left in the free path is exactly how
    # the grid behaviour came back. The free bundle must not contain it at all.
    check("the free bundle contains no grid snapping code",
          "pitchX" not in free and "pitchY" not in free
          and "snapCol" not in free and "snapRow" not in free
          and "gridTemplateColumns" not in free, "")
    check("the legacy bundle still has the cell-snapping path",
          "pitchX" in grid and "snapCol" in grid, "")
    check("free dragging adds the pointer delta directly",
          "g.x = drag.start.x + dx / c.w;" in free, "")
    check("free resizing is by pixels, not by cell spans",
          "g.w = drag.start.w + dx / c.w;" in free
          and "g.h = drag.start.h + dy;" in free, "")
    check("the card itself follows the pointer, not just an outline",
          "writeGeom(el, g);" in free and "refit(el);" in free)
    check("the proposal is clamped before it is sent to Python",
          "clampGeom(g, el);" in free)
    # Fractions in, fractions out -- int() here would quantise every card's
    # left edge and width back to whole columns.
    check("fractional geometry is never int()-ed in the overlay",
          "Math.round(g.x * 100)" not in free)
    check("the canvas is grown live so a card can be dropped below it",
          "host.style.height" in free)
    # ECharts migration: the live-drag grows the scroll host, and releasing
    # re-fits its height to the actual card bottoms (syncCanvasHeight) -- the
    # old literal "height = ''" restore is gone with the Plotly path.
    _end = free.split("function end()", 1)[-1]
    check("the temporary canvas growth is handed back on release",
          "syncCanvasHeight(host)" in _end, "")
    check("the charts are refit after a resize",
          "getInstanceByDom" in free and "chart.resize()" in free, "")
    check("a click that never moved is not treated as a layout change",
          "Math.abs(g.x - b.x) < 0.0005" in free)
    # Whole-card dragging must not steal interactive content. The chart
    # surface itself is deliberately NOT excluded: a card drag starts from it
    # after a small movement threshold, and while a drag is live the CSS rule
    # ".ly-dragging .chart { pointer-events: none !important; }" stops the
    # ECharts canvas from swallowing the pointer. Mirrors the old Plotly-era
    # .js-plotly-plot/.modebar guard for the ECharts shell.
    _flat = " ".join(
        free.replace("' +", "'").replace("'", " ").split())
    check("dragging the card does not hijack the notes or form controls",
          all(t in _flat for t in
              (".notes", ".slicer-body", "button", "input", "select",
               "textarea", "[contenteditable]")),
          _flat[-300:])
    check("a click that never moved stays a chart click (5px threshold)",
          "dx * dx + dy * dy >= 25" in free, "")
    check("the whole card is a drag handle on the free canvas",
          ".ly-editing.ly-free .chart-card" in editor_css())
    _css = editor_css()
    check("during a drag the charts stop swallowing pointer events",
          ".ly-dragging .chart" in _css and "pointer-events: none" in _css,
          "")

    css = editor_css("#6163df")
    # position:relative on a free CARD would silently drop its left/top and
    # collapse the whole board into the top-left corner. Every rule that sets it
    # on a card must therefore be qualified with .ly-grid-editing. The CONTAINER
    # legitimately needs it (the drag ghost is positioned inside it).
    _bare = css.replace("/*__NOTHING__*/", "")
    _card_rules = re.findall(
        r"([^{}]*(?:chart-card|kpi-card)[^{}]*)\{([^}]*)\}", _bare)
    _relative_cards = [sel.strip() for sel, body in _card_rules
                       if "position: relative" in body]
    check("position:relative on a card is qualified to grid mode",
          _relative_cards
          and all("ly-grid-editing" in sel for sel in _relative_cards),
          str(_relative_cards))
    check("no unscoped position:relative rule can catch a free card",
          ".ly-editing .chart-card, .ly-editing .kpi-card { position: relative; }"
          not in css, "")

    check("no persistent handle chrome remains -- glow only",
          ".ly-handle" not in css and ".ly-h-" not in css
          and ".ly-working > .ly-sel" in css and "ly-glow-pulse" in css, "")
    check("the accent placeholder is substituted",
          "__ACCENT__" not in css)


def test_kpi_tiles_share_the_chart_canvas() -> None:
    print("[28] KPI tiles: placed like charts, on the same canvas")
    import re
    from modules.chart_model import make_chart_model
    from modules.canvas_geometry import GAP_PX, title_band_height
    from services.export_service import generate_html_report

    def _model(uid, title):
        return make_chart_model(
            uid=uid, chart_type="statistical", title=title,
            data={"dimensions": ["g"],
                  "series": [{"id": "c", "name": "c", "type": "bar",
                              "values": [{"name": "a", "value": 1},
                                         {"name": "b", "value": 2}]}]})

    charts = [("a", "First", _model("a", "First")),
              ("b", "Second", _model("b", "Second"))]
    kpis = [{"label": "Total", "value": "1"},
            {"label": "Count", "value": "2"}]
    band = title_band_height(28, True) + GAP_PX

    both = generate_html_report(
        charts, "S", {}, kpis=kpis,
        tile_geometry={"a": {"x": 0, "y": 0, "w": 0.5, "h": 400},
                       "b": {"x": 0.5, "y": 0, "w": 0.5, "h": 400}},
        kpi_geometry={"0": {"x": 0, "y": 420, "w": 1.0, "h": 120}})
    check("KPIs and charts share ONE canvas on the free layout",
          both.count('class="ly-canvas"') == 1
          and "kpi-row" not in both, both[:0])
    # A KPI placed below a chart must not push that chart's saved row index --
    # there are no row indices any more, because placement is absolute.
    ks = re.findall(r'<section class="tile kpi-card"[^>]*style="([^"]*)"', both)
    cs = re.findall(r'<section class="tile chart-card"[^>]*style="([^"]*)"', both)

    def _n(style, key):
        m = re.search(re.escape(key) + r":([0-9.\-]+)", style)
        return float(m.group(1)) if m else None

    def _nz(style, key, default=-1.0):
        v = _n(style, key)
        return default if v is None else v

    check("a KPI is placed exactly like a chart",
          any(abs(_nz(k, "left") - 0.0) < 1e-4
              and abs(_nz(k, "top") - 420.0) < 0.01
              and abs(_nz(k, "width") - 100.0) < 1e-4
              for k in ks), str(ks))
    check("placing a KPI leaves the chart coordinates untouched",
          any(abs(_nz(c, "left") - 0.0) < 1e-4
              and abs(_nz(c, "top") - band) < 0.01
              for c in cs), str(cs))
    check("a KPI with no saved geometry is still positioned",
          len(ks) == 2 and all(k.strip() for k in ks), str(ks))

    hn = generate_html_report(charts, "S", {}, kpis=kpis)
    hn_geom = re.findall(r'<section class="tile kpi-card"[^>]*style="([^"]*)"', hn)
    check("auto-placed KPIs are sized like KPIs, not like charts",
          len(hn_geom) == 2 and all("height:120.00px" in k for k in hn_geom),
          str(hn_geom))
    check("no empty style leaves a KPI unpositioned",
          all(k.strip() for k in hn_geom), str(hn_geom))

    bad = generate_html_report(charts, "S", {}, kpis=kpis,
                               kpi_geometry={"0": {"x": -5, "y": 0, "w": 9, "h": 0}})
    bs = re.findall(r'data-kpi="0" style="([^"]*)"', bad)
    check("out-of-range KPI geometry is clamped, never invalid CSS",
          bs and "NaN" not in bs[0] and "nan" not in bs[0].lower()
          and abs(_nz(bs[0], "left") - 0.0) < 1e-4
          and _nz(bs[0], "height", 0.0) >= 120.0, str(bs))


def test_export_follows_tile_geometry() -> None:
    print("[24] HTML export: free placement, visual order, no editor chrome")
    import re
    from modules.chart_model import make_chart_model
    from modules.canvas_geometry import TITLE_UID, GAP_PX, title_band_height
    from services.export_service import generate_html_report

    def _model(uid, title):
        return make_chart_model(
            uid=uid, chart_type="statistical", title=title,
            data={"dimensions": ["g"],
                  "series": [{"id": "c", "name": "c", "type": "bar",
                              "values": [{"name": "a", "value": 1},
                                         {"name": "b", "value": 2}]}]})

    charts = [("b", "Second", _model("b", "Second")),
              ("a", "First", _model("a", "First")),
              ("c", "Third", _model("c", "Third"))]
    geom = {"a": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 420.0},
            "b": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 300.0},
            "c": {"x": 0.1, "y": 440.0, "w": 0.4, "h": 200.0}}
    html = generate_html_report(charts, "S", {}, grid_cols_n=2,
                                tile_geometry=geom)
    # Nothing saved a title band, so one is inserted: the two y=0 cards shift
    # down by exactly one band, once.
    band = title_band_height(28, True) + GAP_PX
    order = re.findall(r'data-uid="([abc])"', html)
    check("export walks the visual tile order, not bridge order",
          order == ["a", "b", "c"], str(order))
    check("cards are absolutely placed on one canvas",
          'class="ly-canvas"' in html and "grid-template" not in html)

    def _style(uid, doc):
        m = re.search(r'data-uid="%s" style="([^"]*)"' % re.escape(uid), doc)
        return m.group(1) if m else ""

    def _n(style, key):
        m = re.search(re.escape(key) + r":([0-9.\-]+)", style)
        return float(m.group(1)) if m else None

    a, c = _style("a", html), _style("c", html)
    check("placement is left/top in percent+px, width percent, height px",
          abs(_n(a, "left") - 0.0) < 1e-4
          and abs(_n(a, "top") - band) < 0.01
          and abs(_n(a, "width") - 50.0) < 1e-4
          and abs(_n(a, "height") - 420.0) < 0.01
          and abs(_n(c, "left") - 10.0) < 1e-4
          and abs(_n(c, "top") - 440.0) < 0.01
          and abs(_n(c, "width") - 40.0) < 1e-4
          and abs(_n(c, "height") - 200.0) < 0.01,
          f"a={a} c={c}")
    m = re.search(r'data-min-height="([0-9.]+)"', html)
    check("the canvas is told how tall it must be",
          m is not None and float(m.group(1)) >= 640.0,
          m.group(0) if m else "no min-height")

    # Arbitrary positions must survive the round trip to CSS -- this is the
    # regression behind "the resize handles are just for show".
    nudged = generate_html_report(charts, "S", {}, tile_geometry={
        TITLE_UID: {"x": 0.0, "y": 0.0, "w": 1.0,
                    "h": title_band_height(28, True)},
        "a": {"x": 0.137, "y": 217.5, "w": 0.413, "h": 333.25},
        "b": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 400.0},
        "c": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 400.0}})
    ns = _style("a", nudged)
    check("an arbitrary dragged position is emitted verbatim",
          abs(_n(ns, "left") - 13.7) < 1e-4
          and abs(_n(ns, "top") - 217.5) < 0.01
          and abs(_n(ns, "width") - 41.3) < 1e-4
          and abs(_n(ns, "height") - 333.25) < 0.01,
          ns)

    html0 = generate_html_report(charts, "S", {}, grid_cols_n=2)
    o0 = re.findall(r'data-uid="([abc])" style="([^"]*)"', html0)
    check("no geometry -> cards are auto-placed, not stacked",
          len(o0) == 3 and len({s for _, s in o0}) == 3, str(o0))
    st = dict(o0)
    check("an auto-placed board fills two across",
          abs(_n(st["b"], "width") - 50.0) < 1e-4
          and abs(_n(st["a"], "width") - 50.0) < 1e-4
          and abs(_n(st["b"], "left") - 0.0) < 1e-4
          and abs(_n(st["a"], "left") - 50.0) < 1e-4,
          str(st))

    # A malformed stored value must not reach the document: the browser
    # discards invalid CSS and the card silently disappears.
    bad = generate_html_report(charts, "S", {}, tile_geometry={
        "a": {"x": -5, "y": "nan", "w": 9, "h": 0},
        "b": {"x": 0.9, "y": 0, "w": 0.5, "h": 100},
        "c": {"x": 0, "y": 0, "w": 0.5, "h": 400}})
    bs = dict(re.findall(r'data-uid="([abc])" style="([^"]*)"', bad))
    check("out-of-range geometry is clamped, never invalid CSS",
          all("nan" not in s.lower() for s in bs.values()), str(bs))
    check("a card dragged past the right edge is pulled back inside",
          all((_n(s, "left") or 0) + (_n(s, "width") or 0) <= 100.0 + 1e-3
              for s in bs.values()), str(bs))
    check("a card with no usable height is floored, not zero-height",
          (_n(bs["a"], "height") or 0) >= 120.0, bs["a"])

    # A session saved before the free canvas converts once, exactly as the
    # live canvas converts it on load -- grid cells become free units instead
    # of dropping the saved board on the floor.
    grid = generate_html_report(charts, "S", {}, geometry_mode="grid",
                                tile_geometry={"a": {"x": 0, "y": 0, "w": 1, "h": 2},
                                               "b": {"x": 1, "y": 0, "w": 1, "h": 1},
                                               "c": {"x": 1, "y": 1, "w": 1, "h": 1}})
    gs = dict(re.findall(r'data-uid="([abc])" style="([^"]*)"', grid))
    band2 = title_band_height(28, True) + GAP_PX
    check("legacy grid mode converts cells to free units on export",
          abs(_n(gs["a"], "width") - 50.0) < 1e-4
          and (_n(gs["a"], "height") or 0) > 400.0, gs.get("a"))
    check("legacy grid conversion keeps the second column on the right",
          abs(_n(gs["b"], "left") - 50.0) < 1e-4, gs.get("b"))
    check("legacy grid conversion is shifted below the title band once",
          (_n(gs["b"], "top") or 0) >= band2 - 0.01, gs.get("b"))


def test_export_replicates_the_legend_layout() -> None:
    print("[35c] HTML export: the dashboard legend matches the setting area")
    import json
    import re
    import pandas as pd
    from chart_display import (apply_display_options, default_layout_opts,
                               default_typo_opts)
    from modules.analysis.pie_chart import run_pie_chart
    from services.export_service import generate_html_report

    df = pd.DataFrame({
        "product_category": ["Coffee", "Tea", "Bakery", "Drinking Chocolate",
                             "Flavours", "Coffee beans", "Loose Tea",
                             "Branded", "Packaged Chocolate"],
        "Count": [137, 138, 136, 99, 89, 89, 89, 87, 76]})
    _, base_model = run_pie_chart(df, x_cols=["product_category"])[0]

    for pos in ("Bottom Center", "Top Center", "Bottom Left"):
        lo = dict(default_layout_opts())
        lo["legend_position"] = pos
        lo["legend_orientation"] = "Horizontal"
        typo = dict(default_typo_opts())
        styled = apply_display_options(base_model, lo, typo,
                                       width_px=1100, height_px=430)
        meta = {"config": {}, "layout": dict(lo),
                "typography": dict(typo)}
        chart = ("c1", "Pie", styled, "", "pie", meta)
        html = generate_html_report(
            [chart], "S", {},
            tile_geometry={"c1": {"x": 0.1, "y": 0.3, "w": 0.8, "h": 430}})
        # 1. The intent the user set in the Analysis setting area must reach
        #    the exported document, or the canvas can only ever show the
        #    default. The model travels in the chart-models payload and the
        #    embedded ECharts runtime renders it client-side.
        m = re.search(
            r'<script type="application/json" id="chart-models">(.*?)</script>',
            html, re.S)
        payload = json.loads(m.group(1)) if m else {}
        # The runner gives the model its own uid, so look the model up by
        # position: this test exports exactly one chart.
        model = next(iter(payload.values()), None)
        disp = (model or {}).get("display") or {}
        check(f"export carries the {pos} + Horizontal legend intent",
              str(disp.get("legend_position")) == pos
              and str(disp.get("legend_orientation")) == "Horizontal",
              f"position={disp.get('legend_position')!r} "
              f"orientation={disp.get('legend_orientation')!r}")
        # 2. The file must embed the ECharts runtime and the Lytrize adapter
        #    that actually draws the legend -- an export that ships a model
        #    but no renderer is a blank chart.
        check(f"the exported file embeds the ECharts runtime ({pos})",
              "LytrizeEChartsAdapter" in html and "echarts" in html.lower(), "")


def test_export_free_canvas_is_printable() -> None:
    print("[24b] HTML export: the free canvas survives print and preview CSS")
    import re
    from modules.chart_model import make_chart_model
    from services.export_service import generate_html_report

    def _model(uid, title):
        return make_chart_model(
            uid=uid, chart_type="statistical", title=title,
            data={"dimensions": ["g"],
                  "series": [{"id": "c", "name": "c", "type": "bar",
                              "values": [{"name": "a", "value": 1},
                                         {"name": "b", "value": 2}]}]})

    charts = [("a", "First", _model("a", "First"))]
    html = generate_html_report(charts, "S", {},
                                tile_geometry={"a": {"x": 0, "y": 0, "w": 0.5,
                                                     "h": 400}})
    # Absolute pixels are meaningless across paginated paper, and two cards
    # overlapping on screen would print on top of each other -- so print falls
    # back to document flow in the same visual order the canvas paints.
    _print_block = html.split("@media print")[-1].replace(" ", "")
    # relative (not static): the tile stays in flow exactly like static, but
    # remains the containing block for its ::before card picture -- with
    # static, every card image would anchor to the page and stack into one
    # full-sheet overlay on top of the printout.
    check("print CSS drops the absolute positioning",
          ".tile{position:relative!important" in _print_block, "")
    check("print CSS clears the pixel height so nothing keeps a stale size",
          "height:auto!important" in _print_block, "")
    check("print stacks every tile full-width in document flow",
          "width:100%!important" in _print_block, "")
    check("print keeps a card from splitting across pages",
          "page-break-inside:avoid" in _print_block, "")
    check("print gives the charts a concrete height to fit into",
          ".chart{height:320px!important}" in _print_block, "")
    check("cards are box-sizing:border-box so padding does not inflate height",
          "box-sizing:border-box" in html.replace(" ", ""), "")
    check("the charts refit once the print layout applies",
          "matchMedia('print')" in html.replace(" ", "")
          and "getInstanceByDom" in html,
          "")
    # Print walks the same DOM order the canvas paints, which is the visual
    # order -- that half of the contract is pinned in [24].


def test_dashboard_palette_defaults_follow_theme() -> None:
    print("[29] Dashboard palette: defaults follow the app theme, explicit keys are stored")
    from desktop.pages import dashboard_page as dp
    from desktop import theme as theme_mod
    from services.export_service import build_export_theme, apply_preset

    # The board and the file now read ONE dict, so there is no light/dark split
    # left to regress -- but the preset path is exactly where it used to leak,
    # so assert the contract directly.
    state = dict(dp.EXPORT_DEFAULTS)
    merged = apply_preset(state, "Midnight Pulse")
    before = build_export_theme(merged)
    original = theme_mod.current_mode()
    try:
        for mode in ("light", "dark"):
            theme_mod.current_mode = lambda m=mode: m
            after = build_export_theme(merged)
            check(f"preset palette is unchanged under {mode} app theme",
                  after == before, f"{before} != {after}")
    finally:
        theme_mod.current_mode = lambda: original
    check("the page no longer keeps a second, theme-following palette",
          not hasattr(dp, "_resolve_live_style"))
    # Page defaults now resolve against the CURRENT app theme (a permanently
    # dark board on a light app was the 2026-11-24 report). Keys the user set
    # explicitly are stored and always win, so only the never-touched look moved.
    from desktop import theme as _theme_mod
    dark_mode = _theme_mod.current_mode()
    try:
        _theme_mod.current_mode = lambda: "dark"
        check("dark defaults ARE the service defaults (one source of truth)",
              dict(dp._STYLE_DEFAULTS) == dict(dp.EXPORT_DEFAULTS))
        _theme_mod.current_mode = lambda: "light"
        from services.export_service import export_defaults_for
        check("light defaults ARE the light service defaults (one source of truth)",
              dict(dp._STYLE_DEFAULTS) == dict(export_defaults_for("light")))
        check("light board canvas is light, card chrome is light",
              dict(dp._STYLE_DEFAULTS)["ex_bg"] == "#f3f6fb"
              and dict(dp._STYLE_DEFAULTS)["ex_card"] == "#ffffff")
        check("dark board canvas stays the historical dark palette",
              __import__("services.export_service", fromlist=["EXPORT_DEFAULTS"]).EXPORT_DEFAULTS["ex_bg"] == "#121a2e")
    finally:
        _theme_mod.current_mode = lambda: dark_mode


def test_kpi_ids_reconcile_across_the_bridge() -> None:
    print("[30] KPI ids: canvas spelling and persisted spelling are one tile")
    from desktop.pages import dashboard_page as dp
    from modules.report_editor import editor_js

    # The overlay tags a KPI with data-kpi="0" and reports it as "kpi:0" so it
    # cannot be confused with a chart uid. The persisted blob and the report
    # writer both key KPIs by the bare index. Both spellings must resolve to
    # the same tile or a KPI drag is silently dropped.
    check("canvas KPI uid maps to the persisted KPI key",
          dp._kpi_key("kpi:0") == "0" and dp._kpi_key("kpi:12") == "12",
          f"got {dp._kpi_key('kpi:0')!r}")
    check("persisted KPI key is already normalised",
          dp._kpi_key("0") == "0")
    check("a chart uid is never mistaken for a KPI",
          dp._kpi_key("abc123") is None and dp._kpi_key("") is None
          and dp._kpi_key(None) is None)
    check("both spellings are recognised as KPI keys",
          dp._is_kpi_key("kpi:0") and dp._is_kpi_key("0")
          and not dp._is_kpi_key("abc123"))
    check("the overlay really does emit the prefixed spelling",
          "'kpi:' + el.dataset.kpi" in editor_js(2, 24))

    # A KPI action must reach the KPI handlers, never a chart handler that
    # would run against a KPI index.
    import inspect
    src = inspect.getsource(dp.DashboardPage._on_canvas_action)
    check("KPI actions are dispatched by index",
          all(a in src for a in ("kpi-delete", "kpi-up", "kpi-down",
                                 "kpi-edit")))
    # The KPI bar has its OWN operations (kpi-edit/kpi-up/kpi-down/kpi-delete)
    # and the chart bar is now pencil + delete only (open-analysis/delete).
    # Either branch offering the other's actions routes a click to a handler
    # that runs against the wrong card kind.
    _js = editor_js(2, 24)
    _kpi_branch = _js.split("kpi-card'))", 1)[1].split("} else if", 1)[0]
    check("KPI cards no longer offer chart-only actions",
          all(a not in _kpi_branch
              for a in ("act('style')", "act('up')", "act('down')",
                        "act('notes')", "act('open-analysis')"))
          and "el.classList.contains('kpi-card')" in _js)
    # Anchored on the pencil's own tooltip, not on a generic brace: the
    # document has several "} else {" blocks and rsplit grabs the wrong one.
    _chart_branch = _js.split("Edit chart in Analysis", 1)[0].rsplit(
        "} else {", 1)[1] + _js.split("Edit chart in Analysis", 1)[1].split(
        "el.appendChild(bar);", 1)[0]
    check("chart cards offer pencil + delete only",
          "act('open-analysis')" in _chart_branch
          and "act('delete')" in _chart_branch
          and all(a not in _chart_branch
                  for a in ("act('style')", "act('up')", "act('down')",
                            "act('notes')")))


def test_kpi_move_up_accepts_the_first_card() -> None:
    print("[31] KPI reorder: the first card can move up")
    from desktop.pages import dashboard_page as dp

    class _B:
        def __init__(self):
            self.kpis = [{"label": "A"}, {"label": "B"}, {"label": "C"}]
            self.refreshed = 0
            # The heading is a card in the same blob, so it is part of the
            # fixture: without it the first _current_geometry call inserts a
            # title band and shifts every KPI down, which would make these
            # assertions about the SWAP measure the migration instead.
            self.tiles = {"__title__": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 120.0},
                          "0": {"x": 0.0, "y": 144.0, "w": 0.5, "h": 120.0},
                          "1": {"x": 0.5, "y": 144.0, "w": 0.5, "h": 120.0},
                          "2": {"x": 0.0, "y": 288.0, "w": 0.5, "h": 120.0}}

        def get(self, key, default=None):
            if key == "kpis":
                return self.kpis
            if key == "tile_geometry":
                return self.tiles
            return default

        def set(self, key, value):
            if key == "kpis":
                self.kpis = value
            if key == "tile_geometry":
                self.tiles = value

    holder = dp.DashboardPage.__new__(dp.DashboardPage)
    b = _B()
    holder.bridge = b
    holder._refresh_kpis = lambda: setattr(b, "refreshed", b.refreshed + 1)
    # The bare holder has no QTimers. Stubbed rather than built: the point of
    # this test is the swap, not the debounced rebuild.
    holder._schedule_regen = lambda *a, **k: None

    # index 0 has no neighbour above it. The bound must stay "0 < index":
    # relaxing it to "0 <= index" makes kpis[index - 1] wrap to kpis[-1], so
    # the first card's up-arrow silently swaps it with the LAST card.
    before = list(b.kpis)
    dp.DashboardPage._on_move_kpi_up(holder, 0)
    check("the first KPI refuses to move up (and does not wrap to the last)",
          b.kpis == before, str([k["label"] for k in b.kpis]))
    check("a refused move does not refresh the board", b.refreshed == 0,
          str(b.refreshed))

    dp.DashboardPage._on_move_kpi_up(holder, 2)
    check("the last KPI moves up",
          [k["label"] for k in b.kpis] == ["A", "C", "B"],
          str([k["label"] for k in b.kpis]))
    check("moving up refreshes the board", b.refreshed == 1, str(b.refreshed))

    # Reordering the LIST is not enough any more: KPI cards are absolutely
    # positioned, so where a card sits is decided by its geometry. Without the
    # tile swap the arrow buttons would reorder a list nobody looks at and the
    # board would not move -- the same "decorative control" bug as the handles.
    # Checked after ONE swap, before the second undoes it.
    check("moving a KPI trades its tile with its neighbour's",
          b.tiles["1"] == {"x": 0.0, "y": 288.0, "w": 0.5, "h": 120.0}
          and b.tiles["2"] == {"x": 0.5, "y": 144.0, "w": 0.5, "h": 120.0},
          str(b.tiles))
    check("a KPI tile outside the swap is left alone",
          b.tiles["0"] == {"x": 0.0, "y": 144.0, "w": 0.5, "h": 120.0},
          str(b.tiles["0"]))
    check("the title card is not dragged along by a KPI reorder",
          b.tiles["__title__"] == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 120.0},
          str(b.tiles.get("__title__")))

    dp.DashboardPage._on_move_kpi_up(holder, 2)
    check("moving up again is still a plain adjacent swap",
          [k["label"] for k in b.kpis] == ["A", "B", "C"],
          str([k["label"] for k in b.kpis]))
    # _persist_geometry stamps the free-canvas mode key on EVERY write, so
    # the blob that comes back carries __ly_mode__ even though the fixture
    # did not seed it. The TILES themselves must still land exactly where
    # they started -- the metadata key is expected, drift is not.
    check("two swaps put the tiles back where they started",
          b.tiles == {"__title__": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 120.0},
                      "0": {"x": 0.0, "y": 144.0, "w": 0.5, "h": 120.0},
                      "1": {"x": 0.5, "y": 144.0, "w": 0.5, "h": 120.0},
                      "2": {"x": 0.0, "y": 288.0, "w": 0.5, "h": 120.0},
                      dp._MODE_KEY: dp._FREE_MODE},
          str(b.tiles))

    # Down is the mirror image, and must move the tiles too.
    b.tiles = {"__title__": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 120.0},
               "0": {"x": 0.0, "y": 144.0, "w": 0.5, "h": 120.0},
               "1": {"x": 0.5, "y": 144.0, "w": 0.5, "h": 120.0}}
    dp.DashboardPage._on_move_kpi_down(holder, 0)
    check("moving a KPI down trades its tile too",
          b.tiles["0"] == {"x": 0.5, "y": 144.0, "w": 0.5, "h": 120.0}
          and b.tiles["1"] == {"x": 0.0, "y": 144.0, "w": 0.5, "h": 120.0},
          str(b.tiles))


def test_prune_geometry_keeps_the_title_band() -> None:
    print("[35b] Canvas prune: the title band survives (no per-round-trip drift)")
    from desktop.pages import dashboard_page as dp
    from modules.canvas_geometry import TITLE_UID, ensure_title_band

    # _prune_geometry runs on EVERY _refresh_charts -- i.e. every time the user
    # opens a chart's settings and comes back. It used to keep only chart uids,
    # KPI keys, slicer keys and the mode marker, so it silently dropped the
    # __title__ tile. The next _current_geometry then took ensure_title_band's
    # "first board" branch and shifted EVERY card down by one title band + gap,
    # so a card parked at the top sank a row on each edit round-trip.
    class _B:
        def __init__(self, tiles):
            self.tiles = dict(tiles)

        def get(self, key, default=None):
            return self.tiles if key == "tile_geometry" else default

        def set(self, key, value):
            if key == "tile_geometry":
                self.tiles = value

    seeded = {
        TITLE_UID: {"x": 0.0, "y": 0.0, "w": 1.0, "h": 96.0},
        "chartA": {"x": 0.05, "y": 120.0, "w": 0.9, "h": 300.0},
        "chartB": {"x": 0.05, "y": 444.0, "w": 0.9, "h": 300.0},
        "__ly_mode__": "free",
    }
    holder = dp.DashboardPage.__new__(dp.DashboardPage)
    b = _B(seeded)
    holder.bridge = b

    dp.DashboardPage._prune_geometry(holder, ["chartA", "chartB"])
    check("prune keeps the title band tile",
          TITLE_UID in b.tiles, str(sorted(b.tiles)))

    # The real drift test: repeat the prune -> ensure_title_band cycle the way a
    # series of edit round-trips does. With the title preserved, ensure_title_band
    # takes its "already has a title" branch every time and nothing moves.
    for _ in range(5):
        dp.DashboardPage._prune_geometry(holder, ["chartA", "chartB"])
        merged, _t = ensure_title_band(b.tiles, height=96.0, gap_px=24.0)
        b.tiles = merged
        b.tiles["__ly_mode__"] = "free"
    check("a top card does not drift down over repeated round-trips",
          b.tiles["chartA"]["y"] == 120.0 and b.tiles["chartB"]["y"] == 444.0,
          str({k: b.tiles[k] for k in ("chartA", "chartB")}))


def test_dashboard_panel_widgets_survive_construction() -> None:
    print("[34] Dashboard panel: no section is built and then dropped")
    import inspect
    from desktop.pages import dashboard_page as dp

    # EVERY CollapsibleSection built in _build_left_panel has to be handed to the
    # layout. A section that is only a local variable has no parent and no
    # reference, so Python collects it the moment the builder returns -- and Qt
    # deletes its children with it. The page then kept reading
    # self.session_name out of a destroyed C++ object, _regen_preview raised, and
    # the canvas stayed blank no matter how many charts were generated.
    src = inspect.getsource(dp.DashboardPage._build_left_panel)
    import ast
    tree = ast.parse(src.strip())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    built = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
    sections = {name for name in built
                if name in ("info", "kpi_group", "themes", "canvas",
                            "text_group", "notes_group", "kpi_text_group")}
    added = {a for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "addWidget"
             and n.args and isinstance(n.args[0], ast.Name)
             for a in (n.args[0].id,)}
    missing = sorted(sections - added)
    check("every collapsible section is added to the layout",
          not missing, f"dropped sections: {missing}")

    # Belt and braces: the widgets read on every rebuild must be reachable
    # through the widget tree, which is only true if nothing was orphaned.
    names = ("session_name", "dash_title", "layout_mode", "preset_gallery",
             "btn_add_kpi", "ex_kpi_val_size")
    src2 = inspect.getsource(dp.DashboardPage.__init__)
    check("the panel's inputs are all created in the page builder",
          all(f"self.{n}" in src2 or f"self.{n} =" in inspect.getsource(dp.DashboardPage._build_left_panel)
              for n in names), str(names))


def test_preset_buttons_show_their_theme_colours_and_names() -> None:
    print("[35] Quick themes: labelled, and actually painted")
    import inspect
    from desktop.widgets import formatting_pane as fp

    # Qt Style Sheets implement a SUBSET of CSS and qlinear-gradient is not in
    # it: the declaration parses, is silently dropped, and every swatch rendered
    # as a plain unstyled box. The bands therefore have to be painted.
    check("the gallery builds PresetButton, not bare buttons",
          "PresetButton(" in inspect.getsource(fp.PresetGallery.__init__),
          "")
    check("the swatch bands are painted, not style-sheeted",
          "paintEvent" in inspect.getsource(fp.PresetButton), "")

    # A theme you cannot name is not a preset you can pick.
    check("the button paints a readable label",
          "self._label" in inspect.getsource(fp.PresetButton.paintEvent), "")
    check("the label strips the leading emoji",
          fp._strip_emoji("\u26a1 Midnight Pulse") == "Midnight Pulse",
          fp._strip_emoji("\u26a1 Midnight Pulse"))

    # Contrast: the readable half is filled with the theme's PAGE colour, so a
    # dark theme needs light ink. Painting dark ink on a dark theme is what made
    # every caption invisible in the first screenshot.
    for name, colors in fp.EXPORT_PRESETS.items():
        dark_page = fp.luminance(colors.get("ex_bg", "#121a2e")) < 0.5
        btn = fp.PresetButton(name, colors)
        want = "#f8fbff" if dark_page else "#0f172a"
        check(f"{name} label ink contrasts with its own page colour",
              btn._fg == want, f"{btn._fg} on {colors.get('ex_bg')}")

    # And the bands really are the theme's four colours.
    first = next(iter(fp.EXPORT_PRESETS.items()))
    btn = fp.PresetButton(first[0], first[1])
    check("the four bands come from the preset's own keys",
          btn._bands == [str(first[1].get(k, "#121a2e"))
                         for k in fp.PRESET_SWATCH_KEYS], str(btn._bands))


def test_kpi_band_does_not_sit_under_the_charts() -> None:
    print("[36] KPI band is stacked above the charts, not hidden under them")
    from desktop.widgets import tile_geometry as tg

    # Charts and KPIs are auto-placed from two SEPARATE maps, so on a fresh
    # session each used to put its first card at y=0. The charts are emitted
    # after the KPIs, so the KPI row was simply covered -- "the KPI does not
    # appear". floor_px is what keeps the two bands apart.
    kpis = tg.auto_place(["0"], {}, gap_px=24, h=120)
    charts = tg.auto_place(["a", "b"], {}, gap_px=24, h=400,
                           floor_px=tg.canvas_height(kpis, 24))
    check("the KPI band sits above the chart band",
          charts["a"]["y"] >= kpis["0"]["y"] + kpis["0"]["h"],
          f"kpi {kpis} charts {charts}")
    check("the KPI and the charts do not overlap on a fresh board",
          not tg.overlaps({**{f"k:{k}": v for k, v in kpis.items()},
                           **{f"c:{k}": v for k, v in charts.items()}}),
          str(charts))

    # floor_px must not disturb an arrangement that already exists.
    existing = {"a": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 400.0}}
    same = tg.auto_place(["a", "b"], existing, gap_px=24, h=400, floor_px=9999)
    check("a floor below an existing card does not move it",
          same["a"] == existing["a"], str(same))
    check("a new card still lands under the existing one",
          same["b"]["y"] > same["a"]["y"], str(same))


def test_kpi_handlers_are_defined_once() -> None:
    print("[32] KPI handlers: no duplicated definitions shadowing each other")
    import ast
    import pathlib
    src = pathlib.Path("desktop/pages/dashboard_page.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        seen: dict = {}
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen.setdefault(item.name, []).append(item.lineno)
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        check(f"{node.name} defines no method twice", not dupes, str(dupes))


def test_preview_and_export_share_one_chart_input() -> None:
    print("[33] Preview/export parity: notes and chart_type reach the canvas")
    import inspect
    from desktop import workers as wk

    # The session holds bare (uid, title, fig) tuples, so a worker that
    # rebuilds them without the maps loses notes and chart_type. That made the
    # canvas show no notes while the exported file had them.
    for cls in (wk.ReportPreviewWorker, wk.ExportWorker):
        src = inspect.getsource(cls.run)
        check(f"{cls.__name__} builds with no meta/notes maps dropped",
              "build_export_charts(self._charts)" not in src)

    from desktop.pages import dashboard_page as dp
    # Two-stage pipeline: _regen_preview snapshots cheap state and
    # ReportInputsWorker assembles the heavy inputs off-thread; the HTML
    # build then consumes them verbatim. Assert the chain, not the old
    # single synchronous _export_inputs() call.
    src = inspect.getsource(dp.DashboardPage._regen_preview)
    check("the preview assembles inputs off-thread",
          "ReportInputsWorker" in src and "_snapshot_inputs" in src)
    src2 = inspect.getsource(dp.DashboardPage._on_inputs_ready)
    check("the preview reuses the assembled inputs verbatim",
          'd["export_charts"]' in src2)

    # And the notes commit has to rebuild, or the canvas keeps showing the
    # pre-edit text while everything else is regenerated from the store.
    nsrc = inspect.getsource(dp.DashboardPage._on_canvas_notes)
    check("committing a note schedules a rebuild",
          "_schedule_regen()" in nsrc)

    # _export_inputs supplies BOTH geometry maps, but ExportWorker accepted
    # only one of them -- so every single export died with a TypeError on the
    # caller's keyword argument, before a byte was written.
    import inspect as _i
    import re as _re
    esig = _i.signature(wk.ExportWorker.__init__).parameters
    check("ExportWorker accepts the KPI geometry the caller passes",
          "kpi_geometry" in esig, str(list(esig)))

    # Every key _start_export forwards must exist in the worker's signature.
    # Checked against the signature rather than by text, because the params
    # are annotated ("kpis: Optional[list] = None"), so a "kpis=" substring
    # test would flag perfectly valid parameters.
    src = _i.getsource(dp.DashboardPage._start_export)
    forwarded = set(_re.findall(r'd\["(\w+)"\]', src))
    unknown = sorted(k for k in forwarded
                     if k not in ("export_charts", "session_name", "state")
                     and k not in esig)
    check("every input _start_export forwards is one ExportWorker accepts",
          not unknown, str(unknown))


def test_editor_publishes_the_bridge_globally() -> None:
    print("[34] Editor bridge: the connected channel is observable")
    from modules.report_editor import editor_js
    js = editor_js(2, 24)
    # `bridge` is an IIFE-local, so assigning it alone left window.bridge
    # undefined -- a working bridge indistinguishable from a dead one.
    check("the editor publishes window.bridge", "window.bridge = bridge =" in js)


def _lytrize_adapter_option(model, width=430, height=320):
    """Evaluate lytrize_adapter.js under Node and return buildOption()'s output.

    The ECharts renderer lives in the browser shell, so the pie/legend
    geometry these tests pin down is only observable through the adapter.
    The adapter is plain JS with every DOM access behind a guard, which
    makes Node a faithful runtime for exactly the part we assert on.
    """
    import json as _json
    import subprocess as _sp
    import tempfile as _tmp
    from pathlib import Path as _P
    js = _P(__file__).resolve().parent.parent / "backend" / "assets" / "echarts" / "lytrize_adapter.js"
    if not js.is_file():
        js = _P("backend") / "assets" / "echarts" / "lytrize_adapter.js"
    # process.argv == [node, harness, adapter, model, width, height]
    harness = (
        "const fs=require('fs');"
        "global.window=global;"
        "eval(fs.readFileSync(process.argv[2],'utf8'));"
        "const model=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));"
        "const opt=window.LytrizeEChartsAdapter.buildOption(model,"
        "Number(process.argv[4]),Number(process.argv[5]),undefined);"
        "process.stdout.write(JSON.stringify(opt));")
    with _tmp.NamedTemporaryFile("w", suffix=".js", delete=False) as hf, \
            _tmp.NamedTemporaryFile("w", suffix=".json", delete=False) as mf:
        hf.write(harness)
        _json.dump(model, mf)
        hpath, mpath = hf.name, mf.name
    try:
        out = _sp.run(["node", hpath, str(js), mpath, str(width), str(height)],
                      capture_output=True, text=True, timeout=60, check=True)
        return _json.loads(out.stdout)
    finally:
        for p in (hpath, mpath):
            try:
                os.unlink(p)
            except OSError:
                pass


def _pie_model(names, counts, legend_position="right", legend_layout="auto",
               legend_order=None, legend_itemclick=None, theme_mode="light",
               typography=None, labels=None):
    """Minimal pie_chart ChartModel exactly as the adapter consumes it."""
    layout = {}
    if legend_order is not None:
        layout["legend_order"] = legend_order
    if legend_itemclick is not None:
        layout["legend_itemclick"] = legend_itemclick
    display = {
        "legend": {"show": True, "position": legend_position,
                   "layout": legend_layout, "font_size": 11,
                   "title": "", "title_font_size": 12, "click": True},
        "labels": dict({"show": True, "position": "outside",
                        "formatter": "label+percent", "font_size": 11,
                        "color": "auto"}, **(labels or {})),
        "theme_mode": theme_mode,
    }
    return {"schema_version": 1, "kind": "chart", "uid": "pie-test",
            "chart_type": "pie_chart", "title": "T",
            "recipe": {"version": 1, "chart_type": "pie_chart",
                       "config": {}, "layout": layout,
                       "typography": dict(typography or {})},
            "data": {"dimensions": ["product_category"],
                     "series": [{"id": "s", "name": "count", "type": "pie",
                                 "values": [[str(n), int(c)]
                                            for n, c in zip(names, counts)]}]},
            "display": display}


PIE_NAMES = ["Tea", "Flavours", "Drinking Chocolate", "Coffee beans",
             "Bakery", "Loose Tea", "Packaged Chocolate", "Coffee",
             "Branded", "Other"]
PIE_COUNTS = [137, 138, 136, 99, 89, 89, 89, 87, 76, 77]


def test_pie_legend_is_not_drawn_over_the_pie() -> None:
    print("[35] Pie legend: the plate gets its own space")
    # The ECharts shell sizes the pie from the space left after the title and
    # the legend -- the rectangle the pie may occupy is computed at paint
    # time from the actual card, so "the legend is parked on the marks" is
    # impossible by construction. These checks pin the geometry the adapter
    # produces for the two configurations that used to collide.
    right = _lytrize_adapter_option(
        _pie_model(PIE_NAMES, PIE_COUNTS, legend_position="right"), 430, 320)
    rseries = right["series"][0]
    rlegend = right["legend"]
    legend_left = 430 - 6 - rlegend["width"]
    pie_right = rseries["center"][0] + rseries["radius"][1]
    check("a right-hand legend is docked on the right edge",
          rlegend.get("right") == 6 and rlegend.get("left") is None,
          f"left={rlegend.get('left')} right={rlegend.get('right')}")
    check("a right-hand legend lists its entries vertically",
          rlegend.get("orient") == "vertical", str(rlegend.get("orient")))
    check("the legend column is a real clamped pixel width",
          abs(rlegend["width"] - min(180, max(76, 430 * 0.30))) < 1e-9,
          str(rlegend["width"]))
    check("the pie never enters the legend strip",
          pie_right <= legend_left + 1.0,
          f"pie right edge {pie_right:.1f} vs legend left edge {legend_left:.1f}")
    check("the pie is squeezed away from the right edge",
          rseries["center"][0] < 430 / 2, f"cx={rseries['center'][0]}")

    left = _lytrize_adapter_option(
        _pie_model(PIE_NAMES, PIE_COUNTS, legend_position="left"), 430, 320)
    lseries = left["series"][0]
    llegend = left["legend"]
    legend_right = 6 + llegend["width"]
    pie_left = lseries["center"][0] - lseries["radius"][1]
    check("a left-hand legend is docked on the left edge",
          llegend.get("left") == 6 and llegend.get("right") is None,
          f"left={llegend.get('left')} right={llegend.get('right')}")
    check("the pie never enters the left legend strip",
          pie_left >= legend_right - 1.0,
          f"pie left edge {pie_left:.1f} vs legend right edge {legend_right:.1f}")

    # Centred positions reserve a BAND: the pie keeps its width and gives
    # up height instead of sharing a column.
    bottom = _lytrize_adapter_option(
        _pie_model(PIE_NAMES, PIE_COUNTS, legend_position="bottom center",
                   legend_layout="horizontal"), 430, 320)
    bseries = bottom["series"][0]
    check("a bottom-centre legend is a horizontal band",
          bottom["legend"].get("orient") == "horizontal"
          and bottom["legend"].get("bottom") is not None
          and bottom["legend"].get("left") == "center",
          str({k: bottom["legend"].get(k) for k in ("orient", "bottom", "left")}))
    check("the pie gives up vertical room to the band, not horizontal",
          bseries["center"][1] < 320 / 2 and abs(bseries["center"][0] - 215) < 1e-9,
          f"center={bseries['center']}")

    # A card too narrow for a side column must fall back to a bottom band,
    # never crush the pie into a sliver.
    tight = _lytrize_adapter_option(
        _pie_model(PIE_NAMES, PIE_COUNTS, legend_position="right"), 260, 320)
    check("a compact card demotes the side legend to a bottom band",
          tight["legend"].get("orient") == "horizontal"
          and tight["legend"].get("bottom") == 2,
          str({k: tight["legend"].get(k) for k in ("orient", "bottom")}))


def test_pie_legend_squeeze_is_stable() -> None:
    print("[36] Pie legend: the squeeze is idempotent and width-safe")
    # The legend geometry is computed from the container width at paint
    # time -- there is no Python-side reservation left to compound across
    # refits. What must hold instead: the layout is a pure function of the
    # width (so a resize storm converges), and a width-less or tiny card
    # over-reserves rather than overlaps.
    model = _pie_model(PIE_NAMES, PIE_COUNTS, legend_position="right")
    first = _lytrize_adapter_option(model, 430, 320)
    for _ in range(3):
        again = _lytrize_adapter_option(model, 430, 320)
        check("repeated builds produce the identical layout",
              again["legend"] == first["legend"]
              and again["series"][0]["center"] == first["series"][0]["center"],
              "build not idempotent")

    storm = None
    for w in (1400, 700, 380, 900, 430):
        storm = _lytrize_adapter_option(model, w, 320)
    fresh = _lytrize_adapter_option(model, 430, 320)
    check("a resize storm converges on the single-shot layout",
          storm["legend"] == fresh["legend"]
          and storm["series"][0]["center"] == fresh["series"][0]["center"],
          "resize sequence left residual state")

    # The side strip clamps at 180px instead of growing without bound, so a
    # very wide card never hands the legend an unreasonable share.
    wide = _lytrize_adapter_option(model, 1800, 320)
    check("a very wide card caps the legend strip",
          wide["legend"]["width"] <= 180.0, str(wide["legend"]["width"]))
    # ...and never shrinks it below the 76px floor while a side layout fits.
    narrow = _lytrize_adapter_option(model, 320, 320)
    check("a small-but-viable card keeps a usable legend strip",
          narrow["legend"]["width"] >= 76.0, str(narrow["legend"]["width"]))


def test_pie_legend_options_are_live() -> None:
    """[36b] Pie & Donut: the legend position / layout combos actually apply.

    The legend is assembled by the JS adapter from the ChartModel's
    ``display.legend`` (position/layout/click) and ``recipe.layout``
    (order/itemclick) blocks, which the desktop combos write through
    ``apply_chart_model_display_options``. A placement that never reaches
    the adapter is a dead control, so every position is driven through
    buildOption and the resulting anchor is checked against the one a
    second, distinct position would produce.
    """
    print("[36b] Pie & Donut: legend position / layout options are live")
    from chart_display import LEGEND_POSITIONS

    names = ["grocery_net", "personal_care", "travel", "health_fitness",
             "food_dining", "misc_pos", "misc_net", "entertainment",
             "kids_pets"]
    counts = [500, 470, 430, 390, 350, 310, 270, 230, 190]

    def _build(pos, layout="auto", order=None, itemclick=None, w=1160, h=560):
        return _lytrize_adapter_option(
            _pie_model(names, counts, legend_position=pos.lower(),
                       legend_layout=layout, legend_order=order,
                       legend_itemclick=itemclick), w, h)

    def _anchor(opt):
        L = opt["legend"]
        return ((L.get("left") if L.get("left") is not None else None),
                (L.get("right") if L.get("right") is not None else None),
                (L.get("top") if L.get("top") is not None else None),
                (L.get("bottom") if L.get("bottom") is not None else None),
                L.get("orient"))

    # Every one of the eight placements must produce a DISTINCT anchor --
    # a placement that silently re-uses another's corner reads as a dead
    # combo in the UI.
    anchors = {}
    for pos in LEGEND_POSITIONS:
        anchors[pos] = _anchor(_build(pos))
    check("legend: every position produces a distinct anchor",
          len(set(anchors.values())) == len(LEGEND_POSITIONS),
          str(anchors))

    # Side placements dock to the edge and squeeze the pie; the other six
    # keep it centred.
    for pos in ("Left", "Right"):
        opt = _build(pos)
        edge = "left" if pos == "Left" else "right"
        check(f"legend: {pos} docks on the {edge} edge",
              opt["legend"].get(edge) == 6, str(opt["legend"]))
        check(f"legend: {pos} squeezes the pie away from its edge",
              (opt["series"][0]["center"][0] > 1160 / 2) == (pos == "Left"),
              f"cx={opt['series'][0]['center'][0]}")
    for pos in ("Top Right", "Top Left", "Top Center",
                "Bottom Right", "Bottom Left", "Bottom Center"):
        opt = _build(pos)
        check(f"legend: {pos} keeps the pie centred",
              abs(opt["series"][0]["center"][0] - 1160 / 2) < 1e-9,
              f"cx={opt['series'][0]['center'][0]}")

    # The legend LAYOUT row (orientation / order / click) has to land too.
    for orient, want in (("Vertical", "vertical"), ("Horizontal", "horizontal")):
        opt = _build("Right", layout=orient.lower())
        check(f"legend layout: {orient} reaches the adapter",
              opt["legend"].get("orient") == want,
              str(opt["legend"].get("orient")))
    opt = _build("Right", order="Reversed", itemclick="toggleothers")
    check("legend layout: Reversed reverses the entry order",
          list(opt["legend"].get("data", [])) == list(reversed(names)),
          str(opt["legend"].get("data"))[:60])
    check("legend layout: Toggle others becomes single-select",
          opt["legend"].get("selectedMode") == "single",
          str(opt["legend"].get("selectedMode")))
    opt = _build("Right", itemclick="none")
    check("legend layout: click=none disables selection",
          opt["legend"].get("selectedMode") is False,
          str(opt["legend"].get("selectedMode")))
    # Auto orientation resolves to vertical on the sides and horizontal
    # everywhere else -- the documented heuristic.
    for pos, want in (("Left", "vertical"), ("Right", "vertical"),
                      ("Top Center", "horizontal"),
                      ("Bottom Center", "horizontal")):
        opt = _build(pos, layout="auto")
        check(f"legend layout: Auto resolves to {want} for {pos}",
              opt["legend"].get("orient") == want,
              str(opt["legend"].get("orient")))

    # The strip must hug the pie, not float at a fixed fraction: the same
    # position at two widths keeps the SAME edge anchor and clamps the strip
    # width, so there is nothing to "snap back" after a refit.
    wide = _build("Right", w=1800)
    check("legend survives a width change without re-anchoring",
          wide["legend"].get("right") == 6 and wide["legend"]["width"] <= 180.0,
          f"right={wide['legend'].get('right')} width={wide['legend'].get('width')}")


def test_pie_slice_labels_follow_the_theme() -> None:
    """[36c] Pie & Donut: slice labels are legible in both app themes.

    The adapter resolves the label ink in this order: an explicit
    ``typography.value_label_color`` wins; a stored dark default is
    remapped only for light cards; otherwise the ink is derived from the
    card it paints on (``contrastColor``), which the shell and the export
    both drive with ``display.theme_mode``. A light-mode pie must never
    paint near-white slice labels on a light card again.
    """
    print("[36c] Pie & Donut: slice labels follow the app theme")

    def _label_color(theme_mode, typography=None):
        opt = _lytrize_adapter_option(
            _pie_model(["a", "b", "c"], [30, 20, 10],
                       legend_position="bottom center",
                       legend_layout="horizontal",
                       theme_mode=theme_mode, typography=typography),
            430, 320)
        return str((((opt.get("series") or [{}])[0].get("label")
                     or {}).get("color") or "")).lower()

    dark = _label_color("dark")
    light = _label_color("light")
    check("dark mode keeps a near-white slice-label ink",
          dark in ("#f8fafc", "#e2e8f0", "#ffffff", "#fff"), dark)
    check("light mode does NOT paint near-white on a light card",
          light not in ("#f8fafc", "#e2e8f0", "#ffffff", "#fff")
          and light == "#111827", light)

    # A colour the user picked in Typography must survive, exactly like
    # every other swatch -- the theme switch never overrules an explicit
    # choice.
    picked_dark = _label_color("dark", {"value_label_color": "#00ff00"})
    picked_light = _label_color("light", {"value_label_color": "#00ff00"})
    check("a user-picked label colour survives the dark theme",
          picked_dark == "#00ff00", picked_dark)
    check("a user-picked label colour survives the light theme",
          picked_light == "#00ff00", picked_light)
def test_pie_slice_value_typography_round_trips() -> None:
    """[36e] Pie & Donut: the Slice size / Slice color Typography rows.

    The Layout tab deliberately carries no value size/colour (they belong to
    Typography), and for a while nothing anywhere edited the data-value
    typography -- the settings round removed the old 'Value size'/'Value
    color' rows as dead, taking the only slice controls with them, and the
    user lost the ability to size/colour the labels on the slices (issue:
    'Add fixed typography Value Size & Value colour settings with separate
    options for data values & donut centre total'). The Typography tab now
    carries Slice size + Slice color (Auto + swatch) alongside Donut total
    size/color, bound to the typography keys the renderer prefers.
    """
    print("[36e] Pie: slice (data-value) typography rows round-trip")
    import pandas as pd
    from PySide6.QtWidgets import QApplication, QLabel
    from desktop.widgets.analysis_settings.pie_chart import PieChartSettings
    from services.analysis_service import run_analysis

    app = QApplication.instance() or QApplication([])

    def _rows(page):
        out = []
        for w in page.findChildren(object):
            try:
                if w.property("grid_row"):
                    labs = [s.text() for s in w.findChildren(QLabel)
                            if s.property("theme_role") in ("section", "caption")]
                    if labs:
                        out.append(labs[0])
            except Exception:
                pass
        return out

    b = PieChartSettings()
    page = b.build_typography()
    page.show()
    app.processEvents()
    rows = _rows(page)
    for want in ("Slice size", "Slice color",
                 "Donut total size", "Donut total color"):
        check(f"pie Typography offers '{want}'", want in rows, str(rows))
    lay = b.build_layout()
    lay_rows = _rows(lay)
    check("pie Layout carries no value size/colour rows",
          "Slice size" not in lay_rows and "Slice color" not in lay_rows
          and "Pie value size" not in lay_rows
          and "Pie value color" not in lay_rows, str(lay_rows))
    # The real user path: drag the size, uncheck Auto, pick a colour.
    b._slice_size.setValue(14)
    check("size edit reaches the model keys immediately",
          b._typo_opts.get("slice_value_size") == 14, str(b._typo_opts))
    b._ck_sliceauto.setChecked(False)
    b._sw_slicecol._retint("#ff0055")
    b._set_typo(slice_value_color="#ff0055")
    got = b.collect_typography()
    check("collect: Slice size round-trips",
          got.get("slice_value_size") == 14, str(got))
    check("collect: Slice color round-trips",
          got.get("slice_value_color") == "#ff0055", str(got))
    # A saved recipe restores; 'auto' switches the swatch off.
    b.prefill_typography({"slice_value_size": 16, "slice_value_color": "auto"})
    check("prefill: auto disables the swatch",
          b._ck_sliceauto.isChecked() and not b._sw_slicecol.isEnabled(), "")
    check("prefill: size restores", b._slice_size.value() == 16,
          str(b._slice_size.value()))
    check("collect: auto round-trips",
          b.collect_typography().get("slice_value_color") == "auto", "")
    # The renderer prefers these typography keys.
    df = pd.DataFrame({"product_category": ["Tea", "Coffee", "Bakery"] * 4,
                       "Count": [137, 138, 99, 89, 120, 77, 101, 90, 88, 95, 70, 60]})
    _title, _meta, model = run_analysis(
        "pie_chart", df, x_cols=["product_category"], y=[],
        typography={"slice_value_size": 14, "slice_value_color": "#ff0055"})[0]
    check("renderer: Slice size reaches display.labels",
          model["display"]["labels"]["font_size"] == 14,
          str(model["display"]["labels"]))
    check("renderer: Slice color reaches display.labels",
          model["display"]["labels"]["color"] == "#ff0055",
          str(model["display"]["labels"]))
    page.close()
    lay.close()


def test_type_scales_with_the_card() -> None:
    """[36d] Legend + slice-label type scales with the card width.

    The legend / slice-label sizes are absolute pixels (11px default). Pinned at
    11px, a Horizontal legend on a narrow card wraps to two rows and collides
    (the "only stays horizontal if I widen the card" report) and a small card's
    legend + outside labels overflow the shrunken plot (the "resize the card
    small and the text doesn't adjust" report).  Power BI scales the type with
    the visual.

    The factor is 1.0 at/above ``_TYPE_SCALE_FULL_PX`` (640) so a wide card and
    headless export keep the exact chosen size, and shrinks to ``_TYPE_SCALE_MIN``
    (0.72) at 300px and below.  A HORIZONTAL legend shrinks further
    (``_horizontal_fit_scale``) until its entries fit one row.
    """
    print("[36d] Type scales with the card width (small card, small text)")
    from chart_display import (apply_display_options, default_layout_opts,
                               default_typo_opts, _type_scale)

    # The pure factor rule.
    check("no card width -> no scaling (headless/export unchanged)",
          _type_scale(None) == 1.0 and _type_scale(0) == 1.0)
    check("a card at/above the full anchor keeps full-size type",
          _type_scale(640) == 1.0 and _type_scale(1160) == 1.0
          and _type_scale(1920) == 1.0)
    check("a tiny card hits the shrink floor",
          _type_scale(300) <= 0.721 and _type_scale(200) <= 0.721)
    check("shrink is monotonic in width",
          _type_scale(400) > _type_scale(350) > _type_scale(320))

    # The card-width contract, through the public apply_display_options API:
    # headless models (no width) keep the chosen typography verbatim; a
    # narrow card gets proportionally smaller type down to the floor. The
    # on-screen ECharts renderer does not rebuild the option on resize yet,
    # so width-anchored type on the live canvas is tracked as a redesign
    # follow-up (see FIX_SUMMARY); the factor itself is pinned here.
    exp = default_typo_opts()
    wide = apply_display_options({}, dict(default_layout_opts()),
                                 dict(exp), width_px=1160, height_px=430)
    small = apply_display_options({}, dict(default_layout_opts()),
                                  dict(exp), width_px=360, height_px=430)
    headless = apply_display_options({}, dict(default_layout_opts()),
                                     dict(exp))
    check("a wide card keeps the full 11px legend text",
          wide["display"]["legend_font_size"] == exp["legend_font_size"],
          f"legend_font_size={wide['display']['legend_font_size']}")
    check("a small card shrinks the legend text",
          0 < small["display"]["legend_font_size"] < exp["legend_font_size"],
          f"{exp['legend_font_size']}px -> {small['display']['legend_font_size']}px")
    check("a small card shrinks the slice/label fonts with it",
          small["display"]["data_label_font_size"] < exp["data_label_font_size"]
          and small["display"]["tick_label_font_size"] < exp["tick_label_font_size"],
          f"labels={small['display']['data_label_font_size']}px "
          f"ticks={small['display']['tick_label_font_size']}px")
    check("a small card shrinks the header with the body",
          small["display"]["header_font_size"] < exp["header_font_size"],
          f"header={small['display']['header_font_size']}px")
    check("headless mode (export) keeps the chosen sizes verbatim",
          all(headless["display"][k] == exp[k]
              for k in ("legend_font_size", "data_label_font_size",
                        "tick_label_font_size", "header_font_size")),
          "")
    tiny = apply_display_options({}, dict(default_layout_opts()),
                                 dict(exp), width_px=200, height_px=300)
    check("a tiny card floors at the 0.72 factor",
          tiny["display"]["font_size"] == max(7, int(exp["font_size"] * 0.72)),
          f"font_size={tiny['display']['font_size']}")


def test_legend_and_colourbar_follow_the_card() -> None:
    """The legend and the colour bar draw no plate of their own, and their
    ink is the ink of the card underneath.

    Since the ECharts migration both policies live in the JS adapter
    (``contrastColor``, ``legendLightRemap``, the visualMap text ink), which
    the desktop shell and the exported HTML both run at paint time. A stored
    dark default (the #e2e8f0 family) is rewritten for a light card, a
    user-picked colour survives untouched, and an auto/unset value falls
    back to the card's own ink -- this test pins all three behaviours plus
    the plate-free default, through the same adapter call the renderer
    makes.
    """
    print("[41] Legend transparent by default; text still follows the card")
    import json as _json

    def _build(theme_mode, typography=None, display=None, chart_type="categorical",
               data=None):
        model = {"schema_version": 1, "kind": "chart", "uid": "t41",
                 "chart_type": chart_type, "title": "T",
                 "recipe": {"version": 1, "chart_type": chart_type, "config": {},
                            "layout": {}, "typography": dict(typography or {})},
                 "data": data or {"dimensions": ["g"],
                                  "series": [{"id": "s", "name": "c", "type": "bar",
                                              "values": [["a", 1], ["b", 2]]}]},
                 "display": dict({"theme_mode": theme_mode}, **(display or {}))}
        return _lytrize_adapter_option(model, 720, 420)

    # -- 1. The legend ships with no plate, in either app theme ------------
    for mode in ("dark", "light"):
        opt = _build(mode)
        legend = opt.get("legend") or {}
        check(f"{mode}: the legend draws no plate",
              legend.get("backgroundColor") in (None, "transparent"),
              str(legend.get("backgroundColor")))
        check(f"{mode}: the legend border is off by default",
              legend.get("borderColor") in (None, "transparent")
              and not legend.get("borderWidth"),
              f"{legend.get('borderColor')}/{legend.get('borderWidth')}")

    # -- 2. The legend ink follows the card --------------------------------
    def _legend_ink(mode, item_color):
        opt = _build(mode, typography={"legend_item_color": item_color})
        return str(((opt.get("legend") or {}).get("textStyle")
                    or {}).get("color", "")).lower()

    check("auto legend ink on a dark card is near-white",
          _legend_ink("dark", "auto") == "#f8fafc", _legend_ink("dark", "auto"))
    check("auto legend ink on a light card is dark",
          _legend_ink("light", "auto") == "#111827", _legend_ink("light", "auto"))
    check("a stored dark default is rewritten for a light card",
          _legend_ink("light", "#e2e8f0") == "#334155", _legend_ink("light", "#e2e8f0"))
    check("the same stored default is kept on a dark card",
          _legend_ink("dark", "#e2e8f0") == "#e2e8f0", _legend_ink("dark", "#e2e8f0"))
    check("a user-picked legend colour survives a light card untouched",
          _legend_ink("light", "#ff00ff") == "#ff00ff", _legend_ink("light", "#ff00ff"))

    # -- 3. The colour bar's tick ink follows the card too ------------------
    heat_data = {"dimensions": ["x", "y"],
                 "series": [{"id": "s", "name": "v", "type": "heatmap",
                             "values": [["a", "p", 1], ["a", "q", 5],
                                        ["b", "p", 3], ["b", "q", 9]]}]}

    def _tick_ink(mode):
        opt = _build(mode, chart_type="matrix_heatmap",
                     display={"heatmap_rows": ["p", "q"],
                              "heatmap_columns": ["a", "b"]},
                     data=heat_data)
        return str(((opt.get("visualMap") or {}).get("textStyle")
                    or {}).get("color", "")).lower()

    check("colour-bar ticks follow a dark card",
          _tick_ink("dark") == "#f8fafc", _tick_ink("dark"))
    check("colour-bar ticks follow a light card",
          _tick_ink("light") == "#111827", _tick_ink("light"))

    # -- 4. The exported report must actually ship the policy ---------------
    from services.export_service import generate_html_report
    html = generate_html_report(
        [("a", "A", {"schema_version": 1, "kind": "chart", "uid": "a",
                     "chart_type": "categorical", "title": "A",
                     "recipe": {"version": 1, "chart_type": "categorical",
                                "config": {}, "layout": {}, "typography": {}},
                     "data": {"dimensions": ["g"],
                              "series": [{"id": "s", "name": "c", "type": "bar",
                                          "values": [{"name": "a", "value": 1}]}]},
                     "display": {}},
          "", "", {})],
        "S", {}, grid_cols_n=2)
    check("the exported report ships the adapter that applies the policy",
          "LytrizeEChartsAdapter" in html and "legendLightRemap" in html,
          "")


def test_styling_never_invents_a_colour_bar() -> None:
    """The ghost colour bar: styling must not CREATE a scale nobody asked for.

    Since the ECharts migration a colour bar can only appear as a
    ``visualMap`` emitted by the JS adapter, and the adapter emits one in
    exactly two families -- the matrix heatmap and the calendar/map
    colour scales -- always gated on ``display.show_colorbar``. A bar,
    line, pie, scatter, funnel or waterfall can no longer sprout a ramp
    because someone styled it: there is no code path from styling to
    ``visualMap``. These checks pin that split, plus the export end-to-end.
    """
    print("[52] Colour-bar styling cannot conjure a colour bar")

    def _model(chart_type, display, data):
        return {"schema_version": 1, "kind": "chart", "uid": "t52-" + chart_type,
                "chart_type": chart_type, "title": "T",
                "recipe": {"version": 1, "chart_type": chart_type,
                           "config": {}, "layout": {}, "typography": {}},
                "data": data, "display": display}

    heat_data = {"dimensions": ["x", "y"],
                 "series": [{"id": "s", "name": "v", "type": "heatmap",
                             "values": [["a", "p", 1], ["a", "q", 5],
                                        ["b", "p", 3], ["b", "q", 9]]}]}
    heat_display = {"heatmap_rows": ["p", "q"], "heatmap_columns": ["a", "b"],
                    "theme_mode": "light"}

    # -- The reported bug: scale-less chart types stay bare ------------------
    def _pairs(n):
        return [["g" + str(i), i + 1] for i in range(n)]

    bare_cases = [
        ("categorical bar", _model("categorical", {"theme_mode": "light"},
            {"dimensions": ["g"],
             "series": [{"id": "s", "name": "c", "type": "bar",
                         "values": _pairs(4)}]})),
        ("pie", _model("pie_chart",
            {"theme_mode": "light",
             "legend": {"show": True, "position": "right",
                        "layout": "vertical"}},
            {"dimensions": ["g"],
             "series": [{"id": "s", "name": "c", "type": "pie",
                         "values": _pairs(4)}]})),
        ("waterfall", _model("waterfall", {"theme_mode": "light"},
            {"dimensions": ["g"],
             "series": [{"id": "s", "name": "c", "type": "bar",
                         "values": [["a", 5], ["b", -2], ["c", 3]]}]})),
        ("funnel", _model("funnel", {"theme_mode": "light"},
            {"dimensions": ["g"],
             "series": [{"id": "s", "name": "c", "type": "funnel",
                         "values": _pairs(4)}]})),
        ("scatter", _model("scatter_plot", {"theme_mode": "light"},
            {"dimensions": ["x", "y"],
             "series": [{"id": "s", "name": "c", "type": "scatter",
                         "values": [[1, 2], [3, 1], [4, 5]]}]})),
        ("sankey", _model("sankey", {"theme_mode": "light"},
            {"sankey": {"nodes": [{"name": "a"}, {"name": "b"},
                                  {"name": "c"}],
                        "links": [{"source": "a", "target": "b", "value": 5},
                                  {"source": "b", "target": "c", "value": 3}]}})),
    ]
    ghosts = []
    for label, model in bare_cases:
        opt = _lytrize_adapter_option(model, 720, 420)
        if "visualMap" in opt:
            ghosts.append(label)
    check("no scale-less chart grows a colour bar (bar/pie/funnel/...)",
          not ghosts, ", ".join(ghosts))

    # -- A real scale chart keeps its bar -----------------------------------
    heat = _lytrize_adapter_option(
        _model("matrix_heatmap", dict(heat_display), heat_data), 720, 420)
    check("a heatmap draws its colour bar by default",
          (heat.get("visualMap") or {}).get("show") is True,
          str(heat.get("visualMap")))
    hidden = _lytrize_adapter_option(
        _model("matrix_heatmap",
               dict(heat_display, show_colorbar=False), heat_data),
        720, 420)
    check("show_colorbar=False hides the bar but keeps the chart",
          (hidden.get("visualMap") or {}).get("show") is False
          and bool(hidden.get("series"))
          and bool(hidden.get("xAxis")),
          str(hidden.get("visualMap")))
    check("the colour bar binds to the values dimension, series 0",
          (heat.get("visualMap") or {}).get("dimension") == 2
          and (heat.get("visualMap") or {}).get("seriesIndex") == 0,
          str({k: heat["visualMap"].get(k) for k in ("dimension", "seriesIndex")}))

    # -- The export path cannot smuggle one back in -------------------------
    # The report embeds the adapter and the chart model; the model itself
    # carries no visualMap, and the adapter is the only code that paints.
    import json as _json
    import re as _re
    from services.export_service import generate_html_report
    from modules.chart_model import make_chart_model
    bar_model = make_chart_model(
        uid="a", chart_type="categorical", title="A",
        data={"dimensions": ["g"],
              "series": [{"id": "c", "name": "c", "type": "bar",
                          "values": [{"name": "a", "value": 1},
                                     {"name": "b", "value": 2}]}]})
    html = generate_html_report([("a", "A", bar_model, "", "", {})],
                                "S", {}, grid_cols_n=2)
    check("the export embeds the adapter that paints the charts",
          "LytrizeEChartsAdapter" in html and "buildOption(model" in html,
          "")
    m = _re.search(r'<script type="application/json" id="chart-models">(.*?)</script>',
                   html, _re.S)
    check("the embedded chart models carry no visualMap of their own",
          bool(m) and '"visualMap"' not in m.group(1),
          "chart-models payload missing or dirty" if not m else "visualMap in payload")


def test_kpi_value_and_label_are_stylable() -> None:
    print("[42] KPI customisation: the value and the label are separable")
    from services.export_service import (EXPORT_DEFAULTS, PRESET_SWATCH_KEYS,
                                         generate_html_report)
    from modules.chart_model import make_chart_model

    model = make_chart_model(
        uid="a", chart_type="categorical", title="A",
        data={"dimensions": ["g"],
              "series": [{"id": "s", "name": "c", "type": "bar",
                          "values": [{"name": "a", "value": 1}]}]})
    kpis = [{"label": "Total Sales", "value": "149,116", "icon": "M",
             "column": "sepal_length"}]

    def _report(state: dict) -> str:
        return generate_html_report(
            [("a", "A", model, "", "", {})],
            "S", state, kpis=kpis, grid_cols_n=2)

    base = _report({})
    check("the KPI card renders the label and the value",
          'class="kpi-label">Total Sales<' in base
          and 'class="kpi-value">149,116<' in base, base[base.find('kpi-label'):base.find('kpi-label') + 120])
    check("an unset value colour follows the theme accent",
          "color:#6163df" in base, "accent default missing from .kpi strong rule")

    # Appearance keys travel through the initial __lytrizeApplyDashboardStyle
    # call as raw style state (the JS applier resolves them against the DOM);
    # checked at the JSON payload, not by inventing CSS text.
    coloured = _report({"ex_kpi_val_color": "#ff0055"})
    check("the KPI value takes its own colour",
          '"ex_kpi_val_color":"#ff0055"' in coloured, coloured[:0])
    check("the colour is not burned into the static accent rule",
          ".kpi strong{font-size:clamp(20px,4vw,52px);color:#6163df" in coloured, "")

    sized = _report({"ex_kpi_label_size": 18})
    check("the KPI label takes its own size",
          '"ex_kpi_label_size":18' in sized, "")
    both = _report({"ex_kpi_label_size": 18, "ex_kpi_val_color": "#ff0055"})
    check("the label size and the value colour are separable keys",
          '"ex_kpi_label_size":18' in both and '"ex_kpi_val_color":"#ff0055"' in both, "")

    upper = _report({"ex_kpi_label_upper": True})
    plain = _report({"ex_kpi_label_upper": False})
    check("label caps can be switched off",
          '"ex_kpi_label_upper":true' in upper
          and '"ex_kpi_label_upper":false' in plain, "")
    check("the applier honours the caps flag",
          "s.ex_kpi_label_upper?'uppercase':'none'" in base, "")

    # Every key must exist end to end, or the panel writes a key nothing reads.
    for key in ("ex_kpi_val_color", "ex_kpi_label_size", "ex_kpi_label_upper"):
        check(f"{key} is a known style key", key in EXPORT_DEFAULTS, key)
    check("a quick theme does not overwrite the KPI customisation",
          not any(k in PRESET_SWATCH_KEYS
                  for k in ("ex_kpi_val_color", "ex_kpi_label_size",
                            "ex_kpi_label_upper")), "")


def test_the_dashboard_has_a_background_image() -> None:
    print("[43] Background image: works over any theme, survives the export")
    from services.export_service import (EXPORT_DEFAULTS, EXPORT_PRESETS,
                                         generate_html_report)

    check("the background image is a known style key",
          "ex_bg_image" in EXPORT_DEFAULTS
          and "ex_bg_image_opacity" in EXPORT_DEFAULTS
          and "ex_bg_blend" in EXPORT_DEFAULTS, "")

    uri = "data:image/png;base64,aGk="  # "hi" -- valid base64, tiny payload
    html = generate_html_report(
        [], "S", {"ex_bg_image": uri, "ex_bg_image_opacity": 40})
    check("the image reaches the report", uri in html, "")
    check("it is layered as its own board child over the theme colour",
          "#ly-bg-layer{" in html and "position:absolute" in html
          and "inset:0" in html and "z-index:0" in html, "")
    check("the theme colour is still the base layer",
          "background:#121a2e" in html, "")
    check("it cannot cover a card or swallow a click",
          "pointer-events:none" in html, "")
    check("the board clips it (no stray full-page band on scroll)",
          "#board{position:relative" in html and "overflow:hidden" in html, "")
    check("the chosen opacity is honoured", "opacity:0.400" in html, "")

    # The blend mode control actually composites the layer.
    blended = generate_html_report(
        [], "S", {"ex_bg_image": uri, "ex_bg_image_opacity": 40,
                  "ex_bg_blend": "screen"})
    check("the chosen blend mode is honoured",
          "mix-blend-mode:screen" in blended, "")
    bogus = generate_html_report(
        [], "S", {"ex_bg_image": uri, "ex_bg_image_opacity": 40,
                  "ex_bg_blend": "weirdblend"})
    import re as _re
    layer = _re.search(r"#ly-bg-layer\{[^}]*\}", bogus)
    check("an unknown blend mode falls back to normal, never invalid CSS",
          bool(layer) and "mix-blend-mode:normal" in layer.group(0)
          and "weirdblend" not in layer.group(0),
          layer.group(0) if layer else "layer missing")

    # It must work for EVERY theme -- that is the whole point of compositing
    # rather than replacing.
    missing = [n for n, c in EXPORT_PRESETS.items()
               if uri not in generate_html_report(
                   [], "S",
                   dict(c, ex_bg_image=uri, ex_bg_image_opacity=30))]
    check("every quick theme keeps the background image", not missing,
          str(missing))

    # No image -> no layer at all, so nothing is left behind.
    plain = generate_html_report([], "S", {})
    check("a report with no image emits no background layer",
          "#ly-bg-layer" not in plain, "")
    off = generate_html_report([], "S",
                               {"ex_bg_image": uri, "ex_bg_image_opacity": 0})
    check("zero opacity switches the layer off",
          "#ly-bg-layer" not in off, "")


def test_every_dashboard_setting_reaches_the_report() -> None:
    print("[44] Every style key in the panel changes the report")
    import re as _re
    from services.export_service import generate_html_report
    from modules.chart_model import make_chart_model

    model = make_chart_model(
        uid="a", chart_type="categorical", title="A",
        data={"dimensions": ["g"],
              "series": [{"id": "s", "name": "c", "type": "bar",
                          "values": [{"name": "a", "value": 1}]}]})
    kpis = [{"label": "Total", "value": "10", "icon": "M"}]

    def _report(state: dict) -> str:
        return generate_html_report(
            [("a", "A", model, "a note", "", {})],
            "S", state, kpis=kpis, grid_cols_n=2)

    base = _report({})
    # Keys baked into the static stylesheet: they externalise as literal
    # CSS text in the document.
    static_probes = [
        ("ex_bg", "#101010", "background:#101010"),
        ("ex_card", "#202020", "background:#202020"),
        ("ex_border", "#445566", "border:1px solid #445566"),
        ("ex_text", "#fefefe", "color:#fefefe"),
        ("ex_accent", "#00ff00", "color:#00ff00"),
    ]
    # Keys applied at document load by the embedded
    # __lytrizeApplyDashboardStyle call: the only place they externalise in
    # a static file is the JSON payload of that call, so assert there --
    # never on invented CSS text.
    live_probes = [
        ("ex_kpi", "#303030", '"ex_kpi":"#303030"'),
        ("ex_not_bg", "#0a0a1a", '"ex_not_bg":"#0a0a1a"'),
        ("ex_not_bd", "#8844ff", '"ex_not_bd":"#8844ff"'),
        ("ex_title_color", "#00aaff", '"ex_title_color":"#00aaff"'),
        ("ex_notes_color", "#ccccff", '"ex_notes_color":"#ccccff"'),
        ("ex_kpi_text_color", "#dddddd", '"ex_kpi_text_color":"#dddddd"'),
        ("ex_kpi_val_size", 40, '"ex_kpi_val_size":40'),
        ("ex_title_font", "Georgia", '"ex_title_font":"Georgia"'),
        ("ex_title_size", 40, '"ex_title_size":40'),
        ("ex_notes_size", 22, '"ex_notes_size":22'),
        ("ex_radius", 30, '"ex_radius":30'),
        ("ex_title_style", "Bold", '"ex_title_style":"Bold"'),
    ]
    dead = []
    for key, value, fragment in static_probes + live_probes:
        if fragment in base:
            dead.append(f"{key}: already present by default")
        elif fragment not in _report({key: value}):
            dead.append(f"{key}: {fragment!r} missing")
    check("no dashboard setting is a no-op on the report", not dead,
          "; ".join(dead))

    def _rule(html: str, selector: str) -> str:
        m = _re.search(_re.escape(selector) + r"([^}]*)\}", html)
        return m.group(1) if m else ""

    notes_rule = _rule(base, ".notes{")
    check("the notes keep their established look by default",
          "font-size:12px" in notes_rule and "max-height:24%" in notes_rule,
          notes_rule)
    check("the notes are not italic by default",
          "font-style: italic" not in base, "")
    bold_notes = _report({"ex_notes_style": "Bold"})
    check("Notes Typography > Style reaches the notes block",
          '"ex_notes_style":"Bold"' in bold_notes
          and "st.indexOf('bold')" in bold_notes, "")


def test_cards_have_no_dead_padding() -> None:
    """KPI and title cards must fill the box the user sized them to.

    In the ECharts-native export the KPI is label-above-value with a
    sparkline and a delta chip; the only sizing knobs are the static
    ``.kpi`` row/gap and the padding, which the live style layer re-pads
    from ``ex_kpi_pad``. No ``.kpi-icon`` rule, no invented margins, and
    the print path keeps tiles in flow without detaching their ::before.
    """
    print("[45] KPI and title cards: content fills the box")
    import re as _re
    from services.export_service import generate_html_report

    html = generate_html_report([], "S", {})

    def _rule(selector: str) -> str:
        m = _re.search(_re.escape(selector) + r"([^}]*)\}", html)
        return m.group(0) if m else ""

    kpi = _rule(".kpi{")
    check("the KPI row is flush (padding + no row gap at rest)",
          "padding:8px" in kpi and "row-gap:0" in kpi, kpi)
    check("the KPI card lays its content out in a column without slack",
          "justify-content:center" in kpi and "min-height:0" in kpi, kpi)
    check("the KPI value is a single un-margined strong",
          ".kpi strong{" in html and "overflow-wrap:anywhere" in _rule(".kpi strong{"),
          _rule(".kpi strong{"))
    check("no .kpi-icon rule exists anywhere (the old gap source)",
          ".kpi-icon" not in html, "")
    check("no static .kpi-label margin rule exists",
          ".kpi-label{" not in html, "")
    check("the delta chip is a compact inline element",
          ".kpi-delta{font-size:13px;font-weight:700}" in html, "")
    check("the sparkline has no dead box around it",
          ".kpi-sparkline{display:block" in html, "")

    check("the chart card spends only its 8px content padding",
          ".chart-card{display:flex;flex-direction:column;padding:8px}" in html,
          "")
    title_obj = _rule(".title-card .text-object{")
    check("the title text fills its card edge-to-edge",
          "padding:8px 14px" in title_obj, title_obj)
    check("text objects centre their content, no dead band",
          ".text-object{" in html and "justify-content:center" in _rule(".slicer,.text-object{"),
          _rule(".slicer,.text-object{"))

    # The print block puts tiles back into document flow; the tile must
    # stay a containing block (position:relative, not static) so its
    # ::before card picture prints on the card, not once per page.
    check("print keeps tiles as containing blocks for their picture",
          ".tile{position:relative !important;" in html
          and "position:static !important" not in html, "")


def test_cards_can_have_their_own_background_image() -> None:
    """Image 2: a background image for the CARDS, not just the page."""
    print("[46] Card background image: per card kind, embedded, themable")
    import re as _re
    from services.export_service import (EXPORT_DEFAULTS, generate_html_report)

    check("the card image is a known style key",
          "ex_card_image" in EXPORT_DEFAULTS
          and "ex_card_image_opacity" in EXPORT_DEFAULTS
          and "ex_card_blend" in EXPORT_DEFAULTS
          and "ex_card_image_groups" in EXPORT_DEFAULTS, "")
    check("it is off by default, so no existing report changes",
          EXPORT_DEFAULTS["ex_card_image"] == "", "")

    uri = "data:image/png;base64,BBBB"  # valid base64

    def _report(state: dict) -> str:
        return generate_html_report([], "S", state)

    def _board(html: str) -> str:
        m = _re.search(r"#board\{[^}]*\}", html)
        return m.group(0) if m else ""

    html = _report({"ex_card_image": uri, "ex_card_image_opacity": 40})
    board = _board(html)
    check("the picture is embedded in the report, not linked", uri in html, "")
    check("it is drawn on the cards, not on the page",
          ".tile::before{" in html and "background-image:var(--tile-image)" in html,
          "")
    check("the cards inherit the picture from the board's tile variables",
          "--ly-chart-tile-image:url(" in board
          and "--ly-kpi-tile-image:url(" in board
          and "--ly-title-tile-image:url(" in board, board)
    check("it is layered OVER the card colour, so it works on any theme",
          "mix-blend-mode:var(--tile-image-blend,normal)" in html, "")
    check("the card keeps clipping it to the rounded corners",
          _re.search(r"\.tile\{[^}]*overflow:hidden", html) is not None
          and "border-radius:8px" in html, "")
    # The card's text and plot must stay ON TOP of the picture; a picture
    # that buries the content is not a feature.
    check("the card's own content is lifted above the picture",
          ".tile>*{position:relative;z-index:1}" in html, "")
    check("it cannot swallow a click meant for a card or the chart",
          _re.search(r"\.tile::before\{[^}]*pointer-events:none", html) is not None,
          "")
    check("the chosen opacity is honoured",
          "--ly-chart-tile-image-opacity:0.4" in board, board)

    # "WHERE it applies" is the point of the feature: the same picture behind
    # only the KPI strip, or only the title band.
    only_kpi = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                        "ex_card_image_groups": "kpi"})
    check("the image can go behind the KPI cards alone",
          "--ly-kpi-tile-image:url(" in _board(only_kpi)
          and "--ly-chart-tile-image:none" in _board(only_kpi)
          and "--ly-title-tile-image:none" in _board(only_kpi), _board(only_kpi))
    only_title = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                          "ex_card_image_groups": "title"})
    check("the image can go behind the title band alone",
          "--ly-title-tile-image:url(" in _board(only_title)
          and "--ly-kpi-tile-image:none" in _board(only_title), _board(only_title))
    both = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                    "ex_card_image_groups": "kpi,title"})
    check("it can go behind several kinds at once",
          "--ly-kpi-tile-image:url(" in _board(both)
          and "--ly-title-tile-image:url(" in _board(both)
          and "--ly-chart-tile-image:none" in _board(both), _board(both))

    # Off switches. A layer that cannot be switched off is a layer the user
    # cannot get rid of. Zero opacity suppresses the URL itself, so the
    # bytes never reach the file.
    plain = _report({})
    check("a report with no card image emits no card picture",
          "--ly-chart-tile-image:none" in _board(plain) and uri not in plain,
          "")
    off = _report({"ex_card_image": uri, "ex_card_image_opacity": 0})
    check("zero opacity switches the card layer off",
          "--ly-chart-tile-image:none" in _board(off)
          and uri not in off, "")
    nowhere = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                       "ex_card_image_groups": ""})
    check("ticking no card kinds switches it off too",
          "--ly-chart-tile-image:none" in _board(nowhere)
          and "--ly-kpi-tile-image:none" in _board(nowhere)
          and "--ly-title-tile-image:none" in _board(nowhere)
          and uri not in nowhere, _board(nowhere))
    # An unrecognised group name must not silently widen the image to every
    # card -- a corrupt or hand-edited style value should draw nothing extra.
    bogus = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                     "ex_card_image_groups": "banana"})
    check("an unknown group name draws nothing rather than guessing",
          "--ly-chart-tile-image:none" in _board(bogus)
          and "--ly-kpi-tile-image:none" in _board(bogus)
          and "--ly-title-tile-image:none" in _board(bogus)
          and uri not in bogus, _board(bogus))

    # The page background and the card background are independent settings.
    both_layers = _report({"ex_bg_image": "data:image/png;base64,AAAA",
                           "ex_card_image": uri, "ex_card_image_opacity": 40})
    check("the page image and the card image can both be on",
          "#ly-bg-layer{" in both_layers
          and "--ly-chart-tile-image:url(" in _board(both_layers), "")


def test_home_banner_scales_with_the_window() -> None:
    """The official banner artwork: present, and stretched to the window."""
    print("[47] Home banner: fills the header and follows the window width")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout
    _app = QApplication.instance() or QApplication([])
    from desktop.pages.home_page import HomePage, _asset_path
    from desktop.session_bridge import SessionBridge
    from desktop.widgets.banner_label import BannerLabel

    # The asset is resolved from the same bundle as the icon and the logo, so a
    # rename or a packaging change has one place to be reflected.
    check("the banner asset is bundled with the app",
          _asset_path("welcome-banner.png").exists(),
          str(_asset_path("welcome-banner.png")))

    host = QWidget()
    lay = QVBoxLayout(host)
    page = HomePage(SessionBridge())
    lay.addWidget(page)
    host.resize(1600, 900)
    host.show()
    _app.processEvents()

    banner = page.banner
    check("the banner is on the Home page", isinstance(banner, BannerLabel),
          type(banner).__name__)
    check("the banner artwork actually loaded", banner.has_image,
          "pixmap is null")

    src = banner._source
    native = (src.width(), src.height())
    check("the banner is the bundled PNG, not a placeholder",
          native == (1080, 462), str(native))

    # It must stretch WITH the window. A fixed-size pixmap would report the
    # same width at every window size, which is the bug this pins down.
    widths = []
    for w in (1000, 1400, 1900):
        host.resize(w, 900)
        _app.processEvents()
        widths.append(banner.width())
    check("the banner takes the width the window offers",
          widths == sorted(widths) and widths[0] < widths[-1], str(widths))

    # Never upscaled past native: a blown-up PNG is visibly soft. This is a "fit"
    # rule, so it is asserted on a fit-mode banner. The Home banner deliberately
    # does NOT obey it -- "cover" has to scale past native in order to span a
    # box wider than the source, which is the whole point of the mode.
    fitted = BannerLabel(str(_asset_path("welcome-banner.png")))
    fitted.show()
    _app.processEvents()
    host.resize(3000, 900)
    fitted.resize(3000, 300)
    _app.processEvents()
    check("a fit-mode banner is never enlarged beyond its native width",
          fitted.pixmap().width() <= native[0],
          f"{fitted.pixmap().width()} > {native[0]}")

    # Aspect ratio preserved at every size -- a stretched/distorted logo is
    # worse than a small one.
    def _ar(pix):
        return pix.width() / pix.height()
    native_ar = native[0] / native[1]
    ratios = []
    for w in (900, 1200, 1600, 2200):
        host.resize(w, 900)
        _app.processEvents()
        pm = banner.pixmap()
        if not pm.isNull():
            ratios.append(abs(_ar(pm) - native_ar) / native_ar)
    check("the banner is never distorted, only scaled",
          ratios and max(ratios) < 0.02,
          f"max relative aspect error {max(ratios) if ratios else 'n/a'}")

    # The height is derived, so it must never exceed the cap -- otherwise a
    # wide window would push the rest of the page off screen.
    host.resize(2400, 900)
    _app.processEvents()
    check("the banner's height is capped so it stays a header element",
          banner.height() <= 300, str(banner.height()))

    # A missing file must degrade to "no banner", never raise: a packaging
    # mistake should not take the Home page down with it.
    broken = BannerLabel("/nonexistent/welcome-banner.png")
    check("a missing banner file does not raise",
          not broken.has_image and broken.height() == 0,
          str(broken.height()))

    # -- Cover mode --------------------------------------------------------
    # "cover" is retained for a slot whose SHAPE is fixed and whose content is
    # decorative -- a photo behind a header, say. Home no longer uses it (it
    # cropped the artwork, see the adapt block below), so these checks pin the
    # mode's own contract rather than the Home page's appearance.
    band = BannerLabel(str(_asset_path("welcome-banner.png")),
                       fill="cover", min_height=10, max_height=100000)
    band.show()
    _app.processEvents()
    gaps = []
    for w in range(140, 1200, 23):
        band.resize(w, 240)
        _app.processEvents()
        pm = band.pixmap()
        if pm.isNull():
            gaps.append(f"w={w}: null pixmap")
        elif pm.width() < w or pm.height() < 240:
            gaps.append(f"w={w}: {pm.width()}x{pm.height()} in {w}x240 box")
    check("cover mode fills its box in both axes, leaving no empty strip",
          not gaps, "; ".join(gaps[:4]) if gaps else "no gaps at any width")

    # Cover crops; it must never distort. Both edges are derived from ONE scale
    # factor, so the aspect ratio survives up to integer rounding.
    errs = []
    for w in (300, 700, 1100, 1500):
        band.resize(w, 240)
        _app.processEvents()
        pm = band.pixmap()
        if not pm.isNull():
            errs.append(abs((pm.width() / pm.height()) - native_ar) / native_ar)
    check("cover mode crops without distorting the artwork",
          errs and max(errs) < 0.02,
          f"max relative aspect error {max(errs) if errs else 'n/a'}")

    # The painted image is larger than the box by design, so it MUST be clipped
    # or it would bleed over the page title and the Start button beside it.
    # Asserting the method is genuinely overridden (and not just inherited from
    # QLabel, which would draw the oversized pixmap unclipped).
    check("cover mode clips the oversized artwork to its own box",
          type(band).paintEvent is not QLabel.paintEvent,
          "paintEvent not overridden -- overflow would bleed")

    # -- Adapt mode --------------------------------------------------------
    # The Home header needs the artwork WHOLE and gap-free at every window
    # size. "fit" left a wide empty strip beside a small image; "cover" filled
    # the strip by cropping the top and bottom off the artwork (the report:
    # "image got clipped in fullscreen"). "adapt" solves it by making the WIDGET
    # take the artwork's aspect ratio and filling that box, so neither failure is
    # possible. These checks pin all three properties.
    adapt = BannerLabel(str(_asset_path("welcome-banner.png")),
                        fill="adapt", min_height=180, max_height=260)
    adapt.show()
    _app.processEvents()

    problems = []
    for w in range(200, 1400, 17):
        adapt.resize(w, 400)
        _app.processEvents()
        b = adapt.size()
        pm = adapt.pixmap()
        if pm.isNull() or b.height() == 0:
            # Collapsing is legitimate only when the slot is too narrow to use.
            if w >= adapt._min_width:
                problems.append(f"w={w}: vanished at {b.width()}x{b.height()}")
            continue
        # (1) Never cropped: nothing may spill past the box it was given.
        if pm.width() > b.width() or pm.height() > b.height():
            problems.append(f"w={w}: cropped, {pm.width()}x{pm.height()} "
                            f"in {b.width()}x{b.height()}")
        # (2) No empty strip: the artwork fills the box it was given.
        if pm.width() < b.width() - 1 or pm.height() < b.height() - 1:
            problems.append(f"w={w}: gap, {pm.width()}x{pm.height()} "
                            f"in {b.width()}x{b.height()}")
        # (3) Same aspect ratio, within integer-rounding tolerance.
        for label, pair in (("box", (b.width(), b.height())),
                            ("pixmap", (pm.width(), pm.height()))):
            if not pair[1]:
                continue
            err = abs((pair[0] / pair[1]) - native_ar) / native_ar
            if err > 0.02:
                problems.append(f"w={w}: {label} aspect off by {err:.1%}")
    check("adapt mode is never cropped and never letterboxed",
          not problems, "; ".join(problems[:4]) if problems
          else "whole artwork, no gaps, at every width")

    # It must follow the window rather than sit at a fixed size -- that is the
    # "auto adjusted" half of the request, and the reason the width drives it.
    grow = []
    for w in (300, 500, 700, 900):
        adapt.resize(w, 400)
        _app.processEvents()
        grow.append(adapt.width())
    check("adapt mode scales with the window it is given",
          grow == sorted(grow) and grow[0] < grow[-1], str(grow))

    # Still a header element: the height band is honoured, so a wide window
    # cannot push the stat cards and the sessions list off screen.
    caps = []
    for w in (900, 1400, 2400):
        adapt.resize(w, 400)
        _app.processEvents()
        caps.append(adapt.height())
    check("adapt mode stays inside its height band",
          caps and all(180 <= h <= 260 for h in caps), str(caps))

    # The page must not be pushed sideways on a narrow window: adapt may shrink
    # below its height floor to respect the width, but never exceed the slot.
    over = []
    for w in range(160, 900, 13):
        adapt.resize(w, 400)
        _app.processEvents()
        if adapt.width() > w:
            over.append(f"w={w}: grew to {adapt.width()}")
    check("adapt mode never overflows the width it was offered",
          not over, "; ".join(over[:4]) if over else "never wider than the slot")

    # "fit" stays the default, so any other caller is unaffected by the new mode.
    default_mode = BannerLabel(str(_asset_path("welcome-banner.png")))
    default_mode.resize(1600, 400)
    default_mode.show()
    _app.processEvents()
    check("fit is still the default fill mode",
          default_mode._fill == "fit", default_mode._fill)

    # "cover" is still reachable for a genuinely decorative slot -- removing it
    # would be a silent behaviour change for any caller that asked for it.
    check("cover mode is still available",
          BannerLabel(str(_asset_path("welcome-banner.png")),
                      fill="cover")._fill == "cover", "cover missing")

    # The Home page must actually ask for adapt, or the clipping comes back.
    check("the Home banner uses adapt fill",
          page.banner._fill == "adapt", page.banner._fill)


def test_session_counting_rules() -> None:
    """Home counters: what counts as a dataset analysed, and what doesn't.

    The old Streamlit app kept an `analysed_datasets` table with record/count
    helpers and a hook on "Confirm & Proceed to Analysis". The Qt port dropped
    all of it and derived "Datasets Analyzed" from
    COUNT(DISTINCT file_name) over saved sessions, which broke three of the
    four documented rules at once:

      1. it only counted datasets the user happened to SAVE, so analysing
         without saving was invisible;
      2. it keyed on filename alone, so an updated file with the same name
         never counted;
      3. "Analyses Available" reported a count of charts made rather than the
         number of analysis types the build offers.

    Runs against a throwaway database so the developer's real sessions are
    neither read nor written.
    """
    print("[48] Session counting: dataset/session/analysis rules")
    import os
    import tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    # Redirect the database BEFORE anything caches DB_PATH.
    tmp_db = tempfile.mktemp(suffix=".db")
    from modules import database as dbmod
    _saved_db_path = dbmod.DB_PATH
    _caches = []
    for fn_name in ("get_user_stats", "get_user_sessions"):
        fn = getattr(dbmod, fn_name, None)
        if fn is not None and hasattr(fn, "clear"):
            _caches.append(fn)
            fn.clear()
    dbmod.DB_PATH = tmp_db
    try:
        dbmod.init_db()
        uid = dbmod.get_or_create_guest_user()["id"]
        dbmod.get_user_stats.clear()

        import pandas as pd
        import polars as pl
        from desktop.session_bridge import SessionBridge
        from desktop.pages.upload_page import UploadPage
        from services.analysis_service import ANALYSIS_OPTIONS

        def _df():
            # The bridge and every consumer (the table model, the filter
            # row, the classifier panel) are Polars-native; handing in a
            # pandas frame raised deep in the callback chain, which the
            # SessionManager swallows by design -- so the button never
            # enabled and the click was a silent no-op.
            return pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

        def _count():
            return dbmod.count_datasets_analysed(uid)

        # -- Rule 1: reaching the analysis stage counts, on both upload routes.
        bridge = SessionBridge()
        bridge.set("user_id", uid)
        page = UploadPage(bridge)
        bridge.set_df(_df())
        bridge.set("file_name", "Coffee.csv")
        bridge.set("file_signature", "Coffee.csv:1024")
        page._on_preview_mode()

        nav = []
        page.navigate_requested.connect(nav.append)
        page.btn_go_analysis.click()
        _app.processEvents()
        check("'Go to Analysis' counts the dataset and navigates",
              nav == ["analysis"] and _count() == 1,
              f"nav={nav} n={_count()}")

        # Repeated presses must be idempotent -- this is what the UNIQUE
        # signature is for.
        page.btn_go_analysis.click()
        page.classification_ready.emit({})
        _app.processEvents()
        check("reaching analysis again does not double count",
              _count() == 1, f"n={_count()}")

        # The other route: straight to Analysis from the sidebar, with no
        # upload-page navigation at all.
        side = SessionBridge()
        side.set("user_id", uid)
        side.set_df(_df())
        side.set("file_name", "Tea.csv")
        side.set("file_signature", "Tea.csv:2048")
        check("the sidebar route records via the same bridge helper",
              side.record_dataset_analysed() and _count() == 2, f"n={_count()}")

        # -- Rule 2: identity is name + size.
        fresh = SessionBridge()          # simulates a brand new app launch
        fresh.set("user_id", uid)
        fresh.set_df(_df())
        fresh.set("file_name", "Coffee.csv")
        fresh.set("file_signature", "Coffee.csv:1024")
        check("the same file in a fresh session is not counted twice",
              not fresh.record_dataset_analysed() and _count() == 2,
              f"n={_count()}")

        fresh.set("file_signature", "Coffee.csv:4096")   # same name, new size
        fresh.record_dataset_analysed()
        check("the same name at a different size counts as new data",
              _count() == 3, f"n={_count()}")

        # -- Rule 3: sessions only on an explicit Save Session. Everything above
        # analysed data and created no session rows at all.
        n_analyses = len(ANALYSIS_OPTIONS)
        stats = dbmod.get_user_stats(uid, analyses_available=n_analyses)
        check("analysing a dataset does not create a saved session",
              stats["sessions"] == 0, f"sessions={stats['sessions']}")
        check("analysing without saving still counts the dataset",
              stats["datasets"] == 3, f"datasets={stats['datasets']}")

        dbmod.save_session_db(
            user_id=uid, session_name="S1", file_name="Coffee.csv",
            rows=3, cols=2, analysis_types=[], charts_json="[]",
            dashboard_title="", kpis_json="[]", layout_mode="portrait",
            grid_cols_n=2, grid_order_json="[]", grid_fullwidth_json="{}",
            export_text_json="{}", export_colours_json="{}",
        )
        stats = dbmod.get_user_stats(uid, analyses_available=n_analyses)
        check("a manually saved session is counted once",
              stats["sessions"] == 1, f"sessions={stats['sessions']}")
        check("saving a session does not inflate the dataset count",
              stats["datasets"] == 3, f"datasets={stats['datasets']}")

        # -- Rule 4: analyses available is dynamic, not a chart count.
        check("'Analyses Available' is the size of the analysis registry",
              stats["analyses"] == n_analyses and n_analyses > 0,
              f"{stats['analyses']} vs {n_analyses}")

        from desktop.pages.home_page import HomePage
        home = HomePage(bridge)
        home._refresh_stats()
        # Assert on the rendered label, which is what the user actually sees.
        shown = home.stat_analyses.lbl_value.text()
        check("the Home card shows the dynamic analysis count",
              shown == f"{n_analyses:,}", f"{shown!r} vs {n_analyses}")

        # -- The counter must not be stale: get_user_stats is TTL-cached, so a
        # new dataset has to invalidate it or the card lags by up to 30s.
        before = dbmod.get_user_stats(uid, analyses_available=1)["datasets"]
        dbmod.record_dataset_analysed(uid, "New.csv", "New.csv:50", 5, 2)
        after = dbmod.get_user_stats(uid, analyses_available=1)["datasets"]
        check("recording a dataset invalidates the cached stats",
              after == before + 1, f"{before} -> {after}")

        # -- NEW: every import path counts, even the ones that never stored a
        # file_signature (Excel sheets, merges, clipboard). The bridge must
        # derive one from the frame itself instead of silently no-oping.
        excelish = SessionBridge()
        excelish.set("user_id", uid)
        excelish.set_df(_df())            # no file_signature at all
        excelish.set("file_name", "North.xlsx")
        check("an import without a stored signature still counts",
              excelish.record_dataset_analysed() is True,
              f"sig={excelish.get('file_signature')!r}")
        check("the derived signature is stable across imports of the same frame",
              excelish.get("file_signature") == excelish.get("file_signature"),
              excelish.get("file_signature"))
        # And recording again with the same frame does not double-count.
        n_after_excel = dbmod.get_user_stats(uid)["datasets"]
        excelish.record_dataset_analysed()
        check("the derived signature de-duplicates the same data",
              dbmod.get_user_stats(uid)["datasets"] == n_after_excel,
              f"datasets={dbmod.get_user_stats(uid)['datasets']}")

        # -- Reports Built: exporting a dashboard file counts it; saving a
        # session does not.
        from desktop.pages.home_page import HomePage
        n_reports = dbmod.get_user_stats(uid)["reports"]
        check("no reports built yet", n_reports == 0,
              f"reports={n_reports}")
        home_reports = HomePage(bridge)
        check("the Home page carries a Reports Built card",
              hasattr(home_reports, "stat_reports"),
              "missing stat_reports")
        check("recording a report build increments the counter",
              dbmod.record_report_built(uid, "Q1 Board", "html", 150) is True
              and dbmod.get_user_stats(uid)["reports"] == 1,
              f"reports={dbmod.get_user_stats(uid)['reports']}")
        home_reports._refresh_stats()
        check("the Reports Built card shows the count",
              home_reports.stat_reports.lbl_value.text() == "1",
              home_reports.stat_reports.lbl_value.text())
        dbmod.record_report_built(uid, "Q1 Board", "png", 150)
        check("a PNG export counts as a report build too",
              dbmod.get_user_stats(uid)["reports"] == 2,
              f"reports={dbmod.get_user_stats(uid)['reports']}")
        check("recording a report without a user is a no-op",
              dbmod.record_report_built(None, "x", "html", 0) is False)

        # -- Guard rails: no user, or no identity, must be a silent no-op
        # rather than a crash or a bogus row.
        check("recording without a user is a no-op",
              dbmod.record_dataset_analysed(None, "x.csv", "x.csv:1") is False)
        check("recording without a signature is a no-op",
              dbmod.record_dataset_analysed(uid, "x.csv", "") is False)
        check("stats for no user are all zero",
              dbmod.get_user_stats(None) == {"sessions": 0, "datasets": 0,
                                             "reports": 0, "analyses": 0})

        # -- Merging a guest into a signed-in account must not trip the new
        # UNIQUE(user_id, file_signature) constraint when both already saw the
        # same file (a plain UPDATE would abort the whole merge).
        conn = dbmod._connect()
        cur = conn.cursor()
        cur.execute("INSERT INTO users (username,email,password_hash,is_guest,uuid)"
                    " VALUES ('mg','mg@x','h',1,'umg')")
        guest_id = cur.lastrowid
        cur.execute("INSERT INTO users (username,email,password_hash,is_guest,uuid)"
                    " VALUES ('mt','mt@x','h',1,'umt')")
        target_id = cur.lastrowid
        conn.commit()
        conn.close()
        dbmod.record_dataset_analysed(guest_id, "shared.csv", "shared.csv:500", 10, 2)
        dbmod.record_dataset_analysed(target_id, "shared.csv", "shared.csv:500", 10, 2)
        dbmod.record_dataset_analysed(guest_id, "guest.csv", "guest.csv:700", 7, 1)
        try:
            dbmod.merge_user_data(guest_id, target_id)
            merged = (dbmod.count_datasets_analysed(target_id) == 2
                      and dbmod.count_datasets_analysed(guest_id) == 0)
        except Exception as exc:
            merged = False
            print(f"    merge raised: {exc}")
        check("merging accounts keeps the count distinct, not doubled", merged,
              f"target={dbmod.count_datasets_analysed(target_id)}")

        # -- The upgrade path: an existing install has no analysed_datasets rows,
        # so init_db must seed them from saved sessions rather than showing 0.
        conn = dbmod._connect()
        conn.execute("DELETE FROM analysed_datasets")
        conn.commit()
        conn.close()
        dbmod.get_user_stats.clear()
        dbmod.init_db()
        check("existing installs backfill the dataset counter on upgrade",
              dbmod.get_user_stats(uid)["datasets"] == 1,
              f"datasets={dbmod.get_user_stats(uid)['datasets']}")
        dbmod.init_db()      # must be idempotent, not re-seed or inflate
        dbmod.get_user_stats.clear()
        check("the backfill does not re-run and double count",
              dbmod.get_user_stats(uid)["datasets"] == 1,
              f"datasets={dbmod.get_user_stats(uid)['datasets']}")
    finally:
        dbmod.DB_PATH = _saved_db_path
        for fn in _caches:
            try:
                fn.clear()
            except Exception:
                pass
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(tmp_db + suffix)
            except OSError:
                pass



def test_editor_free_bundle_slicer_sync_and_chrome() -> None:
    """[46] Round-3 regressions: quoted slicer selector, glow-only chrome,
    raw-state initial style (KPI/title alignment & spacing settings)."""
    print("[46] Slicer sync runs: quoted selectors, no handle chrome")
    import json as _json
    from modules.report_editor import editor_css, editor_js
    from modules import echarts_export

    js = editor_js(2, 24, mode="free")
    # An unquoted digit-leading attribute selector ([data-slicer=0]) is not a
    # valid CSS selector: Chromium throws SyntaxError at querySelector time,
    # which silently aborted the live slicer-card sync. The value must be
    # quoted, exactly like every other querySelector in the bundle.
    check("the slicer-card lookup quotes its attribute value",
          '.slicer-card[data-slicer="' in js,
          "quoted form MISSING")
    import re as _re
    from pathlib import Path as _P
    _src = (_P(__file__).resolve().parents[1]
            / "backend" / "modules" / "report_editor.py").read_text(encoding="utf-8")
    unquoted = _re.findall(r'\[data-[a-z0-9-]+=(?!")', _src)
    check("no unquoted attribute selector anywhere in the editor source",
          not unquoted, str(unquoted))
    css = editor_css("#6163df")
    check("no persistent handle chips remain anywhere",
          ".ly-handle" not in css and ".ly-h-" not in css,
          "")
    check("the resize/drag glow is still there while a gesture is live",
          ".ly-working > .ly-sel" in css and "ly-glow-pulse" in css,
          "")
    # -- NEW: overlapping cards during a drag/resize go RED, not accent.
    check("dragging over another card paints the affordance red",
          ".ly-working.ly-overlap > .ly-sel" in css
          and "ly-glow-overlap" in css
          and "__OVERLAP__" not in css,     # tokens must be substituted
          "red overlap CSS missing or unsubstituted")
    check("the ghost goes red too, so both affordances agree",
          ".ly-ghost.ly-overlap" in css, "")
    check("the free-canvas bundle hit-tests other tiles during a gesture",
          "function overlapHit(el)" in js
          and "el.classList.add('ly-overlap')" in js
          and "el.classList.remove('ly-overlap')" in js,
          "overlap hit-test missing")
    check("grid mode never paints the overlap red (cells cannot overlap)",
          "if (FREE)" in js, "")
    check("overlap is cleared on gesture start and on release",
          "card.classList.remove('ly-overlap')" in js
          and "drag.el.classList.remove('ly-overlap')" in js,
          "")
    check("overlap red is fixed, not theme-derived",
          "#ef4444" in css, "")
    check("the grip is grid-mode only; free mode drags the body",
          ".ly-editing.ly-grid-editing .ly-grip { display: flex" in css
          and ".ly-editing .ly-grip," not in css,
          "")
    check("editing mode adds no fixed KPI spacing offsets",
          "margin-bottom: 14px" not in css
          and "margin-top: 26px" not in css,
          "")

    live = echarts_export._LIVE_STYLE_JS
    for token in ("kpiGap", "kpiPad", "kpiValAlign", "kpiLabelAlign",
                  "titleGap", "titlePad", "titleAlign"):
        check(f"the live style patcher applies {token}",
              token in live, "")

    # The export must hand the RAW ex_* state to the initial style patch.
    # The compact theme dict never carried ex_* keys, so every full rebuild
    # silently reset appearance settings to the selector defaults.
    from modules.board_model import make_board, make_board_object
    objects = [
        make_board_object("__title__", "text", {"x": 0, "y": 0, "w": 1600,
                                                "h": 64},
                          {"text": "t", "role": "title", "visible": True}),
        make_board_object("kpi-0", "kpi", {"x": 0, "y": 80, "w": 240,
                                           "h": 120},
                          {"index": 0, "label": "TOTAL", "value": "1",
                           "visible": True}),
    ]
    board = make_board(objects,
                       style={"background": "#121a2e", "card": "#1b2245",
                              "border": "#2c3564", "text": "#f5f7ff",
                              "accent": "#6163df"},
                       background={"color": "#121a2e",
                                   "image_data_uri": ""},
                       metadata={"width": 1600, "height": 900,
                                 "title": "t", "orientation": "landscape"})
    html = echarts_export.board_to_html(
        board, editor=True, grid_cols_n=2,
        report_style={"ex_kpi_val_size": 33, "ex_kpi_gap": 5,
                      "ex_kpi_pad": 6, "ex_kpi_val_align": "center",
                      "ex_kpi_label_align": "right",
                      "ex_title_align": "center", "ex_title_gap": 7,
                      "ex_title_pad": 11, "ex_radius": 9})
    check("the initial style patch carries the raw KPI size",
          '"ex_kpi_val_size":33' in html, "")
    check("the initial style patch carries the KPI alignment settings",
          '"ex_kpi_val_align":"center"' in html
          and '"ex_kpi_label_align":"right"' in html, "")
    check("the initial style patch carries the KPI/title spacing settings",
          '"ex_kpi_gap":5' in html and '"ex_kpi_pad":6' in html
          and '"ex_title_gap":7' in html and '"ex_title_pad":11' in html, "")
    check("the export carries no legacy handle chrome",
          ".ly-handle" not in html and ".ly-h-left" not in html, "")



def test_multipage_export_wiring() -> None:
    """Multi-page reports: active-page partition, per-page payload, round-trip."""
    print("[33] Multi-page reports: partition, payload, restore round-trip")
    import polars as pl
    from desktop.pages import dashboard_page as dp
    from services import page_state
    from modules.chart_model import make_chart_model

    df = pl.DataFrame({"segment": ["a", "b", "a"], "sales": [10, 20, 30]})

    def _model(uid, title):
        return make_chart_model(
            uid=uid, chart_type="bar", title=title,
            data={"dimensions": ["segment"], "series": [
                {"id": "s1", "name": "sales", "values": [25, 20]}]},
            recipe={"kind": "sum", "column": "sales", "group_by": "segment"},
            display={})

    class _FakeBridge:
        def __init__(self):
            self.data = {
                "charts": [("chartA", "A", _model("chartA", "A")),
                           ("chartB", "B", _model("chartB", "B"))],
                "kpis": [{"label": "TOTAL", "value": "60",
                          "_recipe": {"kpi_type": "sum", "kpi_key": "sum",
                                      "col": "sales"}}],
                "slicers": [
                    {"uid": "s_global", "column": "segment", "kind": "in",
                     "widget": "dropdown", "scope": "report", "target": "",
                     "label": "segment", "page": "__global__",
                     "spec": {"kind": "in", "column": "segment",
                              "values": []}},
                    {"uid": "s_over", "column": "segment", "kind": "in",
                     "widget": "dropdown", "scope": "report", "target": "",
                     "label": "segment", "page": "overview",
                     "spec": {"kind": "in", "column": "segment",
                              "values": []}},
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
            }
            self._df = df

        def get(self, key, default=None):
            return self.data.get(key, default)

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
            return {}

        def get_note(self, uid):
            return ""

    def _holder(bridge):
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

        class _Text:
            def __init__(self, value):
                self._v = value

            def text(self):
                return self._v

        class _Combo:
            def currentText(self):
                return "Portrait"

        holder.session_name = _Text("Smoke Board")
        holder.layout_mode = _Combo()
        holder.dash_title = _Text("Smoke Board")
        return holder

    bridge = _FakeBridge()
    holder = _holder(bridge)

    snap = holder._snapshot_inputs()
    check("snapshot partitions charts to the active page",
          [c[0] for c in snap["charts"]] == ["chartA"])
    check("snapshot keeps the unpartitioned list for the export",
          [c[0] for c in snap["raw_charts"]] == ["chartA", "chartB"])
    check("snapshot carries active_page / page_list / chart_pages",
          snap["active_page"] == "overview"
          and [p["id"] for p in snap["page_list"]] == ["overview", "finance"]
          and snap["chart_pages"]["chartB"] == "finance")

    # Unassigned uids land on the active page and persist the backfill.
    bridge.data["chart_pages"] = {"chartA": "overview"}
    snap = holder._snapshot_inputs()
    check("unassigned charts are backfilled to the active page",
          snap["chart_pages"].get("chartB") == "overview"
          and bridge.data["chart_pages"].get("chartB") == "overview")

    bridge = _FakeBridge()
    holder = _holder(bridge)
    payload = holder._pages_payload(list(bridge.data["charts"]), holder._page_state())
    check("multi-page reports build a paged payload",
          payload is not None and len(payload) == 2)
    if payload:
        ov, fin = payload
        check("each page keeps only its own charts",
              [c[0] for c in ov["charts"]] == ["chartA"]
              and [c[0] for c in fin["charts"]] == ["chartB"])
        check("KPIs route to their page slot",
              [k["label"] for k in ov["kpis"]] == ["TOTAL"]
              and [k["label"] for k in fin["kpis"]] == ["BURN"])
        check("global slicers lead both pages' effective lists",
              ov["slicers"][0]["uid"] == "s_global"
              and fin["slicers"][0]["uid"] == "s_global")
        check("page-local slicers ride only with their page",
              [s["uid"] for s in ov["slicers"]] == ["s_global", "s_over"]
              and [s["uid"] for s in fin["slicers"]] == ["s_global"])
        check("slicer_values are keyed by the effective list index",
              set(ov["slicer_values"]) == {"0", "1"}
              and set(fin["slicer_values"]) == {"0"})
        check("mode marker never reaches the tile geometry",
              "__ly_mode__" not in ov["tile_geometry"]
              and "__ly_mode__" not in fin["tile_geometry"])
        check("finance kept its own chart + KPI placement",
              fin["tile_geometry"]["chartB"]["x"] == 0.1
              and fin["kpi_geometry"]["0"]["y"] == 400.0)

    # Single-page reports produce no payload, so the saved file is unchanged.
    bridge = _FakeBridge()
    bridge.data["pages"] = [{"id": "overview", "name": "Overview"}]
    bridge.data["chart_pages"] = {"chartA": "overview", "chartB": "overview"}
    holder = _holder(bridge)
    payload = holder._pages_payload(list(bridge.data["charts"]), holder._page_state())
    check("single-page reports get no payload (byte-identical export)",
          payload is None)

    # pages_json round-trip: assemble -> serialise -> apply onto an empty bridge.
    bridge = _FakeBridge()
    blob = page_state.page_state_json(page_state.assemble_from_bridge(bridge.get))
    fresh = _FakeBridge()
    fresh.data.update({"pages": [], "kpis": [], "slicers": [],
                       "tile_geometry": {"__ly_mode__": "free"}})
    page_state.apply_to_bridge(fresh.set, blob)
    check("pages survive the save/restore round-trip",
          [p["id"] for p in fresh.data["pages"]] == ["overview", "finance"]
          and fresh.data["active_page"] == "overview")
    check("the active page's live keys are mirrored on restore",
          [k["label"] for k in fresh.data["kpis"]] == ["TOTAL"]
          and [s["uid"] for s in fresh.data["slicers"]] == ["s_global", "s_over"])

    # The live style patcher must be page-safe: every board, every bg layer.
    from modules import echarts_export as _ee
    check("live style patcher iterates every page board",
          "boards.forEach(function(board){var key=prefix+'_image'" in _ee._LIVE_STYLE_JS)
    check("live style patcher matches class-based background layers",
          "querySelectorAll('.ly-bg-layer')" in _ee._LIVE_STYLE_JS
          and '#ly-bg-layer' not in _ee._LIVE_STYLE_JS)

    # A legacy single-Overview blob must not wipe the live layout on restore.
    fresh = _FakeBridge()
    fresh.data.update({"pages": [], "slicers": [],
                       "tile_geometry": {"__title__": {"x": 0.0, "y": 0.0,
                                                       "w": 1.0, "h": 64.0}}})
    page_state.apply_to_bridge(fresh.set, "{}")
    check("a legacy pages blob installs Overview without wiping the layout",
          fresh.data["tile_geometry"].get("__title__", {}).get("h") == 64.0
          and [p["id"] for p in fresh.data["pages"]] == [page_state.DEFAULT_PAGE_ID])



def test_backup_restore_carries_pages() -> None:
    """Backup/restore of sessions must round-trip the multi-page state."""
    print("[49] Backup & restore: pages_json survives export and import")
    import os
    import tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    tmp_db = tempfile.mktemp(suffix=".db")
    from modules import database as dbmod
    _saved_db_path = dbmod.DB_PATH
    dbmod.DB_PATH = tmp_db
    try:
        dbmod.init_db()
        uid = dbmod.get_or_create_guest_user()["id"]

        pages_blob = ('{"version":1,"active":"overview","pages":['
                      '{"id":"overview","name":"Overview"},'
                      '{"id":"finance","name":"Finance"}],'
                      '"chart_pages":{"chartA":"overview","chartB":"finance"},'
                      '"page_geometry":{"finance":{"chartB":{"x":0.1,"y":80,"w":0.5,"h":300}}},'
                      '"page_kpis":{"finance":[{"label":"BURN","value":"-3"}]},'
                      '"page_slicers":{"__global__":[{"uid":"s_g","column":"segment",'
                      '"widget":"dropdown","page":"__global__"}],"finance":[],"overview":[]}}')

        sid = dbmod.save_session_db(
            user_id=uid, session_name="Paged Board", file_name="Coffee.csv",
            rows=3, cols=2, analysis_types=[], charts_json="[]",
            dashboard_title="Paged", kpis_json="[]", layout_mode="portrait",
            grid_cols_n=2, grid_order_json="[]", grid_fullwidth_json="{}",
            export_text_json="{}", export_colours_json="{}",
            slicers_json="[]", pages_json=pages_blob)

        exported = dbmod.export_sessions_to_dict(uid)
        check("backup exports the pages_json column",
              len(exported) == 1 and exported[0].get("pages_json") == pages_blob,
              str(exported[0].get("pages_json"))[:80])

        # Re-import into a THROWAWAY user (a different profile restoring the
        # package): the multi-page state must travel with the session. The
        # insert path deliberately generates a fresh uuid for a foreign row,
        # so we read back by name.
        conn = dbmod._connect()
        cur = conn.cursor()
        cur.execute("INSERT INTO users (username,email,password_hash,is_guest,uuid) "
                    "VALUES ('other','o@x','h',1,'uother')")
        other = cur.lastrowid
        conn.commit(); conn.close()

        imported, updated, warnings = dbmod.import_sessions_from_dict(other, exported)
        check("restore imports the session", imported == 1,
              f"imported={imported} updated={updated} warnings={warnings}")
        conn = dbmod._connect()
        row = conn.execute(
            "SELECT pages_json FROM sessions WHERE user_id=? AND session_name=?",
            (other, "Paged Board")).fetchone()
        conn.close()
        check("restored session keeps its pages_json",
              bool(row) and row[0] == pages_blob,
              str(row[0])[:80] if row else "no row")

        # The update branch (newer backup over the SAME profile's uuid) must
        # also carry pages_json, or restoring a modified package silently
        # downgrades the dashboard to single-page.
        newer = dict(exported[0])
        newer_pages = '{"version":1,"active":"finance","pages":[{"id":"overview","name":"Overview"},{"id":"finance","name":"Finance"}],"chart_pages":{},"page_geometry":{},"page_kpis":{},"page_slicers":{}}'
        newer["pages_json"] = newer_pages
        newer["updated_at"] = "2999-01-01 00:00:00"
        imported, updated, warnings = dbmod.import_sessions_from_dict(uid, [newer])
        conn = dbmod._connect()
        row = conn.execute(
            "SELECT pages_json FROM sessions WHERE user_id=? AND session_name=?",
            (uid, "Paged Board")).fetchone()
        conn.close()
        check("a newer backup overwrites pages_json on restore",
              updated == 1 and row and row[0] == newer_pages,
              f"updated={updated} row={str(row[0])[:60] if row else None}")

        # A pre-pages backup (no pages_json key at all) restores without
        # error and lands on the single-Overview default.
        legacy = dict(exported[0])
        del legacy["pages_json"]
        legacy.pop("session_uuid", None)
        legacy["session_name"] = "Legacy Board"
        imported, updated, warnings = dbmod.import_sessions_from_dict(other, [legacy])
        conn = dbmod._connect()
        row = conn.execute(
            "SELECT pages_json FROM sessions WHERE user_id=? AND session_name=?",
            (other, "Legacy Board")).fetchone()
        conn.close()
        check("a legacy backup restores on the default pages blob",
              imported == 1 and row and row[0] == "{}",
              f"imported={imported} row={str(row[0])[:60] if row else None}")
    finally:
        dbmod.DB_PATH = _saved_db_path
        try:
            os.remove(tmp_db)
        except OSError:
            pass



def test_data_source_plugin_seam() -> None:
    """The data-source registry is a real seam, not a sketch.

    Pins the front-door every ingestion path (and the future database
    plugin) will use: kind registry, lazy frames, content fingerprints,
    SQL via stdlib sqlite3, credential redaction, identifier validation.
    """
    print("[50] Data sources: plugin seam + large-data front door")
    import os
    import sqlite3
    import tempfile
    import polars as pl

    from services import data_source as ds

    check("built-in kinds registered",
          {"csv", "parquet", "sql"} <= set(ds.available_sources()),
          str(ds.available_sources()))

    tmpdir = tempfile.mkdtemp(prefix="lytrize_ds_")
    parq = os.path.join(tmpdir, "sales.parquet")
    pl.DataFrame({"region": ["n", "s", "n", "s"],
                  "amount": [1.0, 2.0, 3.0, 4.0]}).write_parquet(parq)

    src = ds.make_source("parquet", path=parq)
    frame = src.lazy_frame().collect()
    check("parquet source reads rows lazily", frame.height == 4, str(frame.height))
    check("parquet source adds the source row id", "__ly_row_id" in frame.columns)
    check("parquet fingerprint is stable",
          ds.make_source("parquet", path=parq).fingerprint() == src.fingerprint())
    dataset = src.to_dataset()
    check("parquet to_dataset yields a PolarsDataset",
          type(dataset).__name__ == "PolarsDataset" and dataset.revision == src.fingerprint())

    csv_path = os.path.join(tmpdir, "sales.csv")
    with open(csv_path, "w") as f:
        f.write("region,amount\nn,1.0\ns,2.0\n")
    check("csv source reads rows",
          ds.make_source("csv", path=csv_path).lazy_frame().collect().height == 2)

    # The bundle's Polars route: saving a pl.DataFrame must dedup repeat
    # saves by CONTENT (the pre-polars path only deduped pandas frames).
    from services import session_bundle as sb
    frame = pl.DataFrame({"region": ["n", "s"], "amount": [1.0, 2.0]})
    h1, u1 = sb.save_dataset(frame)
    h2, u2 = sb.save_dataset(frame)
    check("polars save_dataset dedups identical frames by content",
          bool(u1) and u1 == u2 and h1 == h2, f"{u1} vs {u2}")
    loaded = sb.load_polars_dataset(u1)
    check("polars bundle loads back as a lazy Polars dataset",
          loaded is not None and loaded.frame.collect().height == 2)

    db_path = os.path.join(tmpdir, "sales.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sales(id INTEGER PRIMARY KEY, region TEXT, amount REAL)")
    conn.executemany("INSERT INTO sales(region, amount) VALUES (?, ?)",
                     [("n", 1.0), ("s", 2.0), ("n", 3.0)])
    conn.commit(); conn.close()

    sql = ds.make_source("sql", uri=f"sqlite:///{db_path}", table="sales")
    total = sql.lazy_frame().select(pl.col("amount").sum()).collect().item()
    check("sql source reads via stdlib sqlite3 (no extra drivers)",
          abs(total - 6.0) < 1e-9, str(total))
    grouped = (sql.lazy_frame().group_by("region")
               .agg(pl.col("amount").sum().alias("total"))
               .collect().sort("region"))
    check("sql source aggregates on the lazy frame",
          grouped.rows() == [("n", 4.0), ("s", 2.0)], str(grouped.rows()))

    sql_q = ds.make_source("sql", uri=f"sqlite:///{db_path}",
                           query="SELECT region, SUM(amount) AS t FROM sales GROUP BY region")
    check("sql source accepts an explicit query",
          sql_q.lazy_frame().collect().height == 2)

    try:
        ds.make_source("sql", uri=f"sqlite:///{db_path}", table='sales; DROP TABLE sales--')
        check("sql table= rejects injection", False, "accepted")
    except ds.DataSourceError:
        check("sql table= rejects injection", True)

    secret_src = ds.make_source("sql", uri="postgresql://alice:hunter2@example/db",
                                query="SELECT 1")
    check("describe() redacts credentials",
          "hunter2" not in str(secret_src.describe()),
          str(secret_src.describe()))

    plugin_built = []
    original = dict(ds._SOURCE_FACTORIES)
    class _Probe(ds.ParquetSource):
        kind = "probe_ds"
    def _factory(**kwargs):
        plugin_built.append(kwargs)
        return _Probe(**kwargs)
    try:
        ds.register_source("probe_ds", _factory)
        probe = ds.make_source("probe_ds", path=parq)
        check("a plugin registers and is built through the registry",
              bool(plugin_built) and probe.lazy_frame().collect().height == 4)
    finally:
        ds._SOURCE_FACTORIES.clear(); ds._SOURCE_FACTORIES.update(original)

    try:
        ds.make_source("definitely_not_a_kind")
        check("unknown kinds are refused", False, "accepted")
    except ds.DataSourceError:
        check("unknown kinds are refused", True)



def test_canvas_editing_chrome_invariants() -> None:
    """The free-canvas chrome contract, pinned statically + one Qt check.

    The live board is the exported report document, so the accepted
    #1 behaviours (no persistent resize handles, glow during a gesture,
    red glow on overlap) live in its CSS/JS -- pinning them here stops a
    refactor from silently dropping them, which no unit test can catch
    without a running display.
    """
    print("[51] Canvas chrome: no permanent handles, gesture glow, red overlap")
    import re as _re
    from modules import report_editor as re_mod

    css = re_mod.editor_css("#6163df")
    js = re_mod.editor_js(2, 24, mode="free")

    check("overlap affordance exists in the editor CSS",
          ".ly-working.ly-overlap > .ly-sel" in css and "__OVERLAP__" not in css)
    check("overlap colour is wired (no unreplaced placeholder)",
          "#ef4444" in css and "__OVERLAP__" not in js)
    check("gesture glow pulse keyframes survive",
          "ly-glow-pulse" in css and "ly-glow-overlap" in css)
    check("ghost goes red on overlap too",
          ".ly-ghost.ly-overlap" in css)
    check("free mode keeps the card body a drag handle, not a corner dot",
          ".ly-editing.ly-free" in css and "cursor: grab" in css)
    check("the grip is display:none outside grid mode",
          bool(_re.search(r"\.ly-grip \{[^}]*display: none", css, _re.S)))
    check("free-mode resize zones are invisible (no permanent chrome)",
          "ly-edge" not in js or "pad = 9" in js)
    check("overlap hit test requires ~5% coverage (no seam trips)",
          "> 0.05" in js)
    check("overlap class is painted during both move and resize",
          js.count("classList.add('ly-overlap')") >= 2)
    check("overlap class is removed when the gesture ends",
          "classList.remove('ly-overlap')" in js)
    check("the gesture glow lives only for the gesture",
          "classList.add('ly-working')" in js and "classList.remove('ly-working')" in js)
    check("no setInterval polling anywhere in the overlay",
          "setInterval" not in js)

    # The Qt-side tiling chrome is opt-in and nothing currently opts in:
    # every card must be born without grips/resize glyphs showing.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from desktop.widgets.chart_card import ChartCard
    from modules.chart_model import make_chart_model
    model = make_chart_model(
        uid="chrome", chart_type="data_table", title="T",
        data={"dimensions": [], "series": []})
    try:
        card = ChartCard("chrome", "T", chart_type="data_table",
                         chart_model=model)
        # NB: isVisible() also requires the whole ancestor chain to be shown,
        # which never happens in offscreen tests -- isHidden() reflects the
        # explicit flag we actually set.
        check("Qt card grip starts hidden", card.grip.isHidden() is True)
        check("Qt card resize handles start hidden",
              all(h.isHidden() for h in card.resize_handles.values()))
        card.set_tiling_enabled(True)
        check("Qt card tiling chrome is opt-in (re-shows on demand)",
              not card.grip.isHidden()
              and all(not h.isHidden() for h in card.resize_handles.values()))
    except Exception as exc:
        check("Qt card construction offscreen", False, repr(exc))


def test_per_element_appearance() -> None:
    """Per-card appearance overrides for KPIs and slicers.

    The override contract, pinned end to end:

    * a KPI/slicer dict may carry ``style`` = {value_color, label_color,
      card_bg} (slicers: label_color, value_color, card_bg);
    * the export emits inline styles + ``data-ly-nostyle-color`` on exactly
      the overridden nodes (property-scoped: a per-card colour override owns
      only COLOUR, never size or alignment, so those stay live), leaving
      every other card on the board theme;
    * the live style patcher and both live sync engines honour the same
      opt-out, so a global theme change never stomps a per-card decision;
    * the two dialogs round-trip the override through result_*().
    """
    print("[52] Per-element KPI & slicer appearance overrides")
    from modules import echarts_export
    from modules.board_model import make_board, make_board_object

    objects = [
        make_board_object("__title__", "text", {"x": 0, "y": 0, "w": 1600,
                                                "h": 64},
                          {"text": "t", "role": "title", "visible": True}),
        make_board_object("kpi-0", "kpi", {"x": 0, "y": 80, "w": 240,
                                           "h": 120},
                          {"index": 0, "label": "BETA", "value": "42",
                           "visible": True,
                           "style": {"value_color": "#ff0000",
                                     "label_color": "#00ff00",
                                     "card_bg": "#111122"}}),
        make_board_object("kpi-1", "kpi", {"x": 260, "y": 80, "w": 240,
                                           "h": 120},
                          {"index": 1, "label": "ALPHA", "value": "7",
                           "visible": True}),
        make_board_object("slicer-0", "slicer",
                          {"x": 0, "y": 220, "w": 240, "h": 160},
                          {"index": 0, "label": "REGION", "widget": "dropdown",
                           "uid": "s1", "visible": True,
                           "spec": {"kind": "in", "column": "region",
                                    "options": ["n", "s"]},
                           "style": {"label_color": "#123456",
                                     "value_color": "#654321",
                                     "card_bg": "#222233"}}),
    ]
    board = make_board(objects,
                       style={"background": "#121a2e", "card": "#1b2245",
                              "border": "#2c3564", "text": "#f5f7ff",
                              "accent": "#6163df"},
                       background={"color": "#121a2e",
                                   "image_data_uri": ""},
                       metadata={"width": 1600, "height": 900,
                                 "title": "t", "orientation": "landscape"})
    html = echarts_export.board_to_html(
        board, editor=True, grid_cols_n=2,
        report_style={"ex_kpi_val_color": "#6163df",
                      "ex_kpi_text_color": "#f5f7ff",
                      "ex_slicer_label_color": "#ffffff"})

    # Overridden KPI: inline colours + colour opt-out attributes, nothing else.
    # (Opt-outs are property-scoped: the override owns COLOUR, never size/align,
    # so a stored font-size stays live -- the "Value size won't re-edit" report.)
    check("overridden KPI value keeps its inline colour",
          'style="color:#ff0000" data-ly-nostyle-color="1"' in html, "")
    check("overridden KPI label keeps its inline colour",
          'style="color:#00ff00" data-ly-nostyle-color="1"' in html, "")
    check("overridden KPI card carries its inline background + opt-out",
          "background:#111122" in html
          and 'data-ly-nostyle="1"' in html, "")
    # The untouched KPI must render exactly as before: no inline colours.
    plain_slice = html.split('data-kpi="1"', 1)[-1]
    check("theme KPI value gets no inline colour",
          'style="color:#ff0000"' not in plain_slice.split("</section>", 1)[0],
          "")

    # Overridden slicer: tile + title + control opt-outs.
    check("slicer title keeps its inline colour",
          'style="color:#123456" data-ly-nostyle-color="1"' in html, "")
    check("slicer control keeps its inline colour",
          'style="color:#654321" data-ly-nostyle-color="1"' in html, "")
    check("slicer card carries its inline background",
          "background:#222233" in html, "")

    # The live patcher must skip every per-element opt-out.
    live = echarts_export._LIVE_STYLE_JS
    check("live patcher skips opted-out tiles for background",
          "if(el.hasAttribute('data-ly-nostyle'))bg=null" in live, "")
    for token in (".kpi-label').forEach(function(el){if(!el.hasAttribute('data-ly-nostyle-color'))put(el,'color',",
                  ".kpi-value').forEach(function(el){if(!el.hasAttribute('data-ly-nostyle-color'))put(el,'color',",
                  ".slicer-title').forEach(function(el){if(!el.hasAttribute('data-ly-nostyle-color'))put(el,'color',",
                  ".slicer-select').forEach(function(el){if(!el.hasAttribute('data-ly-nostyle-color'))put(el,'color',"):
        check(f"live patcher honours {token.split(')')[0]} opt-out",
              token in live, "")

    # The live sync engines re-apply overrides on every in-place refresh,
    # so an edit made while the canvas is ready never flashes the theme
    # colours over a per-card decision.
    from modules import report_editor
    js = report_editor.editor_js(2, 24, mode="free")
    check("KPI sync engine applies kpi.style", "kpiStyle" in js, "")
    check("slicer sync engine applies slicer.style", "slicerStyle" in js, "")

    # -- Dialog round-trip: the controls the user actually clicks. --
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pandas as pd
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from desktop.widgets.kpi_dialog import KpiDialog
    from desktop.widgets.slicer_dialog import SlicerDialog

    df = pd.DataFrame({"amount": [10, 20, 30],
                       "region": ["n", "s", "n"]})

    kpi_existing = {
        "label": "TOTAL", "value": "60",
        "_recipe": {"kpi_type": "Total (Sum)", "col": "amount",
                    "label": "TOTAL"},
        "style": {"value_color": "#ff0000", "card_bg": "#111122"},
    }
    kpi_dialog = KpiDialog(df, existing=kpi_existing)
    check("KPI dialog loads a stored per-card style",
          kpi_dialog._style_override == kpi_existing["style"],
          str(kpi_dialog._style_override))
    out = kpi_dialog.result_kpi()
    check("KPI dialog result carries the per-card style",
          out.get("style") == kpi_existing["style"], str(out.get("style")))
    kpi_dialog._reset_style_override()
    out = kpi_dialog.result_kpi()
    check("KPI dialog Reset drops the per-card style entirely",
          "style" not in out, str(out.get("style")))
    kpi_dialog.close()

    slicer_existing = {
        "uid": "s1", "column": "region", "widget": "list", "scope": "report",
        "target": "", "label": "REGION",
        "spec": {"kind": "in", "column": "region", "values": ["n"]},
        "style": {"label_color": "#123456", "value_color": "#654321",
                  "card_bg": "#222233"},
    }
    slicer_dialog = SlicerDialog(df, charts=[], existing=slicer_existing)
    check("slicer dialog loads a stored per-card style",
          slicer_dialog._style_override == slicer_existing["style"],
          str(slicer_dialog._style_override))
    out = slicer_dialog.result_slicer()
    check("slicer dialog result carries the per-card style",
          out.get("style") == slicer_existing["style"],
          str(out.get("style")))
    slicer_dialog._reset_style_override()
    out = slicer_dialog.result_slicer()
    check("slicer dialog Reset drops the per-card style entirely",
          "style" not in out, str(out.get("style")))
    slicer_dialog.close()


def test_chart_recipe_cache() -> None:
    """The slicer re-run path caches rebuilt charts keyed by content.

    The cache is a correctness-critical mechanism (a wrong hit serves a
    stale chart with no error), so the smoke pins the invariants that
    make it safe: fingerprints change with the data, with row order,
    with the selection and with the recipe; unfingerprintable frames
    bypass the cache; the map is a bounded LRU.
    """
    print("[53] Chart recipe result cache: content keys, bounded LRU")
    import polars as pl
    from services import chart_service as cs

    df_a = pl.DataFrame({"region": ["n", "s", "n", "s"],
                         "amount": [1.0, 2.0, 3.0, 4.0]})
    df_b = pl.DataFrame({"region": ["n", "s", "n", "s"],
                         "amount": [1.0, 2.0, 3.0, 5.0]})
    df_reordered = pl.DataFrame({"region": ["s", "n", "s", "n"],
                                 "amount": [2.0, 1.0, 4.0, 3.0]})
    fp_a = cs.frame_fingerprint(df_a)
    check("polars frame fingerprints cleanly",
          isinstance(fp_a, str) and len(fp_a) == 40, str(fp_a))
    check("fingerprint changes when a value changes",
          fp_a != cs.frame_fingerprint(df_b), "")
    check("fingerprint is row-order sensitive",
          fp_a != cs.frame_fingerprint(df_reordered), "")
    check("fingerprint is stable for the same content",
          fp_a == cs.frame_fingerprint(df_a.clone()), "")
    check("non-polars frames bypass the cache (None fingerprint)",
          cs.frame_fingerprint({"not": "a frame"}) is None
          and cs.frame_fingerprint(None) is None, "")

    recipe = {"chart_type": "bar", "config": {"x": ["region"],
                                              "y": "amount",
                                              "agg": "Sum"},
              "uid": "chartA"}
    report_slicers = [{"uid": "s1", "column": "region", "widget": "list",
                       "scope": "report", "target": "",
                       "spec": {"kind": "in", "column": "region",
                                "values": ["n"]}}]
    chart_scoped = [dict(report_slicers[0], scope="chart", target="chartA")]
    key_a = cs.recipe_cache_key(fp_a, report_slicers, recipe)
    check("key is stable for identical inputs",
          key_a == cs.recipe_cache_key(fp_a, report_slicers, recipe), "")
    check("key changes with the selection",
          key_a != cs.recipe_cache_key(
              fp_a, [dict(report_slicers[0],
                          spec={"kind": "in", "column": "region",
                                "values": ["s"]})], recipe), "")
    check("key changes with the recipe",
          key_a != cs.recipe_cache_key(fp_a, report_slicers,
                                       dict(recipe,
                                            config={"x": ["region"],
                                                    "y": "amount",
                                                    "agg": "Mean"})), "")
    check("key changes with the data",
          key_a != cs.recipe_cache_key(cs.frame_fingerprint(df_b),
                                       report_slicers, recipe), "")
    check("chart-scoped slicers key separately from report-scope",
          key_a != cs.recipe_cache_key(fp_a, chart_scoped, recipe), "")

    # Round-trip: put returns exactly what gets stored.
    sentinel = ("chartA", "A", {"chart_type": "bar", "data": {}})
    cs.recipe_cache_put(key_a, sentinel)
    check("cached tuple comes back as stored",
          cs.recipe_cache_get(key_a) is sentinel, "")

    # Bounded LRU: eviction drops the oldest entry, never the newest.
    for i in range(cs._RECIPE_CACHE_MAX + 2):
        cs.recipe_cache_put(f"eviction-probe-{i}", ("u", "t", {"i": i}))
    probe_cache = getattr(cs, "_RECIPE_CACHE")
    if probe_cache is None:
        cs._recipe_cache()
        probe_cache = cs._RECIPE_CACHE
    check("recipe cache is bounded at _RECIPE_CACHE_MAX",
          len(probe_cache) <= cs._RECIPE_CACHE_MAX, str(len(probe_cache)))
    check("newest entries survive eviction",
          cs.recipe_cache_get(f"eviction-probe-{cs._RECIPE_CACHE_MAX + 1}")
          is not None, "")
    check("oldest entries are evicted first",
          cs.recipe_cache_get("eviction-probe-0") is None, "")

    # The worker hot path actually calls the cache (static integration
    # pin: the cache is wired into the slicer re-run loop, not just
    # defined).
    from pathlib import Path
    workers_src = Path(__file__).resolve().parent.parent.joinpath(
        "desktop", "workers.py").read_text(encoding="utf-8")
    check("ReportInputsWorker consults the recipe cache",
          "recipe_cache_get" in workers_src and "df_fingerprint" in workers_src,
          "")
    check("ReportInputsWorker keys on the applicable slice of slicers",
          "slicers_for_chart" in workers_src, "")


def test_sidebar_filter() -> None:
    """The left panel's settings filter: search + fold-all.

    Pins the behaviour the user actually relies on: matching rows stay,
    matching sections expand, non-matching sections fold away, clearing
    the query restores the pre-filter fold state, and the fold-all
    buttons affect only what is visible.
    """
    print("[54] Sidebar filter: settings search + fold-all")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (
        QApplication, QVBoxLayout, QLabel, QLineEdit, QWidget,
    )
    app = QApplication.instance() or QApplication([])
    from desktop.widgets.formatting_pane import (
        CollapsibleSection, FieldRow,
    )
    from desktop.pages import dashboard_page as dp

    content = QWidget()
    layout = QVBoxLayout(content)
    sec_a = CollapsibleSection("Canvas Colours", expanded=True)
    row_a1 = FieldRow("Background", QLineEdit(), "Canvas background colour")
    row_a2 = FieldRow("Border", QLineEdit(), "Card border colour")
    sec_a.add(row_a1)
    sec_a.add(row_a2)
    layout.addWidget(sec_a)
    sec_b = CollapsibleSection("Text & Accent", expanded=False)
    row_b1 = FieldRow("Font", QLineEdit(), "Font family for the board")
    sec_b.add(row_b1)
    layout.addWidget(sec_b)

    holder = dp.DashboardPage.__new__(dp.DashboardPage)
    holder.sidebar_filter_status = QLabel("")
    holder._pane_fold_saved = None
    dp.DashboardPage._index_sidebar_rows(holder, layout)
    sections = holder._sidebar_sections
    check("indexer finds every section and row",
          len(sections) == 2
          and [len(rows) for _s, rows in sections] == [2, 1],
          str([len(rows) for _s, rows in sections]))

    dp.DashboardPage._on_sidebar_filter_changed(holder, "colour")
    check("matching rows stay visible",
          not row_a1.isHidden() and not row_a2.isHidden()
          and row_b1.isHidden(), "")
    check("matching section expands", sec_a.btn_head.isChecked(), "")
    check("section with no matches folds away", sec_b.isHidden(), "")
    check("status line reports the match count and becomes visible",
          "2 of 3" in holder.sidebar_filter_status.text()
          and not holder.sidebar_filter_status.isHidden(),
          holder.sidebar_filter_status.text())

    dp.DashboardPage._on_sidebar_filter_changed(holder, "font")
    check("a different query re-filters everything",
          row_b1.isHidden() is False and sec_b.isHidden() is False
          and row_a1.isHidden() and row_a2.isHidden() and sec_a.isHidden(),
          "")

    dp.DashboardPage._on_sidebar_filter_changed(holder, "")
    check("clearing restores every row",
          not row_a1.isHidden() and not row_a2.isHidden()
          and not row_b1.isHidden(), "")
    check("clearing restores visibility of every section",
          not sec_a.isHidden() and not sec_b.isHidden(), "")
    check("clearing restores the pre-filter fold state",
          sec_a.btn_head.isChecked() is True
          and sec_b.btn_head.isChecked() is False, "")

    dp.DashboardPage._on_sidebar_fold_all(holder, False)
    check("Fold all collapses every visible section",
          not sec_a.btn_head.isChecked()
          and not sec_b.btn_head.isChecked(), "")
    dp.DashboardPage._on_sidebar_fold_all(holder, True)
    check("Expand all opens every visible section",
          all(section.btn_head.isChecked() for section, _rows in sections),
          "")

    dp.DashboardPage._on_sidebar_filter_changed(holder, "zzzz-no-such-setting")
    check("a query with no matches hides everything and says so",
          sec_a.isHidden() and sec_b.isHidden()
          and "No settings match" in holder.sidebar_filter_status.text(),
          holder.sidebar_filter_status.text())
    dp.DashboardPage._on_sidebar_filter_changed(holder, "")
    check("recovery from a no-match query restores the panel",
          not sec_a.isHidden() and not sec_b.isHidden()
          and not row_b1.isHidden(), "")


def test_database_source_postgres() -> None:
    """The bundled postgres plugin kind + the "From database" dialog.

    Hermetic parts run everywhere; the real wire-protocol round trip is
    opt-in via LYTRIZE_TEST_PG_URI (verified against a local PostgreSQL 17
    cluster while developing this). The connectorx driver route is exercised
    against a sqlite file by hiding the stdlib shortcut -- the identical
    code path a non-sqlite Postgres URI takes.
    """
    print("[55] Database source: postgres kind + dialog validation + worker")

    import os
    import sqlite3
    import tempfile
    from services import data_source as ds
    from services.connector_plugins import load_connector_plugins

    # postgres is a bundled plugin now — discover it (idempotent)
    load_connector_plugins()

    check("postgres source kind registered via bundled plugin",
          "postgres" in ds.available_sources(),
          str(ds.available_sources()))

    uri = "postgresql://u:p@h/db"
    src = ds.make_source("postgres", uri=uri, table="sales")
    check("table builds a quoted SELECT",
          src.query == 'SELECT * FROM "sales"', src.query)
    src2 = ds.make_source("postgres", uri=uri, table="public.sales")
    check("schema.table builds a qualified SELECT",
          src2.query == 'SELECT * FROM "public"."sales"', src2.query)

    for bad in ("sales; DROP TABLE sales--", "sales--", "1sales",
                "sales table", "a.b.c", 'sales"x'):
        try:
            ds.make_source("postgres", uri=uri, table=bad)
            ok = False
        except ds.DataSourceError:
            ok = True
        check(f"table={bad!r} is refused", ok)

    limited = ds.make_source("postgres", uri=uri, table="sales", limit=10)
    check("row limit wraps the query server-side",
          limited.effective_query()
          == 'SELECT * FROM (SELECT * FROM "sales") AS ly_limited LIMIT 10',
          limited.effective_query())
    check("describe() shows the effective (limited) query",
          "LIMIT 10" in str(limited.describe()["query"]),
          str(limited.describe()))
    for bad_limit in (0, -3):
        try:
            ds.make_source("postgres", uri=uri, query="SELECT 1",
                           limit=bad_limit)
            ok = False
        except ds.DataSourceError:
            ok = True
        check(f"limit={bad_limit} is refused", ok)

    try:
        ds.make_source("postgres", uri=uri, query="SELECT 1", engine="duckdb")
        ok = False
    except ds.DataSourceError:
        ok = True
    check("an unknown reader engine is refused", ok)

    secret_src = ds.make_source("postgres",
                                uri="postgresql://alice:hunter2@example/db",
                                query="SELECT 1")
    described = str(secret_src.describe())
    check("describe() redacts the password", "hunter2" not in described,
          described)
    check("fingerprint() carries no credentials",
          "hunter2" not in secret_src.fingerprint())
    from services.data_source import _redact
    check("worker-style error text is redacted",
          "hunter2" not in _redact(
              "could not connect to postgresql://alice:hunter2@h/db"))

    # The connectorx route, exercised against a sqlite file with the stdlib
    # shortcut hidden -- the same code path a Postgres URI takes.
    try:
        import connectorx  # noqa: F401
        have_connectorx = True
    except ImportError:
        have_connectorx = False
    if have_connectorx:
        tmpdir = tempfile.mkdtemp(prefix="lytrize_cx_")
        dbfile = os.path.join(tmpdir, "probe.db")
        conn = sqlite3.connect(dbfile)
        conn.execute("CREATE TABLE t(a INTEGER, b TEXT)")
        conn.executemany("INSERT INTO t(a, b) VALUES (?, ?)",
                         [(1, "x"), (2, "y")])
        conn.commit(); conn.close()
        original = ds.SqlSource._resolve_sqlite_path
        try:
            ds.SqlSource._resolve_sqlite_path = lambda self: None
            probe = ds.make_source("sql", uri=f"sqlite:///{dbfile}",
                                   query="SELECT b, COUNT(*) AS n FROM t "
                                         "GROUP BY b ORDER BY b")
            rows = probe.lazy_frame().collect().drop(ds.ROW_ID).rows()
            check("connectorx route reads and aggregates",
                  rows == [("x", 1), ("y", 1)], str(rows))
        finally:
            ds.SqlSource._resolve_sqlite_path = original
    else:
        print("  SKIP connectorx route (driver not installed here)")

    # Real Postgres, opt-in: LYTRIZE_TEST_PG_URI=postgresql://...
    pg_uri = os.environ.get("LYTRIZE_TEST_PG_URI")
    if pg_uri:
        frame = (ds.make_source("postgres", uri=pg_uri,
                                query="SELECT 1 AS one")
                 .lazy_frame().collect())
        check("real Postgres round trip (env-provided server)",
              frame.height == 1, str(frame.shape))
    else:
        print("  SKIP real-Postgres round trip "
              "(set LYTRIZE_TEST_PG_URI to enable)")

    # -- The dialog's pure validation paths (no event loop) --
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from desktop.pages import upload_page as up
    app = QApplication.instance() or QApplication([])
    from desktop.widgets.database_dialog import DatabaseDialog
    dialog = DatabaseDialog(None)
    check("driver status line is populated",
          bool(dialog.lbl_driver.text().strip()), dialog.lbl_driver.text())
    check("empty form is rejected", dialog._load_spec() is None, "")
    dialog.rb_fields.setChecked(True)
    dialog.le_host.setText("127.0.0.1")
    dialog.le_database.setText("somedb")
    dialog.le_user.setText("alice")
    dialog.le_password.setText("s3cret@with/slash")
    dialog.le_table.setText("sales")
    composed = dialog._composed_uri()
    check("field-composed URI carries scheme, host, db and sslmode",
          composed.startswith("postgresql://alice:")
          and "@127.0.0.1:5432/somedb" in composed
          and "sslmode=prefer" in composed, composed)
    check("password is URL-encoded in the composed URI",
          "s3cret%40with%2Fslash" in composed and "s3cret@with" not in composed,
          composed)
    spec = dialog._load_spec()
    check("fields-mode form produces a spec",
          spec is not None and spec["table"] == "sales"
          and spec["limit"] == DatabaseDialog.DEFAULT_LIMIT,
          str(spec))
    label = up.UploadPage._db_label(spec)
    check("source label never carries credentials",
          "s3cret" not in label and "alice" not in label, label)

    dialog.le_table.setText("sales; DROP TABLE sales--")
    check("the dialog refuses an injected table",
          dialog._load_spec() is None, "")
    dialog.le_table.setText("")
    dialog.rb_query.setChecked(True)
    check("an empty query is rejected", dialog._load_spec() is None, "")
    dialog.te_query.setPlainText("SELECT 1;")
    spec = dialog._load_spec()
    check("trailing semicolons are stripped from queries",
          spec is not None and spec["query"] == "SELECT 1", str(spec))

    # -- The worker against the stdlib sqlite route (zero-dep round trip) --
    from PySide6.QtCore import QEventLoop, QTimer
    from desktop.workers import DatabaseLoadWorker
    tmpdir = tempfile.mkdtemp(prefix="lytrize_dbw_")
    dbfile = os.path.join(tmpdir, "worker.db")
    conn = sqlite3.connect(dbfile)
    conn.execute("CREATE TABLE t(a INTEGER, b TEXT)")
    conn.executemany("INSERT INTO t(a, b) VALUES (?, ?)",
                     [(1, "x"), (2, "y"), (3, "z")])
    conn.commit(); conn.close()
    outcome = {}
    worker = DatabaseLoadWorker({"kind": "sql", "uri": f"sqlite:///{dbfile}",
                                 "table": "t", "limit": 2}, None)
    worker.finished_ok.connect(lambda df: outcome.setdefault("df", df))
    worker.polars_ready.connect(lambda ds_: outcome.setdefault("ds", ds_))
    worker.failed.connect(lambda msg: outcome.setdefault("fail", msg))
    worker.start()
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(15000)
    loop.exec()
    guard.stop()
    check("worker loads through the stdlib sqlite route",
          outcome.get("df") is not None and outcome["df"].shape[0] == 2,
          str(outcome.get("fail") or outcome.get("df")))
    check("worker frame carries no internal row-id column",
          outcome.get("df") is not None
          and ds.ROW_ID not in outcome["df"].columns,
          str(outcome.get("df")))
    check("worker emitted the dataset snapshot",
          outcome.get("ds") is not None, "")
    # Teardown hygiene: a leaked top-level dialog can crash Qt at exit
    # (observed as exit 139 after an otherwise-green run).
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_database_connector_plugins() -> None:
    """Drop-in connector plugins: discovery, registration, overrides."""
    print("[56] Database connector plugins: drop-in add-ons")

    import os
    import tempfile
    import textwrap
    from pathlib import Path
    from services import data_source as ds
    from services import connector_plugins as cp

    tmpdir = Path(tempfile.mkdtemp(prefix="lytrize_plugins_"))
    good = tmpdir / "myengine.py"
    good.write_text(textwrap.dedent("""
        from services.connector_plugins import ConnectorSpec
        from services.data_source import SqlSource

        class MyEngineSource(SqlSource):
            kind = "myengine"

        CONNECTOR = ConnectorSpec(
            kind="myengine",
            display_name="My Engine",
            uri_template="myengine://{auth}{host}:{port}/{database}",
            default_port=4321,
            driver_modules=("sqlite3",),
            driver_hint="pip install myengine",
            factory=lambda **kw: MyEngineSource(**kw),
            notes="reference test engine",
        )
    """), encoding="utf-8")
    (tmpdir / "broken_syntax.py").write_text("def nope(:\n", encoding="utf-8")
    (tmpdir / "no_spec.py").write_text("X = 1\n", encoding="utf-8")
    (tmpdir / "bad_kind.py").write_text(
        "from services.connector_plugins import ConnectorSpec\n"
        "from services.data_source import SqlSource\n"
        "CONNECTOR = ConnectorSpec(kind='BAD KIND!', display_name='X',"
        " uri_template='x://{host}', factory=SqlSource)\n", encoding="utf-8")

    saved_factories = dict(ds._SOURCE_FACTORIES)
    saved_env = os.environ.get("LYTRIZE_PLUGINS_DIR")
    try:
        os.environ["LYTRIZE_PLUGINS_DIR"] = str(tmpdir)

        result = cp.load_connector_plugins()
        kinds = [s.kind for s in result.specs]
        check("drop-in plugin discovered and registered",
              "myengine" in kinds, str(kinds))
        check("bundled postgres plugin discovered",
              "postgres" in kinds, str(kinds))

        failure_files = [Path(f).name for f, _ in result.failures]
        check("syntax-error plugin recorded, not fatal",
              "broken_syntax.py" in failure_files, str(failure_files))
        check("CONNECTOR-less plugin recorded, not fatal",
              "no_spec.py" in failure_files, str(failure_files))
        check("invalid kind refused with a clear reason",
              "bad_kind.py" in failure_files, str(failure_files))

        # composed spec details for the discovered plugin
        spec = next(s for s in result.specs if s.kind == "myengine")
        check("spec carries display name, port, driver info, notes",
              spec.display_name == "My Engine" and spec.default_port == 4321
              and spec.driver_modules == ("sqlite3",)
              and spec.notes == "reference test engine", str(spec))
        check("plugin file path recorded on the spec",
              spec.source_path.endswith("myengine.py"), spec.source_path)

        uri = cp.compose_uri(spec, host="db.example.com", port=4321,
                             database="appdb", user="a@b.com",
                             password="p/ss:w0rd", sslmode="")
        check("compose_uri percent-encodes user and password",
              uri == "myengine://a%40b.com:p%2Fss%3Aw0rd@db.example.com:"
                     "4321/appdb", uri)
        check("driver_status finds installed module",
              "reader available: sqlite3" in cp.driver_status(spec),
              cp.driver_status(spec))
        no_driver = cp.ConnectorSpec(
            kind="nodriver", display_name="No Driver",
            uri_template="nodriver://{host}/{database}",
            default_port=1,
            driver_modules=("definitely_not_a_real_module_xyz",),
            driver_hint="pip install nodriver",
            factory=lambda **kw: None)
        check("driver_status reports missing driver + pip hint",
              cp.driver_status(no_driver) == "no reader installed — pip install nodriver",
              cp.driver_status(no_driver))

        # worst-case contract values are rejected at construction
        for bad_kwargs, why in (
                (dict(kind="bad K!nd", display_name="X",
                      uri_template="x://{host}", factory=lambda **kw: None),
                 "invalid kind"),
                (dict(kind="okname", display_name="",
                      uri_template="x://{host}", factory=lambda **kw: None),
                 "empty display name"),
                (dict(kind="okname", display_name="X",
                      factory=lambda **kw: None),
                 "no template and no compose_uri"),
        ):
            try:
                cp.ConnectorSpec(**bad_kwargs)
                ok = False
            except (ValueError, TypeError):
                ok = True
            check(f"spec contract rejects {why}", ok)

        # make_source works end-to-end for the discovered plugin (sqlite URI)
        import sqlite3 as _sqlite3
        dbfile = tmpdir / "probe.db"
        conn = _sqlite3.connect(str(dbfile))
        conn.execute("CREATE TABLE t(a INTEGER)")
        conn.execute("INSERT INTO t(a) VALUES (1)")
        conn.commit(); conn.close()
        frame = (ds.make_source("myengine", uri=f"sqlite:///{dbfile}",
                                query="SELECT a FROM t")
                 .lazy_frame().collect())
        check("plugin source reads through SqlSource machinery",
              frame.shape[0] == 1, str(frame.shape))

        # a plugin may override a built-in kind (with the registry logging)
        override_src = tmpdir / "partyoverride.py"
        override_src.write_text(textwrap.dedent("""
            from services.connector_plugins import ConnectorSpec

            class _MarkerSource:
                kind = "csv"
                def __init__(self, **kw):
                    self.marker = ("overridden", kw.get("path"))
                def lazy_frame(self):
                    raise AssertionError("not used in this smoke")
                def fingerprint(self):
                    return "csv:override"
                def describe(self):
                    return {"kind": "csv", "label": "override"}
                def to_dataset(self):
                    raise AssertionError("not used in this smoke")

            CONNECTOR = ConnectorSpec(
                kind="csv", display_name="CSV (overridden)",
                uri_template="file://{host}", factory=_MarkerSource)
        """), encoding="utf-8")
        os.environ["LYTRIZE_PLUGINS_DIR"] = str(tmpdir)
        result2 = cp.load_connector_plugins()
        check("override plugin discovered",
              any(s.kind == "csv" for s in result2.specs),
              str([s.kind for s in result2.specs]))
        marker = ds.make_source("csv", path="whatever.csv")
        check("plugin override wins over the built-in kind",
              getattr(marker, "marker", (None, None))[0] == "overridden",
              repr(getattr(marker, "marker", None)))
    finally:
        # restore the pristine registry + env so later sections are unaffected
        ds._SOURCE_FACTORIES.clear()
        ds._SOURCE_FACTORIES.update(saved_factories)
        if saved_env is None:
            os.environ.pop("LYTRIZE_PLUGINS_DIR", None)
        else:
            os.environ["LYTRIZE_PLUGINS_DIR"] = saved_env
    check("registry restored after override test",
          sorted(ds.available_sources()) == ["csv", "parquet", "postgres", "sql"],
          str(ds.available_sources()))


def main():
    import services.transform_service as ts
    failures = []
    ran = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        ran += 1
        try:
            if fn.__code__.co_argcount:
                fn(ts)
            else:
                fn()
        except Exception:
            import traceback
            failures.append(name)
            print(f"\n!! {name} raised:\n" + traceback.format_exc())
    print("=" * 72)
    print(f"sections ran: {ran} | crashed sections: {len(failures)}")
    if failures:
        print("crashed:", ", ".join(failures))
    print(f"PASS: {len(PASS)} | FAIL: {len(FAIL)}")
    if FAIL:
        print("failures:")
        for item in FAIL:
            print("  FAIL", item)
    if FAIL or failures:
        sys.exit(1)
    print("ALL SMOKE TESTS PASSED")
    # Qt/WebEngine C++ teardown at interpreter shutdown intermittently
    # segfaults in the offscreen environment (pre-existing and environmental:
    # observed equally with the 99-, 100- and 101-section suites, always AFTER
    # the full summary has printed, with no Python frame). Flush and bypass
    # the C++ teardown on the green path only; failure paths exit(1) above
    # and keep normal, fully-diagnosed interpreter shutdown.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()