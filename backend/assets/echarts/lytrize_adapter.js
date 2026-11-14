/* Lytrize semantic ChartModel v1 -> Apache ECharts options.
 * Keep saved data declarative. This adapter owns layout policy, including a
 * fixed single-line scroll legend and reserved plot space for outside labels.
 */
(function (global) {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c];
    });
  }

  function cleanNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function fontStyle(typography, part, size, color) {
    var heading = part === "header";
    var style = String((typography || {})[heading ? "header_font_style" : "font_style"] || "Normal").toLowerCase();
    var fallbackColor = heading ? "#111827" : "#263247";
    return {
      fontFamily: String((typography || {})[heading ? "header_family" : "family"] || "Inter"),
      fontSize: Number(size || (heading ? 20 : 12)),
      fontWeight: style.indexOf("bold") >= 0 ? 700 : 400,
      fontStyle: style.indexOf("italic") >= 0 || style.indexOf("oblique") >= 0 ? "italic" : "normal",
      color: configuredColor(color, fallbackColor)
    };
  }

  function parseColor(value) {
    var match = String(value || "").match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?/i);
    if (match && match[4] !== undefined && Number(match[4]) === 0) return null;
    if (match) return [Number(match[1]), Number(match[2]), Number(match[3])];
    var hex = String(value || "").trim().replace(/^#/, "");
    if (/^[0-9a-f]{3}$/i.test(hex)) hex = hex.split("").map(function (c) { return c + c; }).join("");
    if (/^[0-9a-f]{6}$/i.test(hex)) return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
    return null;
  }

  function contrastColor(root, fallback, themeMode) {
    if (themeMode === "dark") return "#f8fafc";
    if (themeMode === "light") return "#111827";
    var node = root;
    while (node && typeof getComputedStyle === "function") {
      var color = getComputedStyle(node).backgroundColor;
      var rgb = parseColor(color);
      if (rgb) {
        var luminance = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
        return luminance > 0.55 ? "#111827" : "#f8fafc";
      }
      node = node.parentElement;
    }
    var ink = parseColor(fallback);
    if (ink) {
      var inkLightness = (0.2126 * ink[0] + 0.7152 * ink[1] + 0.0722 * ink[2]) / 255;
      return inkLightness > 0.55 ? "#f8fafc" : "#111827";
    }
    return "#111827";
  }

  function configuredColor(value, fallback) {
    var color = String(value || "").trim();
    return !color || color.toLowerCase() === "auto" ? fallback : color;
  }

  function formatCompactNumber(value) {
    if (value === null || value === undefined || !isFinite(value)) return "";
    var n = Number(value);
    var sign = n < 0 ? "-" : "";
    var abs = Math.abs(n);
    if (abs >= 1e9) return sign + trimNum(abs / 1e9) + "B";
    if (abs >= 1e6) return sign + trimNum(abs / 1e6) + "M";
    if (abs >= 1e3) return sign + trimNum(abs / 1e3) + "K";
    return sign + trimNum(abs);
  }
  function trimNum(v) {
    if (v >= 100) return String(Math.round(v));
    if (v >= 10) return String(Math.round(v * 10) / 10);
    return String(Math.round(v * 100) / 100);
  }
  // Graphics are the only label surface guaranteed to paint for every ECharts
  // component in every renderer (the continuous visualMap drops its default
  // tick labels and its `name` on some builds), so chart chrome that must
  // always be visible -- the colour-bar title -- is drawn this way.
  function pushGraphic(option, item) {
    if (!option.graphic) option.graphic = [];
    if (!Array.isArray(option.graphic)) option.graphic = [option.graphic];
    option.graphic.push(item);
  }

  function orderedNames(names, order) {
    var result = names.slice();
    var mode = String(order || "Normal").toLowerCase();
    if (mode.indexOf("reverse") >= 0 || mode.indexOf("z-a") >= 0) result.reverse();
    else if (mode.indexOf("a-z") >= 0) result.sort(function (a, b) { return a.localeCompare(b); });
    return result;
  }

  function mapProjection(name) {
    var mode = String(name || "natural earth").toLowerCase();
    if (mode !== "natural earth" && mode !== "mercator" && mode !== "equirectangular") {
      mode = "natural earth";
    }
    if (mode === "equirectangular") {
      return {project: function (point) {
        return [point[0] * Math.PI / 180, point[1] * Math.PI / 180];
      }, unproject: function (point) {
        return [point[0] * 180 / Math.PI, point[1] * 180 / Math.PI];
      }};
    }
    if (mode === "mercator") {
      return {project: function (point) {
        var lambda = point[0] * Math.PI / 180;
        var phi = Math.max(-85.05112878, Math.min(85.05112878, point[1])) * Math.PI / 180;
        return [lambda, Math.log(Math.tan(Math.PI / 4 + phi / 2))];
      }, unproject: function (point) {
        return [point[0] * 180 / Math.PI,
          (2 * Math.atan(Math.exp(point[1])) - Math.PI / 2) * 180 / Math.PI];
      }};
    }
    function xFactor(phi) {
      var p2 = phi * phi, p4 = p2 * p2, p6 = p4 * p2, p12 = p6 * p6;
      return 0.8707 - 0.131979 * p2 + p12 * (-0.013791 + p2 * (0.003971 * p2 - 0.001529 * p4));
    }
    function yProject(phi) {
      var p2 = phi * phi, p4 = p2 * p2, p6 = p4 * p2, p8 = p4 * p4;
      return phi * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4)));
    }
    return {project: function (point) {
      var lambda = point[0] * Math.PI / 180;
      var phi = Math.max(-89.5, Math.min(89.5, point[1])) * Math.PI / 180;
      return [lambda * xFactor(phi), yProject(phi)];
    }, unproject: function (point) {
      var phi = Math.max(-1.56, Math.min(1.56, point[1]));
      for (var i = 0; i < 12; i++) {
        var delta = 1e-6;
        var derivative = (yProject(phi + delta) - yProject(phi - delta)) / (2 * delta);
        if (!Number.isFinite(derivative) || Math.abs(derivative) < 1e-9) break;
        phi -= (yProject(phi) - point[1]) / derivative;
      }
      return [point[0] / xFactor(phi) * 180 / Math.PI, phi * 180 / Math.PI];
    }};
  }

  function buildOption(model, width, height, root) {
    if (!model || model.schema_version !== 1 || model.kind !== "chart") {
      throw new Error("Unsupported Lytrize chart model");
    }
    var display = model.display || {};
    var recipe = model.recipe || {};
    var typography = recipe.typography || {};
    var layout = recipe.layout || {};
    var legend = display.legend || {};
    var labels = display.labels || {};
    var titleText = model.title || "";
    var topTitle = display.title_position !== "bottom";
    var titleBand = titleText ? Math.max(34, Number(typography.header_size || display.title_size || 20) + 16) : 12;
    var legendPos = String(legend.position || "bottom center").toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
    var legendTitle = String(legend.title || "");
    var hasLegend = legend.show !== false;
    var atTop = legendPos.indexOf("top") === 0;
    var atBottom = legendPos.indexOf("bottom") === 0;
    var atLeft = legendPos === "left";
    var atRight = legendPos === "right";
    var atCenter = legendPos.indexOf("center") >= 0;
    var atEnd = legendPos.indexOf("right") >= 0 && !atRight;
    var legendOrientation = String(legend.layout || "auto").toLowerCase();
    if (legendOrientation !== "vertical" && legendOrientation !== "horizontal") {
      legendOrientation = (atLeft || atRight) ? "vertical" : "horizontal";
    }
    var legendBand = !hasLegend ? 8 : legendOrientation === "vertical" ?
      Math.min(Math.max(84, height * 0.30), Math.max(84, height * 0.48)) :
      Math.max(30, Number(legend.font_size || 11) + 16);
    var legendTitleBand = hasLegend && legendTitle ? Math.max(20,
      Number(typography.legend_title_size || legend.title_font_size || 11) + 8) : 0;
    var sideLegend = atLeft || atRight;
    // Width a vertical left/right legend column occupies, so the plot
    // grid can leave room for it instead of sitting underneath it.
    var sideLegendWidth = (sideLegend && legendOrientation === "vertical") ?
      Math.max(74, Math.min(150, width * 0.28)) : 0;
    var sideLegendGutter = sideLegendWidth ? sideLegendWidth + 18 : 0;
    var autoValueColor = contrastColor(root, display.text_color || "#111827", display.theme_mode);
    var axisInk = configuredColor(typography.axis_tick_color,
      contrastColor(root, display.text_color || "#111827", display.theme_mode));
    var axisTitleInk = configuredColor(typography.axis_title_color, axisInk);
    var headerInk = configuredColor(typography.header_color, axisInk);
    var oldDefaultLabelColor = ["#fff", "#ffffff", "#e2e8f0"].indexOf(
      String((labels || {}).color || "").toLowerCase()) >= 0;
    var valueLabelColor = configuredColor(typography.value_label_color,
      oldDefaultLabelColor ? autoValueColor : configuredColor((labels || {}).color, autoValueColor));
    var tooltipColor = configuredColor(typography.tooltip_color, autoValueColor);
    var tooltipBackground = autoValueColor === "#111827" ? "rgba(255,255,255,.96)" : "rgba(15,23,42,.96)";
    // Legend ink mirrors theme_figure (chart_display._LIGHT_TEXT_REMAP): a
    // stored DARK default is rewritten for a light card, a colour the user
    // picked survives untouched, and "auto"/unset falls back to the ink
    // computed for the card. autoValueColor is "#111827" exactly when the
    // card under the chart reads light (app light mode or a light export
    // board), so the same rule holds on screen and in exports.
    var legendLightRemap = {"#f8fafc": "#1e293b", "#f1f5f9": "#1e293b", "#e2e8f0": "#334155",
      "#cbd5e1": "#334155", "#94a3b8": "#475569", "#64748b": "#475569",
      "#f5f7ff": "#0f172a", "#ffffff": "#1e293b", "#fff": "#1e293b",
      "white": "#1e293b"};
    var themedLegendColor = function (value, fallback) {
      var ink = String(value || "").trim();
      if (!ink || ink.toLowerCase() === "auto") return fallback;
      return autoValueColor === "#111827" ? (legendLightRemap[ink.toLowerCase()] || ink) : ink;
    };
    var legendItemColor = themedLegendColor(typography.legend_item_color,
      themedLegendColor(legend.item_color, autoValueColor));
    var legendTitleColor = themedLegendColor(typography.legend_title_color,
      themedLegendColor(legend.title_color, legendItemColor));
    var legendTitleWidth = legendTitle && !sideLegend ?
      Math.min(Math.max(42, legendTitle.length * Number(legend.font_size || 11) * 0.62 + 8),
        Math.max(42, width * 0.42)) : 0;
    var isRose = model.chart_type === "nightingale_rose";
    var isPie = model.chart_type === "pie_chart" || model.chart_type === "pie" || isRose;
    var isSunburst = model.chart_type === "sunburst";
    var isSankey = model.chart_type === "sankey";
    var isCalendar = model.chart_type === "calendar_heatmap";
    var isWaterfall = model.chart_type === "waterfall";
    var isFunnel = model.chart_type === "funnel";
    var isGlobeMap = false;
    var isCategorical = model.chart_type === "categorical" || model.chart_type === "bar";
    var isScatter = model.chart_type === "scatter_plot" || model.chart_type === "scatter";
    var isHeatmap = model.chart_type === "matrix_heatmap" || model.chart_type === "heatmap";
    var isMap = (model.chart_type === "map_plot" || isGlobeMap) && model.data && model.data.map &&
      !!model.data.map.asset;
    var satellite = isMap && String(display.map_basemap || "").toLowerCase() === "satellite";
    var color = display.palette || display.colors;
    var option = {
      backgroundColor: display.chart_background || "transparent",
      animation: width * height < 1000000,
      textStyle: fontStyle(typography, "body", typography.axis_tick_size,
        axisInk),
      color: Array.isArray(color) ? color : undefined,
      title: titleText ? {
        text: titleText,
        left: String(display.title_align || typography.header_align || "left").toLowerCase(),
        top: topTitle ? 4 : undefined,
        bottom: topTitle ? undefined : (hasLegend && !atTop ? legendBand + 2 : 4),
        width: Math.max(80, width - 148),
        overflow: "truncate",
        textStyle: Object.assign(fontStyle(typography, "header",
          typography.header_size || display.title_size,
          headerInk), {overflow: "truncate"}),
        padding: [4, 8]
      } : undefined,
      legend: hasLegend ? {
        type: "scroll",
        orient: legendOrientation,
        left: (atLeft || legendPos.indexOf("left") >= 0) ? 6 :
          atCenter ? "center" : (atEnd || atRight) ? undefined : 8 + legendTitleWidth,
        right: (atEnd || atRight) ? 6 : undefined,
        top: atTop ? titleBand + legendTitleBand + 2 : sideLegend ? "middle" : undefined,
        bottom: atBottom ? (titleText && !topTitle ? titleBand + 2 : 2) : undefined,
        width: sideLegend ? Math.max(74, Math.min(150, width * 0.28)) :
          Math.max(40, width - legendTitleWidth - 16),
        height: sideLegend ? Math.max(40, height - titleBand - 12) : legendBand,
        pageIconSize: 9,
        itemWidth: 10,
        itemHeight: 10,
        itemGap: 10,
        textStyle: fontStyle(typography, "body",
          typography.legend_item_size || legend.font_size,
          legendItemColor),
        selectedMode: (legend.click === false || layout.legend_itemclick === "false" || layout.legend_itemclick === "none") ? false :
          (layout.legend_itemclick === "toggleothers" || legend.click === "single") ? "single" : true,
        borderColor: layout.show_border ? (display.text_color || "#64748b") : "transparent",
        borderWidth: layout.show_border ? 1 : 0,
        padding: layout.show_border ? 6 : 2
      } : {show: false},
      tooltip: {trigger: isPie || isScatter || isHeatmap || isMap ? "item" : "axis", confine: true,
        backgroundColor: tooltipBackground, borderColor: autoValueColor === "#111827" ? "#cbd5e1" : "#475569",
        textStyle: fontStyle(typography, "body", typography.tooltip_size || 12, tooltipColor)},
      aria: {enabled: true},
      series: []
    };
    if (hasLegend && legendTitle) {
      // The title always sits directly ABOVE the legend category list, for
      // horizontal and vertical legends alike (left / right / top / bottom).
      var sideTop = (titleText && !topTitle) ? 4 : 8;
      var legendTitleTop = sideLegend ? (atTop ? titleBand + 3 : sideTop)
        : (atTop ? titleBand + 3
          : atBottom ? height - legendBand - legendTitleBand - 2
          : sideTop);
      // style.x/style.y + textAlign are the only reliable graphic-text
      // anchors (see the colour-bar title fix) -- style.align is ignored.
      var titleX, titleAlign;
      if (atCenter) { titleX = width / 2; titleAlign = "center"; }
      else if (atEnd || atRight) { titleX = width - 8; titleAlign = "right"; }
      else { titleX = 8; titleAlign = "start"; }
      pushGraphic(option, {type: "text",
        silent: true, style: {text: legendTitle,
          x: titleX,
          y: legendTitleTop + Math.max(10, legendTitleBand / 2),
          textAlign: titleAlign,
          textVerticalAlign: "middle",
          fill: legendTitleColor,
          font: (String(typography.font_style || "Normal").toLowerCase().indexOf("italic") >= 0 ? "italic " : "") +
            (String(typography.font_style || "Normal").toLowerCase().indexOf("bold") >= 0 ? "700 " : "400 ") +
            Number(typography.legend_title_size || legend.title_font_size || legend.font_size || 11) + "px \"" +
            String(typography.family || "Inter") + "\""}});
      if (sideLegend) {
        // Start the category list below its title instead of underneath it.
        option.legend.top = legendTitleTop + legendTitleBand;
        option.legend.bottom = 4;
        option.legend.height = Math.max(36, height - option.legend.top - 10);
      }
    }
    if (option.legend && layout.legend_labels && typeof layout.legend_labels === "object") {
      option.legend.formatter = function (name) {
        return String(layout.legend_labels[name] || name);
      };
    }
    var series = (model.data && model.data.series) || [];

    if (isSunburst) {
      var tree = model.data && model.data.tree || [];
      var sunburstAvailable = Math.max(80, Math.min(width, height - (topTitle ? titleBand : 8)));
      var sunburstRadius = Math.max(32, Math.min(92, 88 * sunburstAvailable / Math.max(1, Math.min(width, height))));
      var sunburstCenterY = Math.min(58, 50 + (topTitle && titleText ? titleBand * 34 / Math.max(1, height) : 0));
      option.tooltip.trigger = "item";
      option.series = [{type: "sunburst", data: tree,
        center: ["50%", sunburstCenterY + "%"],
        radius: [Number(display.sunburst_inner_radius || 0) + "%", sunburstRadius + "%"],
        sort: display.sunburst_sort === "desc" ? "desc" : undefined,
        nodeClick: display.sunburst_node_click || "rootToNode",
        emphasis: {focus: "ancestor"},
        label: Object.assign(fontStyle(typography, "body",
          typography.axis_tick_size || 11, valueLabelColor),
          {rotate: display.sunburst_label_rotate || "radial", minAngle: 4}),
        levels: [{}, {itemStyle: {borderWidth: 2, borderColor: option.backgroundColor || "#fff"},
          label: {rotate: "tangential"}}, {itemStyle: {borderWidth: 1,
          borderColor: option.backgroundColor || "#fff"}}]}];
    } else if (isSankey) {
      var flow = model.data && model.data.sankey || {};
      option.tooltip.trigger = "item";
      option.series = [{type: "sankey", data: flow.nodes || [], links: flow.links || [],
        orient: display.sankey_orient || "horizontal",
        nodeAlign: display.sankey_align || "justify",
        nodeWidth: Math.max(8, Number(display.sankey_node_width || 18)),
        nodeGap: Math.max(4, Number(display.sankey_node_gap || 10)),
        layoutIterations: Math.max(0, Math.min(64, Number(display.sankey_iterations || 32))),
        draggable: display.sankey_draggable !== false,
        label: Object.assign(fontStyle(typography, "body",
          typography.axis_tick_size || 11, valueLabelColor), {position: "right"}),
        lineStyle: {color: "gradient", curveness: 0.5},
        emphasis: {focus: "adjacency"}}];
    } else if (isCalendar) {
      var calendar = model.data && model.data.calendar || {};
      var values = calendar.values || [];
      var dates = values.map(function (point) { return cleanNumber(point[1]); })
        .filter(function (value) { return value !== null; });
      var dateMin = dates.length ? Math.min.apply(null, dates) : 0;
      var dateMax = dates.length ? Math.max.apply(null, dates) : 1;
      if (dateMax <= dateMin) dateMax = dateMin + 1;
      option.tooltip.trigger = "item";
      option.calendar = {top: (topTitle ? titleBand : 12) + 8,
        left: 48, right: 24, bottom: 24, range: calendar.range || [],
        cellSize: display.calendar_cell_size || ["auto", 18],
        orient: display.calendar_orient || "horizontal",
        yearLabel: {show: display.calendar_year_label !== false,
          color: axisInk},
        monthLabel: {color: axisInk,
          fontSize: Number(typography.axis_tick_size || 11)},
        dayLabel: {firstDay: 1, color: axisInk,
          fontSize: Number(typography.axis_tick_size || 10)}};
      option.visualMap = {min: dateMin, max: dateMax, calculable: false,
        orient: "horizontal", left: "center", bottom: 0,
        inRange: {color: display.heatmap_colors || ["#e2e8f0", "#2563eb"]},
        show: display.show_colorbar !== false,
        textStyle: fontStyle(typography, "body", typography.axis_tick_size || 10, valueLabelColor)};
      option.series = [{type: "heatmap", coordinateSystem: "calendar",
        data: values.map(function (point) { return [point[0], cleanNumber(point[1])]; })
          .filter(function (point) { return point[1] !== null; })}];
    } else if (isWaterfall) {
      var water = (series[0] && series[0].values || []).map(function (pair) {
        return [String(pair[0]), cleanNumber(pair[1])];
      }).filter(function (pair) { return pair[1] !== null; });
      var running = Number(display.waterfall_start || 0), base = [], rise = [], fall = [], total = [];
      water.forEach(function (pair, index) {
        var name = pair[0], value = pair[1], isTotal = (model.data.waterfall_totals || []).indexOf(name) >= 0;
        if (isTotal) { base.push(0); rise.push(0); fall.push(0); total.push(value); running = value; }
        else if (value >= 0) { base.push(running); rise.push(value); fall.push(0); total.push(0); running += value; }
        else { base.push(running + value); rise.push(0); fall.push(-value); total.push(0); running += value; }
      });
      option.legend = {show: display.legend && display.legend.show !== false,
        data: ["Increase", "Decrease", "Total"], left: "center", bottom: 2,
        textStyle: fontStyle(typography, "body", typography.legend_item_size || 11, legendItemColor)};
      option.grid = {left: 58, right: 22, top: titleBand + 8,
        bottom: Math.max(legendBand, 28) + (!topTitle ? titleBand : 8), containLabel: true};
      if (sideLegendGutter) {
        if (atLeft) option.grid.left += sideLegendGutter; else option.grid.right += sideLegendGutter;
      }
      option.xAxis = {type: "category", data: water.map(function (pair) { return pair[0]; }),
        axisLabel: Object.assign(fontStyle(typography, "body", typography.axis_tick_size || 11, valueLabelColor), {hideOverlap: true})};
      option.yAxis = {type: "value", axisLabel: fontStyle(typography, "body", typography.axis_tick_size || 11, valueLabelColor),
        splitLine: {show: layout.show_grid !== false}};
      option.series = [
        {name: "Base", type: "bar", stack: "waterfall", itemStyle: {color: "transparent", borderColor: "transparent"}, emphasis: {itemStyle: {color: "transparent"}}, data: base},
        {name: "Increase", type: "bar", stack: "waterfall", itemStyle: {color: display.increase_color || "#10b981"}, data: rise},
        {name: "Decrease", type: "bar", stack: "waterfall", itemStyle: {color: display.decrease_color || "#ef4444"}, data: fall},
        {name: "Total", type: "bar", itemStyle: {color: display.total_color || "#6366f1"}, data: total}
      ];
    } else if (isFunnel) {
      var funnelData = (series[0] && series[0].values || []).map(function (pair) {
        return {name: String(pair[0]), value: cleanNumber(pair[1])};
      }).filter(function (point) { return point.value !== null; });
      option.tooltip.trigger = "item";
      option.series = [{type: "funnel", data: funnelData,
        sort: display.funnel_sort || "descending", orient: display.funnel_orient || "vertical",
        left: "8%", top: titleBand + 8, bottom: 12, width: "84%",
        minSize: String(display.funnel_min_size || 0) + "%",
        maxSize: String(display.funnel_max_size || 100) + "%",
        gap: Math.max(0, Number(display.funnel_gap || 2)),
        label: Object.assign(fontStyle(typography, "body", typography.axis_tick_size || 11, valueLabelColor),
          {show: labels.show !== false, position: display.funnel_label_position || "inside", formatter: "{b}: {c}"}),
        labelLine: {show: display.funnel_label_position === "outside"},
        itemStyle: {borderColor: display.funnel_border_color || "#fff", borderWidth: Number(display.funnel_border_width || 1)},
        emphasis: {label: {fontWeight: "bold"}}}];
    } else if (isMap) {
      var mapMeta = model.data.map || {};
      var mapDisplay = display;
      var mapSeries = series[0] || {values: []};
      var isChoropleth = mapMeta.mode === "choropleth";
      var isDensityMap = mapMeta.mode === "density";
      var mapValues = (mapSeries.values || []).map(function (item) {
        if (item && !Array.isArray(item)) return item;
        return {name: String(item[0]), value: cleanNumber(item[1]),
          displayName: item[2] || String(item[0]), details: item[3] || {}};
      }).filter(function (item) { return item.value !== null; });
      var mapCenter = Array.isArray(mapDisplay.map_center) ? mapDisplay.map_center : [0, 0];
      var mapZoom = Number(mapDisplay.map_zoom || 1);
      option.tooltip = {trigger: "item", confine: true,
        backgroundColor: tooltipBackground,
        borderColor: autoValueColor === "#111827" ? "#cbd5e1" : "#475569",
        textStyle: fontStyle(typography, "body", typography.tooltip_size || 12, tooltipColor),
        formatter: function (params) {
        var data = params && params.data || {};
        var details = data.details || {};
        var parts = ["<strong>" + escapeHtml(data.displayName || params.name) + "</strong>"];
        if (data.value != null) parts.push(escapeHtml(mapDisplay.map_value_title || "Value") + ": " + escapeHtml(data.value));
        Object.keys(details).forEach(function (key) {
          if (details[key] != null) parts.push(escapeHtml(key) + ": " + escapeHtml(details[key]));
        });
        return parts.join("<br>");
      }};
      var colorbarPosition = String(mapDisplay.colorbar_position || "right").toLowerCase();
      var colorbarOrientation = String(mapDisplay.colorbar_orientation || "auto").toLowerCase();
      var colorbarVertical = colorbarOrientation === "vertical" ||
        (colorbarOrientation === "auto" &&
          (colorbarPosition === "left" || colorbarPosition === "right"));
      var mapMin = mapDisplay.heatmap_min == null ? 0 : Number(mapDisplay.heatmap_min);
      var mapMax = mapDisplay.heatmap_max == null ? 1 : Number(mapDisplay.heatmap_max);
      option.visualMap = {type: "continuous", min: mapMin,
        max: mapMax, calculable: false,
        orient: colorbarVertical ? "vertical" : "horizontal",
        left: colorbarVertical ? (colorbarPosition === "left" ? 8 : undefined) : "center",
        right: colorbarVertical && colorbarPosition === "right" ? 8 : undefined,
        top: colorbarVertical ? Math.max(44, titleBand + 4) :
          (colorbarPosition === "top" ? Math.max(4, titleBand) : undefined),
        bottom: !colorbarVertical && colorbarPosition !== "top" ? 6 : undefined,
        itemWidth: colorbarVertical ? 12 : undefined,
        itemHeight: colorbarVertical ? Math.max(70, height * 0.58) : 10,
        name: mapDisplay.colorbar_title || "",
        nameLocation: "middle", nameGap: Number(mapDisplay.colorbar_title_size || 11) + 8,
        text: [String(mapMax), String(mapMin)],
        textStyle: fontStyle(typography, "body", mapDisplay.colorbar_tick_size || 10,
          mapDisplay.colorbar_tick_color || mapDisplay.text_color || "#cbd5e1"),
        nameTextStyle: fontStyle(typography, "body", mapDisplay.colorbar_title_size || 11,
          mapDisplay.colorbar_title_color || mapDisplay.text_color || "#cbd5e1"),
        borderColor: mapDisplay.colorbar_border ? "rgba(100,116,139,.55)" : "transparent",
        borderWidth: mapDisplay.colorbar_border ? 1 : 0,
        show: mapDisplay.show_colorbar !== false,
        inRange: {color: mapDisplay.heatmap_colors || ["#eff6ff", "#1d4ed8"]}};
      option.visualMap.show = mapDisplay.show_colorbar !== false &&
        (isChoropleth || isDensityMap || mapDisplay.map_numeric_color === true);
      if (isChoropleth) {
        // Keep the choropleth shapes transparent over satellite tiles. A
        // visualMap would otherwise paint opaque fills over the imagery.
        if (satellite) option.visualMap = undefined;
        option.series = [{id: mapSeries.id || "choropleth", name: mapSeries.name || model.title,
          type: "map", map: mapMeta.id, nameProperty: "name", data: mapValues,
          center: mapCenter, zoom: mapZoom, roam: true, scaleLimit: {min: 1, max: 12},
          projection: mapProjection(satellite ? "mercator" : mapDisplay.map_projection),
          layoutCenter: ["48%", "54%"], layoutSize: "92%",
          itemStyle: {areaColor: satellite ? "rgba(0,0,0,0)" : (mapDisplay.map_land_color || "#1e293b"),
            borderColor: mapDisplay.map_borders ? (mapDisplay.map_border_color || "rgba(203,213,225,.68)") : "transparent",
            borderWidth: mapDisplay.map_borders ? 0.65 : 0},
          emphasis: {label: {show: false}, itemStyle: {areaColor: "#818cf8"}},
          select: {disabled: true}, label: {show: false}}];
      } else {
        option.geo = {map: mapMeta.id, roam: true, center: mapCenter, zoom: mapZoom,
          scaleLimit: {min: 1, max: 12}, projection: mapProjection(satellite ? "mercator" : mapDisplay.map_projection),
          layoutCenter: ["50%", "54%"], layoutSize: "92%",
          itemStyle: {areaColor: satellite ? "rgba(0,0,0,0)" : (mapDisplay.map_land_color || "#e8edf4"),
            borderColor: mapDisplay.map_borders ? (mapDisplay.map_border_color || "#9aa8ba") : "transparent",
            borderWidth: mapDisplay.map_borders ? 0.55 : 0},
          emphasis: {itemStyle: {areaColor: mapDisplay.map_emphasis_color || "#cbd5e1"}}};
        // A continuous visualMap bound to dimension 2 turns points whose
        // third value is null into fill="none" -- the whole scatter vanishes
        // even with show:false. Bind it only when the model really carries a
        // numeric colour dimension (or this is a density grid, whose cells
        // always carry a count); otherwise drop it and let itemStyle paint.
        if (isDensityMap || mapDisplay.map_numeric_color === true) {
          option.visualMap.dimension = 2;
          option.visualMap.seriesIndex = 0;
          // Belt and braces: an out-of-range colour so null cells paint gray
          // instead of disappearing.
          option.visualMap.outOfRange = {color: ["#64748b"]};
        } else {
          option.visualMap = undefined;
        }
        if (isDensityMap) {
          var densityValues = (mapSeries.values || []).map(function (item) {
            return {name: item.name || "Density", value: item.value,
              details: item.details || {}, displayName: item.displayName || item.name};
          });
          option.series = [{id: mapSeries.id || "map-density", name: mapSeries.name || "Density",
            // Geo heatmap layers have inconsistent support across the bundled
            // ECharts builds. Render the bounded density grid as a graduated
            // geo scatter instead: every source row still contributes, the
            // visualMap colors each cell, and the circles remain visible over
            // both local boundaries and satellite imagery.
            type: "scatter", coordinateSystem: "geo", data: densityValues,
            symbol: "circle",
            symbolSize: function (item) {
              var rawValue = Array.isArray(item) ? item[2] :
                (item && item.value && item.value[2]);
              var value = Number(rawValue);
              var min = Number(mapDisplay.heatmap_min || 0);
              var max = Number(mapDisplay.heatmap_max || 1);
              var ratio = Number.isFinite(value) && max > min ?
                Math.max(0, Math.min(1, (value - min) / (max - min))) : 0.25;
              return Math.max(7, Math.min(30, 7 + Math.sqrt(ratio) * 23));
            },
            itemStyle: {opacity: Number(mapDisplay.map_marker_opacity || 0.82),
              borderColor: "rgba(255,255,255,.45)", borderWidth: 0.5},
            encode: {value: 2}, large: true, largeThreshold: 2000,
            progressive: 4000, animation: false}];
        } else {
          option.series = series.map(function (s, index) {
            return {id: s.id || "map-points-" + index, name: s.name || "Locations",
              type: "scatter", coordinateSystem: "geo",
              data: (s.values || []).map(function (point) {
                var item = point && !Array.isArray(point) ? point : {};
                return {name: item.name || "Location", displayName: item.displayName || item.name || "Location",
                  value: item.value || [], details: item.details || {},
                  itemStyle: item.itemColor ? {color: item.itemColor} : undefined};
              }),
              symbolSize: function (params) {
                if (mapDisplay.map_size_by_value !== true) {
                  return Math.max(2, Number(mapDisplay.map_marker_size || 6));
                }
                var raw = params && params.value && Number(params.value[3]);
                var min = Number(mapDisplay.map_marker_min || 4), max = Number(mapDisplay.map_marker_max || 22);
                if (!Number.isFinite(raw)) return Math.max(min, 8);
                return Math.max(min, Math.min(max, raw));
              },
              itemStyle: {opacity: Number(mapDisplay.map_marker_opacity || 0.82)},
              encode: {value: 2}, large: true, largeThreshold: 2000,
              progressive: 3000, progressiveThreshold: 5000};
          });
        }
      }
    } else if (isPie) {
      var source = series[0] || {values: []};
      var pieData = (source.values || []).map(function (pair) {
        return {name: String(pair[0] == null ? "" : pair[0]), value: cleanNumber(pair[1]),
          selected: Number(display.pull_slices || 0) > 0};
      }).filter(function (point) { return point.value !== null; });
      if (option.legend && option.legend.show !== false) {
        option.legend.data = orderedNames(pieData.map(function (point) { return point.name; }), layout.legend_order);
      }
      // Fit the pie in the measured space left after the title and legend.
      // Fixed 33%/67% centers and a 48px minimum diameter pushed small charts
      // under their headers and left a large empty band beside side legends.
      if (sideLegend && width < 300) {
        // Preserve the user's requested position in the recipe. Temporarily
        // move a side legend below the plot when a compact card cannot support
        // two usable columns.
        sideLegend = false;
        atLeft = false; atRight = false; atTop = false; atBottom = true;
        legendOrientation = "horizontal";
        option.legend.orient = "horizontal";
        option.legend.left = "center";
        option.legend.right = undefined;
        option.legend.top = undefined;
        option.legend.bottom = 2;
        option.legend.width = Math.max(32, width - 16);
        option.legend.height = Math.max(24, Number(legend.font_size || 11) + 14);
        // The shared legend-title graphic was initially positioned beside a
        // vertical legend. Move it above the compact horizontal legend so it
        // does not sit over the pie after the responsive fallback.
        if (legendTitle && Array.isArray(option.graphic) && option.graphic.length) {
          option.graphic[0].style.x = width / 2;
          option.graphic[0].style.textAlign = "center";
          option.graphic[0].style.y = Math.max(6, height - legendBand -
            (titleText && !topTitle ? titleBand : 0) - legendTitleBand / 2 - 2);
        }
      }
      var pieLegendWidth = sideLegend && hasLegend ?
        Math.min(180, Math.max(76, width * 0.30)) : 0;
      var pieTop = (topTitle && titleText ? titleBand + 5 : 8) +
        (atTop && hasLegend ? legendBand + legendTitleBand + 4 : 0);
      var pieBottom = (!topTitle && titleText ? titleBand + 5 : 8) +
        (atBottom && hasLegend ? legendBand + 4 : 0);
      var pieLeft = 8, pieRight = Math.max(8, width - 8);
      if (sideLegend && hasLegend) {
        if (atLeft) pieLeft = pieLegendWidth + 14;
        if (atRight) pieRight = Math.max(pieLeft, width - pieLegendWidth - 14);
        option.legend.width = pieLegendWidth;
        option.legend.height = Math.max(24, height - pieTop - pieBottom);
        option.legend.top = pieTop;
        option.legend.bottom = pieBottom;
        option.legend.left = atLeft ? 6 : undefined;
        option.legend.right = atRight ? 6 : undefined;
      }
      var labelGutter = labels.show !== false && labels.position !== "inside" ? 28 : 12;
      var plotWidth = Math.max(0, pieRight - pieLeft - labelGutter);
      var plotHeight = Math.max(0, height - pieTop - pieBottom - labelGutter);
      var diameter = Math.max(0, Math.min(plotWidth, plotHeight));
      var cx = (pieLeft + pieRight) / 2;
      var cy = pieTop + Math.max(0, height - pieTop - pieBottom) / 2;
      var outerRadius = Math.max(0, diameter * 0.46);
      var compactPie = width < 380 || height < 280;
      var pieLabelPosition = compactPie && labels.position !== "inside" ? "inside" :
        (labels.position || "outside");
      option.series = [{
        id: source.id || "series-0", name: source.name || model.title,
        type: "pie", roseType: isRose ? (display.rose_type || "area") : undefined,
        radius: [display.donut ? (Number(display.donut_ratio || 38) + "%") : 0,
          outerRadius],
        center: [cx, cy], data: pieData,
        // boolean true means 'single' in ECharts 6; the Pull option pulls ALL slices.
        selectedMode: Number(display.pull_slices || 0) > 0 ? "multiple" : false,
        selectedOffset: Math.round(outerRadius * Math.max(0, Math.min(0.5,
          Number(display.pull_slices || 0)))),
        startAngle: Number(display.rotation || 0),
        clockwise: display.direction !== "counterclockwise",
        avoidLabelOverlap: true,
        minShowLabelAngle: Number(labels.min_angle || 3),
        labelLayout: {hideOverlap: true, moveOverlap: "shiftY"},
        label: Object.assign(fontStyle(typography, "body", labels.font_size || typography.value_label_size ||
          typography.axis_tick_size || 11, valueLabelColor), {
          show: labels.show !== false, position: pieLabelPosition,
          formatter: labels.formatter === "label+percent" ? "{b}\n{d}%" :
            labels.formatter === "label+value" ? "{b}\n{c}" :
            labels.formatter === "percent" ? "{d}%" :
            labels.formatter === "value" ? "{c}" : "{b}"}),
        labelLine: {show: labels.show !== false && pieLabelPosition === "outside",
          length: compactPie ? 6 : 10, length2: compactPie ? 5 : 8, smooth: 0.15}
      }];
      if (display.donut && display.donut_center_total && outerRadius >= 18) {
        var centerTotal = pieData.reduce(function (sum, point) {
          return sum + (Number.isFinite(point.value) ? point.value : 0);
        }, 0);
        var totalText;
        var absTotal = Math.abs(centerTotal), compactSuffix = "", compactValue = centerTotal;
        if (absTotal >= 1e9) { compactValue = centerTotal / 1e9; compactSuffix = "B"; }
        else if (absTotal >= 1e6) { compactValue = centerTotal / 1e6; compactSuffix = "M"; }
        else if (absTotal >= 1e3) { compactValue = centerTotal / 1e3; compactSuffix = "K"; }
        try {
          totalText = new Intl.NumberFormat(undefined, {
            maximumFractionDigits: compactSuffix ? 2 : Number(display.center_total_precision == null ? 2 : display.center_total_precision)
          }).format(compactValue) + compactSuffix;
        } catch (e) { totalText = String(Math.round(compactValue * 100) / 100) + compactSuffix; }
        // ECharts graphic text honours textAlign/textVerticalAlign only
        // through style.x/style.y; style.align with left/top is silently
        // ignored and left-anchored the total beside the donut. Placing
        // style.x/style.y on the pie centre pins it to the absolute centre
        // no matter where a side legend moved the pie.
        option.graphic = (option.graphic || []).concat([{
          type: "text", z: 20, silent: true,
          style: {text: "Total\n" + totalText,
            x: cx, y: cy,
            textAlign: "center", textVerticalAlign: "middle",
            lineHeight: 18,
            fill: configuredColor(display.center_total_color || typography.pie_center_total_color, autoValueColor),
            font: "600 " + Math.max(8, Math.min(28, Number(display.center_total_size || typography.pie_center_total_size || 12))) + "px " +
              String(typography.family || "Inter")}
        }]);
      }
    } else if (isScatter) {
      var scatterTop = (topTitle ? titleBand : 12) + (atTop && hasLegend ? legendBand + legendTitleBand : 4);
      var scatterBottom = (!atTop && hasLegend ? legendBand + legendTitleBand : 8) + (!topTitle ? titleBand : 8);
      option.grid = {left: 58, right: 20, top: scatterTop, bottom: scatterBottom,
        containLabel: true, show: !!display.show_border,
        borderWidth: display.show_border ? 1 : 0,
        borderColor: display.border_color || "#cbd5e1"};
      if (sideLegendGutter) {
        if (atLeft) option.grid.left += sideLegendGutter; else option.grid.right += sideLegendGutter;
      }
      option.xAxis = {type: "value", name: display.x_title || "", scale: true,
        axisLabel: fontStyle(typography, "body", typography.axis_tick_size, axisInk),
        nameTextStyle: fontStyle(typography, "body", typography.axis_title_size, axisTitleInk),
        splitLine: {show: display.show_grid !== false, lineStyle: {opacity: 0.18, color: axisInk}}};
      option.yAxis = {type: "value", name: display.y_title || "", scale: true,
        axisLabel: fontStyle(typography, "body", typography.axis_tick_size, axisInk),
        nameTextStyle: fontStyle(typography, "body", typography.axis_title_size, axisTitleInk),
        splitLine: {show: display.show_grid !== false, lineStyle: {opacity: 0.18, color: axisInk}}};
      option.series = series.map(function (s, i) {
        if (s.type === "line") {
          var lineStyle = s.style || {};
          // Line shape is the SAME interpolation vocabulary the line charts
          // use (linear / spline / hv / vh / hvh / vhv) -- it drives the
          // trendline's smoothing and step mode. The previous build mapped
          // "diamond"/"triangle" onto the point symbols and "dashed"/
          // "dotted" onto the dash, none of which exist in LINE_SHAPES, so
          // the control looked connected while every chart drew a dashed
          // circle-marked line. trend dash stays dashed unless the series
          // carries its own dash; interpolation is the line_shape's job.
          var trendShape = String(display.line_shape || "linear").toLowerCase();
          var trendStepShapes = ["hv", "vh", "hvh", "vhv"];
          var trendStep = trendStepShapes.indexOf(trendShape) >= 0 ?
            (trendShape === "vh" || trendShape === "vhv" ? "end" :
              (trendShape === "hvh" ? "middle" : "start")) : undefined;
          return {id: s.id || "series-" + i, name: s.name || "Trend",
            type: "line", smooth: trendShape === "spline", step: trendStep,
            data: (s.values || []).map(function (pair) {
              return [cleanNumber(pair[0]), cleanNumber(pair[1])];
            }).filter(function (pair) { return pair[0] !== null && pair[1] !== null; }),
            showSymbol: false, symbol: "none", silent: true,
            lineStyle: {color: lineStyle.color || "#ef4444",
              width: Number(display.line_width || lineStyle.width || 2),
              type: display.line_shape === "dashed" ? "dashed" :
                (display.line_shape === "dotted" ? "dotted" : (lineStyle.dash || "dashed"))},
            areaStyle: display.line_fill && display.line_fill !== "none" ? {opacity: 0.16} : undefined,
            tooltip: {show: false}, z: 4};
        }
        return {id: s.id || "series-" + i, name: s.name || s.id || "Series " + (i + 1),
          // A scatter draws bare points; the marker shape has no dedicated
          // control of its own, so keep the established circle instead of
          // the old dead "diamond"/"triangle" mapping off line_shape.
          type: "scatter", symbol: "circle",
          symbolSize: display.size_by_value ? function (value) {
            return Number(value && value[2] || display.symbol_size || 6);
          } : Number(display.symbol_size || 8),
          itemStyle: {opacity: Number(display.symbol_opacity || 0.8),
            color: (s.style || {}).color},
          large: !!display.large, progressive: display.large ? 4000 : 0,
          data: (s.values || []).map(function (pair) {
            var point = [cleanNumber(pair[0]), cleanNumber(pair[1])];
            if (pair.length > 2) point.push(cleanNumber(pair[2]));
            return point;
          }).filter(function (pair) { return pair[0] !== null && pair[1] !== null; })};
      });
    } else if (isHeatmap) {
      var rows = display.heatmap_rows || [];
      var columns = display.heatmap_columns || [];
      var colorbarPosition = String(display.colorbar_position || "right").toLowerCase();
      var colorbarOrientation = String(display.colorbar_orientation || "auto").toLowerCase();
      var colorbarVertical = colorbarOrientation === "vertical" ||
        (colorbarOrientation === "auto" &&
          (colorbarPosition === "left" || colorbarPosition === "right"));
      var colorbarAtTop = colorbarPosition === "top";
      var colorbarAtLeft = colorbarPosition === "left";
      var colorbarAtRight = colorbarPosition === "right";
      var colorbarSpace = display.show_colorbar === false ? 8 : 54;
      var heatValues = (series[0] && series[0].values) || [];
      var numericValues = heatValues.map(function (point) { return cleanNumber(point[2]); })
        .filter(function (v) { return v !== null; });
      var minValue = numericValues.length ? Math.min.apply(null, numericValues) : 0;
      var maxValue = numericValues.length ? Math.max.apply(null, numericValues) : 1;
      // Typography "Value colour" is the general ink for chart values. A
      // heatmap keeps its data ink inside the cells -- the Layout "Ann
      // color" control owns those -- so Value colour carries the matrix's
      // tick labels. Precedence: an explicitly configured Value colour
      // wins; otherwise the explicitly configured Axis colour; otherwise
      // the auto contrast ink.
      var heatTickInk = configuredColor(typography.value_label_color,
        configuredColor(typography.axis_tick_color, autoValueColor));
      // The Value ink doubles as the fallback for the matrix axis titles --
      // "Value colour" is the chart's value ink (ticks, axis headers, other
      // chart values); an explicitly set axis-title colour still wins.
      var heatAxisTitleInk = configuredColor(typography.axis_title_color, heatTickInk);
      option.grid = {
        left: 76 + (colorbarVertical && colorbarAtLeft ? 54 : 0),
        right: 20 + (colorbarVertical && colorbarAtRight ? 54 : 0),
        top: (topTitle ? titleBand : 12) + 4 +
          (display.show_colorbar !== false && colorbarAtTop && !colorbarVertical ? colorbarSpace : 0),
        bottom: (!topTitle ? titleBand : 8) +
          (display.show_colorbar === false ? 8 : (!colorbarAtTop && !colorbarVertical ? colorbarSpace : 16)),
        containLabel: true};
      option.xAxis = {type: "category", data: columns, name: display.x_title || "",
        splitArea: {show: true}, axisLabel: Object.assign(fontStyle(typography, "body",
          typography.axis_tick_size, heatTickInk), {hideOverlap: true}),
        nameTextStyle: fontStyle(typography, "body", typography.axis_title_size, heatAxisTitleInk)};
      option.yAxis = {type: "category", data: rows, name: display.y_title || "",
        splitArea: {show: true}, axisLabel: Object.assign(fontStyle(typography, "body",
          typography.axis_tick_size, heatTickInk), {hideOverlap: true}),
        nameTextStyle: fontStyle(typography, "body", typography.axis_title_size, heatAxisTitleInk)};
      var colorbarMin = display.heatmap_min == null ? minValue : display.heatmap_min;
      var colorbarMax = display.heatmap_max == null ? maxValue : display.heatmap_max;
      var colorbarTickInk = configuredColor(display.colorbar_tick_color || display.text_color,
        autoValueColor);
      var colorbarTitleInk = configuredColor(display.colorbar_title_color || display.text_color,
        colorbarTickInk);
      // The continuous visualMap draws NO tick labels unless calculable:true
      // (which would add draggable data-filtering handles -- not a display
      // colorbar) or an explicit text pair is given. Supply one.
      // Geometry semantics: vertical -> itemWidth = bar WIDTH,
      // itemHeight = bar LENGTH; horizontal -> itemWidth = bar LENGTH
      // (defaulting to a nearly invisible 20px), itemHeight = thickness.
      var colorbarBarLength = colorbarVertical ? Math.max(90, Math.round(height * 0.62))
        : Math.min(Math.max(160, Math.round(width * 0.36)), 340);
      var colorbarBarThickness = colorbarVertical ? 14 : 12;
      option.visualMap = {
        show: display.show_colorbar !== false,
        type: "continuous",
        dimension: 2, seriesIndex: 0,
        min: colorbarMin,
        max: colorbarMax,
        calculable: false,
        orient: colorbarVertical ? "vertical" : "horizontal",
        left: colorbarVertical ? (colorbarAtLeft ? 4 : undefined) : "center",
        right: colorbarVertical && colorbarAtRight ? 4 : undefined,
        top: colorbarVertical ? "middle" : (colorbarAtTop ? (topTitle ? titleBand + 4 : 8) : undefined),
        bottom: !colorbarVertical && !colorbarAtTop ? 6 : undefined,
        itemWidth: colorbarVertical ? colorbarBarThickness : colorbarBarLength,
        itemHeight: colorbarVertical ? colorbarBarLength : colorbarBarThickness,
        text: [formatCompactNumber(colorbarMax), formatCompactNumber(colorbarMin)],
        textGap: 6,
        inRange: display.heatmap_colors ? {color: display.heatmap_colors} : undefined,
        borderColor: display.colorbar_border ? "#94a3b8" : "transparent",
        borderWidth: display.colorbar_border ? 1 : 0,
        textStyle: fontStyle(typography, "body", display.colorbar_tick_size || 10,
          colorbarTickInk)};
      // The colour-bar title is drawn by the host, not by visualMap: the
      // continuous visualMap silently drops its `name` on some builds, which
      // is why a custom title never appeared.
      var colorbarTitle = String(display.colorbar_title || "").trim();
      if (display.show_colorbar !== false && colorbarTitle) {
        var cbTitleSize = Number(display.colorbar_title_size || 11);
        var cbTitleFont = (String(typography.font_style || "Normal").toLowerCase().indexOf("italic") >= 0 ? "italic " : "") +
          (String(typography.font_style || "Normal").toLowerCase().indexOf("bold") >= 0 ? "700 " : "400 ") +
          cbTitleSize + "px \"" + String(typography.family || "Inter") + "\"";
        // ECharts graphic text only honours textAlign/textVerticalAlign via
        // style.x/style.y; style.align with left/right/top clips the title
        // past the canvas edge. Anchor deterministically instead.
        var cbTitleItem = {type: "text", silent: true, style: {
          text: colorbarTitle,
          fill: colorbarTitleInk, font: cbTitleFont,
          textAlign: "center", textVerticalAlign: "middle"}};
        if (colorbarVertical) {
          var barTop = Math.round((height - colorbarBarLength) / 2);
          cbTitleItem.style.y = Math.max(2, barTop - cbTitleSize - 4) + cbTitleSize / 2;
          if (colorbarAtRight) { cbTitleItem.style.x = width - 2; cbTitleItem.style.textAlign = "right"; }
          else if (colorbarAtLeft) { cbTitleItem.style.x = 2; cbTitleItem.style.textAlign = "start"; }
          else { cbTitleItem.style.x = width / 2; }
        } else if (colorbarAtTop) {
          // Just below the bar, inside the reserved colour-bar space.
          cbTitleItem.style.x = width / 2;
          cbTitleItem.style.y = (topTitle ? titleBand + 4 : 8) + colorbarBarThickness + 3 + cbTitleSize / 2;
        } else {
          // Just above the bottom bar, inside the reserved colour-bar space.
          cbTitleItem.style.x = width / 2;
          cbTitleItem.style.y = height - 6 - colorbarBarThickness - 4 - cbTitleSize / 2;
        }
        pushGraphic(option, cbTitleItem);
      }
      option.series = [{id: (series[0] && series[0].id) || "heatmap",
        name: (series[0] && series[0].name) || "Value", type: "heatmap",
        data: heatValues.map(function (point) {
          return [columns.indexOf(point[0]), rows.indexOf(point[1]), cleanNumber(point[2])];
        }).filter(function (point) { return point[0] >= 0 && point[1] >= 0 && point[2] !== null; }),
        label: Object.assign(fontStyle(typography, "body", labels.font_size || typography.value_label_size || 10,
          configuredColor(labels.color, autoValueColor)), {
          show: labels.show !== false && width > 420 && height > 300,
          formatter: function (params) {
            var value = params.value && params.value[2];
            var precision = Number.isFinite(Number(labels.precision)) ?
              Math.max(0, Math.min(10, Number(labels.precision))) : 2;
            return value == null ? "" : Number(value).toFixed(precision);
          }}),
        emphasis: {itemStyle: {shadowBlur: 10, shadowColor: "rgba(0,0,0,.35)"}}}];
    } else {
      var categories = [];
      series.forEach(function (s) {
        (s.values || []).forEach(function (pair) {
          var category = String(pair[0] == null ? "" : pair[0]);
          if (categories.indexOf(category) < 0) categories.push(category);
        });
      });
      var horizontal = display.orientation === "horizontal";
      var gridTop = (topTitle ? titleBand : 12) + (atTop && hasLegend ? legendBand + legendTitleBand : 4);
      var gridBottom = (!atTop && hasLegend ? legendBand + legendTitleBand : 8) + (!topTitle ? titleBand : 8);
      var axisColor = axisInk;
      var axisLabelStyle = Object.assign(fontStyle(typography, "body",
        typography.axis_tick_size, axisColor), {hideOverlap: true});
      var showBorder = display.show_border !== undefined ? !!display.show_border : !!layout.show_border;
      option.grid = {
        show: showBorder,
        borderWidth: showBorder ? 1 : 0,
        borderColor: configuredColor(display.border_color || layout.border_color, "#cbd5e1"),
        left: horizontal ? 68 : 54, right: 22, top: gridTop,
        bottom: gridBottom, containLabel: true};
      var dualXAxis = display.dual_axis && display.dual_axis_orientation === "x";
      var dualYAxis = display.dual_axis && !dualXAxis;
      var primaryXAxis = horizontal ? {type: "value", name: display.x_title || ""} :
        {type: "category", data: categories, name: display.x_title || "", axisLabel: axisLabelStyle};
      var secondaryXAxis = {type: "value", name: display.x2_title || "",
        position: "top", scale: true, axisLabel: axisLabelStyle,
        splitLine: {show: false}};
      option.xAxis = dualXAxis ? [primaryXAxis, secondaryXAxis] : primaryXAxis;
      var primaryYAxis = horizontal ? {type: "category", data: categories, inverse: true,
        name: display.y_title || "", axisLabel: axisLabelStyle} :
        {type: "value", name: display.y_title || "", scale: true, axisLabel: axisLabelStyle};
      var secondaryYAxis = {type: "value", name: display.y2_title || "",
        position: "right", scale: true, axisLabel: axisLabelStyle,
        splitLine: {show: false}};
      option.yAxis = dualYAxis ? [primaryYAxis, secondaryYAxis] : primaryYAxis;
      if (dualYAxis) option.grid.right = Math.max(option.grid.right, 64);
      if (dualXAxis) option.grid.top = gridTop + 24;
      if (sideLegendGutter) {
        if (atLeft) option.grid.left += sideLegendGutter; else option.grid.right += sideLegendGutter;
      }
      var showGrid = (display.show_grid !== undefined) ? !!display.show_grid : (layout.show_grid !== false);
      [].concat(option.xAxis, option.yAxis).forEach(function (axis) {
        axis.nameTextStyle = fontStyle(typography, "body",
          typography.axis_title_size, axisTitleInk);
        axis.axisLine = {lineStyle: {color: axisColor}};
        axis.splitLine = {show: showGrid && axis !== secondaryYAxis && axis.type === "value",
          lineStyle: {color: axisColor, opacity: 0.18}};
      });
      var paletteColors = (Array.isArray(display.palette) && display.palette.length) ? display.palette :
        (Array.isArray(color) && color.length ? color : []);
      var swatchColor = function (index) {
        return paletteColors.length ? paletteColors[index % paletteColors.length] : null;
      };
      // A LONE bar series colours its bars PER CATEGORY (the established
      // look), so its legend offers one row per category -- each row carries
      // the exact colour of that category's bar as its swatch. Charts with
      // several bar series (Statistical Mean/Min/Max/Std Dev) keep ONE colour
      // per series so every bar matches the swatch on its own series row; a
      // dual-axis overlay line keeps series rows so its click-to-hide works.
      var barSeriesCount = 0;
      series.forEach(function (s) { if (s.type !== "line") barSeriesCount += 1; });
      var categoryLegend = isCategorical && barSeriesCount === 1 && categories.length > 0;
      option.series = series.map(function (s, i) {
        var lookup = Object.create(null);
        var seriesStyle = s.style || {};
        (s.values || []).forEach(function (pair) { lookup[String(pair[0])] = cleanNumber(pair[1]); });
        var isLine = s.type === "line";
        var axisLabelsVisible = Number(s.axis) === 1 ?
          (display.secondary_show_value_labels !== undefined ? !!display.secondary_show_value_labels : labels.show !== false) :
          (display.primary_show_value_labels !== undefined ? !!display.primary_show_value_labels : labels.show !== false);
        var data = categories.map(function (category, pointIndex) {
          var value = lookup[category];
          if (isLine && horizontal && dualXAxis) {
            return [cleanNumber(value), category];
          }
          if (!isCategorical) return value;
          var datum = {name: category, value: value};
          // Bars carry the colour their legend row promises: the category
          // swatch for a lone bar series, the series colour otherwise. Line
          // series keep their lineStyle colour instead.
          var datumColor = isLine ? null : categoryLegend ? swatchColor(pointIndex) :
            (seriesStyle.color || swatchColor(i));
          if (datumColor) datum.itemStyle = {color: datumColor};
          return datum;
        });
        return {id: s.id || "series-" + i, name: s.name || s.id || "Series " + (i + 1),
          type: isLine ? "line" : "bar", smooth: isLine && !!display.smooth,
          xAxisIndex: dualXAxis && Number(s.axis) === 1 ? 1 : 0,
          yAxisIndex: dualYAxis && Number(s.axis) === 1 ? 1 : 0,
          step: isLine && ["hv", "vh", "hvh", "vhv"].indexOf(display.line_shape) >= 0 ?
            (display.line_shape === "vh" || display.line_shape === "vhv" ? "end" :
              display.line_shape === "hvh" ? "middle" : "start") : undefined,
          showSymbol: isLine && display.markers !== false,
          symbolSize: isLine ? Number(display.marker_size || 6) : undefined,
          lineStyle: isLine ? {width: Number(display.line_width || 2),
            color: seriesStyle.color, type: seriesStyle.dash || "solid"} : undefined,
          areaStyle: isLine && display.line_fill && display.line_fill !== "none" ?
            {opacity: 0.22} : undefined,
          stack: !isLine && (display.bar_mode === "stack" || display.bar_mode === "relative") ?
            "lytrize-bars" : (isLine && (display.line_fill === "tonexty" ||
              display.line_fill === "tonextx") ? "lytrize-area" : undefined),
          // The legacy bar-gap control means spacing between category groups.
          // ECharts distinguishes that from barGap between series in a group.
          barCategoryGap: isCategorical ?
            (Math.round(Math.max(0, Math.min(0.9,
              Number(display.bar_gap == null ? 0.28 : display.bar_gap))) * 1000) / 10) + "%" : undefined,
          barGap: display.histogram_overlay || display.bar_mode === "overlay" ?
            "-100%" : undefined,
          itemStyle: !isLine && display.histogram_opacity !== undefined ?
            {opacity: Number(display.histogram_opacity)} : undefined,
          barMaxWidth: isCategorical ? 48 : undefined,
          label: {
            show: axisLabelsVisible,
            position: (labels.position || layout.label_position) === "inside" ? "inside" :
              ((labels.position || layout.label_position) === "outside" ?
                (horizontal ? "right" : "top") : ((labels.position || layout.label_position) || "top")),
            color: valueLabelColor,
            fontSize: Number(labels.font_size || typography.value_label_size || 11),
            fontFamily: String(typography.family || "Inter"),
            fontWeight: String(typography.font_style || "Normal").toLowerCase().indexOf("bold") >= 0 ? 700 : 400,
            fontStyle: String(typography.font_style || "Normal").toLowerCase().indexOf("italic") >= 0 ? "italic" : "normal",
            formatter: function (params) {
              var value = params.value && typeof params.value === "object" ? params.value.value : params.value;
              return value == null ? "" : String(value);
            }
          },
          data: data};
      });
      if (option.legend) {
        if (categoryLegend) {
          // One row per category, in the axis's legend_order, each swatch the
          // exact colour that category's bar uses; a dual-axis overlay line
          // appends its series row so its colour and click-to-hide survive.
          var legendRows = orderedNames(categories, layout.legend_order).map(function (category) {
            var row = {name: category};
            var swatch = swatchColor(categories.indexOf(category));
            if (swatch) row.itemStyle = {color: swatch};
            return row;
          });
          series.forEach(function (s, i) {
            if (s.type !== "line") return;
            var lineRow = {name: String(s.name || s.id || "Series " + (i + 1))};
            var lineSwatch = (s.style || {}).color || swatchColor(i);
            if (lineSwatch) lineRow.itemStyle = {color: lineSwatch};
            legendRows.push(lineRow);
          });
          option.legend.data = legendRows;
        } else {
          var names = series.map(function (s, i) { return String(s.name || s.id || "Series " + (i + 1)); });
          option.legend.data = orderedNames(names, layout.legend_order);
        }
      }
    }

    if (display.empty_message) {
      option.title = {text: String(display.empty_message), left: "center", top: "middle",
        textStyle: {fontSize: 13, color: display.text_color || "#8b93b5", fontWeight: 400}};
      option.series = [];
      option.xAxis = undefined;
      option.yAxis = undefined;
    }
    Object.keys(option).forEach(function (key) { if (option[key] === undefined) delete option[key]; });
    return option;
  }

  function applySatelliteBasemap(chart, model, root) {
    if (!model || model.chart_type !== "map_plot") return;
    var display = model && model.display || {};
    var active = String(display.map_basemap || "").toLowerCase() === "satellite";
    var graphics = [];
    if (active && chart && root && typeof chart.convertToPixel === "function") {
      var center = display.map_center || [0, 0];
      var state = chart.getOption() || {};
      var mapSeries = (state.series || []).find(function (item) { return item.type === "map"; });
      var mapSeriesIndex = (state.series || []).indexOf(mapSeries);
      var coordTarget = mapSeries ? {seriesIndex: mapSeriesIndex} : {geoIndex: 0};
      try { center = chart.convertFromPixel(coordTarget, [root.clientWidth / 2, root.clientHeight / 2]) || center; }
      catch (_) { /* use the configured starting center */ }
      var zoom = Number((mapSeries && mapSeries.zoom) ||
        ((state.geo || [])[0] && (state.geo[0].zoom)) || display.map_zoom || 1);
      var level = Math.max(1, Math.min(13, Math.round(Math.log2(Math.max(1, zoom))) + 2));
      var count = Math.pow(2, level);
      var cx = (Number(center[0]) + 180) / 360 * count;
      var cy = (1 - Math.asinh(Math.tan(Math.max(-85, Math.min(85, Number(center[1]))) * Math.PI / 180)) / Math.PI) / 2 * count;
      var radius = Math.ceil(count / Math.max(2, zoom * 2)) + 1;
      function tileLat(y) { return Math.atan(Math.sinh(Math.PI * (1 - 2 * y / count))) * 180 / Math.PI; }
      for (var y = Math.max(0, Math.floor(cy) - radius); y <= Math.min(count - 1, Math.floor(cy) + radius); y++) {
        for (var x = Math.max(0, Math.floor(cx) - radius); x <= Math.min(count - 1, Math.floor(cx) + radius); x++) {
          var west = x / count * 360 - 180, east = (x + 1) / count * 360 - 180;
          var north = tileLat(y), south = tileLat(y + 1);
          try {
            var nw = chart.convertToPixel(coordTarget, [west, north]);
            var se = chart.convertToPixel(coordTarget, [east, south]);
            if (!nw || !se || !Number.isFinite(nw[0]) || !Number.isFinite(se[1])) continue;
            graphics.push({id: "lytrize-imagery-" + x + "-" + y,
              type: "image", left: Math.min(nw[0], se[0]), top: Math.min(nw[1], se[1]),
              z: -10, silent: true,
              style: {image: "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/" + level + "/" + y + "/" + x,
                width: Math.abs(se[0] - nw[0]), height: Math.abs(se[1] - nw[1])}});
          } catch (_) { /* ignore tiles outside the active map projection */ }
        }
      }
      graphics.push({id: "lytrize-imagery-attribution", type: "text", right: 8, bottom: 5,
        z: 20, silent: true, style: {text: "Powered by Esri | Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community",
          fill: "#fff", backgroundColor: "rgba(0,0,0,.65)", padding: [3, 5],
          font: "11px sans-serif"}});
    }
    if (display.map_notice) {
      graphics.push({id: "lytrize-map-notice", type: "text", left: 8, bottom: 6,
        z: 20, silent: true, style: {text: String(display.map_notice),
          fill: display.text_color || "#fff", backgroundColor: "rgba(0,0,0,.58)",
          padding: [4, 6], font: "12px sans-serif"}});
    }
    if (chart && typeof chart.setOption === "function") {
      try {
        chart.setOption({graphic: graphics}, {replaceMerge: ["graphic"], lazyUpdate: true});
      } catch (e) {
        log.error("Lytrize ECharts adapter: setOption failed: " + e);
      }
    }
  }

  global.LytrizeEChartsAdapter = {buildOption: buildOption, escapeHtml: escapeHtml,
    applySatelliteBasemap: applySatelliteBasemap};
})(window);
