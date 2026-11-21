"""Build the numbers shown on milestone_3_flow.html and milestone_3_recap.html.

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

from daily_orders import warehouse
from daily_orders.config import LOOKBACK_DAYS, MILESTONE_DIR
from daily_orders.pipeline import run
from daily_orders.report import daily_report, day_totals, dollars, rebuild_days
from order_system.app import create_app
from order_system.catalog import morning_of
from order_system.truth import daily_report as true_report
from order_system.truth import day_totals as true_totals

PAGE = MILESTONE_DIR / "milestone_3_flow.html"
RECAP_PAGE = MILESTONE_DIR / "milestone_3_recap.html"
MARKER = re.compile(
    r'(<script id="page-data" type="application/json">)(.*?)(</script>)', flags=re.DOTALL
)
FLOW_DATABASE = "arden_m3_pages"

WORDS = {
    "append": (
        "Append",
        "Add rows at the end of a table. Right for raw data. Wrong for a table people count from.",
    ),
    "lookback": (
        "Lookback",
        "The window starts N days before the watermark, to catch late rows. It is safe only because the write is idempotent.",
    ),
    "merge": (
        "Upsert with MERGE",
        "Update the row if the key exists, add it if not. One order is one row, however often it arrives.",
    ),
    "partition": (
        "Partition",
        "One slice of a table. Here, all the rows of one business day. A day is replaced, never added to.",
    ),
    "idempotent": ("Idempotent", "Two runs leave the same result as one run."),
    "late": (
        "Late row",
        "A row that reaches the source after the window that should have caught it.",
    ),
    "dedupe": (
        "Dedupe key",
        "The key you keep one row per, when the same thing arrived more than once. Here: order_id.",
    ),
    "exactly_once": (
        "Exactly-once",
        "Each row changes the result one time. It is at-least-once delivery plus an idempotent write.",
    ),
    "soft_delete": (
        "Soft delete",
        "Keep the row and mark it as deleted. A hard delete removes the row for good.",
    ),
    "reprocess": (
        "Reprocessing",
        "Building a past slice again, to correct it. Here: one day of the report.",
    ),
    "rerun": (
        "Safe re-run",
        "A run you can repeat without fear. It is what an idempotent pipeline gives you.",
    ),
}


def _word(key: str) -> dict:
    return {"term": WORDS[key][0], "meaning": WORDS[key][1]}


def _day(day: str | date) -> str:
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day.strftime("%a %m-%d")


def _short(utc_text: str | None) -> str:
    return utc_text[5:16].replace("T", " ") + "Z" if utc_text else "none"


def build() -> dict:
    ticks = {"now": 0.0}

    def nap(seconds: float) -> None:
        ticks["now"] += seconds

    api = TestClient(create_app(timer=lambda: ticks["now"]))
    world = api.app.state.world
    p4, p6, p8 = world.planted["P4"], world.planted["P6"], world.planted["P8"]

    def morning(day: str) -> None:
        api.post("/admin/clock", json={"morning": day})

    def truth(day: str) -> dict:
        return true_totals(true_report(world.events, morning_of(date.fromisoformat(day))))

    def go(con, day: str, **options):
        morning(day)
        return run(api, con, sleep=nap, **options)

    def counts(con) -> tuple[int, int]:
        return con.execute(
            "SELECT (SELECT count(*) FROM raw.orders_landing), (SELECT count(*) FROM core.orders)"
        ).fetchone()

    def source_cards(order_ids: list[int], tone: str = "") -> list[dict]:
        cards = []
        for order_id in order_ids:
            answer = api.get(f"/orders/{order_id}")
            if answer.status_code == 404:
                cards.append(
                    {
                        "id": order_id,
                        "amount": "gone",
                        "sub": "deleted from the source",
                        "tone": "bad",
                    }
                )
                continue
            order = answer.json()
            cards.append(
                {
                    "id": order_id,
                    "amount": dollars(order["total_cents"] - order["refunded_cents"]),
                    "sub": f"changed {_short(order['updated_at'])} · {order['status']}",
                    "tone": tone,
                }
            )
        return cards

    def raw_cards(con, order_ids: list[int], last: int = 3) -> list[dict]:
        rows = con.execute(
            "SELECT (payload->>'order_id')::BIGINT, payload->>'status', "
            "(payload->>'total_cents')::BIGINT - (payload->>'refunded_cents')::BIGINT, run_id "
            "FROM raw.orders_landing WHERE (payload->>'order_id')::BIGINT = ANY(%s) ORDER BY 1, landing_id",
            [order_ids],
        ).fetchall()
        cards = []
        for order_id in order_ids:
            mine = [row for row in rows if row[0] == order_id]
            for _, status, net, run_id in mine[-last:]:
                cards.append(
                    {
                        "id": order_id,
                        "amount": dollars(net),
                        "sub": f"run {run_id[5:]} · {status}",
                        "tone": "changed" if len(mine) > 1 else "",
                    }
                )
        return cards

    def core_cards(con, order_ids: list[int], tone: str = "good") -> list[dict]:
        rows = con.execute(
            "SELECT order_id, order_date, status, total_cents - refunded_cents, is_deleted "
            "FROM core.orders WHERE order_id = ANY(%s) ORDER BY 1",
            [order_ids],
        ).fetchall()
        return [
            {
                "id": order_id,
                "amount": dollars(net),
                "sub": f"{_day(day)} · {'marked deleted' if deleted else status}",
                "tone": tone,
            }
            for order_id, day, status, net, deleted in rows
        ]

    def report_cards(con, days: list[str], as_of: str, was: dict | None = None) -> list[dict]:
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
                    "tone": ("changed" if was and day in was else "good") if ok else "bad",
                }
            )
        return cards

    def all_right(con, as_of: str) -> bool:
        return daily_report(con) == true_report(world.events, morning_of(date.fromisoformat(as_of)))

    idle = {"count": "", "cards": []}
    steps: list[dict] = []

    # ---- Where milestone 2 left it: the same two mornings, counted from the raw table ----
    warehouse.drop_database(FLOW_DATABASE)
    con = warehouse.connect(FLOW_DATABASE)
    go(con, "2026-06-08", lookback_days=0)
    go(con, "2026-06-09", lookback_days=0)
    old_rows, old_orders = counts(con)
    naive_orders, naive_cents = con.execute(
        "SELECT count(*), sum((payload->>'total_cents')::BIGINT - (payload->>'refunded_cents')::BIGINT)::BIGINT "
        "FROM raw.orders_landing WHERE payload->>'status' <> 'cancelled' "
        "AND ((payload->>'placed_at')::TIMESTAMPTZ AT TIME ZONE 'America/Chicago')::DATE = '2026-06-05'"
    ).fetchone()
    friday_true = truth("2026-06-09")["2026-06-05"]
    before_cards = raw_cards(con, [p4])
    steps.append(
        {
            "id": "before",
            "active": "report",
            "problem": True,
            "clock": "Tue 06-09, 07:00",
            "title": "Where milestone 2 left it",
            "text": f"The raw table holds {old_rows} rows for {old_orders} orders. A report that adds up every landed "
            f"row shows {naive_orders} orders and {dollars(naive_cents)} for {_day('2026-06-05')}. The truth is "
            f"{friday_true['orders']} orders and {dollars(friday_true['revenue_cents'])}.",
            "word": _word("append"),
            "stages": {
                "api": {"count": f"{old_orders} orders", "cards": source_cards([p4])},
                "extract": idle,
                "raw": {"count": f"{old_rows} rows · {old_orders} orders", "cards": before_cards},
                "orders": {
                    "count": "milestone 2: only a view",
                    "cards": [dict(c, tone="bad") for c in before_cards],
                },
                "report": {
                    "count": "counted twice",
                    "cards": [
                        {
                            "id": _day("2026-06-05"),
                            "amount": dollars(naive_cents),
                            "sub": f"{naive_orders} orders · truth: {friday_true['orders']}, {dollars(friday_true['revenue_cents'])}",
                            "tone": "bad",
                        }
                    ],
                },
            },
            "moves": [],
        }
    )
    con.close()

    # ---- The real pipeline of this milestone ----------------------------------
    warehouse.drop_database(FLOW_DATABASE)
    con = warehouse.connect(FLOW_DATABASE)
    monday = go(con, "2026-06-08")
    friday_monday = day_totals(daily_report(con))["2026-06-05"]["revenue_cents"]
    tuesday = go(con, "2026-06-09")
    raw2, orders2 = counts(con)
    late1 = con.execute(
        "SELECT order_id FROM core.orders WHERE first_run_id = %s AND last_run_id = %s AND order_id <> %s ORDER BY 1 LIMIT 1",
        [monday.run_id, tuesday.run_id, p4],
    ).fetchone()[0]
    shown = [p4, late1]
    left_alone = tuesday.rows_landed - tuesday.orders_inserted - tuesday.orders_updated
    tuesday_api = {"count": f"{orders2} orders", "cards": source_cards(shown, "changed")}
    steps.append(
        {
            "id": "lookback",
            "active": "extract",
            "clock": "Tue 06-09, 07:00",
            "title": "Extract looks back two days",
            "text": f"The watermark is {monday.watermark_after}. The pipeline asks for orders changed since "
            f"{tuesday.asked_since}, {LOOKBACK_DAYS} days earlier, to catch changes that arrive late. It reads "
            f"{tuesday.rows_landed} rows. Most of them it already holds.",
            "word": _word("lookback"),
            "stages": {
                "api": tuesday_api,
                "extract": {
                    "count": f"{tuesday.rows_landed} rows · since {_short(tuesday.asked_since)}",
                    "cards": source_cards(shown, "changed"),
                },
                "raw": {"count": f"{monday.rows_landed} rows", "cards": []},
                "orders": {"count": f"{monday.rows_landed} rows", "cards": []},
                "report": idle,
            },
            "moves": [{"from": "api", "to": "extract"}],
        }
    )
    steps.append(
        {
            "id": "land",
            "active": "raw",
            "clock": "Tue 06-09, 07:00",
            "title": "Everything still lands raw",
            "text": f"All {tuesday.rows_landed} rows are appended, as in milestone 2. The raw table now holds "
            f"{raw2} rows for {orders2} orders. It keeps every version and every repeat. Nobody counts from it.",
            "word": _word("append"),
            "stages": {
                "api": tuesday_api,
                "extract": {
                    "count": f"{tuesday.rows_landed} rows",
                    "cards": source_cards(shown, "changed"),
                },
                "raw": {"count": f"{raw2} rows · {orders2} orders", "cards": raw_cards(con, shown)},
                "orders": {"count": f"{monday.rows_landed} rows", "cards": []},
                "report": idle,
            },
            "moves": [{"from": "extract", "to": "raw"}],
        }
    )
    steps.append(
        {
            "id": "merge",
            "active": "orders",
            "fix": True,
            "clock": "Tue 06-09, 07:00",
            "title": "The fix: MERGE by order_id",
            "text": f"Each row is matched to the clean table by its key. {tuesday.orders_inserted} orders are new and "
            f"are inserted. {tuesday.orders_updated} are known and newer, and are updated. {left_alone} are the "
            f"version the table already holds, and are left alone. {orders2} orders, {orders2} rows.",
            "word": _word("merge"),
            "stages": {
                "api": tuesday_api,
                "extract": {"count": f"{tuesday.rows_landed} rows", "cards": []},
                "raw": {"count": f"{raw2} rows · {orders2} orders", "cards": raw_cards(con, shown)},
                "orders": {
                    "count": f"{orders2} rows = {orders2} orders",
                    "cards": [
                        *core_cards(con, shown),
                        {
                            "id": "inserted",
                            "amount": str(tuesday.orders_inserted),
                            "sub": "new orders",
                            "tone": "",
                        },
                        {
                            "id": "updated",
                            "amount": str(tuesday.orders_updated),
                            "sub": "a newer version",
                            "tone": "",
                        },
                        {
                            "id": "left alone",
                            "amount": str(left_alone),
                            "sub": "already held",
                            "tone": "",
                        },
                    ],
                },
                "report": idle,
            },
            "moves": [{"from": "raw", "to": "orders"}],
        }
    )
    change = tuesday.answer["earlier_changes"][0]
    steps.append(
        {
            "id": "report",
            "active": "report",
            "fix": True,
            "clock": "Tue 06-09, 07:00",
            "title": "The report is rebuilt, one day at a time",
            "text": f"The run touched orders of {len(tuesday.days_rebuilt)} business days. Only those days are deleted "
            f"and built again from the clean table. {_day(change['order_date'])} moves by "
            f"{dollars(change['revenue_cents_change'])}, and the pipeline writes down why: order "
            f"{change['reasons'][0]['order_id']}, {change['reasons'][0]['what']}.",
            "word": _word("partition"),
            "stages": {
                "api": tuesday_api,
                "extract": {"count": f"{tuesday.rows_landed} rows", "cards": []},
                "raw": {"count": f"{raw2} rows · {orders2} orders", "cards": raw_cards(con, shown)},
                "orders": {
                    "count": f"{orders2} rows = {orders2} orders",
                    "cards": core_cards(con, shown),
                },
                "report": {
                    "count": f"{len(tuesday.days_rebuilt)} days rebuilt",
                    "cards": report_cards(
                        con, tuesday.days_rebuilt, "2026-06-09", {"2026-06-05": friday_monday}
                    ),
                },
            },
            "moves": [{"from": "orders", "to": "report"}],
        }
    )

    snapshot = (con.execute("SELECT * FROM core.orders ORDER BY 1").fetchall(), daily_report(con))
    again = go(con, "2026-06-09")
    same = snapshot == (
        con.execute("SELECT * FROM core.orders ORDER BY 1").fetchall(),
        daily_report(con),
    )
    raw3, orders3 = counts(con)
    steps.append(
        {
            "id": "again",
            "active": "orders",
            "rerun": True,
            "fix": True,
            "clock": "Tue 06-09, 07:05",
            "title": "Run it again: nothing changes",
            "text": f"The same morning, a second time. It reads {again.rows_landed} rows and lands them raw. The merge "
            f"inserts {again.orders_inserted} and updates {again.orders_updated}: every row is a version the table "
            f"already holds. The clean table and the report are "
            + ("identical to before." if same else "DIFFERENT."),
            "word": _word("idempotent"),
            "stages": {
                "api": tuesday_api,
                "extract": {
                    "count": f"{again.rows_landed} rows, again",
                    "cards": source_cards(shown),
                },
                "raw": {"count": f"{raw3} rows · {orders3} orders", "cards": raw_cards(con, shown)},
                "orders": {"count": f"{orders3} rows · unchanged", "cards": core_cards(con, shown)},
                "report": {
                    "count": "unchanged",
                    "cards": report_cards(
                        con, ["2026-06-08", "2026-06-07", "2026-06-05"], "2026-06-09"
                    ),
                },
            },
            "moves": [
                {"from": "api", "to": "extract"},
                {"from": "extract", "to": "raw"},
                {"from": "raw", "to": "orders"},
            ],
        }
    )

    monday_before = day_totals(daily_report(con))["2026-06-08"]["revenue_cents"]
    wednesday = go(con, "2026-06-10")
    late_change = next(
        c for c in wednesday.answer["earlier_changes"] if c["reasons"][0]["order_id"] == p6
    )
    late_source = api.get(f"/orders/{p6}").json()
    steps.append(
        {
            "id": "late",
            "active": "extract",
            "fix": True,
            "clock": "Wed 06-10, 07:00",
            "title": "A late change is caught",
            "text": f"Order {p6} was refused at the door on Monday at 21:35. The driver's phone was offline, so the API "
            f"showed it on Tuesday, after that morning's run. Its updated_at, {_short(late_source['updated_at'])}, "
            f"is before the watermark. The lookback still reaches it: {_day(late_change['order_date'])} loses "
            f"1 order and {dollars(-late_change['revenue_cents_change'])}.",
            "word": _word("late"),
            "stages": {
                "api": {"count": "the source", "cards": source_cards([p6], "changed")},
                "extract": {
                    "count": f"since {_short(wednesday.asked_since)}",
                    "cards": [
                        {
                            "id": "watermark",
                            "amount": _short(tuesday.watermark_after),
                            "sub": "the change is before it",
                            "tone": "bad",
                        },
                        {
                            "id": "lookback",
                            "amount": _short(wednesday.asked_since),
                            "sub": "the change is after it",
                            "tone": "good",
                        },
                    ],
                },
                "raw": {"count": f"order {p6}, every version", "cards": raw_cards(con, [p6])},
                "orders": {"count": "one row, cancelled", "cards": core_cards(con, [p6])},
                "report": {
                    "count": "corrected",
                    "cards": report_cards(
                        con, ["2026-06-08"], "2026-06-10", {"2026-06-08": monday_before}
                    ),
                },
            },
            "moves": [{"from": "api", "to": "extract"}, {"from": "raw", "to": "orders"}],
        }
    )

    thursday = go(con, "2026-06-11")
    landed, staged = con.execute(
        "SELECT count(*), count(DISTINCT payload->>'order_id') FROM raw.orders_landing WHERE run_id = %s",
        [thursday.run_id],
    ).fetchone()
    raw4, orders4 = counts(con)
    steps.append(
        {
            "id": "repeated_page",
            "active": "orders",
            "fix": True,
            "clock": "Thu 06-11, 07:00",
            "title": "The API serves one page twice",
            "text": f"{landed} rows land for {staged} orders. A merge refuses the same key twice, so the pipeline "
            f"first keeps one row per order: the newest. Then it merges. The clean table holds {orders4} rows "
            f"for {orders4} orders.",
            "word": _word("dedupe"),
            "stages": {
                "api": {"count": "one page, twice", "cards": []},
                "extract": {"count": f"{landed} rows · {thursday.api_calls} calls", "cards": []},
                "raw": {"count": f"{landed} rows this run", "cards": []},
                "orders": {
                    "count": f"{orders4} rows = {orders4} orders",
                    "cards": [
                        {
                            "id": "landed",
                            "amount": str(landed),
                            "sub": "rows in this run",
                            "tone": "changed",
                        },
                        {
                            "id": "staged",
                            "amount": str(staged),
                            "sub": "one row per order_id",
                            "tone": "good",
                        },
                        {
                            "id": "inserted",
                            "amount": str(thursday.orders_inserted),
                            "sub": "new orders",
                            "tone": "",
                        },
                        {
                            "id": "updated",
                            "amount": str(thursday.orders_updated),
                            "sub": "a newer version",
                            "tone": "",
                        },
                    ],
                },
                "report": {
                    "count": "right",
                    "cards": report_cards(con, ["2026-06-10", "2026-06-09"], "2026-06-11"),
                },
            },
            "moves": [{"from": "extract", "to": "raw"}, {"from": "raw", "to": "orders"}],
        }
    )
    ok4 = all_right(con, "2026-06-11")
    steps.append(
        {
            "id": "exactly_once",
            "active": "report",
            "fix": True,
            "clock": "Thu 06-11, 07:00",
            "title": "Rows arrive more than once. Each one counts once",
            "text": f"After five runs the raw table holds {raw4} rows for {orders4} orders. Rows were re-read by the "
            "lookback, by a second run and by a repeated page. The report "
            + ("equals the truth on every day." if ok4 else "is WRONG.")
            + " The delivery is at-least-once. The write is idempotent. The result is exactly-once.",
            "word": _word("exactly_once"),
            "stages": {
                "api": {"count": f"{orders4} orders", "cards": []},
                "extract": {"count": "at-least-once", "cards": []},
                "raw": {"count": f"{raw4} rows · {orders4} orders", "cards": raw_cards(con, [p4])},
                "orders": {
                    "count": f"{orders4} rows = {orders4} orders",
                    "cards": core_cards(con, [p4, p6]),
                },
                "report": {
                    "count": "every day right" if ok4 else "WRONG",
                    "cards": report_cards(
                        con, ["2026-06-10", "2026-06-08", "2026-06-05"], "2026-06-11"
                    ),
                },
            },
            "moves": [],
        }
    )

    go(con, "2026-06-12")
    go(con, "2026-06-13")
    friday12_before = day_totals(daily_report(con))["2026-06-12"]
    sunday = go(con, "2026-06-14")
    deleted = next(c for c in sunday.answer["earlier_changes"] if c["reasons"][0]["order_id"] == p8)
    steps.append(
        {
            "id": "deleted",
            "active": "orders",
            "fix": True,
            "clock": "Sun 06-14, 07:00",
            "title": "An order vanishes from the source",
            "text": f"Test order {p8} was deleted on Saturday. A deleted row has no updated_at, so the query can never "
            f"return it. The pipeline reads the source's change feed, finds the delete, and marks the row. "
            f"{_day(deleted['order_date'])} loses 1 order and {dollars(-deleted['revenue_cents_change'])}. The row is kept.",
            "word": _word("soft_delete"),
            "stages": {
                "api": {"count": "the source", "cards": source_cards([p8])},
                "extract": {
                    "count": "change feed",
                    "cards": [
                        {
                            "id": p8,
                            "amount": "op d",
                            "sub": "a delete, from the feed",
                            "tone": "changed",
                        }
                    ],
                },
                "raw": {"count": f"order {p8}, as landed", "cards": raw_cards(con, [p8], last=1)},
                "orders": {"count": "kept, and marked", "cards": core_cards(con, [p8], "changed")},
                "report": {
                    "count": "not counted",
                    "cards": report_cards(
                        con,
                        ["2026-06-12"],
                        "2026-06-14",
                        {"2026-06-12": friday12_before["revenue_cents"]},
                    ),
                },
            },
            "moves": [{"from": "extract", "to": "orders"}, {"from": "orders", "to": "report"}],
        }
    )

    right_report = daily_report(con)
    con.execute(
        "UPDATE analytics.daily_orders SET revenue_cents = 1 WHERE order_date = '2026-06-05' AND city = 'NGT'"
    )
    broken = day_totals(daily_report(con))["2026-06-05"]["revenue_cents"]
    rebuild_days(con, [date(2026, 6, 5)])
    repaired = daily_report(con) == right_report
    steps.append(
        {
            "id": "rebuild",
            "active": "report",
            "fix": True,
            "clock": "Sun 06-14, 09:00",
            "title": "Someone breaks Friday. Friday is rebuilt, alone",
            "text": f"A number is edited by hand, and {_day('2026-06-05')} shows {dollars(broken)}. One command deletes "
            "that day's rows and builds them again from the clean table. "
            + (
                "Friday is right again, and no other day was touched."
                if repaired
                else "It is still WRONG."
            ),
            "word": _word("reprocess"),
            "stages": {
                "api": idle,
                "extract": idle,
                "raw": idle,
                "orders": {"count": "the source of the rebuild", "cards": core_cards(con, [p4])},
                "report": {
                    "count": "1 day rebuilt",
                    "cards": report_cards(
                        con,
                        ["2026-06-05", "2026-06-04", "2026-06-06"],
                        "2026-06-14",
                        {"2026-06-05": broken},
                    ),
                },
            },
            "moves": [{"from": "orders", "to": "report"}],
        }
    )
    raw_end, orders_end = counts(con)
    days_right = all_right(con, "2026-06-14")
    steps.append(
        {
            "id": "still_wrong",
            "active": "orders",
            "problem": True,
            "clock": "Sun 06-14, 09:00",
            "title": "Still wrong: nothing checks the tables",
            "text": "The numbers are right on every morning. But every rule is SQL inside a Python string, in one "
            "script. And the tests check the code on test data. Nothing looks at the tables after this morning's "
            "real run. If the source sent a negative total tomorrow, the report would add it up. "
            "Milestone 4 moves the SQL into models and puts tests on the tables.",
            "word": _word("rerun"),
            "stages": {
                "api": {"count": f"{orders_end - 1} orders", "cards": []},
                "extract": {"count": "safe from loss", "cards": []},
                "raw": {"count": f"{raw_end} rows", "cards": []},
                "orders": {
                    "count": f"{orders_end} rows · nobody checks them",
                    "cards": core_cards(con, [p4, p6, p8], ""),
                },
                "report": {
                    "count": "right today" if days_right else "WRONG",
                    "cards": report_cards(con, ["2026-06-13", "2026-06-12"], "2026-06-14"),
                },
            },
            "moves": [],
        }
    )

    follow = {
        str(p4): {
            "before": "Landed twice: before and after its refund. The report adds both.",
            "lookback": "It was refunded on Monday, so it is inside the window.",
            "land": "Its refunded version lands beside the old one. The raw table keeps both.",
            "merge": "It is in the clean table already, and this version is newer. Its row is updated. One row.",
            "report": f"Its refund lowers {_day('2026-06-05')} by {dollars(-change['revenue_cents_change'])}. The pipeline names it as the reason.",
            "again": "It is read again. It is the version the table holds, so nothing happens.",
            "late": "Not touched by this run's changes.",
            "repeated_page": "Still one row.",
            "exactly_once": "It arrived several times and changed the report once.",
            "deleted": "Still one row.",
            "rebuild": "The rebuild of Friday reads its one row and counts it once.",
            "still_wrong": "One order, one row. Nobody checks that it stays so.",
        },
        str(p6): {
            "before": "Not placed yet.",
            "lookback": "Placed on Monday evening. It is read as 'out for delivery'.",
            "land": "It lands once, as 'out for delivery'.",
            "merge": "It is new, so it is inserted.",
            "report": "It is counted as a live order of Monday. That is what the source says so far.",
            "again": "Read again, left alone.",
            "late": f"Its late cancellation is inside the lookback. The row is updated to 'cancelled', and {_day('2026-06-08')} is corrected.",
            "repeated_page": "One row, cancelled.",
            "exactly_once": "A late change, read once it arrived, counted once.",
            "deleted": "One row, cancelled.",
            "rebuild": "Not part of Friday.",
            "still_wrong": "One row, cancelled, and right.",
        },
        str(p8): {
            "before": "Not placed yet.",
            "lookback": "Not placed yet.",
            "land": "Not placed yet.",
            "merge": "Not placed yet.",
            "report": "Not placed yet.",
            "again": "Not placed yet.",
            "late": "Not placed yet.",
            "repeated_page": "Not placed yet. It is ordered on Friday 12 June.",
            "exactly_once": "Not placed yet.",
            "deleted": "Loaded on Saturday morning, deleted from the source that day. Now marked, kept, and not counted.",
            "rebuild": "Not part of Friday 5 June.",
            "still_wrong": "Its row stays in the warehouse with is_deleted = true.",
        },
    }
    for step in steps:
        step["follow"] = {case: lines[step["id"]] for case, lines in follow.items()}

    con.close()
    warehouse.drop_database(FLOW_DATABASE)

    recap = {
        "before": {
            "rows": old_rows,
            "orders": old_orders,
            "shown": f"{naive_orders} orders, {dollars(naive_cents)}",
            "truth": f"{friday_true['orders']} orders, {dollars(friday_true['revenue_cents'])}",
            "day": _day("2026-06-05"),
        },
        "merge": {
            "read": tuesday.rows_landed,
            "inserted": tuesday.orders_inserted,
            "updated": tuesday.orders_updated,
            "left_alone": left_alone,
            "orders": orders2,
            "raw": raw2,
            "since": tuesday.asked_since,
            "days_rebuilt": len(tuesday.days_rebuilt),
        },
        "again": {
            "read": again.rows_landed,
            "inserted": again.orders_inserted,
            "updated": again.orders_updated,
            "same": "identical" if same else "DIFFERENT",
            "raw": raw3,
            "orders": orders3,
        },
        "late": {
            "order": p6,
            "by": dollars(-late_change["revenue_cents_change"]),
            "day": _day(late_change["order_date"]),
            "lookback": LOOKBACK_DAYS,
        },
        "page": {"landed": landed, "staged": staged},
        "deleted": {
            "order": p8,
            "by": dollars(-deleted["revenue_cents_change"]),
            "day": _day(deleted["order_date"]),
        },
        "end": {
            "raw": raw_end,
            "orders": orders_end,
            "right": "every day" if days_right else "NOT every day",
        },
        "refund": {
            "order": p4,
            "by": dollars(-change["revenue_cents_change"]),
            "day": _day(change["order_date"]),
        },
    }
    return {
        "recap": recap,
        "milestone": 3,
        "of": 5,
        "title": "Safe to run twice",
        "lede": "Everything still lands raw. Then a MERGE by the order's key keeps one row per order, and the report "
        "is rebuilt one day at a time. Run it again, and nothing changes.",
        "stages": [
            {"id": "api", "name": "Order system API", "role": "Source", "state": "given"},
            {
                "id": "extract",
                "name": "Extract",
                "role": "Ingest",
                "state": "new",
                "badge": "changed: looks back",
            },
            {
                "id": "raw",
                "name": "Raw landing table",
                "role": "Store",
                "state": "built",
                "badge": "append, as before",
            },
            {
                "id": "orders",
                "name": "Clean orders table",
                "role": "Transform, store",
                "state": "new",
                "badge": "new: one row per order",
            },
            {
                "id": "report",
                "name": "Daily report",
                "role": "Serve",
                "state": "new",
                "badge": "new: a table, per day",
            },
        ],
        "schedule": {
            "state": "by_hand",
            "text": "By hand. A person starts every run. A schedule arrives in milestone 5.",
        },
        "tests": {
            "state": "not_yet",
            "text": "Not yet. Tests check the code, but nothing checks the tables. Table tests arrive in milestone 4.",
        },
        "cases": [
            {"id": str(p4), "label": f"Order {p4}", "hint": "refunded: now one row"},
            {"id": str(p6), "label": f"Order {p6}", "hint": "late update: now caught"},
            {"id": str(p8), "label": f"Order {p8}", "hint": "deleted: now marked"},
        ],
        "steps": steps,
        "still_wrong": "All the logic is in one script. Nothing checks the tables. Nobody knows when the number is wrong.",
        "next": "Milestone 4: models and tests.",
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
    """The numbers shown on milestone_3_recap.html."""
    recap = dict((flow or build())["recap"])
    glossary = (MILESTONE_DIR.parent.parent / "GLOSSARY.md").read_text(encoding="utf-8")
    recap["numbers"] = {
        "notebooks": len(list((MILESTONE_DIR / "notebooks").glob("0*.ipynb"))),
        "tests": count_tests(),
        "tables": 6,  # raw.orders_landing, core.orders, analytics.daily_orders, and three in ops
        "rows": recap["merge"]["orders"],
        "words": len(
            re.findall(r"^\| \*\*.+\| Milestone 3, notebook \d \|", glossary, flags=re.MULTILINE)
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
