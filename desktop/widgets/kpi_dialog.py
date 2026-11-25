"""desktop/widgets/kpi_dialog.py -- The "Add / Edit KPI" form.

The old dashboard had a fixed three-field dialog (column, aggregation, label) so
only Sum/Average/Count/Min/Max were reachable. This is the type-driven form
that goes with ``services.kpi_service``: pick a KPI type and the form shows
exactly the fields that type needs.

Details that matter for correctness rather than looks:

* The value preview is recomputed on EVERY change, from the live dataset, so the
  user sees the number before committing and an input that cannot work is
  visibly wrong instead of silently producing "--".
* Rows are built as explicit label+field pairs rather than a QFormLayout,
  because showing and hiding a FORM row means hiding its label too; leaving
  labels behind is what produced the orphaned "Category:" captions that made the
  settings read as cluttered.
* The type, the fields and the calculation all come from one catalogue
  (``kpi_service.KPI_SPECS``), so a new KPI type needs no change here at all.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QColorDialog, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from services import kpi_service as kpi_svc

log = logging.getLogger(__name__)


class _KpiPreviewSignals(QObject):
    ready = Signal(int, object)


class _KpiPreviewTask(QRunnable):
    """Calculate one debounced KPI preview without blocking the dialog."""

    def __init__(self, revision: int, data, kpi_type: str, args: dict) -> None:
        super().__init__()
        self.revision = revision
        self.data = data
        self.kpi_type = kpi_type
        self.args = dict(args)
        self.signals = _KpiPreviewSignals()

    def run(self) -> None:
        try:
            value = kpi_svc.calc_kpi(self.data, self.kpi_type, **self.args)
        except Exception:
            log.debug("KPI preview worker failed", exc_info=True)
            value = {"icon": "", "label": "", "value": "Err: calculation failed",
                     "prefix": "", "suffix": ""}
        self.signals.ready.emit(self.revision, value)


def _field_row(label: str, widget: QWidget, tip: str = "") -> QWidget:
    """A label + control pair on one line, as a single hideable widget.

    Returning one widget for the PAIR is what lets the caller show/hide a field
    without leaving its caption behind -- a QFormLayout row cannot be hidden as
    a unit, and an orphaned label is exactly the "cluttered settings" look.
    """
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    cap = QLabel(label)
    cap.setFixedWidth(96)
    cap.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    cap.setProperty("theme_role", "section")
    lay.addWidget(cap)
    lay.addWidget(widget, 1)
    if tip:
        row.setToolTip(tip)
        widget.setToolTip(tip)
    return row


class KpiDialog(QDialog):
    """Build one KPI from the current dataset.

    ``result_kpi()`` returns the dict produced by ``kpi_service.calc_kpi``,
    which carries the ``_recipe`` needed to recompute it later.
    """

    def __init__(self, df: pd.DataFrame, parent=None,
                 existing: Optional[dict] = None,
                 calculation_data=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit KPI" if existing else "Add KPI")
        self.setMinimumWidth(520)
        self._df = df
        self._calculation_data = (calculation_data
                                  if calculation_data is not None else df)
        self._existing = dict(existing or {})
        self._recipe = dict(self._existing.get("_recipe") or {})
        self._preview_revision = 0
        self._preview_active = False
        self._pending_preview = None
        self._last_preview = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._start_preview)

        self._num = kpi_svc.numeric_columns(df)
        self._cat = kpi_svc.category_columns(df)
        self._dates = kpi_svc.date_columns(df) or [str(c) for c in df.columns]
        self._any = [str(c) for c in df.columns]

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.cb_type = QComboBox()
        self.cb_type.addItems(kpi_svc.KPI_TYPES)
        self.cb_type.setToolTip("What the card measures. Each type asks\n"
                                "only for the fields it actually needs.")
        root.addWidget(_field_row("KPI type", self.cb_type))

        # One control per field name, built ONCE and shown/hidden as the type
        # changes. Rebuilding per type was the alternative, and it loses what the
        # user already typed when they change their mind about the type -- which
        # is a completely normal thing to do.
        self.cb_col = QComboBox()
        self.cb_group = QComboBox()
        self.cb_metric = QComboBox()
        self.cb_date = QComboBox()
        self.cb_value = QComboBox()
        self.cb_value.setEditable(True)
        self.le_period = QLineEdit()
        self.le_period.setPlaceholderText("e.g. 2024-01")
        self.le_formula = QLineEdit()
        self.le_formula.setPlaceholderText("`Revenue` / `Units`")
        self.le_label = QLineEdit()
        self.le_label.setPlaceholderText("Leave blank for an automatic label")

        self.rows = {
            "col": _field_row("Column", self.cb_col,
                              "The numeric column to measure."),
            "group_col": _field_row("Category", self.cb_group,
                                    "The column the measure is split by."),
            "metric_col": _field_row("Metric", self.cb_metric,
                                     "The numeric column to total per category."),
            "filter_col": _field_row("Date", self.cb_date,
                                     "The date column the periods are cut from."),
            "filter_val": _field_row("Value", self.cb_value,
                                     "The category whose share is shown."),
            "baseline_period": _field_row("Baseline", self.le_period,
                                          "The baseline month. The card reports "
                                          "the month AFTER it."),
            "formula": _field_row("Formula", self.le_formula,
                                  "Arithmetic over numeric columns. Wrap each "
                                  "column name in `backticks`."),
        }
        for r in self.rows.values():
            root.addWidget(r)
        root.addWidget(_field_row("Label", self.le_label))

        # Per-card appearance. These ride on the KPI dict's own ``style``
        # block and override the board-wide ex_kpi_* theme keys for THIS
        # card only; clearing them returns the card to the theme.
        self._style_override = {
            key: str(value)
            for key, value in (self._existing.get("style") or {}).items()
            if key in ("value_color", "label_color", "card_bg") and str(value)}
        self._style_buttons = {}
        style_box = QFrame()
        style_box.setObjectName("kpi_style_override")
        style_lay = QVBoxLayout(style_box)
        style_lay.setContentsMargins(12, 10, 12, 10)
        style_lay.setSpacing(4)
        style_cap = QLabel("Appearance — this card only")
        style_cap.setProperty("theme_role", "section")
        style_hint = QLabel("Colours that override the board theme for just "
                            "this card. Reset to go back to the theme.")
        style_hint.setStyleSheet("font-size: 11px; opacity: 0.7;")
        style_lay.addWidget(style_cap)
        style_lay.addWidget(style_hint)
        style_row = QHBoxLayout()
        style_row.setSpacing(10)
        for key, text in (("value_color", "Value"),
                          ("label_color", "Label"),
                          ("card_bg", "Card")):
            style_row.addWidget(self._make_swatch(key, text))
        style_row.addStretch()
        btn_reset_style = QPushButton("Reset")
        btn_reset_style.setToolTip(
            "Clear this card's colour overrides and follow the board theme.")
        btn_reset_style.clicked.connect(self._reset_style_override)
        style_row.addWidget(btn_reset_style)
        style_lay.addLayout(style_row)
        root.addWidget(style_box)

        root.addWidget(self._build_preview())

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = buttons.button(QDialogButtonBox.Ok)
        self._ok_button = ok
        ok.setEnabled(False)
        ok.setText("Update KPI" if existing else "Add KPI")
        ok.setProperty("kind", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._wire()
        self._load_existing()
        self._on_type_changed(self.cb_type.currentText())

    # -- Construction helpers ---------------------------------------------

    def _make_swatch(self, key: str, text: str) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        btn = QPushButton()
        btn.setFixedSize(44, 24)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(f"Pick a colour for this card's {text.lower()}.")
        btn.clicked.connect(lambda: self._pick_style_color(key))
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("font-size: 10px; opacity: 0.7;")
        v.addWidget(btn)
        v.addWidget(label)
        self._style_buttons[key] = btn
        self._refresh_swatch(key)
        return wrap

    def _refresh_swatch(self, key: str) -> None:
        btn = self._style_buttons[key]
        value = str(self._style_override.get(key) or "")
        if value:
            btn.setStyleSheet(
                f"background:{value}; border: 1px solid palette(mid);"
                " border-radius: 4px;")
            btn.setText("")
        else:
            btn.setStyleSheet(
                "background: transparent; border: 1px dashed palette(mid);"
                " border-radius: 4px; font-size: 10px;")
            btn.setText("Auto")

    def _pick_style_color(self, key: str) -> None:
        current = str(self._style_override.get(key) or "#ffffff")
        color = QColorDialog.getColor(QColor(current), self)
        if color.isValid():
            self._style_override[key] = color.name()
            self._refresh_swatch(key)

    def _reset_style_override(self) -> None:
        self._style_override.clear()
        for key in self._style_buttons:
            self._refresh_swatch(key)

    def _build_preview(self) -> QWidget:
        """The live preview: exactly what the card will show, before it exists.

        This is the difference between a form and a guessing game -- and it is
        why the preview reads the same fields the calculation does.
        """
        box = QFrame()
        box.setObjectName("kpi_preview")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        self.lbl_preview_icon = QLabel("")
        self.lbl_preview_value = QLabel("--")
        self.lbl_preview_label = QLabel("")
        for w, style in ((self.lbl_preview_icon, "font-size: 20px;"),
                         (self.lbl_preview_value,
                          "font-size: 26px; font-weight: 700;"),
                         (self.lbl_preview_label,
                          "font-size: 11px; opacity: 0.7;")):
            w.setAlignment(Qt.AlignCenter)
            w.setStyleSheet(style)
            lay.addWidget(w)
        return box

    def _wire(self) -> None:
        self.cb_type.currentTextChanged.connect(self._on_type_changed)
        self.cb_group.currentTextChanged.connect(self._on_group_changed)
        for w in (self.cb_col, self.cb_metric, self.cb_date, self.cb_value):
            w.currentTextChanged.connect(lambda _t: self._refresh())
        self.le_period.textChanged.connect(lambda _t: self._refresh())
        self.le_formula.textChanged.connect(lambda _t: self._refresh())

    @staticmethod
    def _fill(combo: QComboBox, items: list) -> None:
        """Repopulate a combo, keeping the current value when it survives.

        Silently jumping back to the first item is what made these pickers feel
        broken: a card saved against another dataset carries a column name that
        is no longer in the list, and every re-open moved the selection.
        """
        want = combo.currentText()
        texts = [str(i) for i in items]
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(texts)
        if want and want in texts:
            combo.setCurrentText(want)
        combo.blockSignals(False)

    # -- Behaviour ---------------------------------------------------------

    def _load_existing(self) -> None:
        """Open an existing card on the right form.

        A card saved by the old five-way dialog has no stored type at all, so
        the type is inferred from its label -- otherwise editing such a card
        would silently reset it to a sum.
        """
        r = self._recipe
        label = str(r.get("kpi_type") or "")
        if label in kpi_svc.KPI_TYPES:
            self.cb_type.setCurrentText(label)
            return
        text = str(self._existing.get("label", "")).lower()
        if text.startswith("avg") or text.startswith("mean"):
            self.cb_type.setCurrentText("Average (Mean)")
        elif text.startswith("min"):
            self.cb_type.setCurrentText("Minimum Value")
        elif text.startswith("max"):
            self.cb_type.setCurrentText("Maximum Value")
        elif text.startswith("count") or "record" in text:
            self.cb_type.setCurrentText("Count (Rows)")
        else:
            self.cb_type.setCurrentText("Total (Sum)")

    def _on_group_changed(self, _text: str) -> None:
        """Keep the value picker in step with the category picker.

        Distinct values are only meaningful for the chosen column, so the list
        is rebuilt from it. ``distinct_values`` caps the list, which is what
        keeps this cheap on a high-cardinality key column.
        """
        self._reload_values()
        self._refresh()

    def _reload_values(self) -> None:
        col = self.cb_group.currentText()
        if not col:
            self.cb_value.clear()
            return
        try:
            self._fill(self.cb_value, kpi_svc.distinct_values(self._df, col))
        except Exception:
            log.debug("could not list distinct values of %s", col, exc_info=True)
            self.cb_value.clear()

    def _on_type_changed(self, type_label: str) -> None:
        """Show exactly the fields this type needs, and refill them.

        Every field row exists for the whole dialog and is shown or hidden here,
        so switching types cannot leave a stale row behind with a control that
        no longer drives the calculation.
        """
        fields = kpi_svc.kpi_spec(type_label)["fields"]
        # One control per FIELD NAME, but three different COLUMN POOLS: a plain
        # metric wants numeric columns, a distinct-count or missing-count wants
        # any column, and a date range wants a date column. Choosing the pool
        # from whether the field is literally "col" is what made "Date Range"
        # default to a numeric column and parse it as 1970.
        for name, pool in (("col", self._num), ("col_any", self._any),
                           ("col_date", self._dates)):
            if name in fields:
                self._fill(self.cb_col, pool or self._any)
                break
        self._fill(self.cb_group, self._cat or self._any)
        self._fill(self.cb_metric, self._num)
        self._fill(self.cb_date, self._dates)

        # ``col`` / ``col_any`` / ``col_date`` are all the one Column row.
        wants_col = bool({"col", "col_any", "col_date"} & set(fields))
        self.rows["col"].setVisible(wants_col)
        for name, row in self.rows.items():
            if name == "col":
                continue
            row.setVisible(name in fields)
        if "group_col" in fields:
            self._reload_values()
        self._restore_recipe()
        self._refresh()

    def _restore_recipe(self) -> None:
        """Put a stored card's saved choices back after the type switch.

        Applied on EVERY type change, not just the first: the field lists are
        refilled from the dataset, so a saved column has to be re-selected each
        time or the form would open on whatever happens to sort first.
        """
        r = self._recipe
        if not r:
            return
        for combo, key in ((self.cb_col, "col"), (self.cb_group, "group_col"),
                           (self.cb_metric, "metric_col"),
                           (self.cb_date, "filter_col")):
            want = str(r.get(key) or "")
            if want and want in [combo.itemText(i) for i in range(combo.count())]:
                combo.setCurrentText(want)
        if r.get("filter_val") is not None:
            self.cb_value.setCurrentText(str(r["filter_val"]))
        if r.get("formula"):
            self.le_formula.setText(str(r["formula"]))
        if r.get("baseline_period"):
            self.le_period.setText(str(r["baseline_period"]))
        if r.get("label"):
            self.le_label.setText(str(r["label"]))

    def _current_args(self) -> dict:
        """The field values, read exactly as ``calc_kpi`` takes them.

        Empty text is sent as None, not "", so the service's own "was this left
        blank" checks work and the stored recipe stays clean.
        """
        def _text(w: QWidget) -> Optional[str]:
            v = w.text().strip() if isinstance(w, QLineEdit) else w.currentText().strip()
            return v or None
        return {
            "col": _text(self.cb_col),
            "group_col": _text(self.cb_group),
            "metric_col": _text(self.cb_metric),
            "filter_col": _text(self.cb_date),
            "filter_val": _text(self.cb_value),
            "formula": self.le_formula.text().strip(),
            "baseline_period": self.le_period.text().strip(),
            "label": _text(self.le_label),
        }

    def _refresh(self) -> None:
        """Queue a debounced preview and leave the Qt event loop free."""
        self._preview_revision += 1
        self._pending_preview = (
            self._preview_revision, self.cb_type.currentText(),
            self._current_args())
        self._last_preview = None
        self._ok_button.setEnabled(False)
        self.lbl_preview_value.setText("Calculating…")
        self._preview_timer.start()

    def _start_preview(self) -> None:
        if self._preview_active or self._pending_preview is None:
            return
        revision, kpi_type, args = self._pending_preview
        self._pending_preview = None
        self._preview_active = True
        task = _KpiPreviewTask(revision, self._calculation_data, kpi_type, args)
        task.signals.ready.connect(self._on_preview_ready)
        # Keep a reference until QRunnable finishes; QThreadPool owns execution
        # and the signal's QObject safely disconnects if the dialog is closed.
        self._preview_task = task
        QThreadPool.globalInstance().start(task)

    def _on_preview_ready(self, revision: int, kpi: dict) -> None:
        self._preview_active = False
        self._preview_task = None
        if revision == self._preview_revision:
            self._last_preview = (revision, kpi)
            self.lbl_preview_icon.setText(str(kpi.get("icon", "")))
            self.lbl_preview_value.setText("%s%s%s" % (
                kpi.get("prefix", ""), kpi.get("value", "--"),
                kpi.get("suffix", "")))
            self.lbl_preview_label.setText(str(kpi.get("label", "")))
            bad = str(kpi.get("value", "")).startswith("Err:")
            self.lbl_preview_value.setStyleSheet(
                "font-size: 26px; font-weight: 700; color: %s;"
                % ("#ef4444" if bad else ""))
            self._ok_button.setEnabled(True)
        if self._pending_preview is not None:
            self._preview_timer.start(0)

    def result_kpi(self) -> dict:
        """The finished card, ready to store. Includes its ``_recipe``."""
        if self._last_preview and self._last_preview[0] == self._preview_revision:
            out = dict(self._last_preview[1])
        else:
            out = kpi_svc.calc_kpi(self._calculation_data,
                                   self.cb_type.currentText(),
                                   **self._current_args())
        clean = {key: value for key, value in self._style_override.items()
                 if str(value)}
        if clean:
            out["style"] = clean
        else:
            out.pop("style", None)
        return out

