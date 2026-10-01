"""modules/analysis/matrix_table.py -- Pivot table & heatmap runner."""
import logging
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from modules.charts import chart_layout, COLORS
from modules.utils.perf import cached_pivot
from services.column_roles import numeric_subset, role_pools


_DIVERGING_AGGS   = {"mean", "median", "std"}
_MAX_CATS_HEATMAP = 40
_MAX_CATS_TABLE   = 300
_HEX_GRADIENT     = [
    "#0f172a", "#111827", "#172554", "#1e3a5f",
    "#1e40af", "#2563eb", "#3b82f6", "#60a5fa",
]




def _trim_pivot(pivot: pd.DataFrame, max_cats: int) -> pd.DataFrame:
    if pivot.shape[0] > max_cats:
        pivot = pivot.loc[pivot.abs().sum(axis=1).nlargest(max_cats).index]
    if pivot.shape[1] > max_cats:
        pivot = pivot[pivot.abs().sum(axis=0).nlargest(max_cats).index]
    return pivot




def _sort_pivot(pivot: pd.DataFrame) -> pd.DataFrame:
    try:
        row_order = pivot.mean(axis=1).sort_values(ascending=False).index
        col_order = pivot.mean(axis=0).sort_values(ascending=False).index
        return pivot.loc[row_order, col_order]
    except Exception:
        return pivot




def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    try:
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:,.1f}M"
        if abs(v) >= 1_000:
            return f"{v:,.0f}"
        return f"{v:,.2f}"
    except Exception:
        return str(v)




def _fmt_total(v) -> str:
    """Bold-formatted value for totals row/column."""
    raw = _fmt(v)
    return f"<b>{raw}</b>" if raw != "—" else "—"




def _cell_bg_gradient(val, vmin, vmax, base_dark="#0f172a", accent="#2563eb") -> str:
    """Return a hex colour interpolated between dark base and accent based on val position."""
    if vmin == vmax or val is None or (isinstance(val, float) and np.isnan(val)):
        return base_dark
    ratio = max(0.0, min(1.0, (val - vmin) / (vmax - vmin)))
    def _hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    def _rgb_to_hex(r, g, b):
        return "#{:02x}{:02x}{:02x}".format(int(r), int(g), int(b))
    r1, g1, b1 = _hex_to_rgb(base_dark)
    r2, g2, b2 = _hex_to_rgb(accent)
    return _rgb_to_hex(r1 + ratio * (r2 - r1), g1 + ratio * (g2 - g1), b1 + ratio * (b2 - g1))




def _build_pivot(df, index_col, columns_col, values_col, agg, sort_rows,
                 top_n_rows, roles=None):
    """Shared pivot-building logic for both heatmap and table views.

    The index/columns defaults are dimensions and the value default is a
    metric, both taken from the shared role pools (``services.column_roles``)
    rather than from local ``select_dtypes`` calls, so a pivot obeys the
    user's Column Classification just like every other chart. See
    ``correlation.py`` for the full rationale.
    """
    pools = role_pools(df, roles=roles)
    cats = pools["dim"]
    num = pools["num"]

    idx = index_col or (cats[0] if cats else df.columns[0])
    cols = columns_col or (cats[1] if len(cats) > 1 else df.columns[1])
    # Fall back to the first genuinely-numeric column when the metric pool is
    # empty (no metric classified at all). This is a last-resort safety net
    # for a pivot, not a second pool: `numeric_subset` asks only "will the
    # aggregation work", never "is this a metric".
    vals = values_col or (num[0] if num else next(
        iter(numeric_subset(df, list(df.columns))), None))

    if not vals or idx not in df.columns or cols not in df.columns or vals not in df.columns:
        return None, None, None, None, None

    try:
        pivot = cached_pivot(df, index=idx, columns=cols, values=vals, aggfunc=agg)
        if isinstance(pivot.columns, pd.MultiIndex):
            pivot.columns = [" · ".join(str(c) for c in col).strip() for col in pivot.columns]
        if isinstance(pivot.index, pd.MultiIndex):
            pivot.index = [" · ".join(str(c) for c in row).strip() for row in pivot.index]
    except Exception:
        return None, None, None, None, None

    return idx, cols, vals, pivot, agg




def run_matrix_heatmap(df, index_col=None, columns_col=None, values_col=None,
                       agg="mean", palette=None, roles=None,
                       sort_rows="value_desc", top_n_rows=None, **kwargs):
    """Generate a matrix heatmap chart (always heatmap view)."""
    result = _build_pivot(df, index_col, columns_col, values_col, agg, sort_rows,
                          top_n_rows, roles=roles)
    idx, cols, vals, pivot, agg = result
    if pivot is None:
        return []

    pal = palette or COLORS
    agg_label = agg.upper()
    _row_cap = _MAX_CATS_HEATMAP
    pivot = _trim_pivot(pivot, _row_cap)

    try:
        if sort_rows in ("value_desc", "value_asc"):
            row_means = pivot.mean(axis=1)
            pivot = pivot.loc[row_means.sort_values(ascending=(sort_rows == "value_asc")).index]
            col_order = pivot.mean(axis=0).sort_values(ascending=False).index
            pivot = pivot[col_order]
        elif sort_rows == "cat_asc":
            pivot = pivot.sort_index(ascending=True)
        elif sort_rows == "cat_desc":
            pivot = pivot.sort_index(ascending=False)
        else:
            pivot = _sort_pivot(pivot)
    except Exception:
        pivot = _sort_pivot(pivot)

    if top_n_rows and top_n_rows > 0:
        pivot = pivot.iloc[:top_n_rows]

    n_rows, n_cols_p = pivot.shape
    if n_rows == 0 or n_cols_p == 0:
        return []

    tr = df[idx].nunique()
    tc = df[cols].nunique()
    trunc_note = f"  (top {n_rows}×{n_cols_p} of {tr}×{tc})" if (tr > _row_cap or tc > _row_cap) else ""
    base_title = f"Matrix ({agg_label}): {vals}  ·  {idx} × {cols}{trunc_note}"

    z_values = pivot.values.tolist()
    x_labels = [str(c) for c in pivot.columns]
    y_labels = [str(r) for r in pivot.index]
    flat = [v for row in z_values for v in row
            if v is not None and not (isinstance(v, float) and np.isnan(v))]

    height = max(420, min(n_rows * 30 + 160, 800))

    use_diverging = agg in _DIVERGING_AGGS
    colorscale = "RdBu" if use_diverging else "Blues"
    zmid  = float(np.mean(flat)) if use_diverging and flat else None
    zmin  = float(min(flat)) if (not use_diverging and flat) else None
    zmax  = float(max(flat)) if (not use_diverging and flat) else None

    text_vals = [[_fmt(v) for v in row] for row in z_values]
    show_cell_text = n_rows <= 18 and n_cols_p <= 18

    fig = go.Figure(go.Heatmap(
        z=z_values,
        x=x_labels,
        y=y_labels,
        text=text_vals if show_cell_text else None,
        texttemplate="%{text}" if show_cell_text else None,
        textfont=dict(size=10, color="white"),
        colorscale=colorscale,
        zmid=zmid, zmin=zmin, zmax=zmax,
        hoverongaps=False,
        hovertemplate=(
            f"<b>{idx}:</b> %{{y}}<br>"
            f"<b>{cols}:</b> %{{x}}<br>"
            f"<b>{agg_label}({vals}):</b> %{{z:,.3f}}"
            "<extra></extra>"
        ),
        colorbar=dict(
            title=dict(
                text=f"{agg_label}({vals})", side="right",
                font=dict(color="#cbd5e1", size=11),
            ),
            tickfont=dict(color="#94a3b8", size=10),
            thickness=14, len=0.85,
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(100,116,139,0.3)", borderwidth=1,
        ),
    ))
    _layout = chart_layout(height=height)
    _layout["margin"] = dict(l=10, r=100, t=58, b=90)
    fig.update_layout(**_layout)
    fig.update_layout(
        title=dict(text=base_title, font=dict(color="#e2e8f0", size=13)),
        xaxis=dict(
            showgrid=False,
            linecolor="rgba(100,116,139,0.25)",
            automargin=True,
            tickangle=-30, side="bottom",
        ),
        yaxis=dict(
            showgrid=False,
            linecolor="rgba(100,116,139,0.25)",
            automargin=True,
            autorange="reversed",
        ),
    )
    fig._lytrize_meta = {
        "analysis_type": "matrix_heatmap",
        "x_axis": cols, "y_axis": idx,
        "legend": f"{agg_label}({vals})",
        "supports_axis_editing": True, "supports_legend_editing": True,
        "matrix_view": "heatmap",
    }
    return [(f"Matrix Heatmap: {idx} × {cols}", fig)]




def run_matrix_table(df, index_col=None, columns_col=None, values_col=None,
                     agg="mean", palette=None, roles=None,
                     sort_rows="value_desc", top_n_rows=None, **kwargs):
    """Generate a pivot table chart (always table view)."""
    result = _build_pivot(df, index_col, columns_col, values_col, agg, sort_rows,
                          top_n_rows, roles=roles)
    idx, cols, vals, pivot, agg = result
    if pivot is None:
        return []

    pal = palette or COLORS
    agg_label = agg.upper()
    _row_cap = _MAX_CATS_TABLE
    pivot = _trim_pivot(pivot, _row_cap)

    try:
        if sort_rows in ("value_desc", "value_asc"):
            row_means = pivot.mean(axis=1)
            pivot = pivot.loc[row_means.sort_values(ascending=(sort_rows == "value_asc")).index]
            col_order = pivot.mean(axis=0).sort_values(ascending=False).index
            pivot = pivot[col_order]
        elif sort_rows == "cat_asc":
            pivot = pivot.sort_index(ascending=True)
        elif sort_rows == "cat_desc":
            pivot = pivot.sort_index(ascending=False)
        else:
            pivot = _sort_pivot(pivot)
    except Exception:
        pivot = _sort_pivot(pivot)

    if top_n_rows and top_n_rows > 0:
        pivot = pivot.iloc[:top_n_rows]

    n_rows, n_cols_p = pivot.shape
    if n_rows == 0 or n_cols_p == 0:
        return []

    tr = df[idx].nunique()
    tc = df[cols].nunique()
    trunc_note = f"  (top {n_rows}×{n_cols_p} of {tr}×{tc})" if (tr > _row_cap or tc > _row_cap) else ""
    base_title = f"Matrix ({agg_label}): {vals}  ·  {idx} × {cols}{trunc_note}"

    z_values = pivot.values.tolist()
    x_labels = [str(c) for c in pivot.columns]
    y_labels = [str(r) for r in pivot.index]

    _idx_w  = min(max(max((len(str(r)) for r in y_labels), default=6), len(str(idx)), 8), 30)
    _hdr_len  = max((len(str(h)) for h in x_labels), default=4)
    _val_len  = max((len(str(v)) for row in z_values for v in row
                     if v is not None and not (isinstance(v, float) and np.isnan(v))),
                    default=4)
    _data_w = max(_hdr_len, _val_len, 6)
    _VALUE_HEADER_MAP = {
        "sum":    "Overall Total",
        "mean":   "Overall Average",
        "median": "Overall Median",
        "min":    "Overall Minimum",
        "max":    "Overall Maximum",
        "count":  "Overall Count",
        "std":    "Overall Std. Dev",
    }
    _val_hdr_w = max(len(_VALUE_HEADER_MAP.get(agg, f"{agg.upper()}({vals})")), _data_w)
    _col_widths = [_idx_w] + [_data_w] * n_cols_p + [_val_hdr_w]

    _px_per_unit = 8
    _fig_width   = min((sum(_col_widths)) * _px_per_unit + 24, 1400)

    table_header_color = None
    if isinstance(kwargs, dict):
        table_header_color = kwargs.get("table_header_color")

    hdr_color = str(table_header_color) if table_header_color else (pal[0] if pal else "#4f46e5")
    hdr_alt   = pal[1] if len(pal) > 1 else "#6366f1"
    hdr_fills = [hdr_color] + [hdr_color if i % 2 == 0 else hdr_alt
                                for i in range(n_cols_p)]
    hdr_fills = hdr_fills + [hdr_color if n_cols_p % 2 == 0 else hdr_alt]

    n_data_rows = len(y_labels)
    row_fills_base = ["#1e293b" if i % 2 == 0 else "#0f172a" for i in range(n_data_rows)]

    cell_fills_by_col = []
    cell_fills_by_col.append(row_fills_base)
    for j in range(n_cols_p):
        vmin, vmax = 0, 0
        try:
            clean = [row[j] for row in z_values
                     if row[j] is not None and not (isinstance(row[j], float) and np.isnan(row[j]))]
            if clean:
                vmin, vmax = min(clean), max(clean)
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass
        col_bg = []
        for i, row in enumerate(z_values):
            v = row[j]
            base = "#1e293b" if i % 2 == 0 else "#0f172a"
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                blend_hex = _cell_bg_gradient(v, vmin, vmax,
                                               base_dark=base,
                                               accent="#1e40af")
                col_bg.append(blend_hex)
            else:
                col_bg.append(base)
        cell_fills_by_col.append(col_bg)

    _value_col_method = "std" if agg == "std" else agg
    try:
        if _value_col_method in ("count",):
            _val_series = df.groupby(idx, dropna=False)[vals].count()
        elif _value_col_method in ("min", "max", "sum", "mean", "median"):
            _val_series = getattr(df.groupby(idx, dropna=False)[vals], _value_col_method)()
        else:
            _val_series = df.groupby(idx, dropna=False)[vals].first()
    except Exception:
        _val_series = pd.Series(index=pivot.index, dtype=float)
    _val_series = _val_series.reindex(pivot.index)
    value_col_fmt = [_fmt(v) for v in _val_series.tolist()]
    val_col_base = ["#0f2a3a" if i % 2 == 0 else "#0a1e2a" for i in range(n_data_rows)]
    cell_fills_by_col.append(val_col_base)

    index_vals_disp = y_labels
    data_cols_fmt   = [[_fmt(row[j]) for row in z_values] for j in range(n_cols_p)]
    _data_cells = [index_vals_disp] + data_cols_fmt + [value_col_fmt]

    # Keep the RAW numbers beside the pre-formatted labels. The "Num format"
    # Layout control is applied after the figure is built, and a formatted
    # string cannot be re-formatted to a different precision -- so the runner
    # stashes what it needs and ``chart_display`` re-renders the labels from
    # these when the user picks a different format. Without this the control
    # had nothing to act on and every table kept its 2-decimal labels.
    _raw_number_cols = ([[row[j] for row in z_values] for j in range(n_cols_p)]
                        + [_val_series.tolist()])

    value_header_label = _VALUE_HEADER_MAP.get(agg, f"{agg.upper()}({vals})")
    col_headers = ([f"<b>{idx}</b>"] + [f"<b>{h}</b>" for h in x_labels]
                   + [f"<b>{value_header_label}</b>"])

    height = max(n_rows * 28 + 200, 480)

    fig = go.Figure(go.Table(
        columnwidth=_col_widths,
        header=dict(
            values=col_headers,
            fill_color=hdr_fills,
            font=dict(color="white", size=12, family="Inter, system-ui, sans-serif"),
            align=["left"] + ["right"] * (n_cols_p + 1),
            line_color="rgba(255,255,255,0.1)",
            height=34,
        ),
        cells=dict(
            values=_data_cells,
            fill_color=cell_fills_by_col,
            font=dict(color="#f1f5f9", size=11, family="Inter, system-ui, sans-serif"),
            align=["left"] + ["right"] * (n_cols_p + 1),
            line_color="rgba(255,255,255,0.05)",
            height=26,
        ),
    ))

    _layout = chart_layout(height=height)
    _layout["margin"] = dict(l=10, r=10, t=58, b=10)
    fig.update_layout(
        **_layout,
        title=dict(text=base_title, font=dict(color="#e2e8f0", size=13)),
    )
    fig._lytrize_meta = {
        "analysis_type": "matrix_table",
        "x_axis": cols, "y_axis": idx, "legend": None,
        "supports_axis_editing": True,
        "matrix_view": "table",
        # Consumed once by chart_display's table pass; see above.
        "table_index_labels": list(y_labels),
        "table_raw_number_cols": _raw_number_cols,
    }
    return [(f"Matrix Table: {idx} × {cols}", fig)]