"""desktop/widgets/slicer_dialog.py -- The "Add / Edit Slicer" form.

The slicer itself is a card on the board, rendered by the exporter exactly like
a KPI. This is the form that decides what one *is*.

Two decisions worth stating, because both were the easy thing to get wrong:

**The widget list is computed per column.** ``slicer_service.available_widgets``
answers it from ``column_roles``, so a ``category`` column -- what
``optimize_dtypes`` produces for low-cardinality text, and therefore the columns
users most want to filter on -- offers list/dropdown/search rather than nothing.
A picker built on ``select_dtypes("object")`` would have shown an empty list for
exactly those columns. A slider is offered only when the column has a real
range, since a handle that cannot travel is not a control.

**Scope is one control, not two code paths.** "Whole report" and "One chart"
are the same object with a different ``scope`` field, so a slicer created
against one chart can be widened to the whole board by editing a combo, without
being rebuilt.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from services import slicer_service as slicer_svc
from services.filter_service import distinct_values, filterable_columns, numeric_bounds

log = logging.getLogger(__name__)


def _field_row(label: str, widget: QWidget, tip: str = "") -> QWidget:
    """A label + control pair on one line, as a single hideable widget.

    Same shape as ``kpi_dialog._field_row``, for the same reason: hiding a
    FORM row leaves its caption behind, and an orphaned label is exactly what
    made the old settings read as cluttered.
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

class SlicerDialog(QDialog):
    """Build one slicer against the current dataset.

    Parameters
    ----------
    df : pd.DataFrame
        The dataset the slicer will filter.
    charts : list
        ``(uid, title, fig)`` tuples, offered as targets when the scope is
        "One chart". An empty list hides that row rather than showing a combo
        with nothing in it -- a board with no charts cannot host a
        chart-scoped slicer, and offering the choice would only invite a
        validation error on OK.
    existing : dict, optional
        A slicer to re-open rather than create.
    """

    def __init__(self, df: pd.DataFrame, parent=None, charts=None,
                 existing: Optional[dict] = None, *,
                 recipes: Optional[dict] = None,
                 preset_scope: Optional[str] = None,
                 preset_target: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Slicer" if existing else "Add Slicer")
        self.setMinimumWidth(520)
        self._df = df
        self._existing = dict(existing or {})
        self._charts = list(charts or [])
        # ``recipes`` maps uid -> chart meta. When present the dialog shows
        # a live "which charts will this reach" line computed by the SAME
        # affected_charts() the recompute path uses, so the hint cannot
        # drift from what a click will really re-run.
        self._recipes = dict(recipes or {})
        # The per-chart entry point passes its scope/target as presets; they
        # apply only to a NEW slicer -- an edit keeps its saved scope.
        self._preset_scope = preset_scope
        self._preset_target = preset_target

        # Dimensions first, then measures -- filterable_columns returns both
        # in one list on purpose (a slicer may be built on either), and the
        # order is a UX default: the categorical columns are what a dashboard
        # usually filters on.
        self._columns = filterable_columns(df)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.cb_column = QComboBox()
        self.cb_column.addItems(self._columns)
        self.cb_column.setToolTip(
            "The column this slicer filters on.\n"
            "Charts that plot it will re-run when the selection changes.")
        root.addWidget(_field_row("Column", self.cb_column))

        self.cb_widget = QComboBox()
        self.cb_widget.setToolTip(
            "How the value is picked. Only the kinds that can work on the\n"
            "chosen column are offered -- a slider needs a real range.")
        root.addWidget(_field_row("Control", self.cb_widget))

        # -- Scope ---------------------------------------------------------
        self.cb_scope = QComboBox()
        self.cb_scope.addItems(["Whole report", "One chart"])
        self.cb_scope.setToolTip(
            "\"Whole report\" filters every chart -- a report slicer applies\n"
            "to the whole board, even charts that never plot the column.\n"
            "\"One chart\" filters only the chart picked below.")
        root.addWidget(_field_row("Affects", self.cb_scope))

        self.cb_target = QComboBox()
        self.cb_target.addItem("", "")
        for uid, title, _fig in self._charts:
            self.cb_target.addItem(str(title or uid), str(uid))
        self._row_target = _field_row(
            "Chart", self.cb_target,
            "Only this chart is filtered; the rest of the board is untouched.")
        root.addWidget(self._row_target)

        # Live reach line ("Affects 3 of 8 charts ..."). Empty and hidden
        # until there is a column AND recipes to match against.
        self.lbl_affected = QLabel("")
        self.lbl_affected.setWordWrap(True)
        root.addWidget(self.lbl_affected)

        self.cb_topn = QSpinBox()
        self.cb_topn.setRange(1, 1000)
        self.cb_topn.setValue(10)
        self._row_topn = _field_row(
            "Top N", self.cb_topn,
            "How many of the largest (or smallest) members to keep.")
        root.addWidget(self._row_topn)

        self.cb_least = QComboBox()
        self.cb_least.addItems(["Largest", "Smallest"])
        self._row_least = _field_row("Keep", self.cb_least)
        root.addWidget(self._row_least)

        self.le_label = QLineEdit()
        self.le_label.setPlaceholderText("Leave blank to use the column name")
        root.addWidget(_field_row("Card title", self.le_label,
                                  "The heading shown on the slicer card."))

        root.addWidget(self._build_preview())

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = buttons.button(QDialogButtonBox.Ok)
        ok.setText("Update Slicer" if existing else "Add Slicer")
        ok.setProperty("kind", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # Wired EXACTLY once. A second _wire() here connected every signal
        # handler twice, so each change ran twice per gesture -- the sort of
        # duplication that only ever showed up as weird behaviour later.
        self._wire()
        self._load_existing()
        self._apply_presets()
        self._on_column_changed(self.cb_column.currentText())

    # -- Construction helpers ---------------------------------------------

    def _build_preview(self) -> QWidget:
        """What the control will offer, read from the live dataset.

        Shown before the card exists, so a column with no usable values reads
        as such here rather than as an empty card on the board.
        """
        box = QFrame()
        box.setObjectName("slicer_preview")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        head = QLabel("Values this control will offer")
        head.setStyleSheet("font-size: 11px; opacity: 0.7;")
        self.lbl_preview = QLabel("--")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setStyleSheet("font-size: 12px;")
        lay.addWidget(head)
        lay.addWidget(self.lbl_preview)
        return box

    def _wire(self) -> None:
        self.cb_column.currentTextChanged.connect(self._on_column_changed)
        self.cb_widget.currentTextChanged.connect(lambda _t: self._refresh())
        self.cb_scope.currentTextChanged.connect(self._on_scope_changed)
        self.cb_target.currentIndexChanged.connect(
            lambda _i: self._update_affected())
        self.cb_topn.valueChanged.connect(lambda _v: self._refresh())
        self.cb_least.currentTextChanged.connect(lambda _t: self._refresh())
        self.le_label.textChanged.connect(lambda _t: self._refresh())

    @staticmethod
    def _fill(combo: QComboBox, items: list) -> None:
        """Repopulate a combo, keeping the current value when it survives.

        The same guard ``kpi_dialog._fill`` carries: a slicer saved against
        another dataset holds a column that may no longer exist, and silently
        jumping to whatever sorts first would rewrite the user's choice on
        every re-open.
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

    def _on_column_changed(self, column: str) -> None:
        """Re-derive the widget choices for the selected column."""
        if not column:
            self.cb_widget.clear()
            self._refresh()
            self._update_affected()
            return
        try:
            kinds = slicer_svc.available_widgets(self._df, column)
        except Exception:
            log.debug("available_widgets failed for %r", column, exc_info=True)
            kinds = ["list"]
        current = self.cb_widget.currentText()
        # A stored widget the new column cannot support would produce a slicer
        # that cannot function, so it is dropped rather than kept and silently
        # inert. The first offered kind is the sensible default.
        self._fill(self.cb_widget, kinds)
        if current not in kinds:
            self.cb_widget.setCurrentText(kinds[0] if kinds else "")
        self._refresh()
        self._update_affected()

    def _on_scope_changed(self, scope: str) -> None:
        one_chart = str(scope).strip().lower() == "one chart"
        self._row_target.setVisible(one_chart)
        self._update_affected()

    def _apply_presets(self) -> None:
        """Pre-scoped values for the per-chart entry point.

        Only on a NEW form: an edit must keep its saved scope/target, while
        a fresh chart-scoped slicer starts already pointing at the chart the
        card's ⊞ button was pressed on.
        """
        if self._existing:
            return
        if self._preset_scope:
            self.cb_scope.setCurrentText(str(self._preset_scope))
        if self._preset_target:
            idx = self.cb_target.findData(str(self._preset_target))
            if idx >= 0:
                self.cb_target.setCurrentIndex(idx)
        self._on_scope_changed(self.cb_scope.currentText())

    def _update_affected(self) -> None:
        """Live "which charts will this hit" line under the scope row.

        Computed with ``slicer_service.affected_charts`` -- the SAME function
        the recompute path uses -- so the count cannot drift from what a
        click will really re-run. The draft carries a placeholder selection
        so it reads as ACTIVE: the hint is about REACH, and an as-yet-unticked
        new slicer would otherwise always say "affects 0 charts".
        """
        lbl = getattr(self, "lbl_affected", None)
        if lbl is None:
            return
        column = self.cb_column.currentText().strip()
        one_chart = self.cb_scope.currentText() == "One chart"
        if not column or not self._recipes:
            lbl.setVisible(False)
            return
        draft = {
            # No "kind": slicer_spec() derives it from "widget" via
            # filter_kind() -- the same route make_slicer takes, so the hint
            # sees exactly what a real slicer of this shape would be.
            "widget": self.cb_widget.currentText() or "list",
            "column": column,
            # One placeholder per kind; slicer_spec() reads only the fields
            # its kind declares, and slicer_is_active() just needs one.
            "values": ["__draft__"], "low": 0, "high": 1,
            "value": "__draft__", "n": 1,
            "scope": "chart" if one_chart else "report",
            "target": str(self.cb_target.currentData() or ""),
        }
        try:
            hits = slicer_svc.affected_charts(self._recipes, [draft])
        except Exception:
            log.debug("affected_charts failed for %r", column, exc_info=True)
            lbl.setVisible(False)
            return
        if one_chart:
            if not draft["target"]:
                text = "Pick the one chart this slicer will filter."
            else:
                title = next((str(t or u) for u, t, _f in self._charts
                              if u == draft["target"]), draft["target"])
                text = f"Filters 1 chart: {title}"
        else:
            text = (f"Filters all {len(hits)} charts "
                    f"(a whole-report slicer applies to every chart, "
                    f"not just those plotting {column}).")
        lbl.setText(text)
        lbl.setVisible(True)
    def _load_existing(self) -> None:
        """Put a saved slicer back on the form."""
        s = self._existing
        if not s:
            return
        column = str(s.get("column") or "")
        if column and column in self._columns:
            self.cb_column.setCurrentText(column)
        widget = str(s.get("widget") or "")
        if widget:
            self.cb_widget.setCurrentText(widget)
        self.cb_scope.setCurrentText(
            "One chart" if str(s.get("scope")) == "chart" else "Whole report")
        target = str(s.get("target") or "")
        if target:
            idx = self.cb_target.findData(target)
            if idx >= 0:
                self.cb_target.setCurrentIndex(idx)
        spec = s.get("spec") or {}
        if spec.get("n"):
            self.cb_topn.setValue(int(spec["n"]))
        if spec.get("least"):
            self.cb_least.setCurrentText("Smallest")
        if s.get("label"):
            self.le_label.setText(str(s["label"]))

    def _current_args(self) -> dict:
        """The form's values, named as ``make_slicer`` takes them."""
        one_chart = self.cb_scope.currentText() == "One chart"
        return {
            "column": self.cb_column.currentText().strip(),
            "widget_kind": self.cb_widget.currentText().strip() or "list",
            "scope": "chart" if one_chart else "report",
            # Read only when the row is actually in play: a "Whole report"
            # slicer must not inherit a stale target from an earlier edit, or
            # it would narrow itself to one chart while claiming not to.
            "target": (self.cb_target.currentData() or "") if one_chart else "",
            "label": self.le_label.text().strip(),
            "n": int(self.cb_topn.value()),
            "least": self.cb_least.currentText() == "Smallest",
        }

    def _refresh(self) -> None:
        """Update the value preview.

        Never raises: a preview that cannot compute must not stop the dialog
        from opening, because every other field on it may still be usable.
        """
        try:
            column = self.cb_column.currentText()
            if not column:
                self.lbl_preview.setText("--")
                return
            widget = self.cb_widget.currentText().lower()
            if widget == "slider":
                bounds = numeric_bounds(self._df, column)
                if bounds:
                    self.lbl_preview.setText(
                        f"Range {bounds.get('min')} - {bounds.get('max')}")
                else:
                    self.lbl_preview.setText("No usable range on this column")
                return
            if widget == "top_n":
                self.lbl_preview.setText(
                    f"Top {int(self.cb_topn.value())} by {column} "
                    f"({self.cb_least.currentText().lower()})")
                return
            values = distinct_values(self._df, column)
            if not values:
                self.lbl_preview.setText("No values in this column")
                return
            shown = [str(v) for v in values[:8]]
            text = ", ".join(shown)
            more = len(values) - len(shown)
            if more > 0:
                text += f" ... +{more} more"
            self.lbl_preview.setText(text)
        except Exception:
            log.debug("slicer preview failed", exc_info=True)
            self.lbl_preview.setText("--")

    def result_slicer(self) -> dict:
        """The finished slicer definition, ready to store.

        Bounds are attached for the slider because the card renders its
        ``min``/``max`` from the definition rather than re-reading the dataset
        -- the exporter is deliberately headless and receives no DataFrame, so
        a slicer stored without its bounds would render an unbounded slider.
        """
        args = self._current_args()
        slicer = slicer_svc.make_slicer(**args)
        if args["widget_kind"] == "slider":
            bounds = numeric_bounds(self._df, args["column"])
            if bounds:
                slicer["spec"]["low"] = bounds.get("min")
                slicer["spec"]["high"] = bounds.get("max")
        return slicer

        self._refresh()
