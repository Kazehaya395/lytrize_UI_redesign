"""Scatter Plot settings -- the Time Series page plus the marker options.

Feature reference (old codebase, ``chart_settings.py`` ``CHART_TYPE_SETTINGS``
and ``_CONTROL_META``; the old Streamlit grouping and "Advanced" popover are NOT
reproduced):

    "scatter_plot": has_axes=True, has_legend=True,
    controls = [title, axes_labels, legend_labels, marker_opacity, marker_size,
        show_value_labels, line_width, show_markers, line_fill, render_mode];
    typography = [family, font_style, header, axis_title, axis_tick,
        legend_title, legend_item].

``CONTROLS_ADVANCED`` put [line_width, show_markers, line_fill, render_mode]
behind the old popover; marker opacity and size were in the main view.

Why it subclasses ``TimeSeriesSettings``
----------------------------------------
A scatter is a line-shaped trace too, so the line rows (width / markers / shape /
fill) are inherited verbatim rather than rebuilt. This builder only replaces the
Generate tab -- a scatter needs X / Y / Colour / Size / Trendline, which is a
different question entirely -- and adds the two marker controls and the
render-mode dropdown to the Layout tab.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox
from .base import (hint_label, cap_widget, slider_with_readout, page_root,
                   section_box, grid_row, FlowGrid, GEN_W, HINT_GAP)
from .statistical import NONE_SENTINEL
from .time_series import TimeSeriesSettings

# Old _CONTROL_META: svg for a normal-sized chart, webgl for a very large one.
# The scatter runner picks this at generation time, so the Layout row records
# that choice rather than re-rendering the existing figure -- Plotly cannot
# switch renderer on a live figure without rebuilding it.
RENDER_MODES = ["svg", "webgl"]

# Trendline kinds, from the old scatter panel. The regression methods map onto
# Plotly's px.scatter_trendline names; "None" disables the overlay.
TRENDLINES = ["None", "Linear", "Logarithmic", "Polynomial", "Moving Average"]


class ScatterPlotSettings(TimeSeriesSettings):
    """Generate + Layout + Typography settings for the Scatter Plot type."""

    def _build(self):
        super()._build()
        # The inherited time-series shape is entirely wrong here: a scatter has
        # two independent numeric axes plus optional colour / size encodings and
        # a trendline, and the service's scatter branch reads each by its own
        # name. Rebuilt from scratch rather than reinterpreted.
        self._pending = {'x_col': None, 'y_col': None, 'color_col': None,
                         'size_col': None, 'trendline': TRENDLINES[0],
                         'palette': None}
        for attr in ('_combo_x', '_combo_y', '_combo_color', '_combo_size',
                     '_combo_trend', '_combo_palette', '_mo_spin', '_ms_spin',
                     '_cb_rmode'):
            if not hasattr(self, attr):
                setattr(self, attr, None)

    # -- Generate ----------------------------------------------------------
    def build_generate(self):
        """X | Y | Colour by | Size by | Trendline | Palette.

        X and Y are required -- a scatter is defined by the pair -- so their
        combos have no blank entry. Colour-by and Size-by are optional
        encodings and do, and both draw from ANY column (a size encoding is
        usually numeric, but a colour one is often categorical).
        """
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label(
            'Adjust options below. All selections are live — no submit needed until Generate.'))
        flow = FlowGrid()

        nums = list(self._num_cols or [])
        box, self._combo_x = self._combo_section(
            'X axis', 'Numeric column for the horizontal axis',
            nums if nums else [''], nums[0] if nums else '', self._on_x)
        flow.add_section(box)
        second = nums[1] if len(nums) > 1 else (nums[0] if nums else '')
        box, self._combo_y = self._combo_section(
            'Y axis', 'Numeric column for the vertical axis',
            nums if nums else [''], second, self._on_y)
        flow.add_section(box)

        allc = [str(c) for c in (self._all_cols or [])]
        box, self._combo_color = self._combo_section(
            'Colour by', 'None = one colour for every point',
            [NONE_SENTINEL] + allc, NONE_SENTINEL, self._on_color)
        flow.add_section(box)
        box, self._combo_size = self._combo_section(
            'Size by', 'None = one size for every point',
            [NONE_SENTINEL] + nums, NONE_SENTINEL, self._on_size)
        flow.add_section(box)

        box, self._combo_trend = self._combo_section(
            'Trendline', 'Fitted curve drawn over the points',
            TRENDLINES, TRENDLINES[0], self._on_trend)
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
            self._on_color(self._combo_color.currentText())
            self._on_size(self._combo_size.currentText())
            self._on_trend(self._combo_trend.currentText())
            self._on_palette(self._combo_palette.currentText())
        except Exception:
            pass

    @staticmethod
    def _clean(text):
        """A combo's blank placeholder means "not encoded", not an empty column."""
        return None if str(text or '') in ('', NONE_SENTINEL) else str(text)

    def _on_x(self, text):
        self._pending['x_col'] = str(text) if text else None
        self._mark_dirty(emit=True)

    def _on_y(self, text):
        self._pending['y_col'] = str(text) if text else None
        self._mark_dirty(emit=True)

    def _on_color(self, text):
        self._pending['color_col'] = self._clean(text)
        self._mark_dirty(emit=True)

    def _on_size(self, text):
        self._pending['size_col'] = self._clean(text)
        self._mark_dirty(emit=True)

    def _on_trend(self, text):
        self._pending['trendline'] = str(text)
        self._mark_dirty(emit=True)

    def _on_palette(self, text):
        self._pending['palette'] = str(text)
        self._mark_dirty(emit=True)

    def _on_render_mode(self, text):
        """Store Render mode in BOTH places it is read from.

        The layout dict keeps the combo's state (and what the runner-independent
        display passes see); the pending dict is what collect_values() returns
        to the service, which is the only place ``render_mode`` is honoured.
        Setting only the layout value is what left the control dead.
        """
        self._layout_opts['render_mode'] = str(text)
        self._pending['render_mode'] = str(text)
        self._mark_dirty(emit=True)

    def collect_values(self):
        """The Generate values, keyed as the scatter service branch reads them.

        ``render_mode`` is included here even though its combo lives on the
        Layout tab, because it is a Plotly EXPRESS choice fixed when the figure
        is BUILT (``px.scatter(render_mode=...)``) and the service branch reads
        it from the widget values. Keeping it in the layout dict alone left the
        dropdown looking connected while every chart was still drawn as SVG.
        """
        p = self._pending
        return {'x_col': p.get('x_col'), 'y_col': p.get('y_col'),
                'color_col': p.get('color_col'), 'size_col': p.get('size_col'),
                'trendline': p.get('trendline', TRENDLINES[0]),
                'palette': p.get('palette'),
                'render_mode': str(self._layout_opts.get('render_mode', 'svg'))}

    def prefill(self, values):
        """Restore a saved config, snapping each name to an option that exists.

        A chart saved against another dataset can name a column that is gone, so
        a stale name is skipped rather than displayed while collect_values() was
        still returning it.
        """
        v = values or {}
        p = self._pending
        for key, dflt in (('x_col', ''), ('y_col', ''), ('color_col', ''),
                          ('size_col', ''), ('trendline', TRENDLINES[0]),
                          ('palette', '')):
            p[key] = v.get(key, p.get(key, dflt))
        for attr, key, dflt in (('_combo_x', 'x_col', ''), ('_combo_y', 'y_col', ''),
                                ('_combo_color', 'color_col', ''),
                                ('_combo_size', 'size_col', ''),
                                ('_combo_trend', 'trendline', TRENDLINES[0]),
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


    def _add_scatter_rows(self, root):
        """Append the marker and render options to the Layout option column."""
        ms = slider_with_readout(2, 30, int(self._layout_opts.get("marker_size", 6)))
        self._ms_spin = ms._as_spin
        try:
            self._ms_spin.valueChanged.connect(
                lambda v: self._set_layout(marker_size=int(v)))
        except Exception:
            pass
        self._add_option_row(root, grid_row('M. size', ms))

        mo = slider_with_readout(0.1, 1.0,
                                 float(self._layout_opts.get("marker_opacity", 0.8)),
                                 is_int=False, step=0.05)
        self._mo_spin = mo._as_spin
        try:
            self._mo_spin.valueChanged.connect(
                lambda v: self._set_layout(marker_opacity=float(v)))
        except Exception:
            pass
        self._add_option_row(root, grid_row('M. opacity', mo))

        # -- The one remaining old "Advanced" option -------------------------
        self._cb_rmode = QComboBox()
        cap_widget(self._cb_rmode, 200)
        self._cb_rmode.addItems(RENDER_MODES)
        self._cb_rmode.setCurrentText(str(self._layout_opts.get("render_mode", "svg")))
        self._cb_rmode.currentTextChanged.connect(self._on_render_mode)
        self._add_option_row(root, grid_row('Render mode', self._cb_rmode))

    def build_layout(self):
        """The inherited line Layout tab plus the scatter-specific rows."""
        root = super().build_layout()
        self._add_scatter_rows(root)
        return root
