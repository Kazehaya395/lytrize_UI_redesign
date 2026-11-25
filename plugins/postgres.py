"""PostgreSQL connector — the reference add-on.

Copy this file to ``~/.lytrize/plugins/postgres.py`` to modify or override
the built-in PostgreSQL connector, or to ``MyConnector.py`` as the starting
point for an entirely new engine. Everything Lytrize knows about connecting
to PostgreSQL comes from this one file — see
``docs/WRITING_DATABASE_PLUGINS.md`` for the full contract.

What it talks to: any PostgreSQL-backed server. reads happen through
``polars.read_database_uri`` with either connectorx (``pip install
connectorx``) or an ADBC driver (``pip install adbc-driver-postgresql``);
Lytrize itself does not pin either.
"""

from __future__ import annotations

from services.connector_plugins import ConnectorSpec
from services.data_source import SqlSource


class PostgresSource(SqlSource):
    """PostgreSQL front door.

    Deliberately a thin specialisation of :class:`SqlSource`: the lazy-row-id
    machinery, the identifier validation, the credential redaction and the
    connectorx/ADBC driver error all behave identically for every SQL-style
    connector. What is specific to Postgres lives in the driver you install,
    not in Lytrize.
    """

    kind = "postgres"


CONNECTOR = ConnectorSpec(
    kind="postgres",
    display_name="PostgreSQL",
    # {auth} = percent-encoded "user[:password]@" (or "") — positions are
    # always right for libpq-style URIs.
    uri_template="postgresql://{auth}{host}:{port}/{database}?sslmode={sslmode}",
    default_port=5432,
    ssl_modes=("prefer", "require", "disable"),
    default_ssl="prefer",
    driver_modules=("connectorx", "adbc_driver_postgresql"),
    driver_hint="pip install connectorx   (or: pip install adbc-driver-postgresql)",
    factory=lambda **kwargs: PostgresSource(**kwargs),
    notes=("Reads through connectorx or an ADBC driver installed on this "
           "machine; row limit is applied server-side as an outer LIMIT."),
)
