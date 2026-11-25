# Writing a database connector for Lytrize

One connector = one `.py` file. Drop it into a plugin directory, restart
Lytrize, and it appears in **Upload → 🗄 From database…** as a choice in the
"Connector" combo. No core edits, no registration calls, no rebuild.

## Where to put the file

Directories are scanned in this order (later wins on conflicts, with a log
line):

1. `<install>/plugins/` — bundled with the app (PostgreSQL ships here).
2. `~/.lytrize/plugins/` — your personal add-ons.
3. `$LYTRIZE_PLUGINS_DIR` — os.pathsep-separated list of directories
   (e.g. `/opt/team-plugins:/opt/experimental`).

Subdirectories are ignored; files starting with `_` are ignored. One file may
define exactly one connector.

## The contract

The file must define a module-level `CONNECTOR` of type
`services.connector_plugins.ConnectorSpec`:

```python
"""MySQL connector — minimal worked example."""

from services.connector_plugins import ConnectorSpec
from services.data_source import SqlSource


class MysqlSource(SqlSource):
    kind = "mysql"


CONNECTOR = ConnectorSpec(
    kind="mysql",                      # registry key; lowercase, short
    display_name="MySQL / MariaDB",    # shown in the dialog combo
    uri_template="mysql://{auth}{host}:{port}/{database}",
    default_port=3306,
    ssl_modes=(),                      # empty => SSL row hidden entirely
    default_ssl="",
    driver_modules=("connectorx", "adbc_driver_mysql"),
    driver_hint="pip install adbc-driver-mysql",
    factory=lambda **kwargs: MysqlSource(**kwargs),
    notes="Reads through connectorx or ADBC.",
)
```

That's the whole plugin. `SqlSource` already gives you: URI/query handling,
identifier quoting for `table=`, the server-side outer `LIMIT` from the row
limit spinbox, credential redaction in errors, `__ly_row_id` handling and the
Polars dataset machinery. Your job is only the engine-specific bits.

### The `uri_template` placeholders (fields mode)

| Placeholder | Replaced with |
|-------------|---------------|
| `{auth}`    | percent-encoded `user[:password]@`, or `""` when no user. Use `{auth}{host}` style — never `user@{host}`. |
| `{host}`    | the Host field, verbatim |
| `{port}`    | the Port spinbox |
| `{database}`| the Database field |
| `{sslmode}` | the SSL combo text, or `default_ssl` when the SSL row is hidden |

`user` and `password` are percent-encoded for you. Need a URI shape the
template can't express (cloud endpoints with query params, custom auth)?
Pass `compose_uri=` instead — a callable
`(host, port, database, user, password, sslmode) -> str`
receiving the **plain** user/password (encode them yourself):

```python
def _compose(host, port, database, user, password, sslmode):
    from urllib.parse import quote
    q = f"?user={quote(user)}&password={quote(password)}" if user else ""
    return f"mycloud://{host}:{port}/{database}{q}"

CONNECTOR = ConnectorSpec(kind="mycloud", display_name="MyCloud DB",
                          uri_template="unused",   # ignored when compose_uri is set
                          default_port=443,
                          compose_uri=_compose,
                          factory=lambda **kw: MyCloudSource(**kw))
```

### `driver_modules` / `driver_hint`

Any one of `driver_modules` being importable marks the connector "driver
available" in the dialog's status line; otherwise the hint text is shown.
Empty `driver_modules` means "this connector manages its own reader" (e.g. a
DBAPI plugin that opens its own connection) — the status line says so.

Dependencies of your plugin are **yours** — Lytrize deliberately does not
pin drivers. If your engine needs `psycopg2` or a bespoke client, import it
inside your factory or inside `_read`-equivalent code, never at module
import time unless you want every Lytrize startup to pay for it.

### Source object rules

`factory` is called with the same kwargs everywhere:

* `uri` (composed or raw),
* exactly one of `table` or `query`,
* `limit` (int or `None`) — apply it server-side as an outer `LIMIT` if the
  engine supports it,
* `engine` (`""`, `"connectorx"` or `"adbc"` for the generic SQL readers).

The returned source must implement the `services.data_source.DataSource`
protocol: `lazy_frame()` (Polars `LazyFrame` with `__ly_row_id` attached —
subclassing `SqlSource` does this for you), `fingerprint()` (kind-prefixed,
credential-free), `describe()` (credential-free) and `to_dataset()`.

**Never** return pandas from an engine-specific reader without converting at
exactly one boundary via `pl.from_pandas`, and never hold an open
connection at module scope.

## Failure behavior

A plugin that raises at import, defines no `CONNECTOR`, or fails validation
is **logged and skipped** — the rest of Lytrize keeps working. Lytrize shows
a red banner in the dialog naming the offending file(s). Re-registering a
kind that already exists is allowed (with a log line) — that's how you
override the bundled Postgres connector with your own build.

## Testing your plugin

1. Copy `plugins/postgres.py` (or the example above) to
   `~/.lytrize/plugins/myengine.py`.
2. Restart Lytrize; open **Upload → 🗄 From database…**, select your
   connector — the status line shows whether its driver is importable.
3. Use **Test connection** (probes your own table/query wrapped in `LIMIT 1`).
4. Row limit still applies: the load spec always carries the Limit the user
   sees (default 500,000).

From a development checkout you can also run the smoke suite:

```
QT_QPA_PLATFORM=offscreen python scripts/smoke_test_fixes.py
```

Sections [55] (database source + dialog) and [56] (plugin discovery,
registration, override, failure isolation, redaction) exercise the whole
path. `LYTRIZE_TEST_PG_URI=postgresql://…` enables a real round trip against
a live server, otherwise it is skipped.
