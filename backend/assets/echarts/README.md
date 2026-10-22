# Vendored ECharts runtime

The application loads `echarts.min.js` from this directory. This is the
version-pinned Apache ECharts 6.1.0 distribution, with the upstream `LICENSE`,
`NOTICE`, and bundled D3 license retained beside it. Runtime access to network
resources is disabled by `EChartsWebView`.

`lytrize_adapter.js` translates Lytrize ChartModel v1 data into ECharts options.
Saved sessions must contain semantic chart models/recipes, never arbitrary
JavaScript or raw ECharts options.

To refresh the vendor runtime, obtain the published `echarts@<version>` npm
package, copy its `dist/echarts.min.js`, `LICENSE`, `NOTICE`, and
`licenses/LICENSE-d3` into this directory, update `VERSION`, and record the
runtime checksum. Review upstream notices before adding or removing bundled
files. The Linux builders already copy `backend/`, so no network fetch occurs
at install or application launch time.
