"""Outlier (IQR) settings -- numeric columns scanned with an IQR fence.

Feature reference (old codebase, ``chart_settings.py``; the old Streamlit
grouping and "Advanced" popover are NOT reproduced):

    The anomaly analysis has no dimension control and no palette: the runner
    scans one numeric column per chart and draws the points, the detected
    outliers, and the two IQR fence lines. Everything else the anomaly type
    offers is therefore exactly the scatter-like controls that the renderer
    actually reads.

What this chart deliberately does NOT offer
-------------------------------------------
The shared statistical template drags in bar-family rows that cannot change
anything on a point/fence chart, so the Layout tab removes them:

    "Bar mode" / "Bar gap"     -> there are no bars; _categorical_display
                                   would accept the values and the adapter
                                   would never read them.
    "Show value labels" / "Label pos" -> the outlier runner sets
                                   labels.show=False and the fence lines
                                   carry the meaningful numbers in their
                                   series names, not on the marks.

The rows it keeps are the ones the model really renders: title, axis labels,
the legend (Normal / Outlier / fences), grid, border -- plus "M. size" and
"M. opacity", which flow through the scatter live-edit branch into the
adapter's symbol settings.

``render_mode`` is stored on BOTH the layout dict and the Generate values for
the same reason as the scatter builder: the service reads it from the widget
values at build time, so a Layout-only value would leave the control dead.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox
from .base import (hint_label, cap_widget, multi_select_combo, slider_with_readout,
                   page_root, section_box, grid_row, FlowGrid, GEN_W, HINT_GAP)
from .statistical import StatisticalSettings, NONE_SENTINEL

# Old control metadata for the IQR fence multiplier.
MULTIPLIER_RANGE = (0.5, 5.0)
MULTIPLIER_STEP = 0.25
MULTIPLIER_DEFAULT = 1.5

RENDER_MODES = ["svg", "canvas"]


class OutlierSettings(StatisticalSettings):
    """Generate + Layout + Typography settings for the outlier type."""

    def _build(self):
        super()._build()
        # The inherited column roles are the wrong shape for this chart: it
        # scans numeric columns. The service's outlier branch reads
        # ``columns`` (a list) and ``multiplier`` (a float) by name.
        self._pending = {'columns': [], 'multiplier': MULTIPLIER_DEFAULT,
                         'render_mode': RENDER_MODES[0]}
        for attr in ('_combo_columns', '_mult_slider', '_mult_spin',
                     '_ms_spin', '_mo_spin', '_cb_rmode'):
            if not hasattr(self, attr):
                setattr(self, attr, None)

    # -- Generate ----------------------------------------------------------
    def build_generate(self):
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label(
            'Adjust options below. All selections are live — no submit needed until Generate.'))
        flow = FlowGrid()
        # -- Columns (numeric multi-select) ---------------------------------
        nbody = QWidget()
        nl = QVBoxLayout(nbody)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(HINT_GAP)
        self._combo_columns = multi_select_combo(list(self._num_cols or []))
        cap_widget(self._combo_columns, GEN_W)
        try:
            self._combo_columns.set_selection(list(self._pending.get('columns') or []))
        except Exception:
            pass
        try:
            self._combo_columns.selection_changed.connect(self._on_columns)
        except Exception:
            pass
        nl.addWidget(self._combo_columns)
        nl.addStretch()
        flow.add_section(section_box('Columns', nbody,
                                     'Numeric columns to scan for outliers'))
        # -- IQR multiplier --------------------------------------------------
        mbody = QWidget()
        ml = QVBoxLayout(mbody)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(HINT_GAP)
        mult = slider_with_readout(MULTIPLIER_RANGE[0], MULTIPLIER_RANGE[1],
                                   float(self._pending.get('multiplier',
                                                           MULTIPLIER_DEFAULT)),
                                   is_int=False, step=MULTIPLIER_STEP)
        self._mult_slider = mult._as_slider
        self._mult_spin = mult._as_spin
        try:
            self._mult_spin.valueChanged.connect(self._on_multiplier)
        except Exception:
            pass
        ml.addWidget(mult)
        ml.addStretch()
        flow.add_section(section_box('IQR multiplier', mbody,
                                     'Fences at Q1 − k×IQR and Q3 + k×IQR'))
        rl.addWidget(flow)
        rl.addStretch()
        return root

    def _on_columns(self, sel):
        self._pending['columns'] = list(sel or [])
        self._mark_dirty(emit=True)

    def _on_multiplier(self, v):
        try:
            self._pending['multiplier'] = max(MULTIPLIER_RANGE[0],
                                              min(MULTIPLIER_RANGE[1],
                                                  float(v)))
        except Exception:
            self._pending['multiplier'] = MULTIPLIER_DEFAULT
        self._mark_dirty(emit=True)

    def _on_render_mode(self, text):
        text = str(text).lower()
        text = "canvas" if text == "webgl" else text
        self._layout_opts['render_mode'] = text
        self._pending['render_mode'] = text
        self._mark_dirty(emit=True)

    def collect_values(self):
        """The Generate values, keyed as the service's outlier branch reads them."""
        p = self._pending
        try:
            mult = float(p.get('multiplier', MULTIPLIER_DEFAULT) or MULTIPLIER_DEFAULT)
        except Exception:
            mult = MULTIPLIER_DEFAULT
        mult = max(MULTIPLIER_RANGE[0], min(MULTIPLIER_RANGE[1], mult))
        return {'columns': list(p.get('columns') or []),
                'multiplier': mult,
                'render_mode': str(self._layout_opts.get('render_mode', 'svg'))}

    def prefill(self, values):
        """Restore a saved config; snap every name to an option that exists.

        ``column`` (the old scalar key) is honoured for configs saved before
        the multi-select existed.
        """
        v = values or {}
        p = self._pending
        cols = v.get('columns')
        if cols is None:
            cols = v.get('column')  # legacy scalar key
        if isinstance(cols, str):
            cols = [cols]
        want = [c for c in (cols or []) if c in (self._num_cols or [])]
        try:
            p['columns'] = want
        except Exception:
            pass
        try:
            mult = float(v.get('multiplier', MULTIPLIER_DEFAULT) or MULTIPLIER_DEFAULT)
            p['multiplier'] = max(MULTIPLIER_RANGE[0],
                                  min(MULTIPLIER_RANGE[1], mult))
        except Exception:
            p['multiplier'] = MULTIPLIER_DEFAULT
        try:
            if getattr(self, '_combo_columns', None) is not None:
                self._combo_columns.set_selection(list(want))
        except Exception:
            pass
        try:
            if getattr(self, '_mult_spin', None) is not None:
                self._mult_spin.blockSignals(True)
                self._mult_spin.setValue(float(p['multiplier']))
                self._mult_spin.blockSignals(False)
        except Exception:
            pass

    # -- Layout --------------------------------------------------------------
    def _add_outlier_rows(self, root):
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
                                 float(self._layout_opts.get("marker_opacity", 0.6)),
                                 is_int=False, step=0.05)
        self._mo_spin = mo._as_spin
        try:
            self._mo_spin.valueChanged.connect(
                lambda v: self._set_layout(marker_opacity=float(v)))
        except Exception:
            pass
        self._add_option_row(root, grid_row('M. opacity', mo))

        self._cb_rmode = QComboBox()
        cap_widget(self._cb_rmode, 200)
        self._cb_rmode.addItems(RENDER_MODES)
        saved_mode = str(self._layout_opts.get("render_mode", "svg")).lower()
        self._cb_rmode.setCurrentText("canvas" if saved_mode == "webgl" else saved_mode)
        self._cb_rmode.currentTextChanged.connect(self._on_render_mode)
        self._add_option_row(root, grid_row('Render mode', self._cb_rmode))

    def build_layout(self):
        """The shared Layout tab minus the bar/value-label rows, plus markers."""
        root = super().build_layout()
        self._mask_rows(root, ("Bar mode", "Bar gap",
                               "Show value labels", "Label pos"))
        self._add_outlier_rows(root)
        return root
