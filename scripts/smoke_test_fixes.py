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

    print("\n========== RESULT ==========")
    print(f"  Passed: {len(PASS)}")
    print(f"  Failed: {len(FAIL)}")
    for f in FAIL:
        print(f"    - {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()