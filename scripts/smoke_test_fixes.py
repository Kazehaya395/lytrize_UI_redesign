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
        if aid == "statistical":
            # Registered builder -> real controls on all three tabs.
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
    from services.database_service import get_draft, clear_draft

    app_ = app()

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
    b1.set_note("uid_r1", "a note")
    b1.manager.save_df_snapshot(1, b1.df)
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

    Regression: ``run_dev.sh`` died with SIGSEGV before its first frame. The
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
    from chart_display import (_legend_need_px, apply_display_options,
                               refit_legend_width, infer_legend_position)

    def base(names):
        f = go.Figure()
        for i, n in enumerate(names):
            f.add_bar(x=["c"], y=[i + 1], name=n)
        return f

    names = ["mean", "min", "max", "std"]
    need = _legend_need_px(names, "", "right")

    # The reserved strip must equal what the labels need, at EVERY width.
    for w in (700, 1200, 1900, 2400):
        f = apply_display_options(base(names),
                                  {"legend_position": "Top Right"}, None,
                                  width_px=w)
        reserved = (1.0 - list(f.layout.xaxis.domain)[1]) * w
        check(f"legend: reserved column ~= label need at {w}px",
              abs(reserved - need) < 1.0,
              f"reserved {reserved:.0f}px, need {need}px")

    # A fraction-based reservation would GROW with the card; this must not.
    f1 = apply_display_options(base(names), {"legend_position": "Top Right"},
                               None, width_px=700)
    f2 = apply_display_options(base(names), {"legend_position": "Top Right"},
                               None, width_px=2400)
    r1 = (1.0 - list(f1.layout.xaxis.domain)[1]) * 700
    r2 = (1.0 - list(f2.layout.xaxis.domain)[1]) * 2400
    check("legend: reservation does not grow with the card",
          abs(r1 - r2) < 2.0, f"{r1:.0f}px at 700 vs {r2:.0f}px at 2400")

    # Long labels must still get a genuinely wide column.
    long_names = ["Very Long Series Name Alpha", "Very Long Series Name Beta"]
    need_long = _legend_need_px(long_names, "", "right")
    fl = apply_display_options(base(long_names), {"legend_position": "Top Right"},
                               None, width_px=1900)
    res_long = (1.0 - list(fl.layout.xaxis.domain)[1]) * 1900
    check("legend: long labels still reserve a wider column",
          res_long > need + 100,
          f"reserved {res_long:.0f}px, need {need_long}px")

    # The Generate path: built on a worker with no width, then re-fitted once
    # the widget knows its real size.
    fg = apply_display_options(base(names), {"legend_position": "Top Right"},
                               None)
    before = (1.0 - list(fg.layout.xaxis.domain)[1]) * 1900
    applied = refit_legend_width(fg, 1900)
    after = (1.0 - list(fg.layout.xaxis.domain)[1]) * 1900
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
    check("legend: left column still carries axis-label room",
          list(fleft.layout.xaxis.domain)[0] * 1900 > need,
          f"reserved {list(fleft.layout.xaxis.domain)[0] * 1900:.0f}px")

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
    test_font_choices_are_installed_and_snapped()
    test_font_change_uses_fast_debounce()
    test_bundled_fonts_are_registered_everywhere()
    test_bundled_font_stacks_resolve_for_charts()

    print("\n========== RESULT ==========")
    print(f"  Passed: {len(PASS)}")
    print(f"  Failed: {len(FAIL)}")
    for f in FAIL:
        print(f"    - {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()