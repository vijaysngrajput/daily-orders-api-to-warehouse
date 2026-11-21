"""The pipeline, as steps a scheduler can run one by one.

    python -m daily_orders extract-load      E and L: read what changed, land it raw
    python -m daily_orders transform-test    T: dbt builds the models and tests the tables
    python -m daily_orders report            print the manager's report
    python -m daily_orders                   all three, in order

Each step is safe to run again. So a scheduler may retry any of them.
"""

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


class WrongDay(Exception):
    """The order system's clock is not on the morning this run is for."""


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
    expect_run_date: date | None = None,
) -> dict:
    """E and L: read what changed, and land it raw. One transaction. Returns facts about the run."""
    run_date = read_run_date(client)
    if expect_run_date is not None and run_date != expect_run_date:
        raise WrongDay(
            f"This run is for the morning of {expect_run_date}, but the order system says it is {run_date}."
        )
    watermark = read_watermark(con)
    since = watermark - timedelta(days=lookback_days) if watermark else None
    extract = fetch_changed_orders(client, to_api_text(since), sleep)
    deletes, feed_position = fetch_deletes(client, read_feed_position(con))
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
        "rows_landed": len(extract.orders),
        "api_calls": extract.api_calls,
    }


def latest_load(con: psycopg.Connection) -> dict | None:
    """The newest load, as the run log holds it."""
    row = con.execute(
        "SELECT run_id, run_date, asked_since, watermark_after, rows_landed, deletes_landed, api_calls, retries "
        "FROM ops.load_runs ORDER BY loaded_at DESC, run_id DESC LIMIT 1"
    ).fetchone()
    names = (
        "run_id",
        "run_date",
        "asked_since",
        "watermark_after",
        "rows_landed",
        "deletes_landed",
        "api_calls",
        "retries",
    )
    return dict(zip(names, row)) if row else None


def transform_and_test(con: psycopg.Connection, full_refresh: bool = False) -> RunResult:
    """T: build the dbt models and run the table tests, for the newest load.

    If a test fails, the report is not rebuilt, and this raises TransformFailed.
    """
    loaded = latest_load(con)
    if loaded is None:
        raise ValueError("Nothing is loaded yet. Run the extract-load step first.")
    # The last morning, before this one, whose report passed every test.
    previous = con.execute(
        "SELECT max(run_date) FROM ops.load_runs WHERE tests_failed = 0 AND run_date < %s",
        [loaded["run_date"]],
    ).fetchone()[0]
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
    explain_changes(con, loaded["run_id"], previous)
    remember_reported(con)
    return RunResult(
        loaded["run_id"],
        loaded["run_date"].isoformat(),
        to_api_text(loaded["asked_since"]),
        to_api_text(loaded["watermark_after"]),
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
    """Run the whole pipeline once, for the morning on the order system's clock."""
    extract_and_load(client, con, sleep, lookback_days)
    return transform_and_test(con)


def report_text(con: psycopg.Connection) -> str:
    """The manager's report for the newest run that passed every test."""
    row = con.execute(
        "SELECT run_id, run_date FROM ops.load_runs WHERE tests_failed = 0 ORDER BY loaded_at DESC, run_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "No run has passed its tests yet. There is no report."
    return format_answer(manager_answer(con, row[1], row[0]))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the daily orders pipeline, or one step of it."
    )
    parser.add_argument(
        "step",
        nargs="?",
        default="all",
        choices=["all", "extract-load", "transform-test", "report", "freshness"],
    )
    parser.add_argument(
        "--run-date",
        help="the morning this run is for, like 2026-06-08. The step stops if the source disagrees",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="rebuild every day of the report, not only the newest",
    )
    args = parser.parse_args()
    expected = date.fromisoformat(args.run_date) if args.run_date else None
    try:
        con = connect()
    except ConnectionError as error:
        print(error)
        return 1
    try:
        if args.step == "freshness":
            result = source_freshness(database_of(con))
            print("The raw data is fresh." if result.success else "The raw data is STALE.")
            return 0 if result.success else 1
        if args.step in ("all", "extract-load"):
            with httpx.Client(base_url=API_URL, timeout=30) as client:
                loaded = extract_and_load(client, con, expect_run_date=expected)
            print(
                f"extract-load: run {loaded['run_id']} landed {loaded['rows_landed']} rows in {loaded['api_calls']} API calls."
            )
        if args.step in ("all", "transform-test"):
            done = transform_and_test(con, args.full_refresh)
            print(
                f"transform-test: dbt built {done.models_built} models and passed {done.tests_passed} tests on the tables."
            )
        if args.step in ("all", "report"):
            print()
            print(report_text(con))
    except httpx.ConnectError:
        print(f"Cannot reach the order system at {API_URL}.")
        return 1
    except (ExtractFailed, WrongDay) as error:
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
    return 0
