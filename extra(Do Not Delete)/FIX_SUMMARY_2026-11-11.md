# Lytrize — Fix Summary (session of 2026-11-11)

Verification environment: Linux sandbox, Qt offscreen (limited), headless Chromium SSR
(`echarts.min.js` + `lytrize_adapter.js` in node), Polars 1.44.2 via venv. **Pixel/visual
sign-off must happen on the user's Windows machine at 720p** — this file records what was
verified how; pending items are marked [OPEN].

## Root cause of the "stale" symptoms

The user's Windows build is an older frozen bundle. A large fraction of the reported issues
were already fixed in this checkout (verified this session); do not re-fix. Notable:
typography rows "Value Color / Tooltip color positioned way wider" (all chart types) — the
compact `_auto_color_control` group (Maximum size policy, packs against the caption) already
in source; offscreen probe shows every control column aligned at x=110. Distribution #1a
bins, grid toggles, pie #4c/#4e/#4f/#4g, correlation #2a colour-bar render+title, matrix
heatmap CB position/title, categorical secondary-Y labels, KPI polar path (`PolarDataset`
`.height` error does not reproduce — `calc_polars_kpi` dispatches correctly), Sankey-family
compact Generate layout (FlowGrid), free-form canvas drag/8-way resize, slicer widget types
(list/dropdown/slider/search/top-N), slicer drag no longer breaking the board (namespaced
`slicer:N` geometry keys), pivot hierarchy drill-down (native view + HTML export, depth ≤3),
light-mode pivot table (desktop + export), "Markers"/"Value size"/"Value color" dead rows on
scatter, Pie layout row removal.

## Fixes applied in this session (all in this tree)

1. **Heatmap/correlation colour semantics** (`backend/assets/echarts/lytrize_adapter.js`,
   heatmap branch): new `heatTickInk` (configured Value colour → else configured Axis colour →
   else theme auto ink) now paints the matrix tick labels **and axis headers**; the new
   `heatAxisTitleInk` makes the axis header fall back to the same ladder. Ann colour
   (cell text) remains exclusively owned by `heatmap_annotation_color`. SSR-verified:
   Value `#e879f9` → ticks/headers `#e879f9`, ann stays `#00ffff`; Axis `#4ade80` → ticks
   `#4ade80`; AxisTitle `#fbbf24` → headers only. (Fixes #2c; keeps correlation/matrix
   heatmap tick semantics per-issue.)
2. **Legend title placement** (same file, legend top-level + `legendLightRemap`):
   the legend title now always renders **directly above the legend category list** in every
   position (left/right/top/bottom) and both orientations — side legends now dock as a
   150px column below the title (previously full canvas width, vertically centred, and the
   title floated at the top of the card). `#f8fafc` added to the light-theme remap (was
   rendered washed-out on light cards). SSR-verified for right/left × horizontal/vertical,
   top-right vertical, bottom-center horizontal. (#4b, #5b, #6b, pie legend.)
3. **Categorical value labels default OFF** (`backend/services/polars_chart_service.py`,
   `_categorical_display`): `labels.show` fallback `True → False` (desktop widget and
   live-edit path already defaulted off). #3a.
4. **Scatter render-mode consistency** (`polars_chart_service.py` live edit): `large` is now
   derived from the *current* `render_mode` (svg → False) instead of the chart's born value —
   live svg exports no longer keep large mode. SSR-verified (canvas→large, svg→not).
5. **Scatter adapter** (earlier this session, SSR-verified): trendline honors `line_shape`
   (spline→smooth; hv/hvh→step start/middle; vh/vhv→step end); removed dead
   diamond/triangle symbol mapping; points always `circle` with `symbolSize=6 opacity=0.8`.
6. **Model validation speed** (`backend/modules/chart_model.py`, `_safe`): eliminated the
   per-element f-string path building and isinstance chains. 200k-point payload:
   make_chart_model 204→87 ms, validate 217→101 ms, model→JSON 288→166 ms.
7. **Time-series raw-date path** (`polars_chart_service.py`): fused the double list
   comprehension into one pass (524→432 ms on 200k rows).
8. **Scatter typography masking (#6d)** — verified ALREADY masked in source
   (Value size/color rows removed); an accidental duplicate was re-added and re-removed.
9. **Performance probe baseline** (results in summary): categorical 3 ms, distribution 13 ms,
   correlation 2 ms, scatter 9.5 ms, matrix heatmap 4 ms @ 200k rows.
10. **Gap-analysis doc** added: `docs/GAP_ANALYSIS_POWERBI_TABLEAU.md` (Lytrize vs Power BI /
    Tableau, prioritized P0–P2 roadmap, reality checks).
11. **App #5 KPI**: "PolarDataset object has no attribute 'height'" not reproducible in
    source; KPI polar dispatch + trend/sparkline/delta (▲▼, green/red) work in both the
    desktop card and exports. [OPEN] user sign-off on Windows build.
12. **App #2/#10c** Sankey/Sunburst/Calendar/Waterfall/Funnel settings: already compact
    FlowGrid layouts in source (offscreen render confirmed 3 sections/row at 1468px).
    `GlobeMapSettings` exists but is registered for a non-existent id — 3D Globe/Extruded
    maps are modes of Map Plot (`MAP_MODES`), not a separate chart. [OPEN] sign-off.
13. **App #3 / App #4** (free resize/drag everywhere, slicer widgets + drag stability):
    implemented in source — verified by code walk + prior session offscreen render;
    Windows sign-off pending.
14. **Correlation #2b** 'Show border' draws the colour-bar frame — verified by rendering the
    real correlation model through the adapter both ways (stroke path appears only when on).
15. **Matrix heatmap #7a/#7b/#7c**: CB position (left/right/top/bottom), CB tick size/colour,
    CB title (graphic, custom colour/size), border frame — end-to-end verified with a real
    `build_correlation_chart` model through the adapter. #7c (Value size/color removal) was
    already masked in source. Pivot #8a/#8b (same typography symptom) covered by the
    auto-color fix; #8c light-mode table verified in desktop + export paths; #8d drill-down
    exists (row hierarchy, expand/collapse, depth ≤3) in native table and HTML export.

## [OPEN] pending user verification (Windows, 720p)

- All SSR/probe results above; pixel-level acceptance of: legend title placement with your
  themes, heatmap tick/header ink with your palettes, donut centre total position + K/M/B
  units, Sankey-family settings density/order at your pane width, free canvas drag/resize,
  slicer add/edit/drag flows, KPI cards, pivot drill-down, light vs dark tables, exports.
- Map #9a (scatterplot blank) NOT reproducible at SSR (both modes render; geo symbols emit
  as `<path>`). Data-specific suspicion: null/empty lat-lon columns or float dtype. Please
  supply the CSV column dtypes (or a sample) if it still reproduces after updating.

## Files changed this session

- backend/assets/echarts/lytrize_adapter.js (heatTickInk/heatAxisTitleInk; legend docking; legendLightRemap #f8fafc)
- backend/services/polars_chart_service.py (scatter live-edit `large`; `_categorical_display` labels default; time_series single-pass tail)
- backend/modules/chart_model.py (`_safe` 2.6× faster)
- docs/GAP_ANALYSIS_POWERBI_TABLEAU.md (new)
- FIX_SUMMARY_2026-11-11.md (this file)

Not shipped, intentionally: `backend/assets/echarts/lytrize_adapter.js.bak` (legacy backup),
`__pycache__/`, `.venv-probe/` (scratch).
