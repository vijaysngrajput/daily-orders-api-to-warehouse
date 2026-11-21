"""Load: write the rows into the warehouse.

The warehouse is one DuckDB file. The load replaces the whole `orders` table.
"""

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

ORDERS_COLUMNS_SQL = """
    order_id        BIGINT PRIMARY KEY,
    customer_id     BIGINT NOT NULL,
    city            VARCHAR NOT NULL,
    status          VARCHAR NOT NULL,
    placed_at       TIMESTAMPTZ NOT NULL,   -- event time, UTC
    order_date      DATE NOT NULL,          -- the business day
    item_count      INTEGER NOT NULL,
    total_cents     BIGINT NOT NULL,
    refunded_cents  BIGINT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    loaded_at       TIMESTAMPTZ NOT NULL,   -- load time
    run_date        DATE NOT NULL           -- the morning this load was for
"""


def connect(path: Path | str) -> duckdb.DuckDBPyConnection:
    """Open the warehouse, and make its tables if they are missing."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"CREATE TABLE IF NOT EXISTS orders ({ORDERS_COLUMNS_SQL})")
    con.execute(f"CREATE TABLE IF NOT EXISTS orders_previous ({ORDERS_COLUMNS_SQL})")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS load_log (
            run_date       DATE PRIMARY KEY,
            loaded_at      TIMESTAMPTZ NOT NULL,
            orders_loaded  INTEGER NOT NULL,
            api_calls      INTEGER NOT NULL
        )
        """
    )
    return con


def loaded_run_date(con: duckdb.DuckDBPyConnection, table: str = "orders") -> date | None:
    """The morning the table was loaded for. None when it is empty."""
    return con.execute(f"SELECT max(run_date) FROM {table}").fetchall()[0][0]


def replace_orders(
    con: duckdb.DuckDBPyConnection, table: pd.DataFrame, run_date: date, api_calls: int
) -> None:
    """Replace `orders` with the new rows, in one transaction.

    On a new morning, the old table is first kept as `orders_previous`.
    Running the same morning again does not touch `orders_previous`.
    """
    con.register("incoming", table)
    con.execute("BEGIN")
    try:
        current = loaded_run_date(con)
        if current is not None and current != run_date:
            con.execute("DELETE FROM orders_previous")
            con.execute("INSERT INTO orders_previous SELECT * FROM orders")
        con.execute("DELETE FROM orders")
        con.execute("INSERT INTO orders SELECT * FROM incoming")
        con.execute("DELETE FROM load_log WHERE run_date = ?", [run_date])
        con.execute(
            "INSERT INTO load_log SELECT ?, max(loaded_at), count(*), ? FROM incoming",
            [run_date, api_calls],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.unregister("incoming")
