"""The warehouse: a Postgres database, its schemas, tables and views."""

import psycopg
from psycopg import sql

from daily_orders.config import BUSINESS_TZ, DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER

HOW_TO_START = (
    "Cannot reach the warehouse. Start it from the project folder:\n"
    "  docker compose up -d warehouse"
)

TABLES = f"""
CREATE SCHEMA IF NOT EXISTS raw;        -- data exactly as it arrived
CREATE SCHEMA IF NOT EXISTS ops;        -- what the pipeline remembers about itself
CREATE SCHEMA IF NOT EXISTS analytics;  -- what people read

-- The raw landing table. One row for every order the API sent, every time it sent it.
-- Rows are only added. Nothing here is ever changed or deleted.
CREATE TABLE IF NOT EXISTS raw.orders_landing (
    landing_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payload     JSONB NOT NULL,          -- the order, exactly as received
    run_id      TEXT NOT NULL,           -- load metadata: which run loaded it
    page_no     INTEGER NOT NULL,        -- load metadata: on which page it arrived
    loaded_at   TIMESTAMPTZ NOT NULL     -- load metadata: when it was loaded
);

-- The watermark: how far the last run read.
CREATE TABLE IF NOT EXISTS ops.watermarks (
    source          TEXT PRIMARY KEY,
    watermark       TIMESTAMPTZ NOT NULL,   -- the largest updated_at already loaded
    saved_by_run    TEXT NOT NULL
);

-- One row per run: what it did.
CREATE TABLE IF NOT EXISTS ops.load_runs (
    run_id            TEXT PRIMARY KEY,
    run_date          DATE NOT NULL,
    loaded_at         TIMESTAMPTZ NOT NULL,
    watermark_before  TIMESTAMPTZ,
    watermark_after   TIMESTAMPTZ,
    rows_landed       INTEGER NOT NULL,
    api_calls         INTEGER NOT NULL,
    retries           INTEGER NOT NULL
);

-- The landing table as columns. Still one row per landed row, so a changed order is here twice.
CREATE OR REPLACE VIEW analytics.orders_as_landed AS
SELECT
    landing_id,
    (payload->>'order_id')::BIGINT                                        AS order_id,
    payload->>'city'                                                      AS city,
    payload->>'status'                                                    AS status,
    (payload->>'placed_at')::TIMESTAMPTZ                                  AS placed_at,
    ((payload->>'placed_at')::TIMESTAMPTZ AT TIME ZONE '{BUSINESS_TZ}')::DATE AS order_date,
    (payload->>'total_cents')::BIGINT                                     AS total_cents,
    (payload->>'refunded_cents')::BIGINT                                  AS refunded_cents,
    (payload->>'updated_at')::TIMESTAMPTZ                                 AS updated_at,
    run_id,
    loaded_at
FROM raw.orders_landing;

-- The report. It adds up every landed row. That is what is still wrong in this milestone.
CREATE OR REPLACE VIEW analytics.daily_orders AS
SELECT
    order_date,
    city,
    count(*) FILTER (WHERE status <> 'cancelled')::INTEGER                      AS orders,
    count(*) FILTER (WHERE status = 'cancelled')::INTEGER                       AS cancelled_orders,
    coalesce(sum(total_cents - refunded_cents)
             FILTER (WHERE status <> 'cancelled'), 0)::BIGINT                   AS revenue_cents,
    coalesce(sum(refunded_cents) FILTER (WHERE status <> 'cancelled'), 0)::BIGINT AS refunded_cents
FROM analytics.orders_as_landed
GROUP BY order_date, city;
"""


def _connect(database: str, autocommit: bool = False) -> psycopg.Connection:
    try:
        return psycopg.connect(
            host=PG_HOST,
            port=PG_PORT,
            user=PG_USER,
            password=PG_PASSWORD,
            dbname=database,
            autocommit=autocommit,
            connect_timeout=5,
            options="-c timezone=UTC",
        )
    except psycopg.OperationalError as error:
        if "does not exist" in str(error):
            raise
        raise ConnectionError(HOW_TO_START) from error


def create_database(database: str) -> None:
    """Make the database if it is missing."""
    with _connect("arden", autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", [database]
        ).fetchone()
        if not exists:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))


def drop_database(database: str) -> None:
    with _connect("arden", autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
        )


def connect(database: str = DATABASE) -> psycopg.Connection:
    """Open the warehouse, and make its database and tables if they are missing."""
    create_database(database)
    con = _connect(database)
    con.execute(TABLES)
    con.commit()
    return con


def reset(con: psycopg.Connection) -> None:
    """Empty the warehouse: forget every load. Used by the lab to start again."""
    con.execute(
        "TRUNCATE raw.orders_landing RESTART IDENTITY; TRUNCATE ops.watermarks, ops.load_runs;"
    )
    con.commit()
