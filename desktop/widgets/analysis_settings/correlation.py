"""Correlation (heatmap) settings -- follows the Statistical template exactly.

Design note
-----------
Same approach as ``distribution.py``: the Generate / Layout / Typography tabs
are built from the shared helpers with the shared constants, and the
type-agnostic Typography tab is INHERITED rather than copied. Only the two
tabs that genuinely differ are overridden. See
``extra/CHART_IMPLEMENTATION_RULES.md``.

Why it subclasses ``StatisticalSettings`` and not ``DistributionSettings``:
the correlation form is a single numeric multi-select, with NO dimension
control. ``DistributionSettings`` would drag in a "Colour by" combo this chart
has no use for, so the nearest builder that actually has the shape we want is
the base one.

What this chart deliberately does NOT offer
------------------------------------------
The old ``chart_settings.py`` declares correlation as ``has_axes=False`` and
``has_legend=False``, and its ``controls`` list is only the title, the colour
scale, the colour-bar styling and the four annotation options. So this tab has
no X/Y axis labels, no legend title or position, no "Show value labels" and no
"Show grid", and Generate has no Palette. Those were briefly present because
inheriting the template restores everything at once, and they are masked here
deliberately -- a control that cannot change the chart is worse than a missing
one, because it reads as "broken" rather than "not applicable".

Two rows are NOT in the old app and exist only because they were requested:
"Show border" (the colour-bar frame) and "Colorbar pos".

Feature reference (old codebase, ``chart_settings.py`` _CONTROL_META, which is
where the names/types/ranges/defaults below come from -- the old app's
Streamlit grouping is deliberately NOT reproduced):

    "correlation": CAPS controls = [title, heatmap_colorscale, colorbar_title,
        colorbar_tick_size, colorbar_tick_color, colorbar_title_size,
        colorbar_title_color, heatmap_show_text,
        heatmap_annotation_precision, heatmap_annotation_size,
        heatmap_annotation_color];  typography = [family, font_style, header,
        axis_tick].  "correlation" appears in CONTROLS_ADVANCED for
        [heatmap_annotation_precision, heatmap_annotation_color,
        colorbar_tick_size, colorbar_tick_color, colorbar_title_size,
        colorbar_title_color].

The old "Advanced" popover is not a Qt concept: those six controls become
ordinary rows in the Layout tab's right-hand option column, in the same order
the old popover listed them.

The colour-scale list is the old app's own COLORSCALES_ALL (diverging then
sequential), copied here as data. Plotly ships these names natively, so no new
dependency is introduced.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QCheckBox, QLabel
from .base import (hint_label, cap_widget, multi_select_combo, slider_with_readout,
                   color_swatch, colorbar_position_row, GEN_W, HINT_GAP, SECTION_GAP, page_root,
                   section_box, grid_row, FlowGrid)
from .statistical import StatisticalSettings, NONE_SENTINEL

# GEN_W (the compact Generate field width) now comes from ``.base`` so the
# flow grid's minimum column width cannot drift from it.

# Colour scales available in the old app (chart_settings.py:20-23). Diverging
# scales first because a correlation matrix is signed data centred on zero, and
# RdBu (the old default) belongs to that group.
COLORSCALES_DIVERGING = ["RdBu", "RdYlBu", "PRGn", "PiYG", "BrBG", "Spectral"]
COLORSCALES_SEQUENTIAL = ["Blues", "Viridis", "Plasma", "Magma", "Cividis",
                          "Turbo", "Inferno", "Greys", "YlGnBu", "YlOrRd",
                          "PuBu", "BuPu"]
COLORSCALES_ALL = COLORSCALES_DIVERGING + COLORSCALES_SEQUENTIAL

# Ranges/steps/defaults, all from the old control metadata.
ANNOTATION_SIZE_RANGE = (6, 24)
COLORBAR_TICK_SIZE_RANGE = (6, 18)
COLORBAR_TITLE_SIZE_RANGE = (8, 24)
PRECISION_RANGE = (0, 10)


class CorrelationSettings(StatisticalSettings):
    """Generate + Layout + Typography settings for the correlation type."""

    def _build(self):
        super()._build()
        # One field only: Columns (the numeric multi-select). WIDGET_SPEC
        # also declares "palette" for this type, but it is inert -- the
        # display layer always repaints the trace from heatmap_colorscale --
        # so it is deliberately neither built nor returned. See
        # build_generate for the full reasoning.
        self._pending = {'x': []}
        for attr in ('_combo_columns', '_cb_cs',
                     '_ck_ann', '_le_title', '_le_cb', '_cb_cbpos',
                     '_ck_border',
                     '_ann_slider', '_ann_spin', '_prec_slider',
                     '_prec_spin', '_cbts_slider', '_cbts_spin',
                     '_cbtsz_slider', '_cbtsz_spin', '_sw_ann', '_sw_cbtc',
                     '_sw_cbtclr'):
            if not hasattr(self, attr):
                setattr(self, attr, None)

    def prefill(self, values):
        v = values or {}
        # Snap to options that actually exist BEFORE pushing them into the
        # widgets: a chart saved against another dataset can name a column or
        # palette that is gone, and the combo would then show item 0 while
        # collect_values() still returned the stale name.
        x_want = [c for c in (list(v.get('x', []) or []))
                  if c in (self._num_cols or [])]
        try:
            self._pending['x'] = x_want
        except Exception:
            pass
        try:
            if getattr(self, '_combo_columns', None) is not None:
                self._combo_columns.set_selection(list(self._pending['x']))
        except Exception:
            pass

    def _on_columns(self, sel):
        self._pending['x'] = list(sel or [])
        self._mark_dirty(emit=True)

    def collect_values(self):
        # No 'palette' key: the control is masked, so returning one would put
        # a value back into the saved config that the UI can no longer show or
        # change.
        try:
            return {'x': list(self._pending.get('x') or [])}
        except Exception:
            return {'x': []}

    def build_generate(self):
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label('Adjust options below. All selections are live \u2014 no submit needed until Generate.'))
        # Sections flow across the pane (FlowGrid): as many per line as fit,
        # each line stretched to the full width, the remainder wrapped to the
        # next line. See the statistical template for the reasoning.
        flow = FlowGrid()
        # Columns is the only Generate control this chart has (see the module
        # docstring on why the palette and the dimension slot are both absent).
        # -- Columns (numeric multi-select) ---------------------------------
        nbody = QWidget()
        nl = QVBoxLayout(nbody)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(HINT_GAP)
        self._combo_columns = multi_select_combo(list(self._num_cols or []))
        cap_widget(self._combo_columns, GEN_W)
        try:
            self._combo_columns.set_selection(list(self._pending.get('x') or []))
        except Exception:
            pass
        try:
            self._combo_columns.selection_changed.connect(self._on_columns)
        except Exception:
            pass
        nl.addWidget(self._combo_columns)
        nl.addStretch()
        flow.add_section(section_box('Columns', nbody, 'Two or more numeric columns'))
        # -- Slot 3: Palette is deliberately NOT built --------------------
        # The old _WIDGET_SPEC listed a palette for correlation, but it was a
        # DEAD control there too: the old display layer unconditionally wrote
        # tr.colorscale from the "heatmap_colorscale" Layout option (default
        # RdBu) for every heatmap/choropleth trace, so whatever palette the
        # runner passed as color_continuous_scale was always overwritten. The
        # Layout tab's Colorscale row is the one control that actually colours
        # a correlation matrix, and it is the only one shown.
        rl.addWidget(flow)
        rl.addStretch()
        return root

    def build_layout(self):
        """Statistical's shared Layout rows PLUS the heatmap / colour-bar rows.

        This deliberately does NOT start from ``StatisticalSettings.build_layout``.
        That method builds and returns a finished page widget, so there is no
        way to append to it; calling it and then bolting heatmap rows on top
        would have produced a second, stacked page. The inherited rows are
        therefore recreated here and the heatmap rows are interleaved, which
        keeps the two option columns and the shared helpers identical to the
        template instead of growing a private layout.

        Rows the earlier version silently dropped, and that this restores:
        Chart title, Show value labels, Show grid, Show border, Legend title,
        Legend position, and the X/Y axis labels. "Show border" is the toggle
        the user asked for -- it is what frames the colour bar.
        """
        root, rl = page_root()
        cols = QWidget()
        cl = QHBoxLayout(cols)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(SECTION_GAP)
        left, ll_ = self._option_column()
        right, rr = self._option_column()
        rl.addWidget(hint_label('\u2728 Live Preview \u2014 changes appear instantly on the chart \u2192'))
        # -- Left column: titles + legend, then the heatmap essentials -------
        self._le_title = QLineEdit(str(self._layout_opts.get('title', '') or ''))
        self._le_title.setPlaceholderText('Correlation Heatmap')
        self._le_title.textChanged.connect(lambda t: self._set_layout(title=t))
        ll_.addWidget(grid_row('Chart title', self._le_title))
        # No X-axis / Y-axis / Legend title / Legend position rows. The old
        # chart_settings metadata declares "correlation" as
        # has_axes=False, has_legend=False and lists neither axes_labels nor
        # legend_labels among its controls, so those rows would be controls
        # that cannot change anything on this chart.
        self._ck_ann = QCheckBox('Annotations')
        self._ck_ann.setToolTip('Show the correlation value inside each cell')
        self._ck_ann.setChecked(bool(self._layout_opts.get('heatmap_show_text', True)))
        self._ck_ann.toggled.connect(
            lambda v: self._set_layout(heatmap_show_text=bool(v)))
        ll_.addWidget(self._check_row(self._ck_ann))
        self._cb_cs = QComboBox()
        cap_widget(self._cb_cs, 200)
        self._cb_cs.addItems(COLORSCALES_ALL)
        self._cb_cs.setCurrentText(str(self._layout_opts.get('heatmap_colorscale', 'RdBu')))
        self._cb_cs.currentTextChanged.connect(
            lambda t: self._set_layout(discrete=True, heatmap_colorscale=t))
        ll_.addWidget(grid_row('Colorscale', self._cb_cs))
        ann = slider_with_readout(ANNOTATION_SIZE_RANGE[0], ANNOTATION_SIZE_RANGE[1],
                                  int(self._layout_opts.get('heatmap_annotation_size', 10)),
                                  is_int=True, step=1)
        self._ann_slider = ann._as_slider
        self._ann_spin = ann._as_spin
        try:
            self._ann_spin.valueChanged.connect(
                lambda v: self._set_layout(heatmap_annotation_size=int(v)))
        except Exception:
            pass
        ll_.addWidget(grid_row('Ann size', ann))
        self._sw_ann = color_swatch(
            str(self._layout_opts.get('heatmap_annotation_color', '#ffffff')),
            lambda c: self._set_layout(heatmap_annotation_color=c))
        ll_.addWidget(grid_row('Ann color', self._sw_ann))
        prec = slider_with_readout(PRECISION_RANGE[0], PRECISION_RANGE[1],
                                   int(self._layout_opts.get('heatmap_annotation_precision', 2)),
                                   is_int=True, step=1)
        self._prec_slider = prec._as_slider
        self._prec_spin = prec._as_spin
        try:
            self._prec_spin.valueChanged.connect(
                lambda v: self._set_layout(heatmap_annotation_precision=int(v)))
        except Exception:
            pass
        ll_.addWidget(grid_row('Precision', prec))
        # The colour bar / colour range. Right/Left give a vertical strip beside
        # the matrix; Top/Bottom give a horizontal bar above or below it, which
        # is the only readable choice once the column names are long -- and
        # reserving it in the margin instead of as a side strip is what removes
        # the gap between the matrix and the bar.
        _cb_row, self._cb_cbpos, self._cb_cborient = colorbar_position_row(
            self._layout_opts, self._set_layout)
        ll_.addWidget(_cb_row)
        ll_.addStretch()
        # -- Right column: colour-bar styling + the shared display rows -----
        self._le_cb = QLineEdit(str(self._layout_opts.get('colorbar_title', '') or ''))
        self._le_cb.setPlaceholderText('Correlation')
        self._le_cb.textChanged.connect(lambda t: self._set_layout(colorbar_title=t))
        rr.addWidget(grid_row('CB title', self._le_cb))
        cbts = slider_with_readout(COLORBAR_TICK_SIZE_RANGE[0],
                                   COLORBAR_TICK_SIZE_RANGE[1],
                                   int(self._layout_opts.get('colorbar_tick_size', 10)),
                                   is_int=True, step=1)
        self._cbts_slider = cbts._as_slider
        self._cbts_spin = cbts._as_spin
        try:
            self._cbts_spin.valueChanged.connect(
                lambda v: self._set_layout(colorbar_tick_size=int(v)))
        except Exception:
            pass
        rr.addWidget(grid_row('CB tick sz', cbts))
        self._sw_cbtc = color_swatch(
            str(self._layout_opts.get('colorbar_tick_color', '#94a3b8')),
            lambda c: self._set_layout(colorbar_tick_color=c))
        rr.addWidget(grid_row('CB tick clr', self._sw_cbtc))
        cbtsz = slider_with_readout(COLORBAR_TITLE_SIZE_RANGE[0],
                                    COLORBAR_TITLE_SIZE_RANGE[1],
                                    int(self._layout_opts.get('colorbar_title_size', 11)),
                                    is_int=True, step=1)
        self._cbtsz_slider = cbtsz._as_slider
        self._cbtsz_spin = cbtsz._as_spin
        try:
            self._cbtsz_spin.valueChanged.connect(
                lambda v: self._set_layout(colorbar_title_size=int(v)))
        except Exception:
            pass
        rr.addWidget(grid_row('CB title sz', cbtsz))
        self._sw_cbtclr = color_swatch(
            str(self._layout_opts.get('colorbar_title_color', '#cbd5e1')),
            lambda c: self._set_layout(colorbar_title_color=c))
        rr.addWidget(grid_row('CB title clr', self._sw_cbtclr))
        # Shared display rows, in the template's own order. "Show border" is
        # the colour-bar frame toggle: _apply_colorbar_border() in
        # chart_display applies it to the heatmap's own colour bar.
        # "Show value labels" and "Show grid" are masked: the old metadata
        # lists neither for correlation (its controls are the title, the
        # colorscale, the colour-bar styling and the four annotation options),
        # and the in-cell numbers are already governed by Annotations.
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
        try:
            from chart_display import default_layout_opts
            base = default_layout_opts()
        except Exception:
            base = {}
        base.update(values or {})
        self._layout_opts = base
        # Every plain-text row the Layout tab now shows: the two heatmap
        # titles plus the inherited chart/axis/legend titles. All of them share
        # one loop, because each needs the same blockSignals guard -- setting
        # the text fires textChanged, which would mark the form dirty while
        # merely restoring a saved chart.
        for attr, key in (("_le_title", "title"), ("_le_cb", "colorbar_title")):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setText(str(self._layout_opts.get(key, "") or ""))
                    w.blockSignals(False)
            except Exception:
                pass
        # Drop-downs: colorscale and colour-bar placement. A saved chart can
        # name a scale (or a position) that no longer exists, so the value is
        # snapped back to a real entry rather than left showing something that
        # is not there -- which is why the position list is read off the combo
        # itself instead of being hard-coded.
        for attr, key, dflt in (("_cb_cs", "heatmap_colorscale", "RdBu"),
                                ("_cb_cbpos", "colorbar_position", "Right"),
                                ("_cb_cborient", "colorbar_orientation", "Auto")):
            try:
                w = getattr(self, attr, None)
                if w is None:
                    continue
                w.blockSignals(True)
                want = str(self._layout_opts.get(key, dflt) or dflt)
                if want not in [w.itemText(i) for i in range(w.count())]:
                    want = dflt
                    self._layout_opts[key] = want
                w.setCurrentText(want)
                w.blockSignals(False)
            except Exception:
                pass
        # Checkboxes: the heatmap's own Annotations toggle, plus Show border
        # (the colour-bar frame).
        for attr, key, dflt in (("_ck_ann", "heatmap_show_text", True),
                                ("_ck_border", "show_border", True)):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setChecked(bool(self._layout_opts.get(key, dflt)))
                    w.blockSignals(False)
            except Exception:
                pass
        for attr, key, dflt in (("_ann_spin", "heatmap_annotation_size", 10),
                                ("_cbts_spin", "colorbar_tick_size", 10),
                                ("_cbtsz_spin", "colorbar_title_size", 11),
                                ("_prec_spin", "heatmap_annotation_precision", 2)):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.blockSignals(True)
                    w.setValue(int(self._layout_opts.get(key, dflt)))
                    w.blockSignals(False)
            except Exception:
                pass
        for attr, key, dflt in (("_sw_cbtc", "colorbar_tick_color", "#94a3b8"),
                                ("_sw_cbtclr", "colorbar_title_color", "#cbd5e1"),
                                ("_sw_ann", "heatmap_annotation_color", "#ffffff")):
            try:
                w = getattr(self, attr, None)
                if w is not None and hasattr(w, "_retint"):
                    w._retint(str(self._layout_opts.get(key, dflt)))
            except Exception:
                pass
        try:
            self._refresh_previews()
        except Exception:
            pass

    def build_typography(self):
        """The shared Typography tab, with the rows correlation does not have.

        The old chart_settings metadata lists correlation's typography as
        ["family", "font_style", "header", "axis_tick"] -- no "axis_title"
        and no "legend_title"/"legend_item", matching its has_axes=False,
        has_legend=False flags. Inheriting the template verbatim therefore
        offered an axis-title size/colour and a legend size/colour that this
        chart cannot render, so those four rows are removed after the shared
        page is built.

        Building the page first and deleting rows (rather than rewriting it)
        keeps the remaining typography on the one shared template, so a fix to
        the font handling applies here too.
        """
        root = super().build_typography()
        self._mask_rows(root, ("Title size", "Title color",
                               "Legend size", "Legend color"))
        return root
