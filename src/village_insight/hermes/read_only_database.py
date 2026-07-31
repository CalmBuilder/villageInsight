from __future__ import annotations

from functools import lru_cache

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

HERMES_READER_ROLE = "village_insight_hermes_reader"
HERMES_READABLE_TABLES = (
    "dataset_records",
    "record_index_values",
    "record_value_lineage",
    "ingestion_items",
    "administrative_units",
    "semantic_fields",
    "semantic_field_versions",
)


@lru_cache(maxsize=4)
def ensure_hermes_readonly_database_url(database_url: str) -> str:
    """Provision the native Hermes code sandbox with a read-only DB login."""

    application_url = make_url(database_url)
    if application_url.get_backend_name() != "postgresql":
        return database_url
    password = application_url.password
    if not password:
        raise RuntimeError("database password is required for Hermes read-only access")

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": HERMES_READER_ROLE},
            ).scalar_one_or_none()
            driver_connection = connection.connection.driver_connection
            if driver_connection is None:
                raise RuntimeError("PostgreSQL driver connection is unavailable")
            with driver_connection.cursor() as cursor:
                if exists is None:
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOINHERIT PASSWORD {}"
                        ).format(
                            sql.Identifier(HERMES_READER_ROLE),
                            sql.Literal(password),
                        )
                    )
                else:
                    cursor.execute(
                        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                            sql.Identifier(HERMES_READER_ROLE),
                            sql.Literal(password),
                        )
                    )
            connection.exec_driver_sql(
                f"ALTER ROLE {HERMES_READER_ROLE} "
                "SET default_transaction_read_only = on"
            )
            connection.exec_driver_sql(
                f"ALTER ROLE {HERMES_READER_ROLE} "
                "SET statement_timeout = '60s'"
            )
            connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE "
                f'"{application_url.database}" TO {HERMES_READER_ROLE}'
            )
            connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA public TO {HERMES_READER_ROLE}"
            )
            tables = ", ".join(
                f'public."{table}"' for table in HERMES_READABLE_TABLES
            )
            connection.exec_driver_sql(
                f"GRANT SELECT ON {tables} TO {HERMES_READER_ROLE}"
            )
    finally:
        engine.dispose()

    readonly_url = application_url.set(
        drivername="postgresql",
        username=HERMES_READER_ROLE,
        password=password,
    )
    return readonly_url.render_as_string(hide_password=False)
