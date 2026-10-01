"""Time Series settings -- inherits the Statistical template, swaps bars for lines.

Feature reference (old codebase, ``chart_settings.py`` ``CHART_TYPE_SETTINGS``
and ``_CONTROL_META``; the old Streamlit grouping and "Advanced" popover are NOT
reproduced):

    "time_series": has_axes=True, has_legend=True,
    controls = [title, axes_labels, legend_labels, line_width, line_shape,
        show_markers, line_fill, show_value_labels];
    typography = [family, font_style, header, axis_title, axis_tick,
        legend_title, legend_item].

``CONTROLS_ADVANCED`` put [line_shape, line_fill] behind the old popover, so
those two become ordinary rows here. "Line width" was in the main view.

What is masked
--------------
The template's bar options. A time series draws lines, not bars, so "Bar mode"
and "Bar gap" are removed -- ``barmode``/``bargap`` do nothing on a scatter
trace. Everything else the template offers applies: both axis labels, the grid,
the legend, the value labels and the full typography set.

Why ``StatisticalSettings`` and not ``CategoricalSettings``
----------------------------------------------------------
Both are bar charts. The time-series form is a LINE chart, so inheriting the
line-shaped template and adding the line options is a smaller and clearer diff
than inheriting a bar builder and then removing its bar rows.

The Generate tab is written out rather than inherited, because a time series
needs single-choice Date / Metric fields plus a granularity, an aggregation and
a secondary axis -- none of which the template's dimension + metrics-list pair
can express, and all of which the service's ``time_series`` branch reads by
name.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QCheckBox
from .base import (hint_label, cap_widget, slider_with_readout, page_root,
                   section_box, grid_row, FlowGrid, GEN_W, HINT_GAP)
from .statistical import StatisticalSettings, NONE_SENTINEL
from .categorical import AGGS

# Old _CONTROL_META option lists for the line style and the area fill.
LINE_SHAPES = ["linear", "spline", "hv", "vh", "hvh", "vhv"]
LINE_FILLS = ["none", "tozeroy", "tozerox", "tonexty", "tonextx"]

# Generate-row option, from the old time-series panel (screenshot time1.png).
# The labels come from the SERVICE's own DATE_PARTS table rather than a copy of
# it, so the combo text and the value it maps to cannot drift apart -- a
# hand-written list here silently sent "Month" to a table that expects
# "Month (number)", and the granularity was then ignored.
try:
    from services.analysis_service import DATE_PARTS as _DATE_PART_MAP
    DATE_PARTS = list(_DATE_PART_MAP.keys())
except Exception:  # pragma: no cover - keeps the form buildable standalone
    DATE_PARTS = ["None", "Year", "Quarter", "Month (number)", "Month Name",
                  "Weekday Name", "Day", "Hour"]


class TimeSeriesSettings(StatisticalSettings):
    """Generate + Layout + Typography settings for the Time Series type."""

    def _build(self):
        super()._build()
        # The template's x/y/palette shape is wrong for a time series: the date
        # column is a single choice (a line needs one x), the metric is one
        # column, and there are three scalars the template has no field for.
        self._pending = {'x': None, 'y': None, 'date_part': DATE_PARTS[0],
                         'agg': AGGS[0], 'dual_y_ts': NONE_SENTINEL,
                         'dual_y_agg': AGGS[0], 'palette': None}
        for attr in ('_combo_x', '_combo_y', '_combo_dpart', '_combo_agg',
                     '_combo_dual', '_combo_dualagg', '_combo_palette',
                     '_cb_lshape', '_cb_lfill', '_lw_spin', '_ck_markers'):
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
        """Date | Metric | Granularity | Aggregation | Secondary axis | Palette.

        The slots mirror the old time-series panel (screenshot ``time1.png``).
        The date pool is the DATETIME columns, not the whole dimension pool: a
        line chart's x axis has to be a time axis for the date-part grouping and
        the trend to mean anything.
        """
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label(
            'Adjust options below. All selections are live — no submit needed until Generate.'))
        flow = FlowGrid()

        dts = list(self._dt_cols or []) or list(self._group_cols or [])
        box, self._combo_x = self._combo_section(
            'Date', 'The datetime column for the x axis',
            dts if dts else [''], dts[0] if dts else '', self._on_x)
        flow.add_section(box)

        nums = list(self._num_cols or [])
        box, self._combo_y = self._combo_section(
            'Metric', 'Value to plot over time',
            nums if nums else [''], nums[0] if nums else '', self._on_y)
        flow.add_section(box)

        box, self._combo_dpart = self._combo_section(
            'Granularity', 'None keeps the raw dates',
            DATE_PARTS, DATE_PARTS[0], self._on_date_part)
        flow.add_section(box)

        box, self._combo_agg = self._combo_section(
            'Aggregation', 'How to combine values in each period',
            AGGS, AGGS[0], self._on_agg)
        flow.add_section(box)

        box, self._combo_dual = self._combo_section(
            'Secondary Y-Axis', 'None = disabled.',
            [NONE_SENTINEL] + nums, NONE_SENTINEL, self._on_dual)
        flow.add_section(box)

        box, self._combo_dualagg = self._combo_section(
            'Secondary Agg', 'Aggregation for the secondary axis',
            AGGS, AGGS[0], self._on_dual_agg)
        flow.add_section(box)

        pals = list(self._palette_names or ['Default'])
        box, self._combo_palette = self._combo_section(
            '\U0001f3a8 Palette', '', pals, pals[0], self._on_palette)
        flow.add_section(box)

        rl.addWidget(flow)
        rl.addStretch()
        self._sync_pending()
        return root

    def _sync_pending(self):
        """Pull every Generate control's value into ``_pending``."""
        try:
            self._on_x(self._combo_x.currentText())
            self._on_y(self._combo_y.currentText())
            self._on_date_part(self._combo_dpart.currentText())
            self._on_agg(self._combo_agg.currentText())
            self._on_dual(self._combo_dual.currentText())
            self._on_dual_agg(self._combo_dualagg.currentText())
            self._on_palette(self._combo_palette.currentText())
        except Exception:
            pass

    # -- Generate handlers -------------------------------------------------
    def _on_x(self, text):
        self._pending['x'] = str(text) if text else None
        self._mark_dirty(emit=True)

    def _on_y(self, text):
        self._pending['y'] = str(text) if text else None
        self._mark_dirty(emit=True)

    def _on_date_part(self, text):
        self._pending['date_part'] = str(text)
        self._mark_dirty(emit=True)

    def _on_agg(self, text):
        self._pending['agg'] = str(text)
        self._mark_dirty(emit=True)

    def _on_dual(self, text):
        self._pending['dual_y_ts'] = '' if text == '' else str(text)
        self._mark_dirty(emit=True)

    def _on_dual_agg(self, text):
        self._pending['dual_y_agg'] = str(text)
        self._mark_dirty(emit=True)

    def _on_palette(self, text):
        self._pending['palette'] = str(text)
        self._mark_dirty(emit=True)

    def collect_values(self):
        """The Generate values, keyed as the time-series service branch reads them."""
        p = self._pending
        return {'x': p.get('x'), 'y': p.get('y'),
                'date_part': p.get('date_part', DATE_PARTS[0]),
                'agg': p.get('agg', AGGS[0]),
                'dual_y_ts': p.get('dual_y_ts', NONE_SENTINEL),
                'dual_y_agg': p.get('dual_y_agg', AGGS[0]),
                'palette': p.get('palette')}

    def prefill(self, values):
        """Restore a saved config, snapping each name to an option that exists.

        A chart saved against another dataset can name a column that is gone, so
        a stale name is skipped rather than displayed while still being returned
        by collect_values().
        """
        v = values or {}
        p = self._pending
        for key, dflt in (('x', ''), ('y', ''), ('date_part', DATE_PARTS[0]),
                          ('agg', AGGS[0]), ('dual_y_ts', NONE_SENTINEL),
                          ('dual_y_agg', AGGS[0]), ('palette', '')):
            p[key] = v.get(key, p.get(key, dflt))
        for attr, key, dflt in (('_combo_x', 'x', ''), ('_combo_y', 'y', ''),
                                ('_combo_dpart', 'date_part', DATE_PARTS[0]),
                                ('_combo_agg', 'agg', AGGS[0]),
                                ('_combo_dual', 'dual_y_ts', NONE_SENTINEL),
                                ('_combo_dualagg', 'dual_y_agg', AGGS[0]),
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


    def _add_line_rows(self, root):
        """Append the line-style options to the shared Layout option column."""
        lw = slider_with_readout(1, 10, int(self._layout_opts.get("line_width", 2)))
        self._lw_spin = lw._as_spin
        try:
            self._lw_spin.valueChanged.connect(
                lambda v: self._set_layout(line_width=int(v)))
        except Exception:
            pass
        self._add_option_row(root, grid_row('Line width', lw))

        self._ck_markers = QCheckBox('Markers')
        self._ck_markers.setChecked(bool(self._layout_opts.get("show_markers", True)))
        self._ck_markers.toggled.connect(
            lambda v: self._set_layout(show_markers=bool(v)))
        self._add_option_row(root, self._check_row(self._ck_markers))

        # -- The two options the old app put behind "Advanced" ---------------
        self._cb_lshape = QComboBox()
        cap_widget(self._cb_lshape, 200)
        self._cb_lshape.addItems(LINE_SHAPES)
        self._cb_lshape.setCurrentText(str(self._layout_opts.get("line_shape", "linear")))
        self._cb_lshape.currentTextChanged.connect(
            lambda t: self._set_layout(line_shape=t))
        self._add_option_row(root, grid_row('Line shape', self._cb_lshape))

        self._cb_lfill = QComboBox()
        cap_widget(self._cb_lfill, 200)
        self._cb_lfill.addItems(LINE_FILLS)
        self._cb_lfill.setCurrentText(str(self._layout_opts.get("line_fill", "none")))
        self._cb_lfill.currentTextChanged.connect(
            lambda t: self._set_layout(line_fill=t))
        self._add_option_row(root, grid_row('Fill', self._cb_lfill))

    def build_layout(self):
        """The shared Layout tab plus the line options, minus the bar rows."""
        root = super().build_layout()
        self._mask_rows(root, ("Bar mode", "Bar gap"))
        self._add_line_rows(root)
        return root
