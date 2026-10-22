# Lytrize Dashboard Quality Audit and Fix Plan

**Audit date:** 2026-10-20  
**Scope:** ECharts chart settings and sizing, dashboard canvas, background images, session save/restore, backup portability, and likely performance/UI failure points.  
**Method:** Static code inspection of the current workspace and the reported screenshots. Implementation notes were updated 2026-10-21. The desktop app has not been launched and no tests or visual verification have been run in this pass.

## Implementation progress

**Updated:** 2026-10-21. The source changes for the main implementation phases are in the working tree. This is not a claim of production readiness: visual behavior and performance still need an app run.

- **Implemented:** free-canvas session geometry and slicer positions round-trip with a `free-v2` marker; marker-less legacy grid sessions retain their grid migration path. Empty/unauthenticated dashboard saves stop after warning.
- **Implemented:** `.lybackup` v2 packages session records and available Parquet datasets with SHA-256 validation. Legacy JSON remains readable and reports that its machine-local dataset cannot travel with it.
- **Implemented:** independent chart, KPI, and title-card background images, opacity, and blend settings; old shared-card-image settings remain as a restore fallback.
- **Implemented:** advanced-family settings now expose the implemented family-specific controls and hide several irrelevant categorical controls. The exact option mapping still requires a chart-by-chart runtime pass.
- **Implemented:** each chart observes its own viewport and coalesces resize work to animation frames. Resizes patch dimensions and resize ECharts without rebuilding the entire report document.
- **Implemented:** slicer filtering and KPI recomputation run in a local Web Worker, debounced and protected against stale responses; chart/KPI painting is batched to an animation frame.
- **Implemented in the 2026-10-21 screenshot follow-up:** repaired the extra closing brace that made the embedded worker fail to parse; prevented the bridge state listener from treating local slicer selections as structural edits and regenerating the whole report; added live DOM/worker synchronization for list and dropdown slicers; added in-place KPI DOM/worker updates; skipped redundant initial full-data recalculation when no slicer is active; and limited live recomputation to charts/KPIs affected by active slicers.
- **Implemented in the same follow-up:** chart image style values now map into the board model's per-kind image configuration; initial dashboard styles are applied on first render; per-chart image overrides remain isolated; free-canvas `y` and `h` render in their documented pixel units; board height grows to the bottom of the furthest card with padding; donut totals use a centered text bounding box around the computed pie center; pivot-table headers sort rows on click or keyboard activation; and card resizing relies on one chart `ResizeObserver` path instead of a second competing option rebuild.
- **Implemented:** report HTML file writes run in Qt's thread pool; out-of-date write results are ignored. Backup package compression, checksumming, extraction, and validation also run off the UI thread.
- **Still open:** a canonical capability schema and round-trip for every chart setting; 3D WebGL globe/extruded-map rendering (the current family maps to the available 2D map implementation); live in-place control support for slider, search, and top-N slicer widgets (those definitions still use the background report-render fallback); non-embedded/deduplicated asset storage; data-volume benchmarks; and live app visual QA across chart sizes, QtWebEngine worker support, backup migration, and all dashboard interactions. No app run or visual verification has been performed for this follow-up.

## Executive summary

The first repair pass addresses the source-level causes found in the audit: free-canvas geometry and slicer positions now persist with saved sessions; `.lybackup` packages include available datasets and validate checksums; chart, KPI, and title cards have separate image/opacity/blend settings; advanced chart forms hide a set of irrelevant inherited controls; each chart handles resize events independently; slicer filtering and KPI recomputation run in a local Web Worker; and report writes plus backup archive I/O run off the UI thread.

Remaining work is focused on completeness and validation: finish the per-family capability mapping and settings round-trip, implement actual WebGL globe/extruded rendering, make slider/search/top-N slicer controls update in place, add asset deduplication, benchmark representative data sizes, and verify chart families, resizing, restore, and interactions in the running desktop app. The source has not been run through the desktop UI for this follow-up.

The 2026-10-21 screenshot follow-up extends this summary: the worker parse failure and local-selection-triggered rebuild were repaired; first-render styling and card-image mapping now reach the generated DOM; free-canvas vertical coordinates use pixels and the board expands below its final card; KPI and list/dropdown slicer cards update in place; donut total placement and native pivot-table sorting were corrected. These are source-level changes that still need visual confirmation in the running app.

## Findings and evidence

The findings below document the original audit evidence. Items marked **Implemented repair** describe source changes now present in the working tree; requirements marked **Remaining repair** identify work that is still open. The current summary above is the authoritative status overview.

### P0 — Saved free-canvas cards lose their horizontal geometry (fixed in working tree)

**Confirmed in source; matches the reported restore behavior.**

- The canonical geometry contract says `x` and `w` are fractions in `[0, 1]`, while `y` and `h` are pixel values: `backend/modules/canvas_geometry.py:8-22`.
- `_on_save()` stores chart and KPI geometry in `grid_fullwidth_json`: `desktop/pages/dashboard_page.py:3030-3039`.
- `_load_saved_tiles()` treats the title card specially but runs `int()` on every other tile's `x`, `y`, `w`, and `h`: `desktop/pages/dashboard_page.py:152-173`.
- The geometry clamp makes the minimum width `0.12`: `backend/modules/canvas_geometry.py:124-138`.
- The save path serializes only `{**chart_geom, **kpi_geom}`. It excludes the free-canvas mode marker and `_slicer_geom`, even though the in-memory geometry writer stores both: `desktop/pages/dashboard_page.py:1772-1789` and `3037-3039`.

For example, `x=0.5, w=0.5` becomes `x=0, w=0`. The clamp changes the zero width to `0.12`; the card is consequently narrow and pinned left. The missing mode marker also makes format detection dependent on a legacy-shape heuristic. Slicer positions cannot round-trip at all through this session save path.

**Implemented repair:** free-mode chart, KPI, slicer, and title rectangles now retain floating-point x/w values. Session save persists the free-mode marker and namespaced slicer geometry. The loader keeps marker-less integer-grid sessions on the existing legacy conversion path.

**Remaining:** the layout marker is versioned but the full model is not a standalone schema with migration tooling. The canvas still has no general independent image/text object model beyond its existing title/notes objects.

### P1 — Backup JSON is not a complete portable session package

**Confirmed in source.**

- Chart settings/metadata are serialized with chart records in `charts_json`; dashboard style is stored in `export_colours_json`: `desktop/pages/dashboard_page.py:3017-3027` and `3040-3057`.
- The backup exporter includes `grid_fullwidth_json`, `export_colours_json`, `dataset_uri`, `dataset_hash`, and slicer definitions: `backend/modules/database.py:1292-1327`.
- The dataset is written as a separate Parquet artifact next to the local database; the session stores only its filename/hash: `backend/services/session_bundle.py:1-18`, `:39-45`, and `:68-84`.
- The JSON restore path imports the session rows but does not package or copy the Parquet files: `desktop/pages/profile_page.py:190-218` and `backend/modules/database.py:1337-1390`.

On the same installation, local data may still be present. On a different machine/profile, the filename reference is insufficient and the board can become view-only. Large embedded card-image data URIs also make session JSON backups grow substantially.

**Implemented repair:** `.lybackup` packages contain a versioned manifest, saved session records, available Parquet snapshots, and SHA-256 checksums. Restore verifies asset paths, sizes, and checksums before importing session rows and installs datasets locally. Legacy JSON backups remain supported with dataset references cleared and a warning because they cannot carry their Parquet files. Images remain data URIs in saved style records rather than separately deduplicated package assets.

### P1 — Chart settings mix generic controls with family-specific behavior

**Confirmed in source; exact user-visible impact requires app verification.**

- Statistical settings provide a broad shared layout form, including axis labels, legend controls, bar mode, bar gap, and grid: `desktop/widgets/analysis_settings/statistical.py:350-415`.
- Categorical settings inherit the statistical form: `desktop/widgets/analysis_settings/categorical.py:81` onward.
- The advanced visual settings class inherits `CategoricalSettings`; its family-specific generation fields and layout controls are added around that inherited base: `desktop/widgets/analysis_settings/advanced.py:11-54` and `:118-171`.
- Advanced generation hides some irrelevant *generation* rows, but the family builders do not define a complete per-chart capability list for inherited layout/typography rows.
- The ECharts adapter supports family-specific properties beyond the visible controls—for example Sankey iterations/draggability, Sunburst sort/label behavior, calendar cell sizing, and Funnel sizing/gap/label placement: `backend/assets/echarts/lytrize_adapter.js:272-370`.
- The model display mapper currently copies only a subset of advanced family settings into the model: `backend/services/polars_chart_service.py:2131-2154`.

This design permits both sides of the reported problem: irrelevant generic settings can appear for charts that do not use them, while valid ECharts capabilities have no control in the UI. A setting can also be present in a form but have no effect unless the builder, stored chart recipe/model, adapter, live preview, and export path all agree on the same key.

The Analysis page does bind form values from per-chart metadata and can fall back to a native chart model's recipe: `desktop/pages/analysis_page.py:432-438` and `:454-503`. This is the intended persistence path, but the dashboard-to-edit return flow and all chart families still need a round-trip check; the code alone does not prove the reported reset symptom is fixed.

**Required repair:** define chart-family capability schemas (data fields, layout controls, typography controls, defaults, validation, and adapter keys). Generate each form only from its chart's supported capabilities. Every visible setting must have a traceable path to the model/recipe and live renderer; unsupported settings should not be shown. Keep each chart's config/layout/typography bound to its UID through dashboard edit and return.

### P1 — Background images are shared between card types

**Confirmed in source.**

- The page background is independent, but the card background uses one `ex_card_image` and group checkboxes for `charts`, `kpi`, and `title`: `desktop/pages/dashboard_page.py:746-812` and `:814-877`.
- Opacity and blend mode are also shared across all selected groups: `desktop/pages/dashboard_page.py:879-913`.
- The HTML writer applies that single URI to whichever card groups are selected: `backend/modules/echarts_export.py:412-445`.

The current implementation cannot use one image for chart cards, a different image for KPI cards, and a third for the title card. It also cannot tune their opacity/blend independently.

**Required repair:** store separate image references and appearance settings for chart, KPI, and title cards. Each should have its own choose/clear action, preview, opacity, blend, fit, and focal position. Preserve the existing page background as a fourth separate layer. Keep the same model for live preview, saved sessions, and exported dashboards.

### P1 — Resize recalculates complete chart options; not all families have equal fit logic

**Confirmed code path; visual failure needs runtime reproduction.**

- Each report chart has a `ResizeObserver` that calls `buildOption(...)`, then `setOption(..., notMerge:true)`, then `resize()`. The board itself has another observer that calls `draw()` over all chart models: `backend/modules/echarts_export.py:594`.
- `buildOption` makes dimensions available to the chart adapter and contains special fit rules for some chart families, but each series family has its own layout behavior: `backend/assets/echarts/lytrize_adapter.js:120-240` and `:260-370`.
- The card content uses a flex chart region plus title and optional notes, with notes allowed to take up to 24% of card height: `backend/modules/echarts_export.py:582-587`.

This means resizing can trigger full option construction and series replacement instead of a cheap ECharts resize. It can also produce different behavior between chart families that use custom dimensions and ones that retain fixed margins/radii/legend assumptions. This is a likely source of drag-resize lag and “chart stays at default size”; the report does not treat the observer as proof that each chart actually fits correctly.

**Required repair:** test every chart family against a size matrix, then separate dimension-only `chart.resize()` from data/style updates. Where dimensions affect layout, recompute only the necessary geometry and coalesce repeated observer events into one animation frame. Use chart-family-specific compact-mode rules for title, legend, labels, and plot area.

### P1 — Slicer filtering blocks the browser event loop on large datasets

**Confirmed in source; likely contributor to freezes.**

- The live report filter engine walks all payload rows to build a selection mask and selected-index list: `backend/assets/echarts/lytrize_live_filter.js:61-135`.
- Aggregation then builds grouped arrays and computes values synchronously; some operations sort full per-category values: `backend/assets/echarts/lytrize_live_filter.js:147` onward.
- A slicer `change` event schedules `drawAll` after 60 ms, but does not move the filtering/calculation off the browser thread: `backend/modules/echarts_export.py:318-325`.
- The input/payload work is moved to a Qt worker before report creation, which is useful, but the subsequent embedded-browser filtering remains synchronous: `desktop/workers.py:500-560`.

The existing filter-index cache avoids rebuilding identical masks across cards, but every new selection still scans the active columns and then rebuilds affected chart/KPI data. On high-row-count reports this can freeze chart interaction and make the desktop feel unresponsive even when Polars is used elsewhere.

**Required repair:** profile representative datasets and move row filtering/aggregation away from the WebEngine UI thread, using a local worker strategy compatible with the app's offline/privacy design. Pre-index columns and categorical values where practical, avoid materializing repeated arrays, cancel stale requests, and batch ECharts updates. Preserve the local/offline privacy model.

### P1 — Full report reload and UI-thread file writing remain expensive

**Confirmed in source.**

- Some colors/text are patched into the current DOM, but settings outside `_LIVE_STYLE_KEYS` schedule a complete report regeneration: `desktop/pages/dashboard_page.py:1327-1356`.
- `ReportCanvas.set_report()` writes a new digest-named HTML file and loads it into WebEngine whenever the HTML changes: `desktop/widgets/report_canvas.py:213-261`.
- `_write()` uses `Path.write_text()` synchronously. The preview HTML can contain chart models and a column-pruned copy of the complete report dataset, so this file write is on the UI thread and can be large.
- The dashboard style patch rebuilds and applies chart options to every chart for selected card/background color changes: `backend/modules/echarts_export.py:338-359`.

The worker pipeline reduces synchronous chart/data preparation, but it does not eliminate document reloads or the final HTML file write on the UI thread. Reloading also reinitializes every ECharts instance. This can explain delayed image/layout updates, flashes, and the feeling that a local change rebuilds the whole canvas.

**Required repair:** classify mutations into model/data, style, geometry, and content updates. Patch the existing DOM and only the affected chart where possible; keep geometry changes in the editor DOM; transfer HTML file writes off the UI thread; and reserve full document reloads for structural changes. Avoid rerunning ECharts option builders for CSS-only changes.

### P2 — Backup and save flows need clearer failure guards and size reporting

**Confirmed in source.**

- `_on_save()` displays warnings for an empty chart list and missing user, but those branches do not return before continuing: `desktop/pages/dashboard_page.py:2982-2991`.
- The embedded-image path allows images up to 4 MiB each, but base64 encoding expands them and a single shared image is duplicated into the report/session JSON: `desktop/pages/dashboard_page.py:703-719`, `:965-1035`, and `backend/modules/database.py:1292-1327`.

The save action should either reject these states explicitly or deliberately support a blank dashboard. Large backups should show an estimate and provide predictable package-size handling instead of surprising the user with a large JSON file.

## Chart-family review matrix

This matrix reflects code structure, not a visual pass in the running app.

| Chart family | Current settings structure | Audit risk to resolve |
|---|---|---|
| Statistical, categorical bars, distribution | Shared statistical/categorical controls; common axes, labels, legends, and typography are mapped through model display helpers. | Verify every setting changes the rendered option; hide controls that do not apply to the concrete generated series. |
| Time series, scatter | Specialized builders inherit shared settings and mask some bar controls. | Verify line width/marker/axis/legend mapping and ensure responsive resizing does not replace unnecessary data. |
| Pie, donut, Nightingale rose | Pie-specific settings; bar controls are masked; rose adds a rose-mode control. | Test title/legend/pie layout at small sizes and confirm independent legend spacing and donut-center text. |
| Matrix heatmap, pivot table | Specialized settings and table/heatmap renderer paths. | Check font/axis/cell sizing and large-table rendering under resize and theme changes. |
| Map plot | Specialized map builder and map display configuration. | Verify scatter and choropleth independently, including empty/missing geography and resize behavior. |
| Sankey, Sunburst, Calendar heatmap, Waterfall, Funnel, 3D Globe & Extruded Map | Advanced settings inherit categorical controls, then add only a small family-specific subset. | Remove inherited irrelevant fields and expose/validate the relevant adapter options; test layout, tooltip, legend, and typography per family. |

## Detailed fix plan

### Phase 0 — Reproduction baseline and data safety

**Status: partially complete.** Source-level failure points have been repaired in the working tree. The requested fixture/save/restore reproductions and running-app regression pass have not been performed.

1. Capture the current repo revision and make a copy of the user's existing session/backup data before migration.
2. Create a fixed local fixture with at least two charts at fractional x/width positions, two KPIs, two slicers, a title card, per-chart settings, independent theme overrides, and image assets.
3. Record geometry and settings from the live board, save as a session, export JSON backup, restore on the same profile, then restore on a clean profile.
4. Check the existing “CSS code leaked into dashboard” screenshot as a regression reproduction. The current editor CSS is embedded in a `<style>` element in the source, so the screenshot symptom cannot be conclusively attributed from static inspection alone.

### Phase 1 — Repair and version the dashboard/session model

**Status: partially complete.** Fractional free-canvas geometry and slicer placements now persist with a `free-v2` marker; legacy integer-grid interpretation remains. Portable dataset backup/restore has a versioned `.lybackup` package. A complete entity schema/version migration for every object type remains open.

1. Introduce one versioned layout schema with explicit `mode` (`free` or legacy grid) and documented units. Preserve `x` and `w` as floats; validate finite numbers without integer coercion.
2. Persist chart, KPI, slicer, title, image, and text card placements in that schema with stable namespaced IDs. Store the full geometry blob, including the free-mode marker.
3. Migrate legacy grid sessions only when their schema/version says grid. Do not infer free-vs-grid from geometry values when an explicit version is available.
4. Keep a v1 migration path for existing SQLite sessions and JSON backups. Migration must be reversible from a pre-migration backup and must not overwrite the original source file.
5. Make JSON backup a versioned package: manifest, session records, dataset snapshot, referenced image assets, and checksums. Use safe relative asset paths; validate all entries before committing restore; report missing assets clearly.
6. Add round-trip validation for the DB save/load path and JSON export/import path, including fractional coordinates and independent entity IDs.

### Phase 2 — Make chart settings truthful and complete

**Status: partially complete.** Advanced-family controls were added and unrelated inherited controls masked for Sankey, Sunburst, Calendar heatmap, Waterfall, Funnel, and the current map family. A canonical capability schema, complete settings persistence audit, and chart-family render verification remain open.

1. Define a capability schema per chart family: supported generation fields, layout fields, typography fields, defaults, value ranges, and renderer mapping.
2. Build the Generate/Edit, Layout, and Typography tabs from those capabilities. Remove bar gap/mode, axes, legends, or other fields where the current visual family does not use them.
3. Add controls only for features the ECharts adapter and model can support. Where the adapter already supports a feature, either expose it in the correct family or remove the dead implementation path.
4. Establish one canonical settings record per chart UID (`config`, `layout`, `typography`, schema version). Use the same record for generation, live preview, dashboard “edit chart,” saved sessions, and export.
5. Add a settings-to-render mapping review: changing each visible field must change the relevant model/option, while unrelated families remain unchanged. This includes legend position/layout, line width, bar gap, labels, fonts, colors, tooltip appearance, and family-specific options.
6. Verify edit navigation: select chart A, edit and save; select chart B, edit; return to A from the dashboard; A must reload A's saved values rather than the last form's values.

### Phase 3 — Independent card background images

**Status: partially complete.** Chart, KPI, and title cards have independent embedded images, opacity, blend, cover/contain/tile fit, and focal-position settings, with legacy shared-image fallback. Page background remains separate. Deduplicated package assets remain open.

1. Replace the shared card image with independent `chart_image`, `kpi_image`, and `title_image` assets; retain a separate `page_image`.
2. Give each layer independent opacity, blend, fit (`cover`/`contain`/`tile`), position/focal point, and clear action.
3. Persist references through the versioned asset store, not repeated unbounded base64 strings in every style object. Embed images only when building a self-contained HTML export.
4. Ensure background changes update only the affected cards and do not rebuild chart data or every ECharts option.

### Phase 4 — Make chart sizing responsive and predictable

**Status: partially complete.** Per-chart observers coalesce resize work and update the chart without reloading the board. Fit rules for every family and the compact/medium/large aspect-ratio pass remain unverified.

1. Test all chart families at compact, medium, and large card sizes and at narrow/wide aspect ratios. Include cards with long titles, many legend items, notes, and outside labels.
2. Use `chart.resize()` for pure viewport changes. Rebuild chart options only when data or a layout control changes; if a family depends on measured dimensions, debounce a geometry-only option patch to one animation frame.
3. Remove fixed plot margins/radii that do not scale with available content. Calculate title, legend, labels, plot center, and color bar from the measured inner content box after card padding, title, notes, and editing chrome are accounted for.
4. Add per-family compact behavior: move side legends when needed, abbreviate/hide labels rather than overlap, and keep pie/donut centers inside the actual plot area.
5. Confirm hidden/visible tabs, preview mode, browser zoom, and high-DPI scaling all trigger a correct resize without resetting data or interaction state.

### Phase 5 — Remove blocking work from interactions

**Status: partially complete.** Slicer filtering and KPI recalculation run in a local worker, stale requests are ignored, chart painting is batched, and preview HTML writes plus backup archive I/O leave the UI thread. Benchmarks, unsupported-worker fallback behavior, database-import duration, and broader incremental update classification remain open.

1. Profile slicer filtering, KPI calculations, initial dashboard rendering, and export at small, medium, and large row counts.
2. Move interactive row filtering and aggregation away from the WebEngine UI thread, using a local worker strategy compatible with the app's offline/privacy design.
3. Precompute column indices and stable categorical dictionaries; avoid repeated string normalization and unnecessary full-length arrays on each selection.
4. Cancel stale requests and only apply the newest filter result. Batch `setOption` calls per animation frame and update only charts/KPIs affected by the slicer scope.
5. Split updates into DOM style, geometry, chart display, data, and full-document rebuild categories. Geometry edits should stay in the live canvas. CSS-only image/color changes should not re-run chart aggregation.
6. Move writing large HTML preview files off the Qt UI thread. Keep progress/busy indicators responsive and preserve the last valid board if a newer build fails.

### Phase 6 — Canvas and UI polish

**Status: pending runtime review.** Existing source paths include drag/resize chrome and slicer scrolling, but hit targets, error-state presentation, interaction responsiveness, and exports have not been visually verified after these changes.

1. Verify drag/resize hit targets at minimum card sizes, edit toolbar overlap, keyboard movement, selection, and usable scrolling for slicer values/notes.
2. Ensure the title, chart area, notes, and legend each have explicit space allocation; no hidden bottom text or resize handle should cover values.
3. Provide clear loading, stale-result, empty-data, invalid-configuration, missing-backup-asset, and view-only states. Keep error detail in expandable diagnostics rather than drawing SQL/code or implementation text over the dashboard.
4. Make save/restore success messages summarize charts, KPIs, slicers, geometry, images, and dataset availability—not just row counts.

## Acceptance checklist

- A half-width card on the right stays half-width and on the right after save, close/reopen, JSON backup, and JSON restore.
- Chart, KPI, slicer, and title positions and sizes all round-trip; no object IDs collide between card types.
- A restored package on a clean profile has the same data-backed slicers and the same images, or reports exactly which asset is absent.
- Each chart family shows only relevant controls. Every shown setting affects the expected chart live and after save/reopen.
- Changing a chart's settings and returning to another chart restores that chart's own values.
- Every chart family fits the available card at compact/medium/large sizes without retaining default dimensions, clipping titles, or overlapping legends.
- Slicer interaction, KPI add/edit, chart resize, theme changes, and image changes remain responsive at representative large dataset sizes.
- UI-thread work is limited to short state/DOM updates; heavy filtering, aggregation, HTML serialization, and file writes do not freeze interaction.
- Live dashboard, saved session, JSON-restored dashboard, HTML export, and PNG export agree on styling, geometry, and chart settings.

## Verification limits and implementation order

The most certain immediate fix is the fractional geometry loader and the save blob omissions. That should be handled before any broad session migration because new backups would otherwise keep reproducing the same damage. Next, establish the chart capability/settings map and independent image model. Then optimize the renderer and live filtering using measurements, so an optimization does not mask incorrect settings or break saved-session compatibility.

This plan tracks implementation state as of 2026-10-21. The code changes are source-level repairs; screenshot-level acceptance remains unverified until the user runs the app and checks the dashboard on representative data and card sizes.

## 2026-10-21 screenshot follow-up: root causes and repairs

The five screenshots identify six concrete failures. Earlier implementation notes covered surrounding features but left integration paths that were not connected; that is why those notes did not match the behavior shown.

| Screenshot annotation | Root cause found in code | Repair in working tree | Status |
|---|---|---|---|
| Donut total is displaced from the hole center | Graphic `left`/`top` were set to the pie center, which positions the graphic bounding box from its top-left. The box therefore extended right/down from the intended center. | Size the text box to the inner donut hole and subtract half its width/height from the computed pie center. | Implemented; visual confirmation pending. |
| Bottom of canvas is clipped and scrollbar stops early | The free-canvas contract stores `y`/`h` in pixels, but HTML rendering converted both to percentages. The board also lacked a persistent bottom extent after moving/resizing the last card. | Render `top` and `height` in pixels; grow the board to the maximum card bottom plus 32px after move/resize; calculate the same extent in generated export HTML. | Implemented; resize/scroll confirmation pending. |
| KPI/card styling does not follow left-panel settings on first load | The live style function ran only after an interactive edit. Initial HTML did not apply the saved style dictionary to KPI and other DOM elements. | Apply the saved dashboard style during first page load and reapply after dynamic KPI insertion. | Implemented; saved-session visual check pending. |
| Adding/filtering slicers freezes or reloads the board | The bundled live-filter script had an unmatched closing brace, causing the worker to fail parsing. Separately, each local `bridge.set("slicers", ...)` emitted `state_changed`; the generic listener mistook selection for an external edit and scheduled full HTML/data regeneration. | Repair the worker source; fingerprint local slicer writes so the generic listener ignores them; patch list/dropdown slicers in the DOM; compute options in the worker; skip recomputation for unaffected chart/KPI scopes and when no selection is active. Other widget families retain the background-render fallback. | Implemented for list/dropdown; other widgets and real data-volume latency remain open. |
| Pivot table has no sorting control | The native table renderer emitted static headers with no sorting state or event handling. | Make headers sortable by click, Enter, or Space; compare numeric-looking cells numerically and other cells with locale-aware natural sorting; expose direction via `aria-sort` and an indicator. | Implemented; runtime confirmation pending. |
| Selected chart background image is not visible after save/restore | Dashboard style uses `ex_chart_image`, `ex_kpi_image`, and `ex_title_image`, while export rendering only read a legacy `card_images` field that the dashboard never populated. Global images therefore never entered the generated board. | Translate family-specific image URI/opacity/blend/fit/position into the board model; preserve chart-specific overrides and avoid copying image URIs during unrelated edits. | Implemented; image export/restore visual confirmation pending. |
| Adding a KPI or slicer pauses the whole canvas when card images are configured | Routine `_apply_live_style()` calls expanded to every style key, including multi-megabyte image data URIs and the per-chart override key. Adding a KPI/slicer then resent image bytes across QWebChannel and reapplied image styles to every card. Chart-scoped filtering also returned unchanged chart models, causing needless option rebuilds. | Routine style patches now omit image-bearing keys; explicit image edits and reset still update them. Per-chart appearance edits send only opacity/blend/fit/position after the image is installed. Filter results include only affected charts (or all original models when clearing the last filter), and grouped chart aggregation streams sum/count/min/max without allocating a value array per row. | Implemented; measure real-world latency on the user's dataset. |

Worker-side filtering runs away from the page's main thread, so a long computation should no longer freeze QtWebEngine input. Chart-scoped filtering now skips unchanged chart models, and non-median grouped calculations no longer allocate one temporary value array per category row. Filtering can still take noticeable time on large datasets; representative timing and memory measurements are required before claiming a latency target. Chart creation still needs a new model and report update, but its assembly/write work uses the existing background workers.

Additional image-path follow-up: family images now live once as board-level CSS variables and are inherited by chart, KPI, and title tiles. Chart-specific overrides stay on their own tile, so adding a KPI does not need to resend or duplicate an embedded image. Live CSS custom-property updates use setProperty, and routine KPI/slicer style synchronization excludes image payloads.
