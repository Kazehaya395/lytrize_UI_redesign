"""services/connector_plugins.py -- drop-in database connector plugins.

The user-facing contract for a Lytrize add-on connector. One ``.py`` file in a
plugin directory is one connector. No setup.py, no package, no edits to core:

* **Where**: plugins are discovered in, in order —
  1. ``<repo>/plugins/`` (bundled with the app; the reference Postgres
     plugin ships here),
  2. ``~/.lytrize/plugins/`` (per-user add-ons),
  3. ``$LYTRIZE_PLUGINS_DIR`` (os.pathsep-separated list; wins on conflicts).

  A later registration of the same ``kind`` overrides an earlier one, with an
  info-level log — so a user file can patch the bundled Postgres connector.

* **What the file defines**: exactly one module-level ``CONNECTOR`` attribute
  of type :class:`ConnectorSpec`. Anything missing, malformed or importable-
  only-sometimes is logged and skipped — one broken add-on never takes the
  app down.

* **What Lytrize guarantees in return**: the background loader
  (``DatabaseLoadWorker``), the "Test connection" probe, credential
  redaction, the row limit, the dataset bridge and the re-open machinery all
  work unchanged for plugin sources. A plugin author only ever writes:
  the spec (name, port, SSL modes, driver hint, URI template) and
  ``make_source`` (almost always a thin subclass of ``SqlSource``).

* **What plugins must not do**: call the network at import time, hold module-
  level connections open, import pandas downstream of Polars, or log
  credentials. The loader imports every plugin on every ``load_connector_
  plugins()`` call — keep imports cheap and lazy (drivers are imported inside
  ``_read``, not at module scope, unless you enjoy slow startups).
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_ENV_VAR = "LYTRIZE_PLUGINS_DIR"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_USER_DIR = Path(os.path.expanduser("~/.lytrize/plugins"))


@dataclass(frozen=True)
class ConnectorSpec:
    """Everything Lytrize needs to render and run one connector.

    Attributes
    ----------
    kind:
        Registry key, lowercase ``[a-z][a-z0-9_-]*``. ``make_source(kind)``
        and the plugin-override rule both use it.
    display_name:
        Label shown in the connection dialog's connector combo.
    uri_template:
        How a **fields**-mode connection becomes a URI. May reference
        ``{auth}`` (``user[:password]@`` or ""; both pre-percent-encoded),
        ``{host}``, ``{port}``, ``{database}`` and ``{sslmode}`` (the SSL
        combo's raw text, or ``default_ssl`` when the dialog hides SSL).
        Ignored when ``compose_uri`` is set.
    default_port:
        Pre-fill for the port spinbox when the connector is selected.
    ssl_modes:
        Choices for the SSL-mode combo. Empty tuple hides the SSL row
        entirely and ``{sslmode}`` expands to ``default_ssl``.
    default_ssl:
        Pre-selected SSL mode (must be a member of ``ssl_modes`` when
        ``ssl_modes`` is non-empty).
    driver_modules:
        Modules that serve as readers for this engine; **any one present**
        means the connector reports "driver available". Empty = the driver
        check always reports OK (e.g. pure-DBAPI plugins managing their own
        connection).
    driver_hint:
        One-line ``pip install ...`` shown when no ``driver_modules`` entry
        is importable. The connection still fails gracefully with this hint.
    factory:
        ``make_source(**kwargs)`` callable registered under ``kind``.
        Receives the kwargs the worker/dialog build: ``uri``, plus one of
        ``table``/``query``, ``limit``, ``engine``.
    compose_uri:
        Optional callable ``(host, port, database, user, password,
        sslmode) -> str`` for engines whose URI shape can't be expressed as
        a template. ``password`` is the **plain** value here; encode it.
    notes:
        Free text shown as a tooltip next to the connector combo.
    """

    kind: str
    display_name: str
    uri_template: str = ""
    default_port: int = 0
    ssl_modes: tuple[str, ...] = ()
    default_ssl: str = ""
    driver_modules: tuple[str, ...] = ()
    driver_hint: str = ""
    factory: Optional[Callable[..., Any]] = field(default=None, repr=False)
    compose_uri: Optional[Callable[..., str]] = field(default=None, repr=False)
    notes: str = ""

    # -- carried plumbing, not for plugin authors ------------------------
    source_path: str = field(default="<built-in>", compare=False)

    def __post_init__(self) -> None:
        if not _KIND_RE.match(str(self.kind or "")):
            raise ValueError(
                f"connector kind {self.kind!r} must match {_KIND_RE.pattern}")
        if not str(self.display_name or "").strip():
            raise ValueError("connector spec needs a non-empty display_name")
        if self.factory is None or not callable(self.factory):
            raise ValueError("connector spec needs a callable factory")
        if not self.compose_uri and not self.uri_template.strip():
            raise ValueError(
                "connector spec needs either uri_template or compose_uri")
        if self.ssl_modes and self.default_ssl and \
                self.default_ssl not in self.ssl_modes:
            raise ValueError(
                f"default_ssl {self.default_ssl!r} is not in ssl_modes "
                f"{self.ssl_modes}")
        if any(ssl and not isinstance(ssl, str) for ssl in self.ssl_modes):
            raise ValueError("ssl_modes entries must be strings")


@dataclass
class PluginLoadResult:
    """Outcome of one discovery pass."""

    specs: list[ConnectorSpec]
    failures: list[tuple[str, str]]          # (file, short reason)
    scanned: list[str]                        # every directory that was scanned

    @property
    def kinds(self) -> list[str]:
        return sorted(spec.kind for spec in self.specs)


def plugin_dirs() -> list[Path]:
    """Discovery directories, lowest to highest precedence."""
    dirs: list[Path] = [_REPO_ROOT / "plugins"]
    dirs.append(_DEFAULT_USER_DIR)
    env = os.environ.get(_ENV_VAR) or ""
    for raw in env.split(os.pathsep):
        raw = raw.strip()
        if raw:
            dirs.append(Path(raw).expanduser())
    unique: list[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def compose_uri(spec: ConnectorSpec, *, host: str, port: int,
                database: str, user: str, password: str,
                sslmode: str) -> str:
    """Build the connection URI for ``spec`` from already-collected fields.

    User/password are percent-encoded here (template path only — a
    ``compose_uri`` callable gets the plain values). ``{auth}`` is the
    encoded userinfo including the trailing ``@``, or "".
    """
    if spec.compose_uri is not None:
        return spec.compose_uri(host=host, port=int(port), database=database,
                                user=user, password=password,
                                sslmode=sslmode)
    from urllib.parse import quote_plus
    user_enc = quote_plus(user) if user else ""
    pass_enc = quote_plus(password) if password else ""
    if user_enc and pass_enc:
        auth = f"{user_enc}:{pass_enc}@"
    elif user_enc:
        auth = f"{user_enc}@"
    else:
        auth = ""
    effective_ssl = sslmode or spec.default_ssl or ""
    return spec.uri_template.format(
        auth=auth, host=host, port=int(port), database=database,
        sslmode=effective_ssl)


def driver_status(spec: ConnectorSpec) -> str:
    """One-line availability summary for ``spec`` (never raises)."""
    if not spec.driver_modules:
        return "reader: managed by the connector itself"
    try:
        import importlib.metadata as md
    except ImportError:  # pragma: no cover
        md = None  # type: ignore[assignment]
    found: list[str] = []
    for module in spec.driver_modules:
        try:
            mod = importlib.import_module(module)
        except Exception:
            continue
        version = getattr(mod, "__version__", "")
        if not version and md is not None:
            try:
                version = md.version(module.replace("_", "-"))
            except Exception:
                version = ""
        found.append(f"{module} {version}".strip())
    if found:
        return "reader available: " + ", ".join(found)
    hint = spec.driver_hint.strip() or f"install a driver for {spec.display_name}"
    return f"no reader installed — {hint}"


def _load_one(path: Path) -> ConnectorSpec:
    name = f"lytrize_connector_{path.stem}_{abs(hash(str(path))) % 10**8}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import plugin file {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # so dataclasses/pickle behave
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    connector = getattr(module, "CONNECTOR", None)
    if connector is None:
        raise ValueError(
            "plugin defines no module-level CONNECTOR (ConnectorSpec)")
    if not isinstance(connector, ConnectorSpec):
        raise ValueError(
            f"CONNECTOR is {type(connector).__name__}, expected ConnectorSpec")
    # frozen dataclass — carry where it came from without mutating the spec
    return ConnectorSpec(**{**connector.__dict__, "source_path": str(path)})


def load_connector_plugins() -> PluginLoadResult:
    """Discover, validate and register every plugin. Never raises.

    Re-registers each factory in ``services.data_source`` and returns the
    specs (for the dialog) plus any per-file failures (for the warning row).
    """
    from services import data_source as ds

    specs: list[ConnectorSpec] = []
    failures: list[tuple[str, str]] = []
    scanned: list[str] = []

    for directory in plugin_dirs():
        scanned.append(str(directory))
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                spec = _load_one(path)
            except Exception as exc:  # noqa: BLE001 — a broken add-on must not kill the app
                log.warning("connector plugin %s failed to load: %s", path, exc)
                failures.append((str(path), str(exc)))
                continue
            try:
                ds.register_source(spec.kind, spec.factory)
            except Exception as exc:  # noqa: BLE001
                log.warning("connector plugin %s failed to register: %s", path, exc)
                failures.append((str(path), f"register: {exc}"))
                continue
            log.info("connector plugin registered kind=%r from %s",
                     spec.kind, path)
            specs.append(spec)
    return PluginLoadResult(specs=specs, failures=failures, scanned=scanned)
