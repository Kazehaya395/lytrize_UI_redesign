"""Generate and display controls for the new ECharts visual families."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QSpinBox, QWidget, QVBoxLayout

from .base import FlowGrid, GEN_W, HINT_GAP, cap_widget, hint_label, multi_select_combo, section_box
from .categorical import CategoricalSettings
from .pie_chart import PieChartSettings


class AdvancedChartSettings(CategoricalSettings):
    family = ""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._extra_fields = {}
        self._extra_layout_fields = {}

    def _choices(self):
        dims = list(self._group_cols or [])
        nums = list(self._num_cols or [])
        if self.family == "sankey":
            return [("source_col", dims, dims[0] if dims else ""),
                    ("target_col", dims, dims[1] if len(dims) > 1 else ""),
                    ("value_col", ["Row count"] + nums, nums[0] if nums else "Row count")]
        if self.family == "sunburst":
            return [("path_cols", dims, dims[:3]),
                    ("value_col", ["Row count"] + nums, nums[0] if nums else "Row count")]
        if self.family == "calendar_heatmap":
            dates = list(self._dt_cols or [])
            return [("date_col", dates, dates[0] if dates else ""),
                    ("value_col", ["Row count"] + nums, nums[0] if nums else "Row count")]
        if self.family == "globe_extruded_map":
            return [("location_col", dims, dims[0] if dims else ""),
                    ("value_col", ["Row count"] + nums, nums[0] if nums else "Row count")]
        if self.family in {"waterfall", "funnel"}:
            return [("x", dims, dims[0] if dims else ""),
                    ("value_col", ["Row count"] + nums,
                     nums[0] if nums else "Row count")]
        return []

    def build_generate(self):
        root = super().build_generate()
        if self.family in {"sankey", "sunburst", "calendar_heatmap", "globe_extruded_map",
                           "waterfall", "funnel"}:
            self._mask_rows(root, {"Dimension", "Metric", "📊 Direction", "Top N",
                                   "Secondary Y-Axis", "Secondary Agg"})
        if self.family in {"sankey", "sunburst", "calendar_heatmap", "waterfall", "funnel"}:
            self._combo_agg.setCurrentText("Sum")
        self._extra_fields = {}
        if not self._choices():
            return root
        flow = FlowGrid()
        for key, choices, default in self._choices():
            if key == "path_cols":
                widget = multi_select_combo(choices)
                widget.set_selection(default)
                widget.selection_changed.connect(lambda selected, k=key: self._on_extra(k, selected))
            else:
                widget = QComboBox()
                widget.addItems([str(item) for item in choices] or [""])
                if default in choices:
                    widget.setCurrentText(str(default))
                widget.currentTextChanged.connect(lambda selected, k=key: self._on_extra(k, selected))
            cap_widget(widget, GEN_W)
            self._extra_fields[key] = widget
            body = QWidget()
            lay = QVBoxLayout(body)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(HINT_GAP)
            lay.addWidget(widget)
            flow.add_section(section_box(key.replace("_", " ").title(), body,
                "Choose fields for this visual"))
            self._pending[key] = list(default) if key == "path_cols" else default
        root.layout().insertWidget(max(1, root.layout().count() - 1), flow)
        return root

    def _on_extra(self, key, value):
        self._pending[key] = list(value) if key == "path_cols" else value
        self._mark_dirty(emit=True)

    def collect_values(self):
        values = dict(super().collect_values())
        for key in ("source_col", "target_col", "value_col", "path_cols", "date_col", "location_col", "x"):
            if key in self._pending:
                value = self._pending[key]
                values[key] = (None if value in ("", "Row count") and key == "x"
                               else value)
        return values

    def prefill(self, values):
        super().prefill(values)
        values = dict(values or {})
        for key, widget in self._extra_fields.items():
            selected = values.get(key)
            if key == "path_cols":
                if selected:
                    widget.set_selection(selected)
                self._pending[key] = widget.current_selected()
            else:
                if selected and widget.findText(str(selected)) >= 0:
                    widget.setCurrentText(str(selected))
                self._pending[key] = widget.currentText()

    def prefill_layout(self, values):
        super().prefill_layout(values)
        values = dict(values or {})
        for key, widget in self._extra_layout_fields.items():
            if key not in values:
                continue
            try:
                widget.blockSignals(True)
                if isinstance(widget, QSpinBox):
                    widget.setValue(int(values[key]))
                else:
                    widget.setCurrentText(str(values[key]))
                widget.blockSignals(False)
                self._layout_opts[key] = values[key]
            except Exception:
                try:
                    widget.blockSignals(False)
                except Exception:
                    pass

    def build_layout(self):
        root = super().build_layout()
        from .base import page_root, grid_row
        # The categorical template is reused only for its shared shell. These
        # renderer-independent rows do not make sense for flow, hierarchy,
        # calendar, funnel, or map visuals, so remove them from the form rather
        # than leaving controls that appear to work but cannot affect options.
        irrelevant = {
            "sankey": {"X-axis", "Y-axis", "Legend title", "Legend position",
                       "Legend layout", "Click", "Show value labels", "Label pos",
                       "Bar mode", "Bar gap", "Show grid", "Show border"},
            "sunburst": {"X-axis", "Y-axis", "Legend title", "Legend position",
                         "Legend layout", "Click", "Show value labels", "Label pos",
                         "Bar mode", "Bar gap", "Show grid", "Show border"},
            "calendar_heatmap": {"X-axis", "Y-axis", "Legend title", "Legend position",
                                 "Legend layout", "Click", "Show value labels", "Label pos",
                                 "Bar mode", "Bar gap", "Show grid", "Show border"},
            "waterfall": {"Legend title", "Legend position", "Legend layout", "Click",
                          "Show value labels", "Label pos", "Bar mode", "Bar gap",
                          "Show border"},
            "funnel": {"X-axis", "Y-axis", "Legend title", "Legend position",
                       "Legend layout", "Click", "Label pos", "Bar mode", "Bar gap",
                       "Show grid", "Show border"},
            "globe_extruded_map": {"X-axis", "Y-axis", "Legend title", "Legend position",
                                   "Legend layout", "Click", "Show value labels", "Label pos",
                                   "Bar mode", "Bar gap", "Show grid"},
        }
        self._mask_rows(root, irrelevant.get(self.family, set()))
        extras = {
            "sankey": [("sankey_orient", "Orientation", ["horizontal", "vertical"]),
                       ("sankey_align", "Node alignment", ["justify", "left", "right"]),
                       ("sankey_node_gap", "Node gap (px)", []),
                       ("sankey_node_width", "Node width (px)", []),
                       ("sankey_iterations", "Layout iterations", [])],
            "sunburst": [("sunburst_node_click", "Drill behavior", ["rootToNode", "link", "false"]),
                         ("sunburst_inner_radius", "Inner radius (%)", []),
                         ("sunburst_sort", "Slice order", ["desc", "asc"]),
                         ("sunburst_label_rotate", "Label rotation", ["radial", "tangential", "horizontal"])],
            "calendar_heatmap": [("calendar_orient", "Orientation", ["horizontal", "vertical"]),
                                 ("calendar_cell_height", "Cell height (px)", [])],
            "waterfall": [("waterfall_start", "Starting value", [])],
            "funnel": [("funnel_sort", "Stage order", ["descending", "ascending", "none"]),
                       ("funnel_orient", "Orientation", ["vertical", "horizontal"]),
                       ("funnel_label_position", "Label placement", ["inside", "insideLeft", "insideRight", "outside"]),
                       ("funnel_gap", "Stage gap (px)", []),
                       ("funnel_min_size", "Minimum width (%)", []),
                       ("funnel_max_size", "Maximum width (%)", [])],
            "globe_extruded_map": [("map_projection", "Map projection", ["natural earth", "mercator"])],
            "nightingale_rose": [("rose_type", "Rose mode", ["area", "radius"])],
        }.get(self.family, [])
        if not extras:
            return root
        flow = FlowGrid()
        self._extra_layout_fields = {}
        for key, label, choices in extras:
            widget = QSpinBox() if not choices else QComboBox()
            if choices:
                widget.addItems(choices)
            else:
                if key == "sunburst_inner_radius":
                    widget.setRange(0, 70)
                elif key.startswith("sankey_"):
                    widget.setRange(0 if key.endswith("iterations") else 4,
                                    64 if key.endswith("iterations") else 80)
                elif key == "calendar_cell_height":
                    widget.setRange(8, 48)
                elif key in {"funnel_min_size", "funnel_max_size"}:
                    widget.setRange(0 if key == "funnel_min_size" else 1, 100)
                elif key == "funnel_gap":
                    widget.setRange(0, 40)
                else:
                    widget.setRange(-1000000000, 1000000000)
            numeric_defaults = {"sankey_node_gap": 10, "sankey_node_width": 18,
                                "sankey_iterations": 32,
                                "sunburst_inner_radius": 0, "waterfall_start": 0,
                                "calendar_cell_height": 18, "funnel_gap": 2,
                                "funnel_min_size": 0, "funnel_max_size": 100}
            default = self._layout_opts.get(key, numeric_defaults.get(key, 0) if not choices else choices[0])
            if choices:
                widget.setCurrentText(str(default))
                widget.currentTextChanged.connect(lambda v, k=key: self._set_extra_layout(k, v))
            else:
                widget.setValue(int(default))
                widget.valueChanged.connect(lambda v, k=key: self._set_extra_layout(k, v))
            cap_widget(widget, GEN_W)
            self._extra_layout_fields[key] = widget
            body = QWidget()
            lay = QVBoxLayout(body)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(widget)
            flow.add_section(section_box(label, body, "Family-specific display control"))
        root.layout().insertWidget(max(1, root.layout().count() - 1), flow)
        return root

    def build_typography(self):
        root = super().build_typography()
        self._mask_rows(root, {"Title size", "Title color", "Axis size", "Axis color",
                               "Legend size", "Legend color"})
        return root

    def _set_extra_layout(self, key, value):
        self._layout_opts[key] = value
        self._dirty = True
        self.values_changed.emit({"layout": dict(self._layout_opts)})


class SankeySettings(AdvancedChartSettings): family = "sankey"
class SunburstSettings(AdvancedChartSettings): family = "sunburst"
class CalendarHeatmapSettings(AdvancedChartSettings): family = "calendar_heatmap"
class GlobeMapSettings(AdvancedChartSettings): family = "globe_extruded_map"
class WaterfallSettings(AdvancedChartSettings): family = "waterfall"
class FunnelSettings(AdvancedChartSettings): family = "funnel"
class RoseSettings(PieChartSettings):
    family = "nightingale_rose"

    def build_layout(self):
        root = super().build_layout()
        flow = FlowGrid()
        choices = ["area", "radius"]
        widget = QComboBox()
        widget.addItems(choices)
        widget.setCurrentText(str(self._layout_opts.get("rose_type", "area")))
        widget.currentTextChanged.connect(lambda value: self._set_layout(rose_type=value))
        cap_widget(widget, GEN_W)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        flow.add_section(section_box("Rose mode", body, "Area = equal angle; Radius = value-scaled radius"))
        root.layout().insertWidget(max(1, root.layout().count() - 1), flow)
        self._rose_mode = widget
        return root

    def prefill_layout(self, values):
        super().prefill_layout(values)
        mode = str((values or {}).get("rose_type", "area"))
        if getattr(self, "_rose_mode", None):
            self._rose_mode.blockSignals(True)
            self._rose_mode.setCurrentText(mode)
            self._rose_mode.blockSignals(False)
        self._layout_opts["rose_type"] = mode
