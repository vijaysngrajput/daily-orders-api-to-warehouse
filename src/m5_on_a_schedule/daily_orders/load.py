"""Load: land what arrived, exactly as it arrived. That is all Python does to the data now.

The orders, the deletes, the watermark and the run log are written in ONE transaction (see pipeline.py).
Everything after this, cleaning, deduplication, the report, is SQL in the dbt project.
"""

from datetime import date, datetime

import psycopg
from psycopg.types.json import Jsonb

SOURCE = "orders"
FEED = "change_feed"


def to_api_text(moment: datetime | None) -> str | None:
    """A timestamp as the API wants it: 2026-06-08T03:48:53Z"""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def read_watermark(con: psycopg.Connection) -> datetime | None:
    """The largest `updated_at` already loaded. None before the first load."""
    row = con.execute("SELECT watermark FROM ops.watermarks WHERE source = %s", [SOURCE]).fetchone()
    return row[0] if row else None


def read_feed_position(con: psycopg.Connection) -> int:
    """How far the change feed was read. 0 before the first load."""
    row = con.execute("SELECT position FROM ops.watermarks WHERE source = %s", [FEED]).fetchone()
    return row[0] if row else 0


def next_run_id(con: psycopg.Connection, run_date: date) -> str:
    """A readable name for this run: the morning, and a counter. 2026-06-09_2 is the second run."""
    done = con.execute(
        "SELECT count(*) FROM ops.load_runs WHERE run_date = %s", [run_date]
    ).fetchone()[0]
    return f"{run_date.isoformat()}_{done + 1}"


def land(
    con: psycopg.Connection, orders: list[dict], pages: list[int], run_id: str, loaded_at: datetime
) -> None:
    """Append the orders to the raw landing table, exactly as received."""
    with con.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO raw.orders_landing (payload, run_id, page_no, loaded_at) VALUES (%s, %s, %s, %s)",
            [(Jsonb(order), run_id, page, loaded_at) for order, page in zip(orders, pages)],
        )


def land_deletes(
    con: psycopg.Connection, deletes: list[dict], run_id: str, loaded_at: datetime
) -> None:
    """Append the deletes read from the change feed."""
    with con.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO raw.order_deletes (order_id, deleted_at, feed_pos, run_id, loaded_at) VALUES (%s, %s, %s, %s, %s)",
            [(d["order_id"], d["changed_at"], d["pos"], run_id, loaded_at) for d in deletes],
        )


def save_progress(
    con: psycopg.Connection, run_id: str, newest_updated_at: str | None, feed_position: int
) -> datetime | None:
    """Move the watermark and the feed position. Neither ever moves back."""
    if newest_updated_at is not None:
        con.execute(
            """
            INSERT INTO ops.watermarks (source, watermark, saved_by_run) VALUES (%s, %s, %s)
            ON CONFLICT (source) DO UPDATE
                SET watermark = greatest(ops.watermarks.watermark, EXCLUDED.watermark),
                    saved_by_run = EXCLUDED.saved_by_run
            """,
            [SOURCE, newest_updated_at, run_id],
        )
    con.execute(
        """
        INSERT INTO ops.watermarks (source, position, saved_by_run) VALUES (%s, %s, %s)
        ON CONFLICT (source) DO UPDATE
            SET position = greatest(ops.watermarks.position, EXCLUDED.position),
                saved_by_run = EXCLUDED.saved_by_run
        """,
        [FEED, feed_position, run_id],
    )
    return read_watermark(con)
