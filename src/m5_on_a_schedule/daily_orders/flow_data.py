"""Build the numbers shown on milestone_5_flow.html and milestone_5_recap.html.

The pages must never show a number typed by hand. This script drives the real Airflow
in Docker through a week, a bad morning, a replay and a run with bad data, and writes
what happened into the pages. It takes a few minutes.

    docker compose --profile schedule up -d --build   Airflow must be running
    uv run python -m daily_orders.flow_data           write the pages' data
    uv run python -m daily_orders.flow_data --check   fail if a page is out of date

It resets the lab database `arden_m5` and the run history of the DAG.
"""

import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import airflow_tools as at
from daily_orders import pipeline, warehouse
from daily_orders.config import API_URL, MILESTONE_DIR
from daily_orders.report import daily_report, day_totals, dollars
from order_system.app import create_app
from order_system.catalog import morning_of
from order_system.generate import build as build_events
from order_system.truth import daily_report as true_report
from order_system.truth import day_totals as true_totals

PAGE = MILESTONE_DIR / "milestone_5_flow.html"
RECAP_PAGE = MILESTONE_DIR / "milestone_5_recap.html"
MARKER = re.compile(
    r'(<script id="page-data" type="application/json">)(.*?)(</script>)', flags=re.DOTALL
)
DATABASE = "arden_m5"
TASKS = ["set_source_clock", "extract_and_load", "transform_and_test", "publish_report"]

WORDS = {
    "cron": (
        "Schedule and cron",
        "A schedule starts a run at a time. Cron's five fields are the usual way to write one: 0 7 * * * is every day at 07:00.",
    ),
    "dag": ("DAG", "Directed acyclic graph: the tasks of a job, and the arrows between them."),
    "dependency": (
        "Dependency",
        "A rule that says task B needs task A to finish first. The arrows of the DAG.",
    ),
    "interval": (
        "Data interval",
        "The slice of time a run covers. The logical date is its start: the day the run is for.",
    ),
    "backfill": (
        "Backfill",
        "Running the job again for days in the past. One command for a range of days.",
    ),
    "retry": (
        "Retry",
        "Running a failed task again after a short wait. It is safe only because the task is idempotent.",
    ),
    "replay": (
        "Replay",
        "Running a finished day again. In Airflow: clear the run, and its tasks run again.",
    ),
    "upstream": (
        "Dependency",
        "A task whose upstream task failed never runs. Airflow marks it upstream_failed.",
    ),
    "catchup": (
        "Catchup",
        "The setting that makes a scheduler run missed days by itself. Here it is off: a person asks for a backfill.",
    ),
    "micro": (
        "Micro-batch",
        "Batch with a very small slice. The same tasks, a different schedule.",
    ),
    "orchestration": (
        "Orchestration",
        "Running the steps of a pipeline in the right order, at the right time, and watching them.",
    ),
}


def _word(key: str) -> dict:
    return {"term": WORDS[key][0], "meaning": WORDS[key][1]}


def _day(day: str | date) -> str:
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day.strftime("%a %m-%d")


def _states(states: dict) -> str:
    """The four tasks and their states, in the order of the arrows."""
    short = {
        "set_source_clock": "clock",
        "extract_and_load": "extract",
        "transform_and_test": "transform",
        "publish_report": "publish",
    }
    return "  →  ".join(f"{short[task]}: {states.get(task) or 'waiting'}" for task in TASKS)


def build() -> dict:
    events, planted = build_events()
    p4, p6, p8 = planted["P4"], planted["P6"], planted["P8"]
    source = httpx.Client(base_url=API_URL, timeout=30)

    def truth(morning: str) -> list[dict]:
        return true_report(events, morning_of(date.fromisoformat(morning)))

    def right(con, morning: str) -> bool:
        return daily_report(con) == truth(morning)

    def core_cards(con, order_ids: list[int], tone: str = "good") -> list[dict]:
        rows = con.execute(
            "SELECT order_id, order_date, city_name, status, net_cents, is_deleted FROM core.orders WHERE order_id = ANY(%s) ORDER BY 1",
            [order_ids],
        ).fetchall()
        return [
            {
                "id": order_id,
                "amount": dollars(net),
                "sub": f"{_day(day)} · {city} · {'marked deleted' if deleted else status}",
                "tone": tone,
            }
            for order_id, day, city, status, net, deleted in rows
        ]

    def report_cards(con, days: list[str], morning: str, tone: str | None = None) -> list[dict]:
        report, real = day_totals(daily_report(con)), true_totals(truth(morning))
        cards = []
        for day in days:
            shown, good = report[day], real[day]
            ok = (shown["orders"], shown["revenue_cents"]) == (
                good["orders"],
                good["revenue_cents"],
            )
            cards.append(
                {
                    "id": _day(day),
                    "amount": dollars(shown["revenue_cents"]),
                    "sub": f"{shown['orders']} orders"
                    + (
                        " · equals the truth"
                        if ok
                        else f" · truth: {good['orders']}, {dollars(good['revenue_cents'])}"
                    ),
                    "tone": tone or ("good" if ok else "bad"),
                }
            )
        return cards

    def run_cards(runs: list[dict]) -> list[dict]:
        return [
            {
                "id": _day(run["business_day"]),
                "amount": run["state"],
                "sub": f"started by {run['kind']}",
                "tone": "good" if run["state"] == "success" else "bad",
            }
            for run in runs
        ]

    def last_load(con) -> tuple:
        return con.execute(
            "SELECT l.run_id, l.rows_landed, l.api_calls, l.retries, l.tests_passed, "
            "(SELECT count(DISTINCT payload->>'order_id') FROM raw.orders_landing r WHERE r.run_id = l.run_id) "
            "FROM ops.load_runs l ORDER BY l.loaded_at DESC, l.run_id DESC LIMIT 1"
        ).fetchone()

    idle = {"count": "", "cards": []}
    steps: list[dict] = []

    # ---- The pain: two steps started by the clock (no Airflow, a private order system) ----
    ticks = {"now": 0.0}
    private = TestClient(create_app(timer=lambda: ticks["now"]))
    warehouse.drop_database("arden_m5_pages")
    scratch = warehouse.connect("arden_m5_pages")
    private.post("/admin/clock", json={"morning": "2026-06-08"})
    pipeline.run(private, scratch, sleep=lambda s: ticks.update(now=ticks["now"] + s))
    private.post("/admin/clock", json={"morning": "2026-06-09"})
    early = pipeline.transform_and_test(
        scratch
    )  # 07:05: the transform starts, the load has not finished
    stale_morning = _day(early.run_date)
    stale_tests = early.tests_passed
    stale_cards = report_cards(scratch, ["2026-06-07", "2026-06-06"], "2026-06-08", tone="changed")
    scratch.close()
    warehouse.drop_database("arden_m5_pages")
    steps.append(
        {
            "id": "cron",
            "active": "schedule",
            "problem": True,
            "clock": "Tue 06-09, 07:05",
            "title": "Two steps, started by the clock",
            "text": f"A first idea: start the load at 07:00 and the transform at 07:05. On Tuesday the load is slow. "
            f"At 07:05 the transform starts anyway, finds only Monday's rows, passes all {stale_tests} tests, and "
            f"publishes the report of {stale_morning}. Nothing failed. The manager reads yesterday's numbers.",
            "word": _word("cron"),
            "band": "cron:  0 7 * * *  extract-load      5 7 * * *  transform-test      (no step waits for another)",
            "stages": {"api": {"count": "slow this morning", "cards": []},
                       "extract": {"count": "still running at 07:05", "cards": []},
                       "raw": {"count": "Tuesday's rows: not here yet", "cards": []},
                       "orders": {"count": f"{stale_tests} tests passed", "cards": []},
                       "report": {"count": f"still the report of {stale_morning}", "cards": stale_cards}},
            "moves": [],
        }
    )  # fmt: skip

    # ---- The real Airflow --------------------------------------------------------
    at.forget_runs()
    warehouse.drop_database(DATABASE)
    source.post("/admin/reset")
    steps.append(
        {
            "id": "dag",
            "active": "schedule",
            "fix": True,
            "clock": "every day, 07:00",
            "title": "The fix: tasks, and arrows between them",
            "text": "The pipeline becomes four tasks in an Airflow DAG. An arrow means: this task needs that one to "
            "finish first. The transform no longer starts at a time. It starts when the load is done. "
            "Airflow adds order, timing, retries and a history. The work is the same program as before.",
            "word": _word("dag"),
            "band": "set_source_clock  →  extract_and_load  →  transform_and_test  →  publish_report",
            "stages": {"api": idle, "extract": {"count": "task: extract_and_load", "cards": []},
                       "raw": idle, "orders": {"count": "task: transform_and_test", "cards": []},
                       "report": {"count": "task: publish_report", "cards": []}},
            "moves": [],
        }
    )  # fmt: skip

    at.trigger("2026-06-07")
    con = warehouse.connect(DATABASE)
    first = last_load(con)
    first_states = at.task_states("2026-06-07")
    late1 = con.execute(
        "SELECT order_id FROM core.orders WHERE status = 'placed' ORDER BY 1 LIMIT 1"
    ).fetchone()[0]
    steps.append(
        {
            "id": "interval",
            "active": "schedule",
            "clock": "Mon 06-08, 07:00",
            "title": "A run is for one day, and happens the next morning",
            "text": f"This run is for the business day {_day('2026-06-07')}: its logical date. Its data interval is "
            f"Sunday 07:00 to Monday 07:00, and it starts when that interval ends. It lands {first[1]} rows and "
            f"passes {first[4]} table tests. The tasks take the day from the run, never from the clock.",
            "word": _word("interval"),
            "band": _states(first_states),
            "stages": {"api": {"count": f"{first[1]} orders", "cards": []},
                       "extract": {"count": f"{first[1]} rows · {first[2]} calls", "cards": []},
                       "raw": {"count": f"{first[1]} rows", "cards": []},
                       "orders": {"count": f"{first[4]} tests passed", "cards": core_cards(con, [p4, late1])},
                       "report": {"count": "morning of Mon 06-08", "cards": report_cards(con, ["2026-06-07", "2026-06-06"], "2026-06-08")}},
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "raw"}, {"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )  # fmt: skip

    runs = at.backfill("2026-06-08", "2026-06-11")
    loads = con.execute(
        "SELECT run_id, rows_landed FROM ops.load_runs ORDER BY loaded_at"
    ).fetchall()
    week_ok = right(con, "2026-06-12")
    steps.append(
        {
            "id": "backfill",
            "active": "schedule",
            "fix": True,
            "clock": "any time later",
            "title": "Four missed days, one command",
            "text": f"Nobody ran the pipeline from Monday to Thursday. One backfill command runs the "
            f"{len(runs) - 1} missed days, oldest first, one at a time. Each run does exactly what a scheduled run "
            "would have done. The report " + ("equals the answer key." if week_ok else "is WRONG."),
            "word": _word("backfill"),
            "band": "airflow backfill create --dag-id daily_orders --from-date 2026-06-08 --to-date 2026-06-12 --max-active-runs 1",
            "stages": {"api": idle,
                       "extract": {"count": f"{len(loads)} runs, in order", "cards": [
                           {"id": f"morning {run_id[5:10]}", "amount": f"{rows} rows", "sub": f"run {run_id[5:]}", "tone": ""} for run_id, rows in loads]},
                       "raw": {"count": f"{con.execute('SELECT count(*) FROM raw.orders_landing').fetchone()[0]} rows", "cards": []},
                       "orders": {"count": f"{con.execute('SELECT count(*) FROM core.orders').fetchone()[0]} orders", "cards": core_cards(con, [p4, p6])},
                       "report": {"count": "right" if week_ok else "WRONG", "cards": run_cards(runs)}},
            "moves": [{"from": "extract", "to": "raw"}, {"from": "raw", "to": "orders"}],
        }
    )  # fmt: skip

    # ---- The bad morning ---------------------------------------------------------
    source.post("/admin/faults", json={"kind": "error_500", "count": 7})
    source.post("/admin/faults", json={"kind": "repeat_page", "count": 1})
    at.trigger("2026-06-12", wait=False)
    seen: list[str] = []
    while True:
        state = at.task_states("2026-06-12").get("extract_and_load")
        if state and (not seen or seen[-1] != state):
            seen.append(state)
        if all(run["state"] in ("success", "failed") for run in at.runs()):
            break
        time.sleep(2)
    if "up_for_retry" in seen and seen[-1] == "success":
        # A task that was up for retry and then succeeded did run a second time.
        # The check every two seconds can miss that short second "running".
        seen = ["running", "up_for_retry", "running", "success"]
    drill = last_load(con)
    drill_ok = right(con, "2026-06-13")
    retried = "up_for_retry" in seen
    steps.append(
        {
            "id": "down",
            "active": "extract",
            "problem": True,
            "clock": "Sat 06-13, 07:00",
            "title": "The bad morning: the order system is down",
            "text": "At 07:00 the order system answers every call with an error. Inside the task, the code tries five "
            "times and gives up. The task fails. Airflow sees that it has tries left, marks it "
            + ("up_for_retry, and waits 20 seconds." if retried else "FAILED WITHOUT A RETRY.")
            + " Nothing was landed, and the watermark did not move.",
            "word": _word("retry"),
            "band": "clock: success  →  extract: up_for_retry  →  transform: waiting  →  publish: waiting",
            "stages": {"api": {"count": "500, 500, 500, 500, 500", "cards": []},
                       "extract": {"count": "try 1 failed · waiting 20 seconds", "cards": [
                           {"id": f"state {n}", "amount": state, "sub": "extract_and_load", "tone": "changed" if state == "up_for_retry" else ""}
                           for n, state in enumerate(seen[:2], start=1)]},
                       "raw": {"count": "nothing landed", "cards": []},
                       "orders": {"count": "waiting", "cards": []},
                       "report": {"count": "waiting", "cards": []}},
            "moves": [],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "recovered",
            "active": "extract",
            "fix": True,
            "clock": "Sat 06-13, 07:01",
            "title": "It tries again. Then a page arrives twice",
            "text": f"Airflow starts the task again. The order system is back, and then serves one page twice: "
            f"{drill[1]} rows land for {drill[5]} orders. Staging keeps one row per order, {drill[4]} table tests "
            f"pass, and the report " + ("equals the answer key." if drill_ok else "is WRONG.")
            + " Nobody did anything.",
            "word": _word("retry"),
            "band": _states(at.task_states("2026-06-12")),
            "stages": {"api": {"count": "back, and one page twice", "cards": []},
                       "extract": {"count": f"{drill[1]} rows · {drill[3]} retries in the code", "cards": [
                           {"id": f"state {n}", "amount": state, "sub": "extract_and_load", "tone": "good" if state == "success" else ("changed" if state == "up_for_retry" else "")}
                           for n, state in enumerate(seen, start=1)]},
                       "raw": {"count": f"{drill[1]} rows · {drill[5]} orders", "cards": []},
                       "orders": {"count": f"{drill[4]} tests passed", "cards": core_cards(con, [p4, p6])},
                       "report": {"count": "right" if drill_ok else "WRONG", "cards": report_cards(con, ["2026-06-12", "2026-06-11"], "2026-06-13")}},
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "raw"}, {"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )  # fmt: skip

    # ---- Replay ------------------------------------------------------------------
    def snapshot() -> tuple:
        return (
            con.execute(
                "SELECT order_id, status, total_cents, refunded_cents, is_deleted FROM core.orders ORDER BY 1"
            ).fetchall(),
            daily_report(con),
        )

    before = snapshot()
    at.replay("2026-06-09")
    same = snapshot() == before
    replay_load = last_load(con)
    steps.append(
        {
            "id": "replay",
            "active": "schedule",
            "rerun": True,
            "fix": True,
            "clock": "any time later",
            "title": "Run a past day again: nothing changes",
            "text": f"Finance asks for {_day('2026-06-09')} again. A person clears that run, and Airflow runs its four "
            f"tasks again. It is logged as run {replay_load[0]}. The orders and the report are "
            + ("identical to before." if same else "DIFFERENT.")
            + " Every step is idempotent, so a replay is always safe.",
            "word": _word("replay"),
            "band": "airflow tasks clear daily_orders -s 2026-06-09 -e 2026-06-10 -y",
            "stages": {"api": idle, "extract": {"count": f"run {replay_load[0][5:]}", "cards": []},
                       "raw": idle,
                       "orders": {"count": "unchanged", "cards": core_cards(con, [p4, p6])},
                       "report": {"count": "unchanged", "cards": report_cards(con, ["2026-06-12", "2026-06-09", "2026-06-08"], "2026-06-13")}},
            "moves": [{"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )  # fmt: skip

    # ---- Bad data ----------------------------------------------------------------
    good_report = daily_report(con)
    payload = con.execute(
        "SELECT payload FROM raw.orders_landing WHERE (payload->>'order_id')::bigint = %s ORDER BY landing_id DESC LIMIT 1",
        [p4],
    ).fetchone()[0]
    payload.update(status="teleported", updated_at="2026-06-30T00:00:00Z")
    con.execute(
        "INSERT INTO raw.orders_landing (payload, run_id, page_no, loaded_at) VALUES (%s, 'bad_data', 1, now())",
        [Jsonb(payload)],
    )
    at.trigger("2026-06-13")
    bad_states = at.task_states("2026-06-13")
    kept = daily_report(con) == good_report
    steps.append(
        {
            "id": "bad_data",
            "active": "orders",
            "problem": True,
            "clock": "Sun 06-14, 07:00",
            "title": "The data is wrong: no retry, and no report",
            "text": f"The order system is up, and sends order {p4} with a status nobody has seen. The load succeeds. "
            f"The test task is {bad_states['transform_and_test']}, and it is not tried again: a test that failed on "
            f"the data will fail again. The report task is {bad_states['publish_report']}. It never ran, and the "
            "report " + ("keeps its last good numbers." if kept else "CHANGED."),
            "word": _word("upstream"),
            "band": _states(bad_states),
            "stages": {"api": {"count": "a bad row", "cards": [{"id": p4, "amount": "bad", "sub": "status: teleported", "tone": "bad"}]},
                       "extract": {"count": "success", "cards": []},
                       "raw": {"count": "landed as received", "cards": []},
                       "orders": {"count": "a table test FAILED", "cards": core_cards(con, [p4], "bad")},
                       "report": {"count": "upstream_failed: not published", "cards": []}},
            "moves": [{"from": "raw", "to": "orders"}],
        }
    )  # fmt: skip
    con.execute("DELETE FROM raw.orders_landing WHERE run_id = 'bad_data'")
    at.replay("2026-06-13")
    fixed_states = at.task_states("2026-06-13")
    fixed_ok = right(con, "2026-06-14")
    steps.append(
        {
            "id": "fixed",
            "active": "report",
            "fix": True,
            "clock": "Sun 06-14, 09:00",
            "title": "A person fixes the source, and runs the day again",
            "text": "The bad row is removed at the source. The failed run is cleared, and Airflow runs it again. All "
            "four tasks succeed, and the report " + ("equals the answer key." if fixed_ok else "is WRONG.")
            + f" It also drops the test order {p8}, which was deleted from the source on Saturday.",
            "word": _word("replay"),
            "band": _states(fixed_states),
            "stages": {"api": idle, "extract": {"count": "success", "cards": []}, "raw": idle,
                       "orders": {"count": "all tests passed", "cards": core_cards(con, [p4, p8])},
                       "report": {"count": "right" if fixed_ok else "WRONG", "cards": report_cards(con, ["2026-06-13", "2026-06-12"], "2026-06-14")}},
            "moves": [{"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )  # fmt: skip
    all_runs = at.runs()
    steps.append(
        {
            "id": "catchup",
            "active": "schedule",
            "clock": "after an outage",
            "title": "Missed days do not run by themselves",
            "text": "This DAG has catchup switched off. When the scheduler comes back after days away, it starts "
            "nothing old. A person decides when a pile of old runs hits the order system, and asks for a backfill. "
            f"Every run so far is listed, with the day it was for: {len(all_runs)} runs.",
            "word": _word("catchup"),
            "band": "catchup=False   ·   max_active_runs=1   ·   one morning at a time",
            "stages": {"api": idle, "extract": idle, "raw": idle, "orders": idle,
                       "report": {"count": f"{len(all_runs)} runs, all listed", "cards": run_cards(all_runs)}},
            "moves": [],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "micro",
            "active": "schedule",
            "clock": "a lab",
            "title": "The same tasks, every five minutes",
            "text": "A second DAG runs the same pipeline as a micro-batch: every five minutes, not every day. One line "
            "of the schedule changed. Each run is still a normal batch run, with the same tests. Below about a "
            "minute, the time one run takes is most of the wait, and a stream becomes the better tool. "
            "Arden's report is read once a morning, so it stays daily.",
            "word": _word("micro"),
            "band": "orders_every_five_minutes:   */5 * * * *   (a lab, switched off by default)",
            "stages": {"api": idle, "extract": {"count": "the same tasks", "cards": []}, "raw": idle,
                       "orders": {"count": "the same tests", "cards": []}, "report": idle},
            "moves": [],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "done",
            "active": "schedule",
            "fix": True,
            "clock": "every day, 07:00",
            "title": "Nothing is still wrong",
            "text": "The pipeline reads only what changed, lands it raw, keeps one row per order, tests its tables, "
            "and runs itself every morning. It survived a bad morning with nobody helping. A real company would add "
            "alerts, a quality gate and a dashboard. Those belong to other projects of this series.",
            "word": _word("orchestration"),
            "band": "set_source_clock  →  extract_and_load  →  transform_and_test  →  publish_report      every day at 07:00",
            "stages": {"api": {"count": "the source", "cards": []},
                       "extract": {"count": "incremental · retries", "cards": []},
                       "raw": {"count": "append, as received", "cards": []},
                       "orders": {"count": "one row per order · tested", "cards": core_cards(con, [p4, p6, p8])},
                       "report": {"count": "right, every morning", "cards": report_cards(con, ["2026-06-13", "2026-06-12", "2026-06-08"], "2026-06-14")}},
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "raw"}, {"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )  # fmt: skip

    follow = {
        str(p4): {
            "cron": "It is in Monday's rows. Its refund is in Tuesday's rows, which the early transform never saw.",
            "dag": "Nothing about its data changes in this milestone. Only who starts the work.",
            "interval": "Loaded by the run for Sunday, before its refund.",
            "backfill": "The run for Monday picks up its refund, and Friday's number goes down.",
            "down": "Not affected.",
            "recovered": "Not affected.",
            "replay": "Read again by the replay. One row, unchanged.",
            "bad_data": "The source sends it with the status 'teleported'. This one row fails the test task.",
            "fixed": "Back to 'delivered'. The replay passes.",
            "catchup": "One row, right.",
            "micro": "A micro-batch would have picked up its refund within five minutes, not the next morning.",
            "done": "One row, right, on every morning.",
        },
        str(p6): {
            "cron": "Not placed yet.",
            "dag": "Not placed yet.",
            "interval": "Not placed yet. It is ordered on Monday evening.",
            "backfill": "Loaded as 'out for delivery', then its late cancellation is caught by the lookback of the next run.",
            "down": "Already cancelled, and right.",
            "recovered": "Already cancelled, and right.",
            "replay": "Unchanged.",
            "bad_data": "Not affected.",
            "fixed": "Unchanged.",
            "catchup": "One row, cancelled.",
            "micro": "Its cancellation reached the API twelve hours late. No schedule can load what the source has not said yet.",
            "done": "One row, cancelled, and right.",
        },
        str(p8): {
            "cron": "Not placed yet.",
            "dag": "Not placed yet.",
            "interval": "Not placed yet.",
            "backfill": "Not placed yet. It is ordered on Friday 12 June.",
            "down": "It is in the rows this run is trying to load.",
            "recovered": "Loaded, as a normal order of Friday.",
            "replay": "Unchanged.",
            "bad_data": "Deleted from the source on Saturday. The delete is landed, but the report is not rebuilt in this failed run.",
            "fixed": "The replay passes, and it is marked as deleted. Friday loses one order.",
            "catchup": "Kept, marked, and not counted.",
            "micro": "Kept, marked, and not counted.",
            "done": "Kept, marked, and not counted.",
        },
    }
    for step in steps:
        step["follow"] = {case: lines[step["id"]] for case, lines in follow.items()}

    con.close()
    recap = {
        "dbt": {"tests": first[4]},
        "first": {"rows": first[1], "calls": first[2]},
        "cron": {"tests": stale_tests, "shown": stale_morning},
        "backfill": {
            "days": len(runs) - 1,
            "runs": len(loads),
            "right": "equals the answer key" if week_ok else "WRONG",
        },
        "drill": {
            "states": " → ".join(seen),
            "rows": drill[1],
            "orders": drill[5],
            "retries": drill[3],
            "right": "equals the answer key" if drill_ok else "WRONG",
        },
        "replay": {"same": "identical" if same else "DIFFERENT", "run": replay_load[0]},
        "bad": {
            "transform": bad_states["transform_and_test"],
            "publish": bad_states["publish_report"],
            "kept": "kept its last good numbers" if kept else "CHANGED",
            "after": "equals the answer key" if fixed_ok else "WRONG",
        },
        "runs": len(all_runs),
    }
    return {
        "recap": recap,
        "milestone": 5,
        "of": 5,
        "done": True,
        "title": "On a schedule",
        "lede": "The same pipeline, cut into tasks. Airflow runs them every morning, in the right order, and again "
        "when one fails. Watch it survive a bad morning.",
        "stages": [
            {"id": "api", "name": "Order system API", "role": "Source", "state": "given"},
            {
                "id": "extract",
                "name": "Extract",
                "role": "Task · extract_and_load",
                "state": "built",
                "badge": "now a task: 3 retries",
            },
            {
                "id": "raw",
                "name": "Raw landing table",
                "role": "Store · raw layer",
                "state": "built",
                "badge": "as before",
            },
            {
                "id": "orders",
                "name": "Clean orders table",
                "role": "Task · transform_and_test",
                "state": "built",
                "badge": "now a task: no retry",
            },
            {
                "id": "report",
                "name": "Daily report",
                "role": "Task · publish_report",
                "state": "built",
                "badge": "now a task",
            },
        ],
        "schedule": {
            "state": "new",
            "text": "New. One Airflow DAG: four tasks, every day at 07:00.",
        },
        "tests": {
            "state": "built",
            "text": f"Built. {first[4]} tests run on the tables in every run. A failed test stops the report.",
        },
        "cases": [
            {"id": str(p4), "label": f"Order {p4}", "hint": "refunded, then a bad status"},
            {"id": str(p6), "label": f"Order {p6}", "hint": "cancelled late"},
            {"id": str(p8), "label": f"Order {p8}", "hint": "deleted from the source"},
        ],
        "steps": steps,
        "still_wrong": "Nothing. Every 'still wrong' line of this project is closed.",
        "next": "A real company would add alerts, a quality gate and a dashboard. Those belong to other projects of the series.",
    }


def count_tests() -> int:
    """How many tests this milestone has, counted by pytest itself."""
    listing = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=MILESTONE_DIR,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return int(re.search(r"(\d+) tests? collected", listing).group(1))


def build_recap(flow: dict | None = None) -> dict:
    """The numbers shown on milestone_5_recap.html."""
    recap = dict((flow or build())["recap"])
    glossary = (MILESTONE_DIR.parent.parent / "GLOSSARY.md").read_text(encoding="utf-8")
    recap["numbers"] = {
        "notebooks": len(list((MILESTONE_DIR / "notebooks").glob("0*.ipynb"))),
        "tests": count_tests(),
        "tables": 4,  # the tasks of the DAG
        "rows": recap["dbt"]["tests"],
        "words": len(
            re.findall(r"^\| \*\*.+\| Milestone 5, notebook \d \|", glossary, flags=re.MULTILINE)
        ),
    }
    return recap


def _read(page: Path) -> dict:
    return json.loads(MARKER.search(page.read_text(encoding="utf-8")).group(2))


def _write(page: Path, data: dict) -> None:
    payload = json.dumps(data, indent=1, ensure_ascii=False).replace("</", "<\\/")
    text = page.read_text(encoding="utf-8")
    page.write_text(
        MARKER.sub(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), text),
        encoding="utf-8",
    )


def page_data() -> dict:
    """The data currently written inside the flow page."""
    return _read(PAGE)


def recap_page_data() -> dict:
    """The data currently written inside the recap page."""
    return _read(RECAP_PAGE)


def main() -> int:
    try:
        flow = build()
    except ConnectionError as error:
        print(error)
        return 1
    recap = build_recap(flow)
    if "--check" in sys.argv:
        ok = page_data() == flow and recap_page_data() == recap
        print(
            "The pages match the code."
            if ok
            else "The pages are out of date. Run: uv run python -m daily_orders.flow_data"
        )
        return 0 if ok else 1
    _write(PAGE, flow)
    _write(RECAP_PAGE, recap)
    print(
        f"Wrote {len(flow['steps'])} steps into {PAGE.name}, and the numbers into {RECAP_PAGE.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
