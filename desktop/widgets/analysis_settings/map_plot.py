"""Map Plot settings -- the Correlation (colour-scale) page plus the map controls.

Feature reference (old codebase, ``chart_settings.py`` ``CHART_TYPE_SETTINGS``
and ``_CONTROL_META``)::

    "map_plot": has_axes=False, has_legend=False,
    controls = [title, show_colorbar, size_by_value, colorbar_title,
        colorbar_tick_size, colorbar_tick_color, colorbar_title_size,
        colorbar_title_color, heatmap_colorscale, marker_opacity, marker_size];
    typography = [family, font_style, header, axis_tick].

``CONTROLS_ADVANCED`` put [marker_opacity, marker_size, colorbar_tick_size,
colorbar_tick_color, colorbar_title_size, colorbar_title_color] behind the old
popover; the colour bar / size-by-value toggles and the colour scale were in the
main view.

Why it subclasses ``CorrelationSettings``
-----------------------------------------
A map's colour key IS a colour bar, so the six ``colorbar_*`` controls and the
colour scale are the same controls the correlation page already owns, and the
colour-bar BORDER toggle (the "Show border" the user asked for) comes with them.
Inheriting that page gives the right rows without re-listing nine near-identical
ones, and the annotations are inherited too -- ``_apply_heatmap_options`` guards
on the trace type, so they reach a ``scattermap``/``choropleth`` colour bar and
leave the markers alone.

What is masked
--------------
The rows a map has no use for. A map has no Cartesian axes, so there are no axis
labels; a geo trace has no in-cell text, so the heatmap annotation rows go; and
"Show value labels" would write a ``textposition`` that a geo trace does not
have.

The Generate tab is written out rather than inherited, because a map is
generated from a mode plus geo columns, which the correlation page (a numeric
column list) cannot express.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QCheckBox
from .base import (hint_label, cap_widget, color_swatch, slider_with_readout,
                   page_root, section_box, grid_row, FlowGrid, GEN_W, HINT_GAP)
from .correlation import CorrelationSettings
from .statistical import NONE_SENTINEL
from .categorical import AGGS

# Generate-row options. The two map modes are the exact strings the service
# branch compares against, and the styles are the basemaps the old panel
# offered (screenshot map1.png).
MAP_MODES = ["Scatter (Lat/Lon)", "Choropleth (Location Names)"]
MAP_STYLES = ["carto-positron", "carto-darkmatter", "open-street-map",
              "carto-voyager", "white-bg", "stamen-terrain"]


class MapPlotSettings(CorrelationSettings):
    """Generate + Layout + Typography settings for the Map Plot type."""

    def _build(self):
        super()._build()
        # The correlation page keeps only a Columns list; a map generates from a
        # mode plus geo columns, so the pending shape is replaced wholesale.
        self._pending = {
            'map_mode': MAP_MODES[0], 'lat_col': None, 'lon_col': None,
            'location_col': None, 'color_col': None, 'value_col': None,
            'agg_func': AGGS[0], 'map_style': MAP_STYLES[0],
            'marker_opacity': 0.82, 'invert_colorscale': False,
            'show_borders': True,
        }
        for attr in ('_combo_mode', '_combo_lat', '_combo_lon', '_combo_loc',
                     '_combo_color', '_combo_value', '_combo_agg',
                     '_combo_style', '_gen_opacity', '_ck_cbar', '_ck_sizeby',
                     '_ms_spin', '_mo_spin'):
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

    @staticmethod
    def _clean(text):
        """A combo's blank placeholder means "not set", not an empty column."""
        return None if str(text or '') in ('', NONE_SENTINEL) else str(text)

    # -- Generate ----------------------------------------------------------
    def build_generate(self):
        """The map's mode row, in the old panel's order (screenshot ``map1.png``).

        Mode | Latitude | Longitude | Location | Colour | Value | Aggregation |
        Style | Opacity | Invert | Borders

        One flat row rather than a form that swaps when the mode changes: the
        service branches on ``map_mode`` and reads only the subset that mode
        needs, so every field stays visible while the mode is being chosen
        instead of appearing and disappearing under the cursor.
        """
        self._build()
        root, rl = page_root()
        rl.addWidget(hint_label(
            'Adjust options below. All selections are live — no submit needed until Generate.'))
        flow = FlowGrid()

        box, self._combo_mode = self._combo_section(
            'Mode', 'Scatter plots points; Choropleth fills whole countries.',
            MAP_MODES, MAP_MODES[0], self._on_mode)
        flow.add_section(box)

        allc = [str(c) for c in (self._all_cols or [])]
        for title, attr, handler, hint in (
                ('Latitude', '_combo_lat', self._on_lat, 'Numeric latitude column'),
                ('Longitude', '_combo_lon', self._on_lon, 'Numeric longitude column'),
                ('Location', '_combo_loc', self._on_loc, 'Location name (choropleth)'),
                ('Colour', '_combo_color', self._on_color, 'Colour points / regions by'),
                ('Value', '_combo_value', self._on_value, 'Measure used for colour / size')):
            box, cb = self._combo_section(title, hint, [NONE_SENTINEL] + allc,
                                          NONE_SENTINEL, handler)
            setattr(self, attr, cb)
            flow.add_section(box)

        box, self._combo_agg = self._combo_section(
            'Aggregation', 'How to combine values per location',
            AGGS, AGGS[0], self._on_agg)
        flow.add_section(box)

        box, self._combo_style = self._combo_section(
            'Style', 'Base map style (scatter mode)',
            MAP_STYLES, MAP_STYLES[0], self._on_style)
        flow.add_section(box)

        opac = slider_with_readout(0.1, 1.0,
                                   float(self._pending.get('marker_opacity', 0.82)),
                                   is_int=False, step=0.02)
        self._gen_opacity = opac._as_spin
        try:
            self._gen_opacity.valueChanged.connect(
                lambda v: self._set_pending(marker_opacity=float(v)))
        except Exception:
            pass
        flow.add_section(section_box('Opacity', opac, 'Marker transparency'))

        # Invert / Borders are the two boolean tail controls in the old row.
        self._chk_invert, self._chk_borders = None, None
        for title, key, dflt in (('Invert', 'invert_colorscale', False),
                                 ('Borders', 'show_borders', True)):
            cb = QCheckBox('')
            cb.setChecked(bool(self._pending.get(key, dflt)))
            cb.toggled.connect(lambda v, k=key: self._set_pending(**{k: bool(v)}))
            if key == 'show_borders':
                self._chk_borders = cb
            else:
                self._chk_invert = cb
            holder = QWidget()
            bl = QVBoxLayout(holder)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(HINT_GAP)
            bl.addWidget(cb)
            bl.addStretch()
            flow.add_section(section_box(title, holder, ''))

        rl.addWidget(flow)
        rl.addStretch()
        # Borders only exists on the choropleth trace. A scattermap marker has
        # no outline and layout.map exposes no country toggle, so leaving the
        # box live in scatter mode would promise an effect that cannot happen.
        self._sync_borders_enabled()
        return root

    def _sync_borders_enabled(self):
        """Enable Borders only where the chart can honour it."""
        cb = getattr(self, '_chk_borders', None)
        if cb is None:
            return
        is_geo = 'choropleth' in str(
            self._pending.get('map_mode', MAP_MODES[0])).lower()
        cb.setEnabled(is_geo)
        cb.setToolTip('' if is_geo else
                       'Country borders apply to Choropleth (Location Names) only.')

    def _set_pending(self, **kw):
        """Record a Generate value and mark the form dirty."""
        self._pending.update(kw)
        self._mark_dirty(emit=True)

    # -- Generate handlers -------------------------------------------------
    def _on_mode(self, text):
        self._set_pending(map_mode=str(text))
        self._sync_borders_enabled()

    def _on_lat(self, text):
        self._set_pending(lat_col=self._clean(text))

    def _on_lon(self, text):
        self._set_pending(lon_col=self._clean(text))

    def _on_loc(self, text):
        self._set_pending(location_col=self._clean(text))

    def _on_color(self, text):
        self._set_pending(color_col=self._clean(text))

    def _on_value(self, text):
        self._set_pending(value_col=self._clean(text))

    def _on_agg(self, text):
        self._set_pending(agg_func=str(text))

    def _on_style(self, text):
        self._set_pending(map_style=str(text))

    def collect_values(self):
        """The map Generate values, keyed as the map service branch reads them.

        There is no separate ``geo_col`` control: one Location field serves both
        modes (it is ``location_col`` for a scatter and the choropleth's
        ``geo_col``), so a second field for it would be a duplicate the user has
        to keep in sync. The service's map branch reads the same value under
        either name.
        """
        p = self._pending
        return {k: p.get(k) for k in (
            'map_mode', 'lat_col', 'lon_col', 'location_col', 'color_col',
            'value_col', 'agg_func', 'map_style', 'marker_opacity',
            'invert_colorscale', 'show_borders')}

    def prefill(self, values):
        """Restore a saved map config, snapping names to options that exist."""
        v = values or {}
        p = self._pending
        for key in ('map_mode', 'lat_col', 'lon_col', 'location_col', 'color_col',
                    'value_col', 'agg_func', 'map_style', 'marker_opacity',
                    'invert_colorscale', 'show_borders'):
            if key in v:
                p[key] = v[key]
        for attr, key, dflt in (('_combo_mode', 'map_mode', MAP_MODES[0]),
                                ('_combo_lat', 'lat_col', ''),
                                ('_combo_lon', 'lon_col', ''),
                                ('_combo_loc', 'location_col', ''),
                                ('_combo_color', 'color_col', ''),
                                ('_combo_value', 'value_col', ''),
                                ('_combo_agg', 'agg_func', AGGS[0]),
                                ('_combo_style', 'map_style', MAP_STYLES[0])):
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
        w = getattr(self, '_gen_opacity', None)
        if w is not None:
            try:
                w.blockSignals(True)
                w.setValue(float(p.get('marker_opacity', 0.82) or 0.82))
                w.blockSignals(False)
            except Exception:
                pass




    def _add_map_rows(self, root):
        """Append the two map toggles and the marker controls."""
        self._ck_cbar = QCheckBox('Colorbar')
        self._ck_cbar.setChecked(bool(self._layout_opts.get("show_colorbar", True)))
        self._ck_cbar.toggled.connect(
            lambda v: self._set_layout(show_colorbar=bool(v)))
        self._add_option_row(root, self._check_row(self._ck_cbar))

        self._ck_sizeby = QCheckBox('Size by value')
        self._ck_sizeby.setChecked(bool(self._layout_opts.get("size_by_value", False)))
        self._ck_sizeby.toggled.connect(
            lambda v: self._set_layout(size_by_value=bool(v)))
        self._add_option_row(root, self._check_row(self._ck_sizeby))

        # -- The two marker sliders that sat behind the old "Advanced" -------
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

    def build_layout(self):
        """The inherited colour-scale page plus the map rows, minus the axis rows."""
        root = super().build_layout()
        # A geo chart has no Cartesian axes and no bar labels to place. The
        # heatmap ANNOTATION rows go too: a geo trace has no in-cell text, so
        # those controls would have nothing to act on.
        self._mask_rows(root, ("X-axis", "Y-axis", "Label pos", "Annotations",
                               "Ann size", "Ann color", "Precision"))
        self._add_map_rows(root)
        return root
