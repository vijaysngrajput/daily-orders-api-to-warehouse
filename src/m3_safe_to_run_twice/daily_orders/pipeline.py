"""The whole pipeline for one morning. Safe to run twice."""

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg

from daily_orders.config import API_URL, LOOKBACK_DAYS
from daily_orders.extract import ExtractFailed, fetch_changed_orders, fetch_deletes, read_run_date
from daily_orders.load import (
    explain_changes,
    land,
    merge_orders,
    next_run_id,
    read_feed_position,
    read_watermark,
    save_progress,
    soft_delete,
    to_api_text,
)
from daily_orders.report import format_answer, manager_answer, rebuild_days
from daily_orders.warehouse import connect


@dataclass
class RunResult:
    run_id: str
    run_date: str
    asked_since: str | None
    watermark_after: str | None
    rows_landed: int
    orders_inserted: int
    orders_updated: int
    orders_deleted: int
    days_rebuilt: list[str]
    api_calls: int
    retries: int
    answer: dict


def run(
    client: httpx.Client,
    con: psycopg.Connection,
    sleep: Callable[[float], None] = time.sleep,
    lookback_days: int = LOOKBACK_DAYS,
) -> RunResult:
    """Run the pipeline once, for the morning on the order system's clock."""
    run_date = read_run_date(client)
    watermark = read_watermark(con)
    # Lookback: start a little before the watermark, to catch updates that arrived late.
    # It re-reads rows we already hold. That is safe only because the write is idempotent.
    since = watermark - timedelta(days=lookback_days) if watermark else None

    # 1. Read from the source. If this fails, we stop here and nothing below happens.
    extract = fetch_changed_orders(client, to_api_text(since), sleep)
    deletes, feed_position = fetch_deletes(client, read_feed_position(con))

    # 2. Write. Everything is in ONE transaction: all of it is saved, or none of it.
    previous = con.execute("SELECT max(run_date) FROM ops.load_runs").fetchone()[0]
    with con.transaction():
        run_id = next_run_id(con, run_date)
        land(con, extract.orders, extract.pages, run_id, datetime.now(UTC))
        inserted, updated, days = merge_orders(con, run_id)
        explain_changes(con, run_id, run_date, previous)
        deleted_days = soft_delete(con, deletes, run_id)
        days = sorted(set(days) | set(deleted_days))
        rebuild_days(con, days)
        newest = max((order["updated_at"] for order in extract.orders), default=None)
        after = save_progress(con, run_id, newest, feed_position)
        con.execute(
            "INSERT INTO ops.load_runs VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s)",
            [run_id, run_date, since, after, len(extract.orders), inserted, updated,
             len(deleted_days), extract.api_calls, extract.retries],
        )  # fmt: skip

    return RunResult(
        run_id,
        run_date.isoformat(),
        to_api_text(since),
        to_api_text(after),
        len(extract.orders),
        inserted,
        updated,
        len(deleted_days),
        [day.isoformat() for day in days],
        extract.api_calls,
        extract.retries,
        manager_answer(con, run_date, run_id),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily orders pipeline.")
    parser.add_argument(
        "--rebuild-day", help="only rebuild the report for one business day, like 2026-06-05"
    )
    args = parser.parse_args()
    try:
        con = connect()
    except ConnectionError as error:
        print(error)
        return 1
    try:
        if args.rebuild_day:
            rebuild_days(con, [date.fromisoformat(args.rebuild_day)])
            print(f"Rebuilt the report for {args.rebuild_day}. No other day was touched.")
            return 0
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            result = run(client, con)
    except httpx.ConnectError:
        print(f"Cannot reach the order system at {API_URL}. Start it in another terminal:")
        print("  uv run python -m order_system")
        return 1
    except ExtractFailed as error:
        print(f"The load failed: {error}")
        return 1
    finally:
        con.close()
    since = result.asked_since or "the beginning (first load)"
    print(f"Run {result.run_id}: asked for orders changed since {since}.")
    print(
        f"Landed {result.rows_landed} rows. Orders: {result.orders_inserted} new, "
        f"{result.orders_updated} updated, {result.orders_deleted} deleted. "
        f"Rebuilt {len(result.days_rebuilt)} days of the report.\n"
    )
    print(format_answer(result.answer))
    return 0
