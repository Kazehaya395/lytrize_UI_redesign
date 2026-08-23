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

    print("\n========== RESULT ==========")
    print(f"  Passed: {len(PASS)}")
    print(f"  Failed: {len(FAIL)}")
    for f in FAIL:
        print(f"    - {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()