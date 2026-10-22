# Offline Map Boundaries

Lytrize renders Map Plot with the bundled Apache ECharts runtime and local
Natural Earth GeoJSON. Normal chart use, dashboard preview, and exported HTML
do not contact a tile service or download geometry.

## Included boundary coverage

- **Countries:** Natural Earth Vector 1:110m, 177 country features.
- **Admin-1:** Natural Earth Vector 1:50m, 294 published subdivisions. The
  resolver supports codes, country-qualified names, and bare names when they
  are unique in the bundle. US states also accept postal abbreviations. This
  remains limited to subdivisions Natural Earth publishes in that release.
- Country/state boundaries are generalized for cartographic display. They are
  not legal, cadastral, or survey boundaries.
- Natural Earth publishes these files in the public domain. The bundle records
  its version, source paths, feature counts, attribution, and SHA-256 checksums
  in `backend/assets/echarts/maps/natural-earth/metadata.json`.

## Refreshing the local copy

Refreshing is a maintainer action. It requires internet access, but routine
application use stays offline. From the project root:

```bash
scripts/update_map_boundaries.sh
```

That resolves the latest Natural Earth Vector GitHub release. To reproduce a
specific version, pass its release tag:

```bash
scripts/update_map_boundaries.sh v5.1.2
```

The script downloads the two source GeoJSON files, normalizes and simplifies
them with `scripts/build_map_boundaries.py`, and writes compact local files to
`backend/assets/echarts/maps/natural-earth/`. The normalizer validates feature
counts and unique map keys, rounds coordinates, rebuilds the name/centroid
lookups, and records checksums. Rebuild `.deb`/`.rpm` packages after refreshing;
both package scripts copy the complete `backend/` tree.

## Offline sharing

Single-chart and dashboard HTML exports include the needed GeoJSON and register
it locally in ECharts. They contain no boundary fetch request. Export metadata
identifies the Natural Earth release and public-domain status. The HTML remains
self-contained and does not include source dataset rows beyond the materialized
chart model.
