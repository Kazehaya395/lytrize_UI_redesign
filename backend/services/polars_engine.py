"""Polars-backed dataset primitives for the staged pandas migration.

This module is intentionally isolated from the current pandas session path.
It establishes the semantics needed by the pilot (stable source row IDs,
schema roles, filters, safe expression KPIs, period comparisons, and chart
data materialization) without converting an entire dataset back to pandas.
UI adapters should materialize only the rows/columns they actually display.
"""

from __future__ import annotations

import ast
import datetime as _dt
import hashlib
import math
import operator
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import polars as pl
except ImportError as exc:  # Keep the existing app importable before rollout.
    pl = None
    _POLARS_IMPORT_ERROR = exc
else:
    _POLARS_IMPORT_ERROR = None

ROW_ID = "__ly_row_id"
ENGINE_VERSION = 1


class PolarsEngineError(ValueError):
    """An input or operation is unsupported by the Polars data path."""


def _require_polars():
    if pl is None:
        raise RuntimeError(
            "Polars is not installed. Install the pinned project dependencies "
            "from requirements.txt to use the Polars pilot."
        ) from _POLARS_IMPORT_ERROR


@dataclass(frozen=True)
class PolarsDataset:
    """A lazy CSV dataset and the identity of the source snapshot."""

    frame: Any  # polars.LazyFrame; kept Any so pandas-only installs can import us.
    source: str
    revision: str
    row_id: str = ROW_ID

    @property
    def schema(self) -> dict:
        _require_polars()
        return dict(self.frame.collect_schema())

    @property
    def columns(self) -> list[str]:
        """Expose schema names for recipe/stale-column validation adapters."""
        return list(self.schema)

    @classmethod
    def from_pandas(cls, frame, *, source: str = "compatibility-boundary",
                    revision: Optional[str] = None) -> "PolarsDataset":
        """Create a lazy Polars snapshot at an explicit legacy boundary.

        New ingestion should call :func:`open_csv` directly. This bridge keeps
        the first native chart slice usable while the older Qt table and
        cleaning pages still exchange pandas frames.
        """
        _require_polars()
        if frame is None:
            raise PolarsEngineError("Cannot create a Polars dataset from None")
        eager = pl.from_pandas(frame, include_index=False)
        row_id = ROW_ID
        names = set(eager.columns)
        while row_id in names:
            row_id = "_" + row_id
        eager = eager.with_row_index(row_id, offset=0)
        if revision is None:
            revision = hashlib.sha256(
                f"{id(frame)}:{frame.shape}:{tuple(frame.columns)}".encode()
            ).hexdigest()
        return cls(eager.lazy(), str(source), str(revision), row_id)

    def roles(self) -> dict[str, list[str]]:
        """Classify columns from the inferred Polars schema, preserving order."""
        _require_polars()
        numeric, categorical, temporal, boolean = [], [], [], []
        for name, dtype in self.schema.items():
            if name == self.row_id:
                continue
            if dtype == pl.Boolean:
                boolean.append(name)
            elif dtype in (pl.Date, pl.Datetime, pl.Time):
                temporal.append(name)
            elif dtype.is_numeric():
                numeric.append(name)
            else:
                categorical.append(name)
        return {"numeric": numeric, "categorical": categorical,
                "datetime": temporal, "boolean": boolean,
                "dimensions": categorical + temporal + boolean}

    def filter(self, column: str, operator_name: str, value: Any = None,
               upper: Any = None) -> "PolarsDataset":
        """Return a lazy filtered view; supported ops: eq, ne, in, gt/ge/lt/le,
        between, is_null and is_not_null. Null checks need no value.
        """
        _require_polars()
        if column not in self.schema or column == self.row_id:
            raise PolarsEngineError(f"Unknown or reserved filter column: {column}")
        expr = pl.col(column)
        if operator_name == "eq":
            predicate = expr == pl.lit(value)
        elif operator_name == "ne":
            predicate = expr != pl.lit(value)
        elif operator_name == "in":
            if not isinstance(value, (list, tuple, set)):
                raise PolarsEngineError("The 'in' filter requires a list of values")
            predicate = expr.is_in(list(value))
        elif operator_name in {"gt", "ge", "lt", "le"}:
            ops = {"gt": operator.gt, "ge": operator.ge,
                   "lt": operator.lt, "le": operator.le}
            predicate = ops[operator_name](expr, pl.lit(value))
        elif operator_name == "between":
            predicate = expr.is_between(pl.lit(value), pl.lit(upper), closed="both")
        elif operator_name == "is_null":
            predicate = expr.is_null()
        elif operator_name == "is_not_null":
            predicate = expr.is_not_null()
        else:
            raise PolarsEngineError(f"Unsupported filter operation: {operator_name}")
        return PolarsDataset(self.frame.filter(predicate), self.source,
                             self.revision, self.row_id)

    def apply_filter_specs(self, specs) -> "PolarsDataset":
        """Apply the shared slicer filter-spec shape without collecting rows.

        Ordinary membership/range/search filters remain lazy. ``top_n`` must
        first rank rows, so it collects only the selected stable row IDs and
        applies that small ID set to the original lazy query.
        """
        _require_polars()
        specs = [spec for spec in (specs or []) if isinstance(spec, dict)]
        inclusions = [s for s in specs
                      if str(s.get("kind") or "").lower() != "not_in"]
        exclusions = [s for s in specs
                      if str(s.get("kind") or "").lower() == "not_in"]
        result = self
        for spec in inclusions + exclusions:
            kind = str(spec.get("kind") or "").strip().lower()
            column = spec.get("column")
            schema = result.schema
            if kind not in {"in", "not_in", "range", "search", "top_n"} \
                    or not column or column not in schema or column == result.row_id:
                continue
            expr = pl.col(column)
            if kind in {"in", "not_in"}:
                values = spec.get("values")
                if values is None:
                    values = []
                elif not isinstance(values, (list, tuple, set)):
                    values = [values]
                if not values:
                    continue
                values = list(values)
                includes_null = any(value is None for value in values)
                predicate = expr.is_in([value for value in values if value is not None])
                predicate = predicate.fill_null(False)
                if includes_null:
                    predicate = predicate | expr.is_null()
                if kind == "not_in":
                    predicate = ~predicate
                result = PolarsDataset(result.frame.filter(predicate),
                                       result.source, result.revision, result.row_id)
            elif kind == "range":
                low, high = spec.get("low"), spec.get("high")
                if low in (None, "") and high in (None, ""):
                    continue
                dtype = schema[column]
                if dtype in (pl.Date, pl.Datetime, pl.Time) or dtype == pl.String:
                    date_expr = _parse_dates(result, column)
                    try:
                        low = pl.Series([low]).str.to_datetime(strict=False).item() if low not in (None, "") else None
                        high = pl.Series([high]).str.to_datetime(strict=False).item() if high not in (None, "") else None
                    except Exception:
                        continue
                    expr = date_expr
                else:
                    expr = expr.cast(pl.Float64, strict=False)
                    try:
                        low = float(low) if low not in (None, "") else None
                        high = float(high) if high not in (None, "") else None
                    except (TypeError, ValueError):
                        continue
                predicate = pl.lit(True)
                if low is not None:
                    predicate = predicate & (expr >= pl.lit(low))
                if high is not None:
                    predicate = predicate & (expr <= pl.lit(high))
                result = PolarsDataset(result.frame.filter(predicate),
                                       result.source, result.revision, result.row_id)
            elif kind == "search":
                needle = spec.get("value")
                if needle in (None, ""):
                    values = spec.get("values")
                    needle = values[0] if isinstance(values, (list, tuple)) and values else values
                if needle in (None, ""):
                    continue
                predicate = (expr.cast(pl.String, strict=False).str.to_lowercase()
                             .str.contains(str(needle).lower(), literal=True))
                result = PolarsDataset(result.frame.filter(predicate),
                                       result.source, result.revision, result.row_id)
            else:  # row-ranking top-N
                try:
                    count = int(spec.get("n", 10) or 10)
                except (TypeError, ValueError):
                    continue
                if count <= 0:
                    continue
                ranked = expr
                if schema[column].is_numeric():
                    ranked = expr.cast(pl.Float64, strict=False)
                    if schema[column].is_float():
                        ranked = ranked.fill_nan(None)
                elif schema[column] == pl.String:
                    ranked = _parse_dates(result, column)
                try:
                    ids = (result.frame.select(pl.col(result.row_id), ranked.alias("__rank"))
                           .sort(["__rank", result.row_id],
                                 descending=[not bool(spec.get("least", False)), False],
                                 nulls_last=True)
                           .head(count).select(result.row_id).collect()
                           .get_column(result.row_id).to_list())
                except Exception:
                    continue
                result = PolarsDataset(result.frame.filter(
                    pl.col(result.row_id).is_in(ids)), result.source,
                    result.revision, result.row_id)
        return result

    def scalar(self, expression: Any) -> Any:
        """Evaluate one scalar Polars expression without materializing the frame."""
        _require_polars()
        return self.frame.select(expression.alias("__value")).collect().item()

    def aggregate(self, dimension: str, metric: Optional[str] = None,
                  operation: str = "count", *, sort: str = "value_desc",
                  top_n: Optional[int] = None, include_other: bool = True,
                  uid: str = "polars-preview", title: Optional[str] = None,
                  recipe: Optional[dict] = None) -> dict:
        """Build a renderer-neutral chart model; the full frame stays lazy."""
        _require_polars()
        schema = self.schema
        if dimension not in schema or dimension == self.row_id:
            raise PolarsEngineError(f"Unknown chart dimension: {dimension}")
        if operation != "count" and (metric not in schema or metric == self.row_id):
            raise PolarsEngineError(f"Unknown chart metric: {metric}")
        if operation == "count":
            agg_exprs = (pl.len() if metric is None else
                         pl.col(metric).count()).alias("__value")
        else:
            agg_exprs = _aggregate_expression(operation, metric).alias("__value")
        # pandas groupby/value_counts in the current analysis services exclude
        # missing dimension values. Keep that rule explicit in the pilot.
        dimension_valid = pl.col(dimension).is_not_null()
        if schema[dimension].is_float():
            dimension_valid = dimension_valid & ~pl.col(dimension).is_nan()
        result = (self.frame.filter(dimension_valid)
                  .group_by(dimension).agg(agg_exprs).collect())
        if sort == "value_desc":
            result = result.sort(["__value", dimension],
                                 descending=[True, False], nulls_last=True)
        elif sort == "value_asc":
            result = result.sort(["__value", dimension],
                                 descending=[False, False], nulls_last=True)
        elif sort == "category_desc":
            result = result.sort(dimension, descending=True, nulls_last=True)
        else:
            result = result.sort(dimension, nulls_last=True)

        if top_n is not None and top_n > 0 and result.height > top_n:
            head = result.head(top_n)
            if include_other:
                rest_value = result.slice(top_n)["__value"].sum()
                rest = pl.DataFrame({dimension: ["Other"], "__value": [rest_value]},
                                    schema_overrides={dimension: pl.String})
                # Cast category values to strings only in this mixed Other case.
                head = head.with_columns(pl.col(dimension).cast(pl.String))
                result = pl.concat([head, rest], how="vertical")
            else:
                result = head

        from modules.chart_model import make_chart_model
        chart_type = "pie_chart" if operation == "count" else "categorical"
        return make_chart_model(
            uid=uid,
            chart_type=chart_type,
            title=title or f"{operation.title()} by {dimension}",
            recipe=recipe or {"version": 1, "config": {
                "x": dimension, "y": metric, "agg": operation,
                "sort": sort, "top_n": top_n,
            }},
            data={
                "dimensions": [dimension],
                "series": [{"id": metric or "count", "name": metric or "Count",
                            "type": "pie" if operation == "count" else "bar",
                            "values": [[_json_scalar(x), _json_scalar(y)]
                                       for x, y in result.iter_rows()]}],
            },
            display={},
            source={"engine": "polars", "revision": self.revision,
                    "row_id": self.row_id},
        )


def _aggregate_expression(operation: str, column: str):
    _require_polars()
    expr = pl.col(column).cast(pl.Float64, strict=False)
    # Polars keeps IEEE NaN separate from null; pandas numeric coercion
    # followed by aggregate operations generally skips both.
    expr = expr.fill_nan(None)
    expressions = {"sum": expr.sum(), "mean": expr.mean(),
                   "median": expr.median(), "min": expr.min(),
                   "max": expr.max(), "count": expr.count()}
    try:
        return expressions[operation]
    except KeyError as exc:
        raise PolarsEngineError(f"Unsupported aggregate: {operation}") from exc


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def open_csv(path: str | Path, *, separator: str = ",",
             schema_overrides: Optional[dict] = None) -> PolarsDataset:
    """Open CSV lazily and attach deterministic source-order row IDs."""
    _require_polars()
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(file_path)
    digest = hashlib.sha256()
    with file_path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    lazy = pl.scan_csv(
        file_path,
        separator=separator,
        has_header=True,
        try_parse_dates=True,
        infer_schema_length=10_000,
        schema_overrides=schema_overrides,
        low_memory=True,
        rechunk=False,
    )
    row_id = ROW_ID
    names = set(lazy.collect_schema().names())
    while row_id in names:
        row_id = "_" + row_id
    lazy = lazy.with_row_index(row_id, offset=0)
    return PolarsDataset(lazy, str(file_path), digest.hexdigest(), row_id)


_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
            ast.Pow: operator.pow}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def compile_numeric_formula(frame: Any, formula: str):
    """Compile the app's safe arithmetic grammar into a Polars expression.

    Numeric column references can be bare identifiers or quoted with
    backticks. Function calls, attributes, indexing, and other Python syntax
    are rejected. This intentionally mirrors the existing transform grammar.
    """
    _require_polars()
    schema = dict(frame.collect_schema()) if hasattr(frame, "collect_schema") else dict(frame.schema)
    aliases: dict[str, str] = {}

    def replace_backtick(match):
        name = match.group(1)
        if name not in schema:
            raise PolarsEngineError(f"Unknown column: {name}")
        alias = f"__ly_col_{len(aliases)}"
        aliases[alias] = name
        return alias

    expression = re.sub(r"`([^`]+)`", replace_backtick, str(formula or "").strip())
    if not expression:
        raise PolarsEngineError("Formula is empty")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise PolarsEngineError("Invalid arithmetic expression") from exc

    def compile_node(node):
        if isinstance(node, ast.Expression):
            return compile_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return pl.lit(node.value)
        if isinstance(node, ast.Name):
            name = aliases.get(node.id, node.id)
            if name not in schema:
                raise PolarsEngineError(f"Unknown column: {name}")
            if not schema[name].is_numeric():
                raise PolarsEngineError(f"Column '{name}' is not numeric")
            expr = pl.col(name).cast(pl.Float64, strict=False)
            return expr.fill_nan(None)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](compile_node(node.left),
                                           compile_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](compile_node(node.operand))
        raise PolarsEngineError(
            "Only arithmetic with numeric columns and constants is allowed"
        )

    return compile_node(tree)


def period_change(dataset: PolarsDataset, date_column: str, value_column: str,
                  period: str = "month") -> Optional[float]:
    """Return latest-vs-previous month/year percent change using Polars.

    Date text is parsed by Polars' non-strict parser; invalid/missing dates are
    dropped. The comparison uses the immediately preceding calendar month or
    year, matching the current KPI service even when an intervening period is
    absent from the dataset.
    """
    _require_polars()
    if period not in {"month", "year"}:
        raise PolarsEngineError("period must be 'month' or 'year'")
    schema = dataset.schema
    for name in (date_column, value_column):
        if name not in schema or name == ROW_ID:
            raise PolarsEngineError(f"Unknown KPI column: {name}")
    dates = _parse_dates(dataset, date_column)
    interval = "1mo" if period == "month" else "1y"
    valid = (dataset.frame.with_columns(dates.alias("__ly_date"))
             .filter(pl.col("__ly_date").is_not_null()))
    latest_date = valid.select(pl.col("__ly_date").max()).collect().item()
    if latest_date is None:
        return None
    latest_period = _dt.datetime(latest_date.year, latest_date.month, 1)
    if period == "month":
        previous_period = (_dt.datetime(latest_date.year - 1, 12, 1)
                           if latest_date.month == 1 else
                           _dt.datetime(latest_date.year, latest_date.month - 1, 1))
    else:
        previous_period = _dt.datetime(latest_date.year - 1, 1, 1)
    values = pl.col(value_column).cast(pl.Float64, strict=False)
    if schema[value_column].is_float():
        values = values.fill_nan(None)
    grouped = (valid.with_columns(pl.col("__ly_date").dt.truncate(interval)
                                 .alias("__period"))
               .group_by("__period")
               .agg(values.sum().alias("__total")).collect())
    totals = {date: total for date, total in grouped.iter_rows()}
    previous = totals.get(previous_period)
    latest = totals.get(latest_period)
    if previous is None or latest is None or previous == 0:
        return None
    return (latest - previous) / abs(previous) * 100


def _parse_dates(dataset: PolarsDataset, column: str):
    dtype = dataset.schema[column]
    if dtype == pl.String:
        raw = pl.col(column).str.strip_chars()
        return pl.coalesce(
            raw.str.to_datetime(strict=False),
            raw.str.to_datetime("%m/%d/%Y", strict=False),
            raw.str.to_datetime("%d/%m/%Y", strict=False),
            raw.str.to_datetime("%m-%d-%Y", strict=False),
            raw.str.to_datetime("%d-%m-%Y", strict=False),
        )
    if dtype == pl.Date:
        return pl.col(column).cast(pl.Datetime)
    return pl.col(column).cast(pl.Datetime, strict=False)


def calc_polars_kpi(dataset: PolarsDataset, kpi_type: str, col: Optional[str] = None,
                    group_col: Optional[str] = None, metric_col: Optional[str] = None,
                    filter_col: Optional[str] = None, filter_val: Any = None,
                    label: Optional[str] = None, formula: str = "",
                    baseline_period: str = "") -> dict:
    """Compute KPI values with Polars, matching ``kpi_service.calc_kpi``'s result shape."""
    _require_polars()
    from services.kpi_service import fmt_num, fmt_pct, kpi_spec

    spec = kpi_spec(kpi_type)
    key = spec["key"]
    recipe = {"kpi_type": kpi_type, "kpi_key": key, "col": col,
              "group_col": group_col, "metric_col": metric_col,
              "filter_col": filter_col, "filter_val": filter_val,
              "label": label, "formula": formula,
              "baseline_period": baseline_period}
    output_label = label or spec["label"]
    prefix = suffix = ""
    value: Any = "--"
    change_pct = None
    schema = dataset.schema

    def require(name: Optional[str], message: str = "Pick a column") -> str:
        if not name or name not in schema or name == dataset.row_id:
            raise PolarsEngineError(message)
        return name

    def number_expr(name: str):
        expr = pl.col(name).cast(pl.Float64, strict=False)
        if schema[name].is_float():
            expr = expr.fill_nan(None)
        return expr

    def total_for(name: str, filtered=None):
        source = filtered or dataset
        return source.scalar(number_expr(name).sum())

    try:
        row_count = int(dataset.frame.select(pl.len()).collect().item())
        if row_count == 0:
            result = ("--", output_label, "", "", None)
        elif key == "count":
            result = (fmt_num(row_count), label or "Total Records", "", "", None)
        elif key in {"sum", "mean", "median", "min", "max", "std"}:
            name = require(col)
            expr = number_expr(name)
            aggregate = {"sum": expr.sum(), "mean": expr.mean(),
                         "median": expr.median(), "min": expr.min(),
                         "max": expr.max(), "std": expr.std()}[key]
            short = {"sum": "Total", "mean": "Avg", "median": "Median",
                     "min": "Min", "max": "Max", "std": "Std dev"}[key]
            result = (fmt_num(dataset.scalar(aggregate)), label or f"{short} {name}", "", "", None)
        elif key in {"count_nonnull", "nunique", "missing"}:
            name = require(col)
            missing_expr = pl.col(name).is_null()
            if schema[name].is_float():
                missing_expr = missing_expr | pl.col(name).is_nan()
            if key == "missing":
                count = int(dataset.scalar(missing_expr.sum()) or 0)
                result = (fmt_num(count), label or f"Missing {name}", "",
                          f" ({count / max(1, row_count) * 100:.1f}%)", None)
            elif key == "nunique":
                count = int(dataset.scalar(pl.col(name).drop_nulls().n_unique()) or 0)
                result = (fmt_num(count), label or f"Unique {name}", "", "", None)
            else:
                count = int(dataset.scalar((~missing_expr).sum()) or 0)
                result = (fmt_num(count), label or f"Count {name}", "", "", None)
        elif key == "date_range":
            name = require(col, "Pick a date column")
            dates = _parse_dates(dataset, name)
            low, high = dataset.frame.select(dates.min(), dates.max()).collect().row(0)
            rendered = (f"{low.strftime('%d %b %y')} → {high.strftime('%d %b %y')}"
                        if low is not None and high is not None else "--")
            result = (rendered, label or f"Range of {name}", "", "", None)
        elif key in {"share", "vs_mean"}:
            name = require(col)
            group = require(group_col, "Pick a category column")
            selected_frame = dataset.frame
            if filter_val is not None and filter_val != "":
                selected_frame = selected_frame.filter(
                    pl.col(group).cast(pl.String) == str(filter_val))
            selected = PolarsDataset(selected_frame, dataset.source,
                                     dataset.revision, dataset.row_id)
            if key == "share":
                whole = total_for(name)
                part = total_for(name, selected)
                pct = 0.0 if not whole else (part or 0.0) / whole * 100
                result = (fmt_pct(pct), label or f"{filter_val} share of {name}", "", "%", None)
            else:
                average = dataset.scalar(number_expr(name).mean())
                selected_average = selected.scalar(number_expr(name).mean())
                if not average or selected_average is None:
                    result = ("--", label or f"{filter_val} vs avg {name}", "", "%", None)
                else:
                    pct = (selected_average - average) / abs(average) * 100
                    result = (fmt_pct(pct), label or f"{filter_val} vs avg {name}", "", "%", float(pct))
        elif key in {"top_cat", "bottom_cat"}:
            group = require(group_col, "Pick a category column")
            metric = require(metric_col, "Pick a metric column")
            grouped = (dataset.frame.select(
                pl.col(group).alias("__group"), number_expr(metric).alias("__value"))
                .drop_nulls().group_by("__group").agg(pl.col("__value").sum())
                .sort(["__value", "__group"], descending=[key == "top_cat", False])
                .limit(1).collect())
            word = "Top" if key == "top_cat" else "Bottom"
            if grouped.height:
                category, amount = grouped.row(0)
                result = (f"{category}: {fmt_num(amount)}", label or f"{word} {group}", "", "", None)
            else:
                result = ("--", label or f"{word} {group}", "", "", None)
        elif key in {"mom", "yoy"}:
            date = require(filter_col, "Pick a date column")
            metric = require(col, "Pick a metric column")
            period = "year" if key == "yoy" else "month"
            change_pct = period_change(dataset, date, metric, period)
            output_label = label or (f"YoY {metric}" if key == "yoy" else f"MoM {metric}")
            result = ("--" if change_pct is None else fmt_pct(change_pct),
                      output_label, "", "%", change_pct)
        elif key == "mom_given":
            date = require(filter_col, "Pick a date column")
            metric = require(col, "Pick a metric column")
            try:
                year_text, month_text = str(baseline_period).strip().split("-", 1)
                year, month = int(year_text), int(month_text)
                if month not in range(1, 13):
                    raise ValueError
                next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
            except (TypeError, ValueError):
                raise PolarsEngineError("Choose a baseline month in YYYY-MM format")
            dates = _parse_dates(dataset, date).alias("__date")
            def month_sum(y, m):
                return dataset.frame.with_columns(dates).filter(
                    (pl.col("__date").dt.year() == y) &
                    (pl.col("__date").dt.month() == m)
                ).select(number_expr(metric).sum()).collect().item()
            current, previous = month_sum(year, month), month_sum(next_year, next_month)
            output_label = label or f"{baseline_period} vs {metric}"
            if current is None or previous is None or previous == 0:
                result = ("--", output_label, "", "%", None)
            else:
                change_pct = (current - previous) / abs(previous) * 100
                result = (fmt_pct(change_pct), output_label, "", "%", float(change_pct))
        elif key == "running":
            date = require(filter_col, "Pick a date column")
            metric = require(col, "Pick a metric column")
            dates = _parse_dates(dataset, date).alias("__date")
            frame = dataset.frame.with_columns(dates).filter(pl.col("__date").is_not_null())
            amount, latest = frame.select(number_expr(metric).sum(), pl.col("__date").max()).collect().row(0)
            result = ("--" if amount is None else fmt_num(amount),
                      label or f"Running total {metric}", "",
                      "" if latest is None else f" to {latest.strftime('%d %b %y')}", None)
        elif key == "custom":
            if not str(formula or "").strip():
                raise PolarsEngineError("Enter a formula, e.g. `Revenue` / `Units`")
            amount = dataset.scalar(compile_numeric_formula(dataset.frame, formula).sum())
            result = (fmt_num(amount), label or "Custom", "", "", None)
        else:
            result = ("--", output_label, "", "", None)
        value, output_label, prefix, suffix, change_pct = result
    except Exception as exc:
        value = "Err: %s" % (str(exc)[:60] or exc.__class__.__name__)

    return {"icon": spec["icon"], "label": output_label, "value": value,
            "prefix": prefix, "suffix": suffix, "change_pct": change_pct,
            "_recipe": recipe}
