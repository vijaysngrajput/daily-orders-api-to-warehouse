"""The live page: run the finished pipeline on a day you choose, and watch it.

    docker compose up -d warehouse            the warehouse must be running
    uv run python pipeline_live/server.py     then your browser opens http://127.0.0.1:8030

It runs the real code of milestone 5: the same extract, the same load, the same dbt models
and table tests. Only two things are special here:

- The order system runs inside this program, so nothing else needs to be started.
- Waiting is skipped. When the pipeline wants to wait 4 seconds for the API, the page
  shows "waited 4 seconds", and carries on at once.

It uses its own database, `arden_live`. Nothing here touches the milestones.
"""

import os
import sys
import threading
import webbrowser
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src" / "m5_on_a_schedule"))  # the finished pipeline

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from daily_orders import pipeline, warehouse  # noqa: E402
from daily_orders.extract import ExtractFailed  # noqa: E402
from daily_orders.report import daily_report, day_totals, dollars, orders_state  # noqa: E402
from daily_orders.transform import TransformFailed  # noqa: E402
from order_system.app import create_app  # noqa: E402
from order_system.catalog import morning_of  # noqa: E402
from order_system.truth import daily_report as true_report  # noqa: E402
from order_system.truth import day_totals as true_totals  # noqa: E402

PORT = int(os.environ.get("ARDEN_LIVE_PORT", "8030"))
DATABASE = "arden_live"
FIRST_MORNING, LAST_MORNING = date(2026, 6, 2), date(2026, 6, 18)
BREAKS = {
    "api_down": "Take the API down for 3 calls",
    "page_twice": "Make the API send one page twice",
    "bad_status": "Make the API send a bad row",
}
EXPECT = {
    "api_down": "The pipeline should recover by itself",
    "page_twice": "No order should be counted twice",
    "bad_status": "The pipeline should refuse to publish",
}


class Live:
    """Everything the page works with: a private order system, and the live database."""

    def __init__(self):
        self.lock = threading.Lock()
        self.start()

    def start(self) -> None:
        self.ticks = 0.0  # a pretend wall clock: waiting costs no real time
        self.source = TestClient(create_app(timer=lambda: self.ticks))
        self.events = self.source.app.state.world.events
        self.planned: list[str] = []
        self.last_morning: str | None = None
        self.just_fixed = False  # True right after 'Fix the source', until the next run
        warehouse.drop_database(DATABASE)
        self.con().close()

    def con(self):
        return warehouse.connect(DATABASE)

    def nap(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.ticks += seconds


def _day(day: str | date) -> str:
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day.strftime("%a %m-%d")


def _card(key, amount: str, sub: str, tone: str = "") -> dict:
    return {"id": key, "amount": amount, "sub": sub, "tone": tone}


def _report(live: Live, con, morning: str) -> dict:
    """The report, held up against the answer key for that morning."""
    if not warehouse.table_exists(con, "marts.daily_orders"):
        return {"days": [], "all_right": None, "wrong_days": 0}
    shown = day_totals(daily_report(con))
    real = true_totals(true_report(live.events, morning_of(date.fromisoformat(morning))))
    days = []
    for day in sorted(set(shown) | set(real), reverse=True):
        mine, right = shown.get(day), real.get(day)
        ok = (
            mine is not None
            and right is not None
            and (mine["orders"], mine["revenue_cents"]) == (right["orders"], right["revenue_cents"])
        )
        days.append(
            {
                "day": _day(day),
                "orders": mine["orders"] if mine else 0,
                "money": dollars(mine["revenue_cents"]) if mine else "$0.00",
                "truth": f"{right['orders']} orders, {dollars(right['revenue_cents'])}"
                if right
                else "no orders yet",
                "right": ok,
            }
        )
    return {
        "days": days,
        "all_right": all(d["right"] for d in days),
        "wrong_days": sum(not d["right"] for d in days),
    }


def _log(con) -> list[dict]:
    rows = con.execute(
        "SELECT run_id, rows_landed, api_calls, retries, tests_passed, tests_failed FROM ops.load_runs ORDER BY loaded_at DESC LIMIT 12"
    ).fetchall()
    names = ("run_id", "rows_landed", "api_calls", "retries", "tests_passed", "tests_failed")
    return [dict(zip(names, row)) for row in rows]


def state(live: Live) -> dict:
    con = live.con()
    try:
        mornings = [
            (FIRST_MORNING + timedelta(days=n)).isoformat()
            for n in range((LAST_MORNING - FIRST_MORNING).days + 1)
        ]
        return {
            "mornings": [{"date": m, "label": _day(m)} for m in mornings],
            "last_morning": live.last_morning,
            "planned": live.planned,
            "breaks": BREAKS,
            "expect": EXPECT,
            "log": _log(con),
            "report": _report(live, con, live.last_morning)
            if live.last_morning
            else {"days": [], "all_right": None, "wrong_days": 0},
        }
    finally:
        con.close()


def run_morning(live: Live, morning: str) -> dict:
    """Run the pipeline for one morning, and describe what each stage did."""
    live.waits = []
    planned, live.planned = live.planned, []
    same_morning_again = live.last_morning == morning
    fixed, live.just_fixed = live.just_fixed, False
    outcome: list[dict] = []  # in plain words: what was tried, and what the pipeline did about it
    live.source.post("/admin/clock", json={"morning": morning})
    if "api_down" in planned:
        live.source.post("/admin/faults", json={"kind": "error_500", "count": 3})
    if "page_twice" in planned:
        live.source.post("/admin/faults", json={"kind": "repeat_page", "count": 1})
    con = live.con()
    try:
        before = orders_state(con, "core.orders")
        report_before = (
            daily_report(con) if warehouse.table_exists(con, "marts.daily_orders") else None
        )
        visible = len(live.source.app.state.world.orders())
        steps = [
            {
                "stage": "api",
                "title": "The order system",
                "text": f"It is 07:00 on {_day(morning)}. The order system holds {visible} orders."
                + (" It is about to fail 3 calls." if "api_down" in planned else "")
                + (" It is about to send one page twice." if "page_twice" in planned else ""),
                "count": f"{visible} orders",
                "cards": [],
                "tone": "",
            }
        ]

        # ---- Extract and load ------------------------------------------------------
        try:
            loaded = pipeline.extract_and_load(live.source, con, sleep=live.nap)
        except ExtractFailed as error:
            steps.append(
                {
                    "stage": "extract",
                    "title": "The load failed",
                    "text": str(error),
                    "count": "failed",
                    "cards": [],
                    "tone": "problem",
                }
            )
            outcome.append(
                {
                    "tone": "bad",
                    "you": "The order system kept failing.",
                    "it": "The pipeline gave up after 5 tries and loaded nothing. The report is unchanged.",
                    "from": "Milestone 2",
                }
            )
            return {
                "ok": False,
                "morning": morning,
                "steps": steps,
                "outcome": outcome,
                "state": state(live),
            }
        run_id = loaded["run_id"]
        landed = con.execute(
            "SELECT (payload->>'order_id')::int, payload->>'status', (payload->>'total_cents')::int - (payload->>'refunded_cents')::int, page_no "
            "FROM raw.orders_landing WHERE run_id = %s ORDER BY landing_id",
            [run_id],
        ).fetchall()
        different = len({row[0] for row in landed})
        retries = con.execute(
            "SELECT retries FROM ops.load_runs WHERE run_id = %s", [run_id]
        ).fetchone()[0]
        waited = ", ".join(f"{w:.0f}" for w in live.waits)
        sample = [_card(o, dollars(net), status) for o, status, net, _ in landed[-5:]]
        steps.append(
            {
                "stage": "extract",
                "title": "Extract: only what changed",
                "text": f"It asks for orders changed since the watermark, minus a 2-day lookback, and reads {len(landed)} rows in {loaded['api_calls']} calls."
                + (
                    f" {retries} calls failed. It waited {waited} seconds, one wait after each failure, and tried again."
                    if retries
                    else ""
                ),
                "count": f"{len(landed)} rows · {loaded['api_calls']} calls"
                + (f" · {retries} retries" if retries else ""),
                "cards": sample,
                "tone": "fix" if retries else "",
            }
        )
        if "api_down" in planned:
            outcome.append(
                {
                    "tone": "good",
                    "you": "You took the API down for 3 calls.",
                    "it": f"The pipeline did not break. {retries} calls failed, it waited {waited} seconds, tried again, "
                    "and finished. That is why the button seemed to do nothing: surviving is the point.",
                    "from": "Milestone 2: retry with backoff",
                }
            )
        if "page_twice" in planned:
            outcome.append(
                {
                    "tone": "good",
                    "you": "You made the API send one page twice.",
                    "it": f"{len(landed)} rows arrived, but they are only {different} different orders. "
                    "The pipeline keeps one row per order, so nothing is counted twice.",
                    "from": "Milestone 3: safe to run twice",
                }
            )
        raw_rows, raw_orders = con.execute(
            "SELECT count(*), count(DISTINCT payload->>'order_id') FROM raw.orders_landing"
        ).fetchone()
        steps.append(
            {
                "stage": "raw",
                "title": "Load: everything lands raw",
                "text": f"The {len(landed)} rows are appended exactly as received."
                + (
                    f" They hold only {different} different orders: a page arrived twice."
                    if "page_twice" in planned and different < len(landed)
                    else f" They hold {different} different orders."
                )
                + f" The raw table now has {raw_rows} rows for {raw_orders} orders.",
                "count": f"{raw_rows} rows · {raw_orders} orders",
                "cards": sample,
                "tone": "",
            }
        )
        if "bad_status" in planned and landed:
            payload = con.execute(
                "SELECT payload FROM raw.orders_landing WHERE run_id = %s ORDER BY landing_id DESC LIMIT 1",
                [run_id],
            ).fetchone()[0]
            payload.update(status="teleported", updated_at="2026-06-30T00:00:00Z")
            con.execute(
                "INSERT INTO raw.orders_landing (payload, run_id, page_no, loaded_at) VALUES (%s, 'bad_status', 1, now())",
                [Jsonb(payload)],
            )

        # ---- Transform and test -----------------------------------------------------
        try:
            result = pipeline.transform_and_test(con)
        except TransformFailed as error:
            failures = [name.split("__")[0] for name, _ in error.result.failures]
            kept = report_before is not None and daily_report(con) == report_before
            steps.append(
                {
                    "stage": "orders",
                    "title": "A table test failed",
                    "text": f"dbt built the orders table and tested it. The test {failures[0]} failed: a row has a status outside the list. "
                    f"What depends on that table was not built: {', '.join(error.result.skipped) or 'nothing'}.",
                    "count": f"{error.result.tests_failed} test FAILED",
                    "cards": [_card("table test", "FAIL", name, "bad") for name in failures],
                    "tone": "problem",
                }
            )
            steps.append(
                {
                    "stage": "report",
                    "title": "The report was not rebuilt",
                    "text": "The report "
                    + (
                        "still shows its last good numbers."
                        if kept
                        else "has no earlier numbers to keep."
                    )
                    + " Press 'Fix the source and run again'.",
                    "count": "kept as it was" if kept else "not built",
                    "cards": [],
                    "tone": "fix" if kept else "problem",
                }
            )
            outcome.append(
                {
                    "tone": "stopped",
                    "you": "You made the API send a row with a status nobody has seen: 'teleported'.",
                    "it": "This time the pipeline stopped, on purpose. A test on the table caught the bad row, and the "
                    "report was not rebuilt. "
                    + (
                        "The manager still sees the last good numbers."
                        if kept
                        else "There is no report yet."
                    )
                    + " Press 'Fix the source and run again'.",
                    "from": "Milestone 4: tests on the tables",
                }
            )
            return {
                "ok": False,
                "morning": morning,
                "bad_status": True,
                "steps": steps,
                "outcome": outcome,
                "state": state(live),
            }
        after = orders_state(con, "core.orders")
        new = sum(1 for order in after if order not in before)
        changed = sum(1 for order, now in after.items() if order in before and before[order] != now)
        steps.append(
            {
                "stage": "orders",
                "title": "Transform: one row per order, then tested",
                "text": f"dbt rebuilds the clean tables from the raw rows: {len(after)} orders, one row each. "
                f"Since the last run {new} are new and {changed} changed. {result.tests_passed} tests on the tables passed.",
                "count": f"{len(after)} orders · {result.tests_passed} tests passed",
                "cards": [
                    _card("new", str(new), "orders"),
                    _card("changed", str(changed), "orders"),
                    _card("unchanged", str(len(after) - new - changed), "orders"),
                ],
                "tone": "fix" if not new and not changed and before else "",
            }
        )
        answer = result.answer
        y, b = answer["yesterday"], answer["day_before"]
        live.last_morning = morning
        report = _report(live, con, morning)
        reasons = [
            f"{_day(c['order_date'])} changed by {dollars(c['revenue_cents_change'])}, because order {r['order_id']}: {r['what']}."
            for c in answer["earlier_changes"]
            for r in c["reasons"]
        ]
        steps.append(
            {
                "stage": "report",
                "title": "The manager's report",
                "text": f"Yesterday, {_day(y['order_date'])}: {y['orders']} orders, {dollars(y['revenue_cents'])}. "
                f"The day before: {b['orders']} orders, {dollars(b['revenue_cents'])}. "
                + (" ".join(reasons) if reasons else "No earlier day changed in this run.")
                + (
                    " Every day equals the answer key."
                    if report["all_right"]
                    else f" {report['wrong_days']} days differ from the answer key."
                ),
                "count": "equals the answer key"
                if report["all_right"]
                else "differs from the answer key",
                "cards": [
                    _card(
                        _day(y["order_date"]),
                        dollars(y["revenue_cents"]),
                        f"{y['orders']} orders",
                        "good",
                    ),
                    _card(
                        _day(b["order_date"]),
                        dollars(b["revenue_cents"]),
                        f"{b['orders']} orders",
                        "good",
                    ),
                    *[
                        _card(
                            _day(c["order_date"]),
                            dollars(c["revenue_cents_change"]),
                            c["reasons"][0]["what"],
                            "changed",
                        )
                        for c in answer["earlier_changes"]
                    ],
                ],
                "tone": "fix" if report["all_right"] else "problem",
            }
        )
        if same_morning_again:
            outcome.append(
                {
                    "tone": "good",
                    "you": "You ran the same morning a second time.",
                    "it": f"Nothing changed: {new} new orders, {changed} changed. The report is exactly as it was. "
                    "A pipeline that doubled its numbers here would be dangerous.",
                    "from": "Milestone 3: safe to run twice",
                }
            )
        for c in answer["earlier_changes"]:
            for r in c["reasons"]:
                outcome.append(
                    {
                        "tone": "changed",
                        "you": f"An earlier day changed: {_day(c['order_date'])} moved by {dollars(c['revenue_cents_change'])}.",
                        "it": f"Order {r['order_id']}: {r['what']}. The pipeline found it, corrected that old day, and "
                        "wrote down the reason. Loading only new orders would have missed this.",
                        "from": "Milestones 2 and 3: load what changed",
                    }
                )
        if fixed:
            outcome.insert(
                0,
                {
                    "tone": "good",
                    "you": "You fixed the source, and the morning ran again.",
                    "it": f"The bad row is gone. All {result.tests_passed} tests on the tables passed, so the report was rebuilt. "
                    "Nothing was lost while the pipeline was stopped: the run simply picked up where it left off.",
                    "from": "Milestones 3 and 4: safe re-runs, and tests on the tables",
                },
            )
        if not outcome:
            outcome.append(
                {
                    "tone": "good",
                    "you": f"You ran the morning of {_day(morning)}.",
                    "it": f"The pipeline read {len(landed)} rows, built {len(after)} orders, passed "
                    f"{result.tests_passed} tests, and made the report. "
                    + (
                        "Every day matches the answer key."
                        if report["all_right"]
                        else f"{report['wrong_days']} days do not match the answer key."
                    ),
                    "from": "The whole pipeline",
                }
            )
        return {
            "ok": True,
            "morning": morning,
            "run_id": run_id,
            "steps": steps,
            "outcome": outcome,
            "state": state(live),
        }
    finally:
        con.close()


class RunRequest(BaseModel):
    morning: str


class BreakRequest(BaseModel):
    kind: str


def create_live_app() -> FastAPI:
    try:
        live = Live()
    except ConnectionError as error:
        raise SystemExit(str(error)) from error
    app = FastAPI(title="Arden live pipeline")
    app.state.live = live

    @app.get("/")
    def page():
        return FileResponse(HERE / "index.html")

    @app.get("/api/state")
    def get_state():
        with live.lock:
            return state(live)

    @app.post("/api/run")
    def run(request: RunRequest):
        with live.lock:
            return run_morning(live, request.morning)

    @app.post("/api/break")
    def plan_break(request: BreakRequest):
        with live.lock:
            if request.kind in BREAKS and request.kind not in live.planned:
                live.planned.append(request.kind)
            return state(live)

    @app.post("/api/fix")
    def fix_source():
        """The source is fixed: take the bad row out of the raw table."""
        with live.lock:
            con = live.con()
            con.execute("DELETE FROM raw.orders_landing WHERE run_id = 'bad_status'")
            live.just_fixed = True
            con.close()
            return state(live)

    @app.post("/api/reset")
    def reset():
        with live.lock:
            live.start()
            return state(live)

    return app


if __name__ == "__main__":
    app = create_live_app()
    print(f"The live pipeline is at http://127.0.0.1:{PORT}   Stop it with Ctrl+C.")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
