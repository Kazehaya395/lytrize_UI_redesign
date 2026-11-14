"""Statistical chart settings -- the reference template for every other analysis type."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QCheckBox, QPushButton, QLabel, QSizePolicy
from .base import (AnalysisSettingsBase, section_label, hint_label, cap_widget, multi_select_combo, slider_with_readout, color_swatch, live_preview_label, apply_preview_font, legend_position_row, legend_orientation_row, colorbar_position_row, COMPACT_W, GEN_W, PAGE_MARGINS, PAGE_SPACING, ROW_SPACING, HINT_GAP, SECTION_GAP, page_root, section_box, grid_row, FlowGrid)
from desktop.theme import group_label_style
NONE_SENTINEL = ''
# The "no grouping" entry renders as a blank field, not the literal word
# "None". "None" read like an actual selectable value sitting in the combo,
# and the backend already treats both '' and 'None' as "no grouping"
# (see analysis_service._single_choice_value), so a blank is unambiguous.
# The hint under the field carries the meaning instead.
# Only offer font families that are actually installed. Offering 33 names when
# the machine has 5 of them is what made the picker look broken: every missing
# family silently fell back to the same default, so most choices changed
# nothing. chart_display.available_fonts() resolves this from fontconfig once
# and caches it; the static list stays as the ordered wish-list.
_FONT_WISHLIST = ['Inter', 'Source Sans 3', 'Noto Sans', 'DejaVu Sans',
                  'Lato', 'Ubuntu', 'Arial', 'Calibri', 'Verdana', 'Oswald',
                  'Barlow Condensed', 'Noto Serif', 'DejaVu Serif',
                  'EB Garamond', 'Georgia', 'Times New Roman', 'Fira Code',
                  'DejaVu Sans Mono', 'JetBrains Mono', 'Liberation Mono',
                  'Courier New', 'Comic Sans MS', 'Impact', 'Trebuchet MS',
                  'Helvetica', 'Palatino Linotype', 'Candara', 'Corbel',
                  'Constantia', 'Lucida Console', 'Consolas', 'Sora',
                  'Cambria']


def _fonts() -> list:
    """Installed font choices, with the app defaults first when present."""
    try:
        from chart_display import available_fonts
        return [f["family"] for f in available_fonts(prefer=['Inter'])]
    except Exception:
        return ['DejaVu Sans', 'DejaVu Serif', 'DejaVu Sans Mono']


FSTYLES = ['Normal', 'Bold', 'Italic', 'Bold Italic', 'Underline']
LPOS = ['outside', 'inside', 'auto']
BMODES = ['group', 'stack', 'relative', 'overlay']
# The legend placement choices are NOT listed here: they are read from the
# backend by ``legend_position_row`` (see ``.base``) so the UI can never offer a
# position the renderer does not understand. A second local list is exactly how
# a control ends up offering a value that silently renders as the default.
# Header alignment. Lower-case keys are what chart_display stores; the
# friendly labels are what the combo shows. Kept in the same style as the
# other option lists above.
HALIGNS = ['left', 'center', 'right']
HALIGN_LABELS = {'left': 'Left', 'center': 'Center', 'right': 'Right'}
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
        for attr in ('_as_slider', '_as_spin', '_axs_slider', '_axs_spin', '_cb_bmode', '_cb_fam', '_cb_fstyle', '_cb_halign', '_cb_hfont', '_cb_hfstyle', '_cb_legclick', '_cb_legorder', '_cb_legorient', '_cb_legpos', '_cb_lpos', '_ck_border', '_ck_grid', '_ck_vals', '_combo_groupby', '_combo_metrics', '_combo_palette', '_gap_slider', '_gap_spin', '_hs_slider', '_hs_spin', '_le_leg', '_le_title', '_le_x', '_le_y', '_ls_slider', '_ls_spin', '_prev1', '_prev2', '_sw_acol', '_sw_hcol', '_sw_lcol', '_sw_tcol', '_ts_slider', '_ts_spin', '_vs_spin', '_sw_vcol', '_ck_vauto', '_tips_spin', '_sw_tipcol', '_ck_tipauto'):
            if not hasattr(self, attr):
                setattr(self, attr, None)
    def prefill(self, values):
        self._suppress = True
        try:
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
        finally:
            self._suppress = False
    def prefill_layout(self, values):
        self._suppress = True
        try:
            try:
                from chart_display import default_layout_opts
                base = default_layout_opts()
            except Exception:
                base = {}
            base.update(values or {})
            self._layout_opts = base
            for attr, key in (("_le_title", "title"), ("_le_x", "x_label"), ("_le_y", "y_label"),
                              ("_le_y2", "y2_label"), ("_le_leg", "legend_title"),
                              ("_le_cb", "colorbar_title"), ("_le_cb_title", "colorbar_title")):
                try:
                    w = getattr(self, attr, None)
                    if w is not None:
                        w.blockSignals(True)
                        w.setText(str(self._layout_opts.get(key, "") or ""))
                        w.blockSignals(False)
                except Exception:
                    pass
            for attr, key in (("_ck_vals", "show_value_labels"), ("_ck_grid", "show_grid"),
                              ("_ck_border", "show_border"), ("_ck_markers", "show_markers"),
                              ("_ck_ann", "heatmap_show_text"), ("_ck_annot", "heatmap_show_text"),
                              ("_cb_center_total", "donut_center_total")):
                try:
                    w = getattr(self, attr, None)
                    if w is not None:
                        w.blockSignals(True)
                        w.setChecked(bool(self._layout_opts.get(key, False)))
                        w.blockSignals(False)
                except Exception:
                    pass
            for attr, key, dflt in (("_cb_lpos", "label_position", "outside"),
                                    ("_cb_bmode", "bar_mode", "group"),
                                    ("_cb_legpos", "legend_position", "Top Right"),
                                    ("_cb_legorient", "legend_orientation", "Auto"),
                                    ("_cb_legorder", "legend_order", "Normal"),
                                    ("_cb_legclick", "legend_itemclick", "toggle"),
                                    ("_cb_textinfo", "pie_textinfo", "label+percent"),
                                    ("_cb_pdir", "pie_direction", "clockwise"),
                                    ("_cb_lshape", "line_shape", "linear"),
                                    ("_cb_lfill", "line_fill", "none"),
                                    ("_cb_cbpos", "colorbar_position", "right"),
                                    ("_cb_cb_pos", "colorbar_position", "right"),
                                    ("_cb_cborient", "colorbar_orientation", "auto"),
                                    ("_cb_cb_orient", "colorbar_orientation", "auto"),
                                    ("_cb_cs", "heatmap_colorscale", "RdBu"),
                                    ("_cb_rmode", "render_mode", "svg"),
                                    ("_cb_ialign", "table_index_align", "left"),
                                    ("_cb_dalign", "table_data_align", "right"),
                                    ("_cb_numfmt", "table_number_format", ",.2f"),
                                    ("_cb_basemap", "map_basemap", "monochrome"),
                                    ("_cb_proj", "map_projection", "natural earth")):
                try:
                    w = getattr(self, attr, None)
                    if w is None:
                        continue
                    w.blockSignals(True)
                    want = str(self._layout_opts.get(key, dflt) or dflt)
                    friendly = {"toggle": "Toggle item",
                                "toggleothers": "Toggle others",
                                "false": "Do nothing"}
                    if key == "legend_itemclick":
                        want = friendly.get(want, "")
                        if want not in [w.itemText(i) for i in range(w.count())]:
                            want = friendly[dflt]
                            self._layout_opts[key] = dflt
                    elif want not in [w.itemText(i) for i in range(w.count())]:
                        want = dflt
                        self._layout_opts[key] = want
                    w.setCurrentText(want)
                    w.blockSignals(False)
                except Exception:
                    pass
            for attr, key, dflt in (("_gap_spin", "bar_gap", 0.28),
                                    ("_lw_spin", "line_width", 2),
                                    ("_bins_spin", "histogram_bins", 30),
                                    ("_op_spin", "histogram_opacity", 0.8),
                                    ("_dh_spin", "donut_hole", 0.0),
                                    ("_spin_donut", "donut_hole", 0.0),
                                    ("_pl_spin", "pull_slices", 0.0),
                                    ("_spin_pull", "pull_slices", 0.0),
                                    ("_pr_spin", "pie_rotation", 0),
                                    ("_spin_rot", "pie_rotation", 0),
                                    ("_pvs_spin", "pie_value_size", 11),
                                    ("_spin_pval", "pie_value_size", 11),
                                    ("_ms_spin", "marker_size", 6),
                                    ("_spin_msize", "marker_size", 6),
                                    ("_spin_ssize", "marker_size", 6),
                                    ("_mo_spin", "marker_opacity", 0.8),
                                    ("_spin_sopacity", "marker_opacity", 0.8),
                                    ("_cbtsz_spin", "colorbar_title_size", 11),
                                    ("_spin_cbtitle_sz", "colorbar_title_size", 11),
                                    ("_cbts_spin", "colorbar_tick_size", 10),
                                    ("_spin_cbtick_sz", "colorbar_tick_size", 10),
                                    ("_prec_spin", "heatmap_annotation_precision", 2),
                                    ("_spin_annot_prec", "heatmap_annotation_precision", 2),
                                    ("_ann_spin", "heatmap_annotation_size", 10),
                                    ("_spin_annot_sz", "heatmap_annotation_size", 10),
                                    ("_thh_spin", "table_header_height", 28),
                                    ("_rqh_spin", "table_header_height", 28)):
                try:
                    w = getattr(self, attr, None)
                    if w is not None:
                        w.blockSignals(True)
                        val = self._layout_opts.get(key, dflt)
                        if isinstance(dflt, float):
                            w.setValue(float(val if val is not None else dflt))
                        else:
                            w.setValue(int(val if val is not None else dflt))
                        w.blockSignals(False)
                except Exception:
                    pass
            for attr, key in (("_sw_pvc", "pie_value_color"), ("_sw_pcol", "pie_value_color"),
                              ("_sw_cbtclr", "colorbar_title_color"), ("_sw_cbtitle_col", "colorbar_title_color"),
                              ("_sw_cbtc", "colorbar_tick_color"), ("_sw_cbtick_col", "colorbar_tick_color"),
                              ("_sw_ann", "heatmap_annotation_color"), ("_sw_annot_col", "heatmap_annotation_color")):
                try:
                    w = getattr(self, attr, None)
                    if w is not None and hasattr(w, "_retint"):
                        val = str(self._layout_opts.get(key, "auto") or "auto")
                        w._retint(val)
                except Exception:
                    pass
        finally:
            self._suppress = False
    def _halign_key(self, label):
        """'Center' -> 'center'; anything unrecognised falls back to 'left'."""
        text = str(label or '').strip().lower()
        return text if text in HALIGNS else 'left'

    def prefill_typography(self, values):
        self._suppress = True
        try:
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
            for attr, key, dflt in (("_hs_spin", "header_size", 28), ("_ts_spin", "axis_title_size", 16), ("_axs_spin", "axis_tick_size", 12), ("_ls_spin", "legend_item_size", 11), ("_vs_spin", "value_label_size", 11), ("_tips_spin", "tooltip_size", 12)):
                try:
                    w = getattr(self, attr, None)
                    if w is not None:
                        w.blockSignals(True)
                        w.setValue(int(self._typo_opts.get(key, dflt)))
                        w.blockSignals(False)
                except Exception:
                    pass
            try:
                w = getattr(self, "_cb_halign", None)
                if w is not None:
                    _a = self._halign_key(self._typo_opts.get("header_align", "left"))
                    w.blockSignals(True)
                    w.setCurrentText(HALIGN_LABELS[_a])
                    w.blockSignals(False)
            except Exception:
                pass
            try:
                from desktop.theme import current_mode
                mode = current_mode()
            except Exception:
                mode = "light"
            auto_ink = "#f8fafc" if mode == "dark" else "#111827"
            for attr, key in (("_sw_hcol", "header_color"), ("_sw_tcol", "axis_title_color"), ("_sw_acol", "axis_tick_color"), ("_sw_lcol", "legend_item_color"), ("_sw_vcol", "value_label_color"), ("_sw_tipcol", "tooltip_color")):
                try:
                    w = getattr(self, attr, None)
                    if w is not None and hasattr(w, "_retint"):
                        value = str(self._typo_opts.get(key, ""))
                        w._retint("auto" if value.lower() in ("", "auto", "none") else value)
                except Exception:
                    pass
            for attr, key in (("_ck_vauto", "value_label_color"), ("_ck_tipauto", "tooltip_color")):
                try:
                    check = getattr(self, attr, None)
                    if check is not None:
                        check.blockSignals(True)
                        check.setChecked(str(self._typo_opts.get(key, "auto")).lower() == "auto")
                        check.blockSignals(False)
                        swatch = getattr(self, "_sw_vcol" if key == "value_label_color" else "_sw_tipcol", None)
                        if swatch is not None:
                            swatch.setEnabled(not check.isChecked())
                except Exception:
                    pass
            try:
                self._refresh_previews()
            except Exception:
                pass
        except Exception:
            pass
        finally:
            self._suppress = False
    def _on_groupby(self, text):
        self._pending['x'] = None if text in ('', NONE_SENTINEL) else text
        # emit=True so the host panel learns the form is dirty (Re-Generate
        # stays disabled until something actually changed).
        self._mark_dirty(emit=True)
    def _on_metrics(self, sel):
        self._pending['y'] = list(sel or [])
        self._mark_dirty(emit=True)
    def _on_palette(self, text):
        self._pending['palette'] = text
        self._mark_dirty(emit=True)
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
        # Sections flow across the pane (see FlowGrid): as many per line as
        # fit, each line stretched to the pane's full width, the remainder on
        # the next line at the left. A fixed three-per-row grid instead left
        # the right-hand side of the tab empty and made the page taller than
        # the pane it sits in.
        flow = FlowGrid()
        gbody = QWidget()
        gl = QVBoxLayout(gbody)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(HINT_GAP)
        self._combo_groupby = QComboBox()
        cap_widget(self._combo_groupby, GEN_W)
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
        flow.add_section(section_box('Group by (optional)', gbody, 'Leave blank to summarise the whole dataset'))
        mbody = QWidget()
        ml = QVBoxLayout(mbody)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(HINT_GAP)
        self._combo_metrics = multi_select_combo(list(self._num_cols or []))
        cap_widget(self._combo_metrics, GEN_W)
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
        flow.add_section(section_box('Metrics', mbody, 'One or more numeric columns'))
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
    @staticmethod
    def _check_row(box):
        """A checkbox that carries its own label (design: the tick box sits
        in the label column instead of getting a separate leading label)."""
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(ROW_SPACING)
        hl.addWidget(box)
        hl.addStretch()
        return row
    @staticmethod
    def _option_column():
        """A vertical stack of option rows.

        The Layout/Typography tabs lay several of these side by side (as in
        chart_designs.pdf) so every control stays visible inside the short
        top pane of the analysis splitter instead of clipping/scrolling.

        The ``option_column`` property is how a builder that inherits this page
        finds the column again in order to append its own type-specific rows to
        it (see ``AnalysisSettingsBase._add_option_row``).
        """
        col = QWidget()
        col.setProperty('option_column', True)
        col.setProperty('option_column', True)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(HINT_GAP)
        return col, lay
    def build_layout(self):
        root, rl = page_root()
        cols = QWidget()
        cl = QHBoxLayout(cols)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(SECTION_GAP)
        left, ll_ = self._option_column()
        right, rr = self._option_column()
        rl.addWidget(hint_label('\u2728 Live Preview \u2014 changes appear instantly on the chart \u2192'))
        # -- Left column: chart / axis / legend titles (chart_designs.pdf) --
        self._le_title = QLineEdit(str(self._layout_opts.get('title', '') or ''))
        self._le_title.setPlaceholderText('Statistics of <metric> by <group>')
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
        # The mockup's Layout tab lists no legend-position row; it stays with
        # the other legend control (still exercised) rather than being dropped.
        # All eight placements are offered, plus the direction the items run in
        # and the order they appear -- a Top/Bottom band is only legible when
        # the row is horizontal, and a reversed order is what makes a stacked
        # band read top-down instead of bottom-up.
        _leg_row, self._cb_legpos = legend_position_row(self._layout_opts,
                                                        self._set_layout)
        (_lo_row, self._cb_legorient, self._cb_legorder, self._cb_legclick,
         _click_row) = legend_orientation_row(self._layout_opts,
                                              self._set_layout)
        ll_.addWidget(_leg_row)
        ll_.addWidget(_lo_row)
        ll_.addWidget(_click_row)
        ll_.addStretch()
        # -- Right column: the design's display rows, in design order --------
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
        self._ck_grid = QCheckBox('Show grid')
        self._ck_grid.setChecked(bool(self._layout_opts.get('show_grid', True)))
        self._ck_grid.toggled.connect(lambda v: self._set_layout(show_grid=bool(v)))
        rr.addWidget(self._check_row(self._ck_grid))
        # Sits directly under "Show grid" (design order) and controls every
        # frame the chart draws around a key: the legend plate and the colour
        # bar / colour range.  Checked by default -- that is the look the
        # charts already had, so this is a control over the existing border,
        # not a new decoration.
        self._ck_border = QCheckBox('Show border')
        self._ck_border.setChecked(bool(self._layout_opts.get('show_border', False)))
        self._ck_border.setToolTip(
            'Border around the legend, colour bar / colour range')
        self._ck_border.toggled.connect(lambda v: self._set_layout(show_border=bool(v)))
        rr.addWidget(self._check_row(self._ck_border))
        rr.addStretch()
        cl.addWidget(left, stretch=1)
        cl.addWidget(right, stretch=1)
        rl.addWidget(cols)
        rl.addStretch()
        return root
    def _set_layout(self, emit=True, **kw):
        try:
            self._layout_opts.update(kw)
        except Exception:
            pass
        # Layout edits count as changes too: they dirty the form (gates
        # Re-Generate) even though they also live-apply to the chart.
        self._dirty = True
        if emit and not getattr(self, "_suppress", False):
            try:
                self.values_changed.emit({'layout': dict(self._layout_opts)})
            except Exception:
                pass
    def build_typography(self):
        root, rl = page_root()
        cols = QWidget()
        cl = QHBoxLayout(cols)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(SECTION_GAP)
        # Narrow label column: these three stacks sit side by side, so the
        # controls need the horizontal room (chart_designs.pdf).
        label_w = 104
        body, bl = self._option_column()
        head, hl = self._option_column()
        axis, al = self._option_column()
        rl.addWidget(hint_label('\u2728 Live Preview \u2014 changes appear instantly on the chart \u2192'))
        # -- Column 1: body font (family / style) + its live preview --------
        self._cb_fam = QComboBox()
        cap_widget(self._cb_fam, 220)
        fonts = _fonts()
        self._cb_fam.addItems(fonts)
        # Inter is the documented default but is not installed everywhere; if it
        # is missing, setCurrentText() silently leaves the combo on item 0 while
        # _typo_opts still says "Inter", so the chart would keep asking for a
        # font that does not exist. Snap the stored value to what is listed.
        want = str(self._typo_opts.get('family', 'Inter'))
        if want not in fonts and fonts:
            want = fonts[0]
            self._typo_opts['family'] = want
        self._cb_fam.setCurrentText(want)
        self._cb_fam.currentTextChanged.connect(lambda t: self._set_typo(discrete=True, family=t))
        bl.addWidget(grid_row('Font family', self._cb_fam, label_w))
        self._cb_fstyle = QComboBox()
        cap_widget(self._cb_fstyle, 220)
        self._cb_fstyle.addItems(FSTYLES)
        self._cb_fstyle.setCurrentText(str(self._typo_opts.get('font_style', 'Normal')))
        self._cb_fstyle.currentTextChanged.connect(lambda t: self._set_typo(discrete=True, font_style=t))
        bl.addWidget(grid_row('Font style', self._cb_fstyle, label_w))
        self._prev1 = live_preview_label()
        bl.addWidget(self._prev1)
        value_size = slider_with_readout(8, 32, int(self._typo_opts.get('value_label_size', 11)))
        self._vs_spin = value_size._as_spin
        self._vs_spin.valueChanged.connect(lambda v: self._set_typo(value_label_size=int(v)))
        bl.addWidget(grid_row('Value size', value_size, label_w))
        value_color_row, self._ck_vauto, self._sw_vcol = self._auto_color_control(
            'value_label_color', 'Value color', label_w)
        bl.addWidget(value_color_row)
        bl.addStretch()
        # -- Column 2: the header (the chart title) + its live preview ------
        hs = slider_with_readout(10, 60, int(self._typo_opts.get('header_size', 28)))
        self._hs_slider = hs._as_slider
        self._hs_spin = hs._as_spin
        try:
            self._hs_spin.valueChanged.connect(lambda v: self._set_typo(header_size=int(v)))
        except Exception:
            pass
        hl.addWidget(grid_row('Header size', hs, label_w))
        self._sw_hcol = color_swatch(str(self._typo_opts.get('header_color', 'auto')), lambda c: self._set_typo(header_color=c))
        hl.addWidget(grid_row('Header color', self._sw_hcol, label_w))
        self._cb_hfont = QComboBox()
        cap_widget(self._cb_hfont, 220)
        self._cb_hfont.addItems(fonts)
        want_h = str(self._typo_opts.get('header_family', 'Inter'))
        if want_h not in fonts and fonts:
            want_h = fonts[0]
            self._typo_opts['header_family'] = want_h
        self._cb_hfont.setCurrentText(want_h)
        self._cb_hfont.currentTextChanged.connect(lambda t: self._set_typo(discrete=True, header_family=t))
        hl.addWidget(grid_row('Header font', self._cb_hfont, label_w))
        self._cb_hfstyle = QComboBox()
        cap_widget(self._cb_hfstyle, 220)
        self._cb_hfstyle.addItems(FSTYLES)
        self._cb_hfstyle.setCurrentText(str(self._typo_opts.get('header_font_style', 'Normal')))
        self._cb_hfstyle.currentTextChanged.connect(lambda t: self._set_typo(discrete=True, header_font_style=t))
        hl.addWidget(grid_row('Header font style', self._cb_hfstyle, label_w))
        # Header alignment. Sits with the other header controls because it is
        # a property of the header; the canvas-wide anchors in
        # chart_display make it auto-adjust to the card width and cover the
        # legend column as well as the plot.
        self._cb_halign = QComboBox()
        cap_widget(self._cb_halign, 220)
        self._cb_halign.addItems([HALIGN_LABELS[a] for a in HALIGNS])
        _cur_halign = str(self._typo_opts.get('header_align', 'left') or 'left').strip().lower()
        if _cur_halign not in HALIGNS:
            _cur_halign = 'left'
        self._cb_halign.setCurrentText(HALIGN_LABELS[_cur_halign])
        self._cb_halign.currentTextChanged.connect(
            lambda lbl: self._set_typo(
                header_align=self._halign_key(lbl)))
        hl.addWidget(grid_row('Header align', self._cb_halign, label_w))
        self._prev2 = live_preview_label()
        hl.addWidget(self._prev2)
        hl.addStretch()
        cl.addWidget(head, stretch=1)
        # -- Column 3: axis + legend typography -----------------------------
        ts = slider_with_readout(8, 32, int(self._typo_opts.get('axis_title_size', 16)))
        self._ts_slider = ts._as_slider
        self._ts_spin = ts._as_spin
        try:
            self._ts_spin.valueChanged.connect(lambda v: self._set_typo(axis_title_size=int(v)))
        except Exception:
            pass
        al.addWidget(grid_row('Title size', ts, label_w))
        self._sw_tcol = color_swatch(str(self._typo_opts.get('axis_title_color', 'auto')), lambda c: self._set_typo(axis_title_color=c))
        al.addWidget(grid_row('Title color', self._sw_tcol, label_w))
        axs = slider_with_readout(8, 24, int(self._typo_opts.get('axis_tick_size', 12)))
        self._axs_slider = axs._as_slider
        self._axs_spin = axs._as_spin
        try:
            self._axs_spin.valueChanged.connect(lambda v: self._set_typo(axis_tick_size=int(v)))
        except Exception:
            pass
        al.addWidget(grid_row('Axis size', axs, label_w))
        self._sw_acol = color_swatch(str(self._typo_opts.get('axis_tick_color', 'auto')), lambda c: self._set_typo(axis_tick_color=c))
        al.addWidget(grid_row('Axis color', self._sw_acol, label_w))
        ls = slider_with_readout(8, 24, int(self._typo_opts.get('legend_item_size', 11)))
        self._ls_slider = ls._as_slider
        self._ls_spin = ls._as_spin
        try:
            self._ls_spin.valueChanged.connect(lambda v: self._set_typo(legend_item_size=int(v), legend_title_size=int(v)))
        except Exception:
            pass
        al.addWidget(grid_row('Legend size', ls, label_w))
        self._sw_lcol = color_swatch(str(self._typo_opts.get('legend_item_color', 'auto')), lambda c: self._set_typo(legend_item_color=c, legend_title_color=c))
        al.addWidget(grid_row('Legend color', self._sw_lcol, label_w))
        tooltip_size = slider_with_readout(8, 32, int(self._typo_opts.get('tooltip_size', 12)))
        self._tips_spin = tooltip_size._as_spin
        self._tips_spin.valueChanged.connect(lambda v: self._set_typo(tooltip_size=int(v)))
        al.addWidget(grid_row('Tooltip size', tooltip_size, label_w))
        tooltip_color_row, self._ck_tipauto, self._sw_tipcol = self._auto_color_control(
            'tooltip_color', 'Tooltip color', label_w)
        al.addWidget(tooltip_color_row)
        al.addStretch()
        cl.addWidget(body, stretch=1)
        cl.addWidget(head, stretch=1)
        cl.addWidget(axis, stretch=1)
        rl.addWidget(cols)
        rl.addStretch()
        self._refresh_previews()
        return root
    def _auto_color_control(self, key, label, label_w=104):
        """Color picker with a theme-aware automatic-contrast default.

        Built as one compact widget and handed to ``grid_row`` -- exactly how
        every other typography control is built -- so the Auto tick and the
        swatch sit hard against the caption on the same left edge as the Value
        size / Font style controls, instead of a bespoke row whose spacing
        drifted away from the rest of the page.
        """
        # Compact group: keep Auto and the swatch immediately together and
        # push any spare horizontal space to the trailing side of the row.
        group = QWidget()
        # Maximum: the Auto+swatch group may never take leftover width -- it
        # stays packed against the caption and the trailing stretch owns the
        # rest of the row.
        group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        group_lay = QHBoxLayout(group)
        group_lay.setContentsMargins(0, 0, 0, 0)
        group_lay.setSpacing(4)
        auto = QCheckBox('Auto')
        auto.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        auto.setToolTip('Choose a readable color automatically for the current chart theme')
        current = str(self._typo_opts.get(key, 'auto'))
        is_auto = current.lower() == 'auto'
        swatch = color_swatch('#111827' if is_auto else current,
                              lambda color: self._set_typo(**{key: color}))
        swatch.setEnabled(not is_auto)
        auto.setChecked(is_auto)
        auto.toggled.connect(lambda checked: (swatch.setEnabled(not checked),
            self._set_typo(**{key: 'auto' if checked else str(swatch.text() or '#111827')})))
        group_lay.addWidget(auto)
        group_lay.addWidget(swatch)
        return grid_row(label, group, label_w), auto, swatch
    def _set_typo(self, emit=True, discrete=False, **kw):
        """Update typography options and (optionally) notify listeners.

        ``discrete`` marks a one-shot choice from a combo box rather than a
        value being dragged: the page uses it to pick a much shorter live
        preview debounce, since a single deliberate pick should not wait out
        the slider delay.
        """
        no_emit = kw.pop('_no_emit', False) if isinstance(kw, dict) else False
        try:
            self._typo_opts.update(kw)
        except Exception:
            pass
        # Same dirty-gate contract as _set_layout.
        self._dirty = True
        try:
            self._refresh_previews()
        except Exception:
            pass
        if emit and not no_emit and not getattr(self, "_suppress", False):
            try:
                self.values_changed.emit({'typography': dict(self._typo_opts),
                                          'discrete': bool(discrete)})
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

    def collect_layout(self):
        opts = dict(self._layout_opts)
        for attr, key in (("_le_title", "title"), ("_le_x", "x_label"), ("_le_y", "y_label"),
                          ("_le_y2", "y2_label"), ("_le_leg", "legend_title"),
                          ("_le_cb", "colorbar_title"), ("_le_cb_title", "colorbar_title")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    opts[key] = str(w.text() or "").strip()
                except Exception:
                    pass
        for attr, key in (("_ck_vals", "show_value_labels"), ("_ck_grid", "show_grid"),
                          ("_ck_border", "show_border"), ("_ck_markers", "show_markers"),
                          ("_ck_ann", "heatmap_show_text"), ("_ck_annot", "heatmap_show_text"),
                          ("_cb_center_total", "donut_center_total")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    opts[key] = bool(w.isChecked())
                except Exception:
                    pass
        for attr, key in (("_cb_lpos", "label_position"), ("_cb_bmode", "bar_mode"),
                          ("_cb_legpos", "legend_position"), ("_cb_legorient", "legend_orientation"),
                          ("_cb_legorder", "legend_order"), ("_cb_textinfo", "pie_textinfo"),
                          ("_cb_pdir", "pie_direction"), ("_cb_lshape", "line_shape"),
                          ("_cb_lfill", "line_fill"), ("_cb_cbpos", "colorbar_position"),
                          ("_cb_cb_pos", "colorbar_position"), ("_cb_cborient", "colorbar_orientation"),
                          ("_cb_cb_orient", "colorbar_orientation"), ("_cb_cs", "heatmap_colorscale"),
                          ("_cb_rmode", "render_mode"), ("_cb_ialign", "table_index_align"),
                          ("_cb_dalign", "table_data_align"), ("_cb_numfmt", "table_number_format"),
                          ("_cb_basemap", "map_basemap"), ("_cb_proj", "map_projection")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    opts[key] = str(w.currentText() or "").strip()
                except Exception:
                    pass
        w = getattr(self, "_cb_legclick", None)
        if w is not None:
            try:
                txt = str(w.currentText() or "").lower()
                opts["legend_itemclick"] = "toggleothers" if "others" in txt else ("none" if "nothing" in txt or "false" in txt else "toggle")
            except Exception:
                pass
        for attr, key in (("_gap_spin", "bar_gap"), ("_lw_spin", "line_width"),
                          ("_bins_spin", "histogram_bins"), ("_op_spin", "histogram_opacity"),
                          ("_dh_spin", "donut_hole"), ("_spin_donut", "donut_hole"),
                          ("_pl_spin", "pull_slices"), ("_spin_pull", "pull_slices"),
                          ("_pr_spin", "pie_rotation"), ("_spin_rot", "pie_rotation"),
                          ("_pvs_spin", "pie_value_size"), ("_spin_pval", "pie_value_size"),
                          ("_ms_spin", "marker_size"), ("_spin_msize", "marker_size"),
                          ("_spin_ssize", "marker_size"), ("_mo_spin", "marker_opacity"),
                          ("_spin_sopacity", "marker_opacity"),
                          ("_cbtsz_spin", "colorbar_title_size"), ("_spin_cbtitle_sz", "colorbar_title_size"),
                          ("_cbts_spin", "colorbar_tick_size"), ("_spin_cbtick_sz", "colorbar_tick_size"),
                          ("_prec_spin", "heatmap_annotation_precision"), ("_spin_annot_prec", "heatmap_annotation_precision"),
                          ("_ann_spin", "heatmap_annotation_size"), ("_spin_annot_sz", "heatmap_annotation_size"),
                          ("_thh_spin", "table_header_height"), ("_rqh_spin", "table_header_height")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    opts[key] = w.value()
                except Exception:
                    pass
        for attr, key in (("_sw_pvc", "pie_value_color"), ("_sw_pcol", "pie_value_color"),
                          ("_sw_cbtclr", "colorbar_title_color"), ("_sw_cbtitle_col", "colorbar_title_color"),
                          ("_sw_cbtc", "colorbar_tick_color"), ("_sw_cbtick_col", "colorbar_tick_color"),
                          ("_sw_ann", "heatmap_annotation_color"), ("_sw_annot_col", "heatmap_annotation_color")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    val = str(getattr(w, "_color_value", w.text()) or "").strip()
                    opts[key] = "auto" if val.lower() in ("", "auto", "none") else val
                except Exception:
                    pass
        self._layout_opts.update(opts)
        return dict(self._layout_opts)

    def collect_typography(self):
        opts = dict(self._typo_opts)
        for attr, key in (("_cb_fam", "family"), ("_cb_fstyle", "font_style"),
                          ("_cb_hfont", "header_family"), ("_cb_hfstyle", "header_font_style")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    opts[key] = str(w.currentText() or "").strip()
                except Exception:
                    pass
        w = getattr(self, "_cb_halign", None)
        if w is not None:
            try:
                opts["header_align"] = self._halign_key(w.currentText())
            except Exception:
                pass
        for attr, key in (("_hs_spin", "header_size"), ("_ts_spin", "axis_title_size"),
                          ("_axs_spin", "axis_tick_size"), ("_ls_spin", "legend_item_size"),
                          ("_vs_spin", "value_label_size"), ("_tips_spin", "tooltip_size")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    opts[key] = int(w.value())
                except Exception:
                    pass
        for attr, key in (("_sw_hcol", "header_color"), ("_sw_tcol", "axis_title_color"),
                          ("_sw_acol", "axis_tick_color"), ("_sw_lcol", "legend_item_color")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    val = str(getattr(w, "_color_value", w.text()) or "").strip()
                    opts[key] = "auto" if val.lower() in ("", "auto", "none") else val
                except Exception:
                    pass
        for ck_attr, sw_attr, key in (("_ck_vauto", "_sw_vcol", "value_label_color"),
                                      ("_ck_tipauto", "_sw_tipcol", "tooltip_color")):
            ck = getattr(self, ck_attr, None)
            sw = getattr(self, sw_attr, None)
            if ck is not None and sw is not None:
                try:
                    if ck.isChecked():
                        opts[key] = "auto"
                    else:
                        val = str(getattr(sw, "_color_value", sw.text()) or "").strip()
                        opts[key] = "auto" if val.lower() in ("", "auto", "none") else val
                except Exception:
                    pass
        self._typo_opts.update(opts)
        return dict(self._typo_opts)
