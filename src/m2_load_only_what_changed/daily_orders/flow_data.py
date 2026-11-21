"""Build the numbers shown on milestone_2_flow.html and milestone_2_recap.html.

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
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from daily_orders import warehouse
from daily_orders.config import BUSINESS_TZ, MILESTONE_DIR
from daily_orders.extract import fetch_changed_orders
from daily_orders.pipeline import run
from daily_orders.report import daily_report, day_totals, dollars
from order_system.app import PAGE_SIZE, RATE_LIMIT_CALLS, create_app
from order_system.catalog import morning_of
from order_system.truth import daily_report as true_report
from order_system.truth import day_totals as true_totals

PAGE = MILESTONE_DIR / "milestone_2_flow.html"
RECAP_PAGE = MILESTONE_DIR / "milestone_2_recap.html"
MARKER = re.compile(
    r'(<script id="page-data" type="application/json">)(.*?)(</script>)', flags=re.DOTALL
)
ARDEN = ZoneInfo(BUSINESS_TZ)
FLOW_DATABASE = "arden_m2_pages"

WORDS = {
    "full_load": ("Full load", "It reads every row on every run. Milestone 1 did this."),
    "landing": (
        "Raw landing table",
        "The raw copy of what arrived. Every order is kept exactly as the API sent it.",
    ),
    "watermark": (
        "Watermark",
        "The saved value that marks how far the last run read. Here: the largest updated_at loaded.",
    ),
    "incremental": ("Incremental load", "It reads only rows that are new or changed."),
    "key": (
        "Incremental key",
        "The column that says a row changed. Pick a key that changes whenever the row changes. Here: updated_at.",
    ),
    "retry": (
        "Retry with backoff",
        "Try a failed call again after a wait. Each wait is longer than the last, and the tries are capped.",
    ),
    "append": (
        "Append",
        "Add rows at the end of a table. It never touches the rows that are already there.",
    ),
    "metadata": (
        "Load metadata",
        "Columns that describe the load, not the data: which run loaded the row, and when.",
    ),
    "pagination": (
        "Pagination",
        "An API returns rows in pages. You ask again until it says it is done.",
    ),
    "cdc": (
        "Query-based CDC",
        "Change data capture by polling with an updated_at query. It sees the latest state. It cannot see deletes.",
    ),
}


def _word(key: str) -> dict:
    return {"term": WORDS[key][0], "meaning": WORDS[key][1]}


def _day(day: str | date) -> str:
    """2026-06-06 -> Sat 06-06"""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day.strftime("%a %m-%d")


def _short(utc_text: str | None) -> str:
    """2026-06-08T03:48:53Z -> 06-08 03:48Z"""
    return utc_text[5:16].replace("T", " ") + "Z" if utc_text else "none"


def _local_day(order: dict) -> str:
    moment = datetime.fromisoformat(order["placed_at"].replace("Z", "+00:00")).astimezone(ARDEN)
    return _day(moment.date())


def build() -> dict:
    # A pretend wall clock: waiting costs no real time, but the API's rate limit still sees it pass.
    ticks = {"now": 0.0}

    def nap(seconds: float) -> None:
        ticks["now"] += seconds

    api = TestClient(create_app(timer=lambda: ticks["now"]))
    world = api.app.state.world
    planted = world.planted
    warehouse.drop_database(FLOW_DATABASE)
    con = warehouse.connect(FLOW_DATABASE)

    def morning(day: str) -> None:
        api.post("/admin/clock", json={"morning": day})

    def source(order_id: int) -> dict:
        return api.get(f"/orders/{order_id}").json()

    def truth(day: str) -> dict:
        return true_totals(true_report(world.events, morning_of(date.fromisoformat(day))))

    def landed_rows(order_ids: list[int]) -> list[dict]:
        rows = con.execute(
            "SELECT landing_id, order_id, order_date, status, total_cents - refunded_cents, run_id "
            "FROM analytics.orders_as_landed WHERE order_id = ANY(%s) ORDER BY order_id, landing_id",
            [order_ids],
        ).fetchall()
        names = ("landing_id", "order_id", "order_date", "status", "net", "run_id")
        return [dict(zip(names, row)) for row in rows]

    def counts() -> tuple[int, int]:
        return con.execute(
            "SELECT count(*), count(DISTINCT order_id) FROM analytics.orders_as_landed"
        ).fetchone()

    def source_cards(order_ids: list[int], tones: dict | None = None) -> list[dict]:
        cards = []
        for order_id in order_ids:
            order = source(order_id)
            cards.append(
                {
                    "id": order_id,
                    "amount": dollars(order["total_cents"] - order["refunded_cents"]),
                    "sub": f"changed {_short(order['updated_at'])} · {order['status']}",
                    "tone": (tones or {}).get(order_id, ""),
                }
            )
        return cards

    def raw_cards(order_ids: list[int]) -> list[dict]:
        """One card per landed row. An order that landed more than once is marked."""
        rows = landed_rows(order_ids)
        seen = [row["order_id"] for row in rows]
        return [
            {
                "id": row["order_id"],
                "amount": dollars(row["net"]),
                "sub": f"run {row['run_id'][5:]} · {row['status']}",
                "tone": "changed" if seen.count(row["order_id"]) > 1 else "",
            }
            for row in rows
        ]

    def view_cards(order_ids: list[int]) -> list[dict]:
        rows = landed_rows(order_ids)
        seen = [row["order_id"] for row in rows]
        return [
            {
                "id": row["order_id"],
                "amount": dollars(row["net"]),
                "sub": f"{_day(row['order_date'])} · {row['status']}",
                "tone": "bad" if seen.count(row["order_id"]) > 1 else "",
            }
            for row in rows
        ]

    def report_cards(days: list[str], as_of: str) -> list[dict]:
        report, right = day_totals(daily_report(con)), truth(as_of)
        cards = []
        for day in days:
            shown, real = report[day], right[day]
            wrong = (shown["orders"], shown["revenue_cents"]) != (
                real["orders"],
                real["revenue_cents"],
            )
            cards.append(
                {
                    "id": _day(day),
                    "amount": dollars(shown["revenue_cents"]),
                    "sub": f"{shown['orders']} orders"
                    + (
                        f" · truth: {real['orders']}, {dollars(real['revenue_cents'])}"
                        if wrong
                        else " · right"
                    ),
                    "tone": "bad" if wrong else "good",
                }
            )
        return cards

    def wrong_days(as_of: str) -> list[str]:
        report, right = day_totals(daily_report(con)), truth(as_of)
        return [
            d
            for d in right
            if (report[d]["orders"], report[d]["revenue_cents"])
            != (right[d]["orders"], right[d]["revenue_cents"])
        ]

    p4, p6 = planted["P4"], planted["P6"]
    idle = {"count": "", "cards": []}
    steps: list[dict] = []

    # ---- Where milestone 1 broke: full loads, morning by morning ---------------
    growth = []
    for day in ("2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"):
        morning(day)
        full = fetch_changed_orders(api, None, nap)
        orders_now = len({o["order_id"] for o in full.orders})
        calls = -(-orders_now // PAGE_SIZE)
        growth.append(
            {
                "day": _day(day),
                "orders": orders_now,
                "calls": calls,
                "ok": calls <= RATE_LIMIT_CALLS,
            }
        )
    refused = next(g for g in growth if not g["ok"])
    steps.append(
        {
            "id": "broke",
            "active": "extract",
            "problem": True,
            "clock": "Thu 06-11, 07:00",
            "title": "Where milestone 1 broke",
            "text": f"A full load reads every order, every morning. On {refused['day']} it needs {refused['calls']} "
            f"calls. The API allows {RATE_LIMIT_CALLS} in 10 seconds, so the load was refused and there was no report.",
            "word": _word("full_load"),
            "stages": {
                "api": {"count": f"{refused['orders']} orders", "cards": []},
                "extract": {
                    "count": "full load, each morning",
                    "cards": [
                        {
                            "id": g["day"],
                            "amount": f"{g['calls']} calls",
                            "sub": f"{g['orders']} orders" + ("" if g["ok"] else " · refused"),
                            "tone": "" if g["ok"] else "bad",
                        }
                        for g in growth
                    ],
                },
                "raw": idle,
                "orders": idle,
                "report": {"count": "no report", "cards": []},
            },
            "moves": [],
        }
    )

    # ---- Monday: the first load -----------------------------------------------
    morning("2026-06-08")
    monday = run(api, con)
    sunday_late = [
        row[0]
        for row in con.execute(
            "SELECT order_id FROM analytics.orders_as_landed WHERE status = 'placed' ORDER BY order_id LIMIT 2"
        )
    ]
    shown = [p4, *sunday_late]
    monday_source = source_cards(shown)
    steps.append(
        {
            "id": "first_load",
            "active": "raw",
            "clock": "Mon 06-08, 07:00",
            "title": "Monday: everything lands, exactly as received",
            "text": f"There is no watermark yet, so the first run reads everything: {monday.rows_landed} orders in "
            f"{monday.api_calls} calls. Each one is kept whole, as JSON, in the raw landing table.",
            "word": _word("landing"),
            "stages": {
                "api": {"count": f"{monday.rows_landed} orders", "cards": monday_source},
                "extract": {
                    "count": f"{monday.rows_landed} orders · {monday.api_calls} calls",
                    "cards": monday_source,
                },
                "raw": {"count": f"{counts()[0]} rows", "cards": raw_cards(shown)},
                "orders": idle,
                "report": idle,
            },
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "raw"}],
        }
    )
    steps.append(
        {
            "id": "watermark",
            "active": "extract",
            "clock": "Mon 06-08, 07:00",
            "title": "The pipeline remembers where it stopped",
            "text": f"The newest change it read was at {monday.watermark_after}. That value is saved as the "
            "watermark, in the same transaction as the rows. It is not the time the job ran.",
            "word": _word("watermark"),
            "stages": {
                "api": {"count": f"{monday.rows_landed} orders", "cards": monday_source},
                "extract": {"count": f"watermark {_short(monday.watermark_after)}", "cards": []},
                "raw": {"count": f"{counts()[0]} rows", "cards": raw_cards(shown)},
                "orders": {"count": f"{counts()[0]} rows", "cards": view_cards(shown)},
                "report": {
                    "count": "morning of Mon 06-08",
                    "cards": report_cards(["2026-06-07", "2026-06-06"], "2026-06-08"),
                },
            },
            "moves": [{"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )

    # ---- Tuesday: only what changed -------------------------------------------
    morning("2026-06-09")
    source_total = len(fetch_changed_orders(api, None, nap).orders)
    morning("2026-06-09")
    api.post("/admin/faults", json={"kind": "error_500", "count": 2})
    retry_waits: list[float] = []
    tuesday = run(api, con, sleep=lambda s: (retry_waits.append(s), nap(s)))
    tuesday_source = source_cards(shown, {p4: "changed", **dict.fromkeys(sunday_late, "changed")})
    tuesday_api = {"count": f"{source_total} orders", "cards": tuesday_source}
    steps.append(
        {
            "id": "incremental",
            "active": "extract",
            "fix": True,
            "clock": "Tue 06-09, 07:00",
            "title": "Tuesday: ask only for what changed",
            "text": f"The source holds {source_total} orders. The pipeline asks only for orders changed since the "
            f"watermark, and gets {tuesday.rows_landed}. A full load would have needed "
            f"{-(-source_total // PAGE_SIZE)} calls.",
            "word": _word("incremental"),
            "stages": {
                "api": tuesday_api,
                "extract": {
                    "count": f"{tuesday.rows_landed} orders · since {_short(tuesday.watermark_before)}",
                    "cards": tuesday_source,
                },
                "raw": {"count": f"{monday.rows_landed} rows", "cards": []},
                "orders": idle,
                "report": idle,
            },
            "moves": [{"from": "api", "to": "extract"}],
        }
    )
    steps.append(
        {
            "id": "retry",
            "active": "extract",
            "clock": "Tue 06-09, 07:00",
            "title": "The API says no, twice",
            "text": f"Two calls fail with status 500. The pipeline waits {retry_waits[0]:.0f} second, then "
            f"{retry_waits[1]:.0f}, and asks again. It finishes after {tuesday.api_calls} calls. "
            "After five failed tries it would stop and load nothing.",
            "word": _word("retry"),
            "stages": {
                "api": {"count": "500, 500, then 200", "cards": tuesday_source},
                "extract": {
                    "count": f"{tuesday.retries} retries · {tuesday.api_calls} calls",
                    "cards": [
                        {
                            "id": "try 1",
                            "amount": "500",
                            "sub": f"wait {retry_waits[0]:.0f} second",
                            "tone": "changed",
                        },
                        {
                            "id": "try 2",
                            "amount": "500",
                            "sub": f"wait {retry_waits[1]:.0f} seconds",
                            "tone": "changed",
                        },
                        {"id": "try 3", "amount": "200", "sub": "the page arrives", "tone": "good"},
                    ],
                },
                "raw": {"count": f"{monday.rows_landed} rows", "cards": []},
                "orders": idle,
                "report": idle,
            },
            "moves": [],
        }
    )
    rows2, orders2 = counts()
    steps.append(
        {
            "id": "append",
            "active": "raw",
            "clock": "Tue 06-09, 07:00",
            "title": "The changed orders are added at the end",
            "text": f"{tuesday.rows_landed} rows are appended. Monday's {monday.rows_landed} rows are not touched. "
            f"The table now holds {rows2} rows.",
            "word": _word("append"),
            "stages": {
                "api": tuesday_api,
                "extract": {"count": f"{tuesday.rows_landed} orders", "cards": tuesday_source},
                "raw": {"count": f"{rows2} rows", "cards": raw_cards(shown)},
                "orders": idle,
                "report": idle,
            },
            "moves": [{"from": "extract", "to": "raw"}],
        }
    )
    versions = landed_rows([p4])
    steps.append(
        {
            "id": "metadata",
            "active": "raw",
            "clock": "Tue 06-09, 07:00",
            "title": "Every version is kept, with the run that loaded it",
            "text": f"Order {p4} is here twice: {dollars(versions[0]['net'])} from run {versions[0]['run_id']}, before "
            f"its refund, and {dollars(versions[1]['net'])} from run {versions[1]['run_id']}, after it. "
            "Finance can see exactly what changed, and when.",
            "word": _word("metadata"),
            "stages": {
                "api": tuesday_api,
                "extract": {"count": f"{tuesday.rows_landed} orders", "cards": tuesday_source},
                "raw": {"count": f"{rows2} rows · {orders2} orders", "cards": raw_cards(shown)},
                "orders": idle,
                "report": idle,
            },
            "moves": [],
        }
    )
    wrong2 = wrong_days("2026-06-09")
    friday = day_totals(daily_report(con))["2026-06-05"]
    friday_true = truth("2026-06-09")["2026-06-05"]
    steps.append(
        {
            "id": "report_wrong",
            "active": "report",
            "problem": True,
            "clock": "Tue 06-09, 07:00",
            "title": "The problem: the report counts both versions",
            "text": f"The report adds up every landed row. {_day('2026-06-05')} shows {friday['orders']} orders and "
            f"{dollars(friday['revenue_cents'])}. The truth is {friday_true['orders']} orders and "
            f"{dollars(friday_true['revenue_cents'])}. {rows2 - orders2} orders are in the table twice. No error shows.",
            "word": _word("append"),
            "stages": {
                "api": tuesday_api,
                "extract": {"count": f"{tuesday.rows_landed} orders", "cards": tuesday_source},
                "raw": {"count": f"{rows2} rows · {orders2} orders", "cards": raw_cards(shown)},
                "orders": {"count": f"{rows2} rows, not {orders2}", "cards": view_cards(shown)},
                "report": {
                    "count": f"{len(wrong2)} days wrong",
                    "cards": report_cards(wrong2, "2026-06-09"),
                },
            },
            "moves": [{"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )

    # ---- Run Tuesday again ----------------------------------------------------
    morning("2026-06-09")
    again = run(api, con)
    boundary = con.execute(
        "SELECT order_id FROM analytics.orders_as_landed WHERE run_id = %s", [again.run_id]
    ).fetchone()[0]
    monday_shown = day_totals(daily_report(con))["2026-06-08"]
    monday_true = truth("2026-06-09")["2026-06-08"]
    wrong3 = wrong_days("2026-06-09")
    steps.append(
        {
            "id": "again",
            "active": "raw",
            "rerun": True,
            "problem": True,
            "clock": "Tue 06-09, 07:05",
            "title": "Run Tuesday again: it gets worse",
            "text": f"Nothing changed in the source. But the filter includes the watermark itself, so order "
            f"{boundary} is read again, and append adds it again. {_day('2026-06-08')} now shows "
            f"{monday_shown['orders']} orders, not {monday_true['orders']}.",
            "word": _word("watermark"),
            "stages": {
                "api": tuesday_api,
                "extract": {
                    "count": f"{again.rows_landed} order · since {_short(again.watermark_before)}",
                    "cards": source_cards([boundary], {boundary: "changed"}),
                },
                "raw": {
                    "count": f"{counts()[0]} rows · {counts()[1]} orders",
                    "cards": raw_cards([boundary, p4]),
                },
                "orders": {"count": f"{counts()[0]} rows", "cards": view_cards([boundary, p4])},
                "report": {
                    "count": f"{len(wrong3)} days wrong",
                    "cards": report_cards(wrong3, "2026-06-09"),
                },
            },
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "raw"}],
        }
    )

    # ---- Wednesday, then Thursday with the repeated page -----------------------
    morning("2026-06-10")
    run(api, con)
    morning("2026-06-11")
    thursday = run(api, con)
    pages = con.execute(
        "SELECT page_no, count(*), min((payload->>'order_id')::int), max((payload->>'order_id')::int) "
        "FROM raw.orders_landing WHERE run_id = %s GROUP BY page_no ORDER BY page_no",
        [thursday.run_id],
    ).fetchall()
    thursday_orders = con.execute(
        "SELECT count(DISTINCT order_id) FROM analytics.orders_as_landed WHERE run_id = %s",
        [thursday.run_id],
    ).fetchone()[0]
    rows4, orders4 = counts()
    steps.append(
        {
            "id": "repeated_page",
            "active": "extract",
            "problem": True,
            "clock": "Thu 06-11, 07:00",
            "title": "Thursday: the API serves one page twice",
            "text": f"The order system sends page 2 a second time. Real APIs do this. The run lands "
            f"{thursday.rows_landed} rows for {thursday_orders} orders, and append keeps them all.",
            "word": _word("pagination"),
            "stages": {
                "api": {"count": "page 2, twice", "cards": []},
                "extract": {
                    "count": f"{thursday.rows_landed} rows · {thursday.api_calls} calls",
                    "cards": [
                        {
                            "id": f"page {page}",
                            "amount": f"{n} rows",
                            "sub": f"orders {first} to {last}",
                            "tone": "bad" if page > 1 else "",
                        }
                        for page, n, first, last in pages
                    ],
                },
                "raw": {"count": f"{rows4} rows · {orders4} orders", "cards": []},
                "orders": {"count": f"{rows4} rows", "cards": []},
                "report": idle,
            },
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "raw"}],
        }
    )
    late_source = source(p6)
    late_landed = landed_rows([p6])
    steps.append(
        {
            "id": "late",
            "active": "extract",
            "problem": True,
            "clock": "Thu 06-11, 07:00",
            "title": "One change is never read at all",
            "text": f"Order {p6} was refused at the door on Monday evening. The driver's phone was offline, so the "
            f"API showed it on Tuesday, with an updated_at of {_short(late_source['updated_at'])}. By then the "
            "watermark had passed that time. The warehouse still says it is on its way.",
            "word": _word("cdc"),
            "stages": {
                "api": {"count": "the source", "cards": source_cards([p6], {p6: "changed"})},
                "extract": {"count": f"watermark {_short(thursday.watermark_after)}", "cards": []},
                "raw": {"count": f"order {p6}, as landed", "cards": raw_cards([p6])},
                "orders": {
                    "count": "still counted",
                    "cards": [
                        {
                            "id": p6,
                            "amount": dollars(late_landed[-1]["net"]),
                            "sub": f"{_day(late_landed[-1]['order_date'])} · {late_landed[-1]['status']}",
                            "tone": "bad",
                        }
                    ],
                },
                "report": idle,
            },
            "moves": [],
        }
    )
    wrong4 = wrong_days("2026-06-11")
    steps.append(
        {
            "id": "still_wrong",
            "active": "report",
            "problem": True,
            "clock": "Thu 06-11, 07:00",
            "title": "Still wrong: one order, many rows",
            "text": f"After four mornings the landing table holds {rows4} rows for {orders4} orders, and "
            f"{len(wrong4)} days of the report are wrong. The raw table is right to keep everything. "
            "The layer above it must hold one row per order. Milestone 3 builds it.",
            "word": _word("append"),
            "stages": {
                "api": {"count": f"{orders4} orders", "cards": []},
                "extract": {"count": "safe from loss", "cards": []},
                "raw": {
                    "count": f"{rows4} rows · {orders4} orders",
                    "cards": raw_cards([p4, boundary]),
                },
                "orders": {
                    "count": f"{rows4} rows, not {orders4}",
                    "cards": view_cards([p4, boundary]),
                },
                "report": {
                    "count": f"{len(wrong4)} days wrong",
                    "cards": report_cards(wrong4[:5], "2026-06-11"),
                },
            },
            "moves": [{"from": "raw", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )

    # ---- Follow one order through every step ----------------------------------
    late1 = sunday_late[0]
    net = [dollars(v["net"]) for v in versions]
    follow = {
        str(p4): {
            "broke": "Delivered on Friday. A full load read it again every morning.",
            "first_load": f"It lands on Monday at {net[0]}. Its refund has not happened yet.",
            "watermark": "It last changed on Friday, long before the watermark.",
            "incremental": "It was refunded on Monday at 11:00, so its updated_at moved. It comes back.",
            "retry": "It is on the page that fails twice. The third try brings it.",
            "append": f"Its new version lands at {net[1]}. The old row stays where it was.",
            "metadata": f"Two rows: {net[0]} from Monday's run, {net[1]} from Tuesday's.",
            "report_wrong": f"The report adds both: {net[0]} and {net[1]}. Friday is {dollars(versions[0]['net'])} too high.",
            "again": "It is not read again. It is still counted twice.",
            "repeated_page": "It did not change. It is still counted twice.",
            "late": "It is still counted twice.",
            "still_wrong": "One order, two rows, counted twice.",
        },
        str(late1): {
            "broke": "Placed late on Sunday evening.",
            "first_load": "It lands on Monday as 'placed'. It is not delivered yet.",
            "watermark": "It is one of the last orders before the watermark.",
            "incremental": "It was delivered on Monday, so its updated_at moved. It comes back.",
            "retry": "It waits through the two failed calls like every other order.",
            "append": "It lands again, now 'delivered'. The 'placed' row stays.",
            "metadata": "Two rows: 'placed' from Monday's run, 'delivered' from Tuesday's.",
            "report_wrong": "Only its status changed, not its money. The report still counts it as two orders.",
            "again": "It is not read again. It is still counted twice.",
            "repeated_page": "It did not change. It is still counted twice.",
            "late": "It is still counted twice.",
            "still_wrong": "One order, two rows, counted twice.",
        },
        str(p6): {
            "broke": "Not placed yet.",
            "first_load": "Not placed yet. It is ordered on Monday evening.",
            "watermark": "Not placed yet.",
            "incremental": "Placed on Monday evening. Tuesday's run reads it as 'out for delivery'.",
            "retry": "It arrives with the other changed orders.",
            "append": "It lands once, as 'out for delivery'.",
            "metadata": "One row so far.",
            "report_wrong": "It is counted once, as a live order. That is already wrong: it was refused at the door.",
            "again": "The API now shows it as cancelled. Its updated_at is before the watermark, so it is not read.",
            "repeated_page": "Still not read. Its updated_at is in the past.",
            "late": f"In the source: cancelled. In the warehouse: {late_landed[-1]['status']}. This load will never fix it.",
            "still_wrong": "One order, one row, and that row is out of date.",
        },
    }
    for step in steps:
        step["follow"] = {case: lines[step["id"]] for case, lines in follow.items()}

    con.close()
    warehouse.drop_database(FLOW_DATABASE)

    recap = {
        "growth": growth,
        "load": {"limit": RATE_LIMIT_CALLS},
        "compare": {
            "full_orders": source_total,
            "full_calls": -(-source_total // PAGE_SIZE),
            "changed_orders": tuesday.rows_landed,
            "changed_calls": tuesday.api_calls - tuesday.retries,
            "first_orders": monday.rows_landed,
            "first_calls": monday.api_calls,
            "watermark": monday.watermark_after,
        },
        "retry": {"waits": [f"{w:.0f}" for w in retry_waits], "tries": 5},
        "twice": {
            "order": p4,
            "before": net[0],
            "after": net[1],
            "rows": rows2,
            "orders": orders2,
            "extra": rows2 - orders2,
            "day": _day("2026-06-05"),
            "shown": f"{friday['orders']} orders, {dollars(friday['revenue_cents'])}",
            "truth": f"{friday_true['orders']} orders, {dollars(friday_true['revenue_cents'])}",
        },
        "worse": {
            "rerun_rows": again.rows_landed,
            "thursday_rows": thursday.rows_landed,
            "thursday_orders": thursday_orders,
            "rows": rows4,
            "orders": orders4,
            "wrong_days": len(wrong4),
            "late_order": p6,
        },
    }
    return {
        "recap": recap,
        "milestone": 2,
        "of": 5,
        "title": "Load only what changed",
        "lede": "The pipeline remembers how far it read, asks the order system only for orders that changed, "
        "and lands them exactly as they arrive. Then the report goes wrong. Watch why.",
        "stages": [
            {"id": "api", "name": "Order system API", "role": "Source", "state": "given"},
            {
                "id": "extract",
                "name": "Extract",
                "role": "Ingest",
                "state": "new",
                "badge": "changed: incremental",
            },
            {
                "id": "raw",
                "name": "Raw landing table",
                "role": "Store",
                "state": "new",
                "badge": "new",
            },
            {
                "id": "orders",
                "name": "Clean orders table",
                "role": "Transform, store",
                "state": "built",
                "badge": "only a view, for now",
            },
            {
                "id": "report",
                "name": "Daily report",
                "role": "Serve",
                "state": "built",
                "badge": "a view",
            },
        ],
        "schedule": {
            "state": "by_hand",
            "text": "By hand. A person starts every run. A schedule arrives in milestone 5.",
        },
        "tests": {
            "state": "not_yet",
            "text": "Not yet. Nothing checks the tables. Table tests arrive in milestone 4.",
        },
        "cases": [
            {"id": str(p4), "label": f"Order {p4}", "hint": "refunded: lands twice"},
            {"id": str(late1), "label": f"Order {late1}", "hint": "delivered: lands twice"},
            {"id": str(p6), "label": f"Order {p6}", "hint": "late update: never read"},
        ],
        "steps": steps,
        "still_wrong": "A changed order is now in the table twice, and the report counts it twice. "
        "Run the load two times and it is worse.",
        "next": "Milestone 3: safe to run twice.",
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
    """The numbers shown on milestone_2_recap.html."""
    recap = dict((flow or build())["recap"])
    glossary = (MILESTONE_DIR.parent.parent / "GLOSSARY.md").read_text(encoding="utf-8")
    recap["numbers"] = {
        "notebooks": len(list((MILESTONE_DIR / "notebooks").glob("0*.ipynb"))),
        "tests": count_tests(),
        "tables": 3,  # raw.orders_landing, ops.watermarks, ops.load_runs
        "rows": recap["compare"]["changed_orders"],
        "words": len(
            re.findall(r"^\| \*\*.+\| Milestone 2, notebook \d \|", glossary, flags=re.MULTILINE)
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
