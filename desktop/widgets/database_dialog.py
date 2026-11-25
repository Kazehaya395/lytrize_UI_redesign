"""desktop/widgets/database_dialog.py -- "Connect to database" dialog.

The front end for the database connector add-on framework
(``services.connector_plugins``). The dialog builds a **load spec** — a
plain dict the background worker consumes — and nothing else: no network
traffic happens on this thread except the explicit "Test connection" probe,
which runs in a worker too.

Connectors are drop-in plugins (one ``.py`` file). The combo on this dialog
is populated at construction time by ``load_connector_plugins()``:

* bundled plugins from ``<repo>/plugins/`` (PostgreSQL ships here),
* per-user add-ons from ``~/.lytrize/plugins/``,
* anything in ``$LYTRIZE_PLUGINS_DIR``.

The only built-in entry is "Other SQL (URI)" (kind ``sql``) — the generic
pass-through for engines that don't have (or need) an add-on.

Two ways to describe a connection:

* **Fields** (default) — host, port, database, user, password, SSL mode.
  The URI is composed from the connector's ``uri_template`` at Load/Test
  time; user/password are percent-encoded there and the URI is never shown.
* **URI** — a single line edit for people who already have one. Echoed as
  a password field.

What gets read is either a **table** (plain identifiers, optionally
``schema.table`` — anything else must be a query) or an explicit
**SQL query**. A row limit is pushed into the query as an outer LIMIT so an
exploratory click can never materialise an entire production table.

Drivers (connectorx / ADBC / a DBAPI driver) are NOT hard dependencies of
Lytrize; the dialog reports what the selected connector finds on this
machine and the source raises an actionable error when its driver is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

log = logging.getLogger(__name__)

from services.connector_plugins import (
    ConnectorSpec, PluginLoadResult, compose_uri, driver_status,
    load_connector_plugins,
)

def _core_sql_spec() -> ConnectorSpec:
    """The one built-in entry: generic URI pass-through (kind ``sql``)."""
    return ConnectorSpec(
        kind="sql",
        display_name="Other SQL (URI)",
        uri_template="sqlite:///{host}",   # fields mode hosts a sqlite file path
        default_port=0,
        ssl_modes=(),
        default_ssl="",
        driver_modules=(),
        factory=lambda **kwargs: _make_sqlite_sql(**kwargs),
        notes=("Pass any engine URI Lytrize can read (sqlite:/// works with "
               "zero extra packages). Add connectors as drop-in plugins "
               "instead of fighting this entry."),
    )


def _make_sqlite_sql(**kwargs):
    from services import data_source as ds
    return ds.make_source("sql", **kwargs)


class _TestWorker(QThread):
    """Probe the connection off the UI thread.

    Runs the user's own table/query wrapped in a LIMIT 1 through the real
    front door, so a typo'd table or a broken query fails here -- before
    anything is loaded -- instead of as an empty/garbled report later. The
    probe is cheap on the server side and bounded here by the client collect.
    """

    ok = Signal(float)       # seconds the round trip took
    failed = Signal(str)     # redacted error message

    def __init__(self, spec: dict, parent=None) -> None:
        super().__init__(parent)
        self._spec = dict(spec or {})

    def run(self) -> None:
        import time
        try:
            from services import data_source as ds
            spec = self._spec
            kwargs = {"limit": 1,
                      "engine": spec.get("engine", "")}
            if spec.get("table"):
                kwargs["table"] = spec["table"]
            else:
                kwargs["query"] = spec.get("query", "SELECT 1")
            source = ds.make_source(spec.get("kind", "sql"),
                                    uri=spec.get("uri", ""), **kwargs)
            started = time.monotonic()
            source.lazy_frame().collect()
            self.ok.emit(time.monotonic() - started)
        except Exception as exc:
            from services.data_source import _redact
            log.debug("database probe failed", exc_info=True)
            self.failed.emit(_redact(str(exc)))


class DatabaseDialog(QDialog):
    """Collect a database connection + what to read; return a load spec."""

    DEFAULT_LIMIT = 500_000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect to database")
        self.setMinimumWidth(560)

        self._load = load_connector_plugins()
        self._specs = sorted(self._load.specs,
                             key=lambda s: s.display_name.lower())
        self._specs.append(_core_sql_spec())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # -- Driver availability (selected connector) ------------------------
        self.lbl_driver = QLabel("")
        self.lbl_driver.setWordWrap(True)
        self.lbl_driver.setStyleSheet(
            "font-size: 11px; padding: 6px 8px; border-radius: 4px;"
            " background: palette(alternate-base);")
        root.addWidget(self.lbl_driver)

        if self._load.failures:
            file_list = ", ".join(Path(f).name for f, _ in self._load.failures)
            self.lbl_plugin_failures = QLabel(
                f"⚠ {len(self._load.failures)} add-on(s) failed to load: "
                f"{file_list} — see the application log for details.")
            self.lbl_plugin_failures.setWordWrap(True)
            self.lbl_plugin_failures.setStyleSheet(
                "font-size: 11px; padding: 6px 8px; border-radius: 4px;"
                " background: #7f1d1d; color: #fecaca;")
            self.lbl_plugin_failures.setToolTip(
                "\n".join(f"{f}: {why}" for f, why in self._load.failures))
            root.addWidget(self.lbl_plugin_failures)

        # -- Connector + reader ---------------------------------------------
        kind_row = QHBoxLayout()
        kind_row.addWidget(QLabel("Connector"))
        self.cb_kind = QComboBox()
        for spec in self._specs:
            self.cb_kind.addItem(spec.display_name, spec.kind)
            self.cb_kind.setItemData(
                self.cb_kind.count() - 1, spec.notes or "",
                Qt.ToolTipRole)
        kind_row.addWidget(self.cb_kind, 1)
        kind_row.addWidget(QLabel("Reader"))
        self.cb_engine = QComboBox()
        self.cb_engine.addItem("Auto", "")
        self.cb_engine.addItem("ConnectorX", "connectorx")
        self.cb_engine.addItem("ADBC", "adbc")
        self.cb_engine.setToolTip(
            "How the query is executed for non-sqlite URIs.\n"
            "Auto picks the driver polars prefers (connectorx).")
        kind_row.addWidget(self.cb_engine, 1)
        root.addLayout(kind_row)

        # -- Connection mode: fields vs URI ----------------------------------
        mode_row = QHBoxLayout()
        self.rb_fields = QRadioButton("Connection fields")
        self.rb_uri = QRadioButton("Connection URI")
        self.rb_fields.setChecked(True)
        mode_row.addWidget(self.rb_fields)
        mode_row.addWidget(self.rb_uri)
        mode_row.addStretch()
        root.addLayout(mode_row)

        self.fields_box = QWidget()
        form = QFormLayout(self.fields_box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._form = form
        self.le_host = QLineEdit()
        self.le_host.setPlaceholderText("localhost")
        form.addRow("Host", self.le_host)
        self.sb_port = QSpinBox()
        self.sb_port.setRange(1, 65535)
        self.sb_port.setValue(5432)
        form.addRow("Port", self.sb_port)
        self.le_database = QLineEdit()
        self.le_database.setPlaceholderText("lytrize")
        form.addRow("Database", self.le_database)
        self.le_user = QLineEdit()
        self.le_user.setPlaceholderText("username")
        form.addRow("User", self.le_user)
        self.le_password = QLineEdit()
        self.le_password.setEchoMode(QLineEdit.Password)
        self.le_password.setPlaceholderText("password (never logged)")
        form.addRow("Password", self.le_password)
        self.cb_sslmode = QComboBox()
        form.addRow("SSL mode", self.cb_sslmode)
        self.lbl_sql_note = QLabel(
            "Fields mode builds sqlite:///<file path from Host>.\n"
            "For other engines, use Connection URI instead.")
        self.lbl_sql_note.setStyleSheet("font-size: 10px; opacity: 0.75;")
        self.lbl_sql_note.setVisible(False)
        form.addRow("", self.lbl_sql_note)
        root.addWidget(self.fields_box)

        self.uri_box = QWidget()
        uri_lay = QVBoxLayout(self.uri_box)
        uri_lay.setContentsMargins(0, 0, 0, 0)
        uri_lay.setSpacing(4)
        self.le_uri = QLineEdit()
        self.le_uri.setEchoMode(QLineEdit.Password)
        self.le_uri.setToolTip(
            "The full connection URI. Quotes/encoding are your "
            "responsibility here; use Fields mode for automatic encoding.")
        uri_lay.addWidget(self.le_uri)
        root.addWidget(self.uri_box)
        self.uri_box.setVisible(False)

        self.rb_fields.toggled.connect(self._on_mode_toggled)

        # -- What to read -----------------------------------------------------
        what = QGroupBox("What to read")
        what_lay = QVBoxLayout(what)
        what_lay.setContentsMargins(10, 12, 10, 10)
        what_lay.setSpacing(6)
        src_row = QHBoxLayout()
        self.rb_table = QRadioButton("Table")
        self.rb_query = QRadioButton("SQL query")
        self.rb_table.setChecked(True)
        src_row.addWidget(self.rb_table)
        src_row.addWidget(self.rb_query)
        src_row.addStretch()
        what_lay.addLayout(src_row)
        self.le_table = QLineEdit()
        self.le_table.setPlaceholderText(
            "table or schema.table — plain identifiers only")
        what_lay.addWidget(self.le_table)
        self.te_query = QPlainTextEdit()
        self.te_query.setPlaceholderText(
            "SELECT region, SUM(amount) AS total\n"
            "FROM sales GROUP BY region")
        self.te_query.setFixedHeight(72)
        self.te_query.setVisible(False)
        what_lay.addWidget(self.te_query)
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("Row limit"))
        self.sb_limit = QSpinBox()
        self.sb_limit.setRange(0, 1_000_000_000)
        self.sb_limit.setSingleStep(1000)
        self.sb_limit.setValue(self.DEFAULT_LIMIT)
        self.sb_limit.setGroupSeparatorShown(True)
        self.sb_limit.setToolTip(
            "Applied as an outer LIMIT on the server side.\n"
            "0 means no limit — the whole result is materialised.")
        limit_row.addWidget(self.sb_limit)
        self.lbl_limit_note = QLabel("0 = no limit")
        self.lbl_limit_note.setStyleSheet("font-size: 10px; opacity: 0.7;")
        limit_row.addWidget(self.lbl_limit_note)
        limit_row.addStretch()
        what_lay.addLayout(limit_row)
        root.addWidget(what)
        self.rb_table.toggled.connect(self._on_source_toggled)

        # -- Test connection ---------------------------------------------------
        test_row = QHBoxLayout()
        self.btn_test = QPushButton("Test connection")
        self.btn_test.setProperty("kind", "ghost")
        self.btn_test.setToolTip("Run a 1-row probe through this exact setup.")
        self.btn_test.clicked.connect(self._on_test)
        test_row.addWidget(self.btn_test)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("font-size: 11px;")
        test_row.addWidget(self.lbl_status, 1)
        root.addLayout(test_row)

        # -- Dialog buttons -----------------------------------------------------
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.btn_load = buttons.addButton(
            "Load into Lytrize", QDialogButtonBox.AcceptRole)
        self.btn_load.setProperty("kind", "primary")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._on_load)
        root.addWidget(buttons)

        self._test_worker: Optional[_TestWorker] = None
        self.cb_kind.currentIndexChanged.connect(self._on_connector_changed)
        self._on_connector_changed(0)

    # -- UI state --------------------------------------------------------------
    def _on_mode_toggled(self, fields_on: bool) -> None:
        self.fields_box.setVisible(bool(fields_on))
        self.uri_box.setVisible(not fields_on)

    def _on_source_toggled(self, table_on: bool) -> None:
        self.le_table.setVisible(bool(table_on))
        self.te_query.setVisible(not table_on)

    def _current_spec(self) -> ConnectorSpec:
        return self._specs[self.cb_kind.currentIndex()]

    def _on_connector_changed(self, index: int) -> None:
        spec = self._current_spec()
        is_sql = spec.kind == "sql"

        # port + SSL fields follow the connector's own capabilities
        self._form.setRowVisible(self.sb_port, not is_sql and bool(spec.default_port))
        self._form.setRowVisible(self.le_database, True)
        self._form.setRowVisible(self.le_user, not is_sql)
        self._form.setRowVisible(self.le_password, not is_sql)
        has_ssl = bool(spec.ssl_modes)
        self._form.setRowVisible(self.cb_sslmode, has_ssl)
        self.lbl_sql_note.setVisible(is_sql)
        self.le_host.setPlaceholderText("/path/to/file.db" if is_sql else "localhost")

        if spec.default_port:
            self.sb_port.setValue(int(spec.default_port))

        self.cb_sslmode.blockSignals(True)
        self.cb_sslmode.clear()
        if has_ssl:
            self.cb_sslmode.addItems(list(spec.ssl_modes))
            if spec.default_ssl:
                idx = self.cb_sslmode.findText(spec.default_ssl)
                if idx >= 0:
                    self.cb_sslmode.setCurrentIndex(idx)
        self.cb_sslmode.blockSignals(False)

        if is_sql:
            self.le_uri.setPlaceholderText(
                "postgresql://user:pass@host:5432/db  or  sqlite:///path.db")
        elif spec.uri_template:
            host = self.le_host.text().strip() or "host"
            self.le_uri.setPlaceholderText(
                compose_uri(spec, host=host, port=spec.default_port or 0,
                            database="db", user="user", password="pass",
                            sslmode=self.cb_sslmode.currentText()))
        self.lbl_driver.setText(driver_status(spec))

    # -- Building the load spec -------------------------------------------------

    def _composed_uri(self) -> str:
        if self.rb_uri.isChecked():
            return self.le_uri.text().strip()
        spec = self._current_spec()
        return compose_uri(
            spec,
            host=self.le_host.text().strip(),
            port=int(self.sb_port.value()),
            database=self.le_database.text().strip(),
            user=self.le_user.text().strip(),
            password=self.le_password.text(),
            sslmode=(self.cb_sslmode.currentText()
                     if spec.ssl_modes else spec.default_ssl),
        )

    def _load_spec(self) -> Optional[dict]:
        spec = self._current_spec()
        kind = spec.kind
        uri = self._composed_uri()
        if not uri:
            self._set_status("Connection details are incomplete.", error=True)
            return None
        load_spec: dict = {"kind": kind, "uri": uri,
                           "engine": str(self.cb_engine.currentData() or ""),
                           "limit": int(self.sb_limit.value()) or None}
        if self.rb_table.isChecked():
            table = self.le_table.text().strip()
            if not table:
                self._set_status("Enter a table name.", error=True)
                return None
            load_spec["table"] = table
        else:
            query = self.te_query.toPlainText().strip().rstrip(";").strip()
            if not query:
                self._set_status("Enter a SQL query.", error=True)
                return None
            load_spec["query"] = query
        # Re-use the front door's own validation for identifiers/queries,
        # so the dialog can never hand the worker something the source will
        # reject (or worse, accept something it shouldn't).
        try:
            from services import data_source as ds
            check = {"table": load_spec.get("table"),
                     "query": load_spec.get("query")}
            kwargs = {key: value for key, value in check.items() if value}
            ds.make_source(kind, uri=uri,
                           engine=load_spec["engine"],
                           limit=load_spec["limit"], **kwargs)
        except Exception as exc:
            from services.data_source import _redact
            self._set_status(_redact(str(exc)), error=True)
            return None
        return load_spec

    def load_spec(self) -> Optional[dict]:
        """The validated spec, available after Load is pressed."""
        return getattr(self, "_spec", None)

    # -- Test connection ---------------------------------------------------------

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(
            "font-size: 11px; color: #ef4444;" if error
            else "font-size: 11px; color: #22c55e;")

    def _on_test(self) -> None:
        spec = self._load_spec()
        if spec is None:
            return
        self.btn_test.setEnabled(False)
        self._set_status("Testing…")
        worker = _TestWorker(spec, parent=self)
        worker.ok.connect(self._on_test_ok)
        worker.failed.connect(self._on_test_failed)
        worker.finished.connect(lambda: self.btn_test.setEnabled(True))
        self._test_worker = worker
        worker.start()

    def _on_test_ok(self, seconds: float) -> None:
        self._set_status(f"Connected — round trip {seconds * 1000:.0f} ms.")

    def _on_test_failed(self, message: str) -> None:
        self._set_status(f"Could not connect: {message}", error=True)

    def _on_load(self) -> None:
        spec = self._load_spec()
        if spec is None:
            return
        self._spec = spec
        self.accept()

