"""Load: land the orders in the raw table, and move the watermark.

The orders and the new watermark are written in ONE transaction.
Either both are saved, or neither is. So the watermark can never run ahead of the data.
"""

from datetime import date, datetime

import psycopg
from psycopg.types.json import Jsonb

SOURCE = "orders"


def to_api_text(moment: datetime | None) -> str | None:
    """A timestamp as the API wants it: 2026-06-08T03:48:53Z"""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def read_watermark(con: psycopg.Connection) -> datetime | None:
    """The largest `updated_at` already loaded. None before the first load."""
    row = con.execute("SELECT watermark FROM ops.watermarks WHERE source = %s", [SOURCE]).fetchone()
    return row[0] if row else None


def next_run_id(con: psycopg.Connection, run_date: date) -> str:
    """A readable name for this run: the morning, and a counter. 2026-06-09_2 is the second run."""
    done = con.execute(
        "SELECT count(*) FROM ops.load_runs WHERE run_date = %s", [run_date]
    ).fetchone()[0]
    return f"{run_date.isoformat()}_{done + 1}"


def land(
    con: psycopg.Connection,
    orders: list[dict],
    pages: list[int],
    run_id: str,
    run_date: date,
    loaded_at: datetime,
    api_calls: int,
    retries: int,
) -> datetime | None:
    """Append the orders to the landing table, then save the new watermark. One transaction.

    Returns the new watermark.
    """
    before = read_watermark(con)
    try:
        with con.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO raw.orders_landing (payload, run_id, page_no, loaded_at) VALUES (%s, %s, %s, %s)",
                [(Jsonb(order), run_id, page, loaded_at) for order, page in zip(orders, pages)],
            )
        # Save the largest updated_at we read. Not the time the job ran.
        newest = max((order["updated_at"] for order in orders), default=None)
        after = before
        if newest is not None:
            after = con.execute(
                """
                INSERT INTO ops.watermarks (source, watermark, saved_by_run)
                VALUES (%s, %s, %s)
                ON CONFLICT (source) DO UPDATE
                    SET watermark = greatest(ops.watermarks.watermark, EXCLUDED.watermark),
                        saved_by_run = EXCLUDED.saved_by_run
                RETURNING watermark
                """,
                [SOURCE, newest, run_id],
            ).fetchone()[0]
        con.execute(
            "INSERT INTO ops.load_runs VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [run_id, run_date, loaded_at, before, after, len(orders), api_calls, retries],
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    return after
