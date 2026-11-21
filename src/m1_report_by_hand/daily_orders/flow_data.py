"""Build the numbers shown on milestone_1_flow.html and milestone_1_recap.html.

The page must never show a number typed by hand. This script runs the real
pipeline against a private order system, and writes what happened into the page.

    uv run python -m daily_orders.flow_data          write the page's data
    uv run python -m daily_orders.flow_data --check  fail if the page is out of date
"""

import json
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi.testclient import TestClient

from daily_orders.config import BUSINESS_TZ, MILESTONE_DIR
from daily_orders.extract import RateLimited, fetch_all_orders
from daily_orders.pipeline import run
from daily_orders.report import dollars
from order_system.app import PAGE_SIZE, RATE_LIMIT_CALLS, create_app

PAGE = MILESTONE_DIR / "milestone_1_flow.html"
RECAP_PAGE = MILESTONE_DIR / "milestone_1_recap.html"
MARKER = re.compile(
    r'(<script id="page-data" type="application/json">)(.*?)(</script>)', flags=re.DOTALL
)
ARDEN = ZoneInfo(BUSINESS_TZ)


def _local(utc_text: str) -> datetime:
    return datetime.fromisoformat(utc_text.replace("Z", "+00:00")).astimezone(ARDEN)


def _day(day: str | date) -> str:
    """2026-06-06 -> Sat 06-06"""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day.strftime("%a %m-%d")


def _signed(cents: int) -> str:
    return ("+" if cents >= 0 else "") + dollars(cents)


def build() -> dict:
    api = TestClient(create_app())
    planted = api.app.state.world.planted
    warehouse = Path(tempfile.mkdtemp()) / "flow.duckdb"

    def morning(day: str) -> None:
        api.post("/admin/clock", json={"morning": day})

    # ---- Run the real pipeline ------------------------------------------------
    morning("2026-06-08")
    orders = fetch_all_orders(api).orders
    by_id = {o["order_id"]: o for o in orders}
    morning("2026-06-08")
    monday = run(api, warehouse)
    morning("2026-06-08")
    monday_again = run(api, warehouse)
    morning("2026-06-09")
    tuesday_orders = {o["order_id"]: o for o in fetch_all_orders(api).orders}
    morning("2026-06-09")
    tuesday = run(api, warehouse)

    measured = []
    for day in ("2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"):
        morning(day)
        try:
            extract = fetch_all_orders(api)
            measured.append(
                {
                    "day": _day(day),
                    "orders": len(extract.orders),
                    "calls": extract.api_calls,
                    "ok": True,
                }
            )
        except RateLimited:
            measured.append(
                {"day": _day(day), "orders": None, "calls": RATE_LIMIT_CALLS + 1, "ok": False}
            )

    # ---- Numbers worked out from those runs -----------------------------------
    y, b = monday.answer["yesterday"], monday.answer["day_before"]
    sat, sun = b["order_date"], y["order_date"]
    frame = pd.DataFrame(orders)
    utc_day = pd.to_datetime(frame["placed_at"], utc=True).dt.date.astype(str)
    wrong = {
        day: (int((utc_day == day).sum()), int(frame.loc[utc_day == day, "total_cents"].sum()))
        for day in (sat, sun)
    }
    item_lines = sum(len(o["items"]) for o in orders)
    monday_run = "2026-06-08T12:00:00Z"
    changed_by_tuesday = sum(o["updated_at"] >= monday_run for o in tuesday_orders.values())
    change = tuesday.answer["earlier_changes"][0]
    friday = change["order_date"]

    p5, p4, p2 = planted["P5"], planted["P4"], planted["P2"]
    refund = tuesday_orders[p4]
    refund_wait = (_local("2026-06-09T12:00:00Z") - _local(refund["refunded_at"])) // timedelta(
        hours=1
    )
    # Friday's total, as each morning's report showed it.
    friday_tuesday = sum(
        o["total_cents"] - o["refunded_cents"]
        for o in tuesday_orders.values()
        if _local(o["placed_at"]).date().isoformat() == friday and o["status"] != "cancelled"
    )
    friday_monday = friday_tuesday - change["revenue_cents_change"]

    # Six real orders are shown as cards in every stage: three planted, three ordinary.
    ordinary = sorted(
        o["order_id"]
        for o in orders
        if o["city"] == "LKS" and _local(o["placed_at"]).date().isoformat() == sun
    )[:3]
    shown = [p2, p4, p5, *ordinary]

    def source_card(order: dict, tone: str = "") -> dict:
        placed = order["placed_at"][5:16].replace("T", " ") + "Z"
        return {
            "id": order["order_id"],
            "amount": dollars(order["total_cents"] - order["refunded_cents"]),
            "sub": f"placed {placed} · {order['status']}",
            "tone": tone,
        }

    def table_card(order: dict, use_utc: bool = False, tone: str = "") -> dict:
        if use_utc:
            day = date.fromisoformat(order["placed_at"][:10])
        else:
            day = _local(order["placed_at"]).date()
        return {
            "id": order["order_id"],
            "amount": dollars(order["total_cents"] - order["refunded_cents"]),
            "sub": f"{_day(day)} · {order['city']} · {order['status']}",
            "tone": tone,
        }

    def source_cards(state: dict, tones: dict | None = None) -> list[dict]:
        return [source_card(state[i], (tones or {}).get(i, "")) for i in shown]

    def table_cards(state: dict, use_utc: bool = False, tones: dict | None = None) -> list[dict]:
        return [table_card(state[i], use_utc, (tones or {}).get(i, "")) for i in shown]

    def report_cards(answer: dict, tones: dict | None = None) -> list[dict]:
        cards = []
        for part in ("yesterday", "day_before"):
            row = answer[part]
            cards.append(
                {
                    "id": _day(row["order_date"]),
                    "amount": dollars(row["revenue_cents"]),
                    "sub": f"{row['orders']} orders",
                    "tone": (tones or {}).get(row["order_date"], ""),
                }
            )
        return cards

    n, calls = monday.orders_loaded, monday.api_calls
    n2, calls2 = tuesday.orders_loaded, tuesday.api_calls
    last_ok = [m for m in measured if m["ok"]][-1]
    refused = [m for m in measured if not m["ok"]][0]

    words = {
        "source": ("Source", "Where the data is made. Here it is Arden's order system."),
        "destination": (
            "Destination",
            "Where the data is used. Here it is the warehouse, and the manager's report.",
        ),
        "pipeline": (
            "Data pipeline",
            "It moves data from where it is made to where it is used. It works in steps, and it runs on a schedule.",
        ),
        "full_load": ("Full load", "It reads every row on every run."),
        "event_time": (
            "Event time",
            "When the thing happened in the real world. Here: placed_at, when the customer ordered. Business numbers use it.",
        ),
        "etl": ("ETL", "Extract, transform, load. Transform first, then load only the result."),
        "grain": ("Grain", "What one row of a table means. Here: one row is one order."),
        "batch": (
            "Batch",
            "One run handles a whole slice of data, then stops. Here: one run a morning.",
        ),
        "latency": (
            "Latency",
            "The time from a change in the source until the warehouse can show it.",
        ),
        "load_time": (
            "Load time",
            "When the pipeline copied the row. It is not when the customer ordered.",
        ),
    }

    def word(key: str) -> dict:
        return {"term": words[key][0], "meaning": words[key][1]}

    idle = {"count": "", "cards": []}
    api_monday = {"count": f"{n} orders", "cards": source_cards(by_id)}
    api_tuesday = {"count": f"{n2} orders", "cards": source_cards(tuesday_orders, {p4: "changed"})}
    extract_monday = {"count": f"{n} orders · {calls} calls", "cards": source_cards(by_id)}
    orders_monday = {"count": f"{n} rows", "cards": table_cards(by_id)}
    report_monday = {
        "count": f"morning of {_day(monday.run_date)}",
        "cards": report_cards(monday.answer),
    }

    steps = [
        {
            "id": "question",
            "active": "report",
            "clock": "Mon 06-08, 07:00",
            "title": "The manager asks a question",
            "text": "How many orders did we take yesterday, how much money did they bring in, how does that "
            "compare with the day before, and has any earlier day changed? Today a person answers by hand.",
            "word": word("pipeline"),
            "stages": {"api": idle, "extract": idle, "orders": idle, "report": idle},
            "moves": [],
        },
        {
            "id": "source",
            "active": "api",
            "clock": "Mon 06-08, 07:00",
            "title": "The orders live in the order system",
            "text": f"The order system holds {n} orders this morning. Nobody may run reports on it, "
            "because a heavy report slows the app. Its times are in UTC, shown with a Z.",
            "word": word("source"),
            "stages": {"api": api_monday, "extract": idle, "orders": idle, "report": idle},
            "moves": [],
        },
        {
            "id": "page",
            "active": "extract",
            "clock": "Mon 06-08, 07:00",
            "title": "Extract asks for the first page",
            "text": f"The API gives at most {PAGE_SIZE} orders per call, and a bookmark called next_cursor. "
            "Extract hands the bookmark back to get the next page.",
            "word": word("full_load"),
            "stages": {
                "api": api_monday,
                "extract": {"count": f"{PAGE_SIZE} orders · 1 call", "cards": []},
                "orders": idle,
                "report": idle,
            },
            "moves": [{"from": "api", "to": "extract"}],
        },
        {
            "id": "all_pages",
            "active": "extract",
            "clock": "Mon 06-08, 07:00",
            "title": "Extract follows the pages to the end",
            "text": f"{calls} calls later, extract has all {n} orders. This is a full load: every order "
            "Arden ever took, read again every morning.",
            "word": word("full_load"),
            "stages": {
                "api": api_monday,
                "extract": extract_monday,
                "orders": idle,
                "report": idle,
            },
            "moves": [{"from": "api", "to": "extract"}],
        },
        {
            "id": "wrong_day",
            "active": "orders",
            "problem": True,
            "clock": "Mon 06-08, 07:00",
            "title": "The trap: which day is it?",
            "text": f"The easy idea is to take the date from placed_at. But that date is UTC. {_day(sat)} "
            f"would get {wrong[sat][0]} orders and {dollars(wrong[sat][1])}. No error shows. "
            "Every order placed after 19:00 in Arden lands on the next day.",
            "word": word("event_time"),
            "stages": {
                "api": api_monday,
                "extract": extract_monday,
                "orders": {
                    "count": "UTC date: wrong",
                    "cards": table_cards(by_id, use_utc=True, tones={p5: "bad"}),
                },
                "report": {
                    "count": "wrong",
                    "cards": [
                        {
                            "id": _day(sun),
                            "amount": dollars(wrong[sun][1]),
                            "sub": f"{wrong[sun][0]} orders",
                            "tone": "bad",
                        },
                        {
                            "id": _day(sat),
                            "amount": dollars(wrong[sat][1]),
                            "sub": f"{wrong[sat][0]} orders",
                            "tone": "bad",
                        },
                    ],
                },
            },
            "moves": [],
        },
        {
            "id": "right_day",
            "active": "orders",
            "fix": True,
            "clock": "Mon 06-08, 07:00",
            "title": "The fix: Arden's clock decides the day",
            "text": "Transform moves placed_at to Arden's time zone, then takes the date. "
            f"{_day(sat)} now has {b['orders']} orders and {dollars(b['revenue_cents'])}. "
            "That equals the answer worked out by hand.",
            "word": word("event_time"),
            "stages": {
                "api": api_monday,
                "extract": extract_monday,
                "orders": {
                    "count": "Arden's clock: right",
                    "cards": table_cards(by_id, tones={p5: "good"}),
                },
                "report": idle,
            },
            "moves": [],
        },
        {
            "id": "load",
            "active": "orders",
            "clock": "Mon 06-08, 07:00",
            "title": "Transform, then load",
            "text": f"pandas keeps the columns the report needs. The {item_lines} item lines the API sent are "
            f"dropped. Then {n} rows replace the orders table in one step. No raw copy is kept.",
            "word": word("etl"),
            "stages": {
                "api": api_monday,
                "extract": extract_monday,
                "orders": orders_monday,
                "report": idle,
            },
            "moves": [{"from": "extract", "to": "orders"}],
        },
        {
            "id": "report",
            "active": "report",
            "clock": "Mon 06-08, 07:00",
            "title": "One SQL query answers the manager",
            "text": f"Yesterday, {_day(sun)}: {y['orders']} orders, {dollars(y['revenue_cents'])}. "
            f"The day before: {b['orders']} orders, {dollars(b['revenue_cents'])}. "
            f"That is {monday.answer['change']['orders']:+d} orders and "
            f"{_signed(monday.answer['change']['revenue_cents'])}.",
            "word": word("grain"),
            "stages": {
                "api": api_monday,
                "extract": extract_monday,
                "orders": orders_monday,
                "report": report_monday,
            },
            "moves": [{"from": "orders", "to": "report"}],
        },
        {
            "id": "again",
            "active": "orders",
            "rerun": True,
            "clock": "Mon 06-08, 07:05",
            "title": "Run Monday again: nothing doubles",
            "text": f"Someone runs it a second time. The load replaces the whole table, so it still holds "
            f"{monday_again.orders_loaded} rows, and the report is the same. It cost {monday_again.api_calls} "
            "more API calls.",
            "word": word("full_load"),
            "stages": {
                "api": api_monday,
                "extract": extract_monday,
                "orders": {
                    "count": f"{monday_again.orders_loaded} rows, replaced",
                    "cards": table_cards(by_id),
                },
                "report": {
                    "count": "the same" if monday_again.answer == monday.answer else "DIFFERENT",
                    "cards": report_cards(monday_again.answer),
                },
            },
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "orders"}],
        },
        {
            "id": "next_morning",
            "active": "extract",
            "clock": "Tue 06-09, 07:00",
            "title": "The next morning: read everything again",
            "text": f"A day passes. The source now holds {n2} orders. Only {changed_by_tuesday} are new or "
            f"changed, but the full load reads all {n2}, in {calls2} calls.",
            "word": word("batch"),
            "stages": {
                "api": api_tuesday,
                "extract": {
                    "count": f"{n2} orders · {calls2} calls",
                    "cards": source_cards(tuesday_orders, {p4: "changed"}),
                },
                "orders": {
                    "count": f"{n2} rows",
                    "cards": table_cards(tuesday_orders, tones={p4: "changed"}),
                },
                "report": idle,
            },
            "moves": [{"from": "api", "to": "extract"}, {"from": "extract", "to": "orders"}],
        },
        {
            "id": "changed",
            "active": "report",
            "clock": "Tue 06-09, 07:00",
            "title": "An earlier day has changed",
            "text": f"{_day(friday)} was {dollars(friday_monday)} on Monday. Today it is {dollars(friday_tuesday)}. "
            f"Order {p4} was refunded {dollars(refund['refunded_cents'])} on Monday, three days after "
            f"delivery. The warehouse showed it {refund_wait} hours later.",
            "word": word("latency"),
            "stages": {
                "api": api_tuesday,
                "extract": {
                    "count": f"{n2} orders · {calls2} calls",
                    "cards": source_cards(tuesday_orders, {p4: "changed"}),
                },
                "orders": {
                    "count": f"{n2} rows",
                    "cards": table_cards(tuesday_orders, tones={p4: "changed"}),
                },
                "report": {
                    "count": f"morning of {_day(tuesday.run_date)}",
                    "cards": [
                        *report_cards(tuesday.answer),
                        {
                            "id": _day(friday),
                            "amount": dollars(friday_tuesday),
                            "sub": f"was {dollars(friday_monday)} · {_signed(change['revenue_cents_change'])}",
                            "tone": "changed",
                        },
                    ],
                },
            },
            "moves": [{"from": "orders", "to": "report"}],
        },
        {
            "id": "still_wrong",
            "active": "extract",
            "problem": True,
            "clock": "Thu 06-11, 07:00",
            "title": "Still wrong: the load outgrows the API",
            "text": f"The API allows {RATE_LIMIT_CALLS} calls in 10 seconds. On {last_ok['day']} the load needs "
            f"{last_ok['calls']}. On {refused['day']} it needs {refused['calls']}. Call {refused['calls']} is "
            "refused, nothing is loaded, and there is no report. Milestone 2 loads only what changed.",
            "word": word("full_load"),
            "stages": {
                "api": {"count": "429: too many requests", "cards": []},
                "extract": {
                    "count": f"call {refused['calls']} refused",
                    "cards": [
                        {
                            "id": m["day"],
                            "amount": f"{m['calls']} calls",
                            "sub": f"{m['orders']} orders read"
                            if m["ok"]
                            else "refused: nothing loaded",
                            "tone": "" if m["ok"] else "bad",
                        }
                        for m in measured
                    ],
                },
                "orders": {"count": "not loaded", "cards": []},
                "report": {"count": "no report", "cards": []},
            },
            "moves": [],
        },
    ]

    # ---- Follow one order through every step ----------------------------------
    o5, o4, o2 = by_id[p5], tuesday_orders[p4], by_id[p2]
    t5 = _local(o5["placed_at"])
    follow = {
        str(p5): {
            "question": "This order will test the rule for 'which day'.",
            "source": f"The API says placed_at = {o5['placed_at']}. Read as text, that is Sunday.",
            "page": "It is on a later page. Extract has not reached it yet.",
            "all_pages": "Extract has it now, exactly as the API sent it.",
            "wrong_day": f"By its UTC date it is counted on {_day(o5['placed_at'][:10])}. Wrong.",
            "right_day": f"On Arden's clock it was placed on {t5:%A} at {t5:%H:%M:%S}. It belongs to {_day(t5.date())}.",
            "load": f"Its row lands in the orders table with order_date {t5.date().isoformat()}.",
            "report": f"It is one of {_day(sat)}'s {b['orders']} orders. It adds {dollars(o5['total_cents'])}.",
            "again": "Its row is replaced by the same row. It is still counted once.",
            "next_morning": "Nothing about it changed. It is read again anyway.",
            "changed": "It did not change, so Saturday's number did not move.",
            "still_wrong": "On Thursday it is one of the orders that never get loaded.",
        },
        str(p4): {
            "question": "This order will change after the manager has looked.",
            "source": f"Delivered on Friday. Total {dollars(by_id[p4]['total_cents'])}. Nothing unusual yet.",
            "page": "It is on a later page. Extract has not reached it yet.",
            "all_pages": "Extract has it now, exactly as the API sent it.",
            "wrong_day": "Placed at 10:20 in Arden. Its day is Friday on both clocks.",
            "right_day": f"It belongs to {_day(friday)}.",
            "load": "Its row lands in the orders table. Refunded: $0.00.",
            "report": f"It is part of {_day(friday)}'s {dollars(friday_monday)}.",
            "again": "Its row is replaced by the same row.",
            "next_morning": f"On Monday at 11:00 it was refunded {dollars(o4['refunded_cents'])}. The full load picks up the new state.",
            "changed": f"Its refund lowers {_day(friday)} by {dollars(o4['refunded_cents'])}. The report names it as the reason.",
            "still_wrong": "On Thursday it is one of the orders that never get loaded.",
        },
        str(p2): {
            "question": "This order was cancelled the day after it was placed.",
            "source": f"Placed on {_day(_local(o2['placed_at']).date())}. Status: {o2['status']}. Total {dollars(o2['total_cents'])}.",
            "page": "It is on a later page. Extract has not reached it yet.",
            "all_pages": "Extract has it now, still marked cancelled.",
            "wrong_day": "Placed at 20:15 in Arden, so the UTC date moves it to Thursday. Wrong.",
            "right_day": f"It belongs to {_day(_local(o2['placed_at']).date())}.",
            "load": "Its row lands in the orders table with status cancelled.",
            "report": f"A cancelled order is not a sale. It is counted as a cancelled order, and adds {dollars(0)}.",
            "again": "Its row is replaced by the same row.",
            "next_morning": "Nothing about it changed. It is read again anyway.",
            "changed": "It was already cancelled on Monday, so its day did not move.",
            "still_wrong": "On Thursday it is one of the orders that never get loaded.",
        },
    }
    for step in steps:
        step["follow"] = {case: lines[step["id"]] for case, lines in follow.items()}

    recap = {
        "answer": {
            "yesterday": _day(sun),
            "yesterday_orders": y["orders"],
            "yesterday_money": dollars(y["revenue_cents"]),
            "day_before": _day(sat),
            "day_before_orders": b["orders"],
            "day_before_money": dollars(b["revenue_cents"]),
            "change_orders": f"{monday.answer['change']['orders']:+d}",
            "change_money": _signed(monday.answer["change"]["revenue_cents"]),
        },
        "trap": {
            "day": _day(sat),
            "wrong_orders": wrong[sat][0],
            "wrong_money": dollars(wrong[sat][1]),
            "right_orders": b["orders"],
            "right_money": dollars(b["revenue_cents"]),
            "order": p5,
        },
        "change": {
            "day": _day(friday),
            "before": dollars(friday_monday),
            "after": dollars(friday_tuesday),
            "by": dollars(refund["refunded_cents"]),
            "order": p4,
            "wait_hours": refund_wait,
        },
        "load": {
            "orders": n,
            "calls": calls,
            "item_lines": item_lines,
            "tuesday_orders": n2,
            "tuesday_changed": changed_by_tuesday,
            "limit": RATE_LIMIT_CALLS,
            "page_size": PAGE_SIZE,
            "refused_day": refused["day"],
            "refused_call": refused["calls"],
        },
        "growth": measured,
    }
    return {
        "recap": recap,
        "milestone": 1,
        "of": 5,
        "title": "The report, by hand",
        "lede": "Every order is read from the order system, changed in Python, loaded into one warehouse "
        "table, and added up for the manager. Watch real Arden orders move through it.",
        "stages": [
            {"id": "api", "name": "Order system API", "role": "Source", "state": "given"},
            {"id": "extract", "name": "Extract", "role": "Ingest", "state": "new"},
            {
                "id": "raw",
                "name": "Raw landing table",
                "role": "Store",
                "state": "not_yet",
                "note": "Not yet. The API's answer is used once and thrown away.",
            },
            {
                "id": "orders",
                "name": "Clean orders table",
                "role": "Transform, store",
                "state": "new",
            },
            {"id": "report", "name": "Daily report", "role": "Serve", "state": "new"},
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
            {"id": str(p5), "label": f"Order {p5}", "hint": "placed at 23:58"},
            {"id": str(p4), "label": f"Order {p4}", "hint": "refunded 3 days later"},
            {"id": str(p2), "label": f"Order {p2}", "hint": "cancelled the next day"},
        ],
        "steps": steps,
        "still_wrong": "It reloads every order every day. It is slow, and it hits the API's rate limit.",
        "next": "Milestone 2: load only what changed.",
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
    """The numbers shown on milestone_1_recap.html."""
    recap = dict((flow or build())["recap"])
    glossary = (MILESTONE_DIR.parent.parent / "GLOSSARY.md").read_text(encoding="utf-8")
    recap["numbers"] = {
        "notebooks": len(list((MILESTONE_DIR / "notebooks").glob("0*.ipynb"))),
        "tests": count_tests(),
        "tables": 3,  # orders, orders_previous, load_log
        "rows": recap["load"]["orders"],
        "words": len(
            re.findall(r"^\| \*\*.+\| Milestone 1, notebook \d \|", glossary, flags=re.MULTILINE)
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
    flow = build()
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
