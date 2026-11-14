# Lytrize Production Completion Plan

**Audience:** Codex, Claude Code, Cline, or another coding agent working in this checkout.  
**Goal:** close the original reported defects and the new screenshot defects, verify the running application and export paths, and leave a commit-ready worktree.  
**Priority:** production correctness first; no user questions for routine implementation choices.

## 1. Current situation

The previous file BI_App_Production_Fix_Plan.md has been removed at the user's request. This is its replacement and includes the original issue list, work already present in the tree, the seven supplied screenshots (images 5 and 6 are duplicates), and the remaining release work.

The active architecture is a **Polars dataset → semantic ChartModel → bundled ECharts/custom native renderers** application. Preserve this architecture. Keep full-data aggregation/filtering in Polars, keep browser assets local/offline unless a user explicitly selects the existing satellite imagery option, and maintain old saved recipes.

The worktree already contains many uncommitted changes from earlier work. **Do not reset, restore, stash, overwrite, or stage the worktree wholesale.** Inspect git status --short once at the start, inspect target diffs before edits, make narrow patches, and leave committing to the user.

## 2. What is already in the checkout

These are source changes found during prior implementation. “Present” means code exists; it does **not** mean the behavior has passed production UI acceptance. The screenshots below directly disprove several earlier success claims.

| Area | Work present in source | Current acceptance status |
|---|---|---|
| Selected-chart settings | desktop/pages/analysis_page.py binds settings to chart recipes/metadata. | Re-run select/edit/regenerate/save/reopen against multiple chart UIDs. |
| Advanced forms | desktop/widgets/analysis_settings/advanced.py builds compact family-specific Generate controls. | Sankey Generate currently errors in the user's app (screenshot 5/6); fix and verify each advanced type. |
| Canvas interaction | backend/modules/report_editor.py, backend/modules/canvas_geometry.py, and desktop/pages/dashboard_page.py contain free-canvas movement/resize and geometry handling. | Browser/WebEngine interaction was not visually accepted; repeat move/resize and slicer-preservation checks. |
| Slicers | Native widget types, selection payloads, and geometry namespacing exist in report/editor code. | Verify every widget in the actual dashboard and export. A previous geometry script skipped instance-level coverage. |
| KPI | Polars KPI dispatch and trend/sparkline fields exist in KPI, dashboard, and export paths. | Run actual add/preview/edit/filter/save/export workflow. |
| Shared typography | _auto_color_control has a fixed caption width. | Screenshot 1 still shows Tooltip Color's Auto/swatch controls separated by a large gap. It is not fixed in the visible UI. |
| Distribution/correlation/categorical/pie/time-series/scatter/matrix heatmap | Prior edits cover bins rebuilding, grid/label settings, colorbar mapping, pie settings, scatter options, and renderer fields. | Treat as pending until each control changes the actual rendered output through preview, regeneration, reload, and export. |
| Pivot Table | Light-theme defaults and a Polars-built row hierarchy with native/export expand-collapse were added. | Screenshots 3/4 reveal missing/ineffective typography, useless tooltips, hard-coded cell heat coloring, and missing numeric/currency formatting. Recheck hierarchy and light theme in the UI. |
| Map Plot | Scatter/choropleth model and local Natural Earth assets exist. A local lytrize_map_3d.js canvas renderer plus Globe/Extruded modes were added to Map Plot and export. | Screenshot 7 shows the regular Scatter Map is still blank and its mode/style/render controls are not demonstrably effective. Direct model/option checks are not visual acceptance. Verify regular and 3D modes in the app and exports. |
| Static checks previously run | Compile, git diff --check, 16 settings form checks, and theme/legend option scripts passed in the prior turn. | Re-run after changes. QtWebEngine visual checks were blocked by the environment's Chromium subprocess restriction; do not report them as passing. |

### Screenshot issue inventory

1. **Pie Typography (image 1):** Tooltip Color's Auto control and color swatch remain far apart.
2. **Pie chart (image 2):** center-total text/value is visibly left/up of the donut hole center even though a previous adapter edit attempted to center it.
3. **Pivot Table Typography (image 3):** chart-title Header font/style/alignment controls do not affect the visible table title. Tooltip size/color appear as controls although the native table has no useful chart tooltip. Table currently has no user-selectable cell heatmap range.
4. **Pivot Table/Layout and all charts (image 4):** no usable compact large-number display (K/M/B/T) or explicit currency/locale formatting for monetary values.
5. **Sankey generation (images 5 and 6, same defect):** Generate raises "'list' object has no attribute 'tolist'".
6. **Map Plot (image 7):** chart card remains blank with Scatter (Lat/Lon), valid-looking columns, density rendering, and local style selected. Mode/style/render choices have no visible effect.

## 3. Agent execution contract

1. Start by reading this plan and git status --short. Do not ask the user to repeat requirements or approve routine code changes.
2. For every issue trace **widget → collected config → recipe/model → builder/live rebuild → renderer → save/load/export**. Repair the first broken boundary; do not add a parallel path without retiring the broken active path.
3. Reproduce the screenshot symptoms before editing. For exceptions capture the complete Python traceback and config types. For a blank WebEngine chart capture browser console/runtime errors and the final ChartModel/map registration contract.
4. Preserve recipe compatibility. Read legacy values as fallbacks and write one canonical current value on save.
5. Do not claim success from a model dictionary or generated ECharts option alone. Verify the visible native app and applicable HTML/dashboard export. If this environment blocks QtWebEngine, state that visual verification is blocked and provide reproducible manual steps; never call it a pass.
6. Keep output bounded and aggregation in Polars. Do not convert the full dataset to pandas to work around the Sankey error.
7. Do not add unrelated dependencies, download remote renderers, reformat whole files, edit the old codebase, recreate the deleted backup, or commit/stage changes.
8. Before completion run the gates in Section 7 and report issue IDs, exact checks/results, and remaining blockers.

## 4. Ordered implementation backlog

### P0 — Fix Sankey generation against the active Polars path

**Evidence:** desktop/workers.py::ChartGenerationWorker.run has direct native branches for some families (for example map_plot) but falls through to analysis_service.run_analysis_with_config for Sankey. backend/services/analysis_service.py::build_analysis_config contains pandas-oriented calls including df.columns.tolist(). PolarsDataset.columns is already a Python list. This is a strong candidate for the screenshot error; confirm from the full traceback before changing it.

**Files to trace:** desktop/workers.py, desktop/pages/analysis_page.py, backend/services/analysis_service.py, backend/services/polars_chart_service.py::build_advanced_chart, desktop/widgets/analysis_settings/advanced.py.

**Implement:**

- Capture the full traceback from the screenshot's exact Generate action and identify the first .tolist() call and its receiver type.
- Route Sankey through a native builder using the existing PolarsDataset, following the direct-builder pattern already used by Map Plot. Pass chart_type="sankey" and the collected source_col, target_col, value_col, agg, and layout/typography values; apply display options and return the ChartModel through the normal worker completion signal.
- If a shared analysis-config path must remain, make its column access protocol-aware (PolarsDataset.columns list vs pandas Index) and remove all pandas-only assumptions on the native route. Do not convert the full Polars table to pandas.
- Validate source/target existence, non-equality, null filtering, numeric/count aggregation, empty result, and JSON-safe links. Return a clear user-facing generation error for invalid choices rather than a Python exception dialog.
- Verify saved Sankey recipes still regenerate and that settings are restored on selecting the chart.

**Done when:** the fixture in Section 6 generates a Sankey in the UI with Row Count and numeric Value modes; no .tolist() exception occurs; correct nodes/links render; save/reopen and static/live export work.

### P0 — Repair regular Map Plot rendering and make its controls observable

**Evidence:** screenshot 7's card title is present but the chart body is blank. The existing code has a map-model builder, local GeoJSON, ECharts map registration in desktop/widgets/echarts_web_view.py, and a custom 3D renderer. Earlier verification only established that a semantic model and ECharts option could be produced. It did not establish that the real WebEngine page renders.

**Files to trace:** desktop/widgets/echarts_web_view.py (HTML host, ensureMap, console/errors), backend/assets/echarts/lytrize_adapter.js map branches, backend/services/polars_chart_service.py::build_map_plot_chart / build_choropleth_chart, desktop/widgets/analysis_settings/map_plot.py, backend/modules/echarts_export.py (single-chart and board export).

**Implement in this order:**

1. Reproduce Scatter (Lat/Lon) with the exact dataset/schema used for the screenshot. Inspect model data.map, map asset/id, series value shape, bounds, and ECharts buildOption result. Capture WebEngine JS errors. Check for accidental empty data.map, invalid geo/coordinateSystem, map-registration timing, sizing/resize, or the renderer taking the wrong branch.
2. Make the minimum active-path correction. Keep coordinate points visible over the registered local boundaries. If density aggregation is selected, ensure its generated points and visualMap value dimension are correct. Empty/invalid/out-of-range coordinates must show an explicit empty-state message, not a blank card.
3. Test each visible setting against a visible change: Scatter vs Location Choropleth; density vs sampled points; marker opacity/size; local light vs local dark style; projection/scope for choropleth; color scale and colorbar. The satellite option may request imagery only when explicitly selected; local boundary styles must work offline.
4. Keep Globe/Extruded Map available only if the custom canvas renderer visibly paints the bundled polygons. Verify the Globe rotates and zooms; verify Extruded Map shows polygon side walls and hover values. Check resize, switching to/from ordinary Map Plot, empty map data, and theme changes. Verify the same models in single-chart and dashboard HTML export. Do not imply an ECharts-GL dependency exists.

**Done when:** a nonblank chart is visible in the actual Analysis page for the supplied valid fixture; all advertised map controls visibly work; unsupported/empty inputs give an explicit useful state; desktop, save/reopen, and offline exports agree.

### P0 — Correct Pie/Donut center-total position

**Evidence:** screenshot 2 contradicts the prior adapter's centering attempt. Do not repeat the previous left/top arithmetic without measuring the rendered result.

**Files to trace:** backend/assets/echarts/lytrize_adapter.js Pie branch (series center, legend/title-reserved space, graphic text); backend/services/polars_chart_service.py::apply_pie_display_options; desktop/widgets/analysis_settings/pie_chart.py.

**Implement:**

- Reproduce with the screenshot recipe (donut ratio around 0.46, top-right legend, outside labels, center total enabled) and log the actual pie series center and graphic bounding box in CSS pixels.
- Anchor the center label to the actual rendered pie center using ECharts graphic left/top or a custom series with an explicitly measured bounding rectangle. Account for chart root padding, title, legend position/orientation, outside-label bounds, and resize. Center both label lines within the donut's inner radius; don't center against the overall chart card if the pie is offset by its legend.
- Preserve separate total label and value typography/colors; ensure compact formatting does not change numeric aggregation.
- Check no legend; top/right/left/bottom legend; horizontal/vertical legend; narrow and short cards; resize; dark/light; HTML export.

**Done when:** screenshot coordinates show the visual midpoint of Total + amount coincident with the donut-hole center (within a small pixel tolerance) in native and export renderers.

### P0 — Fix Tooltip Color row spacing, then enforce typography layout across types

**Evidence:** screenshot 1 shows the Tooltip Auto/swatch gap remains after _auto_color_control received a fixed 104 px caption. Inspect the actual widget size policies and parent-column width; the color swatch likely expands into remaining layout space.

**Files:** desktop/widgets/analysis_settings/statistical.py::_auto_color_control, desktop/widgets/analysis_settings/base.py::color_swatch/grid_row, and the actual Typography tab layout.

**Implement:** make the Auto checkbox and color button a compact fixed-size group immediately after the fixed caption; prevent the swatch from receiving expanding horizontal size policy; keep any remaining whitespace after the group. Retain correct Auto enable/disable and color persistence. Inspect the live PySide widget geometry at wide and narrow widths. Verify both Value Color and Tooltip Color rows.

**Done when:** Value/Tooltip labels, Auto chips, and swatches form one compact left-aligned row; changing Auto/custom color changes the intended visible renderer color without moving other controls.

### P0 — Pivot Table typography, dead tooltip settings, and heatmap controls

#### P0.1 Apply Header typography to the visible table title

**Evidence:** screenshot 3 shows hjhg as the table title while Header font/style/alignment choices have no visible effect. Current code sets NativeTableView._title family from table_header_font_family/header_family, size from title_size, bold unconditionally, and color from text_color; it does not apply header_font_style, header_align, or the header_color setting consistently. Table column-header settings (table_header_font_size, table_header_color) are separate Layout settings.

**Files:** desktop/widgets/native_table_view.py::NativeTableView.set_chart_model; backend/services/polars_chart_service.py::build_matrix_table_chart and table apply_chart_model_display_options; desktop/widgets/analysis_settings/statistical.py; desktop/widgets/analysis_settings/matrix_table.py; HTML _table_model_fragment.

**Implement:** define the target clearly:

- Typography Header size/color/family/style/alignment controls the chart title (hjhg).
- Layout Hdr font/Hdr color/Hdr h controls the table's column-header row.
- Carry every control through chart model display/recipe and both native and HTML renderers. Set font weight/style according to the selected style instead of hardcoding bold. Apply horizontal title alignment. Preserve custom colors and light/dark auto colors.

**Done when:** changing each of the five Header Typography properties produces an independently visible, persisted change in the native title and exported table title.

#### P0.2 Remove no-op Pivot Table tooltip settings

The native table has no informative tooltip behavior; screenshot 3 reports only the chart name. Remove Tooltip size and Tooltip color from MatrixTableSettings Typography. Ensure inherited prefill/collect ignores legacy keys safely. Do not remove tooltip settings from chart families whose renderers use them.

**Done when:** Pivot Table Typography contains no dead Tooltip rows and no table hover tooltip merely repeats the chart title.

#### P0.3 Make Pivot Table numeric heat coloring configurable

**Evidence:** screenshot 3/4 shows blue/green conditional cell shading with no user control. desktop/widgets/native_table_view.py::_TableModel.data currently blends each cell from its stripe base to hardcoded #1e40af; HTML table export does not share a configurable matching rule.

**Implement:** add Layout controls for conditional heat coloring (enable/disable) and a selected sequential palette or low/high colors. Store canonical display settings; use them in native table cells and HTML export. Keep header/stripe colors separate. Use the same min/max scale in desktop/export, handle constant/all-null/negative columns, and do not color the overall total column unless the control explicitly includes it. Default behavior should be deliberate and documented in the UI.

**Done when:** palette selection changes cells visibly and consistently in native and HTML views; disabling it restores plain stripe colors; the range is computed from the rendered bounded table values without crashes on constant/null columns.

### P0 — Shared large-number and currency formatting

**Evidence:** screenshot 4 requests compact units for large values across charts and currency with region formatting. The current Pivot Table uses a fixed D3-like table_number_format; the adapter formats the donut center with Intl.NumberFormat, but there is no consistent global format contract across axes, labels, tooltips, KPIs, tables, and exports.

**Files:** analysis settings base/statistical and chart-specific Layout builders; backend/services/polars_chart_service.py model display helpers; backend/assets/echarts/lytrize_adapter.js; desktop/widgets/native_table_view.py; backend/modules/echarts_export.py; KPI render/update paths in backend/services/kpi_service.py, desktop/pages/dashboard_page.py, and export/live-filter JS.

**Implement a shared format policy:**

- Add per-chart/per-KPI numeric display choices: Auto/Full, Compact, Currency; precision; currency code; and locale. Keep the controls compact and expose them only for visualizations with numeric values.
- Compact format uses K at 10³, M at 10⁶, B at 10⁹, T at 10¹², retains sign, and uses configured precision. Values below 1,000 stay unscaled. Do not alter stored/aggregated values.
- Currency must be explicitly selected (never infer currency from a column name). Provide at minimum USD, EUR, INR and a locale field/region preset; use Intl.NumberFormat(locale,{style:"currency",currency}) consistently in JS renderers and equivalent locale-aware formatting for native/exported tables. Preserve ISO currency symbol/code behavior per locale.
- Apply formatting consistently to axis tick labels, data labels, tooltips, pie center totals, KPI values/deltas, Pivot Table cells and totals, and static/live exports. Keep dates/counts untouched unless selected. Ensure tooltip raw precision is not silently rounded if existing behavior shows exact values.
- Preserve old saved recipes with Auto/Full fallback; record format choices in recipe/display so editing and exporting do not drift.

**Done when:** threshold fixtures around 999/1K, 999K/1M, 999M/1B, 999B/1T; negatives; zero; precision; and USD/EUR/INR locale presets render consistently in native chart, native table, KPI, standalone HTML, and dashboard export.

### P1 — Complete old issue list and verify every prior source fix

The old defects below remain required. Source changes exist for many but do not close them until their acceptance checks pass.

#### APP and Dashboard

- **A1 Per-chart settings:** select at least two charts with different Generate/Layout/Typography values; verify values switch by UID, selected chart alone regenerates, and save/reopen restores them.
- **A2 Advanced Generate forms:** Sankey (after P0), Sunburst, Calendar Heatmap, Waterfall, Funnel, and Globe/Extruded Map show only applicable compact fields. Check Edit and Generate plus keyboard/tab order.
- **A3 Free canvas move/resize:** drag from chart plot interior; resize all 4 edges and 4 corners; preserve opposite edges, minimum size, and canvas bounds. Click/zoom/legend interaction must not become a drag without crossing the threshold. Verify only one tile changes and persisted geometry reopens unchanged.
- **A4 Slicers:** list checkbox list, dropdown, numeric/date slider, search, top-N largest/smallest. Add/edit/delete/reorder/drag each adjacent to charts and KPIs; preserve unrelated geometry and correctly filter chart/KPI results. Test initial export HTML and dynamic sync. Verify keyboard and empty-state behavior.
- **A5 KPI Polars/trend:** exercise every KPI aggregate through preview, add/edit, save/load, dashboard filters, and export. Verify PolarsDataset dispatch; bounded date trend aggregation; positive/negative delta and sparkline update; no trend when date/measure is invalid.

#### Shared Typography (T1)

Fix the compact Auto/color row for **Distribution, Correlation, Categorical Bar, Pie/Donut, Time Series, Scatter Plot, Matrix Heatmap, Pivot Table (only applicable rows), Map Plot, Sankey, Sunburst, Calendar Heatmap, Waterfall, and Funnel**. Remove no-op controls for each type instead of preserving them merely by inheritance.

#### Chart-specific acceptance

- **C1 Distribution:** new numeric histogram uses correct bin count; changing bins rebuilds Polars aggregation, not just display metadata. Verify amount/quantity/binary is_fraud, constant, all-null, grouped overlay; show-grid false/true; value/tooltip colors.
- **C2 Correlation:** all four colorbar positions; visibility reclaims plot space; title/ticks/colors/size/border render; in-cell annotation color uses its dedicated Layout setting and is independent of axis Value Color.
- **C3 Categorical Bar:** new charts default labels off; primary/secondary label toggles are independent and secondary control only appears when secondary metric exists; both grids toggle; legend title and tooltip styling remain correct.
- **C4 Pie/Donut:** remove Layout Show Border; legend title never overlaps categories in all placements/orientations; slice value typography separate from center-total typography; Pull visibly offsets slices; donut total centered (P0); K/M/B compact units and sign/precision work.
- **C5 Time Series:** both grids toggle; title/legend never overlap; correct value and tooltip colors; dual-axis settings survive save/load.
- **C6 Scatter:** x/y grids toggle; legend/title spacing; line width/shape/fill affects trendline (or controls are disabled without a trendline); marker size/opacity and canvas/svg render mode visibly work. Remove Value Color if no real rendered target.
- **C7 Matrix Heatmap:** four colorbar positions; title/tick/title typography/border work; remove generic Value Color/Size if no target; annotation and tooltip colors work.
- **C8 Pivot Table:** complete P0 typography, tooltip removal, configurable conditional heat color, and numeric/currency format; retain light/dark theme; verify 3-level hierarchy aggregation, parent subtotals, expand/collapse, duplicate labels under different parents, null categories, sort stability, slicer recompute, save/reopen, and HTML export.
- **C9 Map Plot:** complete P0 blank-map fix. Also test supported named locations, coordinate scatter, density/sampling, choropleth, markers, tooltip, zoom, local styles, optional satellite, empty/invalid coordinates, theme, Globe/Extruded modes, save/reopen, and offline exports. No mode/style may be stored-only.
- **C10 Advanced types:** Sankey generation first (P0), then verify Sankey links/tooltip; Sunburst path hierarchy; Calendar date aggregation; Waterfall totals; Funnel order/labels. Every typography control must have a visible target or be removed. Globe/Extruded acceptance is listed under C9.

## 5. Formatting and UX design rules for the agent

- Keep display formatting separate from computed values and aggregation.
- Use explicit recipe keys with defaults and version-safe fallbacks. Write down the canonical keys in code comments or nearby model documentation; do not create chart-specific ad hoc formatting rules.
- Ensure the same formatter is used by native ECharts, custom map renderer tooltips, NativeTableView, HTML table export, dashboard live updates, and KPI cards.
- For custom map modes, keep the UI label honest about local/offline capability; do not add a satellite promise to a renderer that ignores imagery. If a feature cannot be implemented and verified by this release, disable it visibly with a clear reason instead of leaving an inert selection.
- Display useful generation/render failures in the app, but log the full exception and traceback for diagnosis. Never replace a traceback with only str(exc) before logging.

## 6. Required deterministic fixture and reproduction matrix

Create or reuse a small fixture containing:

- region, country, city, category, source, target, date;
- amount, quantity, binary is_fraud;
- positive, negative, zero, null, all-null and constant measures;
- latitude/longitude rows with valid points, nulls, (0,0), and out-of-range values;
- duplicate child labels under different parents;
- sufficient categories to test Top N, legends, and heat colors.

Use the same fixture to exercise:

1. Sankey with numeric value and Row Count; inspect complete stack trace before the fix.
2. Map coordinate scatter, named-location choropleth, density, globe, extruded; vary mode/style/render and verify visible output each time.
3. Pie donut with top/right legend, outside labels, center total, custom size/color, and resize.
4. Pivot title font/style/alignment/color, tooltip absence, heat palette toggle, 3-level drill, number formats/currency.
5. Cross-chart value/tooltip color control geometry at normal and narrow settings panel sizes.
6. KPI update after slicer change; distribution bins after live edit; chart settings after selecting another chart.

## 7. Verification sequence and release gate

Run and record command plus exit status. Do not add a passing claim for a test that did not execute.

1. venv/bin/python -m compileall -q backend desktop
2. git diff --check
3. Existing current-tree scripts where relevant: scripts/verify_all_analysis_types.py, scripts/verify_theme_and_legend_fix.py, scripts/test_slicer_fixes.py. Treat skipped Qt/WebEngine/instance coverage as unverified.
4. Add focused regressions for Sankey native dispatch, Pie actual center/layout calculation, Pivot title typography/heat range/formatting, formatter thresholds/locales, and map model-to-visible-render contract. Keep tests in the current app's test/script conventions.
5. Render the exact map and donut models in the actual QtWebEngine UI. Inspect console errors; verify nonzero canvas/container bounds. If local Chromium sandbox restrictions block this environment, do not mark these visual checks passed—run them in the user's normal app environment and include exact steps.
6. Manually test Generate → live edit → regenerate → save → reload → export for all newly changed families. Test both light/dark, narrow/wide cards, and offline startup.
7. Inspect final git status --short and diff. Keep unrelated prior user edits intact and do not commit.

**Release is complete only when:** all screenshot defects are fixed in the visible UI; the original issue list's applicable acceptance criteria pass; advanced Sankey no longer errors; ordinary Map Plot is visibly rendered; values/currencies are consistent; native, save/load, and offline export agree; and the final report clearly distinguishes passed, blocked, and not-applicable checks.

## 8. Fast handoff instructions

This plan authorizes the agent to implement and verify all listed work without waiting for user clarification. Start at P0 Sankey, then P0 Map, Pivot, Pie/T1, number formatting, and the carry-over regression matrix. Do not spend time rewriting completed code that passes the visible acceptance checks. Stop only for an actual external blocker; otherwise continue until the release gate is satisfied.

