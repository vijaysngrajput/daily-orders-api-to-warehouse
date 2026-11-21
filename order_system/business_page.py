"""Build the stories shown on understand_business/arden_orders.html.

Every time, status and amount on that page comes from the event log.

    uv run python -m order_system.business_page          write the page's data
    uv run python -m order_system.business_page --check  fail if the page is out of date
"""

import json
import re
import sys
from pathlib import Path

from order_system.catalog import BUSINESS_TZ, CITIES
from order_system.generate import build as build_events
from order_system.planted import PLANTED

PAGE = Path(__file__).resolve().parent.parent / "understand_business" / "arden_orders.html"
MARKER = re.compile(
    r'(<script id="page-data" type="application/json">)(.*?)(</script>)', flags=re.DOTALL
)

POVS = [
    {
        "id": "customer",
        "name": "Customer",
        "blurb": "What a shopper sees: I ordered, it came. Sometimes: I cancelled, or I got money back.",
    },
    {
        "id": "ops",
        "name": "Operations",
        "blurb": "What the packing and delivery teams see: every move of the order, from the shelf to the door.",
    },
    {
        "id": "finance",
        "name": "Finance",
        "blurb": "What finance sees: only the steps that change money. Each one changes the number of the day the order was placed.",
    },
    {
        "id": "city",
        "name": "City manager",
        "blurb": "What a city manager sees: how many orders their city took each day, and whether they arrived.",
    },
    {
        "id": "data",
        "name": "Data engineer",
        "blurb": "What you will build from: every change, with the fields it touched. Each change sets updated_at.",
    },
]

SEEN_BY = {
    "placed": ["customer", "ops", "finance", "city", "data"],
    "packed": ["ops", "data"],
    "out_of_stock": ["customer", "ops", "finance", "data"],
    "out_for_delivery": ["customer", "ops", "data"],
    "delivered": ["customer", "ops", "city", "data"],
    "cancelled": ["customer", "ops", "finance", "city", "data"],
    "refund": ["customer", "finance", "data"],
    "deleted": ["finance", "data"],
}

TITLES = {
    "placed": "Order placed",
    "packed": "Packed",
    "out_of_stock": "Packed, one item missing",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "refund": "Refund given",
    "deleted": "Deleted from the order system",
}

HINTS = {
    "P1": "An ordinary order, delivered the next morning",
    "P2": "Cancelled the day after it was placed",
    "P3": "An item was out of stock",
    "P4": "Refunded three days after delivery",
    "P5": "Placed two minutes before midnight",
    "P6": "A change that showed up late",
    "P8": "A test order that was deleted",
}


def dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def _when(moment) -> str:
    local = moment.astimezone(BUSINESS_TZ)
    return f"{local:%a} {local.day} {local:%b}, {local:%H:%M}"


def _kind(before: dict | None, after: dict | None) -> str:
    if after is None:
        return "deleted"
    if before is None:
        return "placed"
    if after["refunded_cents"] != before["refunded_cents"]:
        return "refund"
    if after["total_cents"] != before["total_cents"]:
        return "out_of_stock"
    return after["status"]


def _changed_fields(before: dict | None, after: dict | None) -> list[str]:
    if after is None:
        return ["the whole row is gone"]
    if before is None:
        return ["a new row"]
    changes = []
    for name, new in after.items():
        old = before[name]
        if name == "items":
            for old_item, new_item in zip(old, new):
                if old_item != new_item:
                    changes.append(
                        f"items[{new_item['line_no']}].item_status: "
                        f"{old_item['item_status']} → {new_item['item_status']}"
                    )
        elif old != new:
            changes.append(f"{name}: {old if old is not None else 'null'} → {new}")
    return changes


def _describe(
    kind: str, before: dict | None, after: dict | None, order_day: str
) -> tuple[str, str]:
    """What happened, and what it does to the daily report."""
    if kind == "placed":
        items = ", ".join(f"{i['quantity']} × {i['product_name']}" for i in after["items"])
        who = "A tester" if after["customer_id"] == 9999 else "A customer"
        return (
            f"{who} in {CITIES[after['city']][0]} orders: {items}. The total is {dollars(after['total_cents'])}.",
            f"{order_day} gains 1 order and {dollars(after['total_cents'])}.",
        )
    if kind == "packed":
        return (
            "A worker takes the items off the shelf and packs the bag.",
            "No change to the report.",
        )
    if kind == "out_of_stock":
        missing = next(i for i in after["items"] if i["item_status"] == "out_of_stock")
        drop = before["total_cents"] - after["total_cents"]
        return (
            f"The {missing['product_name']} are out of stock. The bag is packed without them, and the total "
            f"drops from {dollars(before['total_cents'])} to {dollars(after['total_cents'])}.",
            f"{order_day} loses {dollars(drop)}. The order still counts.",
        )
    if kind == "out_for_delivery":
        return (
            "A driver picks up the bag and leaves for the customer's address.",
            "No change to the report.",
        )
    if kind == "delivered":
        return (
            "The driver hands over the bag and confirms it in the app.",
            "No change to the report.",
        )
    if kind == "cancelled":
        why = (
            "The customer refuses the bag at the door. The order is cancelled."
            if before["status"] == "out_for_delivery"
            else "The customer cancels the order before it leaves."
        )
        return (
            f"{why} It is not a sale any more.",
            f"{order_day} loses 1 order and {dollars(before['total_cents'])}.",
        )
    if kind == "refund":
        amount = after["refunded_cents"] - before["refunded_cents"]
        return (
            f"The customer reports a problem and gets {dollars(amount)} back.",
            f"{order_day} loses {dollars(amount)}. The order still counts.",
        )
    return (
        "It was a test order. Someone removes it from the order system. No trace is left.",
        f"{order_day} should lose 1 order and {dollars(before['total_cents'])}. But nothing tells the pipeline.",
    )


def build() -> dict:
    events, planted_ids = build_events()
    stories = []
    for planted in PLANTED:
        case = planted["case"]
        order_id = planted_ids[case]
        mine = [e for e in events if e.order_id == order_id]
        first = mine[0].event_time.astimezone(BUSINESS_TZ)
        order_day = f"{first:%A} {first.day} {first:%B}"
        steps, before, last_day = [], None, None
        for event in sorted(mine, key=lambda e: e.event_time):
            after = event.after
            kind = _kind(before, after)
            what, report = _describe(kind, before, after, order_day)
            day = event.event_time.astimezone(BUSINESS_TZ).date()
            gap = (day - last_day).days if last_day else 0
            late = None
            if event.visible_at != event.event_time:
                wait = event.visible_at - event.event_time
                hours, minutes = divmod(int(wait.total_seconds()) // 60, 60)
                late = (
                    f"The driver's phone was offline. The order system showed this change only at "
                    f"{_when(event.visible_at)}: {hours} hours and {minutes} minutes late. "
                    f"Its updated_at still says {_when(event.event_time)}."
                )
            state = after or before
            steps.append(
                {
                    "kind": kind,
                    "title": TITLES[kind],
                    "when": _when(event.event_time),
                    "gap": "the next day" if gap == 1 else (f"{gap} days later" if gap > 1 else ""),
                    "what": what,
                    "report": report,
                    "late": late,
                    "status": "deleted" if after is None else after["status"],
                    "total": dollars(state["total_cents"]),
                    "refunded": dollars(state["refunded_cents"]),
                    "fields": _changed_fields(before, after),
                    "seen_by": SEEN_BY[kind],
                }
            )
            before, last_day = after or before, day
        stories.append(
            {
                "case": case,
                "order_id": order_id,
                "hint": HINTS[case],
                "city": CITIES[planted["city"]][0],
                "utc_note": (
                    f"The order system stores this moment as {mine[0].after['placed_at']}. "
                    "In UTC it is already the next day."
                    if mine[0].event_time.date() != first.date()
                    else ""
                ),
                "steps": steps,
            }
        )
    return {"povs": POVS, "stories": stories}


def page_data() -> dict:
    return json.loads(MARKER.search(PAGE.read_text(encoding="utf-8")).group(2))


def main() -> int:
    data = build()
    if "--check" in sys.argv:
        ok = page_data() == data
        print(
            "arden_orders.html matches the event log."
            if ok
            else "arden_orders.html is out of date."
        )
        return 0 if ok else 1
    text = PAGE.read_text(encoding="utf-8")
    payload = json.dumps(data, indent=1, ensure_ascii=False).replace("</", "<\\/")
    PAGE.write_text(
        MARKER.sub(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), text),
        encoding="utf-8",
    )
    print(f"Wrote {len(data['stories'])} order stories into {PAGE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
