"""The whole pipeline for one morning: extract what changed, land it, report."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import psycopg

from daily_orders.config import API_URL
from daily_orders.extract import ExtractFailed, fetch_changed_orders, read_run_date
from daily_orders.load import land, next_run_id, read_watermark, to_api_text
from daily_orders.report import format_answer, manager_answer
from daily_orders.warehouse import connect


@dataclass
class RunResult:
    run_id: str
    run_date: str
    watermark_before: str | None
    watermark_after: str | None
    rows_landed: int
    api_calls: int
    retries: int
    answer: dict


def run(
    client: httpx.Client,
    con: psycopg.Connection,
    sleep: Callable[[float], None] = time.sleep,
) -> RunResult:
    """Run the pipeline once, for the morning on the order system's clock."""
    run_date = read_run_date(client)
    before = read_watermark(con)
    # If the extract fails, we stop here. Nothing is landed and the watermark does not move.
    extract = fetch_changed_orders(client, to_api_text(before), sleep)
    run_id = next_run_id(con, run_date)
    after = land(
        con,
        extract.orders,
        extract.pages,
        run_id,
        run_date,
        datetime.now(UTC),
        extract.api_calls,
        extract.retries,
    )
    return RunResult(
        run_id,
        run_date.isoformat(),
        to_api_text(before),
        to_api_text(after),
        len(extract.orders),
        extract.api_calls,
        extract.retries,
        manager_answer(con, run_date),
    )


def main() -> int:
    try:
        con = connect()
    except ConnectionError as error:
        print(error)
        return 1
    try:
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
    since = result.watermark_before or "the beginning (first load)"
    print(f"Run {result.run_id}: asked for orders changed since {since}.")
    print(
        f"Landed {result.rows_landed} rows in {result.api_calls} API calls. Watermark is now {result.watermark_after}.\n"
    )
    print(format_answer(result.answer))
    return 0
