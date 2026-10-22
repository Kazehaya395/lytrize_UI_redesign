"""Distribution chart settings -- follows the Statistical template exactly.

Design note (why this subclasses StatisticalSettings):
    The brief is that every analysis type uses the SAME three-tab design as
    the statistical chart, so the Generate / Layout / Typography tabs are
    built from the same helpers with the same constants (page_root,
    section_box, grid_row, GEN_W, SECTION_GAP, HINT_GAP, label widths).
    Subclassing means the Typography tab -- which is entirely
    type-agnostic -- is inherited rather than copied, and any future change
    to the shared helpers reaches this type for free.

    Only the two tabs that genuinely differ are overridden: the Generate
    columns (distribution picks a numeric list, not a single category) and
    the Layout tab (which adds Bins / Opacity).

    Feature reference (old codebase, chart_settings.py _CONTROL_META:226):
        histogram_bins    "Bins"     5..200   step 1    default 30
        histogram_opacity "Opacity"  0.1..1.0 step 0.05 default 0.80
    Both are histogram-only; chart_display applies them per-trace.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QCheckBox
from .base import (hint_label, cap_widget, multi_select_combo, slider_with_readout, legend_position_row, legend_orientation_row, GEN_W, HINT_GAP, SECTION_GAP, page_root, section_box, grid_row, FlowGrid)
from .statistical import StatisticalSettings, NONE_SENTINEL, LPOS, BMODES

# GEN_W (the compact Generate field width) now comes from ``.base`` so the
# flow grid's minimum column width cannot drift from it.
# Bins / Opacity ranges come from the old app's control definitions.
BINS_RANGE = (5, 200)
BINS_STEP = 1
OPACITY_RANGE = (0.1, 1.0)
OPACITY_STEP = 0.05


class DistributionSettings(StatisticalSettings):
    """Generate + Layout + Typography settings for the distribution type."""

    def _build(self):
        super()._build()
        # The distribution form stores the colour-by column under "color"
        # (the key WIDGET_SPEC declares for this type) rather than
        # statistical's "y".
        self._pending = {'x': [], 'color': None, 'palette': None}
        for attr in ('_combo_numeric', '_combo_color', '_combo_palette',
                     '_bins_slider', '_bins_spin', '_op_slider', '_op_spin'):
            if not hasattr(self, attr):
                setattr(self, attr, None)

    def prefill(self, values):
        v = values or {}
        # Snap the stored values to options that actually exist BEFORE pushing
        # them into the widgets. A chart saved against a different dataset (or
        # an older release with a different column list) can carry a colour
        # column or palette that no longer exists; the combo would silently
        # show item 0 while collect_values() still returned the stale name,
        # so Generate would build a chart from a column that is not there.
        x_want = [c for c in (list(v.get('x', []) or []))
                  if c in (self._num_cols or [])]
        c_opts = [NONE_SENTINEL] + list(self._cat_cols or [])
        c_want = v.get('color', None)
        c_want = c_want if c_want in self._cat_cols else None
        pals = list(self._palette_names or ['Default'])
        p_want = v.get('palette', None)
        p_want = p_want if p_want in pals else None
        try:
            self._pending['x'] = x_want
            self._pending['color'] = c_want
            self._pending['palette'] = p_want or (pals[0] if pals else None)
        except Exception:
            pass
        try:
            if getattr(self, '_combo_numeric', None) is not None:
                self._combo_numeric.set_selection(list(self._pending['x']))
        except Exception:
            pass
        try:
            if getattr(self, '_combo_color', None) is not None:
                self._combo_color.setCurrentIndex(c_opts.index(self._pending['color'])
                                                  if self._pending['color'] in c_opts else 0)
        except Exception:
            pass
        try:
            if getattr(self, '_combo_palette', None) is not None and self._pending.get('palette'):
                self._combo_palette.setCurrentText(str(self._pending['palette']))
        except Exception:
            pass

    def _on_numeric(self, sel):
        self._pending['x'] = list(sel or [])
        self._mark_dirty(emit=True)

    def _on_color(self, text):
        self._pending['color'] = None if text in ('', NONE_SENTINEL) else text
        self._mark_dirty(emit=True)

    def _on_palette(self, text):
        self._pending['palette'] = text
        self._mark_dirty(emit=True)

    def collect_values(self):
        try:
            return {'x': list(self._pending.get('x') or []),
                    'color': self._pending.get('color'),
                    'palette': self._pending.get('palette')}
        except Exception:
            return {'x': [], 'color': None, 'palette': None}
    def build_generate(self):
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label('Adjust options below. All selections are live \u2014 no submit needed until Generate.'))
        # Sections flow across the pane (FlowGrid): as many per line as fit,
        # each line stretched to the full width, the remainder wrapped to the
        # next line. See the statistical template for the reasoning.
        flow = FlowGrid()
        # Column ORDER mirrors the statistical template exactly:
        #   slot 1 = the optional dimension control  (Group by  <-> Colour by)
        #   slot 2 = the numeric metric control     (Metrics  <-> Numeric columns)
        #   slot 3 = the palette
        # Keeping the same slot for the same kind of control is what makes the
        # two tabs read as one design; putting "Numeric columns" first broke
        # that, because the eye had to relearn the layout per chart type.
        # -- Slot 1: Colour by (optional) ------------------------------------
        cbody = QWidget()
        clay = QVBoxLayout(cbody)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(HINT_GAP)
        self._combo_color = QComboBox()
        cap_widget(self._combo_color, GEN_W)
        c_opts = [NONE_SENTINEL] + list(self._cat_cols or [])
        self._combo_color.addItems(c_opts)
        cur = self._pending.get('color')
        idx = c_opts.index(cur) if cur in c_opts else 0
        self._combo_color.setCurrentIndex(idx if idx >= 0 else 0)
        if not self._pending.get('color'):
            self._pending['color'] = (None if self._combo_color.currentIndex() <= 0
                                      else self._combo_color.currentText())
        self._combo_color.currentTextChanged.connect(self._on_color)
        clay.addWidget(self._combo_color)
        clay.addStretch()
        flow.add_section(section_box('Colour by (optional)', cbody, 'Leave blank for a single distribution'))
        # -- Slot 2: Numeric columns (multi-select) ---------------------------
        nbody = QWidget()
        nl = QVBoxLayout(nbody)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(HINT_GAP)
        self._combo_numeric = multi_select_combo(list(self._num_cols or []))
        cap_widget(self._combo_numeric, GEN_W)
        try:
            self._combo_numeric.set_selection(list(self._pending.get('x') or []))
        except Exception:
            pass
        try:
            self._combo_numeric.selection_changed.connect(self._on_numeric)
        except Exception:
            pass
        nl.addWidget(self._combo_numeric)
        nl.addStretch()
        flow.add_section(section_box('Numeric columns', nbody, 'One or more numeric columns'))
        # -- Palette --------------------------------------------------------
        pbody = QWidget()
        pl = QVBoxLayout(pbody)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(HINT_GAP)
        self._combo_palette = QComboBox()
        cap_widget(self._combo_palette, GEN_W)
        pals = list(self._palette_names or ['Default'])
        self._combo_palette.addItems(pals)
        if self._pending.get('palette') in pals:
            self._combo_palette.setCurrentText(str(self._pending['palette']))
        else:
            self._pending['palette'] = self._combo_palette.currentText()
        self._combo_palette.currentTextChanged.connect(self._on_palette)
        pl.addWidget(self._combo_palette)
        pl.addStretch()
        flow.add_section(section_box('\U0001f3a8 Palette', pbody))
        rl.addWidget(flow)
        rl.addStretch()
        return root





    def build_layout(self):
        root, rl = page_root()
        cols = QWidget()
        cl = QHBoxLayout(cols)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(SECTION_GAP)
        left, ll_ = self._option_column()
        right, rr = self._option_column()
        rl.addWidget(hint_label('\u2728 Live Preview \u2014 changes appear instantly on the chart \u2192'))
        # -- Left column: titles (identical to the statistical template) ---
        self._le_title = QLineEdit(str(self._layout_opts.get('title', '') or ''))
        self._le_title.setPlaceholderText('Distribution of <metric>')
        self._le_title.textChanged.connect(lambda t: self._set_layout(title=t))
        ll_.addWidget(grid_row('Chart title', self._le_title))
        self._le_x = QLineEdit(str(self._layout_opts.get('x_label', '') or ''))
        self._le_x.textChanged.connect(lambda t: self._set_layout(x_label=t))
        ll_.addWidget(grid_row('X-axis', self._le_x))
        self._le_y = QLineEdit(str(self._layout_opts.get('y_label', '') or ''))
        self._le_y.textChanged.connect(lambda t: self._set_layout(y_label=t))
        ll_.addWidget(grid_row('Y-axis', self._le_y))
        self._le_leg = QLineEdit(str(self._layout_opts.get('legend_title', '') or ''))
        self._le_leg.textChanged.connect(lambda t: self._set_layout(legend_title=t))
        ll_.addWidget(grid_row('Legend title', self._le_leg))
        _leg_row, self._cb_legpos = legend_position_row(self._layout_opts,
                                                        self._set_layout)
        (_lo_row, self._cb_legorient, self._cb_legorder, self._cb_legclick,
         _click_row) = legend_orientation_row(self._layout_opts,
                                              self._set_layout)
        ll_.addWidget(_leg_row)
        ll_.addWidget(_lo_row)
        ll_.addWidget(_click_row)
        ll_.addStretch()

        # -- Right column: display rows, in design order --------------------
        self._ck_vals = QCheckBox('Show value labels')
        self._ck_vals.setChecked(bool(self._layout_opts.get('show_value_labels', True)))
        self._ck_vals.toggled.connect(lambda v: self._set_layout(show_value_labels=bool(v)))
        rr.addWidget(self._check_row(self._ck_vals))
        self._cb_lpos = QComboBox()
        cap_widget(self._cb_lpos, 200)
        self._cb_lpos.addItems(LPOS)
        self._cb_lpos.setCurrentText(str(self._layout_opts.get('label_position', 'outside')))
        self._cb_lpos.currentTextChanged.connect(lambda t: self._set_layout(label_position=t))
        rr.addWidget(grid_row('Label pos', self._cb_lpos))
        self._cb_bmode = QComboBox()
        cap_widget(self._cb_bmode, 200)
        self._cb_bmode.addItems(BMODES)
        self._cb_bmode.setCurrentText(str(self._layout_opts.get('bar_mode', 'group')))
        self._cb_bmode.currentTextChanged.connect(lambda t: self._set_layout(bar_mode=t))
        rr.addWidget(grid_row('Bar mode', self._cb_bmode))
        gap = slider_with_readout(0.0, 0.9, float(self._layout_opts.get('bar_gap', 0.28)), is_int=False, step=0.01)
        self._gap_slider = gap._as_slider
        self._gap_spin = gap._as_spin
        try:
            self._gap_spin.valueChanged.connect(lambda v: self._set_layout(bar_gap=float(v)))
        except Exception:
            pass
        rr.addWidget(grid_row('Bar gap', gap))
        # Bins sits with the other bar-geometry row, straight after Bar gap.
        # Range/default from the old app's histogram_bins control.
        bins = slider_with_readout(BINS_RANGE[0], BINS_RANGE[1], int(self._layout_opts.get('histogram_bins', 30)), is_int=True, step=BINS_STEP)
        self._bins_slider = bins._as_slider
        self._bins_spin = bins._as_spin
        try:
            self._bins_spin.valueChanged.connect(lambda v: self._set_layout(histogram_bins=int(v)))
        except Exception:
            pass
        rr.addWidget(grid_row('Bins', bins))
        op = slider_with_readout(OPACITY_RANGE[0], OPACITY_RANGE[1], float(self._layout_opts.get('histogram_opacity', 0.80)), is_int=False, step=OPACITY_STEP)
        self._op_slider = op._as_slider
        self._op_spin = op._as_spin
        try:
            self._op_spin.valueChanged.connect(lambda v: self._set_layout(histogram_opacity=float(v)))
        except Exception:
            pass
        rr.addWidget(grid_row('Opacity', op))
        self._ck_grid = QCheckBox('Show grid')
        self._ck_grid.setChecked(bool(self._layout_opts.get('show_grid', True)))
        self._ck_grid.toggled.connect(lambda v: self._set_layout(show_grid=bool(v)))
        rr.addWidget(self._check_row(self._ck_grid))
        self._ck_border = QCheckBox('Show border')
        self._ck_border.setChecked(bool(self._layout_opts.get('show_border', False)))
        self._ck_border.setToolTip('Border around the legend, colour bar / colour range')
        self._ck_border.toggled.connect(lambda v: self._set_layout(show_border=bool(v)))
        rr.addWidget(self._check_row(self._ck_border))
        rr.addStretch()
        cl.addWidget(left, stretch=1)
        cl.addWidget(right, stretch=1)
        rl.addWidget(cols)
        rl.addStretch()
        return root

    def prefill_layout(self, values):
        super().prefill_layout(values)
        # The two histogram controls are the only rows statistical does not
        # already know how to restore, so they are handled here.
        for attr, key, dflt in (("_bins_spin", "histogram_bins", 30),
                                ("_op_spin", "histogram_opacity", 0.80)):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setValue(float(self._layout_opts.get(key, dflt)))
                    w.blockSignals(False)
            except Exception:
                pass

