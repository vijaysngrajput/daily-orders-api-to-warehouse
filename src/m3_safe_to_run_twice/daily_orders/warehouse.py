"""The warehouse: a Postgres database, its schemas and tables."""

import psycopg
from psycopg import sql

from daily_orders.config import DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER

HOW_TO_START = (
    "Cannot reach the warehouse. Start it from the project folder:\n"
    "  docker compose up -d warehouse"
)

TABLES = """
CREATE SCHEMA IF NOT EXISTS raw;        -- data exactly as it arrived
CREATE SCHEMA IF NOT EXISTS core;       -- clean tables: one row per real thing
CREATE SCHEMA IF NOT EXISTS analytics;  -- what people read
CREATE SCHEMA IF NOT EXISTS ops;        -- what the pipeline remembers about itself

-- The raw landing table. Append only. It keeps every version, and every repeat.
CREATE TABLE IF NOT EXISTS raw.orders_landing (
    landing_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payload     JSONB NOT NULL,
    run_id      TEXT NOT NULL,
    page_no     INTEGER NOT NULL,
    loaded_at   TIMESTAMPTZ NOT NULL
);

-- The clean orders table. One order is ONE row, with its latest state.
CREATE TABLE IF NOT EXISTS core.orders (
    order_id        BIGINT PRIMARY KEY,          -- the key: one row per order
    customer_id     BIGINT NOT NULL,
    city            TEXT NOT NULL,
    status          TEXT NOT NULL,
    placed_at       TIMESTAMPTZ NOT NULL,
    order_date      DATE NOT NULL,               -- the business day
    item_count      INTEGER NOT NULL,
    total_cents     BIGINT NOT NULL,
    refunded_cents  BIGINT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,        -- the version of the order this row holds
    is_deleted      BOOLEAN NOT NULL DEFAULT false,   -- soft delete: the row stays, marked
    deleted_at      TIMESTAMPTZ,
    first_run_id    TEXT NOT NULL,
    last_run_id     TEXT NOT NULL
);

-- The report table. One row per business day, per city. One day is one partition.
CREATE TABLE IF NOT EXISTS analytics.daily_orders (
    order_date        DATE NOT NULL,
    city              TEXT NOT NULL,
    orders            INTEGER NOT NULL,
    cancelled_orders  INTEGER NOT NULL,
    revenue_cents     BIGINT NOT NULL,
    refunded_cents    BIGINT NOT NULL,
    PRIMARY KEY (order_date, city)
);

-- How far the last run read: the watermark for orders, the position in the change feed.
CREATE TABLE IF NOT EXISTS ops.watermarks (
    source          TEXT PRIMARY KEY,
    watermark       TIMESTAMPTZ,
    position        BIGINT,
    saved_by_run    TEXT NOT NULL
);

-- One row per run: what it did.
CREATE TABLE IF NOT EXISTS ops.load_runs (
    run_id            TEXT PRIMARY KEY,
    run_date          DATE NOT NULL,
    loaded_at         TIMESTAMPTZ NOT NULL,
    asked_since       TIMESTAMPTZ,
    watermark_after   TIMESTAMPTZ,
    rows_landed       INTEGER NOT NULL,
    orders_inserted   INTEGER NOT NULL,
    orders_updated    INTEGER NOT NULL,
    orders_deleted    INTEGER NOT NULL,
    api_calls         INTEGER NOT NULL,
    retries           INTEGER NOT NULL
);

-- Why an earlier day's number moved: one row per order that changed it.
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
    """Open the warehouse, and make its database and tables if they are missing."""
    create_database(database)
    con = _connect(database)
    con.execute(TABLES)
    return con


def reset(con: psycopg.Connection) -> None:
    """Empty the warehouse: forget every load. Used by the lab and the tests to start again."""
    con.execute(
        "TRUNCATE raw.orders_landing RESTART IDENTITY; "
        "TRUNCATE core.orders, analytics.daily_orders, ops.watermarks, ops.load_runs, ops.report_changes;"
    )
