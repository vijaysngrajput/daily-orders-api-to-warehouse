"""Build the numbers shown on milestone_4_flow.html and milestone_4_recap.html.

The pages must never show a number typed by hand. This script runs the real
pipeline against a private order system and a private database, and writes
what happened into the pages. The warehouse must be running.

    uv run python -m daily_orders.flow_data          write the pages' data
    uv run python -m daily_orders.flow_data --check  fail if a page is out of date
"""

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from daily_orders import transform, warehouse
from daily_orders.config import MILESTONE_DIR
from daily_orders.pipeline import run
from daily_orders.report import daily_report, day_totals, dollars
from daily_orders.transform import TransformFailed
from order_system.app import create_app
from order_system.catalog import morning_of
from order_system.truth import daily_report as true_report
from order_system.truth import day_totals as true_totals

PAGE = MILESTONE_DIR / "milestone_4_flow.html"
RECAP_PAGE = MILESTONE_DIR / "milestone_4_recap.html"
MARKER = re.compile(
    r'(<script id="page-data" type="application/json">)(.*?)(</script>)', flags=re.DOTALL
)
FLOW_DATABASE = "arden_m4_pages"

WORDS = {
    "elt": (
        "ELT",
        "Extract, load, transform. Load the raw data, then transform it inside the warehouse.",
    ),
    "dedupe": (
        "Deduplication",
        "Keeping one row per key. For each order, keep the newest version.",
    ),
    "enrich": (
        "Enrichment",
        "Adding columns from another table. A join puts two tables side by side where a key matches.",
    ),
    "incremental": (
        "Incremental model",
        "A model that, after its first run, rebuilds only part of its table. Here: the newest days.",
    ),
    "test": (
        "Table test",
        "A check on the data in a table. It is a query that returns the rows that break a rule, and passes when it returns none.",
    ),
    "model": ("Model", "One SELECT in a file. dbt builds a table or a view from it."),
    "fresh": (
        "Freshness",
        "How new the data in the warehouse is. The check: is the newest loaded row recent enough?",
    ),
    "layers": (
        "Staging, core, mart",
        "Three layers. Staging cleans the raw data. Core holds one row per real thing. A mart is what people read.",
    ),
}


def _word(key: str) -> dict:
    return {"term": WORDS[key][0], "meaning": WORDS[key][1]}


def _day(day: str | date) -> str:
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day.strftime("%a %m-%d")


def build() -> dict:
    ticks = {"now": 0.0}

    def nap(seconds: float) -> None:
        ticks["now"] += seconds

    api = TestClient(create_app(timer=lambda: ticks["now"]))
    world = api.app.state.world
    p4, p6 = world.planted["P4"], world.planted["P6"]

    def truth(day: str) -> dict:
        return true_totals(true_report(world.events, morning_of(date.fromisoformat(day))))

    def go(con, day: str):
        api.post("/admin/clock", json={"morning": day})
        return run(api, con, sleep=nap)

    def one(con, query: str, params=None):
        return con.execute(query, params).fetchone()

    def raw_cards(con, order_ids: list[int]) -> list[dict]:
        rows = con.execute(
            "SELECT (payload->>'order_id')::BIGINT, payload->>'status', "
            "(payload->>'total_cents')::BIGINT - (payload->>'refunded_cents')::BIGINT, run_id "
            "FROM raw.orders_landing WHERE (payload->>'order_id')::BIGINT = ANY(%s) ORDER BY 1, landing_id",
            [order_ids],
        ).fetchall()
        return [
            {
                "id": order_id,
                "amount": dollars(net),
                "sub": f"run {run_id[5:]} · {status}",
                "tone": "changed",
            }
            for order_id, status, net, run_id in rows
        ]

    def core_cards(con, order_ids: list[int], tone: str = "good") -> list[dict]:
        rows = con.execute(
            "SELECT order_id, order_date, city_name, status, net_cents FROM core.orders WHERE order_id = ANY(%s) ORDER BY 1",
            [order_ids],
        ).fetchall()
        return [
            {
                "id": order_id,
                "amount": dollars(net),
                "sub": f"{_day(day)} · {city} · {status}",
                "tone": tone,
            }
            for order_id, day, city, status, net in rows
        ]

    def report_cards(
        con, days: list[str], as_of: str, was: dict | None = None, tone: str | None = None
    ) -> list[dict]:
        report, right = day_totals(daily_report(con)), truth(as_of)
        cards = []
        for day in days:
            shown, real = report[day], right[day]
            ok = (shown["orders"], shown["revenue_cents"]) == (
                real["orders"],
                real["revenue_cents"],
            )
            note = (
                " · equals the truth"
                if ok
                else f" · truth: {real['orders']}, {dollars(real['revenue_cents'])}"
            )
            if was and day in was:
                note = f" · was {dollars(was[day])}"
            cards.append(
                {
                    "id": _day(day),
                    "amount": dollars(shown["revenue_cents"]),
                    "sub": f"{shown['orders']} orders{note}",
                    "tone": tone
                    or (("changed" if was and day in was else "good") if ok else "bad"),
                }
            )
        return cards

    def snapshot(con) -> tuple:
        return (
            con.execute(
                "SELECT order_id, status, total_cents, refunded_cents, is_deleted FROM core.orders ORDER BY 1"
            ).fetchall(),
            con.execute("SELECT * FROM core.order_items ORDER BY 1, 2").fetchall(),
            daily_report(con),
        )

    idle = {"count": "", "cards": []}
    steps: list[dict] = []
    warehouse.drop_database(FLOW_DATABASE)
    con = warehouse.connect(FLOW_DATABASE)
    database = warehouse.database_of(con)

    monday = go(con, "2026-06-08")
    friday_monday = day_totals(daily_report(con))["2026-06-05"]["revenue_cents"]
    late1 = one(con, "SELECT order_id FROM core.orders WHERE status = 'placed' ORDER BY 1 LIMIT 1")[
        0
    ]
    shown = [p4, late1]
    tuesday = go(con, "2026-06-09")
    raw_rows = one(con, "SELECT count(*) FROM raw.orders_landing")[0]
    orders_n, items_n = one(
        con, "SELECT (SELECT count(*) FROM core.orders), (SELECT count(*) FROM core.order_items)"
    )
    report_rows = one(con, "SELECT count(*) FROM marts.daily_orders")[0]
    change = tuesday.answer["earlier_changes"][0]
    api_cards = [
        {"id": o, "amount": dollars(api.get(f"/orders/{o}").json()["total_cents"] - api.get(f"/orders/{o}").json()["refunded_cents"]),
         "sub": api.get(f"/orders/{o}").json()["status"], "tone": "changed"}
        for o in shown
    ]  # fmt: skip
    api_stage = {"count": f"{orders_n} orders", "cards": api_cards}
    raw_stage = {"count": f"{raw_rows} rows · {orders_n} orders", "cards": raw_cards(con, shown)}
    core_stage = {
        "count": f"{orders_n} orders · {items_n} item lines",
        "cards": core_cards(con, shown),
    }

    steps.append(
        {
            "id": "before",
            "active": "orders",
            "problem": True,
            "clock": "Tue 06-09, 07:00",
            "title": "Where milestone 3 left it",
            "text": "The numbers are right. But every rule, such as 'a cancelled order is not a sale', is SQL inside a "
            "Python string. And nothing checks the tables after a run. A wrong table would look the same as a right one.",
            "word": _word("model"),
            "stages": {"api": api_stage, "extract": idle, "raw": raw_stage,
                       "orders": {"count": "built by a Python script", "cards": core_cards(con, shown, "")},
                       "report": {"count": "nobody checks it", "cards": report_cards(con, ["2026-06-08", "2026-06-05"], "2026-06-09", tone="")}},
            "moves": [],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "load",
            "active": "raw",
            "clock": "Tue 06-09, 07:00",
            "title": "Python extracts and loads. Then it stops",
            "text": f"Python reads {tuesday.rows_landed} changed rows, lands them raw, and saves its watermark. That is "
            f"all it does to the data now. The raw table holds {raw_rows} rows for {orders_n} orders. "
            "Every transformation after this is SQL in the warehouse.",
            "word": _word("elt"),
            "stages": {"api": api_stage,
                       "extract": {"count": f"{tuesday.rows_landed} rows · {tuesday.api_calls} calls", "cards": api_cards},
                       "raw": raw_stage, "orders": idle, "report": idle},
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "raw"}],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "staging",
            "active": "orders",
            "fix": True,
            "clock": "Tue 06-09, 07:00",
            "title": "Staging: cast, clean, one row per order",
            "text": f"The model stg_orders is one SELECT in a file. It gives every value its real type, one spelling, "
            f"and keeps the newest version of each order. {raw_rows} raw rows become {orders_n} rows, one per order.",
            "word": _word("dedupe"),
            "stages": {"api": api_stage, "extract": idle, "raw": raw_stage,
                       "orders": {"count": f"staging: {raw_rows} rows → {orders_n}", "cards": core_cards(con, shown)},
                       "report": idle},
            "moves": [{"from": "raw", "to": "orders"}],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "core",
            "active": "orders",
            "clock": "Tue 06-09, 07:00",
            "title": "Core: join, and add what is missing",
            "text": f"The model orders joins the staged orders to the list of cities, so each order gains its city's "
            f"name, and to the deletes. A second model keeps the item lines: {items_n} of them. "
            "Milestone 1 threw those away.",
            "word": _word("enrich"),
            "stages": {"api": api_stage, "extract": idle, "raw": raw_stage, "orders": core_stage, "report": idle},
            "moves": [],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "mart",
            "active": "report",
            "clock": "Tue 06-09, 07:00",
            "title": "The report rebuilds only its newest days",
            "text": f"daily_orders is an incremental model. It deletes the newest days and inserts them again; older "
            f"days are not touched. {_day(change['order_date'])} moves by {dollars(change['revenue_cents_change'])}: "
            f"order {change['reasons'][0]['order_id']}, {change['reasons'][0]['what']}.",
            "word": _word("incremental"),
            "stages": {"api": api_stage, "extract": idle, "raw": raw_stage, "orders": core_stage,
                       "report": {"count": f"{report_rows} rows · newest days rebuilt",
                                  "cards": report_cards(con, ["2026-06-08", "2026-06-07", "2026-06-05"], "2026-06-09", {"2026-06-05": friday_monday})}},
            "moves": [{"from": "orders", "to": "report"}],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "tests",
            "active": "report",
            "fix": True,
            "clock": "Tue 06-09, 07:00",
            "title": f"{tuesday.tests_passed} tests run on the tables",
            "text": f"dbt tests each table as soon as it is built. Is order_id unique? Is every status one of the five? "
            f"Does every item belong to an order? Does the report equal the sum of its orders? "
            f"All {tuesday.tests_passed} pass, so the manager may see the report.",
            "word": _word("test"),
            "stages": {"api": api_stage, "extract": idle, "raw": raw_stage, "orders": core_stage,
                       "report": {"count": f"{tuesday.tests_passed} tests passed",
                                  "cards": [
                                      {"id": "unique", "amount": "pass", "sub": "one order is one row", "tone": "good"},
                                      {"id": "accepted values", "amount": "pass", "sub": "five statuses only", "tone": "good"},
                                      {"id": "relationships", "amount": "pass", "sub": "every item has an order", "tone": "good"},
                                      {"id": "report = orders", "amount": "pass", "sub": "each day adds up", "tone": "good"},
                                  ]}},
            "moves": [],
        }
    )  # fmt: skip

    before = snapshot(con)
    again = go(con, "2026-06-09")
    same = snapshot(con) == before
    steps.append(
        {
            "id": "again",
            "active": "orders",
            "rerun": True,
            "fix": True,
            "clock": "Tue 06-09, 07:05",
            "title": "Run it again: nothing changes",
            "text": f"A second run lands {again.rows_landed} rows it already had. The models read all the raw rows and "
            f"keep the newest version of each order, so they build the same tables. The orders, the items and the "
            f"report are " + ("identical to before." if same else "DIFFERENT."),
            "word": _word("model"),
            "stages": {"api": api_stage,
                       "extract": {"count": f"{again.rows_landed} rows, again", "cards": []},
                       "raw": {"count": f"{one(con, 'SELECT count(*) FROM raw.orders_landing')[0]} rows · {orders_n} orders", "cards": raw_cards(con, [p4])},
                       "orders": {"count": f"{orders_n} orders · unchanged", "cards": core_cards(con, shown)},
                       "report": {"count": "unchanged", "cards": report_cards(con, ["2026-06-08", "2026-06-05"], "2026-06-09")}},
            "moves": [{"from": "extract", "to": "raw"}, {"from": "raw", "to": "orders"}],
        }
    )  # fmt: skip

    good_report = daily_report(con)
    payload = one(
        con,
        "SELECT payload FROM raw.orders_landing WHERE (payload->>'order_id')::BIGINT = %s ORDER BY landing_id DESC LIMIT 1",
        [p4],
    )[0]
    payload.update(status="teleported", updated_at="2026-06-30T00:00:00Z")
    con.execute(
        "INSERT INTO raw.orders_landing (payload, run_id, page_no, loaded_at) VALUES (%s, 'bad_data', 1, now())",
        [Jsonb(payload)],
    )
    api.post("/admin/clock", json={"morning": "2026-06-10"})
    try:
        run(api, con, sleep=nap)
        failure = None
    except TransformFailed as error:
        failure = error.result
    kept = daily_report(con) == good_report
    failed_test = failure.failures[0][0].split("__")[0] if failure else "NOTHING FAILED"
    steps.append(
        {
            "id": "bad_data",
            "active": "orders",
            "problem": True,
            "clock": "Wed 06-10, 07:00",
            "title": "The source sends a status nobody has seen",
            "text": f"Order {p4} arrives with the status 'teleported'. It lands raw, and the orders table is built. "
            f"Then the test {failed_test} finds 1 row outside its list, and fails. The report depends on that table, "
            f"so dbt skips it: {', '.join(failure.skipped) if failure else ''}.",
            "word": _word("test"),
            "stages": {"api": {"count": "a bad day", "cards": [{"id": p4, "amount": dollars(payload["total_cents"] - payload["refunded_cents"]), "sub": "status: teleported", "tone": "bad"}]},
                       "extract": idle,
                       "raw": {"count": "landed as received", "cards": [{"id": p4, "amount": "raw", "sub": "run bad_data · teleported", "tone": "bad"}]},
                       "orders": {"count": f"{failure.tests_failed if failure else 0} test FAILED", "cards": core_cards(con, [p4], "bad")},
                       "report": {"count": "skipped: not rebuilt", "cards": []}},
            "moves": [{"from": "raw", "to": "orders"}],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "kept",
            "active": "report",
            "fix": True,
            "clock": "Wed 06-10, 07:00",
            "title": "The report keeps its last good numbers",
            "text": "The run fails loudly and names the test. The report was "
            + ("not touched: it still shows the numbers that passed every test." if kept else "CHANGED.")
            + " An old right number is better than a new wrong one. Nothing is lost: the strange row is in the raw table.",
            "word": _word("test"),
            "stages": {"api": idle, "extract": idle,
                       "raw": {"count": "the bad row is kept", "cards": [{"id": p4, "amount": "raw", "sub": "run bad_data · teleported", "tone": "bad"}]},
                       "orders": {"count": "1 test FAILED", "cards": core_cards(con, [p4], "bad")},
                       "report": {"count": "yesterday's numbers, still right", "cards": report_cards(con, ["2026-06-08", "2026-06-05"], "2026-06-09")}},
            "moves": [],
        }
    )  # fmt: skip

    con.execute("DELETE FROM raw.orders_landing WHERE run_id = 'bad_data'")
    wednesday = go(con, "2026-06-10")
    monday_fix = next(
        c for c in wednesday.answer["earlier_changes"] if c["reasons"][0]["order_id"] == p6
    )
    steps.append(
        {
            "id": "fixed",
            "active": "report",
            "fix": True,
            "clock": "Wed 06-10, 07:30",
            "title": "The source is fixed, and the run passes",
            "text": f"The same morning again, with the bad row gone. {wednesday.tests_passed} tests pass and the report is "
            f"rebuilt. It also picks up the late cancellation of order {p6}: {_day(monday_fix['order_date'])} loses "
            f"1 order and {dollars(-monday_fix['revenue_cents_change'])}.",
            "word": _word("incremental"),
            "stages": {"api": idle, "extract": {"count": f"{wednesday.rows_landed} rows", "cards": []},
                       "raw": {"count": f"{one(con, 'SELECT count(*) FROM raw.orders_landing')[0]} rows", "cards": []},
                       "orders": {"count": f"{wednesday.tests_passed} tests passed", "cards": core_cards(con, [p4, p6])},
                       "report": {"count": "rebuilt", "cards": report_cards(con, ["2026-06-09", "2026-06-08"], "2026-06-10")}},
            "moves": [{"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )  # fmt: skip

    fresh = transform.source_freshness(database).success
    con.execute("UPDATE raw.orders_landing SET loaded_at = loaded_at - interval '3 days'")
    stale = transform.source_freshness(database)
    con.execute("UPDATE raw.orders_landing SET loaded_at = loaded_at + interval '3 days'")
    steps.append(
        {
            "id": "freshness",
            "active": "raw",
            "problem": True,
            "clock": "three days later",
            "title": "Right, but old",
            "text": "Suppose nobody runs the load for three days. Every table test still passes: the tables are valid, "
            "only old. A freshness check looks at the load time of the newest raw row. Just after a load it "
            + ("passes." if fresh else "FAILS.")
            + " After three days it "
            + ("fails: the data is stale." if not stale.success else "STILL PASSES."),
            "word": _word("fresh"),
            "stages": {"api": idle, "extract": {"count": "nobody ran it", "cards": []},
                       "raw": {"count": "newest row: 3 days old", "cards": [
                           {"id": "under 24 hours", "amount": "pass", "sub": "fresh", "tone": "good"},
                           {"id": "24 to 48 hours", "amount": "warn", "sub": "getting old", "tone": "changed"},
                           {"id": "over 48 hours", "amount": "error", "sub": "stale: this is now", "tone": "bad"}]},
                       "orders": {"count": "valid, and old", "cards": []},
                       "report": {"count": "valid, and old", "cards": []}},
            "moves": [],
        }
    )  # fmt: skip

    before = snapshot(con)
    for schema in warehouse.DBT_SCHEMAS:
        con.execute(f"DROP SCHEMA {schema} CASCADE")
    rebuilt = transform.build(database)
    identical = snapshot(con) == before
    steps.append(
        {
            "id": "rebuild",
            "active": "orders",
            "fix": True,
            "clock": "Wed 06-10, 08:00",
            "title": "Drop every clean table. Build them back from raw",
            "text": f"Everything dbt built is dropped, as if a rule had been wrong for a month. One command builds "
            f"{rebuilt.models_built} models from the raw table alone. The order system is not asked. The result is "
            + ("identical." if identical else "DIFFERENT.")
            + " That is what loading raw first buys you.",
            "word": _word("layers"),
            "stages": {"api": {"count": "not asked", "cards": []}, "extract": idle,
                       "raw": {"count": "the only input", "cards": raw_cards(con, [p4])},
                       "orders": {"count": "staging → core, rebuilt", "cards": core_cards(con, [p4, p6])},
                       "report": {"count": "mart, rebuilt", "cards": report_cards(con, ["2026-06-09", "2026-06-08", "2026-06-05"], "2026-06-10")}},
            "moves": [{"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )  # fmt: skip
    steps.append(
        {
            "id": "still_wrong",
            "active": "extract",
            "problem": True,
            "clock": "every morning, 07:00",
            "title": "Still wrong: a person must remember to run it",
            "text": "The pipeline is right, safe to repeat, and checked. And every run in this story started with a "
            "person typing a command. If they are ill, there is no report. If the order system is down at 07:00, "
            "someone must try again later. Milestone 5 puts it on a schedule.",
            "word": _word("fresh"),
            "stages": {"api": idle, "extract": {"count": "waits for a person", "cards": []},
                       "raw": idle, "orders": {"count": f"{wednesday.tests_passed} tests on every run", "cards": []},
                       "report": {"count": "right, when someone runs it", "cards": report_cards(con, ["2026-06-09", "2026-06-08"], "2026-06-10")}},
            "moves": [],
        }
    )  # fmt: skip

    follow = {
        str(p4): {
            "before": "One row, right. But the rule that produced it is hidden in a script.",
            "load": "Its refunded version is in the raw table, beside the older one.",
            "staging": "stg_orders numbers its versions, newest first, and keeps number 1.",
            "core": "It gains its city's name, Northgate, and four item lines.",
            "mart": f"Its refund lowers {_day(change['order_date'])} by {dollars(-change['revenue_cents_change'])}. That day is inside the window, so it is rebuilt.",
            "tests": "It passes every test: one row, a known status, a known city, a total that equals its items.",
            "again": "Read again, staged again, and exactly the same row.",
            "bad_data": "The source sends it with the status 'teleported'. The test on status fails because of this one row.",
            "kept": "Its bad version did not reach the report.",
            "fixed": "Back to 'delivered'. It passes again.",
            "freshness": "Its row is valid. It is just three days old, like everything else.",
            "rebuild": "Built again from its raw rows, and identical.",
            "still_wrong": "Right, and waiting for someone to press run.",
        },
        str(late1): {
            "before": "One row, 'delivered'.",
            "load": "Two raw rows: 'placed' from Monday's run, 'delivered' from Tuesday's.",
            "staging": "The newest version wins: 'delivered'.",
            "core": "One row in orders, with its city name.",
            "mart": "It is counted once, on Sunday.",
            "tests": "It passes every test.",
            "again": "Unchanged.",
            "bad_data": "Not affected. But its day is not rebuilt either: the whole report is skipped.",
            "kept": "Still counted once, in yesterday's good report.",
            "fixed": "Unchanged.",
            "freshness": "Valid, and three days old.",
            "rebuild": "Built again from raw, and identical.",
            "still_wrong": "Right, and waiting for someone to press run.",
        },
        str(p6): {
            "before": "In the warehouse as 'out for delivery'. Its cancellation has not reached the API yet.",
            "load": "Landed once, as 'out for delivery'.",
            "staging": "One version so far.",
            "core": "One row, counted as a live order of Monday.",
            "mart": "Counted on Monday. That is what the source says so far.",
            "tests": "It passes every test. A test cannot know what the source has not said yet.",
            "again": "Unchanged.",
            "bad_data": "Its late cancellation is landed raw in this run. But the report is skipped, so Monday does not move yet.",
            "kept": "Still counted as live in the last good report.",
            "fixed": f"Now the run passes, and its cancellation reaches the report: {_day(monday_fix['order_date'])} loses 1 order.",
            "freshness": "Valid, and three days old.",
            "rebuild": "Built again from raw, as 'cancelled'.",
            "still_wrong": "Right, and waiting for someone to press run.",
        },
    }
    for step in steps:
        step["follow"] = {case: lines[step["id"]] for case, lines in follow.items()}

    con.close()
    warehouse.drop_database(FLOW_DATABASE)

    recap = {
        "dbt": {"models": tuesday.models_built, "tests": tuesday.tests_passed},
        "load": {"read": tuesday.rows_landed, "raw": raw_rows, "orders": orders_n, "items": items_n, "report_rows": report_rows},
        "refund": {"order": p4, "by": dollars(-change["revenue_cents_change"]), "day": _day(change["order_date"])},
        "again": {"same": "identical" if same else "DIFFERENT", "read": again.rows_landed},
        "bad": {
            "order": p4,
            "test": failed_test,
            "skipped": ", ".join(failure.skipped) if failure else "",
            "kept": "kept its last good numbers" if kept else "CHANGED",
        },
        "fresh": {"now": "pass" if fresh else "FAIL", "later": "error: stale" if not stale.success else "PASS"},
        "rebuild": {"models": rebuilt.models_built, "same": "identical" if identical else "DIFFERENT"},
        "monday": {"y": _day(monday.answer["yesterday"]["order_date"]), "orders": monday.answer["yesterday"]["orders"],
                   "money": dollars(monday.answer["yesterday"]["revenue_cents"])},
    }  # fmt: skip
    return {
        "recap": recap,
        "milestone": 4,
        "of": 5,
        "title": "Models and tests",
        "lede": "Python extracts and loads. Everything after that is SQL in dbt models, in three layers, "
        "and every table is tested as soon as it is built. Watch a bad row get stopped.",
        "stages": [
            {"id": "api", "name": "Order system API", "role": "Source", "state": "given"},
            {
                "id": "extract",
                "name": "Extract",
                "role": "Ingest · Python",
                "state": "built",
                "badge": "as before",
            },
            {
                "id": "raw",
                "name": "Raw landing table",
                "role": "Store · raw layer",
                "state": "built",
                "badge": "append, as before",
            },
            {
                "id": "orders",
                "name": "Clean orders table",
                "role": "dbt · staging, core",
                "state": "new",
                "badge": "new: dbt models",
            },
            {
                "id": "report",
                "name": "Daily report",
                "role": "dbt · mart",
                "state": "new",
                "badge": "new: incremental model",
            },
        ],
        "schedule": {
            "state": "by_hand",
            "text": "By hand. A person starts every run. A schedule arrives in milestone 5.",
        },
        "tests": {
            "state": "new",
            "text": f"New. {tuesday.tests_passed} tests run on the tables after every build. A failed test stops the report.",
        },
        "cases": [
            {"id": str(p4), "label": f"Order {p4}", "hint": "refunded, then a bad status"},
            {"id": str(late1), "label": f"Order {late1}", "hint": "an ordinary order"},
            {"id": str(p6), "label": f"Order {p6}", "hint": "cancelled late"},
        ],
        "steps": steps,
        "still_wrong": "A person must remember to run it every morning.",
        "next": "Milestone 5: on a schedule.",
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
    """The numbers shown on milestone_4_recap.html."""
    recap = dict((flow or build())["recap"])
    glossary = (MILESTONE_DIR.parent.parent / "GLOSSARY.md").read_text(encoding="utf-8")
    recap["numbers"] = {
        "notebooks": len(list((MILESTONE_DIR / "notebooks").glob("0*.ipynb"))),
        "tests": count_tests(),
        "tables": recap["dbt"]["models"],  # what dbt builds: 3 views, 3 tables and 1 seed
        "rows": recap["dbt"]["tests"],
        "words": len(
            re.findall(r"^\| \*\*.+\| Milestone 4, notebook \d \|", glossary, flags=re.MULTILINE)
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
