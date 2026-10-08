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
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox,
                             QCheckBox)
from .base import (hint_label, cap_widget, color_swatch, slider_with_readout,
                   page_root, section_box, grid_row, FlowGrid, GEN_W, HINT_GAP)
from .correlation import CorrelationSettings
from .statistical import NONE_SENTINEL
from .categorical import AGGS

# Generate-row options. The two map modes are the exact strings the service
# branch compares against.
MAP_MODES = ["Scatter (Lat/Lon)", "Choropleth (Location Names)"]

# Which Generate sections each mode's runner call actually receives.
#
# This split is read off ``services.analysis_service``'s map branch, which is
# what really decides -- the runner never sees a field the service did not
# pass to it:
#
# * the choropleth branch passes geo/value/color/agg/projection/scope/borders
#   and NOT lat/lon/style/opacity/render, so those five are scatter-only;
# * the scatter branch passes lat/lon/location/value/color/agg/style/opacity/
#   render/borders and NOT projection/scope, so those two are choropleth-only.
#
# Location, Colour, Value, Aggregation and the six hover fields are passed by
# BOTH branches (a scatter groups by location when a Location + Value are
# chosen; the choropleth's Borders is the same checkbox), so they stay visible
# in both modes. Hiding them would remove a capability the runner honours,
# which is a different bug from the one being fixed: the report was that the
# page shows fields the SELECTED mode ignores.
SCATTER_ONLY_SECTIONS = ("Latitude", "Longitude", "Style", "Opacity", "Render")
CHOROPLETH_ONLY_SECTIONS = ("Projection", "Scope")

# Basemaps: the two "OpenStreetMap" entries are CARTO's key-free VECTOR
# styles from the runner's own map (modules.analysis.map_plot._MAP_STYLE_SPECS
# -> carto-positron / carto-darkmatter) -- vector tiles are sharp at any zoom
# and on any HiDPI screen, which is what fixed the "blurry map" report.
# Satellite has no free key-less vector imagery, so it stays the one raster
# basemap (Esri World Imagery) and is the softer of the three.
# ``stamen-terrain`` and friends are dead registry names that blanked the
# canvas; the runner normalises any legacy value onto these three.
# Kept as a literal (not imported) so the settings panel does not drag the
# whole backend runner into the Qt import path.
MAP_STYLES = ["OpenStreetMap (Light)", "OpenStreetMap (Dark)", "Satellite (Earth)"]

# Density is the default: a map that silently drops points misstates the
# spatial analysis, so sampling is only ever an explicit choice.
RENDER_MODES = ["Density (all points)", "Sample 5,000"]

# Choropleth-only projection / scope, from the runner
# (modules.analysis.map_plot._PROJECTIONS / _SCOPES).  Both were in the old
# panel's row and both are honoured by ``px.choropleth``, so they belong here;
# a chart that is a scatter simply ignores them.  Kept as literals for the same
# reason as MAP_STYLES: the panel must not import the backend runner.
PROJECTIONS = ["natural earth", "mercator", "equirectangular",
               "orthographic", "kavrayskiy7", "robinson"]

SCOPES = ["world", "usa", "europe", "asia", "africa",
          "north america", "south america"]


def _normalize_style_name(value) -> str:
    """Snap any legacy/Plotly style name onto one of the three basemaps.

    Mirrors ``_normalize_map_style`` in the runner so the combo, the form and
    the figure never disagree about which basemap is selected.

    Only names that really mean aerial imagery reach the Satellite basemap: it
    is the one raster (and so the one soft) basemap of the three, and a dead
    landscape style such as ``stamen-terrain`` must not select it.
    """
    s = str(value or "").lower()
    if any(k in s for k in ("dark", "matter", "night", "moon")):
        return "OpenStreetMap (Dark)"
    if any(k in s for k in ("satellite", "earth", "imagery", "esri", "aerial")):
        return "Satellite (Earth)"
    return "OpenStreetMap (Light)"


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
            'show_borders': True, 'render_mode': RENDER_MODES[0],
            'choropleth_projection': PROJECTIONS[0],
            'choropleth_scope': SCOPES[0],
            'hover_dim1': None, 'hover_dim2': None,
            'hover_val1': None, 'hover_val1_agg': AGGS[0],
            'hover_val2': None, 'hover_val2_agg': AGGS[0],
        }
        for attr in ('_combo_mode', '_combo_lat', '_combo_lon', '_combo_loc',
                     '_combo_color', '_combo_value', '_combo_agg',
                     '_combo_style', '_combo_render', '_gen_opacity',
                     '_combo_projection', '_combo_scope',
                     '_combo_hdim1', '_combo_hdim2', '_combo_hval1',
                     '_combo_hagg1', '_combo_hval2', '_combo_hagg2',
                     '_ck_cbar', '_ck_sizeby', '_ms_spin', '_mo_spin'):
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
        # Title -> section box, so _sync_mode_sections can show/hide by name.
        # Kept in ADD order so hiding and re-showing restores the original
        # arrangement rather than a shuffled one.
        self._gen_flow = flow
        self._gen_sections = {}

        def add(title, box):
            self._gen_sections[title] = box
            flow.add_section(box)
            return box

        box, self._combo_mode = self._combo_section(
            'Mode', 'Scatter plots points; Choropleth fills whole countries.',
            MAP_MODES, MAP_MODES[0], self._on_mode)
        add('Mode', box)

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
            add(title, box)

        box, self._combo_agg = self._combo_section(
            'Aggregation', 'How to combine values per location',
            AGGS, AGGS[0], self._on_agg)
        add('Aggregation', box)

        box, self._combo_style = self._combo_section(
            'Style', 'Base map tiles (scatter mode)',
            MAP_STYLES, MAP_STYLES[0], self._on_style)
        add('Style', box)

        box, self._combo_render = self._combo_section(
            'Render', 'Density shows every point (default)',
            RENDER_MODES, RENDER_MODES[0], self._on_render)
        add('Render', box)

        # Projection / Scope are choropleth-only in the service; they are built
        # unconditionally (a mode switch must never re-create controls, which
        # would lose whatever was already chosen) and then shown/hidden by
        # _sync_mode_sections according to the selected mode.
        box, self._combo_projection = self._combo_section(
            'Projection', 'How the globe is flattened (choropleth)',
            PROJECTIONS, PROJECTIONS[0], self._on_projection)
        add('Projection', box)

        box, self._combo_scope = self._combo_section(
            'Scope', 'Which region the choropleth frames',
            SCOPES, SCOPES[0], self._on_scope)
        add('Scope', box)

        # The six extra tooltip fields, in the old panel's order:
        # Hover Dim 1 | Hover Dim 2 | Hover Value 1 | Agg 1 |
        # Hover Value 2 | Agg 2.
        for title, attr, handler, hint in (
                ('Hover Dim 1', '_combo_hdim1', self._on_hdim1,
                 'Extra column shown in each tooltip'),
                ('Hover Dim 2', '_combo_hdim2', self._on_hdim2,
                 'Second extra tooltip column'),
                ('Hover Value 1', '_combo_hval1', self._on_hval1,
                 'Measure added to each tooltip'),
                ('Agg 1', '_combo_hagg1', self._on_hagg1,
                 'How Hover Value 1 is combined'),
                ('Hover Value 2', '_combo_hval2', self._on_hval2,
                 'Second measure added to each tooltip'),
                ('Agg 2', '_combo_hagg2', self._on_hagg2,
                 'How Hover Value 2 is combined')):
            items = AGGS if attr in ('_combo_hagg1', '_combo_hagg2') \
                else [NONE_SENTINEL] + allc
            dflt = AGGS[0] if attr in ('_combo_hagg1', '_combo_hagg2') \
                else NONE_SENTINEL
            box, cb = self._combo_section(title, hint, items, dflt, handler)
            setattr(self, attr, cb)
            add(title, box)

        opac = slider_with_readout(0.1, 1.0,
                                   float(self._pending.get('marker_opacity', 0.82)),
                                   is_int=False, step=0.02)
        self._gen_opacity = opac._as_spin
        try:
            self._gen_opacity.valueChanged.connect(
                lambda v: self._set_pending(marker_opacity=float(v)))
        except Exception:
            pass
        add('Opacity', section_box('Opacity', opac, 'Marker transparency'))

        # Invert / Borders are the two boolean tail controls in the old row.
        # They share ONE section box side by side instead of taking a full
        # GEN_W grid column each, which is what left the oversized gap between
        # each label and its checkbox.
        self._chk_invert, self._chk_borders = None, None
        toggles = QWidget()
        tl = QHBoxLayout(toggles)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(12)
        for title, key, dflt in (('Invert', 'invert_colorscale', False),
                                 ('Borders', 'show_borders', True)):
            cell = QWidget()
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(2)
            cb = QCheckBox(title)
            cb.setChecked(bool(self._pending.get(key, dflt)))
            cb.toggled.connect(lambda v, k=key: self._set_pending(**{k: bool(v)}))
            if key == 'show_borders':
                self._chk_borders = cb
            else:
                self._chk_invert = cb
            cl.addWidget(cb)
            cl.addStretch()
            tl.addWidget(cell)
        add('Options', section_box(
            'Options', toggles,
            'Invert flips the colour scale; Borders adds a boundaries overlay'))

        rl.addWidget(flow)
        rl.addStretch()
        # Show only the sections the current mode reads. Runs here as well as
        # on every mode change so a restored/prefilled config lands in the
        # right shape instead of showing the other mode's fields first.
        self._sync_mode_sections()
        return root

    # -- Mode-gated visibility ----------------------------------------------
    def _sync_mode_sections(self) -> None:
        """Show only the Generate sections the selected mode actually reads.

        ``build_generate`` builds the union of both forms on purpose (a mode
        switch must never re-create controls, which would discard whatever was
        already chosen); this is the second half of that decision: the fields
        the SERVICE ignores in the current mode are hidden, and the FlowGrid
        re-flows so the remaining ones keep their even spacing.

        Values are never cleared on a switch -- hide only. Switching back
        restores the user's earlier Lat/Lon or Projection/Scope exactly as
        they left them.
        """
        flow = getattr(self, '_gen_flow', None)
        sections = getattr(self, '_gen_sections', None)
        if flow is None or not sections:
            return
        mode = str(self._pending.get('map_mode') or MAP_MODES[0])
        choropleth = mode == MAP_MODES[1]
        scatter_only = SCATTER_ONLY_SECTIONS
        choro_only = CHOROPLETH_ONLY_SECTIONS
        for title, box in sections.items():
            if title in scatter_only:
                flow.set_section_hidden(box, choropleth)
            elif title in choro_only:
                flow.set_section_hidden(box, not choropleth)

    def _set_pending(self, **kw):
        """Record a Generate value and mark the form dirty."""
        self._pending.update(kw)
        self._mark_dirty(emit=True)

    # -- Generate handlers -------------------------------------------------
    def _on_mode(self, text):
        self._set_pending(map_mode=str(text))
        self._sync_mode_sections()

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

    def _on_render(self, text):
        self._set_pending(render_mode=str(text))

    def _on_projection(self, text):
        self._set_pending(choropleth_projection=str(text))

    def _on_scope(self, text):
        self._set_pending(choropleth_scope=str(text))

    def _on_hdim1(self, text):
        self._set_pending(hover_dim1=self._clean(text))

    def _on_hdim2(self, text):
        self._set_pending(hover_dim2=self._clean(text))

    def _on_hval1(self, text):
        self._set_pending(hover_val1=self._clean(text))

    def _on_hagg1(self, text):
        self._set_pending(hover_val1_agg=str(text))

    def _on_hval2(self, text):
        self._set_pending(hover_val2=self._clean(text))

    def _on_hagg2(self, text):
        self._set_pending(hover_val2_agg=str(text))

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
            'invert_colorscale', 'show_borders', 'render_mode',
            'choropleth_projection', 'choropleth_scope',
            'hover_dim1', 'hover_dim2',
            'hover_val1', 'hover_val1_agg', 'hover_val2', 'hover_val2_agg')}

    def prefill(self, values):
        """Restore a saved map config, snapping names to options that exist.

        A saved style from an older build may name a Plotly style the runner no
        longer offers (``stamen-terrain``, ``carto-positron``, ...).  Those are
        normalised onto one of the three current basemaps here as well as in
        the runner, so the panel shows what the chart will actually draw
        instead of leaving a stale value in the form that no combo displays.
        """
        v = dict(values or {})
        if 'map_style' in v:
            v['map_style'] = _normalize_style_name(v['map_style'])
        p = self._pending
        for key in ('map_mode', 'lat_col', 'lon_col', 'location_col', 'color_col',
                    'value_col', 'agg_func', 'map_style', 'marker_opacity',
                    'invert_colorscale', 'show_borders', 'render_mode',
                    'choropleth_projection', 'choropleth_scope',
                    'hover_dim1', 'hover_dim2',
                    'hover_val1', 'hover_val1_agg',
                    'hover_val2', 'hover_val2_agg'):
            if key in v:
                p[key] = v[key]
        for attr, key, dflt in (('_combo_mode', 'map_mode', MAP_MODES[0]),
                                ('_combo_lat', 'lat_col', ''),
                                ('_combo_lon', 'lon_col', ''),
                                ('_combo_loc', 'location_col', ''),
                                ('_combo_color', 'color_col', ''),
                                ('_combo_value', 'value_col', ''),
                                ('_combo_agg', 'agg_func', AGGS[0]),
                                ('_combo_style', 'map_style', MAP_STYLES[0]),
                                ('_combo_render', 'render_mode', RENDER_MODES[0]),
                                ('_combo_projection', 'choropleth_projection',
                                 PROJECTIONS[0]),
                                ('_combo_scope', 'choropleth_scope', SCOPES[0]),
                                ('_combo_hdim1', 'hover_dim1', ''),
                                ('_combo_hdim2', 'hover_dim2', ''),
                                ('_combo_hval1', 'hover_val1', ''),
                                ('_combo_hagg1', 'hover_val1_agg', AGGS[0]),
                                ('_combo_hval2', 'hover_val2', ''),
                                ('_combo_hagg2', 'hover_val2_agg', AGGS[0])):
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
        # The mode combo above is set with signals blocked, so _on_mode never
        # fires here -- re-gate the sections explicitly or a restored
        # choropleth would come back showing the scatter-only fields.
        self._sync_mode_sections()




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
