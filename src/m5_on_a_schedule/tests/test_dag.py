"""The schedule: these tests drive the real Airflow in Docker.

Airflow must be running:  docker compose --profile schedule up -d --build
They reset the lab database `arden_m5` and the run history of the DAG. They take a few minutes.
"""

import time
from datetime import date

import httpx
import pytest

import airflow_tools as at
from daily_orders import warehouse
from daily_orders.report import daily_report
from order_system.catalog import morning_of
from order_system.generate import build
from order_system.truth import daily_report as true_report

API = "http://127.0.0.1:8010"
EVENTS, PLANTED = build()


def truth(morning: str) -> list[dict]:
    return true_report(EVENTS, morning_of(date.fromisoformat(morning)))


def report() -> list[dict]:
    con = warehouse.connect("arden_m5")
    try:
        return daily_report(con)
    finally:
        con.close()


def load_runs() -> list[tuple]:
    con = warehouse.connect("arden_m5")
    try:
        return con.execute(
            "SELECT run_id, rows_landed, retries, tests_failed FROM ops.load_runs ORDER BY loaded_at"
        ).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def scheduler():
    """A clean start: no run history, an empty lab database, the order system back at its first morning."""
    try:
        at.forget_runs()
    except (ConnectionError, TimeoutError) as error:
        pytest.exit(str(error), returncode=1)
    warehouse.drop_database("arden_m5")
    httpx.post(f"{API}/admin/reset")
    return at


def test_the_dag_has_four_tasks_in_one_line(scheduler):
    tasks = scheduler.airflow("tasks", "list", "daily_orders").split()
    assert sorted(tasks) == [
        "extract_and_load",
        "publish_report",
        "set_source_clock",
        "transform_and_test",
    ]
    details = at._json("dags", "details", "daily_orders")[0]
    assert details["timetable_summary"] == "0 7 * * *" and str(details["max_active_runs"]) == "1"


def test_a_backfill_runs_a_range_of_days_with_one_command(scheduler):
    runs = scheduler.backfill("2026-06-07", "2026-06-11")
    assert [(run["business_day"], run["kind"], run["state"]) for run in runs] == [
        (day, "backfill", "success")
        for day in ("2026-06-07", "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11")
    ]
    assert [row[0] for row in load_runs()] == [
        f"2026-06-{d:02d}_1" for d in range(8, 13)
    ]  # oldest first, one at a time
    assert report() == truth("2026-06-12")


def test_the_bad_morning_the_api_is_down_then_a_page_comes_twice(scheduler):
    """The drill. The extract task fails, Airflow tries it again, and the report is still right."""
    httpx.post(
        f"{API}/admin/faults", json={"kind": "error_500", "count": 7}
    )  # more failures than one try survives
    httpx.post(f"{API}/admin/faults", json={"kind": "repeat_page", "count": 1})
    scheduler.trigger("2026-06-12", wait=False)
    seen = []
    for _ in range(90):
        state = scheduler.task_states("2026-06-12").get("extract_and_load")
        if not seen or seen[-1] != state:
            seen.append(state)
        if all(run["state"] in ("success", "failed") for run in scheduler.runs()):
            break
        time.sleep(2)
    assert "up_for_retry" in seen and seen[-1] == "success"
    assert scheduler.task_states("2026-06-12") == dict.fromkeys(
        ["set_source_clock", "extract_and_load", "transform_and_test", "publish_report"], "success"
    )
    assert report() == truth("2026-06-13")
    con = warehouse.connect("arden_m5")
    landed, different = con.execute(
        "SELECT count(*), count(DISTINCT payload->>'order_id') FROM raw.orders_landing WHERE run_id = '2026-06-13_1'"
    ).fetchone()
    con.close()
    assert landed > different  # the page did arrive twice, and it did no harm


def test_replaying_a_past_day_changes_nothing(scheduler):
    before = report()
    runs = scheduler.replay("2026-06-09")
    assert all(run["state"] == "success" for run in runs)
    assert report() == before


def test_a_failed_table_test_fails_the_run_and_skips_the_report(scheduler):
    from psycopg.types.json import Jsonb

    good = report()
    con = warehouse.connect("arden_m5")
    payload = con.execute(
        "SELECT payload FROM raw.orders_landing WHERE (payload->>'order_id')::bigint = 1130 ORDER BY landing_id DESC LIMIT 1"
    ).fetchone()[0]
    payload.update(status="teleported", updated_at="2026-06-30T00:00:00Z")
    con.execute(
        "INSERT INTO raw.orders_landing (payload, run_id, page_no, loaded_at) VALUES (%s, 'bad_data', 1, now())",
        [Jsonb(payload)],
    )
    scheduler.trigger("2026-06-13")
    states = scheduler.task_states("2026-06-13")
    assert states["extract_and_load"] == "success" and states["transform_and_test"] == "failed"
    assert states["publish_report"] == "upstream_failed"  # the report task never ran
    assert report() == good

    con.execute("DELETE FROM raw.orders_landing WHERE run_id = 'bad_data'")  # the source is fixed
    con.close()
    scheduler.replay("2026-06-13")
    assert scheduler.task_states("2026-06-13")["publish_report"] == "success"
    assert report() == truth("2026-06-14")


def test_the_micro_batch_lab_dag_is_there_and_switched_off(scheduler):
    listing = scheduler.airflow("dags", "list", "--output", "json")
    assert '"dag_id": "orders_every_five_minutes"' in listing
    assert (
        at._json("dags", "details", "orders_every_five_minutes")[0]["timetable_summary"]
        == "*/5 * * * *"
    )
