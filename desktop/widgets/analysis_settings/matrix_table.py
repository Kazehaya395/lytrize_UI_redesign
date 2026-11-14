"""Pivot Table settings -- the Matrix Heatmap page plus the table styling rows.

Feature reference (old codebase, ``chart_settings.py`` ``CHART_TYPE_SETTINGS``
and ``_CONTROL_META``)::

    "matrix_table": has_axes=False, has_legend=False,
    controls = [title, table_font_size, table_font_color,
        table_header_font_size, table_header_color, table_row_height,
        table_header_height, table_index_align, table_data_align,
        table_stripe_even_color, table_stripe_odd_color, table_number_format];
    typography = [family, font_style, header].

``CONTROLS_ADVANCED`` put [table_index_align, table_data_align,
table_stripe_even_color, table_stripe_odd_color, table_number_format,
table_header_height] behind the old popover; the two font sizes/colours and the
row height were in the main view. All twelve become ordinary Layout rows.

Why it subclasses ``MatrixHeatmapSettings``
-----------------------------------------
The two charts are the same cross-tab with a different renderer, so they take
the SAME index / columns / values / aggregation / sort inputs. Inheriting the
heatmap Generate tab verbatim means the cross-tab selection cannot drift between
the two views of one dataset, and only the Layout tab has to be written here.

The typography difference is the old ``["family", "font_style", "header"]`` list:
no axis rows (a Plotly table has no axes) and no legend rows (no legend). The
table's own header/body font sizes and colours are Layout options instead, which
is where ``_apply_table_options`` reads them.
"""
from __future__ import annotations
from PySide6.QtWidgets import QCheckBox, QComboBox
from .base import cap_widget, color_swatch, slider_with_readout, grid_row
from .matrix_heatmap import MatrixHeatmapSettings

# Old _CONTROL_META option lists. The number formats are d3-format strings,
# exactly as the old app stored them, so a saved chart keeps its formatting.
TABLE_ALIGNS = ["left", "center", "right"]
TABLE_NUM_FORMATS = [",.2f", ",.1f", ",.0f", ".2f", ".1f", ".0f"]


class MatrixTableSettings(MatrixHeatmapSettings):
    """Generate + Layout + Typography settings for the Pivot Table type."""

    supports_row_hierarchy = True

    def _build(self):
        super()._build()
        for attr in ('_cb_ialign', '_cb_dalign', '_cb_numfmt', '_rqh_spin',
                     '_rfh_spin', '_tfs_spin', '_thfs_spin', '_sw_tfc',
                     '_sw_thc', '_sw_tsec', '_sw_tsoc'):
            if not hasattr(self, attr):
                setattr(self, attr, None)

    def _add_table_rows(self, root):
        """Append the twelve table styling rows, split across the columns.

        The inherited Layout tab leaves its left column with only "Chart
        title" once the heatmap rows are masked, so appending every table
        row to the right column produced one starved column beside one
        overflowing one (the reported spacing bug). The body/colour rows go
        under "Chart title" in the left column; the header/alignment/format
        and heat rows continue the right column, which keeps its inherited
        "Show border" row.
        """
        # -- Main view in the old app: the two font sizes + the row height ----
        for attr, key, lo, hi, dflt, label, cast in (
                ('_tfs_spin', 'table_font_size', 8, 24, 11, 'Font size', int),
                ('_thfs_spin', 'table_header_font_size', 8, 24, 12, 'Hdr font', int),
                ('_rfh_spin', 'table_row_height', 16, 60, 26, 'Row h', int)):
            sl = slider_with_readout(lo, hi, int(self._layout_opts.get(key, dflt)))
            spin = sl._as_spin
            setattr(self, attr, spin)
            try:
                spin.valueChanged.connect(
                    lambda v, k=key, c=cast: self._set_layout(**{k: c(v)}))
            except Exception:
                pass
            self._add_option_row(root, grid_row(label, sl), column_index=0)

        # -- The four colour rows --------------------------------------------
        for attr, key, dflt, label in (
                ('_sw_tfc', 'table_font_color', 'auto', 'Font color'),
                ('_sw_thc', 'table_header_color', '#6163df', 'Hdr color'),
                ('_sw_tsec', 'table_stripe_even_color', 'auto', 'Even stripe'),
                ('_sw_tsoc', 'table_stripe_odd_color', 'auto', 'Odd stripe')):
            sw = color_swatch(str(self._layout_opts.get(key, dflt)),
                              lambda c, k=key: self._set_layout(**{k: c}))
            setattr(self, attr, sw)
            self._add_option_row(root, grid_row(label, sw), column_index=0)

        # -- The old "Advanced" popover, in its order ------------------------
        thh = slider_with_readout(16, 60,
                                  int(self._layout_opts.get("table_header_height", 28)))
        self._rqh_spin = thh._as_spin
        try:
            self._rqh_spin.valueChanged.connect(
                lambda v: self._set_layout(table_header_height=int(v)))
        except Exception:
            pass
        self._add_option_row(root, grid_row('Hdr h', thh))

        for attr, key, items, dflt, label in (
                ('_cb_ialign', 'table_index_align', TABLE_ALIGNS, 'left', 'Index align'),
                ('_cb_dalign', 'table_data_align', TABLE_ALIGNS, 'right', 'Data align'),
                ('_cb_numfmt', 'table_number_format', TABLE_NUM_FORMATS, ',.2f',
                 'Num format')):
            cb = QComboBox()
            cap_widget(cb, 200)
            cb.addItems(items)
            cb.setCurrentText(str(self._layout_opts.get(key, dflt)))
            cb.currentTextChanged.connect(
                lambda t, k=key: self._set_layout(**{k: t}))
            setattr(self, attr, cb)
            self._add_option_row(root, grid_row(label, cb))

        # -- Conditional heat coloring -----------------------------------------
        heat_cb = QCheckBox("Heat coloring")
        heat_cb.setChecked(bool(self._layout_opts.get("table_heat_enabled", True)))
        heat_sw = color_swatch(str(self._layout_opts.get("table_heat_color", "#1e40af")),
                               lambda c: self._set_layout(table_heat_color=c))
        heat_cb.toggled.connect(lambda checked: self._set_layout(table_heat_enabled=bool(checked)))
        self._heat_cb = heat_cb
        self._sw_heat = heat_sw
        self._add_option_row(root, grid_row("Heat color on", heat_cb))
        self._add_option_row(root, grid_row("Heat accent", heat_sw))

    def build_layout(self):
        """The shared Layout tab with the heatmap rows replaced by table rows.

        The inherited page is a heatmap page, so its colour-scale, annotation and
        colour-bar rows are masked first -- none of them mean anything for a
        Plotly table, and leaving them would be a control that reads as broken
        rather than as inapplicable.
        """
        root = super().build_layout()
        self._mask_rows(root, ("Colorscale", "CB title", "CB tick sz",
                               "CB tick clr", "CB title sz", "CB title clr",
                               "Colorbar pos", "Annotations", "Ann size",
                               "Precision", "Ann color", "Label pos"))
        self._drop_empty_option_columns(root)
        self._add_table_rows(root)
        return root

    def build_typography(self):
        """Typography down to the old [family, font_style, header] list.

        A Plotly table draws no axes and no legend, so every axis and legend
        typography row goes. The table's own header/body font controls are Layout
        options, added above.
        """
        root = super().build_typography()
        self._mask_rows(root, ("Title size", "Title color", "Axis size",
                               "Axis color", "Legend size", "Legend color",
                               "Tooltip size", "Tooltip color"))
        self._drop_empty_option_columns(root)
        return root

