"""Statistical chart settings -- the reference template for every other analysis type."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QCheckBox, QPushButton, QLabel, QSizePolicy
from .base import (AnalysisSettingsBase, section_label, hint_label, cap_widget, multi_select_combo, slider_with_readout, color_swatch, live_preview_label, apply_preview_font, COMPACT_W, PAGE_MARGINS, PAGE_SPACING, ROW_SPACING, HINT_GAP, SECTION_GAP, page_root, section_box, grid_row)
NONE_SENTINEL = 'None'
FONTS = ['Inter', 'Source Sans 3', 'Noto Sans', 'DejaVu Sans', 'Lato', 'Ubuntu', 'Arial', 'Calibri', 'Verdana', 'Oswald', 'Barlow Condensed', 'Noto Serif', 'DejaVu Serif', 'EB Garamond', 'Georgia', 'Times New Roman', 'Fira Code', 'DejaVu Sans Mono', 'JetBrains Mono', 'Liberation Mono', 'Courier New', 'Comic Sans MS', 'Impact', 'Trebuchet MS', 'Helvetica', 'Palatino Linotype', 'Candara', 'Corbel', 'Constantia', 'Lucida Console', 'Consolas', 'Sora', 'Cambria']
FSTYLES = ['Normal', 'Bold', 'Italic', 'Bold Italic', 'Underline']
LPOS = ['outside', 'inside', 'auto']
BMODES = ['group', 'stack', 'relative', 'overlay']
LPOS_FRIENDLY = ['Top Right', 'Top Left', 'Bottom Right', 'Bottom Left']
class StatisticalSettings(AnalysisSettingsBase):
    """Generate + Layout + Typography settings for the statistical type."""
    def _build(self):
        try:
            from chart_display import default_layout_opts, default_typo_opts
            self._layout_opts = default_layout_opts()
            self._typo_opts = default_typo_opts()
        except Exception:
            self._layout_opts = {}
            self._typo_opts = {}
        self._pending = {'x': None, 'y': [], 'palette': None}
        for attr in ('_as_slider', '_as_spin', '_cb_bmode', '_cb_fam', '_cb_fstyle', '_cb_hfont', '_cb_hfstyle', '_cb_legpos', '_cb_lpos', '_ck_grid', '_ck_vals', '_combo_groupby', '_combo_metrics', '_combo_palette', '_gap_slider', '_gap_spin', '_hs_slider', '_hs_spin', '_le_leg', '_le_title', '_le_x', '_le_y', '_ls_slider', '_ls_spin', '_prev1', '_prev2', '_sw_acol', '_sw_hcol', '_sw_lcol', '_sw_tcol', '_ts_slider', '_ts_spin'):
            if not hasattr(self, attr):
                setattr(self, attr, None)
    def prefill(self, values):
        v = values or {}
        try:
            self._pending['x'] = v.get('x', '')
            self._pending['y'] = list(v.get('y', []) or [])
            self._pending['palette'] = v.get('palette', None)
        except Exception:
            pass
        try:
            if getattr(self, '_combo_groupby', None) is not None:
                opts = [NONE_SENTINEL] + list(self._group_cols or [])
                want = self._pending.get('x') or NONE_SENTINEL
                idx = opts.index(want) if want in opts else 0
                self._combo_groupby.setCurrentIndex(idx)
        except Exception:
            pass
        try:
            if getattr(self, '_combo_metrics', None) is not None:
                self._combo_metrics.set_selection(list(self._pending.get('y') or []))
        except Exception:
            pass
        try:
            if getattr(self, '_combo_palette', None) is not None and self._pending.get('palette'):
                self._combo_palette.setCurrentText(str(self._pending['palette']))
        except Exception:
            pass
    def prefill_layout(self, values):
        try:
            from chart_display import default_layout_opts
            base = default_layout_opts()
        except Exception:
            base = {}
        base.update(values or {})
        self._layout_opts = base
        for attr, key in (("_le_title", "title"), ("_le_x", "x_label"), ("_le_y", "y_label"), ("_le_leg", "legend_title")):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setText(str(self._layout_opts.get(key, "") or ""))
                    w.blockSignals(False)
            except Exception:
                pass
        for attr, key in (("_ck_vals", "show_value_labels"), ("_ck_grid", "show_grid")):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setChecked(bool(self._layout_opts.get(key, False)))
                    w.blockSignals(False)
            except Exception:
                pass
        for attr, key in (("_cb_lpos", "label_position"), ("_cb_bmode", "bar_mode"), ("_cb_legpos", "legend_position")):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setCurrentText(str(self._layout_opts.get(key, "")))
                    w.blockSignals(False)
            except Exception:
                pass
        try:
            if getattr(self, "_gap_spin", None) is not None:
                self._gap_spin.blockSignals(True)
                self._gap_spin.setValue(float(self._layout_opts.get("bar_gap", 0.28)))
                self._gap_spin.blockSignals(False)
        except Exception:
            pass
    def prefill_typography(self, values):
        try:
            from chart_display import default_typo_opts
            base = default_typo_opts()
        except Exception:
            base = {}
        base.update(values or {})
        self._typo_opts = base
        for attr, key in (("_cb_fam", "family"), ("_cb_fstyle", "font_style"), ("_cb_hfont", "header_family"), ("_cb_hfstyle", "header_font_style")):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setCurrentText(str(self._typo_opts.get(key, "")))
                    w.blockSignals(False)
            except Exception:
                pass
        for attr, key in (("_hs_spin", "header_size"), ("_ts_spin", "axis_title_size"), ("_ls_spin", "legend_item_size")):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setValue(int(self._typo_opts.get(key, 12)))
                    w.blockSignals(False)
            except Exception:
                pass
        for attr, key in (("_sw_hcol", "header_color"), ("_sw_tcol", "axis_title_color"), ("_sw_acol", "axis_tick_color"), ("_sw_lcol", "legend_item_color")):
            try:
                w = getattr(self, attr, None)
                if w is not None and hasattr(w, "_retint"):
                    w._retint(str(self._typo_opts.get(key, "")))
            except Exception:
                pass
        try:
            self._refresh_previews()
        except Exception:
            pass
    def _on_groupby(self, text):
        self._pending['x'] = None if text in ('', NONE_SENTINEL) else text
        self._mark_dirty(emit=False)
    def _on_metrics(self, sel):
        self._pending['y'] = list(sel or [])
        self._mark_dirty(emit=False)
    def _on_palette(self, text):
        self._pending['palette'] = text
        self._mark_dirty(emit=False)
    def collect_values(self):
        try:
            x = self._pending.get('x')
            y = list(self._pending.get('y') or [])
            pal = self._pending.get('palette')
        except Exception:
            x, y, pal = None, [], None
        return {'x': x, 'y': y, 'palette': pal}
    def build_generate(self):
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label('Adjust options below. All selections are live \u2014 no submit needed until Generate.'))
        cols = QWidget()
        cl = QHBoxLayout(cols)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(SECTION_GAP)
        gbody = QWidget()
        gl = QVBoxLayout(gbody)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(HINT_GAP)
        self._combo_groupby = QComboBox()
        cap_widget(self._combo_groupby)
        opts = [NONE_SENTINEL] + list(self._group_cols or [])
        self._combo_groupby.addItems(opts)
        cur = self._pending.get('x')
        idx = opts.index(cur) if cur in opts else 0
        self._combo_groupby.setCurrentIndex(idx if idx >= 0 else 0)
        if not self._pending.get('x'):
            self._pending['x'] = None if self._combo_groupby.currentIndex() <= 0 else self._combo_groupby.currentText()
        self._combo_groupby.currentTextChanged.connect(self._on_groupby)
        gl.addWidget(self._combo_groupby)
        gl.addStretch()
        cl.addWidget(section_box('Group by (optional)', gbody, 'None = summarise the whole dataset'), stretch=1)
        mbody = QWidget()
        ml = QVBoxLayout(mbody)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(HINT_GAP)
        self._combo_metrics = multi_select_combo(list(self._num_cols or []))
        try:
            self._combo_metrics.set_selection(list(self._pending.get('y') or []))
        except Exception:
            pass
        try:
            self._combo_metrics.selection_changed.connect(self._on_metrics)
        except Exception:
            pass
        ml.addWidget(self._combo_metrics)
        ml.addStretch()
        cl.addWidget(section_box('Metrics', mbody, 'Dropdown with checkboxes; click to expand'), stretch=1)
        pbody = QWidget()
        pl = QVBoxLayout(pbody)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(HINT_GAP)
        self._combo_palette = QComboBox()
        cap_widget(self._combo_palette)
        pals = list(self._palette_names or ['Default'])
        self._combo_palette.addItems(pals)
        if self._pending.get('palette') in pals:
            self._combo_palette.setCurrentText(str(self._pending['palette']))
        else:
            self._pending['palette'] = self._combo_palette.currentText()
        self._combo_palette.currentTextChanged.connect(self._on_palette)
        pl.addWidget(self._combo_palette)
        pl.addStretch()
        cl.addWidget(section_box('\U0001f3a8 Palette', pbody), stretch=1)
        rl.addWidget(cols)
        rl.addStretch()
        return root
    def _section(self, title, body='', hint=''):
        return section_box(title, body, hint) if isinstance(body, QWidget) else section_box(title, QWidget())
    def _pill_row(self, label, widget, label_w=None):
        return grid_row(label, widget, label_w or 128)
    def _text_row(self, label, key, placeholder=''):
        le = QLineEdit()
        try:
            le.setText(str(self._layout_opts.get(key, '') or ''))
        except Exception:
            pass
        if placeholder:
            le.setPlaceholderText(placeholder)
        le.textChanged.connect(lambda t, k=key: self._set_layout(**{k: t}))
        return grid_row(label, le)
    def build_layout(self):
        root, rl = page_root()
        rl.addWidget(hint_label('\u2728 Live Preview \u2014 changes appear instantly on the chart \u2192'))
        self._le_title = QLineEdit(str(self._layout_opts.get('title', '') or ''))
        self._le_title.setPlaceholderText('Statistics of <metric> by <group>')
        self._le_title.textChanged.connect(lambda t: self._set_layout(title=t))
        rl.addWidget(grid_row('Chart title', self._le_title))
        self._le_x = QLineEdit(str(self._layout_opts.get('x_label', '') or ''))
        self._le_x.textChanged.connect(lambda t: self._set_layout(x_label=t))
        rl.addWidget(grid_row('X-axis', self._le_x))
        self._le_y = QLineEdit(str(self._layout_opts.get('y_label', '') or ''))
        self._le_y.textChanged.connect(lambda t: self._set_layout(y_label=t))
        rl.addWidget(grid_row('Y-axis', self._le_y))
        self._le_leg = QLineEdit(str(self._layout_opts.get('legend_title', '') or ''))
        self._le_leg.textChanged.connect(lambda t: self._set_layout(legend_title=t))
        rl.addWidget(grid_row('Legend title', self._le_leg))
        self._ck_vals = QCheckBox('Show value labels')
        self._ck_vals.setChecked(bool(self._layout_opts.get('show_value_labels', False)))
        self._ck_vals.toggled.connect(lambda v: self._set_layout(show_value_labels=bool(v)))
        rl.addWidget(grid_row('Labels', self._ck_vals))
        self._cb_lpos = QComboBox()
        cap_widget(self._cb_lpos, 200)
        self._cb_lpos.addItems(LPOS)
        self._cb_lpos.setCurrentText(str(self._layout_opts.get('label_position', 'outside')))
        self._cb_lpos.currentTextChanged.connect(lambda t: self._set_layout(label_position=t))
        rl.addWidget(grid_row('Label pos', self._cb_lpos))
        self._cb_bmode = QComboBox()
        cap_widget(self._cb_bmode, 200)
        self._cb_bmode.addItems(BMODES)
        self._cb_bmode.setCurrentText(str(self._layout_opts.get('bar_mode', 'group')))
        self._cb_bmode.currentTextChanged.connect(lambda t: self._set_layout(bar_mode=t))
        rl.addWidget(grid_row('Bar mode', self._cb_bmode))
        gap = slider_with_readout(0.0, 0.9, float(self._layout_opts.get('bar_gap', 0.28)), is_int=False, step=0.01)
        self._gap_slider = gap._as_slider
        self._gap_spin = gap._as_spin
        try:
            self._gap_spin.valueChanged.connect(lambda v: self._set_layout(bar_gap=float(v)))
        except Exception:
            pass
        rl.addWidget(grid_row('Bar gap', gap))
        self._ck_grid = QCheckBox('Show grid')
        self._ck_grid.setChecked(bool(self._layout_opts.get('show_grid', True)))
        self._ck_grid.toggled.connect(lambda v: self._set_layout(show_grid=bool(v)))
        rl.addWidget(grid_row('Grid', self._ck_grid))
        self._cb_legpos = QComboBox()
        cap_widget(self._cb_legpos, 200)
        self._cb_legpos.addItems(LPOS_FRIENDLY)
        self._cb_legpos.setCurrentText(str(self._layout_opts.get('legend_position', 'Top Right')))
        self._cb_legpos.currentTextChanged.connect(lambda t: self._set_layout(legend_position=t))
        rl.addWidget(grid_row('Legend position', self._cb_legpos))
        rl.addStretch()
        return root
    def _set_layout(self, emit=True, **kw):
        try:
            self._layout_opts.update(kw)
        except Exception:
            pass
        if emit:
            try:
                self.values_changed.emit({'layout': dict(self._layout_opts)})
            except Exception:
                pass
    def build_typography(self):
        root, rl = page_root()
        rl.addWidget(hint_label('\u2728 Live Preview \u2014 changes appear instantly on the chart \u2192'))
        self._cb_fam = QComboBox()
        cap_widget(self._cb_fam, 220)
        self._cb_fam.addItems(FONTS)
        self._cb_fam.setCurrentText(str(self._typo_opts.get('family', 'Inter')))
        self._cb_fam.currentTextChanged.connect(lambda t: self._set_typo(family=t))
        rl.addWidget(grid_row('Font family', self._cb_fam))
        self._cb_fstyle = QComboBox()
        cap_widget(self._cb_fstyle, 220)
        self._cb_fstyle.addItems(FSTYLES)
        self._cb_fstyle.setCurrentText(str(self._typo_opts.get('font_style', 'Normal')))
        self._cb_fstyle.currentTextChanged.connect(lambda t: self._set_typo(font_style=t))
        rl.addWidget(grid_row('Font style', self._cb_fstyle))
        self._prev1 = live_preview_label()
        rl.addWidget(self._prev1)
        hs = slider_with_readout(10, 60, int(self._typo_opts.get('header_size', 28)))
        self._hs_slider = hs._as_slider
        self._hs_spin = hs._as_spin
        try:
            self._hs_spin.valueChanged.connect(lambda v: self._set_typo(header_size=int(v)))
        except Exception:
            pass
        rl.addWidget(grid_row('Header size', hs))
        self._sw_hcol = color_swatch(str(self._typo_opts.get('header_color', '#6163df')), lambda c: self._set_typo(header_color=c))
        rl.addWidget(grid_row('Header color', self._sw_hcol))
        self._cb_hfont = QComboBox()
        cap_widget(self._cb_hfont, 220)
        self._cb_hfont.addItems(FONTS)
        self._cb_hfont.setCurrentText(str(self._typo_opts.get('header_family', 'Inter')))
        self._cb_hfont.currentTextChanged.connect(lambda t: self._set_typo(header_family=t))
        rl.addWidget(grid_row('Header font', self._cb_hfont))
        self._cb_hfstyle = QComboBox()
        cap_widget(self._cb_hfstyle, 220)
        self._cb_hfstyle.addItems(FSTYLES)
        self._cb_hfstyle.setCurrentText(str(self._typo_opts.get('header_font_style', 'Normal')))
        self._cb_hfstyle.currentTextChanged.connect(lambda t: self._set_typo(header_font_style=t))
        rl.addWidget(grid_row('Header font style', self._cb_hfstyle))
        self._prev2 = live_preview_label()
        rl.addWidget(self._prev2)
        ts = slider_with_readout(8, 32, int(self._typo_opts.get('axis_title_size', 12)))
        self._ts_slider = ts._as_slider
        self._ts_spin = ts._as_spin
        try:
            self._ts_spin.valueChanged.connect(lambda v: self._set_typo(axis_title_size=int(v)))
        except Exception:
            pass
        rl.addWidget(grid_row('Title size', ts))
        self._sw_tcol = color_swatch(str(self._typo_opts.get('axis_title_color', '#cbd5e1')), lambda c: self._set_typo(axis_title_color=c))
        rl.addWidget(grid_row('Title color', self._sw_tcol))
        axs = slider_with_readout(8, 24, int(self._typo_opts.get('axis_tick_size', 10)))
        self._ts_slider = axs._as_slider
        try:
            axs._as_spin.valueChanged.connect(lambda v: self._set_typo(axis_tick_size=int(v)))
        except Exception:
            pass
        rl.addWidget(grid_row('Axis size', axs))
        self._sw_acol = color_swatch(str(self._typo_opts.get('axis_tick_color', '#94a3b8')), lambda c: self._set_typo(axis_tick_color=c))
        rl.addWidget(grid_row('Axis color', self._sw_acol))
        ls = slider_with_readout(8, 24, int(self._typo_opts.get('legend_item_size', 11)))
        self._ls_slider = ls._as_slider
        self._ls_spin = ls._as_spin
        try:
            ls._as_spin.valueChanged.connect(lambda v: self._set_typo(legend_item_size=int(v), legend_title_size=int(v)))
        except Exception:
            pass
        rl.addWidget(grid_row('Legend size', ls))
        self._sw_lcol = color_swatch(str(self._typo_opts.get('legend_item_color', '#e2e8f0')), lambda c: self._set_typo(legend_item_color=c, legend_title_color=c))
        rl.addWidget(grid_row('Legend color', self._sw_lcol))
        rl.addStretch()
        self._refresh_previews()
        return root
    def _set_typo(self, emit=True, **kw):
        no_emit = kw.pop('_no_emit', False) if isinstance(kw, dict) else False
        try:
            self._typo_opts.update(kw)
        except Exception:
            pass
        try:
            self._refresh_previews()
        except Exception:
            pass
        if emit and not no_emit:
            try:
                self.values_changed.emit({'typography': dict(self._typo_opts)})
            except Exception:
                pass
    def _refresh_previews(self):
        try:
            if getattr(self, '_prev1', None) is not None:
                apply_preview_font(self._prev1, str(self._typo_opts.get('family', 'Inter')), str(self._typo_opts.get('font_style', 'Normal')))
        except Exception:
            pass
        try:
            if getattr(self, '_prev2', None) is not None:
                apply_preview_font(self._prev2, str(self._typo_opts.get('header_family', 'Inter')), str(self._typo_opts.get('header_font_style', 'Normal')), int(self._typo_opts.get('header_size', 28)))
        except Exception:
            pass
