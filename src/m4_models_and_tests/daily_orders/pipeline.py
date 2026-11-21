"""The whole pipeline for one morning: extract, load, then transform and test with dbt. ELT."""

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import psycopg

from daily_orders.config import API_URL, LOOKBACK_DAYS
from daily_orders.extract import ExtractFailed, fetch_changed_orders, fetch_deletes, read_run_date
from daily_orders.load import (
    land,
    land_deletes,
    next_run_id,
    read_feed_position,
    read_watermark,
    save_progress,
    to_api_text,
)
from daily_orders.report import explain_changes, format_answer, manager_answer, remember_reported
from daily_orders.transform import TransformFailed, build, source_freshness
from daily_orders.warehouse import connect, database_of


@dataclass
class RunResult:
    run_id: str
    run_date: str
    asked_since: str | None
    watermark_after: str | None
    rows_landed: int
    deletes_landed: int
    api_calls: int
    retries: int
    models_built: int
    tests_passed: int
    answer: dict


def extract_and_load(
    client: httpx.Client,
    con: psycopg.Connection,
    sleep: Callable[[float], None] = time.sleep,
    lookback_days: int = LOOKBACK_DAYS,
) -> dict:
    """E and L: read what changed, and land it raw. One transaction. Returns facts about the run."""
    run_date = read_run_date(client)
    watermark = read_watermark(con)
    since = watermark - timedelta(days=lookback_days) if watermark else None
    extract = fetch_changed_orders(client, to_api_text(since), sleep)
    deletes, feed_position = fetch_deletes(client, read_feed_position(con))
    # The last morning whose report passed every test.
    previous = con.execute(
        "SELECT max(run_date) FROM ops.load_runs WHERE tests_failed = 0"
    ).fetchone()[0]
    with con.transaction():
        run_id = next_run_id(con, run_date)
        now = datetime.now(UTC)
        land(con, extract.orders, extract.pages, run_id, now)
        land_deletes(con, deletes, run_id, now)
        newest = max((order["updated_at"] for order in extract.orders), default=None)
        after = save_progress(con, run_id, newest, feed_position)
        con.execute(
            "INSERT INTO ops.load_runs (run_id, run_date, loaded_at, asked_since, watermark_after, "
            "rows_landed, deletes_landed, api_calls, retries) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                run_id,
                run_date,
                now,
                since,
                after,
                len(extract.orders),
                len(deletes),
                extract.api_calls,
                extract.retries,
            ],
        )
    return {
        "run_id": run_id,
        "run_date": run_date,
        "previous_run_date": previous if previous and previous < run_date else None,
        "asked_since": to_api_text(since),
        "watermark_after": to_api_text(after),
        "rows_landed": len(extract.orders),
        "deletes_landed": len(deletes),
        "api_calls": extract.api_calls,
        "retries": extract.retries,
    }


def transform_and_test(
    con: psycopg.Connection, loaded: dict, full_refresh: bool = False
) -> RunResult:
    """T: build the dbt models and run the table tests. If a test fails, the report is not rebuilt."""
    try:
        dbt = build(database_of(con), full_refresh)
    except TransformFailed as error:
        con.execute(
            "UPDATE ops.load_runs SET models_built = %s, tests_passed = %s, tests_failed = %s WHERE run_id = %s",
            [
                error.result.models_built,
                error.result.tests_passed,
                error.result.tests_failed,
                loaded["run_id"],
            ],
        )
        raise
    con.execute(
        "UPDATE ops.load_runs SET models_built = %s, tests_passed = %s, tests_failed = 0 WHERE run_id = %s",
        [dbt.models_built, dbt.tests_passed, loaded["run_id"]],
    )
    explain_changes(con, loaded["run_id"], loaded["previous_run_date"])
    remember_reported(con)
    return RunResult(
        loaded["run_id"],
        loaded["run_date"].isoformat(),
        loaded["asked_since"],
        loaded["watermark_after"],
        loaded["rows_landed"],
        loaded["deletes_landed"],
        loaded["api_calls"],
        loaded["retries"],
        dbt.models_built,
        dbt.tests_passed,
        manager_answer(con, loaded["run_date"], loaded["run_id"]),
    )


def run(
    client: httpx.Client,
    con: psycopg.Connection,
    sleep: Callable[[float], None] = time.sleep,
    lookback_days: int = LOOKBACK_DAYS,
) -> RunResult:
    """Run the pipeline once, for the morning on the order system's clock."""
    loaded = extract_and_load(client, con, sleep, lookback_days)
    return transform_and_test(con, loaded)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily orders pipeline.")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="rebuild every day of the report, not only the newest",
    )
    parser.add_argument(
        "--freshness", action="store_true", help="only check that the raw data is recent"
    )
    args = parser.parse_args()
    try:
        con = connect()
    except ConnectionError as error:
        print(error)
        return 1
    try:
        if args.freshness:
            result = source_freshness(database_of(con))
            print(
                "The raw data is fresh."
                if result.success
                else "The raw data is STALE: " + str(result.failures)
            )
            return 0 if result.success else 1
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            loaded = extract_and_load(client, con)
            result = transform_and_test(con, loaded, args.full_refresh)
    except httpx.ConnectError:
        print(f"Cannot reach the order system at {API_URL}. Start it in another terminal:")
        print("  uv run python -m order_system")
        return 1
    except ExtractFailed as error:
        print(f"The load failed: {error}")
        return 1
    except TransformFailed as error:
        print(error)
        print(
            "\nThe report was NOT rebuilt. It still shows the last numbers that passed every test."
        )
        return 1
    finally:
        con.close()
    since = result.asked_since or "the beginning (first load)"
    print(f"Run {result.run_id}: asked for orders changed since {since}.")
    print(
        f"Landed {result.rows_landed} rows. dbt built {result.models_built} models "
        f"and passed {result.tests_passed} tests on the tables.\n"
    )
    print(format_answer(result.answer))
    return 0
