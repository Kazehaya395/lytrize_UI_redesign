# Lytrize vs Power BI / Tableau — Capability Gap Analysis & Roadmap

**Date:** 2026-11-11 · **Evidence:** Lytrize source at this checkout; Power BI and Tableau
feature sets as of late 2026 (Power BI Desktop + Power BI Service; Tableau Desktop/Server/Cloud,
VizQL). Effort markers: **S** < 1 day, **M** 1–3 days, **L** 1–2 weeks, **XL** > 2 weeks.

**One-line position:** Lytrize is a privacy-first, offline-capable, single-user desktop BI
workbench with a modern chart engine; it is not yet a *platform*. Everything Power BI and
Tableau do around distribution, collaboration, governance and extensibility is absent — that is
the defining gap, and it is architectural, not cosmetic.

---

## 1. Executive summary

| Dimension | Lytrize (today) | Power BI | Tableau | Verdict |
|---|---|---|---|---|
| Desktop authoring | Yes — native Qt, fast | Yes | Yes | At parity for basic authoring |
| Offline / no-telemetry use | **Yes — core design** | Partial (Desktop) | Partial (Desktop) | Lytrize wins for this user class |
| Chart engine (ECharts + native renderers) | Capable, GPU-friendly, offline | Proprietary | VizQL | Adequate; fewer chart types |
| Calculations (measures) | KPI library only (sum/avg/median/min/max/std/count/nunique/missing/ratios) | **DAX** — thousands of functions, calcs, time intelligence | **Calculated fields, table calcs, LOD expressions** | **Largest functional gap** |
| Data modeling | Flat tables + roles; no relationships/star schema | Full semantic model (relationships, cardinality, RLS, measures, hierarchies) | Relationships, joins, blends, unions, star-schema acceleration | **Major gap** |
| Interactivity | Slicers (list/dropdown/slider/search/top-N), live cross-filter of all charts; per-chart + whole-board | Slicers, cross-highlight, drillthrough, bookmarks, sync slicers, field parameters, tooltips | Quick filters, actions (filter/highlight/URL/set), parameters, sets, history | **Major gap** |
| Drill-down / hierarchy | Pivot table row-hierarchy with expand/collapse (up to 3 levels), native + export | Drill-down hierarchies on any chart, drillthrough pages, expand-down/up | Drill levels out-of-the-box, set actions | Pivot OK; charts lack it |
| Distribution / sharing | HTML + PNG export (self-contained file) | Power BI Service, workspaces, apps, email subscriptions, Teams, embed | Tableau Server/Cloud, subscriptions, embedded analytics, Data Management | **Defines the "not a platform" verdict** |
| Collaboration | None (single-user) | Co-authoring, comments, activity feed | Site/roles, comments, revisions | Missing |
| Governance / security | None (local files) | RLS/OLS, workspace roles, sensitivity labels, endorsement, lineage | Certified data sources, permissions, Data Management (DLP-ish) | Missing |
| Scheduling / refresh | None (manual "regenerate") | Scheduled refresh, incremental refresh, gateway refresh | Extract refresh, live connections, Pulse | Missing |
| Extensibility | Fixed visual set + CSS themes | Custom visuals marketplace, JSON themes, paginated reports | Custom charts limited; extensions API | Missing |
| Data connectors | CSV (Polars) | 150+ sources, DirectQuery/Import/Lake | 80+ sources incl. warehouses, JDBC/ODBC | **Major gap** |
| Performance (this checkout) | Polars aggregations 2–15 ms; 200k-row time series ~430 ms build; `_safe` traversal 2.6× faster this session | Star schema Vertipaq; sub-second at 100M+ rows | Hyper in-memory columnar | Fine for CSV-sized data (<1M rows); no scaling story above that |

---

## 2. Where Lytrize genuinely stands

**Strengths to preserve (they differentiate):**

- **Private, offline, no-account BI.** No telemetry, no login, no per-seat licence, no data
  leaves the machine. Power BI/Tableau cannot offer this; it is the product's niche.
- **Deterministic Polars engine** with lazy frames and a row-id source marker; charts are
  semantics (ChartModel v1), so preview, export, save/reload and edit all render identically.
- **Live analysis loop**: dedicated settings pages per chart family; every control re-renders
  instantly; live-in-place KPI/slicer sync without full rebuild.
- **Free-form canvas** with OS-window semantics (drag anywhere, 8-way resize, arrow-key nudge,
  z-order overlap) — comparable to Power BI's canvas, friendlier than fixed grids.
- **Self-contained exports** — HTML (fully offline, local fonts/assets embedded) and PNG via a
  headless Chromium print path; nothing phones home.
- **Native pivot drill-down** in both the Qt view and the HTML export (indent + toggles, depth ≤ 3).

**Known defects being closed in the current release cycle** (see `lytrize_issues_list`):
correlation/matrix-heatmap colour-bar rendering chain, scatter trendline/line-shape wiring,
legend-title placement, annotation vs tick colour semantics, chart settings sync across
selected charts, canvas/slicer/KPI polish, categorical label defaults.

---

## 3. Gap inventory, prioritized

### P0 — Parity blockers for a "BI tool" claim

| # | Gap | Power BI / Tableau reference | Effort | Note |
|---|---|---|---|---|
| G1 | **No calculated field / measure language.** Only fixed KPI library. | DAX / calc fields + LODs | XL | The single biggest gap. Even a small "DAX-lite" (row context + aggregation over filter) would outrank everything else on a BI checklist. |
| G2 | **No relationships between tables.** One flat CSV per report. | Semantic model / relationships + joins | XL | Requires model storage, join engine, role-playing dims, star-schema heuristic. |
| G3 | **No cross-chart filtering from a chart itself** (click a bar → other charts filter). Only slicers filter. | Cross-filter / actions | L | Needs selection events in ECharts (`click`, `legendselectchanged`) → slicer engine (already exists in `filter_service`) feed-through. |
| G4 | **No drill-through pages** (right-click → filtered detail page). | Drillthrough / actions | M–L | Detail-page concept + report-level filter propagation; reuse slicer serialization. |
| G5 | **No bookmarks / saved views / story mode.** | Bookmarks; Tableau Stories | M | Serialize board state (geometry + slicer selections) under named bookmarks; present as a strip. |
| G6 | **No scheduled refresh.** Data is a one-time import. | Refresh/incremental refresh | M | Local scheduler + "re-open re-read source file" with revision check exists; needs UI + file-watch. |
| G7 | **Chart types missing vs both competitors**: treemap (exists? no — sunburst yes), waterfall ok, **box-and-whisker gaps vs competitors** (statistical has whisker? partially), **decomposition tree**, **key influencers**, ** funnel ok**, ** ribbon**, **arc**, **heatmap density 2D**, **KPI goal/target**. | | M each | Prioritize box plot polish + treemap + Pareto curve. |

### P1 — Professional-grade experience

| # | Gap | Reference | Effort |
|---|---|---|---|
| G8 | **Parameters with data type** driving filters/titles (not just slicers) | Power BI parameters / Tableau parameters | M |
| G9 | **Totals subtotals customization (grand total on/off, % of parent/grand)** — pivot lacks formatting of totals & column subtotals as % of total | Power BI matrix totals | M |
| G10 | **Compact number/currency/unit formatting UI** (K/M/B/T, locale currency, decimal separators) — charts/tables | Both | S–M |
| G11 | **Theme management UI** (create/save/apply named themes, fonts, palette packs) | JSON themes / Tableau themes | M |
| G12 | **Drill levels on ANY chart type** (not only pivot) via hierarchy-aware x-axis (drill down/up buttons) | Both, natively | L |
| G13 | **Conditional formatting rules UI** for table cells (threshold colour scales, not just heat) | Power BI cell CF | M |
| G14 | **Sync slicers across pages/slides** | Synced slicers / global filters | S–M |
| G15 | **Export parity gaps**: PDF export, clipboard image, Excel/CSV export of table models | Both | M |
| G16 | **Tooltips as customizable canvases** (title/body/footer, extra metrics, images) | Report tooltips | L |
| G17 | **Keyboard & screen-reader pass on the canvas** (move/resize/select via keyboard exists partially) | Both | M |

### P2 — Platform & ecosystem

| # | Gap | Why it matters | Effort |
|---|---|---|---|
| G18 | **Web viewer / sharing server** (read-only dashboard via local web app or exported viewer) | The single biggest adoption lever; exports already prove rendering works in a browser | XL |
| G19 | **Workspaces & permissions** (who can see which report) | Multi-user companies | XL |
| G20 | **Row-level security** | Premium customers | XL |
| G21 | **Data connectors beyond CSV** (SQL Server, Postgres, MySQL, Excel, Parquet, Snowflake) via Polars readers (`pl.read_database`, pyarrow) — engine already ready | Warehouse-first customers | L each |
| G22 | **Extract files for fast reopen** (cache normalized data + computed aggregates) | Reopen speed at >1M rows | M |
| G23 | **Custom visuals API / plugin marketplace** | Long-tail visuals | XL |
| G24 | **Auto date hierarchies & time intelligence helpers** (YTD, MAT, same-period-last-year) | DAX/table-calc parity | M |

---

## 4. What competitors have that Lytrize has NO analogue of (honest list)

- DAX / calculated fields, time intelligence functions, measures with implicit filter context
- Semantic models: relationships, cardinality, column profiles, best-practice analyzer
- Drillthrough + tooltips-as-pages + bookmarks + selection pane
- Paginated report pixel-perfect banded printing (SSRS heritage)
- Power Query M / Tableau Prep-style in-app data cleaning pipeline with steps
- Incremental refresh, aggregations, incremental dataset storage
- Row-level security & object-level security
- Subscriptions (email snapshots), alerts (threshold-based), KPI notifications
- Mobile apps with offline cache & touch canvas
- Certification/endorsement workflow + lineage view + usage metrics
- Embedded analytics SDK for third-party embedding

---

## 5. Roadmap

### Phase A — Authoring parity (4–6 weeks)

1. G10 compact/currency formatting (fastest visible win; clears screenshot 4 of the completion plan).
2. G3 chart→chart cross-filter via existing slicer engine (highest interactivity-per-line-of-code).
3. G5 bookmarks & named board states.
4. G7 treemap + box plot polish; verify stat options against competitor outputs.
5. G13 table conditional formatting UI; G9 totals toggles; G14 sync slicers.
6. Acceptance gate: every control in every settings tab provably changes the rendered output in
   Preview **and** in the saved HTML/PNG export, on a 720p screen, with user sign-off.

### Phase B — Calculations & model (6–10 weeks)

1. G1 "Lytrize calc" v1: named measures = expression over columns with filter context
   (aggregates + arithmetic + a small function set: IF/AND/OR/COALESCE/DATEDIFF/DATEADD).
   Reuse Polars for execution; surface in a formula bar + field list, exactly like Tableau.
2. G2 single-fact relationships (left-join on keys, many-to-one only), multiple input tables,
   shared global filters; defer many-to-many/role-playing dims.
3. G4 drill-through detail pages; G12 hierarchy drill on categorical bar & line.
4. G6 scheduled re-read of source files (file-watcher + "source changed" badge).
5. G15 PDF/Excel export.
6. Acceptance gate: a scripted 3-table model + 3 measures reproduces a reference "sales exec
   dashboard" built in Power BI, sign-off on numbers.

### Phase C — Platform (2 quarters+)

1. G18 read-only web viewer (serve saved board HTML + bundled assets from a tiny local server;
   add auth token). Power BI Service equivalent in miniature — still fully local.
2. G21 SQL/Parquet/Excel connectors (Polars native), connection manager screen.
3. G22 extract cache; G24 time-intelligence measure helpers (YTD/MoM/SPLY).
4. G19/G20 workspaces & RLS (if multi-user demand validates it).
5. Optional: G23 plugin SDK.
6. Acceptance gate: 5 pilot users, 2 weeks, no blocking defects; installer + updater verified
   on Windows/Linux/macOS.

---

## 6. Reality checks

- **Do not chase chart-count parity.** Power BI ships ~40 visuals; Lytrize covers the 15 that
  matter for 95% of executive dashboards. Depth (every control works, verified end-to-end)
  beats breadth.
- **Do not start G18/G21 before G1–G3 land.** Distribution of a weak analysis surface does not
  create retention; the current retention risk is "I cannot compute the metric I need".
- **The Windows bundle is older than source.** User-visible defects on that build do not imply
  source defects — verify against this checkout before re-fixing (the current issue-list triage
  found most chart-settings defects already fixed in source).
- **Verification ceiling:** in the Linux sandbox, Qt offscreen + headless Chromium prove
  buildability and option correctness, not pixels. Final acceptance must happen on the user's
  Windows machine at 720p — every phase ends with a sign-off checklist, not a self-assessment.

**Bottom line:** Lytrize can credibly reach "best offline single-analyst BI for CSV/Excel-class
data" with Phase A; "credible team tool" with Phase B; matching Power BI/Tableau as a *platform*
is out of scope and should not be the goal — the goal is the segment where privacy, price and
simplicity win, served better than either incumbent can.
