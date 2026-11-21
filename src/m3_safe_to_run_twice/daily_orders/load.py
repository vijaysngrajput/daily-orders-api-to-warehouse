"""Load: land the rows raw, then MERGE them into one row per order.

Every function here expects to run inside one transaction (see pipeline.py).
Every write is safe to repeat: run it twice, and the tables are the same as after one run.
"""

from datetime import date, datetime

import psycopg
from psycopg.types.json import Jsonb

from daily_orders.config import BUSINESS_TZ

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


# Staging: this run's rows as columns, with ONE row per order.
# The dedupe key is order_id. When an order arrived more than once, keep its newest version.
STAGE_SQL = f"""
CREATE TEMP TABLE stage_orders ON COMMIT DROP AS
SELECT DISTINCT ON (order_id) *
FROM (
    SELECT
        landing_id,
        (payload->>'order_id')::BIGINT                                            AS order_id,
        (payload->>'customer_id')::BIGINT                                         AS customer_id,
        payload->>'city'                                                          AS city,
        payload->>'status'                                                        AS status,
        (payload->>'placed_at')::TIMESTAMPTZ                                      AS placed_at,
        ((payload->>'placed_at')::TIMESTAMPTZ AT TIME ZONE '{BUSINESS_TZ}')::DATE AS order_date,
        (SELECT count(*) FROM jsonb_array_elements(payload->'items') AS item
          WHERE item->>'item_status' = 'ok')::INTEGER                             AS item_count,
        (payload->>'total_cents')::BIGINT                                         AS total_cents,
        (payload->>'refunded_cents')::BIGINT                                      AS refunded_cents,
        (payload->>'updated_at')::TIMESTAMPTZ                                     AS updated_at
    FROM raw.orders_landing
    WHERE run_id = %s
) AS landed
ORDER BY order_id, updated_at DESC, landing_id DESC
"""

# What core.orders held for these orders before the merge. Used to explain every change.
BEFORE_SQL = """
CREATE TEMP TABLE before_orders ON COMMIT DROP AS
SELECT t.* FROM core.orders AS t JOIN stage_orders AS s USING (order_id)
"""

# The upsert. One order is one row, however often it arrives.
MERGE_SQL = """
MERGE INTO core.orders AS t
USING stage_orders AS s
   ON t.order_id = s.order_id
WHEN MATCHED AND s.updated_at > t.updated_at THEN          -- a newer version: update the row
    UPDATE SET status         = s.status,
               item_count     = s.item_count,
               total_cents    = s.total_cents,
               refunded_cents = s.refunded_cents,
               updated_at     = s.updated_at,
               last_run_id    = %(run_id)s
WHEN NOT MATCHED THEN                                       -- a new order: insert a row
    INSERT (order_id, customer_id, city, status, placed_at, order_date, item_count,
            total_cents, refunded_cents, updated_at, first_run_id, last_run_id)
    VALUES (s.order_id, s.customer_id, s.city, s.status, s.placed_at, s.order_date, s.item_count,
            s.total_cents, s.refunded_cents, s.updated_at, %(run_id)s, %(run_id)s)
"""
# A version that is the same or older matches neither rule. The row is left alone.


def merge_orders(con: psycopg.Connection, run_id: str) -> tuple[int, int, list[date]]:
    """Merge this run's landed rows into core.orders.

    Returns the orders inserted, the orders updated, and the business days they belong to.
    """
    con.execute(STAGE_SQL, [run_id])
    con.execute(BEFORE_SQL)
    con.execute(MERGE_SQL, {"run_id": run_id})
    inserted, updated = con.execute(
        "SELECT count(*) FILTER (WHERE first_run_id = %(r)s), "
        "       count(*) FILTER (WHERE last_run_id = %(r)s AND first_run_id <> %(r)s) "
        "FROM core.orders WHERE last_run_id = %(r)s",
        {"r": run_id},
    ).fetchone()
    days = [
        row[0] for row in con.execute("SELECT DISTINCT order_date FROM stage_orders ORDER BY 1")
    ]
    return inserted, updated, days


def soft_delete(con: psycopg.Connection, deletes: list[dict], run_id: str) -> list[date]:
    """Mark deleted orders. The row stays, so we can still say what was deleted, and when.

    Returns the business days of the orders that were marked now.
    """
    if not deletes:
        return []
    rows = con.execute(
        """
        UPDATE core.orders AS t
           SET is_deleted = true, deleted_at = d.deleted_at, last_run_id = %s
          FROM unnest(%s::BIGINT[], %s::TIMESTAMPTZ[]) AS d(order_id, deleted_at)
         WHERE t.order_id = d.order_id AND NOT t.is_deleted          -- marking twice changes nothing
        RETURNING t.order_date, t.order_id, t.status, t.total_cents - t.refunded_cents
        """,
        [run_id, [d["order_id"] for d in deletes], [d["changed_at"] for d in deletes]],
    ).fetchall()
    for day, order_id, status, money in rows:
        counted = status != "cancelled"
        con.execute(
            "INSERT INTO ops.report_changes VALUES (%s, %s, %s, %s, %s, %s)",
            [
                run_id,
                day,
                order_id,
                "deleted from the order system",
                -1 if counted else 0,
                -money if counted else 0,
            ],
        )
    return [row[0] for row in rows]


# Orders this run changed in a way the report can see, with the size of the change.
EXPLAIN_SQL = """
INSERT INTO ops.report_changes
SELECT %(run_id)s, now.order_date, now.order_id,
       CASE
           WHEN now.status = 'cancelled' AND old.status <> 'cancelled' THEN 'cancelled'
           WHEN now.refunded_cents <> old.refunded_cents
               THEN 'refund of ' || to_char((now.refunded_cents - old.refunded_cents) / 100.0, 'FM$999,990.00')
           ELSE 'total changed from ' || to_char(old.total_cents / 100.0, 'FM$999,990.00')
                || ' to ' || to_char(now.total_cents / 100.0, 'FM$999,990.00')
       END,
       (now.status <> 'cancelled')::INT - (old.status <> 'cancelled')::INT,
       CASE WHEN now.status <> 'cancelled' THEN now.total_cents - now.refunded_cents ELSE 0 END
     - CASE WHEN old.status <> 'cancelled' THEN old.total_cents - old.refunded_cents ELSE 0 END
FROM core.orders AS now
JOIN before_orders AS old USING (order_id)
WHERE now.last_run_id = %(run_id)s
  AND now.order_date < %(first_new_day)s          -- only days an earlier report already showed
  AND ((now.status = 'cancelled') <> (old.status = 'cancelled')
       OR now.total_cents <> old.total_cents
       OR now.refunded_cents <> old.refunded_cents)
"""


def explain_changes(
    con: psycopg.Connection, run_id: str, run_date: date, previous_run_date: date | None
) -> None:
    """Write down which orders changed an earlier day's number, and by how much."""
    if previous_run_date is None or previous_run_date >= run_date:
        return
    # The last report showed days up to the day before it ran.
    con.execute(EXPLAIN_SQL, {"run_id": run_id, "first_new_day": previous_run_date})


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
