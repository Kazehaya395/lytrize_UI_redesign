"""services/data_source.py -- pluggable data-source foundation.

Why this module exists
----------------------
Lytrize's ingestion path grew as "open a file" (``open_csv`` / ``scan_parquet``
/ the Excel loader) with the source recorded only as a free-text tag on the
``PolarsDataset``. That was fine while files were the only source, but the
whole analytics pipeline downstream -- lazy Polars frames, row IDs, recipes,
KPI re-derivation, the in-file slicer engine -- is already source-agnostic.
Only the *front door* knows what a CSV is.

This module formalizes that front door so the planned database-connection
plugin is a drop-in, not a rewrite:

* Every source is a small object exposing ``lazy_frame()`` (a Polars
  ``LazyFrame`` with Lytrize's ``__ly_row_id`` column), ``fingerprint()``
  (a stable identity string) and ``describe()`` (a UI/logging dict with any
  credentials **redacted**).
* A registry maps ``kind`` -> factory, so a plugin registers its own kind
  (``register_source("postgres", make_postgres_source)``) without touching
  core code. ``make_source(kind, **kwargs)`` is the single entry point.
* Built-in kinds: ``parquet``, ``csv`` and ``sql``. ``sql`` reads through a
  DBAPI2 connection object when one is handed in (the plugin seam), through
  stdlib ``sqlite3`` for ``sqlite:///`` URIs (zero extra dependencies), or
  through ``polars.read_database_uri`` for everything else (which needs the
  driver of the target engine, e.g. connectorx/ADBC -- deliberately NOT a
  hard dependency of Lytrize). All SQL sources accept ``limit=``
  (server-side row cap applied as an outer LIMIT) and ``engine=``
  (``"connectorx"`` or ``"adbc"``).
* **PostgreSQL ships as a drop-in connector plugin**, not as built-in code:
  see ``plugins/postgres.py`` (the reference add-on) and
  ``services.connector_plugins`` (discovery, specs, dialog contract). The
  plugin registers its ``postgres`` kind on load, so ``make_source`` keeps
  working for it everywhere.

Deliberate scope decisions
--------------------------
* ``SqlSource.fingerprint()`` identifies the **view** (uri + query + selected
  columns), not the data. A database can change under an unchanged view; the
  caller decides when to re-open the source. Snapshots for reports are still
  captured by ``session_bundle.save_polars_dataset`` as content-addressed
  Parquet, which is unchanged by any of this.
* Sources never import pandas. If an engine can only be read through pandas
  (``pd.read_sql`` territory), convert exactly once at the boundary with
  ``pl.from_pandas`` inside the plugin -- never let pandas travel downstream.
* ``lazy_frame()`` attaches ``ROW_ID`` the same way ``open_csv`` does, so
  frames from any source behave identically to the file path.

Typical plugin usage (drop-in file, no core edits)::

    # ~/.lytrize/plugins/my_engine.py
    from services.connector_plugins import ConnectorSpec
    from services.data_source import SqlSource

    class MyEngineSource(SqlSource):
        kind = "myengine"

    CONNECTOR = ConnectorSpec(
        kind="myengine", display_name="My Engine",
        uri_template="myengine://{auth}{host}:{port}/{database}",
        default_port=1234,
        factory=lambda **kw: MyEngineSource(**kw))

    # Lytrize discovers this file at startup; no registration calls needed.
    # See plugins/postgres.py for the worked example and
    # docs/WRITING_DATABASE_PLUGINS.md for the full contract.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional, Protocol

try:
    import polars as pl
except ImportError as exc:  # keep importable on pandas-only installs
    pl = None  # type: ignore[assignment]
    _POLARS_IMPORT_ERROR = exc
else:
    _POLARS_IMPORT_ERROR = None

ROW_ID = "__ly_row_id"


class DataSourceError(ValueError):
    """A data source could not be constructed or read."""


def _require_polars() -> None:
    if pl is None:
        raise DataSourceError(
            "polars is required for data sources. "
            "Install the project dependencies from requirements.txt."
        ) from _POLARS_IMPORT_ERROR


def _with_row_id(lazy) -> Any:
    _require_polars()
    names = set(lazy.collect_schema().names())
    row_id = ROW_ID
    while row_id in names:
        row_id = "_" + row_id
    return lazy.with_row_index(row_id, offset=0), row_id


def _redact(text: str) -> str:
    """Strip any ``user:password@`` userinfo from a URI for logs/UI."""
    return re.sub(r"(://[^:/@\s]+):[^@/\s]+@", r"\1:***@", str(text or ""))


class DataSource(Protocol):
    """The seam every source implements (and plugins register)."""

    kind: str
    label: str

    def lazy_frame(self) -> Any:      # pl.LazyFrame with the row-id column
        ...

    def fingerprint(self) -> str:
        ...

    def describe(self) -> dict:
        ...

    def to_dataset(self):
        """Materialise into the engine's PolarsDataset, or raise."""
        ...


def _dataset_from(lazy, *, source: str, revision: str, row_id: str):
    from services.polars_engine import PolarsDataset
    return PolarsDataset(lazy, source, revision, row_id)


def _fingerprint_file(path: Path, tag: str) -> str:
    try:
        stat = path.stat()
        material = f"{tag}:{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError as exc:
        raise DataSourceError(f"Cannot stat data source file {path}: {exc}") from exc
    return f"{tag}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


class ParquetSource:
    """Lazy columnar source. The cheapest possible large-data front door."""

    kind = "parquet"

    def __init__(self, path: str | Path) -> None:
        _require_polars()
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise DataSourceError(f"Parquet file not found: {self.path}")
        self.label = self.path.name

    def lazy_frame(self):
        lazy, row_id = _with_row_id(pl.scan_parquet(self.path))
        self._row_id = row_id
        return lazy

    def fingerprint(self) -> str:
        return _fingerprint_file(self.path, "parquet")

    def describe(self) -> dict:
        return {"kind": self.kind, "label": self.label,
                "uri": str(self.path), "fingerprint": self.fingerprint()}

    def to_dataset(self):
        lazy = self.lazy_frame()
        return _dataset_from(lazy, source=str(self.path),
                             revision=self.fingerprint(), row_id=self._row_id)


class CsvSource:
    """Lazy CSV source; mirrors :func:`services.polars_engine.open_csv`."""

    kind = "csv"

    def __init__(self, path: str | Path, *, separator: str = ",") -> None:
        _require_polars()
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise DataSourceError(f"CSV file not found: {self.path}")
        self.separator = str(separator)
        self.label = self.path.name

    def lazy_frame(self):
        lazy = pl.scan_csv(
            self.path,
            separator=self.separator,
            has_header=True,
            try_parse_dates=True,
            infer_schema_length=10_000,
            low_memory=True,
            rechunk=False,
        )
        lazy, row_id = _with_row_id(lazy)
        self._row_id = row_id
        return lazy

    def fingerprint(self) -> str:
        return _fingerprint_file(self.path, "csv")

    def describe(self) -> dict:
        return {"kind": self.kind, "label": self.label,
                "uri": str(self.path), "fingerprint": self.fingerprint()}

    def to_dataset(self):
        lazy = self.lazy_frame()
        return _dataset_from(lazy, source=str(self.path),
                             revision=self.fingerprint(), row_id=self._row_id)


# Simple identifier: no schema.table games, no injected SQL fragments. Anything
# fancier belongs in an explicit ``query=`` instead of an unescaped table name.
_SIMPLE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


class SqlSource:
    """SQL-backed source: the seam the database-connection plugin plugs into.

    Pass EITHER ``connection`` (any DBAPI2 connection -- the plugin's own, or
    ``sqlite3.connect(...)``), OR ``uri`` with ``query`` (or ``table``).

    * ``sqlite:///path`` URIs use the stdlib driver: zero dependencies.
    * Other URIs go through ``polars.read_database_uri``, which requires the
      engine's driver (connectorx / ADBC). If it is missing, the error says
      exactly what to install -- Lytrize itself stays driver-free.
    * ``table=`` is limited to a simple identifier (optionally
      ``schema.table``); anything else must use ``query=``.
    """

    kind = "sql"

    def __init__(self, *, connection: Any = None, uri: str = "",
                 query: str = "", table: str = "",
                 limit: Optional[int] = None, engine: str = "") -> None:
        _require_polars()
        query = (query or "").strip()
        table = (table or "").strip()
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                raise DataSourceError(
                    f"limit must be a positive integer or None (got {limit!r})."
                ) from None
            if limit < 1:
                raise DataSourceError(
                    f"limit must be a positive integer or None (got {limit!r}).")
        engine = str(engine or "").strip().lower()
        if engine and engine not in ("connectorx", "adbc"):
            raise DataSourceError(
                f"engine must be 'connectorx' or 'adbc' (got {engine!r}).")
        self.limit = limit
        self.engine = engine
        if connection is None and not uri:
            raise DataSourceError("SqlSource needs either a connection or a uri.")
        if query and table:
            raise DataSourceError("Pass either query= or table=, not both.")
        if table:
            parts = [p.strip() for p in table.split(".")]
            if len(parts) > 2:
                raise DataSourceError(
                    f"table={table!r} is not a plain (optionally schema-qualified) "
                    "identifier; use query= for anything more complex.")
            for part in parts:
                if not _SIMPLE_IDENT.match(part):
                    raise DataSourceError(
                        f"table={table!r} is not a plain identifier; "
                        "use query= for anything more complex.")
            query = "SELECT * FROM " + ".".join(_quote_ident(p) for p in parts)
        if not query:
            raise DataSourceError("SqlSource needs a query or a table.")
        self.connection = connection
        self.uri = str(uri or "")
        self.query = query
        self.label = _redact(table or query)[:64]

    # -- internals ------------------------------------------------------
    def effective_query(self) -> str:
        """The query actually executed, with the row-limit wrapper applied."""
        if self.limit:
            return f"SELECT * FROM ({self.query}) AS ly_limited LIMIT {int(self.limit)}"
        return self.query

    def _resolve_sqlite_path(self) -> Optional[Path]:
        raw = self.uri
        m = re.match(r"^sqlite:///(.+)$", raw)
        if not m:
            return None
        target = Path(m.group(1)).expanduser()
        return target if target.is_file() else None

    def _read(self):
        query = self.effective_query()
        if self.connection is not None:
            return pl.read_database(query, self.connection)
        sqlite_path = self._resolve_sqlite_path()
        if sqlite_path is not None:
            conn = sqlite3.connect(str(sqlite_path))
            try:
                return pl.read_database(query, conn)
            finally:
                conn.close()
        try:
            kwargs = {"engine": self.engine} if self.engine else {}
            return pl.read_database_uri(query, self.uri, **kwargs)
        except Exception as exc:
            raise DataSourceError(
                f"Could not read from {_redact(self.uri)}: {exc}. "
                "Install the driver for this engine (e.g. connectorx or an "
                "ADBC driver), or register a custom source with "
                "register_source().") from exc

    # -- DataSource API ---------------------------------------------------
    def lazy_frame(self):
        # SQL has no push-down yet on purpose: materialize the view, then go
        # lazy. This keeps one execution model downstream instead of two.
        eager = self._read()
        lazy, row_id = _with_row_id(eager.lazy())
        self._row_id = row_id
        return lazy

    def fingerprint(self) -> str:
        material = (f"{self.kind}:{self.uri}|{self.query}"
                    f"|limit={self.limit}|engine={self.engine}")
        return (self.kind + ":"
                + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24])

    def describe(self) -> dict:
        described = {"kind": self.kind, "label": self.label,
                     "uri": _redact(self.uri) or "connection",
                     "query": self.effective_query(),
                     "fingerprint": self.fingerprint()}
        if self.engine:
            described["engine"] = self.engine
        if self.limit:
            described["limit"] = self.limit
        return described

    def to_dataset(self):
        lazy = self.lazy_frame()
        source_id = _redact(self.uri) or "connection"
        return _dataset_from(lazy, source=source_id,
                             revision=self.fingerprint(), row_id=self._row_id)


# -- registry -----------------------------------------------------------------
_SOURCE_FACTORIES: dict[str, Any] = {
    "parquet": ParquetSource,
    "csv": CsvSource,
    "sql": SqlSource,
    # "postgres" arrives via the bundled plugins/postgres.py add-on
    # (services.connector_plugins); plugins may override any built-in kind.
}


def register_source(kind: str, factory) -> None:
    """Register ``factory`` under ``kind`` (plugins call this at import time).

    Re-registering a built-in kind is allowed -- a plugin may override the
    default reader -- but it always logs, so an accident is visible.
    """
    kind = str(kind or "").strip().lower()
    if not kind or not callable(factory):
        raise DataSourceError("register_source needs a non-empty kind and a callable factory.")
    if kind in _SOURCE_FACTORIES:
        import logging
        logging.getLogger(__name__).info(
            "data_source: kind %r re-registered (was built-in or previously registered)", kind)
    _SOURCE_FACTORIES[kind] = factory


def make_source(kind: str, **kwargs):
    """Build the source registered under ``kind``."""
    key = str(kind or "").strip().lower()
    factory = _SOURCE_FACTORIES.get(key)
    if factory is None:
        known = ", ".join(sorted(_SOURCE_FACTORIES))
        raise DataSourceError(f"Unknown data source kind {kind!r}. Known: {known}")
    return factory(**kwargs)


def available_sources() -> list[str]:
    """Kinds that can be handed to :func:`make_source` right now."""
    return sorted(_SOURCE_FACTORIES)
