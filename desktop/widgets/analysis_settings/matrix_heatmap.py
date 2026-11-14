"""Matrix Heatmap settings -- the Correlation page, with its own Generate fields.

Feature reference (old codebase, ``chart_settings.py`` ``CHART_TYPE_SETTINGS``)::

    "matrix_heatmap": has_axes=False, has_legend=False,
    controls = [title, heatmap_colorscale, colorbar_title, colorbar_tick_size,
        colorbar_tick_color, colorbar_title_size, colorbar_title_color,
        heatmap_show_text, heatmap_annotation_precision, heatmap_annotation_size,
        heatmap_annotation_color];
    typography = [family, font_style, header, axis_tick].

The Layout and Typography lists are identical to the ones ``"correlation"``
declares, so both tabs are inherited from the Correlation page VERBATIM rather
than re-listing the same eleven controls: the colour scales, the annotation
controls and the colour-bar styling all come along with it, and a fix to either
page applies here for free.

Why the Generate tab is written out
-----------------------------------
This is the only place the two differ. A correlation matrix derives its axes
from the whole numeric set, so its one Generate control is a Columns
multi-select. A cross-tab heatmap is built from an explicit index / columns /
values triple plus an aggregation and a row sort, and the old app's Generate row
showed exactly those. Five controls are therefore built here, and the heatmap's
colour bar is driven by ``values_col`` rather than by a column list.

The Palette is deliberately absent, for the same reason it is absent from the
correlation page: the Layout tab's Colorscale row is what actually colours this
chart, so a second colour control would be a dead option.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QSpinBox
from .base import hint_label, cap_widget, page_root, section_box, FlowGrid, GEN_W, HINT_GAP
from .correlation import CorrelationSettings
from .statistical import NONE_SENTINEL

# Old chart_settings.py aggregation and row-sort lists, in the order the old
# comboboxes showed them.
MATRIX_AGGS = ["Avg", "Sum", "Min", "Max", "Count"]
MATRIX_SORTS = ["Value ↓", "Value ↑", "Category A→Z", "Category Z→A"]


def _plain_combo(items):
    """A single-choice combo sized to the shared Generate field width."""
    c = QComboBox()
    cap_widget(c, GEN_W)
    c.addItems(items)
    return c


class MatrixHeatmapSettings(CorrelationSettings):
    """Generate + Layout + Typography settings for the Matrix Heatmap type."""

    def _build(self):
        super()._build()
        for attr in ('_combo_index', '_combo_columns_col', '_combo_values',
                     '_combo_magg', '_combo_msort', '_spin_topn'):
            if not hasattr(self, attr):
                setattr(self, attr, None)

    def _single_section(self, title, widget, hint=''):
        """One Generate section: a control in a flow-grid box with its hint."""
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(HINT_GAP)
        bl.addWidget(widget)
        bl.addStretch()
        return section_box(title, body, hint)

    def build_generate(self):
        """Index / Columns / Values / Aggregation / Sort / Top N in one flow."""
        return self._build_crosstab_generate()

    def _build_crosstab_generate(self):
        """The shared cross-tab Generate row.

        A matrix heatmap and a pivot table are the same cross-tab with a
        different renderer, so this is defined once and inherited: the two views
        of one dataset can then never disagree about which cells they show.
        """
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label(
            'Adjust options below. All selections are live — no submit needed until Generate.'))
        flow = FlowGrid()

        # Any column can be a cross-tab key, not just the dimension family, so
        # the pool is the whole dataset.
        keys = [str(c) for c in (self._all_cols or [])]
        self._combo_index = _plain_combo([NONE_SENTINEL] + keys)
        self._combo_index.currentTextChanged.connect(self._on_index)
        flow.add_section(self._single_section(
            'Index (rows)', self._combo_index, 'The row axis of the cross-tab'))

        self._combo_columns_col = _plain_combo([NONE_SENTINEL] + keys)
        self._combo_columns_col.currentTextChanged.connect(self._on_columns_col)
        flow.add_section(self._single_section(
            'Columns', self._combo_columns_col, 'The column axis of the cross-tab'))

        self._combo_values = _plain_combo(
            [NONE_SENTINEL] + [str(c) for c in (self._num_cols or [])])
        self._combo_values.currentTextChanged.connect(self._on_values)
        flow.add_section(self._single_section(
            'Values', self._combo_values, 'The numeric measure in each cell'))

        self._combo_magg = _plain_combo(MATRIX_AGGS)
        self._combo_magg.currentTextChanged.connect(self._on_agg)
        flow.add_section(self._single_section(
            'Aggregation', self._combo_magg, 'How cells are combined'))

        self._combo_msort = _plain_combo(MATRIX_SORTS)
        self._combo_msort.currentTextChanged.connect(self._on_sort)
        flow.add_section(self._single_section(
            'Sort rows', self._combo_msort, 'Row order in the matrix'))

        self._spin_topn = QSpinBox()
        cap_widget(self._spin_topn, GEN_W)
        self._spin_topn.setRange(0, 500)
        self._spin_topn.setValue(0)
        self._spin_topn.valueChanged.connect(self._on_top_n)
        flow.add_section(self._single_section(
            'Top N rows', self._spin_topn, '0 keeps every row'))

        if getattr(self, 'supports_row_hierarchy', False):
            for level in range(3):
                combo = _plain_combo([NONE_SENTINEL] + keys)
                combo.currentTextChanged.connect(
                    lambda _text, i=level: self._on_row_hierarchy(i))
                setattr(self, f'_combo_row_level_{level}', combo)
                flow.add_section(self._single_section(
                    f'Row level {level + 1}', combo,
                    'Ordered parent-to-child grouping for drill-down'))

        rl.addWidget(flow)
        rl.addStretch()
        return root

    # -- Generate field handlers -------------------------------------------
    # Each records the value AND marks the form dirty, so the Update button
    # enables on a real change rather than needing a separate apply gesture.

    @staticmethod
    def _clean(text):
        """A combo's blank placeholder means "unset", not the empty column."""
        return None if str(text or '') in ('', NONE_SENTINEL) else str(text)

    def _on_index(self, text):
        self._pending['index_col'] = self._clean(text)
        self._mark_dirty(emit=True)

    def _on_columns_col(self, text):
        self._pending['columns_col'] = self._clean(text)
        self._mark_dirty(emit=True)

    def _on_values(self, text):
        self._pending['values_col'] = self._clean(text)
        self._mark_dirty(emit=True)

    def _on_agg(self, text):
        self._pending['agg'] = str(text)
        self._mark_dirty(emit=True)

    def _on_sort(self, text):
        self._pending['sort_rows'] = str(text)
        self._mark_dirty(emit=True)

    def _on_top_n(self, value):
        self._pending['top_n_rows'] = int(value or 0)
        self._mark_dirty(emit=True)

    def collect_values(self):
        """The cross-tab triple, as the analysis service expects it.

        ``x`` is carried alongside them because the shared edit/prefill path
        uses it to recognise a saved heatmap; the runner ignores it.
        """
        p = self._pending
        return {
            'x': [],
            'index_col': p.get('index_col'),
            'columns_col': p.get('columns_col'),
            'values_col': p.get('values_col'),
            'agg': p.get('agg', MATRIX_AGGS[0]),
            'sort_rows': p.get('sort_rows', MATRIX_SORTS[0]),
            'top_n_rows': int(p.get('top_n_rows') or 0),
            **({'row_hierarchy': list(p.get('row_hierarchy') or [])}
               if getattr(self, 'supports_row_hierarchy', False) else {}),
        }

    def prefill(self, values):
        """Restore a saved cross-tab config into the combos and the spin box.

        Each value is snapped to an option that still EXISTS before it is shown:
        a chart saved against another dataset can name a column that is gone, and
        the combo would then show item 0 while collect_values() still returned
        the stale name.
        """
        v = values or {}
        for key, attr, dflt in (('index_col', '_combo_index', ''),
                                ('columns_col', '_combo_columns_col', ''),
                                ('values_col', '_combo_values', ''),
                                ('agg', '_combo_magg', MATRIX_AGGS[0]),
                                ('sort_rows', '_combo_msort', MATRIX_SORTS[0])):
            self._pending[key] = v.get(key, dflt)
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                w.blockSignals(True)
                want = str(self._pending.get(key) or dflt)
                if want and want in [w.itemText(i) for i in range(w.count())]:
                    w.setCurrentText(want)
                w.blockSignals(False)
            except Exception:
                pass
        self._pending['top_n_rows'] = int(v.get('top_n_rows') or 0)
        if getattr(self, 'supports_row_hierarchy', False):
            hierarchy = [c for c in (v.get('row_hierarchy') or [])
                         if c in (self._all_cols or [])][:3]
            self._pending['row_hierarchy'] = hierarchy
            for level in range(3):
                w = getattr(self, f'_combo_row_level_{level}', None)
                if w is not None:
                    w.blockSignals(True)
                    w.setCurrentText(hierarchy[level] if level < len(hierarchy) else NONE_SENTINEL)
                    w.blockSignals(False)
        w = getattr(self, '_spin_topn', None)
        if w is not None:
            try:
                w.blockSignals(True)
                w.setValue(self._pending['top_n_rows'])
                w.blockSignals(False)
            except Exception:
                pass
        # The inherited page keeps its own 'x' list; keep it consistent so a
        # later super() call does not see a stale value.
        try:
            self._pending['x'] = list(v.get('x', []) or [])
        except Exception:
            pass

    def _on_row_hierarchy(self, _level):
        if not getattr(self, 'supports_row_hierarchy', False):
            return
        selected = []
        for level in range(3):
            combo = getattr(self, f'_combo_row_level_{level}', None)
            value = self._clean(combo.currentText()) if combo is not None else None
            if value and value not in selected:
                selected.append(value)
        self._pending['row_hierarchy'] = selected
        if selected:
            self._pending['index_col'] = selected[0]
            combo = getattr(self, '_combo_index', None)
            if combo is not None and combo.currentText() != selected[0]:
                combo.blockSignals(True)
                combo.setCurrentText(selected[0])
                combo.blockSignals(False)
        self._mark_dirty(emit=True)

    def build_typography(self):
        root = super().build_typography()
        self._mask_rows(root, ("Value size", "Value color", "Value Color"))
        return root
