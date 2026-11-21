"""The warehouse: a Postgres database. Python makes the raw and ops tables. dbt makes the rest."""

import psycopg
from psycopg import sql

from daily_orders.config import DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER

HOW_TO_START = (
    "Cannot reach the warehouse. Start it from the project folder:\n"
    "  docker compose up -d warehouse"
)

DBT_SCHEMAS = ("staging", "core", "marts")  # built by dbt, never by Python

TABLES = """
CREATE SCHEMA IF NOT EXISTS raw;   -- data exactly as it arrived. Python loads it. dbt only reads it
CREATE SCHEMA IF NOT EXISTS ops;   -- what the pipeline remembers about itself

-- Every order the API sent, every time it sent it. Append only.
CREATE TABLE IF NOT EXISTS raw.orders_landing (
    landing_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payload     JSONB NOT NULL,
    run_id      TEXT NOT NULL,
    page_no     INTEGER NOT NULL,
    loaded_at   TIMESTAMPTZ NOT NULL
);

-- Every delete read from the order system's change feed. Append only.
CREATE TABLE IF NOT EXISTS raw.order_deletes (
    delete_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    BIGINT NOT NULL,
    deleted_at  TIMESTAMPTZ NOT NULL,
    feed_pos    BIGINT NOT NULL,
    run_id      TEXT NOT NULL,
    loaded_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.watermarks (
    source          TEXT PRIMARY KEY,
    watermark       TIMESTAMPTZ,
    position        BIGINT,
    saved_by_run    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.load_runs (
    run_id            TEXT PRIMARY KEY,
    run_date          DATE NOT NULL,
    loaded_at         TIMESTAMPTZ NOT NULL,
    asked_since       TIMESTAMPTZ,
    watermark_after   TIMESTAMPTZ,
    rows_landed       INTEGER NOT NULL,
    deletes_landed    INTEGER NOT NULL,
    api_calls         INTEGER NOT NULL,
    retries           INTEGER NOT NULL,
    models_built      INTEGER,          -- filled in after dbt ran
    tests_passed      INTEGER,
    tests_failed      INTEGER
);

-- Every order as the last report that passed its tests counted it.
-- The next run compares against this, to say which orders moved an earlier day.
CREATE TABLE IF NOT EXISTS ops.orders_reported (
    order_id        BIGINT PRIMARY KEY,
    order_date      DATE NOT NULL,
    status          TEXT NOT NULL,
    total_cents     BIGINT NOT NULL,
    refunded_cents  BIGINT NOT NULL,
    is_deleted      BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.report_changes (
    run_id                TEXT NOT NULL,
    order_date            DATE NOT NULL,
    order_id              BIGINT NOT NULL,
    what                  TEXT NOT NULL,
    orders_change         INTEGER NOT NULL,
    revenue_cents_change  BIGINT NOT NULL
);
"""


def _connect(database: str) -> psycopg.Connection:
    try:
        return psycopg.connect(
            host=PG_HOST,
            port=PG_PORT,
            user=PG_USER,
            password=PG_PASSWORD,
            dbname=database,
            autocommit=True,  # each statement is saved at once, unless it is inside `con.transaction()`
            connect_timeout=5,
            options="-c timezone=UTC",
        )
    except psycopg.OperationalError as error:
        if "does not exist" in str(error):
            raise
        raise ConnectionError(HOW_TO_START) from error


def create_database(database: str) -> None:
    """Make the database if it is missing."""
    with _connect("arden") as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", [database]
        ).fetchone()
        if not exists:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))


def drop_database(database: str) -> None:
    with _connect("arden") as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
        )


def connect(database: str = DATABASE) -> psycopg.Connection:
    """Open the warehouse, and make its database and its raw and ops tables if they are missing."""
    create_database(database)
    con = _connect(database)
    con.execute(TABLES)
    return con


def database_of(con: psycopg.Connection) -> str:
    return con.info.dbname


def table_exists(con: psycopg.Connection, name: str) -> bool:
    return con.execute("SELECT to_regclass(%s) IS NOT NULL", [name]).fetchone()[0]


def reset(con: psycopg.Connection) -> None:
    """Empty the warehouse: forget every load, and drop everything dbt built."""
    con.execute(
        "TRUNCATE raw.orders_landing, raw.order_deletes RESTART IDENTITY; "
        "TRUNCATE ops.watermarks, ops.load_runs, ops.report_changes, ops.orders_reported;"
    )
    for schema in DBT_SCHEMAS:
        con.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
