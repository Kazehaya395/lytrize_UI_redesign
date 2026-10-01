"""Categorical bar settings -- follows the Statistical template exactly.

Design note (why this subclasses StatisticalSettings):
    Same approach as ``distribution.py`` / ``correlation.py``: the three tabs
    are built from the shared helpers with the shared constants, the
    type-agnostic rows come from the template, and only the tabs that
    genuinely differ are touched. See ``extra/CHART_IMPLEMENTATION_RULES.md``.

    * Generate -- rebuilt, because the categorical form has eight controls in
      the old panel's order (single-selects, not the template's multi-select
      metrics). They flow across the pane (``base.FlowGrid``): as many per
      line as fit, every line filled edge to edge, the remainder wrapped onto
      the next line at the left. The old panel's ``st.columns(8)`` did the
      same thing -- a fixed three-per-row grid filled only the left third of
      the tab and made the page taller than the pane it sat in.
    * Layout -- the template's page, plus two rows (dual-axis right label,
      Line width). Nothing is masked: this chart really does draw a legend
      (the Secondary Y-Axis overlay is a second series), and the legend /
      colour-bar / colour-scale settings are one shared setting across every
      chart that draws a key, so Legend title, Legend position and Show
      border stay.
    * Typography -- inherited unchanged, legend group included.

Feature reference (old codebase -- names/types/ranges/defaults only, never
the Streamlit grouping):

    Generate panel (``analysis/__init__.py``, the ``categorical`` branch of
    ``_render_config_panel_body``) renders eight columns in this order::

        Dimension | Metric | Aggregation | \\U0001f4ca Direction | Top N |
        Secondary Y-Axis | Secondary Agg | Palette

    ``_WIDGET_SPEC["categorical"]`` declares x/y as lists (the runner loops
    over dimensions x metrics and emits one chart per combination) and also
    declares ``sort`` -- but the old panel NEVER rendered a Sort control for
    this type (only pie_chart did), so like correlation's masked palette it is
    deliberately not built and not collected: a control the old UI never had
    is not a control this one may invent.

    ``CHART_TYPE_SETTINGS["categorical"]`` says ``has_axes=True`` and
    ``has_legend=False``, but that flag describes the old Streamlit panel, not
    the chart: the runner draws a SECOND series (the Secondary Y-Axis overlay)
    and Plotly puts a legend on screen for it, so the legend rows really do
    change this figure. Legend / colour-bar / colour-scale settings are one
    shared setting across the supported charts, so they are kept here; masking
    them was reported as "again you left legends setting ... what done in
    previous charts". Layout is therefore the template's page plus the two
    rows below (the dual-axis label and Line width), with nothing removed, and
    Typography is inherited as-is.

    ``_CONTROL_META``: ``line_width`` slider 1..10 default 2 step 1. It sizes
    the Secondary Y-Axis overlay line (the runner draws it with
    ``mode="lines+markers"``); a bar-only figure has no line trace, exactly as
    in the old app. ``bar_gap`` 0..1 default 0.28 -- the template's shared
    Bar gap row is kept as built (same default) so the sibling types stay on
    one slider.

    The old Layout panel shows THREE axis fields for this type --
    ``X-axis | Y-axis (left) | Y-axis (right)`` -- because it is dual-axis
    capable (old ``chart_settings`` renders the trio whenever
    ``is_dual_axis_chart(fig)``); the third field writes ``y2_label``.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QLineEdit, QSpinBox, QLabel
from .base import (hint_label, cap_widget, slider_with_readout, GEN_W,
                   HINT_GAP, page_root, section_box, grid_row, FlowGrid)
from .statistical import StatisticalSettings, NONE_SENTINEL

try:
    from services.analysis_service import AGG_FUNCS as _AGG_FUNCS
    AGGS = list(_AGG_FUNCS.keys())
except Exception:  # pragma: no cover - fallback keeps the form buildable
    _AGG_FUNCS = None
    AGGS = ['Avg', 'Sum', 'Median', 'Count', 'Min', 'Max']

# GEN_W (the compact Generate field width) comes from ``.base`` now, so the
# flow grid's minimum column width cannot drift from it.
# The old panel's Direction choices (verbatim labels).
DIRECTIONS = ['Vertical (Column chart)', 'Horizontal (Bar chart)']

class CategoricalSettings(StatisticalSettings):
    """Generate + Layout + Typography settings for the categorical type."""

    def _build(self):
        super()._build()
        # The template's x/y/pending shape does not describe this form: the
        # dimension and the metric are each a single column (the runner still
        # receives one-element lists, per WIDGET_SPEC), and the scalar
        # controls have no template equivalent at all.
        self._pending = {'x': [], 'y': [], 'agg': AGGS[0], 'top_n': 0,
                         'direction': DIRECTIONS[0], 'dual_y': '',
                         'dual_y_agg': AGGS[0], 'palette': None}

    # -- Generate ----------------------------------------------------------
    def _combo_section(self, title, hint, items, current, on_change):
        """One Generate slot: a 210px combo inside the shared section box."""
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(HINT_GAP)
        cb = QComboBox()
        cap_widget(cb, GEN_W)
        cb.addItems([str(i) for i in items])
        idx = cb.findText(str(current)) if current not in (None, '') else -1
        cb.setCurrentIndex(idx if idx >= 0 else 0)
        cb.currentTextChanged.connect(on_change)
        bl.addWidget(cb)
        bl.addStretch()
        return section_box(title, body, hint), cb

    def build_generate(self):
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label('Adjust options below. All selections are live — no submit needed until Generate.'))
        # Eight slots in the old panel's order (Palette last, as in every
        # sibling type), flowed across the pane: as many per line as fit, the
        # rest wrapped onto the next line at the left (``base.FlowGrid``).
        flow = FlowGrid()

        # -- Slots 1-3: Dimension | Metric | Aggregation --------------------
        # Dimension is required (the old selectbox had no "None" entry): it
        # is the column the chart is grouped by. Metric is optional -- the old
        # help text says so and the runner falls back to value counts -- so it
        # carries a BLANK first entry rather than the literal word "None".
        dims = list(self._group_cols or [])
        box, self._combo_dim = self._combo_section(
            'Dimension', 'Column to split the data by',
            dims if dims else [''], dims[0] if dims else '', self._on_dim)
        flow.add_section(box)
        nums = list(self._num_cols or [])
        metric_items = [NONE_SENTINEL] + nums
        box, self._combo_metric = self._combo_section(
            'Metric', 'Value to aggregate (optional)',
            metric_items, nums[0] if nums else NONE_SENTINEL, self._on_metric)
        flow.add_section(box)
        box, self._combo_agg = self._combo_section(
            'Aggregation', 'How to combine multiple metric values',
            AGGS, AGGS[0], self._on_agg)
        flow.add_section(box)

        # -- Slots 4-6: Direction | Top N | Secondary Y-Axis ----------------
        box, self._combo_dir = self._combo_section(
            '\U0001f4ca Direction', 'Vertical = column chart. Horizontal = bar chart.',
            DIRECTIONS, DIRECTIONS[0], self._on_direction)
        flow.add_section(box)
        tbox = QWidget()
        tl = QVBoxLayout(tbox)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(HINT_GAP)
        self._spin_topn = QSpinBox()
        cap_widget(self._spin_topn, GEN_W)
        self._spin_topn.setRange(*TOP_N_RANGE)
        self._spin_topn.setSingleStep(1)
        self._spin_topn.setValue(int(self._pending.get('top_n', 0) or 0))
        self._spin_topn.valueChanged.connect(self._on_topn)
        tl.addWidget(self._spin_topn)
        tl.addStretch()
        flow.add_section(section_box('Top N', tbox, 'Max bars to show (0 = show all)'))
        box, self._combo_dual = self._combo_section(
            'Secondary Y-Axis', 'None = disabled.',
            [NONE_SENTINEL] + nums, NONE_SENTINEL, self._on_dual_y)
        flow.add_section(box)

        # -- Slots 7-8: Secondary Agg | Palette -----------------------------
        box, self._combo_dual_agg = self._combo_section(
            'Secondary Agg', 'Aggregation for secondary Y-axis metric',
            AGGS, AGGS[0], self._on_dual_agg)
        flow.add_section(box)
        pals = list(self._palette_names or ['Default'])
        box, self._combo_palette = self._combo_section(
            '\U0001f3a8 Palette', '', pals,
            self._pending.get('palette') or pals[0], self._on_palette)
        flow.add_section(box)

        rl.addWidget(flow)
        rl.addStretch()
        # The combos above defaulted themselves; record what is on screen so
        # collect_values() agrees with the widgets even before any edit.
        self._sync_pending()
        return root

    def _sync_pending(self):
        """Pull every Generate control's current value into ``_pending``."""
        try:
            self._on_dim(self._combo_dim.currentText())
            self._on_metric(self._combo_metric.currentText())
            self._on_agg(self._combo_agg.currentText())
            self._on_direction(self._combo_dir.currentText())
            self._on_topn(int(self._spin_topn.value()))
            self._on_dual_y(self._combo_dual.currentText())
            self._on_dual_agg(self._combo_dual_agg.currentText())
            self._on_palette(self._combo_palette.currentText())
        except Exception:
            pass

    # -- Generate handlers -------------------------------------------------
    def _on_dim(self, text):
        self._pending['x'] = [text] if text else []
        self._mark_dirty(emit=True)

    def _on_metric(self, text):
        self._pending['y'] = [text] if text else []
        self._mark_dirty(emit=True)

    def _on_agg(self, text):
        self._pending['agg'] = text if text in AGGS else AGGS[0]
        self._mark_dirty(emit=True)

    def _on_topn(self, value):
        self._pending['top_n'] = int(value)
        self._mark_dirty(emit=True)

    def _on_direction(self, text):
        self._pending['direction'] = text if text in DIRECTIONS else DIRECTIONS[0]
        self._mark_dirty(emit=True)

    def _on_dual_y(self, text):
        self._pending['dual_y'] = text if text else ''
        self._mark_dirty(emit=True)

    def _on_dual_agg(self, text):
        self._pending['dual_y_agg'] = text if text in AGGS else AGGS[0]
        self._mark_dirty(emit=True)

    def collect_values(self):
        p = self._pending
        return {'x': list(p.get('x') or []),
                'y': list(p.get('y') or []),
                'agg': str(p.get('agg') or AGGS[0]),
                'top_n': int(p.get('top_n') or 0),
                'direction': str(p.get('direction') or DIRECTIONS[0]),
                'dual_y': str(p.get('dual_y') or ''),
                'dual_y_agg': str(p.get('dual_y_agg') or AGGS[0]),
                'palette': p.get('palette')}

    def prefill(self, values):
        v = values or {}
        dims = list(self._group_cols or [])
        nums = list(self._num_cols or [])
        x_want = [c for c in _as_list(v.get('x')) if c in dims]
        if not x_want and dims:
            # Required control: a saved chart with no (valid) dimension falls
            # back to the first one, exactly like the combo's own default, so
            # the widget and collect_values() can never disagree.
            x_want = [dims[0]]
        y_want = [c for c in _as_list(v.get('y')) if c in nums]
        dual = str(v.get('dual_y') or '')
        # The same column may not appear twice (the config builder also
        # guards this): a secondary axis duplicating the primary is nonsense.
        dual = dual if (dual in nums and dual not in y_want) else ''
        agg = str(v.get('agg') or '')
        agg = agg if agg in AGGS else AGGS[0]
        d_agg = str(v.get('dual_y_agg') or '')
        d_agg = d_agg if d_agg in AGGS else AGGS[0]
        direction = str(v.get('direction') or '')
        direction = direction if direction in DIRECTIONS else DIRECTIONS[0]
        try:
            top_n = int(v.get('top_n') or 0)
        except Exception:
            top_n = 0
        top_n = max(TOP_N_RANGE[0], min(TOP_N_RANGE[1], top_n))
        pals = list(self._palette_names or ['Default'])
        pal = v.get('palette')
        pal = pal if pal in pals else (pals[0] if pals else None)
        try:
            self._pending.update({'x': x_want, 'y': y_want, 'agg': agg,
                                  'top_n': top_n, 'direction': direction,
                                  'dual_y': dual, 'dual_y_agg': d_agg,
                                  'palette': pal})
        except Exception:
            pass
        self._push_prefill()

    def _push_prefill(self):
        """Snap the widgets to ``_pending`` (if the Generate page exists)."""
        x0 = (self._pending.get('x') or [''])[0]
        y0 = (self._pending.get('y') or [''])[0]
        for attr, value in (('_combo_dim', x0),
                            ('_combo_metric', y0),
                            ('_combo_agg', self._pending.get('agg')),
                            ('_combo_dir', self._pending.get('direction')),
                            ('_combo_dual', self._pending.get('dual_y')),
                            ('_combo_dual_agg', self._pending.get('dual_y_agg')),
                            ('_combo_palette', self._pending.get('palette'))):
            try:
                w = getattr(self, attr, None)
                if w is None:
                    continue
                w.blockSignals(True)
                idx = w.findText(str(value if value is not None else ''))
                w.setCurrentIndex(idx if idx >= 0 else 0)
                w.blockSignals(False)
            except Exception:
                pass
        try:
            w = getattr(self, '_spin_topn', None)
            if w is not None:
                w.blockSignals(True)
                w.setValue(int(self._pending.get('top_n', 0) or 0))
                w.blockSignals(False)
        except Exception:
            pass

    # -- Layout ------------------------------------------------------------
    def build_layout(self):
        """Template Layout rows + the dual-axis label + Line width, minus
        the legend rows and the border toggle (has_legend=False and no
        colour bar, so both would be dead controls)."""
        root = super().build_layout()
        try:
            self._le_title.setPlaceholderText('Mean <metric> by <dimension>')
        except Exception:
            pass
        # -- Y-axis (left) / Y-axis (right): the old panel renders three axis
        #    fields for this type because it can carry a secondary axis.
        row_y = self._le_y.parentWidget()
        lay_y = row_y.parentWidget().layout() if row_y.parentWidget() is not None else None
        if lay_y is not None:
            for lbl in row_y.findChildren(QLabel):
                if (str(lbl.property('theme_role')) == 'section'
                        and lbl.text().strip() == 'Y-axis'):
                    lbl.setText('Y-axis (left)')
                    break
            self._le_y2 = QLineEdit(str(self._layout_opts.get('y2_label', '') or ''))
            self._le_y2.textChanged.connect(lambda t: self._set_layout(y2_label=t))
            lay_y.insertWidget(lay_y.indexOf(row_y) + 1,
                               grid_row('Y-axis (right)', self._le_y2))
        # -- Line width, between Bar mode and Bar gap (the old panel's order:
        #    Value labels -> Label pos -> Bar mode -> Line width -> Bar gap).
        row_bm = self._cb_bmode.parentWidget()
        lay_r = row_bm.parentWidget().layout() if row_bm.parentWidget() is not None else None
        if lay_r is not None:
            lw = slider_with_readout(LINE_WIDTH_RANGE[0], LINE_WIDTH_RANGE[1],
                                     int(self._layout_opts.get('line_width',
                                                               LINE_WIDTH_DEFAULT)),
                                     is_int=True, step=1)
            self._lw_slider = lw._as_slider
            self._lw_spin = lw._as_spin
            try:
                self._lw_spin.valueChanged.connect(lambda v: self._set_layout(line_width=int(v)))
            except Exception:
                pass
            lay_r.insertWidget(lay_r.indexOf(row_bm) + 1,
                               grid_row('Line width', lw))
        # Legend title / Legend position / Show border are deliberately NOT
        # masked here. The categorical runner draws a second series (the
        # Secondary Y-Axis overlay) and Plotly legends it, so a legend really
        # is on screen -- and the legend / colour-bar / colour-scale rows are
        # one shared setting across the supported charts, so a chart that
        # draws a key gets them all. See the module docstring.
        return root

    def prefill_layout(self, values):
        super().prefill_layout(values)
        for attr, key, dflt in (('_le_y2', 'y2_label', ''),
                                ('_lw_spin', 'line_width', LINE_WIDTH_DEFAULT)):
            try:
                w = getattr(self, attr, None)
                if w is None:
                    continue
                w.blockSignals(True)
                if attr == '_le_y2':
                    w.setText(str(self._layout_opts.get(key, dflt) or ''))
                else:
                    w.setValue(int(self._layout_opts.get(key, dflt)))
                w.blockSignals(False)
            except Exception:
                pass

# line_width range/default from the old _CONTROL_META (slider 1..10, d=2).
LINE_WIDTH_RANGE = (1, 10)
LINE_WIDTH_DEFAULT = 2
# Top N: old number_input bounds (0..200, step 1, 0 = show all).
TOP_N_RANGE = (0, 200)


def _as_list(value):
    """Normalise a saved scalar/list widget value to a clean list."""
    if value in (None, ''):
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, '')]
    return [value]
