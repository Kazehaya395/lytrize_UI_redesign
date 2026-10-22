"""Pie & Donut settings -- inherits the Statistical template, masks what pie cannot use.

Feature reference (old codebase, ``chart_settings.py`` _CONTROL_META and
``CHART_TYPE_SETTINGS``, which is where the names/ranges/defaults below come
from -- the old app's Streamlit grouping and its "Advanced" popover are
deliberately NOT reproduced):

    "pie_chart": has_axes=False, has_legend=True,
    controls = [title, legend_labels, donut_hole, pie_textinfo, pull_slices,
        pie_rotation, pie_direction, pie_value_size, pie_value_color];
    typography = [family, font_style, header, legend_title, legend_item].

Why it subclasses ``StatisticalSettings``
----------------------------------------
It is the nearest builder with a line-shaped Generate tab, so the shared
helpers are reused. Its own Generate row is written out, because the pie form
is six single-choice controls (Dimension | Metric | Aggregation | Sort |
Palette | Top N -- screenshot ``pie/pie1.png``) rather than the template's
dimension + metrics-list pair, and the service's pie branch reads exactly those
keys.

What this chart deliberately does NOT offer
--------------------------------------------
A pie has no Cartesian axes, so the X-axis and Y-axis label rows are removed
rather than left as controls that cannot change anything. No bars, so "Bar mode"
and "Bar gap" go; no grid either, since none of it applies to a pie trace.
"Show value labels" IS kept -- a pie's slice text is exactly that, and WHAT the
slice text says is the ``pie_textinfo`` dropdown.

The five "Advanced" pie options (pull / rotation / direction / value size /
value colour) become ordinary rows in the Layout tab's right-hand option
column, in the same order the old popover listed them. The old "Advanced" is
not a Qt concept.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QSpinBox, QCheckBox
from .base import (hint_label, cap_widget, color_swatch, slider_with_readout,
                   page_root, section_box, grid_row, FlowGrid, GEN_W, HINT_GAP)
from .statistical import StatisticalSettings, NONE_SENTINEL

# Old _CONTROL_META option lists for the pie slice text and sweep direction.
PIE_TEXTINFO = ["label", "percent", "value", "label+percent", "label+value"]
PIE_DIRECTION = ["clockwise", "counterclockwise"]

# Generate-row options, from the old pie panel (screenshot pie1.png) and the
# matching _WIDGET_SPEC entries.
PIE_SORTS = ["Value ↓", "Value ↑", "Category A→Z", "Category Z→A"]
TOP_N_RANGE = (0, 200)

try:
    from services.analysis_service import AGG_FUNCS as _AGG_FUNCS
    AGGS = list(_AGG_FUNCS.keys())
except Exception:  # pragma: no cover - keeps the form buildable standalone
    AGGS = ['Avg', 'Sum', 'Median', 'Count', 'Min', 'Max']



class PieChartSettings(StatisticalSettings):
    """Generate + Layout + Typography settings for the Pie & Donut type."""

    def _build(self):
        super()._build()
        # The template's x/y/palette shape does not describe this form: the pie
        # takes ONE dimension and ONE optional metric (the runner still receives
        # one-element lists per _WIDGET_SPEC), plus the aggregation / sort / top-N
        # scalars the old pie panel showed and the template has no field for.
        self._pending = {'x': None, 'y': None, 'agg': AGGS[0],
                         'sort': PIE_SORTS[0], 'top_n': 0, 'palette': None}
        for attr in ('_combo_dim', '_combo_metric', '_combo_agg', '_combo_sort',
                     '_combo_palette', '_spin_topn', '_cb_textinfo', '_cb_pdir',
                     '_dh_spin', '_cb_center_total', '_pl_spin', '_pr_spin',
                     '_pvs_spin', '_sw_pvc'):
            if not hasattr(self, attr):
                setattr(self, attr, None)

    # -- Generate ----------------------------------------------------------
    def _combo_section(self, title, hint, items, current, on_change):
        """One Generate slot: a compact combo inside the shared section box."""
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
        """Dimension | Metric | Aggregation | Sort | Palette | Top N.

        The same six slots, in the same order, as the old pie panel (screenshot
        ``pie/pie1.png``), flowed across the pane rather than pinned to a fixed
        column count.
        """
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label(
            'Adjust options below. All selections are live — no submit needed until Generate.'))
        flow = FlowGrid()

        dims = list(self._group_cols or [])
        box, self._combo_dim = self._combo_section(
            'Dimension', 'Column to split the data by',
            dims if dims else [''], dims[0] if dims else '', self._on_dim)
        flow.add_section(box)

        # Metric is optional (the runner falls back to value counts), so it
        # carries a BLANK first entry rather than the literal word "None".
        nums = list(self._num_cols or [])
        box, self._combo_metric = self._combo_section(
            'Metric', 'Value to aggregate (optional)',
            [NONE_SENTINEL] + nums, NONE_SENTINEL, self._on_metric)
        flow.add_section(box)

        box, self._combo_agg = self._combo_section(
            'Aggregation', 'How to combine multiple metric values',
            AGGS, AGGS[0], self._on_agg)
        flow.add_section(box)

        box, self._combo_sort = self._combo_section(
            'Sort', 'Slice order around the pie',
            PIE_SORTS, PIE_SORTS[0], self._on_sort)
        flow.add_section(box)

        pals = list(self._palette_names or ['Default'])
        box, self._combo_palette = self._combo_section(
            '\U0001f3a8 Palette', '', pals, pals[0], self._on_palette)
        flow.add_section(box)

        tbox = QWidget()
        tl = QVBoxLayout(tbox)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(HINT_GAP)
        self._spin_topn = QSpinBox()
        cap_widget(self._spin_topn, GEN_W)
        self._spin_topn.setRange(*TOP_N_RANGE)
        self._spin_topn.setValue(0)
        self._spin_topn.valueChanged.connect(self._on_topn)
        tl.addWidget(self._spin_topn)
        tl.addStretch()
        flow.add_section(section_box('Top N', tbox, 'Max slices (0 = show all)'))

        rl.addWidget(flow)
        rl.addStretch()
        self._sync_pending()
        return root

    def _sync_pending(self):
        """Pull every Generate control's value into ``_pending``."""
        try:
            self._on_dim(self._combo_dim.currentText())
            self._on_metric(self._combo_metric.currentText())
            self._on_agg(self._combo_agg.currentText())
            self._on_sort(self._combo_sort.currentText())
            self._on_palette(self._combo_palette.currentText())
            self._on_topn(int(self._spin_topn.value()))
        except Exception:
            pass

    # -- Generate handlers -------------------------------------------------
    def _on_dim(self, text):
        self._pending['x'] = str(text) if text else None
        self._mark_dirty(emit=True)

    def _on_metric(self, text):
        self._pending['y'] = None if str(text or '') in ('', NONE_SENTINEL) else str(text)
        self._mark_dirty(emit=True)

    def _on_agg(self, text):
        self._pending['agg'] = str(text)
        self._mark_dirty(emit=True)

    def _on_sort(self, text):
        self._pending['sort'] = str(text)
        self._mark_dirty(emit=True)

    def _on_palette(self, text):
        self._pending['palette'] = str(text)
        self._mark_dirty(emit=True)

    def _on_topn(self, value):
        self._pending['top_n'] = int(value or 0)
        self._mark_dirty(emit=True)

    def collect_values(self):
        """The six Generate values, keyed as the pie service branch expects."""
        p = self._pending
        return {'x': p.get('x'), 'y': p.get('y'), 'agg': p.get('agg', AGGS[0]),
                'sort': p.get('sort', PIE_SORTS[0]),
                'top_n': int(p.get('top_n') or 0), 'palette': p.get('palette')}

    def prefill(self, values):
        """Restore a saved pie config, snapping names to options that exist.

        A chart saved against another dataset can name a column or palette that
        is gone, so each value is checked against the combo's items before it is
        displayed -- otherwise the combo would show item 0 while
        collect_values() still returned the stale name.
        """
        v = values or {}
        p = self._pending
        for key, dflt in (('x', ''), ('y', ''), ('agg', AGGS[0]),
                          ('sort', PIE_SORTS[0]), ('palette', '')):
            p[key] = v.get(key, p.get(key, dflt))
        p['top_n'] = int(v.get('top_n', p.get('top_n') or 0) or 0)
        for attr, key, dflt in (('_combo_dim', 'x', ''), ('_combo_metric', 'y', ''),
                                ('_combo_agg', 'agg', AGGS[0]),
                                ('_combo_sort', 'sort', PIE_SORTS[0]),
                                ('_combo_palette', 'palette', '')):
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                w.blockSignals(True)
                want = str(p.get(key) or dflt)
                if want and want in [w.itemText(i) for i in range(w.count())]:
                    w.setCurrentText(want)
                w.blockSignals(False)
            except Exception:
                pass
        w = getattr(self, '_spin_topn', None)
        if w is not None:
            try:
                w.blockSignals(True)
                w.setValue(p['top_n'])
                w.blockSignals(False)
            except Exception:
                pass


    def _add_pie_rows(self, root):
        """Append the pie slice options to the shared Layout option column.

        The rows go in the order the old "Advanced" popover listed them, after
        the two rows that were always in the main view (text info, donut).
        """
        self._cb_textinfo = QComboBox()
        cap_widget(self._cb_textinfo, 200)
        self._cb_textinfo.addItems(PIE_TEXTINFO)
        self._cb_textinfo.setCurrentText(
            str(self._layout_opts.get("pie_textinfo", "label+percent")))
        self._cb_textinfo.currentTextChanged.connect(
            lambda t: self._set_layout(pie_textinfo=t))
        self._add_option_row(root, grid_row('Text info', self._cb_textinfo))

        dh = slider_with_readout(0.0, 0.9,
                                 float(self._layout_opts.get("donut_hole", 0.0)),
                                 is_int=False, step=0.05)
        self._dh_spin = dh._as_spin
        try:
            self._dh_spin.valueChanged.connect(
                lambda v: self._set_layout(donut_hole=float(v)))
            self._dh_spin.valueChanged.connect(self._sync_center_total_enabled)
        except Exception:
            pass
        self._add_option_row(root, grid_row('Donut', dh))

        self._cb_center_total = QCheckBox('Show metric total in centre')
        self._cb_center_total.setChecked(
            bool(self._layout_opts.get('donut_center_total', False)))
        self._cb_center_total.setToolTip(
            'Show the sum of the visible pie slices inside the donut hole.')
        self._cb_center_total.toggled.connect(
            lambda checked: self._set_layout(donut_center_total=bool(checked)))
        self._add_option_row(root, grid_row('Centre total', self._cb_center_total))
        self._sync_center_total_enabled()

        # -- The five options that sat behind the old "Advanced" popover -----
        pl = slider_with_readout(0.0, 0.5,
                                 float(self._layout_opts.get("pull_slices", 0.0)),
                                 is_int=False, step=0.01)
        self._pl_spin = pl._as_spin
        try:
            self._pl_spin.valueChanged.connect(
                lambda v: self._set_layout(pull_slices=float(v)))
        except Exception:
            pass
        self._add_option_row(root, grid_row('Pull', pl))

        pr = slider_with_readout(0, 360, int(self._layout_opts.get("pie_rotation", 0)))
        self._pr_spin = pr._as_spin
        try:
            self._pr_spin.valueChanged.connect(
                lambda v: self._set_layout(pie_rotation=int(v)))
        except Exception:
            pass
        self._add_option_row(root, grid_row('Rotation', pr))

        self._cb_pdir = QComboBox()
        cap_widget(self._cb_pdir, 200)
        self._cb_pdir.addItems(PIE_DIRECTION)
        self._cb_pdir.setCurrentText(
            str(self._layout_opts.get("pie_direction", "clockwise")))
        self._cb_pdir.currentTextChanged.connect(
            lambda t: self._set_layout(pie_direction=t))
        self._add_option_row(root, grid_row('Direction', self._cb_pdir))

        pvs = slider_with_readout(6, 24, int(self._layout_opts.get("pie_value_size", 11)))
        self._pvs_spin = pvs._as_spin
        try:
            self._pvs_spin.valueChanged.connect(
                lambda v: self._set_layout(pie_value_size=int(v)))
        except Exception:
            pass
        self._add_option_row(root, grid_row('Value size', pvs))

        self._sw_pvc = color_swatch(
            str(self._layout_opts.get("pie_value_color", "#e2e8f0")),
            lambda c: self._set_layout(pie_value_color=c))
        self._add_option_row(root, grid_row('Value color', self._sw_pvc))

    def _sync_center_total_enabled(self, *_args):
        """Only offer the center-total toggle while the chart has a hole."""
        checkbox = getattr(self, '_cb_center_total', None)
        spin = getattr(self, '_dh_spin', None)
        if checkbox is None or spin is None:
            return
        try:
            checkbox.setEnabled(float(spin.value()) > 0)
        except (TypeError, ValueError):
            checkbox.setEnabled(False)

    def build_layout(self):
        """The shared Layout tab plus the pie-only rows, minus the bar rows."""
        root = super().build_layout()
        # A pie has no axes and no bars, so those controls are removed rather
        # than left in place doing nothing.
        self._mask_rows(root, ("X-axis", "Y-axis", "Bar mode", "Bar gap", "Show grid"))
        self._add_pie_rows(root)
        return root

    def build_typography(self):
        """Shared Typography tab, minus the rows a pie cannot render.

        ``has_axes=False`` in the old metadata, so there is no axis title or axis
        tick to size -- those four rows go. The legend rows STAY:
        ``has_legend=True``, and a pie is the one chart in this family whose
        legend is genuinely always drawn.
        """
        root = super().build_typography()
        self._mask_rows(root, ("Title size", "Title color", "Axis size", "Axis color"))
        return root

