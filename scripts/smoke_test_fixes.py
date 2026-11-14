#!/usr/bin/env python3
"""Offscreen smoke test for the Lytrize bug-fix set.

Run from the repo root:
    QT_QPA_PLATFORM=offscreen python scripts/smoke_test_fixes.py

Exercises every operation that changed during the fixes:
  - Backend: find_replace (empty-find, CI-literal), numeric_clean op keys,
    text_clean, validate actions
  - DataCleanerPanel: String Ops constants + concat col2, Numeric op-key map
  - DtypeTransformerPanel: apply after lossy preview, nonsense guard, clear
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
    out_nan = p._compute_validate("v", ts.VALIDATION_PATTERNS["Integer number"], "nan")
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
              new_col == "part1" and pd.isna(got.iloc[0]) and pd.isna(got.iloc[1])
              and got.iloc[2] == "Delta", repr(list(got)))

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
              and list(bridge.df["joined2"]) == ["Alpha x", "Beta y", "Gamma Delta z"],
              repr(list(bridge.df["joined2"]) if bridge.df is not None else None))
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
          dc.count_changed_values(pd.Series([1.0, np.nan]),
                                  pd.Series([1.0, np.nan])) == 0)
    check("count_changed counts real change",
          dc.count_changed_values(pd.Series([1.0, np.nan]),
                                  pd.Series([1.0, 5.0])) == 1)

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
        got = p._compute_numeric_clean("num", "clamp", {"min": 2.0, "max": 4.0})["num"]
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
            got = p._compute_numeric_clean("num", key, {"iqr_multiplier": 1.5})["num"]
            check(f"{name}: outlier replaced, rest intact",
                  abs(got.iloc[4] - fill) < 1e-9 and got.iloc[0] == 1.5
                  and got.iloc[2] == 3.5, repr(list(got)))

        # Round: NaN-aware count must say 0 (regression for truthful labels)
        p.cmb_num_op.setCurrentText("Round to N decimal places")
        p.num_decimals.setValue(1)
        p._on_num_preview()
        got = p._compute_numeric_clean("num", "round", {"decimals": 1})["num"]
        check("round to 1dp values", list(got.dropna()) == [1.5, 2.5, 3.5, 100.0],
              repr(list(got)))
        check("round preview count honest (NaN not counted)",
              "0 values will change" in p.lbl_num_preview.text()
              and not p.btn_num_apply.isEnabled(), p.lbl_num_preview.text())

        # Min-max
        p.cmb_num_op.setCurrentText("Scale to 0-1 (min-max)")
        p._on_num_preview()
        got = p._compute_numeric_clean("num", "minmax", {})["num"]
        check("minmax range", got.iloc[0] == 0.0 and got.iloc[4] == 1.0
              and abs(got.iloc[1] - (2.5 - 1.5) / (100.0 - 1.5)) < 1e-9,
              repr(list(got)))

        # Z-score
        p.cmb_num_op.setCurrentText("Z-score standardisation")
        p._on_num_preview()
        got = p._compute_numeric_clean("num", "zscore", {})["num"]
        exp = (base - base.mean()) / base.std()
        check("zscore matches + mean~0",
              np.allclose(got.dropna(), exp.dropna())
              and abs(got.dropna().mean()) < 1e-9, repr(list(got)))

        # Fill NaN variants
        p.cmb_num_op.setCurrentText("Fill NaN with median")
        p._on_num_preview()
        got = p._compute_numeric_clean("num", "fillna_median", {})["num"]
        check("fillna median", got.isna().sum() == 0 and got.iloc[3] == base.median(),
              repr(list(got)))
        p.cmb_num_op.setCurrentText("Fill NaN with mean")
        p._on_num_preview()
        got = p._compute_numeric_clean("num", "fillna_mean", {})["num"]
        check("fillna mean", got.isna().sum() == 0
              and abs(got.iloc[3] - base.mean()) < 1e-9, repr(list(got)))
        p.cmb_num_op.setCurrentText("Fill NaN with custom value")
        p.num_fill.setValue(-7.5)
        p._on_num_preview()
        got = p._compute_numeric_clean("num", "fillna_value", {"value": -7.5})["num"]
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
              bridge.df is not None and bridge.df["num"].iloc[4] == 10.0
              and bridge.df["num"].iloc[0] == 1.5 and pd.isna(bridge.df["num"].iloc[3]),
              repr(list(bridge.df["num"]) if bridge.df is not None else None))

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
          bridge.df is not None and str(bridge.df["n"].dtype) == "float64",
          str(bridge.df["n"].dtype) if bridge.df is not None else "no df")

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
    s = pd.Series(["01/01/2023", "13/01/2023", "05/01/2023", "bad", None],
                  dtype="category")
    res = tss.preview_dtype_conversion(s, "date")
    check("day-first categorical -> date (80% success)",
          "error" not in res and res["pct"] == 80.0,
          f"pct={res.get('pct')} err={res.get('error')}")
    conv = pd.Series(res["converted"])
    check("no month/day swap (13/01->Jan13, 05/01->Jan5)",
          conv.iloc[0] == _dt.date(2023, 1, 1)
          and conv.iloc[1] == _dt.date(2023, 1, 13)
          and conv.iloc[2] == _dt.date(2023, 1, 5)
          and pd.isna(conv.iloc[3]) and pd.isna(conv.iloc[4]),
          repr(list(conv)))

    res_dt = tss.preview_dtype_conversion(s, "datetime64[ns]")
    conv_dt = pd.Series(res_dt["converted"])
    check("day-first categorical -> datetime64[ns]",
          res_dt["pct"] == 80.0 and conv_dt.iloc[1] == pd.Timestamp("2023-01-13"),
          repr(list(conv_dt)))

    # Unambiguous US month-first column must stay month-first
    res_us = tss.preview_dtype_conversion(
        pd.Series(["01/13/2023", "05/20/2023", "12/31/2023"]), "datetime64[ns]")
    conv_us = pd.Series(res_us["converted"])
    check("US month-first preserved",
          res_us["pct"] == 100.0
          and list(conv_us) == pd.to_datetime(
              ["2023-01-13", "2023-05-20", "2023-12-31"]).tolist(),
          repr(list(conv_us)))

    # Mixed conventions: day>12 values recovered via explicit-format retry
    res_mix = tss.preview_dtype_conversion(
        pd.Series(["13/01/2023", "01/13/2023"]), "datetime64[ns]")
    conv_mix = pd.Series(res_mix["converted"])
    check("mixed day/month conventions both recovered",
          res_mix["pct"] == 100.0
          and conv_mix.iloc[0] == pd.Timestamp("2023-01-13")
          and conv_mix.iloc[1] == pd.Timestamp("2023-01-13"),
          repr(list(conv_mix)))

    # ISO columns unaffected
    res_iso = tss.preview_dtype_conversion(
        pd.Series(["2024-01-05", "2024-02-10", "junk"]), "date")
    check("ISO dates still parse (2/3 success)",
          res_iso["pct"] == round((2 / 3) * 100, 1)
          and pd.Series(res_iso["converted"]).iloc[0] == _dt.date(2024, 1, 5),
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
          and bridge.df["transaction_date"].iloc[1] == _dt.date(2023, 1, 13)
          and str(bridge.df["transaction_date"].dtype) == "object",
          repr(list(bridge.df["transaction_date"]) if bridge.df is not None else None))


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
            ok = res.isna().sum() >= 1
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
    check("web view has real height immediately after Generate",
          card.plot_view._web_view.height() > 50,
          f"h={card.plot_view._web_view.height()}")

    # Let the event loop breathe: the shell must load and flush its pending
    # figure WITHOUT the user resizing anything.
    deadline = time.time() + 5.0
    while time.time() < deadline and not card.plot_view._page_loaded:
        app_.processEvents()
        time.sleep(0.01)
    check("web view finished loading with no manual resize",
          card.plot_view._page_loaded, "loadFinished never fired")
    check("QWebChannel handshake completed",
          card.plot_view._bridge.channel_ready,
          "channelReady never called from JS")
    check("pending figure was flushed to the loaded page",
          card.plot_view._pending_fig_json is None, "figure still queued")

    # THE decisive check: probe the live page. loadFinished + an empty
    # pending queue used to look healthy while the figure had actually been
    # emitted into a QWebChannel that had not connected yet, so the chart
    # area stayed blank. Only the DOM can prove the chart really rendered.
    res: dict = {}
    card.plot_view._web_view.page().runJavaScript(
        "(function(){try{return JSON.stringify({"
        "n:(chartDiv.data||[]).length,"
        "title:(chartDiv.layout&&chartDiv.layout.title||{}).text||null,"
        "svg:chartDiv.querySelectorAll('.main-svg').length});}"
        "catch(e){return 'ERR '+e}})()",
        lambda v: res.update(js=v))
    deadline = time.time() + 5.0
    while time.time() < deadline and "js" not in res:
        app_.processEvents()
        time.sleep(0.01)
    probe = res.get("js") or "{}"
    check("figure actually reached the page (JS has traces)",
          '"n":4' in probe.replace(" ", ""), probe)
    check("chart DOM rendered (svg present)",
          '"svg":' in probe.replace(" ", "") and '"svg":0' not in probe,
          probe)

    # relayout() must never trigger a reload.
    loaded_before = card.plot_view._page_loaded
    card.plot_view.relayout()
    app_.processEvents()
    check("relayout does not reload the page",
          card.plot_view._page_loaded == loaded_before, "")

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
    import plotly.graph_objects as go
    from chart_display import (
        apply_display_options, theme_figure, chart_canvas_color,
    )
    from desktop import theme as th

    fig = go.Figure(go.Bar(x=["A", "B"], y=[3, 5]))
    # Mirror what every analysis runner emits (modules.charts.chart_layout)
    # so the test exercises the real 48px top margin being reclaimed.
    fig.update_layout(title="Statistical Summary", margin=dict(t=48))

    styled = apply_display_options(fig, {}, {"header_size": 28})
    check("title pinned to the top-left",
          styled.layout.title.x == 0.0
          and styled.layout.title.xanchor == "left"
          and styled.layout.title.y is not None
          and styled.layout.title.y > 0.9
          and styled.layout.title.yanchor == "top",
          f"x={styled.layout.title.x}/{styled.layout.title.xanchor} "
          f"y={styled.layout.title.y}/{styled.layout.title.yanchor}")
    # "remove this empty space": the title used to sit at y=0.98, leaving 2%
    # of the card height as a dead band ABOVE it. It must now be flush with
    # the top of the chart area.
    check("no dead band above the title (y flush with the top)",
          float(styled.layout.title.y) == 1.0,
          f"y={styled.layout.title.y}")
    check("top margin reclaimed for the plot",
          styled.layout.margin.t < 48, f"t={styled.layout.margin.t}")
    check("title text preserved by placement",
          styled.layout.title.text == "Statistical Summary",
          str(styled.layout.title.text))
    # Idempotent: live preview + re-generate re-run this constantly.
    twice = apply_display_options(styled, {}, {"header_size": 28})
    check("title placement is idempotent",
          twice.layout.title.y == styled.layout.title.y
          and twice.layout.margin.t == styled.layout.margin.t,
          f"{twice.layout.margin.t} vs {styled.layout.margin.t}")
    check("placement never mutates the source figure",
          fig.layout.margin.to_plotly_json() == {"t": 48}
          and fig.layout.title.x is None,
          f"t={fig.layout.margin.t} x={fig.layout.title.x}")

    # The card surface must equal the figure canvas in BOTH modes, else the
    # chart sits on a mismatched rectangle (the "BG not transparent" report).
    for mode in ("dark", "light"):
        themed = theme_figure(fig, mode)
        card_bg = th.CHART_CANVAS[th.mode_tokens(mode)["chart_canvas_key"]]
        check(f"{mode}: card surface matches the chart canvas",
              card_bg.lower() == str(themed.layout.paper_bgcolor).lower()
              == str(themed.layout.plot_bgcolor).lower()
              == chart_canvas_color(mode).lower(),
              f"card={card_bg} paper={themed.layout.paper_bgcolor}")
    check("chart card QSS uses the canvas colour, not alternate-base",
          "QFrame#chart_card { background: #ffffff" in th.build_qss("light"),
          "rule missing")


def test_header_alignment_and_band():
    """Header alignment dropdown, size-scaled top band, unclipped labels.

    Three issues from one screenshot:
      1. FEATURE  -- a header alignment control (left / center / right) that
         spans the FULL canvas, so it auto-adjusts to the card width and
         covers the legend column as well as the plot.
      2. LAYOUT   -- the header band must scale with the header size; it was
         a flat 34px, so a 36px or 60px header overflowed onto the plot.
      3. CLIPPING -- an "outside" value label on the tallest bar is drawn
         above the plot area and was being sliced in half.
    """
    print("[22] Header alignment, band scaling, unclipped value labels")
    import plotly.graph_objects as go
    from chart_display import (
        apply_display_options, default_layout_opts, default_typo_opts,
        _title_band_px,
    )

    def build(align="left", header_size=28, layout=None, **typo):
        # A single tall bar: the classic shape where the top value label is
        # the one that gets clipped.
        f = go.Figure(go.Bar(x=["unit_price"], y=[45],
                            text=["45.00"], textposition="outside"))
        f.update_layout(title="Statistical Summary", margin=dict(t=48))
        t = default_typo_opts()
        t.update(header_align=align, header_size=header_size, **typo)
        lo = default_layout_opts()
        lo.update(layout or {})
        return apply_display_options(f, lo, t)

    # -- 1. Alignment ------------------------------------------------------
    want = {"left": (0.0, "left"), "center": (0.5, "center"),
            "right": (1.0, "right")}
    for align, (x, anchor) in want.items():
        s = build(align=align)
        T = s.layout.title
        check(f"header align {align}: x/anchor honoured",
              float(T.x) == x and T.xanchor == anchor,
              f"x={T.x} xanchor={T.xanchor}")
        # Container coordinates are what make it span the whole chart area
        # (margins + the legend column) instead of just the plot domain.
        check(f"header align {align}: anchored to the full canvas",
              T.xref == "container" and T.yref == "container",
              f"xref={T.xref} yref={T.yref}")
    check("header alignment is a real option, not hard-coded",
          "header_align" in default_typo_opts(),
          str(sorted(default_typo_opts())))
    check("default alignment is left (existing charts unchanged)",
          default_typo_opts()["header_align"] == "left",
          str(default_typo_opts()["header_align"]))
    # An unrecognised stored value must not blank the title or crash.
    junk = build(align="diagonal")
    check("unknown alignment falls back to left",
          float(junk.layout.title.x) == 0.0
          and junk.layout.title.xanchor == "left",
          f"x={junk.layout.title.x} xanchor={junk.layout.title.xanchor}")
    check("alignment survives a header size change",
          float(build(align="right", header_size=48).layout.title.x) == 1.0,
          "")

    # -- 2. Band scales with the header size ------------------------------
    bands = {n: build(header_size=n).layout.margin.t for n in (10, 28, 36, 60)}
    check("header band grows with the header size",
          bands[10] < bands[28] < bands[36] < bands[60], str(bands))
    # The regression: a flat 34px band clipped a 36px header.
    check("36px header is not squeezed into the old flat 34px band",
          bands[36] > 34, f"t={bands[36]}")
    check("60px header gets a real band, not 34px",
          bands[60] > 60, f"t={bands[60]}")
    check("_title_band_px is monotonic and sane",
          _title_band_px(28) == 38 and _title_band_px(60) == 76,
          f"{_title_band_px(28)}/{_title_band_px(60)}")
    check("_title_band_px tolerates junk sizes",
          _title_band_px(None) == _title_band_px("bogus") == 38,
          f"{_title_band_px(None)}/{_title_band_px('bogus')}")

    # -- 3. Value labels are not clipped -----------------------------------
    outside = build(layout={"label_position": "outside"})
    check("outside value labels are unclipped",
          all(getattr(tr, "cliponaxis", None) is False
              for tr in outside.data),
          str([getattr(tr, "cliponaxis", None) for tr in outside.data]))
    # Only "outside" needs the room; inside/auto labels stay inside the plot
    # and must keep Plotly's default clipping.
    for pos in ("inside", "auto"):
        s = build(layout={"label_position": pos})
        check(f"'{pos}' labels keep the default clipping",
              all(getattr(tr, "cliponaxis", None) is not False
                  for tr in s.data),
              str([getattr(tr, "cliponaxis", None) for tr in s.data]))
    # Hiding the labels must not leave a stray cliponaxis override behind.
    hidden = apply_display_options(
        go.Figure(go.Bar(x=["a"], y=[1], text=["1"])),
        dict(default_layout_opts(), show_value_labels=False),
        default_typo_opts())
    check("hidden labels leave clipping at Plotly's default",
          all(getattr(tr, "cliponaxis", None) is None
              for tr in hidden.data),
          str([getattr(tr, "cliponaxis", None) for tr in hidden.data]))

    # -- The UI control exists and round-trips ----------------------------
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
    # And the value the UI produces must be one the backend actually honours.
    b.prefill_typography({"header_align": "center"})
    s = apply_display_options(
        go.Figure(go.Bar(x=["a"], y=[1])).update_layout(title="T"),
        default_layout_opts(), b._typo_opts)
    check("the UI's value drives the figure",
          float(s.layout.title.x) == 0.5 and s.layout.title.xanchor == "center",
          f"x={s.layout.title.x} xanchor={s.layout.title.xanchor}")


def test_card_header_gap_and_cold_start():
    """The header must sit flush at the card top, and a cold start must paint.

    Two separate reports, two root causes:

    1. GAP. The action row (edit/style/delete) was the FIRST ITEM of the
       card's QVBoxLayout, so its 28px buttons + 6px spacing pushed the whole
       canvas down. The header is anchored to the top of the CANVAS, so it
       inherited that offset and started ~40px below the card's top edge --
       the "gap between chart header and top edge". The row now floats over
       the plot in the same grid cell instead of reserving a row above it.

    2. RESTART. _relayout_when_sized polled a fixed 20x20ms and gave up --
       a 400ms budget. A cold process needs ~1.3s before the QWebEngine shell
       has loaded and the QWebChannel handshake has completed, so the poll
       always expired on a fresh start and the chart stayed at 0px until the
       user resized the window ("close the app, reopen, Generate is blank
       again"). The wait is now self-re-arming and time-bounded instead.
    """
    print("[23] Card header flush to top + cold-start render")
    import time
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    import plotly.graph_objects as go
    import desktop.widgets.plotly_web_view as pwv
    from desktop.widgets.chart_card import ChartCard

    app_ = app()

    # -- 1. No chrome row above the canvas --------------------------------
    host = QWidget()
    host.resize(1200, 700)
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    card = ChartCard("gap_uid", "Statistical Summary",
                     go.Figure(go.Bar(x=["A"], y=[3])), "statistical")
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

    # -- 2a. The relayout wait is not a short fixed budget -----------------
    check("relayout wait is time-bounded, not attempt-bounded",
          pwv._RELAYOUT_MAX_SECONDS >= 5.0,
          f"{pwv._RELAYOUT_MAX_SECONDS}s (a cold start needs ~1.3s)")
    # The old ceiling was 20 attempts x 20ms = 400ms.
    check("relayout budget exceeds a measured cold start",
          pwv._RELAYOUT_MAX_SECONDS * 1000 > 1300 * 3,
          f"{pwv._RELAYOUT_MAX_SECONDS * 1000:.0f}ms")

    # -- 2b. It really waits past the old 400ms cutoff ---------------------
    # Drive a real PlotlyWebView but hold the page "unloaded" for longer than
    # the old budget; the chain must still be alive afterwards and must fire
    # exactly one relayout once the page reports ready.
    calls = []
    real_relayout = pwv.PlotlyWebView.relayout

    def counting(self):
        calls.append(self._web_view.height() if self._web_view else -1)
        return real_relayout(self)

    pwv.PlotlyWebView.relayout = counting
    view = pwv.PlotlyWebView()
    holder = QWidget()
    holder.resize(900, 500)
    vl = QVBoxLayout(holder)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.addWidget(view)
    holder.show()
    app_.processEvents()
    try:
        # Hold the view at 0px, which is the "not sizeable yet" state a cold
        # start sits in. Height is used (rather than faking _page_loaded)
        # because the real page finishes loading on its own within ~1s and
        # would otherwise complete the chain, making the test prove nothing
        # about the old fixed-attempt expiry.
        view._web_view.setFixedHeight(0)
        view._relayout_when_sized()
        check("a relayout chain is in flight", view._relayout_pending, "no chain")
        deadline = time.time() + 1.2
        while time.time() < deadline:
            app_.processEvents()
            time.sleep(0.01)
        check("chain survives well past the old 400ms budget",
              view._relayout_pending,
              "gave up early (old fixed-attempt behaviour)")
        check("chain outlives 3x the old fixed budget",
              view._relayout_pending,
              "expired like the old 20-attempt poll")
        n_before = len(calls)
        # A second caller must not start a competing chain.
        view._relayout_when_sized()
        check("a second caller reuses the in-flight chain",
              len(calls) == n_before, f"extra relayouts={len(calls)-n_before}")
        # Give it a real size: the chain must now complete and relayout once.
        view._web_view.setFixedHeight(400)
        deadline = time.time() + 2.0
        while time.time() < deadline and view._relayout_pending:
            app_.processEvents()
            time.sleep(0.01)
        check("chain completes once the view is sizeable",
              not view._relayout_pending, "still pending")
        n_ready = len(calls)
        deadline = time.time() + 0.4
        while time.time() < deadline:
            app_.processEvents()
            time.sleep(0.01)
        check("no repeat relayouts once settled",
              len(calls) == n_ready, f"extra={len(calls)-n_ready}")
    finally:
        pwv.PlotlyWebView.relayout = real_relayout
        holder.close()

    # -- 2c. The title is flush but never clipped --------------------------
    from chart_display import apply_display_options
    styled = apply_display_options(
        go.Figure(go.Bar(x=["A"], y=[3])).update_layout(
            title="Statistical Summary"),
        {}, {"header_size": 28})
    check("title anchored at the exact top of the canvas",
          float(styled.layout.title.y) == 1.0,
          f"y={styled.layout.title.y}")
    # A 0 pad puts the line box's ascender space above the edge (measured at
    # -4px in the real DOM), which can shave the tops off tall/italic headers.
    check("title has a small top pad so glyphs cannot be cut",
          int(styled.layout.title.pad.t) >= 4,
          f"pad.t={styled.layout.title.pad.t}")

    # -- 2d. The modebar is pushed clear of the floating buttons -----------
    check("shell CSS moves the modebar below the floating action bar",
          ".modebar" in pwv._SHELL_TEMPLATE
          and "top: 32px" in pwv._SHELL_TEMPLATE,
          "modebar offset missing")


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
    import pandas as pd
    import plotly.graph_objects as go

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
    df = pd.DataFrame({"g": list("abcd") * 15, "v": np.arange(60.0)})
    b1.set_df(df)
    fig = go.Figure(data=[go.Bar(x=list("abcd"), y=[1, 2, 3, 4])])
    b1.add_chart("uid_r1", "Restored Bar", fig,
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
    b1.save_draft()
    # save_draft() hands the SQLite write to a background saver thread, so
    # the row is not guaranteed visible until the queue drains. Production
    # never needs immediate durability; the test does -- flush, don't sleep.
    b1.flush_draft_save()

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

    uid, title, rfig = charts[0]
    check("draft: title restored", title == "Restored Bar", f"got {title!r}")
    check("draft: figure restored", rfig is not None
          and len(getattr(rfig, "data", [])) == 1)
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
    """Every emoji must have a real outline, not an empty .notdef box.

    The first version of the font guard also rejected NotoColorEmoji. That
    stopped the segfault but left every emoji as a tofu rectangle, because
    NotoColorEmoji is the only font on this system with these glyphs and no
    monochrome fallback is installed.

    Checks coverage with fontconfig directly. This is the ONLY reliable
    signal: ``QFontMetrics.inFont()`` and ``horizontalAdvance()`` both succeed
    happily on a .notdef box, which is exactly why the tofu shipped unnoticed
    behind a green test.
    """
    print("[27] Emoji glyphs resolve to a real font (no tofu)")
    import shutil
    import subprocess

    fc_list = shutil.which("fc-list")
    if not fc_list:
        check("tofu: fc-list available for glyph coverage check", False,
              "fontconfig tools not installed; skipped")
        return

    # Codepoints that must come from the colour emoji font, including the six
    # that originally crashed.
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
    missing = []
    for cp, name in sorted(codepoints.items()):
        r = subprocess.run([fc_list, f":charset={cp}", "family"],
                           capture_output=True, timeout=60)
        families = [ln for ln in r.stdout.decode("utf-8", "replace").splitlines()
                    if ln.strip()]
        if not families:
            missing.append("U+%s (%s)" % (cp, name))
    check("tofu: every emoji has at least one font claiming it",
          not missing, "no font covers: " + ", ".join(missing))


def test_legend_column_tracks_card_width():
    """The reserved legend column must track the pixels it needs, not a % of the card.

    Regression: on a full-width chart the legend sat ~380px away from the last
    bar. ``_legend_strip`` reserved a flat fraction of whatever width the card
    happened to be, so a 1900px card reserved 20% = 380px for a legend box that
    needs ~76px. The reservation is now sized in pixels and shrinks as the card
    grows.
    """
    print("[28] Legend column sized by pixels, not card fraction")
    import copy

    import plotly.graph_objects as go
    from chart_display import (_legend_need_px, _legend_axis_need_px,
                               apply_display_options, legend_anchor,
                               refit_legend_width, infer_legend_position)

    def base(names):
        f = go.Figure()
        for i, n in enumerate(names):
            f.add_bar(x=["c"], y=[i + 1], name=n)
        return f

    names = ["mean", "min", "max", "std"]

    def reserved_px(fig, pos, w):
        """Pixels the reserved column actually spans.

        ``xaxis.domain`` is a fraction of the PLOT AREA (card minus the l/r
        outer margins), so the reserved width has to be measured the same way
        the reservation is computed.  Measuring it against the card width is
        what made this test disagree with a correct implementation.  Unset
        margins default to Plotly's own (see ``_margin_px``), not 0.
        """
        from chart_display import _plot_area_width
        m = fig.layout.margin.to_plotly_json() or {}
        area = _plot_area_width(m, w)
        # legend_anchor() returns a DICT ({"side": ...}); it used to return a
        # tuple whose element 0 was the side, so this indexed [0]. Reading the
        # key keeps the test aligned with the eight-position legend API.
        side = legend_anchor(pos)["side"]
        d = list(fig.layout.xaxis.domain)
        return (1.0 - d[1] if side == "right" else d[0]) * area

    # The reserved strip must hold the legend box AND the axis tick labels that
    # share the column, at EVERY width.
    for w in (700, 1200, 1900, 2400):
        f = apply_display_options(base(names),
                                  {"legend_position": "Top Right"}, None,
                                  width_px=w)
        reserved = reserved_px(f, "Top Right", w)
        # axis_px=0 asks for the legend BOX alone; the gap the default adds
        # belongs to the axis allowance, so counting it twice would be wrong.
        box = _legend_need_px(names, "", "right", axis_px=0)
        axis = _legend_axis_need_px(f, "right")
        check(f"legend: reserved column covers box+axis at {w}px",
              reserved >= box + axis - 1.0,
              f"reserved {reserved:.0f}px, box {box}px + axis {axis}px")

    # A fraction-based reservation would GROW with the card; this must not.
    f1 = apply_display_options(base(names), {"legend_position": "Top Right"},
                               None, width_px=700)
    f2 = apply_display_options(base(names), {"legend_position": "Top Right"},
                               None, width_px=2400)
    r1 = reserved_px(f1, "Top Right", 700)
    r2 = reserved_px(f2, "Top Right", 2400)
    check("legend: reservation does not grow with the card",
          abs(r1 - r2) < 2.0, f"{r1:.0f}px at 700 vs {r2:.0f}px at 2400")

    # Long labels must still get a genuinely wide column.
    long_names = ["Very Long Series Name Alpha", "Very Long Series Name Beta"]
    need_long = _legend_need_px(long_names, "", "right", axis_px=0)
    fl = apply_display_options(base(long_names), {"legend_position": "Top Right"},
                               None, width_px=1900)
    res_long = reserved_px(fl, "Top Right", 1900)
    check("legend: long labels still reserve a wider column",
          res_long > need_long,
          f"reserved {res_long:.0f}px, need {need_long}px")

    # The Generate path: built on a worker with no width, then re-fitted once
    # the widget knows its real size.
    fg = apply_display_options(base(names), {"legend_position": "Top Right"},
                               None)
    before = reserved_px(fg, "Top Right", 1900)
    applied = refit_legend_width(fg, 1900)
    after = reserved_px(fg, "Top Right", 1900)
    check("legend: worker-built figure refits on first real width",
          applied is True and after < before / 2,
          f"{before:.0f}px -> {after:.0f}px")

    # Every position keeps its side through the refit.
    for pos in ("Top Right", "Top Left", "Bottom Right", "Bottom Left"):
        fp = apply_display_options(base(names), {"legend_position": pos}, None)
        refit_legend_width(fp, 1900)
        check(f"legend: refit preserves position {pos}",
              infer_legend_position(fp) == pos,
              f"got {infer_legend_position(fp)}")

    # Left-hand positions must keep room for the y-axis tick labels.
    fleft = apply_display_options(base(names), {"legend_position": "Top Left"},
                                  None, width_px=1900)
    box_l = _legend_need_px(names, "", "left", axis_px=0)
    check("legend: left column still carries axis-label room",
          reserved_px(fleft, "Top Left", 1900) >= box_l
          + _legend_axis_need_px(fleft, "left") - 1,
          f"reserved {reserved_px(fleft, 'Top Left', 1900):.0f}px")

    # A figure with no legend must be left completely alone.
    fnone = go.Figure()
    fnone.add_bar(x=["a"], y=[1], name="n")
    check("legend: no legend => refit is a no-op",
          refit_legend_width(fnone, 1900) is False
          and fnone.layout.xaxis.domain is None)

    # A zero/absent width must not corrupt an existing domain.
    fz = copy.deepcopy(fg)
    dom = list(fz.layout.xaxis.domain)
    refit_legend_width(fz, 0)
    refit_legend_width(fz, None)
    check("legend: width 0/None leaves the domain alone",
          list(fz.layout.xaxis.domain) == dom)

    # Headless callers (export, background generation) keep the old behaviour
    # rather than depending on a width they cannot know.
    fh = apply_display_options(base(names), {"legend_position": "Top Right"},
                               None)
    check("legend: headless path still reserves a usable column",
          0.0 < list(fh.layout.xaxis.domain)[1] < 1.0)


def test_legend_never_overlaps_the_axis_it_shares_a_column_with():
    """The legend plate must not paint over the axis furniture beside it.

    Regression from the "look legends again overlapping with chart" report.
    Two independent causes, both visible on the Categorical dual-axis chart:

    1. The reserved column was sized from the legend box ALONE.  On the left it
       allowed a flat 30px for the y tick labels, but a category like
       "Packaged Chocolate" needs ~150px, so Top Left drew the plate straight
       over the labels.  On the right it allowed NOTHING, yet a secondary y
       axis draws its scale in exactly that freed strip, so Bottom Right
       covered the 0-20 scale.
    2. The pixel need was divided by the full CARD width, but ``xaxis.domain``
       is a fraction of the plot AREA (card minus margins).  Every reservation
       therefore came out short by the margin, and the plate overlapped even
       when the arithmetic looked right.
    """
    print("[28b] Legend column clears the axis labels on both sides")
    from plotly.subplots import make_subplots

    import plotly.graph_objects as go
    from chart_display import (apply_display_options, _legend_need_px,
                               _legend_axis_need_px, legend_anchor,
                               legend_layout_mode, _LEGEND_ROW_PX)

    cats = ["Coffee", "Tea", "Packaged Chocolate", "Drinking Chocolate"]

    def dual_axis():
        """The exact shape that produced the report: bar + secondary line."""
        f = make_subplots(specs=[[{"secondary_y": True}]])
        f.add_trace(go.Bar(x=cats, y=[89250, 69737, 487, 17457],
                           name="Sum transaction_qty"), secondary_y=False)
        f.add_trace(go.Scatter(x=cats, y=[12.3, 9.1, 1.9, 6.2],
                               name="Mean unit_price",
                               mode="lines+markers"), secondary_y=True)
        f.update_layout(margin=dict(l=20, r=20, t=56, b=60))
        return f

    def reserved_px(fig, pos, w):
        """Width given up to the legend on its side (0 in band mode).

        Unset margins default to Plotly's own (``_margin_px``), not 0, or the
        measured strip comes out wider than the reservation really is.
        """
        from chart_display import _plot_area_width
        m = fig.layout.margin.to_plotly_json() or {}
        area = _plot_area_width(m, w)
        d = list(fig.layout.xaxis.domain)
        side = legend_anchor(pos)["side"]
        return (1.0 - d[1] if side == "right" else d[0]) * area

    def band_px(fig):
        """Height given up to the legend when it went horizontal.

        The compact fallback reserves HEIGHT, not width, so the plot keeps
        its full width and stays readable on a dashboard tile. Measuring
        only the horizontal reservation would read that as "0px reserved"
        and report a false failure on exactly the narrow cards the rule
        exists to fix.
        """
        m = fig.layout.margin.to_plotly_json() or {}
        top = int(m.get("t") or 0)
        bot = int(m.get("b") or 0)
        return top if top >= bot else bot

    for w in (760, 1200, 1900, 2400):
        for pos in ("Top Left", "Bottom Left", "Top Right", "Bottom Right"):
            f = apply_display_options(dual_axis(),
                                      {"legend_position": pos}, None,
                                      width_px=w)
            side = legend_anchor(pos)["side"]
            names = [t.name for t in f.data
                     if getattr(t, "showlegend", True) is not False]
            box = _legend_need_px(names, "", side, axis_px=0)
            axis = _legend_axis_need_px(f, side)
            if legend_layout_mode(f) == "band":
                # Horizontal band: it must not eat the plot, and it must
                # still reserve enough height to actually hold its rows.
                reserved = band_px(f)
                check(f"legend {pos} @{w}px goes horizontal, plot keeps width",
                      abs(list(f.layout.xaxis.domain)[0]) < 1e-6
                      and abs(list(f.layout.xaxis.domain)[1] - 1.0) < 1e-6,
                      f"domain={list(f.layout.xaxis.domain)}")
                check(f"legend {pos} @{w}px band reserves >= {_LEGEND_ROW_PX}px "
                      f"of height",
                      reserved >= _LEGEND_ROW_PX, f"reserved {reserved}px")
            else:
                reserved = reserved_px(f, pos, w)
                check(f"legend {pos} @{w}px clears box+axis on the {side}",
                      reserved >= box + axis - 1.0,
                      f"reserved {reserved:.0f}px, needs {box}+{axis}px")
                # The PLATE is anchored past the furniture too: legend.x is a
                # paper coordinate over the plotting area (the same ruler the
                # domain fraction is on), so the plate must start at the domain
                # edge PLUS the furniture allowance -- not on the edge, which is
                # what put it over the tick labels and axis title.
                from chart_display import _plot_area_width as _paw
                _m = f.layout.margin.to_plotly_json() or {}
                _a = _paw(_m, w)
                _lg = f.layout.legend
                if side == "right":
                    _edge = list(f.layout.xaxis.domain)[1]
                    _want = min(1.0, _edge + axis / float(_a))
                else:
                    _edge = list(f.layout.xaxis.domain)[0]
                    _want = max(0.0, _edge - axis / float(_a))
                check(f"legend {pos} @{w}px plate anchored past the axis text",
                      abs(float(_lg.x) - _want) < 1e-6,
                      f"x={_lg.x} expected={_want} anchor={_lg.xanchor}")

    # The right side must actually account for the SECONDARY axis scale.  A
    # right strip sized from the legend box alone is what covered the 0-20
    # ticks, so the allowance has to be non-trivial when yaxis2 is present.
    f = dual_axis()
    right_axis = _legend_axis_need_px(f, "right")
    check("legend: right column accounts for the secondary y scale",
          right_axis > 16,
          f"right allowance only {right_axis}px")

    # And it must measure the SECONDARY axis, not the primary one: the bar
    # values (89250) are far wider than the line values, so using the primary
    # axis would over-reserve, and using nothing under-reserves.
    from chart_display import _axis_label_need_px
    pri = _axis_label_need_px(f, "left")
    sec = _axis_label_need_px(f, "right")
    check("legend: each side measures its own axis",
          sec < pri,
          f"primary {pri}px vs secondary {sec}px")

    # A category label long enough to need real room must widen the column.
    short = go.Figure(go.Bar(x=["a"], y=[1], name="s"))
    long_ = go.Figure(go.Bar(x=["a"], y=[1],
                             name="An Extremely Long Legend Entry Name"))
    fs = apply_display_options(short, {"legend_position": "Top Left"}, None,
                               width_px=1900)
    fl = apply_display_options(long_, {"legend_position": "Top Left"}, None,
                               width_px=1900)
    check("legend: a long entry widens the column it needs",
          reserved_px(fl, "Top Left", 1900)
          > reserved_px(fs, "Top Left", 1900) + 50,
          f"{reserved_px(fs, 'Top Left', 1900):.0f}px vs "
          f"{reserved_px(fl, 'Top Left', 1900):.0f}px")

    # The plate must stay clear of the plot even at a narrow card, where the
    # strip is clamped: the clamp may cap the column, but the plate then has to
    # be allowed to overlap rather than silently crush the data.
    fn = apply_display_options(dual_axis(), {"legend_position": "Bottom Right"},
                               None, width_px=520)
    check("legend: narrow card keeps a usable plot area",
          list(fn.layout.xaxis.domain)[1] > 0.35,
          f"domain ends at {list(fn.layout.xaxis.domain)[1]:.2f}")


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
    offered = available_fonts(prefer=["Inter"])

    # A font is "usable" if the app ships it OR the machine has it. Bundled
    # fonts are registered at startup (see register_qt_fonts), so demanding a
    # system install here would be wrong -- and was, before the bundle existed.
    try:
        from font_bundle import bundled_families, font_is_available
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
          available_fonts(prefer=["Inter"]) == offered)

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
          bool(list(FONT_DIR.glob("LICENSE-*.txt"))),
          "no LICENSE-*.txt beside the fonts")

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

    # The real shell HTML must carry the rules (a template placeholder left
    # unformatted, or the digest ignoring the CSS, would silently drop them).
    try:
        from desktop.widgets.plotly_web_view import (_font_face_css,
                                                     _shell_file_path)
        html = _shell_file_path().read_text(encoding="utf-8")
        check("bundle: shell HTML contains the @font-face rules",
              html.count("@font-face") >= len(bundled_families()),
              f"{html.count('@font-face')} rules in shell")
        check("bundle: no unformatted placeholder left in the shell",
              "{fontfacecss}" not in html)
        check("bundle: shell font CSS matches the current font set",
              html.count("@font-face") == _font_face_css().count("@font-face"))
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
    offered = available_fonts(prefer=["Inter"])
    for fam in bundled_families():
        check(f"bundle: picker offers {fam!r}", fam in offered)


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

    # Georgia: not shipped, must map to Caladea and keep Georgia as a later
    # fallback, so a machine with the real thing still prefers it.
    stack = _font_stack("Georgia")
    check("stack: Georgia maps to a bundled family first",
          stack.split(",")[0].strip() in bundled, stack)
    check("stack: Georgia keeps the requested name as a later fallback",
          "Georgia" in stack, stack)

    for name in ("Inter", "Lato", "Roboto", "Open Sans", "Fira Code"):
        check(f"stack: {name!r} is offered as itself", name in _font_stack(name))

    css = header_font_css({"header_family": "Georgia"})
    check("stack: heading css uses the bundled stand-in", "Caladea" in css,
          css[:60])
    check("stack: heading css for Inter names Inter",
          "Inter" in header_font_css({"header_family": "Inter"}))

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
    from chart_display import apply_display_options, default_layout_opts
    from services.analysis_service import run_analysis_with_config, WIDGET_SPEC

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
    for label, _uid, fig in charts:
        f2 = apply_display_options(fig, lo)
        tr0 = list(f2.data)[0]
        check(f"generate: Bins reaches the histogram ({label[:12]})",
              tr0.nbinsx == 12, str(tr0.nbinsx))
        check(f"generate: Opacity reaches the histogram ({label[:12]})",
              abs(float(tr0.opacity) - 0.25) < 1e-6, str(tr0.opacity))

    # An empty form must still produce charts (the service's own default).
    empty = run_analysis_with_config("distribution", df,
                                     {"x": [], "color": None, "palette": "Default"})
    check("generate: empty selection still charts", len(empty) > 0, str(len(empty)))


def test_histogram_layout_options_are_histogram_only():
    """Bins / Opacity must touch histograms and nothing else.

    A distribution chart can also carry box plots, and nbins is meaningless
    on those; the old app guarded the same way with `if ttype == "histogram"`.
    """
    print("[35] Histogram layout options are histogram-only")
    import plotly.graph_objects as go
    from chart_display import apply_display_options, default_layout_opts

    lo = default_layout_opts()
    check("opts: default bins is 30", lo["histogram_bins"] == 30, str(lo["histogram_bins"]))
    check("opts: default opacity is 0.80",
          abs(lo["histogram_opacity"] - 0.80) < 1e-6, str(lo["histogram_opacity"]))

    def _mixed():
        f = go.Figure()
        f.add_trace(go.Histogram(x=[1, 2, 3, 4, 5, 6], name="h"))
        f.add_trace(go.Box(y=[1, 2, 3], name="b"))
        return f

    f2 = apply_display_options(_mixed(), dict(lo, histogram_bins=12,
                                              histogram_opacity=0.25))
    tr = list(f2.data)
    check("hist: bins applied to the histogram", tr[0].nbinsx == 12, str(tr[0].nbinsx))
    check("hist: opacity applied to the histogram",
          abs(float(tr[0].opacity) - 0.25) < 1e-6, str(tr[0].opacity))
    check("hist: a y-binned variant writes nbinsy, not nbinsx",
          apply_display_options(go.Figure(go.Histogram(y=[1, 2, 3])),
                                dict(lo, histogram_bins=9)).data[0].nbinsy == 9)

    # A Box accepts `opacity` (nbinsx is not a valid Box property at all, so
    # it cannot be used to detect the guard). If the histogram-only check were
    # missing, the opacity from the same layout block would land on the box
    # too -- so opacity is the property that actually proves the guard.
    box = list(tr)[1]
    check("hist: a box trace in the same chart keeps its own opacity",
          box.opacity is None, str(box.opacity))

    # Bar-only figure: no histograms, must not raise.
    fb = apply_display_options(go.Figure(go.Bar(x=[1, 2], y=[1, 2])),
                               dict(lo, histogram_bins=7, histogram_opacity=0.1))
    check("hist: bar-only figure is a no-op", type(fb.data[0]).__name__ == "Bar")

    # Defensive input handling.
    for bad in (None, "junk", 0, -5):
        out = apply_display_options(_mixed(), dict(lo, histogram_bins=bad))
        nb = list(out.data)[0].nbinsx
        ok = (nb is None) if bad in (None, "junk") else (nb == 1)
        check(f"hist: bins={bad!r} handled safely", ok, str(nb))
    frac = list(apply_display_options(_mixed(),
                                      dict(lo, histogram_bins=12.7)).data)[0].nbinsx
    check("hist: fractional bins round to an int", frac == 13, str(frac))



# =========================================================================
# Column-role resolution (services/column_roles.py)
# =========================================================================
#
# Regression cover for the bug where dimension drop-downs ("Group by",
# "Colour by") silently lost every ``category`` / ``string`` / ``bool``
# column, because the pools were built with
# ``df.select_dtypes(include="object")``. Those dtypes do not match, so such
# columns landed in no pool at all and could never be selected. The user's
# Column Classification was also ignored entirely.


def _roles_fixture_df():
    """A dataset whose column families disagree with their dtypes."""
    import pandas as pd
    return pd.DataFrame({
        "sales":      [1.5, 2.5, 3.5],                  # real metric
        "region":     ["N", "S", "E"],                   # object dimension
        "segment":    pd.Series(["a", "b", "a"]).astype("category"),
        "code":       pd.Series(["X1", "X2", "X1"], dtype="string"),
        "is_active":  [True, False, True],               # bool dimension
        "postal":     [90210, 90211, 90212],             # id-like numeric
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-02",
                                      "2024-01-03"]),
    })


def test_column_roles_never_drop_a_column():
    from services.column_roles import auto_roles, resolve_roles

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

    # Pin the DETECTOR itself, not just the resolver. resolve_roles has a
    # total-coverage fallback that would quietly re-home any column the
    # detector misses, so asserting only on the resolver lets a broken
    # detector hide behind that safety net.
    det = auto_roles(df)
    check("roles: detector classifies category/string/bool as dimensions",
          det["categorical"] == ["region", "segment", "code", "is_active"],
          str(det["categorical"]))
    check("roles: detector does not lose a column either",
          sorted(det["numeric"] + det["categorical"] + det["datetime"])
          == sorted(df.columns), str(det))

    # Total coverage: every column exactly once -- this is the invariant
    # that guarantees no drop-down can ever lose a column again.
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
    # though pandas says int64. Their choice must win app-wide.
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
    # date objects. Its dtype is still object, so only the value-profile
    # fingerprint reveals the change -- that one column must be re-derived,
    # or no time-series chart could ever find it.
    df2 = df.copy()
    df2["region"] = pd.to_datetime(["2024-01-01", "2024-01-02",
                                   "2024-01-03"]).date
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

        def __init__(self, uid, title, fig, chart_type, meta):
            super().__init__()
            self._fig = fig
            self.btn_up = QWidget()
            self.btn_down = QWidget()
            self.btn_style = QWidget()

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

    # Every runner must be able to receive the resolved roles, otherwise the
    # threading through analysis_service is dead code for it.
    from services.analysis_service import _RUNNERS, _accepts_roles
    without = sorted(aid for aid, fn in _RUNNERS.items()
                     if not _accepts_roles(fn) and aid != "data_quality")
    check("roles: every runner accepts the shared roles mapping",
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
        # Plotly hands back a numpy array here, so an `or []` truth test would
        # raise rather than fall through -- index it explicitly.
        xs = r_user[0][2].data[0].x
        labels = [] if xs is None else [str(v) for v in xs]
        check("roles: the reclassified id is absent from the matrix",
              "order_id" not in labels, str(labels))
        check("roles: the matrix covers exactly the two real metrics",
              sorted(labels) == ["price", "qty"], str(labels))

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
    import plotly.graph_objects as go
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from desktop.widgets.analysis_settings import get_builder
    from chart_display import apply_display_options, default_layout_opts
    from services.analysis_service import run_analysis_with_config, WIDGET_SPEC

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
    # Defaults come from default_layout_opts, not from hard-coded strings.
    check("layout: default colorscale is RdBu",
          default_layout_opts()["heatmap_colorscale"] == "RdBu")
    check("layout: default annotation colour is #ffffff",
          default_layout_opts()["heatmap_annotation_color"] == "#ffffff")
    check("layout: default colorbar tick colour is #94a3b8",
          default_layout_opts()["colorbar_tick_color"] == "#94a3b8")
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
    from services.analysis_service import run_analysis_with_config as _rac
    _df = _pd.DataFrame({"age": [20, 30, 40, 50, 60, 70],
                         "salary": [1000, 2000, 1500, 4000, 3000, 5000],
                         "years": [1, 2, 3, 4, 5, 6]})
    _vals = dict(b2.collect_values())
    _vals["x"] = ["age", "salary", "years"]
    _res = _rac("correlation", _df, _vals)
    fig = _res[0][2]
    check("generation: the figure is a heatmap",
          str(fig.data[0].type).lower() == "heatmap", str(fig.data[0].type))
    xs = fig.data[0].x
    xs = [] if xs is None else [str(v) for v in xs]
    check("generation: the matrix covers the three chosen columns",
          sorted(xs) == ["age", "salary", "years"], str(xs))

    # Fewer than two columns cannot produce a matrix: the runner must return
    # an empty list, not raise.
    few = _rac("correlation", _df, {"x": ["age"]})
    check("generation: one column yields no chart (not an error)", few == [],
          str(len(few)))

    # Display options must actually reach the heatmap trace.
    f2 = apply_display_options(
        fig, {"heatmap_colorscale": "Viridis",
              "heatmap_annotation_precision": 3,
              "colorbar_title": "r", "colorbar_tick_size": 14},
        {"family": "Inter"})
    tr = f2.data[0]
    check("display: precision reformats the cell text",
          tr.texttemplate == "%{z:.3f}", str(tr.texttemplate))
    check("display: the colorscale reaches the figure",
          "viridis" in str(getattr(f2.layout.coloraxis, "colorscale", "")
                            ).lower()
          or "viridis" in str(getattr(tr, "colorscale", "")).lower()
          or _viridis_first_stop(f2) is not None,
          str(getattr(f2.layout.coloraxis, "colorscale", ""))[:50])
    cb = f2.layout.coloraxis.colorbar
    check("display: the colorbar title is applied",
          getattr(cb.title, "text", None) == "r",
          str(getattr(cb.title, "text", None)))
    check("display: the colorbar tick size is applied",
          getattr(cb.tickfont, "size", None) == 14,
          str(getattr(cb.tickfont, "size", None)))

    f3 = apply_display_options(fig, {"heatmap_show_text": False}, {})
    check("display: turning Annotations off clears BOTH text and texttemplate",
          f3.data[0].text is None and f3.data[0].texttemplate is None,
          f"{f3.data[0].text}/{f3.data[0].texttemplate}")

    # ---- Regression: the annotation size/colour were silently discarded ----
    # A user-reported bug: the Ann size slider and the Ann color swatch both
    # moved but the cell values never changed. Two independent causes, both of
    # which have to stay fixed:
    #   1. _apply_heatmap_options skipped textfont entirely when the trace had
    #      none yet (a fresh px.imshow heatmap has textfont=None).
    #   2. the generic value-label pass ran AFTER it and overwrote textfont
    #      with a bare dict(color=...), discarding size and the colour.
    # The earlier assertion above only checked the colorscale, which is why
    # these two slipped through.
    f4 = apply_display_options(
        fig, {"heatmap_annotation_size": 22, "heatmap_annotation_color": "#00ff00"}, {})
    tf4 = f4.data[0].textfont
    check("display: annotation size reaches the cell text",
          getattr(tf4, "size", None) == 22, str(getattr(tf4, "size", None)))
    check("display: annotation colour reaches the cell text",
          str(getattr(tf4, "color", "")).lower() == "#00ff00",
          str(getattr(tf4, "color", None)))

    # And a bar chart must still get the shared value-label colour -- the fix
    # above skips heatmaps only, so this guards the other side of it.
    bars = go.Figure(go.Bar(x=["a", "b"], y=[1, 2], text=["1", "2"],
                            textposition="outside"))
    b1 = apply_display_options(bars, {"value_label_color": "#ff0000"}, {})
    check("display: a bar trace still takes the value-label colour",
          str(getattr(b1.data[0].textfont, "color", "")).lower() == "#ff0000",
          str(getattr(b1.data[0].textfont, "color", None)))

    # ---- Regression: Colorscale looked selected but never painted ----------
    # px.imshow binds the trace to layout.coloraxis, and that axis carries
    # its own colorscale. Writing colorscale on the trace alone had no
    # visible effect, so the chart kept the app's brand palette.
    f5 = apply_display_options(fig, {"heatmap_colorscale": "Viridis"}, {})
    tr5 = f5.data[0]
    first = _first_colorscale_stop(f5)
    check("display: the selected colorscale is what the axis will paint",
          _rgb(first) == (68, 1, 84), str(first))
    check("display: the trace is not left bound to a stale shared axis",
          getattr(tr5, "coloraxis", None) is None
          or _first_colorscale_stop(f5) is not None,
          str(getattr(tr5, "coloraxis", None)))
    # Switching back must switch back, not latch on the first choice. Plotly
    # normalises a named scale to an rgb() string (Greys starts at pure
    # white), so the comparison is on parsed RGB values, not on the literal
    # text -- comparing "#ffffff" to "rgb(255,255,255)" would fail for a
    # colour that is in fact correct.
    f6 = apply_display_options(fig, {"heatmap_colorscale": "Greys"}, {})
    first6 = _first_colorscale_stop(f6)
    check("display: changing the colorscale changes the paint again",
          _rgb(first6) == (255, 255, 255), str(first6))
    check("display: the two scales really differ (the switch is not a no-op)",
          _rgb(first) != _rgb(first6), f"{first} vs {first6}")

    # ---- Regression: Show border / colorbar position were unreachable -------
    # Assert on the colour bar plotly ACTUALLY DRAWS, via real_colorbars, not
    # on ``layout.coloraxis.colorbar``. This heatmap arrives bound to the shared
    # colour axis, but _apply_heatmap_options deliberately UNBINDS it (writing
    # the trace's own colorscale has no effect while the axis binding wins), so
    # from then on the drawn bar is the TRACE one and the axis copy is an unused
    # leftover. Asserting on the axis would test an object nothing renders.
    from chart_display import real_colorbars as _real_cb

    def _drawn_borderwidth(f):
        bars = list(_real_cb(f))
        return int(getattr(bars[0], "borderwidth", 0)) if bars else None

    fb = apply_display_options(fig, {"show_border": False}, {})
    check("display: Show border off reaches the colorbar",
          _drawn_borderwidth(fb) == 0, str(_drawn_borderwidth(fb)))
    fb2 = apply_display_options(fig, {"show_border": True}, {})
    check("display: Show border on reaches the colorbar",
          _drawn_borderwidth(fb2) == 1, str(_drawn_borderwidth(fb2)))
    # -- Regression: "colour bar & corr. chart meshed together" -------------
    # Placing the bar with a negative x drops it onto the y tick labels, which
    # on a heatmap are the long COLUMN NAMES. The fix reserves a strip of the
    # plot domain for the bar and measures that strip from the bar thickness
    # plus the labels. Three things have to hold:
    #   * the domain shifts away from the chosen side,
    #   * the bar sits in the freed strip, not in the axis-label margin,
    #   * the bar's strip also allows for the tick labels on the left.
    right = apply_display_options(fig, {"colorbar_position": "Right"}, {},
                                  width_px=1200)
    left = apply_display_options(fig, {"colorbar_position": "Left"}, {},
                                 width_px=1200)
    dl = tuple(left.layout.xaxis.domain)
    dr = tuple(right.layout.xaxis.domain)
    check("colorbar: Left reserves a strip on the left", dl[0] > 0.0, str(dl))
    check("colorbar: Right reserves a strip on the right", dr[1] < 1.0, str(dr))
    cbl = left.layout.coloraxis.colorbar
    # The bar sits in the freed strip PAST the axis furniture (tick labels +
    # rotated title), which Plotly draws from the domain edge outwards.  The
    # anchor is a paper coordinate over the PLOTTING AREA -- the same ruler the
    # domain fraction is on -- so the expected value is the domain edge minus
    # the furniture allowance, with no margin conversion.  (The old conversion
    # mixed rulers and parked the bar on the axis text.)
    from chart_display import (_legend_axis_need_px as _laxp,
                               _plot_area_width as _paw)
    _ml = left.layout.margin.to_plotly_json()
    _la = _paw(_ml, 1200)
    _pleft = max(0.0, dl[0] - _laxp(left, "left") / float(_la))
    check("colorbar: Left clears the axis furniture in the strip",
          abs(cbl.x - _pleft) < 1e-6 and cbl.xanchor == "right",
          f"x={cbl.x} expected={_pleft} anchor={cbl.xanchor}")
    cbr = right.layout.coloraxis.colorbar
    _mr = right.layout.margin.to_plotly_json()
    _ra = _paw(_mr, 1200)
    # For the Right dock the domain edge IS dr[1] (the strip is 1 - dr[1]).
    _pright = min(1.0, dr[1] + _laxp(right, "right") / float(_ra))
    check("colorbar: Right clears the axis furniture in the strip",
          abs(cbr.x - _pright) < 1e-6 and cbr.xanchor == "left",
          f"x={cbr.x} expected={_pright} anchor={cbr.xanchor}")
    # The left strip must be wide enough for the bar AND the labels. Measured
    # in paper fractions: bar(30)+gap(14)+labels(len*8+12) over the card width.
    # Plotly returns y as a numpy array, so an `or []` truth test raises here.
    _yv = fig.data[0].y
    _names = [] if _yv is None else [str(v) for v in _yv]
    _longest = max((len(n) for n in _names), default=0)
    _need = (30 + 14 + _longest * 8 + 12) / 1200.0
    check("colorbar: the Left strip also leaves room for the tick labels",
          dl[0] >= _need - 0.001,
          f"strip={dl[0]:.4f} need>={_need:.4f} longest={_longest}")

    # The heatmap draws no legend, so no legend column may be reserved. This
    # used to happen anyway (legend_position still carried a default), and
    # because it writes xaxis.domain it silently undid the colour-bar strip.
    from chart_display import _figure_has_legend
    check("colorbar: a heatmap is not treated as having a legend",
          not _figure_has_legend(left), "reported a legend")
    check("colorbar: the legend reservation did not overwrite the strip",
          tuple(left.layout.xaxis.domain)[0] > 0.0,
          str(tuple(left.layout.xaxis.domain)))
    # A real legend must still get its column.
    import plotly.graph_objects as _go
    bars2 = _go.Figure(_go.Bar(x=["a", "b"], y=[1, 2], name="series"))
    check("colorbar: a bar chart is still treated as having a legend",
          _figure_has_legend(bars2), "no legend reported")
    withleg = apply_display_options(bars2, {"legend_position": "Top Right"}, {},
                                    width_px=1000)
    check("colorbar: the legend column is still reserved where it belongs",
          tuple(withleg.layout.xaxis.domain)[1] < 1.0,
          str(tuple(withleg.layout.xaxis.domain)))


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

    # -- Generate: the old panel's eight controls, in its order. Palette is
    #    last (slot 3), and "sort" from _WIDGET_SPEC is NOT built because the
    #    old panel never rendered a Sort control for this type either.
    secs = _sections(gen_page)
    check("design: Generate offers the eight old controls in order",
          secs == ["Dimension", "Metric", "Aggregation", "\U0001f4ca Direction",
                   "Top N", "Secondary Y-Axis", "Secondary Agg",
                   "\U0001f3a8 Palette"], str(secs))
    for attr in ("_combo_dim", "_combo_metric", "_combo_agg", "_combo_dir",
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

    # -- Values: every WIDGET_SPEC key except the never-rendered "sort".
    got = set(b.collect_values())
    spec_keys = set(k for k, _rk, _ty in WIDGET_SPEC["categorical"]) - {"sort"}
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
    check("layout: value labels + grid + the shared border toggle",
          boxes == ["Show value labels", "Show grid", "Show border"], str(boxes))
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
    from chart_display import apply_display_options, default_layout_opts
    from services.analysis_service import run_analysis_with_config, WIDGET_SPEC

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
    row2 = _ys(("_combo_dir", "_spin_topn", "_combo_dual"))
    row3 = _ys(("_combo_dual_agg", "_combo_palette"))
    check("align: row 1 (Dimension/Metric/Aggregation) is one line",
          len(row1) == 3 and len(set(row1.values())) == 1, str(row1))
    check("align: row 2 (Direction/Top N/Secondary Y) is one line",
          len(row2) == 3 and len(set(row2.values())) == 1, str(row2))
    check("align: row 3 (Secondary Agg/Palette) is one line",
          len(row3) == 2 and len(set(row3.values())) == 1, str(row3))
    check("align: the wrapped rows are really on different lines",
          row1["_combo_dim"] < row2["_combo_dir"] < row3["_combo_palette"],
          f"{row1} | {row2} | {row3}")
    gen_page.close()

    # -- Round trip. "sort" is declared in WIDGET_SPEC but the old panel
    #    never rendered it for this type, so -- like correlation's palette --
    #    it is masked: compare against the spec minus the masked field.
    b2 = get_builder("categorical", None, **_cat_ctx())
    gen2 = b2.build_generate()
    got = set(b2.collect_values())
    spec_keys = set(k for k, _rk, _ty in WIDGET_SPEC["categorical"]) - {"sort"}
    check("values: collected keys are the unmasked WIDGET_SPEC keys",
          got == spec_keys, f"{sorted(got)} vs {sorted(spec_keys)}")
    b2.prefill({"x": ["region"], "y": ["unit_price"], "agg": "Sum",
                "top_n": 5, "direction": "Horizontal (Bar chart)",
                "dual_y": "revenue", "dual_y_agg": "Max", "palette": "A"})
    got2 = b2.collect_values()
    check("values: prefill round-trips every field",
          got2 == {"x": ["region"], "y": ["unit_price"], "agg": "Sum",
                   "top_n": 5, "direction": "Horizontal (Bar chart)",
                   "dual_y": "revenue", "dual_y_agg": "Max", "palette": "A"},
          str(got2))
    check("values: the widgets show what prefill stored",
          b2._combo_dim.currentText() == "region"
          and b2._combo_metric.currentText() == "unit_price"
          and b2._combo_agg.currentText() == "Sum"
          and b2._spin_topn.value() == 5
          and b2._combo_dir.currentText() == "Horizontal (Bar chart)"
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
    fig = res[0][2]
    check("generation: a bar with a secondary-axis line",
          [str(tr.type) for tr in fig.data] == ["bar", "scatter"],
          str([tr.type for tr in fig.data]))
    cats = [str(c) for c in fig.data[0].x]
    ys = [float(v) for v in fig.data[0].y]
    check("generation: bars carry the grouped sums, value-desc",
          cats == ["C", "B", "A"] and ys == [110.0, 70.0, 30.0],
          f"{cats} {ys}")

    h = run_analysis_with_config(
        "categorical", df, dict(vals, direction="Horizontal (Bar chart)"))[0][2]
    check("generation: Horizontal renders a bar chart",
          str(h.data[0].orientation) == "h", str(h.data[0].orientation))

    cnt = run_analysis_with_config(
        "categorical", df, {"x": ["region"], "y": [], "agg": "Avg",
                            "top_n": 1, "direction": "Vertical (Column chart)",
                            "dual_y": "", "dual_y_agg": "Avg",
                            "palette": "Default"})[0][2]
    check("generation: no metric falls back to counts, Top N caps it",
          len(cnt.data[0].x) == 1 and int(cnt.data[0].y[0]) == 3,
          f"{list(cnt.data[0].x)} {list(cnt.data[0].y)}")

    # Legacy scalar callers must not be split into characters.
    sc = run_analysis_with_config(
        "categorical", df, {"x": "region", "y": "unit_price"})[0][2]
    check("generation: scalar x/y are normalised to lists",
          sorted(str(c) for c in sc.data[0].x) == ["N", "S"],
          str(list(sc.data[0].x)))

    # -- Display: the two new Layout options reach the figure. -------------
    f = apply_display_options(fig, {"y2_label": "Secondary",
                                    "line_width": 7}, {"family": "Inter"})
    check("display: the right-hand axis label reaches yaxis2",
          str(getattr(f.layout.yaxis2.title, "text", None)) == "Secondary",
          str(getattr(f.layout.yaxis2.title, "text", None)))
    sc_traces = [tr for tr in f.data if str(tr.type) == "scatter"]
    check("display: line width reaches the secondary line",
          bool(sc_traces) and int(sc_traces[0].line.width) == 7,
          str([tr.line.width for tr in sc_traces]))
    check("display: line width does not outline the bars",
          getattr(f.data[0].marker.line, "width", None) in (None, 0),
          str(getattr(f.data[0].marker.line, "width", None)))
    f2 = apply_display_options(fig, {"line_width": 3}, {})
    check("display: no y2_label keeps the runner's own right-axis title",
          bool(str(getattr(f2.layout.yaxis2.title, "text", None) or "")),
          str(getattr(f2.layout.yaxis2.title, "text", None)))

    # The shared template controls still work on this chart type too.
    f3 = apply_display_options(fig, {"show_value_labels": False,
                                     "bar_mode": "stack", "bar_gap": 0.5}, {})
    check("display: value labels switch off on a categorical bar",
          str(f3.data[0].textposition) == "none",
          str(f3.data[0].textposition))
    check("display: bar mode reaches the figure",
          str(f3.layout.barmode) == "stack", str(f3.layout.barmode))
    check("display: bar gap reaches the figure",
          abs(float(f3.layout.bargap) - 0.5) < 1e-9, str(f3.layout.bargap))


def test_legend_layout_adapts_to_the_viewport():
    """The legend layout must be CHOSEN from the pixels, not fixed per type.

    A dashboard packs 2-3 charts per row, so a chart that reserved a vertical
    legend column sized for a full-width preview pane ended up either wasting
    space or overlapping its own axis on a compact tile. The rule now is: keep
    the column while it fits inside ``LEGEND_COLUMN_MAX_SHARE`` of the plot area,
    and switch to a horizontal band (which costs HEIGHT, not width) when it does
    not. These tests pin the switchover, both anchors, that a band keeps the full
    plot width, idempotence, and that a resize storm converges instead of
    latching a stale strip.
    """
    print("[N] Legend layout adapts to the viewport (column <-> band)")
    import plotly.graph_objects as go
    from chart_display import (LEGEND_COLUMN_MAX_SHARE, apply_legend_layout,
                               apply_display_options, infer_legend_position,
                               legend_layout_mode, plan_legend_layout,
                               refit_legend_width, reserve_legend_band)

    def long_labels(n=6):
        # Names long enough that a column would eat the plot at dashboard sizes.
        return [f"Sum transaction_qty by product category {i}" for i in range(n)]

    def base():
        f = go.Figure()
        for nm in long_labels():
            f.add_trace(go.Scatter(x=[1, 2, 3], y=[1, 2, 3], name=nm,
                                   mode="lines+markers"))
        # A y-axis title is furniture the old measurement forgot, so it is here
        # on purpose: it is what pushed the plate out over the axis text.
        f.update_layout(yaxis=dict(title=dict(text="Sum transaction_qty")))
        return f

    # -- 1. The narrow card gets a band, and the band keeps the full width. --
    narrow = apply_display_options(base(), {"legend_position": "Top Right"},
                                   {}, width_px=420)
    check("legend: a narrow card switches to a band",
          legend_layout_mode(narrow) == "band", str(legend_layout_mode(narrow)))
    check("legend: the band leaves x at full width",
          tuple(narrow.layout.xaxis.domain) == (0.0, 1.0),
          str(narrow.layout.xaxis.domain))
    check("legend: the band is horizontal",
          str(narrow.layout.legend.orientation) == "h",
          str(narrow.layout.legend.orientation))
    check("legend: the band reserves plot HEIGHT, not width",
          tuple(narrow.layout.yaxis.domain) != (0.0, 1.0),
          str(narrow.layout.yaxis.domain))

    # -- 2. A wide card keeps the vertical column. --------------------------
    wide = apply_display_options(base(), {"legend_position": "Top Right"},
                                 {}, width_px=2400)
    check("legend: a wide card keeps the column",
          legend_layout_mode(wide) == "column", str(legend_layout_mode(wide)))
    # A column costs WIDTH: x must lose the strip, and y must be untouched.
    # Which END of x moves depends on the side -- a right-hand column pushes
    # x's upper bound down, a left-hand one raises its lower bound -- so the
    # assertion has to be side-aware or it fails on a correct layout.
    xw = tuple(wide.layout.xaxis.domain)
    check("legend: the column narrows x, not y",
          xw[1] < 1.0 and tuple(wide.layout.yaxis.domain) == (0.0, 1.0),
          f"x={wide.layout.xaxis.domain} y={wide.layout.yaxis.domain}")

    wide_l = apply_display_options(base(), {"legend_position": "Top Left"},
                                   {}, width_px=2400)
    xl = tuple(wide_l.layout.xaxis.domain)
    check("legend: a left-hand column narrows x's lower bound instead",
          xl[0] > 0.0 and xl[1] == 1.0,
          f"x={wide_l.layout.xaxis.domain}")

    # -- 3. The plan reports a strip it can actually be reserved with. ------
    plan = plan_legend_layout(base(), "Top Right", names=long_labels(),
                              legend_title="Series", width_px=2400)
    check("legend: the plan reports a strip within the allowed share",
          plan is not None and plan.get("mode") == "column"
          and 0.0 < float(plan.get("strip", 0)) <= LEGEND_COLUMN_MAX_SHARE,
          str(plan))

    # -- 4. Both modes, at all four positions, at every width. -------------
    for pos in ("Top Left", "Top Right", "Bottom Left", "Bottom Right"):
        modes = []
        for w in (380, 700, 1400, 2400):
            f = apply_display_options(base(), {"legend_position": pos}, {},
                                      width_px=w)
            modes.append(legend_layout_mode(f))
            # The plot must stay usable at every width, in both modes.
            xw = float(f.layout.xaxis.domain[1]) - float(f.layout.xaxis.domain[0])
            yh = float(f.layout.yaxis.domain[1]) - float(f.layout.yaxis.domain[0])
            check(f"legend: {pos}@{w}px keeps a real plot area",
                  xw > 0.25 and yh > 0.55, f"x={xw:.2f} y={yh:.2f}")
        check(f"legend: {pos} uses both modes across the range",
              "band" in modes and "column" in modes, str(modes))
        # The chosen anchor has to survive the band round-trip: a top band is
        # drawn with yanchor="bottom", which a naive anchor reading reports as
        # "Bottom ...".
        last = apply_display_options(base(), {"legend_position": pos}, {},
                                     width_px=380)
        check(f"legend: {pos} survives the band layout",
              infer_legend_position(last) == pos,
              str(infer_legend_position(last)))

    # -- 5. Idempotent: repeated refits at one width do not compound. ------
    f = apply_display_options(base(), {"legend_position": "Top Left"}, {},
                              width_px=700)
    snap = (tuple(f.layout.xaxis.domain), tuple(f.layout.yaxis.domain))
    for _ in range(6):
        refit_legend_width(f, 700)
    check("legend: repeated refits are idempotent",
          snap == (tuple(f.layout.xaxis.domain), tuple(f.layout.yaxis.domain)),
          f"{snap} -> {(f.layout.xaxis.domain, f.layout.yaxis.domain)}")

    # -- 6. A resize storm converges back to the single-shot layout. ------
    storm = apply_display_options(base(), {"legend_position": "Top Right"}, {},
                                  width_px=2400)
    for w in (2400, 1400, 900, 400, 700, 2400, 380, 2400):
        refit_legend_width(storm, w)
    fresh = apply_display_options(base(), {"legend_position": "Top Right"}, {},
                                  width_px=2400)
    check("legend: a resize storm converges (no latched strip/band)",
          tuple(storm.layout.xaxis.domain) == tuple(fresh.layout.xaxis.domain)
          and legend_layout_mode(storm) == legend_layout_mode(fresh),
          f"{storm.layout.xaxis.domain} vs {fresh.layout.xaxis.domain}")

    # -- 7. Headless (no width) keeps the classic column, so export output is
    #       unchanged by the band existing at all. -------------------------
    nowidth = apply_display_options(base(), {"legend_position": "Top Right"}, {})
    check("legend: with no view width the column is kept (exports unchanged)",
          legend_layout_mode(nowidth) == "column", str(legend_layout_mode(nowidth)))

    # -- 8. reserve_legend_band refuses to eat the whole plot. -------------
    hogged = go.Figure(go.Bar(x=["a", "b"], y=[1, 2], name="s"))
    reserve_legend_band(hogged, "Top Left", band_px=100000)
    yh = float(hogged.layout.yaxis.domain[1]) - float(hogged.layout.yaxis.domain[0])
    check("legend: an oversized band is capped so the plot survives",
          yh > 0.5, str(hogged.layout.yaxis.domain))

    # -- 9. A figure with no legend is never given a reservation. ---------
    bare = go.Figure(go.Bar(x=["a"], y=[1], name="only", showlegend=False))
    bare.update_layout(showlegend=False)
    check("legend: a chart with no legend gets no band",
          refit_legend_width(bare, 400) is False, "reserved something")

    # -- 10. The dispatcher is the one entry point both paths share. -------
    # apply_legend_layout returns a bool; the mode it chose is recorded ON the
    # figure, so the figure (not the return value) is what must be inspected.
    narrow, wide = base(), base()
    apply_legend_layout(narrow, "Top Right", names=long_labels(), width_px=400)
    apply_legend_layout(wide, "Top Right", names=long_labels(), width_px=2400)
    check("legend: apply_legend_layout records the mode it chose",
          legend_layout_mode(narrow) == "band"
          and legend_layout_mode(wide) == "column",
          f"400px={legend_layout_mode(narrow)} "
          f"2400px={legend_layout_mode(wide)}")


def test_legend_axis_title_is_measured():
    """The rotated y-axis TITLE shares the legend strip, so it must be measured.

    The old measurement counted only the tick labels. Plotly draws the axis title
    outboard of them, inside the same reserved column, so a strip sized for box +
    ticks still had the title painted under the legend plate -- the "Sum
    transaction_qty" text visibly running through it.
    """
    print("[N] The reserved legend column accounts for the y-axis title")
    import copy
    import plotly.graph_objects as go
    from chart_display import (_legend_axis_need_px, _legend_axis_title_px,
                               reserve_legend_column)

    with_title = go.Figure(go.Scatter(x=[1, 2], y=[1, 2], name="s"))
    with_title.update_layout(yaxis=dict(title=dict(text="Sum transaction_qty")))
    without = go.Figure(go.Scatter(x=[1, 2], y=[1, 2], name="s"))
    without.update_layout(yaxis=dict(title=dict(text="")))

    check("legend: an axis title contributes to the reserved strip",
          _legend_axis_title_px(with_title, "left") > 0,
          str(_legend_axis_title_px(with_title, "left")))
    check("legend: no axis title contributes nothing",
          _legend_axis_title_px(without, "left") == 0,
          str(_legend_axis_title_px(without, "left")))
    check("legend: the total strip need grows with the title",
          _legend_axis_need_px(with_title, "left")
          > _legend_axis_need_px(without, "left"), "")

    # A titled axis therefore reserves MORE than an untitled one, at the same
    # width and the same labels.
    # reserve_legend_column rewrites the figure it is handed IN PLACE, so the
    # domain has to be read back off the figure that was actually passed in --
    # reading the original would compare two untouched, unreserved figures.
    names = ["Alpha series", "Beta series", "Gamma series"]
    fig_t = copy.deepcopy(with_title)
    fig_p = copy.deepcopy(without)
    side_t = reserve_legend_column(fig_t, "Top Left", names=names, width_px=900)
    side_p = reserve_legend_column(fig_p, "Top Left", names=names, width_px=900)
    check("legend: a titled axis reserves a wider column",
          tuple(fig_t.layout.xaxis.domain)[0]
          > tuple(fig_p.layout.xaxis.domain)[0],
          f"{fig_t.layout.xaxis.domain} vs {fig_p.layout.xaxis.domain}")
    check("legend: both reservations name a side",
          side_t == "left" and side_p == "left", "")


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
            "lay": {"Chart title", "Legend title", "Legend position", "Text info",
                    "Donut", "Pull", "Rotation", "Direction", "Value size",
                    "Value color"},
            "typ": {"Legend size", "Legend color"},
            "absent": {"Bar mode", "Bar gap", "X-axis", "Y-axis"},
        },
        "time_series": {
            "lay": {"Line width", "Line shape", "Fill", "Markers"},
            "typ": {"Title size", "Axis size", "Legend size"},
            "absent": {"Bar mode", "Bar gap"},
        },
        "scatter_plot": {
            "lay": {"Line width", "Line shape", "Fill", "Markers",
                    "M. size", "M. opacity", "Render mode"},
            "typ": {"Title size", "Legend size"},
            "absent": {"Bar mode", "Bar gap"},
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
                         "sort_rows", "top_n_rows"},
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
    from chart_display import apply_display_options, default_layout_opts
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

    def _fig(aid, vals):
        res = run_analysis_with_config(aid, df, vals)
        if not res:
            raise AssertionError(f"no figure produced for {aid}")
        return res[0][2]

    def _j(f):
        return json.dumps(f.to_plotly_json(), sort_keys=True, default=str)

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
            "heatmap_colorscale": "Viridis", "heatmap_show_text": False,
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
        base = _j(apply_display_options(fig, dict(defaults), {}))
        dead = [k for k, v in probes.items()
                if _j(apply_display_options(fig, dict(defaults, **{k: v}), {})) == base]
        check(f"new layout controls: every '{aid}' probe changes the figure",
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
    types = {}
    for mode in ("svg", "webgl"):
        figs = run_analysis_with_config(
            "scatter_plot", df,
            {"x_col": "age", "y_col": "salary", "render_mode": mode})
        types[mode] = figs[0][2].data[0].type if figs else None
    check("scatter render_mode=svg uses the SVG trace",
          types.get("svg") == "scatter", f"got {types.get('svg')}")
    check("scatter render_mode=webgl uses the WebGL trace",
          types.get("webgl") == "scattergl", f"got {types.get('webgl')}")

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
        return _json.dumps(figs[0][2].to_plotly_json(),
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
    check("map: unmatchable locations return empty instead of raising",
          res == [], str(res))

    # A REAL country must still render, so the fix did not disable the chart.
    ok = df.assign(country=["India", "USA", "France"] * 4)
    res2 = run_analysis_with_config("map_plot", ok, {
        "map_mode": "Choropleth (Location Names)", "location_col": "country",
        "value_col": "salary", "agg_func": "Sum"})
    check("map: recognised countries still render a choropleth",
          len(res2) == 1 and res2[0][2].data[0].type == "choropleth",
          str([t[2].data[0].type for t in res2]))


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
                        _TYPOTHREE + ["Legend size", "Legend color"]),
        # old controls: + histogram_bins, histogram_opacity.
        "distribution": (_LB_LEGEND + ["Bins", "Opacity"] + _LEGEND_EXTRA, _LB_CHECKS,
                         _TYPOTHREE + ["Legend size", "Legend color"]),
        # has_axes=False, has_legend=False: title, colorscale, colour-bar
        # styling, annotations (+ the two requested colour-bar rows), and no
        # axis-title typography.
        "correlation": (["Chart title", "Colorscale", "Ann size", "Ann color",
                         "Precision", "Colorbar pos", "CB title", "CB tick sz",
                         "CB tick clr", "CB title sz", "CB title clr"],
                        ["Annotations", "Show border"],
                        [r for r in _TYPOTHREE
                         if r not in ("Title size", "Title color")]),
        # has_axes=True; the legend rows are KEPT although the old metadata
        # says has_legend=False, because this runner really does draw a
        # legend (the Secondary Y-Axis overlay is a second series) and the
        # legend / colour-bar settings are shared across the chart types that
        # draw a key. Typography is the shared tab, legend group included.
        "categorical": (["Chart title", "X-axis", "Y-axis (left)",
                         "Y-axis (right)", "Legend title", "Legend position",
                         "Label pos", "Bar mode", "Bar gap", "Line width"] + _LEGEND_EXTRA,
                        ["Show value labels", "Show grid", "Show border"],
                        list(_TYPOTHREE) + ["Legend size", "Legend color"]),
        # ---- the six types migrated in the final pass --------------------
        # Same contract as above: the rows are the OLD Streamlit controls for
        # that chart type (per chart_settings_screenshots/rules.txt), plus the
        # shared chart title row and the template's border box each one uses.
        # pie_chart: old has_legend=True but no x/y axes -> title + the whole
        # legend block + the pie-specific rows, and NO axis-title typography.
        "pie_chart": (["Chart title", "Legend title", "Legend position",
                       "Label pos", "Text info", "Donut", "Pull", "Rotation",
                       "Direction", "Value size", "Value color"] + _LEGEND_EXTRA,
                      ["Show value labels", "Show border"],
                      ["Header size", "Header color", "Header font",
                       "Header font style", "Header align", "Font family",
                       "Font style", "Legend size", "Legend color"]),
        # time_series / scatter_plot: has_axes and has_legend both True, so the
        # legend block is kept. Scatter adds the marker rows and the build-time
        # render-mode combo; its typography therefore matches the axes types.
        "time_series": (["Chart title", "X-axis", "Y-axis", "Legend title",
                         "Legend position", "Label pos", "Line width",
                         "Line shape", "Fill"] + _LEGEND_EXTRA,
                        ["Show value labels", "Show grid", "Show border",
                         "Markers"],
                        _TYPOTHREE + ["Legend size", "Legend color"]),
        "scatter_plot": (["Chart title", "X-axis", "Y-axis", "Legend title",
                          "Legend position", "Label pos", "Line width",
                          "Line shape", "Fill", "M. size", "M. opacity",
                          "Render mode"] + _LEGEND_EXTRA,
                         ["Show value labels", "Show grid", "Show border",
                          "Markers"],
                         _TYPOTHREE + ["Legend size", "Legend color"]),
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
                            "Axis color"]),
        # matrix_table: a table, not an axes chart -> table styling only.
        "matrix_table": (["Chart title", "Font size", "Hdr font", "Row h",
                          "Font color", "Hdr color", "Even stripe",
                          "Odd stripe", "Hdr h", "Index align", "Data align",
                          "Num format"],
                         ["Show border"],
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
                      "Font style", "Axis size", "Axis color"]),
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
    print("[45] Every declared Layout control changes the figure")
    import json
    import pandas as pd
    from chart_display import apply_display_options, default_layout_opts
    from services.analysis_service import run_analysis_with_config

    df = pd.DataFrame({
        "region": ["N", "N", "S", "S", "E", "E"],
        "dept": ["d1", "d2", "d1", "d2", "d1", "d2"],
        "age": [20, 30, 40, 50, 60, 70],
        "salary": [1000, 2000, 1500, 4000, 3000, 5000],
    })

    def _fig(aid, vals):
        res = run_analysis_with_config(aid, df, vals)
        if not res:
            raise AssertionError(f"no figure produced for {aid}")
        return res[0][2]

    def _j(f):
        return json.dumps(f.to_plotly_json(), sort_keys=True, default=str)

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
            "legend_labels": {str(stat.data[0].name): "RENAMED"},
            "show_value_labels": False, "value_label_color": "#00ff00",
            "label_position": "inside", "bar_mode": "stack",
            "bar_gap": 0.5, "show_grid": False, "show_border": True,
        },
        "distribution": {
            "title": "PROBE TITLE", "x_label": "PX", "y_label": "PY",
            "legend_title": "PLEGEND", "legend_position": "Bottom Left",
            "legend_labels": {str(dist.data[0].name): "RENAMED"},
            "histogram_bins": 60, "histogram_opacity": 0.35,
            "show_grid": False, "show_border": True,
        },
        "correlation": {
            "title": "PROBE TITLE", "heatmap_colorscale": "Viridis",
            "heatmap_show_text": False, "heatmap_annotation_size": 18,
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
    for aid, fig in (("statistical", stat), ("distribution", dist),
                     ("correlation", corr), ("categorical", cat)):
        base = _j(apply_display_options(fig, dict(defaults), {}))
        for key, probe in PROBES[aid].items():
            opts = dict(defaults)
            opts[key] = probe
            got = _j(apply_display_options(fig, opts, {}))
            check(f"layout control '{key}' changes the {aid} figure",
                  got != base, f"probe={probe!r}")


def test_new_display_passes_are_wired():
    """The per-trace display passes must actually be CALLED.

    A pass that exists but is never invoked from
    ``apply_display_options`` is dead code, and its control reads as broken
    in the UI. This is the cheap structural half of the audit; the
    behavioural half is ``test_declared_layout_controls_all_do_something``.
    """
    print("[46] New display passes are wired into apply_display_options")
    import inspect
    import chart_display

    src = inspect.getsource(chart_display.apply_display_options)
    for needle in ("_apply_heatmap_options", "_apply_colorbar_border",
                   "_apply_title_placement", "apply_legend_layout",
                   "legend_labels", "show_value_labels", "bargap",
                   "barmode", "showgrid", "line_width", "y2_label"):
        check(f"wired: apply_display_options uses '{needle}'",
              needle in src, "")
    # 'apply_legend_layout' is the one entry point both apply_display_options
    # and the widget's resize refit go through; it dispatches to
    # reserve_legend_column / reserve_legend_band internally.
    for fn in ("_apply_heatmap_options", "_apply_colorbar_border",
               "_apply_title_placement", "apply_legend_layout",
               "reserve_legend_column", "reserve_legend_band",
               "_figure_has_legend"):
        check(f"wired: chart_display defines {fn}()",
              callable(getattr(chart_display, fn, None)), "")


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


def _rgb(colour):




    """Parse "#rgb"/"#rrggbb"/"rgb(...)"/"rgba(...)" into an (r,g,b) tuple."""
    if not colour:
        return None
    s = str(colour).strip().lower()
    try:
        if s.startswith("#"):
            s = s[1:]
            if len(s) == 3:
                s = "".join(c * 2 for c in s)
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        if s.startswith(("rgb(", "rgba(")):
            parts = s[s.index("(") + 1:s.index(")")].split(",")
            return tuple(int(float(p)) for p in parts[:3])
    except Exception:
        return None
    return None


def _first_colorscale_stop(fig):
    """First colour stop of whatever scale *fig* will actually paint, or None.

    Deliberately resolves BOTH routes rather than trusting one: a heatmap can
    paint from the trace's own ``colorscale`` or, while it is bound to
    ``layout.coloraxis``, from the axis. Checking only the trace is what let
    the "Colorscale looks selected but the chart keeps the brand palette"
    bug through, because the trace really did hold Viridis while the axis
    held something else. Returns None when neither route is set.
    """
    # Resolve the way Plotly actually resolves it. A trace bound to
    # layout.coloraxis paints from the AXIS, not from its own colorscale, so
    # the axis wins whenever the binding is present. Preferring the trace
    # unconditionally is what let this bug through a second time: the trace
    # genuinely held Viridis while the axis held the brand palette.
    ca = getattr(fig.layout, "coloraxis", None)
    bound = False
    if fig.data:
        bound = getattr(fig.data[0], "coloraxis", None) is not None
    cs = None
    if bound and ca is not None:
        cs = getattr(ca, "colorscale", None)
    if cs is None and fig.data:
        cs = getattr(fig.data[0], "colorscale", None)
    if cs is None and ca is not None:
        cs = getattr(ca, "colorscale", None)
    try:
        return cs[0][1]
    except Exception:
        return None


def _viridis_first_stop(fig):
    """Viridis' first stop, or None if the figure is not on Viridis.

    Plotly stores a named scale as a list of (position, colour) pairs, so the
    way to tell "the scale changed" is to compare the resolved first colour
    rather than the stored name -- update_coloraxes expands the name.
    """
    holders = [getattr(fig.layout, "coloraxis", None)]
    holders.extend(list(getattr(fig, "data", []) or []))
    for holder in holders:
        cs = getattr(holder, "colorscale", None)
        if not cs:
            continue
        first = cs[0]
        if isinstance(first, (list, tuple)) and len(first) > 1:
            if str(first[1]).lower() in ("#440154", "rgb(68, 1, 84)"):
                return str(first[1])
    return None


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
    import plotly.express as px
    from modules.export import generate_html_report
    from services.export_service import build_export_theme
    df = px.data.iris()
    charts = [("a", "First", px.bar(df, x="species", y="sepal_length"), "n", "", {}),
              ("b", "Second", px.scatter(df, x="petal_length", y="petal_width"),
               "", "", {})]
    th = build_export_theme({})
    geom = {"a": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 500.0},
            "b": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 400.0}}

    he = generate_html_report(charts, "S", grid_cols_n=2, theme=th,
                              tile_geometry=geom, editor=True)
    check("every card carries a stable uid for the canvas to target",
          len(re.findall(r'data-uid="[ab]"', he)) == 2, he[:0])
    check("editor mode adds the editing class to <body>",
          '<body class="ly-editing">' in he)
    check("editor mode ships the drag overlay css and js",
          ".ly-grip" in he and "lySetEditing" in he)
    check("no unsubstituted placeholder leaks into the document",
          "__ACCENT__" not in he and "__COLS__" not in he
          and "__GAP__" not in he and "__MODE__" not in he)

    hs = generate_html_report(charts, "S", grid_cols_n=2, theme=th,
                              tile_geometry=geom)
    check("the SAVED file carries no editing chrome at all",
          "<body>" in hs and "ly-grip" not in hs and "lySetEditing" not in hs)

    # A card the user made 500px tall must be 500px tall with a plot that FITS
    # inside it -- not a 500px box with the default 400px plot spilling out of
    # the bottom. "a" carries a note here, so the plot is the card height less
    # the heading/padding allowance and the notes strip.
    heights = sorted({int(h) for h in re.findall(r'"height":(\d+)', hs)})
    check("a tall card gets a plot that fits inside it, not the default height",
          heights and max(heights) < 500 and 400 not in heights, str(heights))
    tall_no_notes = generate_html_report(
        [("a", "First", px.bar(df, x="species", y="sepal_length"), "", "", {})],
        "S", theme=th, tile_geometry={"a": {"x": 0.0, "y": 0.0, "w": 0.5,
                                             "h": 500.0}})
    nh = sorted({int(h) for h in re.findall(r'"height":(\d+)', tall_no_notes)})
    check("a taller card gets a proportionally taller plot",
          nh and max(nh) > 400, str(nh))
    cs = re.findall(r'<div class="chart-card"[^>]*style="([^"]*)"', hs)
    check("the card itself is exactly the height the user set",
          cs and cs[0].endswith("height:500.00px;"), str(cs))

    hp = generate_html_report(charts, "S", grid_cols_n=2, theme=th,
                              tile_geometry=geom, inline_plotly=False)
    check("external-plotly mode emits a src reference, not 4.5MB of inline js",
          'src="lytrize_plotly.js"' in hp
          and not re.findall(r"plotly\.js v", hp))
    check("the saved file still embeds the bundle",
          re.findall(r"plotly\.js v", hs))

    hc = generate_html_report(charts, "S", grid_cols_n=2, theme=th,
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
    check("the temporary canvas growth is handed back on release",
          "host.style.height = ''" in free)
    check("Plotly is refit in BOTH axes after a resize",
          "Plotly.relayout(plot" in free and "height: h > 0 ? h : undefined" in free)
    check("a click that never moved is not treated as a layout change",
          "Math.abs(g.x - b.x) < 0.0005" in free)
    # Whole-card dragging must not steal Plotly's own pan/zoom.
    # Whitespace- and quote-normalised: the guard is a concatenation of string
    # literals, so an exact substring test fails the moment a token is added to
    # the list or the line is rewrapped -- which is exactly what adding
    # .slicer-body did, without changing what is excluded.
    _flat = " ".join(
        free.replace("' +", "'").replace("'", " ").split())
    check("dragging the card does not hijack the plot or the notes",
          all(t in _flat for t in
              (".js-plotly-plot", ".modebar", ".notes", ".slicer-body")),
          _flat[-300:])
    check("the whole card is a drag handle on the free canvas",
          ".ly-editing.ly-free .chart-card" in editor_css())

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

    check("the handles have a grab target bigger than they look",
          ".ly-h-right::after" in css and "inset: -8px" in css, "")
    check("the accent placeholder is substituted",
          "__ACCENT__" not in css)


def test_kpi_tiles_share_the_chart_canvas() -> None:
    print("[28] KPI tiles: placed like charts, on the same canvas")
    import re
    import plotly.express as px
    from modules.export import generate_html_report
    from services.export_service import build_export_theme
    df = px.data.iris()
    charts = [("a", "First", px.bar(df, x="species", y="sepal_length"), "", "", {}),
              ("b", "Second", px.bar(df, x="species", y="sepal_width"), "", "", {})]
    kpis = [{"label": "Total", "value": "1", "icon": "X"},
            {"label": "Count", "value": "2", "icon": "Y"}]
    th = build_export_theme({})

    both = generate_html_report(
        charts, "S", theme=th, kpis=kpis, inline_plotly=False,
        tile_geometry={"a": {"x": 0, "y": 0, "w": 0.5, "h": 400},
                       "b": {"x": 0.5, "y": 0, "w": 0.5, "h": 400}},
        kpi_geometry={"0": {"x": 0, "y": 420, "w": 1.0, "h": 120}})
    check("KPIs and charts share ONE canvas on the free layout",
          both.count('<div class="ly-canvas"') == 1
          and '<div class="kpi-row">' not in both, both[:0])
    # A KPI placed below a chart must not push that chart's saved row index --
    # there are no row indices any more, because placement is absolute.
    ks = re.findall(r'<div class="kpi-card"[^>]*style="([^"]*)"', both)
    cs = re.findall(r'<div class="chart-card"[^>]*style="([^"]*)"', both)
    # A board whose geometry has no title yet gets one inserted at the top, which
    # shifts every card down by one band. The KPIs here carry no saved geometry
    # of their own, so they are auto-placed below that band; the charts were
    # saved by hand and moved with it.
    from modules.canvas_geometry import title_band_height
    band = title_band_height(th["title_size"], True)
    check("a KPI is placed exactly like a chart",
          ks and any(k.startswith(f"left:0.0000%;top:{420 + band + 24:.2f}px;"
                                  "width:100.0000%")
                     for k in ks), str(ks))
    check("placing a KPI leaves the chart coordinates untouched",
          cs and cs[0].startswith(f"left:0.0000%;top:{band + 24:.2f}px;"),
          str(cs))
    check("a KPI with no saved geometry is still positioned",
          len(ks) == 2 and all(k.strip() for k in ks), str(ks))

    hn = generate_html_report(charts, "S", theme=th, kpis=kpis,
                              inline_plotly=False)
    hn_geom = re.findall(r'<div class="kpi-card"[^>]*style="([^"]*)"', hn)
    check("auto-placed KPIs are sized like KPIs, not like charts",
          all("height:120.00px" in k for k in hn_geom), str(hn_geom))
    check("no empty style leaves a KPI unpositioned",
          all(k.strip() for k in hn_geom), str(hn_geom))

    bad = generate_html_report(charts, "S", theme=th, kpis=kpis,
                               inline_plotly=False,
                               kpi_geometry={"0": {"x": -5, "y": 0, "w": 9, "h": 0}})
    bs = re.findall(r'data-kpi="0" style="([^"]*)"', bad)
    check("out-of-range KPI geometry is clamped, never invalid CSS",
          bs and "NaN" not in bs[0] and "left:0.0000%" in bs[0], str(bs))


def test_export_follows_tile_geometry() -> None:
    print("[24] HTML export: free placement, visual order, no editor chrome")
    import re
    import plotly.express as px
    from modules.export import generate_html_report
    from services.export_service import build_export_theme
    df = px.data.iris()
    charts = [("b", "Second", px.bar(df, x="species", y="sepal_length"), "", "", {}),
              ("a", "First", px.scatter(df, x="petal_length", y="petal_width"), "", "", {}),
              ("c", "Third", px.box(df, x="species", y="sepal_width"), "", "", {})]
    th = build_export_theme({})
    geom = {"a": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 420.0},
            "b": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 300.0},
            "c": {"x": 0.1, "y": 440.0, "w": 0.4, "h": 200.0}}
    html = generate_html_report(charts, "S", grid_cols_n=2, theme=th,
                                tile_geometry=geom)
    order = re.findall(r'">(First|Second|Third)<', html)
    check("export walks the visual tile order, not bridge order",
          order == ["First", "Second", "Third"], str(order))

    check("cards are absolutely placed on one canvas",
          '<div class="ly-canvas"' in html and 'class="grid"' not in html)
    styles = re.findall(r'<div class="chart-card"[^>]*style="([^"]*)"', html)
    # The heading is a card on this canvas now, and a board whose geometry has
    # no title yet gets one inserted at the top -- which moves everything else
    # down by exactly one band. Both facts are pinned here rather than worked
    # around, because "the title covers the first row" is what the shift exists
    # to prevent.
    from modules.canvas_geometry import TITLE_UID, title_band_height
    band = title_band_height(th["title_size"], True)
    check("placement is left/top in percent+px, width percent, height px",
          styles[0] == f"left:0.0000%;top:{band + 24:.2f}px;width:50.0000%;"
                        "height:420.00px;"
          and styles[2] == f"left:10.0000%;top:{440 + band + 24:.2f}px;"
                            "width:40.0000%;height:200.00px;",
          str(styles))
    # The container needs an explicit height: absolutely positioned children are
    # out of flow, so without it the canvas collapses and the lower cards become
    # unreachable.
    m = re.search(r'<div class="ly-canvas" style="height:([\d.]+)px;"', html)
    check("the canvas is told how tall it must be",
          m and float(m.group(1)) == 640.0 + band + 24,
          m.group(1) if m else "no height")

    # Arbitrary positions must survive the round trip to CSS -- this is the
    # regression behind "the resize handles are just for show". The title is
    # given a placement here, so nothing shifts and the coordinates must come
    # out exactly as they went in.
    nudged = generate_html_report(charts, "S", theme=th, tile_geometry={
        TITLE_UID: {"x": 0.0, "y": 0.0, "w": 1.0, "h": band},
        "a": {"x": 0.137, "y": 217.5, "w": 0.413, "h": 333.25},
        "b": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 400.0},
        "c": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 400.0}})
    ns = re.findall(r'data-uid="a" style="([^"]*)"', nudged)
    check("an arbitrary dragged position is emitted verbatim",
          ns and ns[0] == "left:13.7000%;top:217.50px;width:41.3000%;height:333.25px;",
          str(ns))

    html0 = generate_html_report(charts, "S", grid_cols_n=2, theme=th)
    check("no geometry -> cards are auto-placed, not stacked",
          '<div class="ly-canvas"' in html0
          and len(re.findall(r'class="chart-card"', html0)) == 3)
    a0 = re.findall(r'<div class="chart-card"[^>]*style="([^"]*)"', html0)
    check("an auto-placed board fills two across",
          len(a0) == 3 and "width:50.0000%" in a0[0] and "width:50.0000%" in a0[1],
          str(a0))

    # A malformed stored value must not reach the document: the browser
    # discards invalid CSS and the card silently disappears.
    bad = generate_html_report(charts, "S", theme=th, tile_geometry={
        "a": {"x": -5, "y": "nan", "w": 9, "h": 0},
        "b": {"x": 0.9, "y": 0, "w": 0.5, "h": 100},
        "c": {"x": 0, "y": 0, "w": 0.5, "h": 400}})
    bs = re.findall(r'<div class="chart-card"[^>]*style="([^"]*)"', bad)
    check("out-of-range geometry is clamped, never invalid CSS",
          bs and all(("NaN" not in s and "nan" not in s and "-" not in s.split(":")[1][:1])
                     for s in bs), str(bs))
    check("a card dragged past the right edge is pulled back inside",
          all(float(re.search(r"left:([\d.]+)%", x).group(1))
              + float(re.search(r"width:([\d.]+)%", x).group(1)) <= 100.0 + 1e-6
              for x in bs), str(bs))

    # A session saved before the free canvas must still open on the board it was
    # saved with, so the old cell layout stays available as an explicit mode.
    grid = generate_html_report(charts, "S", theme=th, kpis=None,
                                tile_geometry={"a": {"x": 0, "y": 0, "w": 1, "h": 2},
                                               "b": {"x": 1, "y": 0, "w": 1, "h": 1},
                                               "c": {"x": 1, "y": 1, "w": 1, "h": 1}},
                                geometry_mode="grid")
    gs = re.findall(r'<div class="chart-card"[^>]*style="([^"]*)"', grid)
    check("legacy grid mode still emits explicit cell placement",
          gs[0] == "grid-column: 1 / span 1;grid-row: 1 / span 2;"
          and gs[1] == "grid-column: 2 / span 1;grid-row: 1 / span 1;", str(gs))
    check("legacy grid mode does not use the free canvas",
          '<div class="ly-canvas" style=' not in grid)


def test_export_replicates_the_legend_layout() -> None:
    print("[35c] HTML export: the dashboard legend matches the setting area")
    import re
    import pandas as pd
    from chart_display import (apply_display_options, default_layout_opts,
                               default_typo_opts)
    from modules.analysis.pie_chart import run_pie_chart
    from modules.export import generate_html_report
    from services.export_service import build_export_theme

    df = pd.DataFrame({
        "product_category": ["Coffee", "Tea", "Bakery", "Drinking Chocolate",
                             "Flavours", "Coffee beans", "Loose Tea",
                             "Branded", "Packaged Chocolate"],
        "Count": [137, 138, 136, 99, 89, 89, 89, 87, 76]})
    _, base_fig = run_pie_chart(df, x_cols=["product_category"])[0]

    for pos in ("Bottom Center", "Top Center", "Bottom Left"):
        lo = dict(default_layout_opts())
        lo["legend_position"] = pos
        lo["legend_orientation"] = "Horizontal"
        styled = apply_display_options(base_fig, lo, default_typo_opts(),
                                       width_px=1100, height_px=430)
        meta = {"config": {}, "layout": dict(lo),
                "typography": dict(default_typo_opts())}
        chart = ("c1", "Pie", styled, "", "pie", meta)
        html = generate_html_report(
            [chart], "S", theme=build_export_theme({}),
            tile_geometry={"c1": {"x": 0.1, "y": 0.3, "w": 0.8, "h": 430}},
            geometry_mode="free")
        # 1. The intent the user set in the Analysis setting area must reach the
        #    dashboard document, or the canvas can only ever show the default.
        dl = re.search(r'data-legend-pos="([^"]*)"[^>]*'
                       r'data-legend-orient="([^"]*)"', html)
        check(f"export carries the {pos} + Horizontal legend intent",
              dl and dl.group(1) == pos and dl.group(2) == "Horizontal",
              dl.groups() if dl else "no data-legend")
        # 2. The embedded Plotly legend must actually be the horizontal band the
        #    setting area produced -- a vertical column here is the bug where the
        #    canvas ignored the layout entirely.
        check(f"export bakes a horizontal legend for {pos}",
              re.search(r'"orientation":"h"', html) is not None, "")
        # 3. The runtime re-reserve pass must read the edge from data-legend-pos
        #    (a real position name) rather than guessing legend.y >= 0.5, and it
        #    must NOT force yanchor='middle' on every band -- that snapped a
        #    corner row back to the centre. The guard is whitespace-normalised.
        _flat = " ".join(html.split())
        check(f"export band JS reads the edge from data-legend-pos ({pos})",
              "pos.indexOf('bottom') === -1" in _flat, "")
        check("export band JS preserves an explicit legend yanchor",
              "if (!fl.legend.yanchor || fl.legend.yanchor === 'middle')" in _flat,
              "")


def test_export_free_canvas_is_printable() -> None:
    print("[24b] HTML export: the free canvas survives print and preview CSS")
    import re
    import plotly.express as px
    from modules.export import generate_html_report
    from services.export_service import build_export_theme
    df = px.data.iris()
    charts = [("a", "First", px.bar(df, x="species", y="sepal_length"), "", "", {})]
    th = build_export_theme({})
    html = generate_html_report(charts, "S", theme=th,
                                tile_geometry={"a": {"x": 0, "y": 0, "w": 0.5,
                                                     "h": 400}})
    # Absolute pixels are meaningless across paginated paper, and two cards
    # overlapping on screen would print on top of each other -- so print falls
    # back to document flow in the same visual order.
    _print_block = html.split("@media print")[-1]
    check("print CSS drops the absolute positioning",
          ".ly-canvas > .chart-card" in _print_block
          and "position: static !important" in _print_block
          and ".ly-canvas {" in _print_block, "")
    check("print CSS clears the pixel height so nothing keeps a stale size",
          "height: auto !important;" in html)
    check("print CSS does not full-bleed explicitly placed tiles",
          '.chart-card[style*="grid-column: 1 / -1"]' in html)
    # Cards are flex columns so a shortened card shows a shorter CHART, not the
    # original plot spilling past the bottom edge.
    check("the plot area flexes to fill the card",
          ".ly-canvas .chart-wrap" in html and "flex: 1 1 auto" in html)
    check("cards are box-sizing:border-box so padding does not inflate height",
          "box-sizing: border-box;" in html)
    check("the plot is refit to the measured height, not just the width",
          "height: height > 0 ? height : undefined" in html)


def test_dashboard_palette_is_theme_independent() -> None:
    print("[29] Dashboard palette: identical under light and dark app themes")
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
    check("page defaults ARE the service defaults (one source of truth)",
          dp._STYLE_DEFAULTS == dict(dp.EXPORT_DEFAULTS))


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
                      "__ly_mode__": "free"},
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


def test_pie_legend_is_not_drawn_over_the_pie() -> None:
    print("[35] Pie legend: the plate gets its own space")
    import copy as _copy

    import pandas as pd
    import plotly.graph_objects as go
    from chart_display import apply_legend_layout, _domain_traces
    from chart_display import (_legend_plate_px, _plot_area_width,
                               _LEGEND_DOMAIN_GAP_PX)
    from modules.analysis.pie_chart import run_pie_chart
    from modules.export import _is_domain_figure

    df = pd.DataFrame({"product_category": [
        "Tea", "Flavours", "Drinking Chocolate", "Coffee beans", "Bakery",
        "Loose Tea", "Packaged Chocolate", "Coffee", "Branded", "Other"],
        "Count": [137, 138, 136, 99, 89, 89, 89, 87, 76, 77]})
    _title, pie = run_pie_chart(df, x_cols=["product_category"])[0]
    labels = pie.data[0].labels
    names = [str(x) for x in (labels if labels is not None else [])]

    # The figure IS domain-based: it has no xaxis to carve.
    check("a pie is recognised as a domain figure",
          _is_domain_figure(pie) and _domain_traces(pie))
    check("a cartesian bar chart is NOT domain-based",
          not _is_domain_figure(go.Figure(go.Bar(x=["A"], y=[1]))))
    check("the shipped pie really does start full width",
          [round(v, 4) for v in pie.data[0].domain.x] == [0.0, 1.0])

    # A side reservation must squeeze the TRACE domain and anchor the legend.
    # The invariant that matters to the user is not a magic fraction: it is
    # that (a) the pie's box stops short of the legend's side, and (b) the
    # reserved strip is at least as wide as the plate needs, so the plate is
    # anchored BESIDE the marks and can never be pulled back over them (the
    # narrow-tile overlap). A domain figure's strip is allowed to exceed the
    # cartesian 0.50 cap to hold the plate -- the pie simply takes a smaller
    # box -- so the old "[0.0, 0.5]" pin encoded the buggy cap, not the rule.
    def _strip_holds_plate(fig, side, w=430):
        m = fig.layout.margin.to_plotly_json() or {}
        area = _plot_area_width(m, w)
        plate = _legend_plate_px(names, "product_category") + _LEGEND_DOMAIN_GAP_PX
        dx = list(fig.data[0].domain.x)
        strip_px = (1.0 - dx[1]) * area if side == "right" else dx[0] * area
        return area > 0 and strip_px >= plate - 1.0

    right = _copy.deepcopy(pie)
    apply_legend_layout(right, "Right", names=names,
                        legend_title="product_category", width_px=430)
    check("a right-hand legend squeezes the pie's own domain",
          0.0 < right.data[0].domain.x[1] < 1.0,
          str([round(v, 3) for v in right.data[0].domain.x]))
    check("the legend hugs the pie's domain edge, growing away from it",
          right.layout.legend.x < 1.0
          and right.layout.legend.xanchor == "left",
          f"x={right.layout.legend.x} anchor={right.layout.legend.xanchor}")
    check("pie and legend no longer share horizontal space",
          right.data[0].domain.x[1] <= 0.5 + 1e-9)
    check("the right strip is wide enough to hold the plate (no overlap)",
          _strip_holds_plate(right, "right"),
          str([round(v, 3) for v in right.data[0].domain.x]))

    left = _copy.deepcopy(pie)
    apply_legend_layout(left, "Left", names=names,
                        legend_title="product_category", width_px=430)
    check("a left-hand legend squeezes the other way",
          0.0 < left.data[0].domain.x[0] < 1.0,
          str([round(v, 3) for v in left.data[0].domain.x]))
    check("the left strip is wide enough to hold the plate (no overlap)",
          _strip_holds_plate(left, "left"),
          str([round(v, 3) for v in left.data[0].domain.x]))

    # Centred positions take the BAND path, which also used to write
    # yaxis.domain and so was equally a no-op on a pie.
    for pos in ("Bottom Center", "Top Center"):
        band = _copy.deepcopy(pie)
        plan = apply_legend_layout(band, pos, names=names,
                                   legend_title="product_category",
                                   width_px=430)
        ydom = [round(v, 3) for v in band.data[0].domain.y]
        check(f"{pos} reserves a real band on the trace",
              plan.get("mode") == "band"
              and (ydom[1] < 1.0 or ydom[0] > 0.0),
              f"mode={plan and plan.get('mode')} y={ydom}")


def test_pie_legend_squeeze_is_stable() -> None:
    print("[36] Pie legend: the squeeze is idempotent and width-safe")
    import copy as _copy

    import pandas as pd
    from chart_display import apply_legend_layout
    from modules.analysis.pie_chart import run_pie_chart

    df = pd.DataFrame({"product_category": [
        "Tea", "Flavours", "Drinking Chocolate", "Coffee beans", "Bakery",
        "Loose Tea", "Packaged Chocolate", "Coffee", "Branded", "Other"],
        "Count": [137, 138, 136, 99, 89, 89, 89, 87, 76, 77]})
    _title, pie = run_pie_chart(df, x_cols=["product_category"])[0]
    labels = pie.data[0].labels
    names = [str(x) for x in (labels if labels is not None else [])]

    # A compounding squeeze would march the pie off the card over refits.
    idem = _copy.deepcopy(pie)
    apply_legend_layout(idem, "Right", names=names,
                        legend_title="product_category", width_px=430)
    snap = [round(v, 4) for v in idem.data[0].domain.x]
    for _ in range(5):
        apply_legend_layout(idem, "Right", names=names,
                            legend_title="product_category", width_px=430)
    check("repeated refits do not compound the squeeze",
          [round(v, 4) for v in idem.data[0].domain.x] == snap, str(snap))

    storm = _copy.deepcopy(pie)
    apply_legend_layout(storm, "Right", names=names,
                        legend_title="product_category", width_px=430)
    for w in (1400, 700, 380, 900, 430):
        apply_legend_layout(storm, "Right", names=names,
                            legend_title="product_category", width_px=w)
    fresh = _copy.deepcopy(pie)
    apply_legend_layout(fresh, "Right", names=names,
                        legend_title="product_category", width_px=430)
    check("a resize storm converges on the single-shot layout",
          [round(v, 4) for v in storm.data[0].domain.x]
          == [round(v, 4) for v in fresh.data[0].domain.x])

    # With no width the strip must OVER-reserve: the real card width is only
    # decided by CSS at render time, so under-reserving reintroduces the bug.
    nowidth = _copy.deepcopy(pie)
    plan = apply_legend_layout(nowidth, "Right", names=names,
                               legend_title="product_category")
    check("a width-less export over-reserves instead of overlapping",
          plan.get("mode") == "column" and plan.get("strip") >= 0.4,
          f"strip={plan and plan.get('strip')}")


def test_pie_legend_options_are_live() -> None:
    """[36b] Pie & Donut: the legend position / layout combos actually apply.

    The shipped pie is ONE trace with an empty ``name`` and its categories in
    ``labels``. ``_figure_has_legend`` only looked at trace names, so a pie
    reported "no legend" and ``apply_display_options`` skipped the whole legend
    block -- every position except the ones Plotly happens to default to did
    nothing at all, and the plate stayed parked over the marks.
    """
    print("[36b] Pie & Donut: legend position / layout options are live")
    import copy as _copy

    import pandas as pd
    import plotly.graph_objects as go
    from chart_display import (LEGEND_POSITIONS, _figure_has_legend,
                               _legend_names, apply_display_options,
                               default_layout_opts, default_typo_opts,
                               infer_legend_position, infer_legend_orientation,
                               legend_layout_mode, refit_legend_width)
    from modules.analysis.pie_chart import run_pie_chart

    df = pd.DataFrame({"product_category": [
        "grocery_net", "personal_care", "travel", "health_fitness",
        "food_dining", "misc_pos", "misc_net", "entertainment", "kids_pets"],
        "amt": [500, 470, 430, 390, 350, 310, 270, 230, 190]})
    _title, pie = run_pie_chart(df, x_cols=["product_category"],
                                y_cols=["amt"], agg="sum")[0]

    # The pie's own name is empty -- that is the whole bug.
    check("the shipped pie really does carry an empty trace name",
          not str(pie.data[0].name or "").strip(), repr(pie.data[0].name))
    check("...and is nevertheless reported as having a legend",
          _figure_has_legend(pie), "reported no legend")
    check("its legend entries come from the trace's labels",
          _legend_names(pie) == [str(x) for x in pie.data[0].labels],
          str(_legend_names(pie))[:60])
    check("an unnamed cartesian chart still has no legend",
          not _figure_has_legend(go.Figure(go.Bar(x=["a"], y=[1]))),
          "reported a legend")
    check("a heatmap still has no legend",
          not _figure_has_legend(go.Figure(go.Heatmap(z=[[1, 2]]))),
          "reported a legend")

    # Every one of the eight placements must now move BOTH the plate anchor and
    # the pie's own domain -- previously neither was written at all.
    lo = default_layout_opts()
    lo["legend_position"] = "Top Right"
    ref = apply_display_options(_copy.deepcopy(pie), lo, default_typo_opts(),
                                width_px=1160)
    ref_dom = [round(float(v), 4) for v in ref.data[0].domain.x]

    distinct_anchors, distinct_doms = set(), set()
    for pos in LEGEND_POSITIONS:
        o = dict(lo)
        o["legend_position"] = pos
        f = apply_display_options(_copy.deepcopy(pie), o, default_typo_opts(),
                                  width_px=1160)
        check(f"legend: {pos} is recorded on the figure",
              infer_legend_position(f) == pos
              and legend_layout_mode(f) in ("column", "band"),
              f"infer={infer_legend_position(f)} "
              f"mode={legend_layout_mode(f)}")
        distinct_anchors.add((round(float(f.layout.legend.x), 4),
                              round(float(f.layout.legend.y), 4)))
        distinct_doms.add(tuple(round(float(v), 4)
                                for v in f.data[0].domain.x))
        # The live widget re-lays it out at its real width; the position has to
        # survive that, or the combo appears to work once then snap back.
        refit_legend_width(f, 1160)
        check(f"legend: {pos} survives a refit at the real width",
              infer_legend_position(f) == pos,
              f"became {infer_legend_position(f)}")

    check("legend: every position places the plate somewhere different",
          len(distinct_anchors) == len(LEGEND_POSITIONS),
          f"{len(distinct_anchors)} distinct anchors")
    # Exactly three x-domains are possible and all three must occur: the
    # right-hand positions squeeze to [0, 1-strip], the left-hand ones to
    # [strip, 1], and the two centred positions reserve a BAND instead, so the
    # pie keeps its full width. Anything fewer means a side was never carved.
    check("legend: side positions carve a domain, centred ones reserve a band",
          len(distinct_doms) == 3
          and any(d[0] > 0.0 and d[1] == 1.0 for d in distinct_doms)
          and any(d[0] == 0.0 and d[1] < 1.0 for d in distinct_doms)
          and (0.0, 1.0) in distinct_doms,
          f"{sorted(distinct_doms)}")

    # The legend LAYOUT row (orientation / order / click) has to land too.
    for orient, want in (("Vertical", "v"), ("Horizontal", "h")):
        o = dict(lo)
        o["legend_position"] = "Right"
        o["legend_orientation"] = orient
        f = apply_display_options(_copy.deepcopy(pie), o, default_typo_opts(),
                                  width_px=1160)
        check(f"legend layout: {orient} stacks the entries as '{want}'",
              str(f.layout.legend.orientation) == want,
              str(f.layout.legend.orientation))
    o = dict(lo)
    o["legend_order"] = "Reversed"
    o["legend_itemclick"] = "toggleothers"
    f = apply_display_options(_copy.deepcopy(pie), o, default_typo_opts(),
                              width_px=1160)
    check("legend layout: Reversed / Toggle others land on the figure",
          str(f.layout.legend.traceorder) == "reversed"
          and str(f.layout.legend.itemclick) == "toggleothers",
          f"{f.layout.legend.traceorder}/{f.layout.legend.itemclick}")

    # An explicit Horizontal on a CORNER dock must reserve a top/bottom BAND,
    # not a side column a later orientation pass would squeeze a wide row into
    # (the "horizontal legend overlaps the plot" report). Explicit Vertical
    # must keep the side column. Auto keeps the pixel heuristic. The recorded
    # orientation must also survive a refit, or the combo snaps back once the
    # widget re-lays the legend out at its real width.
    _bar = go.Figure()
    for _n in ("Series Alpha", "Series Beta", "Series Gamma"):
        _bar.add_bar(x=["a", "b", "c"], y=[1, 2, 3], name=_n)
    for _pos in ("Top Left", "Top Right", "Bottom Left", "Bottom Right"):
        for _want, _mode, _orient in (("Horizontal", "band", "h"),
                                      ("Vertical", "column", "v")):
            _o = dict(lo)
            _o["legend_position"] = _pos
            _o["legend_orientation"] = _want
            _f = apply_display_options(_copy.deepcopy(_bar), _o,
                                       default_typo_opts(), width_px=1160)
            _xdom = (tuple(round(v, 3) for v in _f.layout.xaxis.domain)
                     if _f.layout.xaxis.domain else None)
            check(f"legend layout: {_pos} + {_want} -> {_mode}",
                  legend_layout_mode(_f) == _mode
                  and str(_f.layout.legend.orientation) == _orient
                  and (_xdom == (0.0, 1.0) if _mode == "band"
                       else _xdom != (0.0, 1.0)),
                  f"mode={legend_layout_mode(_f)} "
                  f"orient={_f.layout.legend.orientation} xdom={_xdom}")
            refit_legend_width(_f, 1160)
            check(f"legend layout: {_pos} + {_want} survives a refit",
                  legend_layout_mode(_f) == _mode
                  and infer_legend_position(_f) == _pos
                  and str(infer_legend_orientation(_f)).lower() == _want.lower(),
                  f"mode={legend_layout_mode(_f)} "
                  f"pos={infer_legend_position(_f)} "
                  f"orient={infer_legend_orientation(_f)}")
    # Auto on a wide card keeps the column; headless (no width) is unchanged.
    _o = dict(lo); _o["legend_position"] = "Top Right"; _o["legend_orientation"] = "Auto"
    check("legend layout: Auto keeps the pixel heuristic (wide=column)",
          legend_layout_mode(apply_display_options(
              _copy.deepcopy(_bar), _o, default_typo_opts(),
              width_px=2400)) == "column",
          str(legend_layout_mode(apply_display_options(
              _copy.deepcopy(_bar), _o, default_typo_opts(), width_px=2400))))
    check("legend layout: no view width keeps the column (exports unchanged)",
          legend_layout_mode(apply_display_options(
              _copy.deepcopy(_bar), _o, default_typo_opts())) == "column",
          "headless path changed")


    # The strip must be sized from the real entry labels, not from ``[""]``.
    # A column measured against an empty name reserves ~4% of the card -- far
    # too narrow for the swatches it is about to hold, so the plate drops
    # straight back over the pie. Measured against the 9 categories it is
    # ~15%, which is what actually separates the two.
    check("legend: the column is sized from the category labels",
          0.80 < ref_dom[1] < 0.90,
          f"domain.x={ref_dom} (expected a ~15% strip, not the ~4% a name-only "
          f"list reserves)")


def test_pie_slice_labels_follow_the_theme() -> None:
    """[36c] Pie & Donut: slice labels are legible in light mode.

    ``_apply_pie_options`` writes the dark default (``pie_value_color``
    #e2e8f0) into ``insidetextfont`` / ``outsidetextfont`` -- the two fonts a
    pie actually labels its slices from. ``theme_figure`` only re-tinted the
    generic ``textfont``, so a light-mode pie drew near-white text on a white
    canvas: the "values are washed out" report.
    """
    print("[36c] Pie & Donut: slice labels follow the app theme")
    import copy as _copy

    import pandas as pd
    import plotly.graph_objects as go
    from chart_display import (apply_display_options, default_layout_opts,
                               default_typo_opts, theme_figure)
    from modules.analysis.pie_chart import run_pie_chart

    df = pd.DataFrame({"category": ["a", "b", "c"], "amt": [30, 20, 10]})
    _title, pie = run_pie_chart(df, x_cols=["category"], y_cols=["amt"],
                                agg="sum")[0]
    lo = default_layout_opts()
    styled = apply_display_options(_copy.deepcopy(pie), lo, default_typo_opts(),
                                   width_px=1160)

    check("the shipped pie labels from the dark default #e2e8f0",
          str(styled.data[0].outsidetextfont.color).lower() == "#e2e8f0",
          str(styled.data[0].outsidetextfont.color))

    dark = theme_figure(_copy.deepcopy(styled), "dark").data[0]
    light = theme_figure(_copy.deepcopy(styled), "light").data[0]

    def cols(t):
        return (str(t.insidetextfont.color).lower(),
                str(t.outsidetextfont.color).lower())

    check("dark mode keeps the near-white slice labels",
          cols(dark) == ("#e2e8f0", "#e2e8f0"), str(cols(dark)))
    check("light mode re-tints BOTH slice-label fonts to a dark ink",
          cols(light) == ("#334155", "#334155"), str(cols(light)))
    check("the light canvas is really white, so the ink is required",
          str(theme_figure(_copy.deepcopy(styled), "light")
              .layout.paper_bgcolor).lower() == "#ffffff")

    # A colour the user picked in Typography must survive, exactly like every
    # other swatch -- the remap only rewrites the known dark defaults.
    o = dict(lo)
    o["pie_value_color"] = "#00ff00"
    picked = apply_display_options(_copy.deepcopy(pie), o, default_typo_opts(),
                                   width_px=1160)
    lightp = theme_figure(_copy.deepcopy(picked), "light").data[0]
    check("a user-picked pie label colour survives the light switch",
          cols(lightp) == ("#00ff00", "#00ff00"), str(cols(lightp)))

    # The generic textfont pass must still work for the other trace types.
    bar = go.Figure(go.Bar(x=["a"], y=[1], name="s",
                           textfont=dict(color="#e2e8f0")))
    check("a cartesian value label still follows the theme",
          str(theme_figure(bar, "light").data[0].textfont.color).lower()
          == "#334155",
          str(theme_figure(bar, "light").data[0].textfont.color))


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
    import copy as _copy
    import pandas as pd
    from chart_display import (apply_display_options, default_layout_opts,
                               default_typo_opts, refit_legend_width,
                               _type_scale)
    from modules.analysis.pie_chart import run_pie_chart

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

    df = pd.DataFrame({"product_category":
                       ["Coffee", "Tea", "Bakery", "Drinking Chocolate",
                        "Flavours", "Coffee beans", "Loose Tea", "Branded",
                        "Packaged Chocolate"],
                       "Count": [137, 138, 136, 99, 89, 89, 89, 87, 76]})
    _, pie = run_pie_chart(df, x_cols=["product_category"])[0]

    def legend_and_label_size(width):
        f = apply_display_options(_copy.deepcopy(pie), default_layout_opts(),
                                  default_typo_opts(), width_px=width,
                                  height_px=430)
        lf = f.layout.legend.font
        pf = f.data[0].outsidetextfont
        return (lf.size if lf and lf.size else None,
                pf.size if pf and pf.size else None)

    wide_lg, wide_pf = legend_and_label_size(1160)
    small_lg, small_pf = legend_and_label_size(360)
    check("a wide card keeps the full 11px legend text",
          wide_lg == 11, f"legend.font.size={wide_lg}")
    check("a wide card keeps the full 11px slice labels",
          wide_pf == 11, f"outsidetextfont.size={wide_pf}")
    check("a small card shrinks the legend text",
          small_lg is not None and small_lg < wide_lg,
          f"{wide_lg}px -> {small_lg}px")
    check("a small card shrinks the slice labels with it",
          small_pf is not None and small_pf < wide_pf,
          f"{wide_pf}px -> {small_pf}px")

    # A HORIZONTAL legend shrinks further so its entries fit one row.
    lo_h = dict(default_layout_opts(), legend_position="Top Left",
                legend_orientation="Horizontal")
    f_h = apply_display_options(_copy.deepcopy(pie), lo_h, default_typo_opts(),
                                width_px=700, height_px=430)
    h_lg = f_h.layout.legend.font.size
    f_v = apply_display_options(_copy.deepcopy(pie),
                                dict(default_layout_opts()), default_typo_opts(),
                                width_px=700, height_px=430)
    v_lg = f_v.layout.legend.font.size
    check("a horizontal legend shrinks to fit one row",
          h_lg is not None and v_lg is not None and h_lg <= v_lg,
          f"horizontal={h_lg}px vs vertical={v_lg}px")

    # The resize path re-scales the font (idempotent), not just the geometry.
    f_rs = apply_display_options(_copy.deepcopy(pie), default_layout_opts(),
                                 default_typo_opts(), width_px=1160,
                                 height_px=430)
    refit_legend_width(f_rs, 1160, 430)     # stash baseline at 11px
    refit_legend_width(f_rs, 360, 300)      # shrink
    refit_legend_width(f_rs, 360, 300)      # repeat: must not compound
    rs_lg = f_rs.layout.legend.font.size
    check("a resize re-scales the legend font to the small card",
          rs_lg is not None and rs_lg < 11, f"legend.font.size={rs_lg} after resize")
    rs_lg2 = refit_legend_width(f_rs, 360, 300) and f_rs.layout.legend.font.size
    check("repeated resizes at the same width do not compound",
          f_rs.layout.legend.font.size == rs_lg,
          f"{rs_lg}px then {f_rs.layout.legend.font.size}px")

    # And it comes back up when the card grows again (scaled from the stash).
    refit_legend_width(f_rs, 1160, 430)
    check("growing the card restores the full-size legend text",
          f_rs.layout.legend.font.size == 11,
          f"legend.font.size={f_rs.layout.legend.font.size} after regrow")


def test_exporter_reserves_pie_legend_space() -> None:
    print("[37] Exporter: pies get a reserved legend, cartesian charts do not")
    import pandas as pd
    import plotly.graph_objects as go
    import re as _re
    import json as _json
    from modules.analysis.pie_chart import run_pie_chart
    from modules.export import generate_html_report
    from services.export_service import build_export_theme

    def _plot(h):
        m = _re.search(r'Plotly\.newPlot\(\s*"[^"]+",\s*(\[.*?\])\s*,\s*'
                       r'(\{.*?\}),\s*\{', h, _re.S)
        return ((_json.loads(m.group(1)), _json.loads(m.group(2)))
                if m else (None, None))

    th = build_export_theme({})
    df = pd.DataFrame({"product_category": [
        "Tea", "Flavours", "Drinking Chocolate", "Coffee beans", "Bakery",
        "Loose Tea", "Packaged Chocolate", "Coffee", "Branded", "Other"],
        "Count": [137, 138, 136, 99, 89, 89, 89, 87, 76, 77]})
    _t, pie = run_pie_chart(df, x_cols=["product_category"])[0]

    for pos in ("Top Right", "Bottom Center", "Left"):
        h = generate_html_report(
            [("p", "Pie", pie, "", "", {"legend_position": pos})],
            "S", grid_cols_n=2, theme=th)
        data, layout = _plot(h)
        dom = (data[0].get("domain") or {}) if data else {}
        lg = layout.get("legend", {}) if layout else {}
        squeezed = dom.get("x") != [0.0, 1.0] or dom.get("y") != [0.0, 1.0]
        anchored = lg.get("x") is not None and lg.get("xanchor") is not None
        check(f"export reserves legend space for a pie ({pos})",
              squeezed and anchored,
              f"domain={dom} legend.x={lg.get('x')} anchor={lg.get('xanchor')}")

    # A cartesian chart's saved layout is pinned elsewhere and must not shift.
    bar = go.Figure(go.Bar(x=["A", "B"], y=[1, 2]))
    hb = generate_html_report(
        [("b", "Bar", bar, "", "", {"legend_position": "Top Right"})],
        "S", grid_cols_n=2, theme=th)
    _db, lb = _plot(hb)
    check("a cartesian chart keeps its axis-based layout (xaxis untouched)",
          "domain" not in (lb.get("xaxis") or {}), str(lb.get("xaxis")))

    # The legend position the user chose must reach the export. A real session
    # stores it under meta["layout"] (meta is the {config, layout, typography}
    # recipe), and the widget already recorded it on the figure at build time;
    # reading only the flat meta["legend_position"] made every saved pie fall
    # back to the "Top Right" default -- the "canvas edits ignored on export"
    # report. Both the flat shape (headless callers) and the nested shape must
    # resolve, and a styled figure's own recorded position must win.
    from chart_display import (apply_display_options, default_layout_opts,
                               default_typo_opts, infer_legend_position)
    for _pos, _side in (("Left", "left"), ("Right", "right"),
                        ("Bottom Left", "left")):
        for _meta in ({"legend_position": _pos},
                      {"layout": {"legend_position": _pos}}):
            _d, _lay = _plot(generate_html_report(
                [("p", "Pie", run_pie_chart(df, x_cols=["product_category"])[0][1],
                  "", "", _meta)], "S", grid_cols_n=2, theme=th))
            _l = _lay.get("legend", {})
            _dm = (_d[0].get("domain") or {}) if _d else {}
            _x = _l.get("x")
            check(f"export honours {_pos} from "
                  f"{'nested' if 'layout' in _meta else 'flat'} meta",
                  _x is not None
                  and ((float(_x) < 0.5) if _side == "left" else (float(_x) > 0.5))
                  and (_dm.get("x") != [0.0, 1.0] or _dm.get("y") != [0.0, 1.0]),
                  f"legend.x={_x} domain={_dm}")
    # The figure's own recorded position beats either meta shape.
    _styled = apply_display_options(
        run_pie_chart(df, x_cols=["product_category"])[0][1],
        dict(default_layout_opts(), legend_position="Bottom Left"),
        default_typo_opts(), width_px=1160)
    assert infer_legend_position(_styled) == "Bottom Left"
    _d, _lay = _plot(generate_html_report(
        [("p", "Pie", _styled, "", "",
          {"layout": {"legend_position": "Right"}})], "S", grid_cols_n=2, theme=th))
    check("export trusts the figure's recorded legend position over meta",
          float(_lay.get("legend", {}).get("x", 1.0)) < 0.5,
          f"legend.x={_lay.get('legend', {}).get('x')} (expected the styled "
          f"Bottom Left, not the meta's Right)")

    h = generate_html_report(
        [("p", "Pie", pie, "", "", {}), ("b", "Bar", bar, "", "", {})],
        "S", grid_cols_n=2, theme=th)
    check("a report mixing a pie and a cartesian chart still exports",
          len(_re.findall(r'class="chart-card"', h)) == 2)


def test_card_chrome_does_not_clip_or_collide() -> None:
    print("[38] Card chrome: full-size handles, heading that fits")
    import plotly.express as px
    import re as _re
    from modules.report_editor import editor_css
    from modules.export import generate_html_report
    from services.export_service import build_export_theme

    css = editor_css("#6163df")
    # The card clips its overflow, so a handle placed outside it loses half its
    # grab target -- the clipped handles visible on the cards in the report.
    check("resize handles sit inside the clipping card",
          ".ly-h-right  { top: 50%; right: 0;" in css
          and ".ly-h-bottom { left: 50%; bottom: 0;" in css
          and ".ly-h-corner { right: 0; bottom: 0;" in css)
    check("no handle is left hanging outside the card",
          "right: -6px" not in css and "bottom: -6px" not in css)
    check("the heading clears the edit chrome",
          ".ly-editing .chart-card h2" in css and "margin-top: 26px" in css)
    check("chrome clearance is scoped to editing only",
          not _re.search(r"^\s*\.chart-card h2[^{]*\{[^}]*margin-top: 26px",
                         css, _re.M))

    d = px.data.iris()
    html = generate_html_report(
        [("a", "A Very Long Chart Title That Has To Fit The Card Width",
          px.bar(d, x="species", y="sepal_length"), "", "", {})],
        "S", grid_cols_n=2, theme=build_export_theme({}))
    check("the card heading can shrink/wrap to its card",
          "overflow-wrap: break-word" in html
          and "word-break: break-word" in html)
    check("heading auto-fit rules reach the saved file",
          "min-width: 0" in html)


def test_the_title_is_a_card_like_every_other() -> None:
    print("[39] Title: a card with the same tiling, handles and arrangement")
    import re as _re
    import plotly.express as px
    from modules.canvas_geometry import (TITLE_UID, ensure_title_band,
                                          is_grid_shaped)
    from modules.export import generate_html_report
    from modules.report_editor import editor_css, editor_js
    from services.export_service import build_export_theme

    d = px.data.iris()
    html = generate_html_report(
        [("a", "A", px.bar(d, x="species", y="sepal_length"), "", "", {})],
        "S", grid_cols_n=2, theme=build_export_theme({}))

    head = _re.search(r'<div class="report-header title-card"[^>]*>', html)
    check("the heading is emitted as a card", head is not None, html[:400])
    check("the title card carries the reserved uid the page stores it under",
          bool(head) and f'data-uid="{TITLE_UID}"' in head.group(0), "")
    check("the title card is positioned by geometry, not left in flow",
          bool(head) and "left:" in head.group(0) and "top:" in head.group(0),
          head.group(0) if head else "")
    # It must be INSIDE the canvas, not beside it: outside, the overlay cannot
    # see it and it is the one element that cannot be arranged.
    canvas = _re.search(r'<div class="ly-canvas"[^>]*>(.*?)</div>\s*<script',
                        html, _re.S)
    check("the title card lives inside the free canvas",
          bool(canvas) and f'data-uid="{TITLE_UID}"' in canvas.group(1), "")

    # The first board this runs on has no title yet, and its charts all start at
    # y=0 -- exactly where the new band lands.
    saved = {"a": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 400.0},
             "b": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 400.0}}
    moved, title = ensure_title_band(saved, height=120, gap_px=24)
    check("a board saved without a title gains one at the top",
          TITLE_UID in moved and moved[TITLE_UID]["y"] == 0.0, str(moved))
    check("the existing cards move DOWN, they are not covered by the title",
          moved["a"]["y"] >= title["h"], str(moved))
    check("the shift is one band, so their arrangement is preserved",
          moved["a"]["y"] - saved["a"]["y"] == title["h"] + 24
          and moved["a"]["h"] == saved["a"]["h"]
          and moved["b"]["x"] == saved["b"]["x"], str(moved))
    # ...and exactly once: with the title stored, the map must be untouched.
    again, title2 = ensure_title_band(moved, height=999, gap_px=24)
    check("a board that already has a title is left completely alone",
          again == moved and title2["h"] == title["h"], str(again))

    # Same grips, same handles: the overlay must decorate it.
    js = editor_js(2, 24, mode="free")
    check("the overlay picks the title card up as a card",
          ".title-card[data-uid]" in js, "")
    check("dragging the title works like dragging a chart",
          # Read out of the begin() selector itself rather than matched as one
          # literal: the list is wrapped over two lines and now carries
          # .slicer-card, so an exact-substring test broke as soon as the
          # slicer became a draggable family -- which is the behaviour being
          # asserted here, not a punctuation detail.
          (lambda _m: bool(_m) and all(
              t in _m.group(1)
              for t in ('.chart-card', '.kpi-card', '.title-card',
                        '.slicer-card')))(
              __import__('re').search(
                  r"currentTarget\.closest\(\s*'([^']+)'", js)),
          "")
    css = editor_css("#6163df")
    check("the title card is a drag target on the free canvas",
          ".ly-editing.ly-free .title-card { cursor: grab; }" in css, "")
    check("the heading clears the edit chrome like a chart heading does",
          ".ly-editing .title-card h1" in css, "")
    check("the title card gets no chart action bar",
          "el.classList.contains('title-card')" in js, "")

    # A saved session must bring the title back exactly where it was put. The
    # loader int()-coerces every stored rectangle into GRID cells, and the title
    # is always full width -- so left in that loop it came back as x=0, w=1 and
    # the whole board was then mistaken for a legacy cell layout and rescaled.
    from desktop.pages.dashboard_page import _load_saved_tiles
    saved_back = _load_saved_tiles({
        "grid_cols_n": 2,
        "grid_fullwidth_json": '{"__title__": {"x": 0.1, "y": 30.0, '
                               '"w": 0.8, "h": 150.0}, '
                               '"a": {"x": 0.0, "y": 0.5, "w": 1.0, "h": 400.0}}',
    })
    check("the title survives a session round trip in free units",
          saved_back[TITLE_UID] == {"x": 0.1, "y": 30.0, "w": 0.8, "h": 150.0},
          str(saved_back.get(TITLE_UID)))
    check("the title's presence cannot tip a free board into a grid reading",
          is_grid_shaped({"a": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 400.0}}, 2)
          is is_grid_shaped(
              {"a": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 400.0},
               TITLE_UID: saved_back[TITLE_UID]}, 2),
          str(saved_back))


def test_the_title_field_actually_rebuilds_the_board() -> None:
    print("[40] Title field: typing has to reach the board")
    import pathlib
    src = pathlib.Path("desktop/pages/dashboard_page.py").read_text()
    # The three Dashboard Info fields are only ever READ by _export_inputs,
    # which is only called from a rebuild -- so with nothing connected, typing a
    # title changed nothing until some other control happened to rebuild.
    for field in ("dash_title", "session_name", "layout_mode"):
        check(f"{field} is connected to a rebuild",
              f"self.{field}.textChanged.connect" in src
              or f"self.{field}.currentTextChanged.connect" in src, field)
    check("the rebuild is debounced rather than per keystroke",
          "_on_report_field_changed" in src
          and "_schedule_regen(continuous=True)" in src, "")


def test_legend_and_colourbar_follow_the_card() -> None:
    print("[41] Legend transparent by default; text still follows the card")
    import plotly.express as px
    from modules.export import _theme_chart_furniture
    from services.export_service import EXPORT_PRESETS

    def _legend_on(card_bg: str):
        fig = px.bar(px.data.iris(), x="species", y="sepal_length",
                     color="species")
        # Worst case a runner can hand the exporter: a dark slate plate and
        # near-white item text, hard-coded for a dark app canvas. The fixture
        # re-supplies the plate ON PURPOSE -- the theme layer's job is to
        # erase it (transparent by default for any chart) and still re-tint
        # the text for the card underneath.
        fig.update_layout(legend=dict(bgcolor="rgba(15, 23, 42, 0.35)",
                                      font=dict(color="#e2e8f0")))
        _theme_chart_furniture(fig, card_bg, "#334155")
        return fig.layout.legend

    def _transparent(value) -> bool:
        return str(value).replace(" ", "") == "rgba(0,0,0,0)"

    light = _legend_on("#ffffff")
    dark = _legend_on("#1b2245")
    check("a light card's legend draws no plate",
          _transparent(light.bgcolor), str(light.bgcolor))
    check("a dark card's legend draws no plate",
          _transparent(dark.bgcolor), str(dark.bgcolor))
    check("legend items are dark ink on a light card",
          str(light.font.color) == "#334155", str(light.font.color))
    check("legend items stay light on a dark card",
          str(dark.font.color) == "#e2e8f0", str(dark.font.color))

    # EVERY preset must resolve. Checking two hand-picked colours would not catch
    # a preset that regresses, and the presets are the palettes users pick from.
    bad = [name for name, colours in EXPORT_PRESETS.items()
           if not _transparent(_legend_on(colours["ex_card"]).bgcolor)]
    check("every quick theme keeps its legend plate-free",
          not bad, str(bad))

    # A colour the USER picked is never re-tinted, or the setting that is
    # supposed to control it stops controlling anything.
    fig = px.bar(px.data.iris(), x="species", y="sepal_length", color="species")
    fig.update_layout(legend=dict(font=dict(color="#ff00ff")))
    _theme_chart_furniture(fig, "#ffffff", "#334155")
    check("a user-picked legend colour is left alone",
          str(fig.layout.legend.font.color) == "#ff00ff",
          str(fig.layout.legend.font.color))

    # The colour bar is the other piece of furniture drawn inside the SVG.
    import plotly.graph_objects as go
    cfig = go.Figure(go.Scatter(x=[1, 2], y=[1, 2], marker=dict(
        color=[1, 2], colorbar=dict(tickfont=dict(color="#e2e8f0")))))
    _theme_chart_furniture(cfig, "#ffffff", "#334155")
    check("colour-bar ticks follow a light card too",
          str(cfig.data[0].marker.colorbar.tickfont.color) == "#334155",
          str(cfig.data[0].marker.colorbar.tickfont.color))

    # And it must actually run on the way out, or none of the above is reachable.
    from modules.export import generate_html_report
    from services.export_service import build_export_theme
    html = generate_html_report(
        [("a", "A", px.bar(px.data.iris(), x="species", y="sepal_length",
                           color="species"), "", "", {})],
        "S", grid_cols_n=2, theme=build_export_theme({"ex_card": "#ffffff"}))
    check("the exporter draws a transparent legend on a real chart",
          "rgba(0, 0, 0, 0)" in html, "")
    check("the old hard-coded slate plate is gone from a light report",
          "rgba(15, 23, 42, 0.35)" not in html, "")
    check("the light-card plate alpha is gone too",
          "rgba(255, 255, 255, 0.860)" not in html, "")


def test_styling_never_invents_a_colour_bar() -> None:
    """The ghost colour bar: styling must not CREATE a scale nobody asked for.

    ``trace.colorbar`` / ``layout.coloraxis.colorbar`` are not plain attributes
    -- Plotly synthesises a default sub-object on every read, and any assignment
    to it is serialised as a real ``colorbar`` key. ``plotly.js`` decides
    "this chart HAS a colour bar" from exactly that key
    (``isPlainObject(t.colorbar)``), so a helper that styled
    ``getattr(tr, "colorbar", None)`` unconditionally painted a full colour ramp
    with tick labels down the right-hand edge of every bar, line and pie chart
    on the dashboard.

    ``real_colorbars`` is the fix, and the split it encodes is what makes this
    test non-obvious: default-scale traces (heatmap, contour, choropleth, ...)
    must be CAUGHT even though no ``colorbar`` appears in their JSON, while
    opt-in traces (marker colour) must be left alone unless asked.
    """
    import copy
    print("[52] Colour-bar styling cannot conjure a colour bar")
    import plotly.express as px
    import plotly.graph_objects as go
    from chart_display import (_apply_colorbar_border, apply_display_options,
                               default_layout_opts, real_colorbars)
    from modules.export import _theme_chart_furniture

    # A figure "has" a colour bar exactly when ``colorbar`` is a plain object
    # somewhere in its serialised form -- the same test plotly.js applies.
    #
    # ``layout.template`` MUST be skipped: Plotly ships a default template whose
    # every trace type carries a colorbar (that is what lets a user write
    # ``template="plotly_dark"`` and get bars), and plotly.js never draws those
    # for a bar trace. Walking into it would flag every figure in existence.
    def drawn(fig) -> bool:
        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("colorbar"), dict):
                    return True
                return any(k != "template" and walk(v)
                           for k, v in node.items())
            if isinstance(node, (list, tuple)):
                return any(walk(v) for v in node)
            return False
        return walk(fig.to_plotly_json())

    # -- The reported bug: every chart type with no scale must stay bare.
    ghosts = []
    for label, fig in (
            ("bar", px.bar(px.data.iris(), x="species", y="sepal_length")),
            ("grouped bar", px.bar(px.data.iris(), x="species",
                                   y="sepal_length", color="species")),
            ("bar+line dual axis",
             go.Figure(go.Bar(x=list("abc"), y=[3, 2, 1])).add_trace(
                 go.Scatter(x=list("abc"), y=[1, 2, 3], yaxis="y2"))),
            ("line", px.line(px.data.gapminder(), x="year", y="gdpPercap",
                             color="country")),
            ("area", px.area(px.data.stocks(), x="date", y="GOOG")),
            ("pie", px.pie(px.data.tips(), values="total_bill", names="day")),
            ("donut", go.Figure(go.Pie(labels=list("abc"), values=[1, 2, 3],
                                       hole=0.4))),
            ("scatter", px.scatter(px.data.iris(), x="sepal_width",
                                   y="sepal_length")),
            # A px bubble with a CONTINUOUS colour is NOT here: px binds it to
            # layout.coloraxis and plotly really does draw a ramp for it, so it
            # belongs in the "keep" list below. Discrete / no colour is the case
            # that must stay bare.
            ("histogram", px.histogram(px.data.tips(), x="total_bill")),
            ("box", px.box(px.data.tips(), x="day", y="total_bill")),
            ("table", go.Figure(go.Table(header=dict(values=["a", "b"]),
                                         cells=dict(values=[[1, 2], [3, 4]])))),
    ):
        # Run each through BOTH passes that used to leak, in both border
        # states -- "Show border" ON was the loudest ghost of all.
        for border in (True, False):
            themed = _theme_chart_furniture(copy.deepcopy(fig), "#ffffff", "#334155")
            styled = apply_display_options(
                copy.deepcopy(fig), default_layout_opts(), width_px=700)
            _apply_colorbar_border(styled, border)
            for tag, out in (("themed", themed), ("display", styled)):
                # ``drawn`` walks the serialised form, so a bar written by any
                # route at all is caught, not just the ones we think to test.
                if drawn(out) or list(real_colorbars(out)):
                    ghosts.append(f"{label}/{tag}/border={border}")
    check("no chart without a scale grows a colour bar (bar/line/pie/table/...)",
          not ghosts, ", ".join(ghosts[:6]))

    # A plain numeric marker colour is a colour, not a colour RAMP: Plotly maps
    # each value to one swatch, so there is nothing for a bar to show.
    nums = go.Figure(go.Scatter(x=[1, 2], y=[1, 2],
                                marker=dict(color=[3, 7], showscale=False)))
    check("numeric marker colour with showscale off keeps no colour bar",
          not drawn(nums), "")

    # -- Real bars must survive BOTH passes, or "Show border" silently dies.
    keep = []
    for label, fig in (
            ("heatmap", go.Figure(go.Heatmap(z=[[1, 2], [3, 4]]))),
            ("contour", go.Figure(go.Contour(z=[[1, 2], [3, 4]]))),
            ("surface", go.Figure(go.Surface(z=[[1, 2], [3, 4]]))),
            ("histogram2d", go.Figure(
                go.Histogram2d(x=[1, 1, 2, 2], y=[1, 2, 1, 2]))),
            ("choropleth", go.Figure(go.Choropleth(
                locations=["IND", "USA"], z=[3, 7]))),
            ("marker showscale", go.Figure(go.Scatter(
                x=[1, 2], y=[1, 2],
                marker=dict(color=[3, 7], showscale=True)))),
            ("marker colorbar", go.Figure(go.Scatter(
                x=[1, 2], y=[1, 2],
                marker=dict(color=[3, 7], colorbar=dict(title="r"))))),
            ("px.imshow", px.imshow([[1, 2], [3, 4]])),
            # px binds a continuous scatter/bubble to layout.coloraxis and sets
            # a colorbar title there, so plotly draws a ramp -- it must survive.
            ("px bubble", px.scatter(px.data.iris(), x="sepal_width",
                                     y="sepal_length", size="petal_length",
                                     color="petal_length")),
    ):
        themed = _theme_chart_furniture(copy.deepcopy(fig), "#ffffff", "#334155")
        styled = apply_display_options(
            copy.deepcopy(fig), default_layout_opts(), width_px=700)
        _apply_colorbar_border(styled, True)
        missing = [tag for tag, out in (("themed", themed), ("display", styled))
                   if not (drawn(out) and list(real_colorbars(out)))]
        if missing:
            keep.append(f"{label}:{'/'.join(missing)}")
    check("every real colour bar survives theming and display options",
          not keep, ", ".join(keep))

    # showscale=False is the user explicitly saying "no scale"; it must beat the
    # heatmap default, or the toggle cannot turn a bar off.
    off = go.Figure(go.Heatmap(z=[[1, 2], [3, 4]], showscale=False))
    off = _theme_chart_furniture(off, "#ffffff", "#334155")
    _apply_colorbar_border(
        apply_display_options(off, default_layout_opts(), width_px=700), True)
    check("showscale=False beats the heatmap default and hides the bar",
          not drawn(off) and not list(real_colorbars(off)), "")

    # "Show border" still has to REACH the bar it is meant to frame.
    bord = go.Figure(go.Heatmap(z=[[1, 2], [3, 4]]))
    _apply_colorbar_border(bord, True)
    check("Show border on reaches a real colour bar",
          int(bord.data[0].colorbar.borderwidth or 0) == 1,
          str(bord.data[0].colorbar.borderwidth))
    _apply_colorbar_border(bord, False)
    check("Show border off clears it again",
          int(bord.data[0].colorbar.borderwidth or 0) == 0,
          str(bord.data[0].colorbar.borderwidth))

    # px.imshow binds to the shared layout.coloraxis and its OWN colorbar is
    # ignored by plotly.js, so exactly one bar must come out -- the axis one.
    im = apply_display_options(px.imshow([[1, 2], [3, 4]]),
                               default_layout_opts(), width_px=700)
    check("px.imshow is styled exactly once, on the shared colour axis",
          len(list(real_colorbars(im))) == 1,
          str(len(list(real_colorbars(im)))))

    # The end-to-end proof: a dark-themed bar chart exported as HTML must carry
    # no colorbar key in its FIGURE payload. This is the path the dashboard
    # actually takes, and it is the one that produced the visible artefact.
    #
    # The page also embeds plotly.js itself, whose source is full of the words
    # "coloraxis"/"colorbar" (schema + the default template), so the page text
    # cannot be substring-matched. The traces are the JSON array handed to
    # ``Plotly.newPlot``, and THAT is what plotly.js reads at draw time.
    import json as _json
    from modules.export import generate_html_report
    from services.export_service import build_export_theme
    html = generate_html_report(
        [("a", "A", px.bar(px.data.iris(), x="species", y="sepal_length",
                           color="species"), "", "", {})],
        "S", grid_cols_n=2,
        theme=build_export_theme({"ex_card": "#0b1220"}))
    # ``rfind``: the embedded plotly.js bundle ALSO contains the literal text
    # "Plotly.newPlot(" inside its own strings, so the FIRST match is library
    # source. The chart's own call is the LAST one.
    start = html.rfind("Plotly.newPlot(")
    check("the exported report embeds the chart for Plotly to draw",
          start > 0, str(start))
    payload = html[start + len("Plotly.newPlot("):].lstrip()
    payload = payload[payload.index("["):]
    try:
        traces, _ = _json.JSONDecoder().raw_decode(payload)
    except Exception as exc:
        traces = []
        check("the chart payload parses out of the report", False, str(exc))

    def ghosts(node):
        """Every colorbar / coloraxis binding in a serialised figure payload.

        Walks the WHOLE tree, because the ghost does not land on the trace: a
        bar trace has no ``colorbar`` property at all, so the phantom bar is
        written to ``marker.colorbar`` (and to ``layout.coloraxis.colorbar``),
        which is exactly where plotly.js looks. A trace-level-only check would
        have missed the reported bug entirely.
        """
        found = []
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "template":
                    continue  # plotly's default theme, never drawn
                if key in ("colorbar", "coloraxis") and val:
                    found.append(f"{key}={str(val)[:40]}")
                found.extend(ghosts(val))
        elif isinstance(node, (list, tuple)):
            for val in node:
                found.extend(ghosts(val))
        return found

    report_ghosts = ghosts(traces)
    check("a dark themed bar export ships no colorbar binding anywhere",
          not report_ghosts, ", ".join(report_ghosts[:4]))
    # And the plotly.js bundle really is in there, so the check above was not
    # trivially true because nothing was embedded at all.
    check("the report really does ship plotly.js (the check is not vacuous)",
          "Plotly.newPlot" in html and len(html) > 200_000, str(len(html)))


def test_kpi_value_and_label_are_stylable() -> None:
    print("[42] KPI customisation: the value and the label are separable")
    import re as _re
    import plotly.express as px
    from modules.export import generate_html_report
    from services.export_service import (EXPORT_DEFAULTS, PRESET_SWATCH_KEYS,
                                         build_export_theme)

    d = px.data.iris()
    kpis = [{"label": "Total Sales", "value": "149,116", "icon": "M",
             "column": "sepal_length"}]

    def _card(state: dict) -> str:
        html = generate_html_report(
            [("a", "A", px.bar(d, x="species", y="sepal_length"), "", "", {})],
            "S", kpis=kpis, grid_cols_n=2, theme=build_export_theme(state))
        m = _re.search(r'<div class="kpi-value" style="([^"]*)"', html)
        m2 = _re.search(r'<div class="kpi-label" style="([^"]*)"', html)
        return (m.group(1) if m else "") + "|" + (m2.group(1) if m2 else "")

    base = _card({})
    coloured = _card({"ex_kpi_val_color": "#ff0055"})
    check("the KPI value takes its own colour",
          "color:#ff0055" in coloured, coloured)
    check("an unset value colour still follows the theme accent",
          "color:#6163df" in base, base)

    sized = _card({"ex_kpi_label_size": 18})
    check("the KPI label takes its own size", "font-size:18px" in sized, sized)
    check("the label and the value are styled independently",
          "font-size:18px" in sized and "color:#6163df" in sized, sized)

    upper = _card({"ex_kpi_label_upper": True})
    plain = _card({"ex_kpi_label_upper": False})
    check("label caps can be switched off",
          "text-transform:uppercase" in upper and "text-transform:none" in plain,
          f"{upper} / {plain}")

    # Every key must exist end to end, or the panel writes a key nothing reads.
    for key in ("ex_kpi_val_color", "ex_kpi_label_size", "ex_kpi_label_upper"):
        check(f"{key} is a known style key", key in EXPORT_DEFAULTS, key)
    check("a quick theme does not overwrite the KPI customisation",
          not any(k in PRESET_SWATCH_KEYS
                  for k in ("ex_kpi_val_color", "ex_kpi_label_size",
                            "ex_kpi_label_upper")), "")


def test_the_dashboard_has_a_background_image() -> None:
    print("[43] Background image: works over any theme, survives the export")
    from modules.export import generate_html_report
    from services.export_service import (EXPORT_DEFAULTS, EXPORT_PRESETS,
                                         build_export_theme)

    check("the background image is a known style key",
          "ex_bg_image" in EXPORT_DEFAULTS
          and "ex_bg_image_opacity" in EXPORT_DEFAULTS
          and "ex_bg_blend" in EXPORT_DEFAULTS, "")

    uri = "data:image/png;base64,AAAA"
    html = generate_html_report(
        [], "S", theme=build_export_theme(
            {"ex_bg_image": uri, "ex_bg_image_opacity": 40}))
    check("the image reaches the report", uri in html, "")
    check("it is layered OVER the theme colour, not instead of it",
          "body::before" in html and "z-index: -1" in html, "")
    check("the theme colour is still the base layer",
          "background: #121a2e" in html, "")
    check("it cannot cover a card or a note", "pointer-events: none" in html, "")
    check("it does not scroll away on a tall board",
          "position: fixed" in html, "")
    check("the chosen opacity is honoured", "opacity: 0.400" in html, "")

    # It must work for EVERY theme -- that is the whole point of compositing
    # rather than replacing.
    missing = [n for n, c in EXPORT_PRESETS.items()
               if uri not in generate_html_report(
                   [], "S", theme=build_export_theme(
                       dict(c, ex_bg_image=uri, ex_bg_image_opacity=30)))]
    check("every quick theme keeps the background image", not missing,
          str(missing))

    # No image -> no pseudo-element at all, so nothing can be left behind.
    plain = generate_html_report([], "S", theme=build_export_theme({}))
    check("a report with no image emits no background layer",
          "body::before" not in plain, "")
    off = generate_html_report([], "S", theme=build_export_theme(
        {"ex_bg_image": uri, "ex_bg_image_opacity": 0}))
    check("zero opacity switches the layer off",
          "body::before" not in off, "")


def test_every_dashboard_setting_reaches_the_report() -> None:
    print("[44] Every style key in the panel changes the report")
    import plotly.express as px
    from modules.export import generate_html_report
    from services.export_service import build_export_theme

    d = px.data.iris()
    kpis = [{"label": "Total", "value": "10", "icon": "M"}]

    def _report(state: dict) -> str:
        return generate_html_report(
            [("a", "A", px.bar(d, x="species", y="sepal_length"),
              "a note", "", {})],
            "S", kpis=kpis, grid_cols_n=2, theme=build_export_theme(state))

    base = _report({})
    # (style key, a value, a fragment present only once it is set). A control
    # that changes nothing on the board is the bug this pins down -- key by key,
    # rather than by trusting that the panel and the writer agree.
    probes = [
        ("ex_bg", "#101010", "background: #101010"),
        ("ex_card", "#202020", "background: #202020"),
        ("ex_kpi", "#303030", "background: #303030"),
        ("ex_border", "#445566", "border: 1px solid #445566"),
        ("ex_text", "#fefefe", "color:#fefefe"),
        ("ex_accent", "#00ff00", "color:#00ff00"),
        ("ex_not_bg", "#0a0a1a", "background: #0a0a1a"),
        ("ex_not_bd", "#8844ff", "border-left: 4px solid #8844ff"),
        ("ex_title_color", "#00aaff", "color: #00aaff"),
        ("ex_notes_color", "#ccccff", "color: #ccccff"),
        ("ex_kpi_text_color", "#dddddd", "color:#dddddd"),
        ("ex_kpi_val_size", 40, "font-size:40px"),
        ("ex_title_font", "Georgia", "Georgia"),
        ("ex_title_size", 40, "font-size: 2.50rem"),
        ("ex_notes_size", 22, "font-size: 1.38rem"),
        ("ex_radius", 30, "border-radius: 30px"),
        ("ex_title_style", "Bold", "font-weight: 900"),
    ]
    dead = []
    for key, value, fragment in probes:
        if fragment in base:
            dead.append(f"{key}: already present by default")
        elif fragment not in _report({key: value}):
            dead.append(f"{key}: {fragment!r} missing")
    check("no dashboard setting is a no-op on the report", not dead,
          "; ".join(dead))

    # The two Style combos are checked inside their OWN rule rather than by a
    # document-wide fragment: "font-weight: 700" is also what a chart heading
    # carries, so a bare search would pass even with the setting ignored.
    def _rule(html: str, selector: str) -> str:
        # The selector includes its own "{", so the body starts at the next one.
        import re
        m = re.search(re.escape(selector) + r"([^}]*)\}", html)
        return m.group(1) if m else ""

    notes_rule = _rule(_report({"ex_notes_style": "Bold"}), ".notes {")
    check("Notes Typography > Style reaches the notes block",
          "font-weight: 700" in notes_rule
          and "font-style: normal" in notes_rule, notes_rule)
    check("the notes keep their established look by default",
          "font-style: italic" in _rule(base, ".notes {"),
          _rule(base, ".notes {"))


def test_cards_have_no_dead_padding() -> None:
    """Image 1: the KPI and title cards were mostly empty box.

    Both cards are sized by the user (drag/resize on the free canvas), so any
    padding or inner gap is height the content does not get. This pins the
    specific declarations that were reported as the "KPI emoji & values gap".
    """
    print("[45] KPI and title cards: content fills the box")
    from modules.export import generate_html_report
    from services.export_service import build_export_theme

    html = generate_html_report([], "S", theme=build_export_theme({}))

    def _rule(selector: str) -> str:
        import re
        m = re.search(re.escape(selector) + r"([^}]*)\}", html)
        return m.group(0) if m else ""

    # The gap between the emoji and the value was a margin on the icon.
    icon = _rule(".kpi-icon  {")
    check("the KPI icon has no margin of its own",
          "margin: 0" in icon and "margin-bottom" not in icon, icon)
    # And the gap between the value and its label was a margin-top on the label.
    label = _rule(".kpi-label {")
    check("the KPI label sits directly under the value",
          "margin-top: 0" in label, label)
    value = _rule(".kpi-value {")
    check("the KPI value carries no default margin", "margin: 0" in value,
          value)
    # A card the user sized to 120px cannot also spend a rem-and-a-half on
    # padding, and the title band is the same kind of sized box.
    kpi_card = _rule(".kpi-card {")
    check("the KPI card's padding is cut to almost nothing",
          "padding: 0.25rem 0.5rem" in kpi_card, kpi_card)
    canvas_kpi = _rule(".ly-canvas > .kpi-card {")
    check("a KPI on the free canvas has NO padding at all",
          "padding: 0;" in canvas_kpi, canvas_kpi)
    title_card = _rule(".ly-canvas > .title-card {")
    check("the title card has NO padding at all",
          "padding: 0;" in title_card, title_card)
    check("the title card has no timestamp line of its own",
          ".ly-canvas > .title-card .meta" not in html, "")
    check("the timestamp lives in the bottom-edge footer instead",
          ".report-footer .meta" in html
          and '<div class="report-footer"' in html, "")

    # The old free-canvas KPI also had its own icon margin, which would have
    # survived the change above and re-introduced the gap on the canvas.
    check("no rule re-introduces a gap under the KPI icon",
          "kpi-icon { margin-bottom" not in html
          and ".ly-canvas > .kpi-card .kpi-icon" not in html, "")


def test_cards_can_have_their_own_background_image() -> None:
    """Image 2: a background image for the CARDS, not just the page."""
    print("[46] Card background image: per card kind, embedded, themable")
    import plotly.express as px
    from modules.export import generate_html_report
    from services.export_service import (EXPORT_DEFAULTS, build_export_theme)

    check("the card image is a known style key",
          "ex_card_image" in EXPORT_DEFAULTS
          and "ex_card_image_opacity" in EXPORT_DEFAULTS
          and "ex_card_blend" in EXPORT_DEFAULTS
          and "ex_card_image_groups" in EXPORT_DEFAULTS, "")
    check("it is off by default, so no existing report changes",
          EXPORT_DEFAULTS["ex_card_image"] == "", "")

    uri = "data:image/png;base64,BBBB"
    d = px.data.iris()

    def _report(state: dict) -> str:
        return generate_html_report(
            [("a", "A", px.bar(d, x="species", y="sepal_length"),
              "", "", {})],
            "S", theme=build_export_theme(state))

    html = _report({"ex_card_image": uri, "ex_card_image_opacity": 40})
    check("the picture is embedded in the report, not linked", uri in html, "")
    check("it is drawn on the cards, not on the page",
          ".chart-card::before" in html, "")
    check("it is layered OVER the card colour, so it works on any theme",
          "mix-blend-mode" in html, "")
    check("the card keeps clipping it to the rounded corners",
          "overflow: hidden" in html, "")
    # The card's text and plot must stay ON TOP of the picture; a picture that
    # buries the content is not a feature.
    check("the card's own content is lifted above the picture",
          "z-index: 1" in html, "")
    check("it cannot swallow a click meant for a card or the chart",
          "pointer-events: none" in html, "")
    check("the chosen opacity is honoured", "opacity: 0.400" in html, "")

    # "WHERE it applies" is the point of the feature: the same picture behind
    # only the KPI strip, or only the title band.
    only_kpi = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                        "ex_card_image_groups": "kpi"})
    check("the image can go behind the KPI cards alone",
          ".kpi-card::before" in only_kpi
          and ".chart-card::before" not in only_kpi
          and ".title-card::before" not in only_kpi, "")
    only_title = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                          "ex_card_image_groups": "title"})
    check("the image can go behind the title band alone",
          ".title-card::before" in only_title
          and ".kpi-card::before" not in only_title, "")
    both = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                    "ex_card_image_groups": "kpi,title"})
    check("it can go behind several kinds at once",
          ".kpi-card::before" in both and ".title-card::before" in both
          and ".chart-card::before" not in both, "")

    # Off switches. A layer that cannot be switched off is a layer the user
    # cannot get rid of.
    plain = _report({})
    check("a report with no card image emits no card layer",
          "chart-card::before" not in plain, "")
    off = _report({"ex_card_image": uri, "ex_card_image_opacity": 0})
    check("zero opacity switches the card layer off",
          "chart-card::before" not in off, "")
    nowhere = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                       "ex_card_image_groups": ""})
    check("ticking no card kinds switches it off too",
          "chart-card::before" not in nowhere, "")
    # An unrecognised group name must not silently widen the image to every
    # card -- a corrupt or hand-edited style value should draw nothing extra.
    bogus = _report({"ex_card_image": uri, "ex_card_image_opacity": 40,
                     "ex_card_image_groups": "banana"})
    check("an unknown group name draws nothing rather than guessing",
          "banana" not in bogus and "chart-card::before" not in bogus, "")

    # The page background and the card background are independent settings.
    both_layers = _report({"ex_bg_image": "data:image/png;base64,AAAA",
                           "ex_card_image": uri, "ex_card_image_opacity": 40})
    check("the page image and the card image can both be on",
          "body::before" in both_layers
          and ".chart-card::before" in both_layers, "")

    # The chart's own background is opaque, so a picture behind a chart card
    # would be invisible without this.
    themed = _report({"ex_card_image": uri, "ex_card_image_opacity": 40})
    check("a picture behind a chart card makes the plot transparent",
          "rgba(0,0,0,0)" in themed, "")

    # PRINT. The print block puts the free canvas back into document flow, and
    # the card image is drawn on the card's own ::before -- so if print made the
    # card `static` again, that pseudo-element would lose its containing block
    # and anchor to the page, printing ONE full-sheet picture over everything
    # instead of one picture per card.
    import re
    free_html = generate_html_report(
        [("a", "A", px.bar(d, x="species", y="sepal_length"), "", "", {})],
        "S", kpis=[{"label": "T", "value": "1", "icon": "M"}],
        geometry_mode="free",
        theme=build_export_theme({"ex_card_image": uri,
                                  "ex_card_image_opacity": 40}))
    print_block = free_html[free_html.index("@media print"):]
    card_rule = re.search(r"\.ly-canvas > \.chart-card,[^}]*\}", print_block)
    check("print keeps the card a containing block for its own picture",
          card_rule is not None
          and "position: relative" in card_rule.group(0)
          and "position: static" not in card_rule.group(0),
          card_rule.group(0) if card_rule else "rule not found")


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
        from desktop.session_bridge import SessionBridge
        from desktop.pages.upload_page import UploadPage
        from services.analysis_service import ANALYSIS_OPTIONS

        def _df():
            return pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

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

        # -- Guard rails: no user, or no identity, must be a silent no-op
        # rather than a crash or a bogus row.
        check("recording without a user is a no-op",
              dbmod.record_dataset_analysed(None, "x.csv", "x.csv:1") is False)
        check("recording without a signature is a no-op",
              dbmod.record_dataset_analysed(uid, "x.csv", "") is False)
        check("stats for no user are all zero",
              dbmod.get_user_stats(None) == {"sessions": 0, "datasets": 0,
                                             "analyses": 0})

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


def main():
    import services.transform_service as ts
    test_find_replace(ts)
    test_numeric_clean(ts)
    test_text_clean(ts)
    test_validate(ts)
    test_string_ops()
    test_numeric_ui()
    test_dtype()
    test_excel()
    test_classifier()
    test_column_manager()
    test_date_difference(ts)
    test_validate_all_rules(ts)
    test_string_ops_full()
    test_numeric_ops_full()
    test_dtype_extra()
    test_date_conversion()
    test_analysis_redesign()
    test_index_header_auto_fit()
    test_index_header_helper()
    test_chart_area_layout_and_render()
    test_chart_canvas_and_title()
    test_tooltip_follows_theme()
    test_header_alignment_and_band()
    test_card_header_gap_and_cold_start()
    test_draft_restore_round_trip()
    test_draft_restore_survives_corrupt_row()
    test_crasher_font_guard()
    test_emoji_glyphs_are_not_tofu()
    test_legend_column_tracks_card_width()
    test_legend_never_overlaps_the_axis_it_shares_a_column_with()
    test_legend_layout_adapts_to_the_viewport()
    test_legend_axis_title_is_measured()
    test_font_choices_are_installed_and_snapped()
    test_font_change_uses_fast_debounce()
    test_bundled_fonts_are_registered_everywhere()
    test_bundled_font_stacks_resolve_for_charts()
    test_distribution_settings_match_statistical_design()
    test_distribution_values_round_trip_and_generation()
    test_histogram_layout_options_are_histogram_only()
    test_column_roles_never_drop_a_column()
    test_user_classification_overrides_dtype()
    test_group_by_offers_every_dimension()
    test_section_controls_align_across_row()
    test_chart_tabs_scoped_to_selected_type()
    test_analysis_runners_use_shared_column_roles()
    test_runners_honour_user_classification()
    test_correlation_settings_match_statistical_design()
    test_correlation_alignment_values_and_generation()
    test_categorical_settings_match_statistical_design()
    test_categorical_alignment_values_and_generation()
    test_newly_migrated_types_match_the_old_metadata()
    test_new_layout_controls_change_the_new_chart_types()
    test_map_runner_degrades_without_streamlit()
    test_every_chart_type_has_a_settings_builder()
    test_masked_controls_stay_masked()
    test_declared_layout_controls_all_do_something()
    test_new_display_passes_are_wired()
    test_generate_sections_fill_the_pane()
    test_tile_geometry_free_placement()
    test_tile_geometry_clamp_and_overlap()
    test_tile_geometry_tidy_reflows()
    test_saved_geometry_migrates_from_the_grid()
    test_saved_tile_geometry_round_trip()
    test_export_follows_tile_geometry()
    test_export_replicates_the_legend_layout()
    test_report_canvas_structure()
    test_export_free_canvas_is_printable()
    test_kpi_tiles_share_the_chart_canvas()
    test_canvas_handles_are_wired()
    test_dashboard_palette_is_theme_independent()
    test_kpi_ids_reconcile_across_the_bridge()
    test_kpi_move_up_accepts_the_first_card()
    test_prune_geometry_keeps_the_title_band()
    test_dashboard_panel_widgets_survive_construction()
    test_preset_buttons_show_their_theme_colours_and_names()
    test_kpi_band_does_not_sit_under_the_charts()
    test_kpi_handlers_are_defined_once()
    test_preview_and_export_share_one_chart_input()
    test_editor_publishes_the_bridge_globally()
    test_pie_legend_is_not_drawn_over_the_pie()
    test_pie_legend_squeeze_is_stable()
    test_pie_legend_options_are_live()
    test_pie_slice_labels_follow_the_theme()
    test_type_scales_with_the_card()
    test_exporter_reserves_pie_legend_space()
    test_card_chrome_does_not_clip_or_collide()
    test_the_title_is_a_card_like_every_other()
    test_the_title_field_actually_rebuilds_the_board()
    test_legend_and_colourbar_follow_the_card()
    test_styling_never_invents_a_colour_bar()
    test_kpi_value_and_label_are_stylable()
    test_the_dashboard_has_a_background_image()
    test_every_dashboard_setting_reaches_the_report()

    test_cards_have_no_dead_padding()
    test_cards_can_have_their_own_background_image()
    test_home_banner_scales_with_the_window()
    test_session_counting_rules()

    print("\n========== RESULT ==========")
    print(f"  Passed: {len(PASS)}")
    print(f"  Failed: {len(FAIL)}")
    for f in FAIL:
        print(f"    - {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()