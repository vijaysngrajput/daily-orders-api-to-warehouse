"""Build the event log: every change to every order, in order.

The log is the one truth of the order system. Everything the API serves
is worked out from it. The same seed always gives the same log.
"""

import copy
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from order_system.catalog import CITIES, DAYS, FIRST_DAY, PRODUCTS, local, to_text
from order_system.planted import PLANTED

SEED = 20260601
FIRST_ORDER_ID = 1001


@dataclass(frozen=True)
class Event:
    pos: int  # position in the log: 1, 2, 3 ...
    op: str  # c = create, u = update, d = delete
    order_id: int
    event_time: datetime  # when the change happened; becomes updated_at
    visible_at: datetime  # when the API starts to show it
    after: dict | None  # the whole order after the change; None for a delete


def _total(items: list[dict]) -> int:
    return sum(i["quantity"] * i["unit_price_cents"] for i in items if i["item_status"] == "ok")


def _new_order(city: str, customer_id: int, lines: list[tuple[str, int]], placed: datetime) -> dict:
    items = [
        {
            "line_no": n,
            "product_id": pid,
            "product_name": PRODUCTS[pid][0],
            "quantity": qty,
            "unit_price_cents": PRODUCTS[pid][1],
            "item_status": "ok",
        }
        for n, (pid, qty) in enumerate(lines, start=1)
    ]
    return {
        "order_id": None,
        "customer_id": customer_id,
        "city": city,
        "status": "placed",
        "placed_at": to_text(placed),
        "updated_at": to_text(placed),
        "delivered_at": None,
        "cancelled_at": None,
        "total_cents": _total(items),
        "refunded_cents": 0,
        "refunded_at": None,
        "items": items,
    }


def _apply(order: dict, what: str, moment: datetime) -> dict:
    """Return the order after one change."""
    order = copy.deepcopy(order)
    order["updated_at"] = to_text(moment)
    kind, _, detail = what.partition(":")
    if kind == "refund":
        order["refunded_cents"] = int(detail)
        order["refunded_at"] = to_text(moment)
        return order
    order["status"] = kind
    if kind == "delivered":
        order["delivered_at"] = to_text(moment)
    if kind == "cancelled":
        order["cancelled_at"] = to_text(moment)
    if detail.startswith("out_of_stock"):
        missing = detail.split(":")[1]
        for item in order["items"]:
            if item["product_id"] == missing:
                item["item_status"] = "out_of_stock"
        order["total_cents"] = _total(order["items"])
    return order


def _ordinary_orders(rng: random.Random) -> list[dict]:
    """Ordinary orders. They move to `delivered` and their money never changes."""
    histories = []
    codes = list(CITIES)
    weights = [CITIES[c][1] for c in codes]
    for d in range(DAYS):
        day = FIRST_DAY + timedelta(days=d)
        for _ in range(rng.randint(24, 36)):
            # Placed between 07:00 and 23:30 local time.
            minute_of_day = rng.randint(7 * 60, 23 * 60 + 29)
            placed = local(day, minute_of_day // 60, minute_of_day % 60, rng.randint(0, 59))
            city = rng.choices(codes, weights)[0]
            lines = [
                (pid, rng.choice([1, 1, 1, 2, 2, 3]))
                for pid in rng.sample(sorted(PRODUCTS), rng.randint(2, 5))
            ]
            order = _new_order(city, rng.randint(1000, 1400), lines, placed)
            if minute_of_day < 21 * 60:
                packed = placed + timedelta(minutes=rng.randint(20, 60))
            else:  # too late for tonight: packed the next morning
                packed = local(day + timedelta(days=1), 7, 30) + timedelta(
                    minutes=rng.randint(0, 90)
                )
            out = packed + timedelta(minutes=rng.randint(30, 90))
            delivered = out + timedelta(minutes=rng.randint(30, 60))
            steps = [
                (placed, placed, "placed"),
                (packed, packed, "packed"),
                (out, out, "out_for_delivery"),
                (delivered, delivered, "delivered"),
            ]
            histories.append({"case": None, "order": order, "steps": steps})
    return histories


def _planted_orders() -> list[dict]:
    histories = []
    for p in PLANTED:
        steps = []
        for day, hour, minute, second, what, visible in p["steps"]:
            moment = local(day, hour, minute, second)
            shown = local(*visible) if visible else moment
            steps.append((moment, shown, what))
        order = _new_order(p["city"], p["customer_id"], p["items"], steps[0][0])
        histories.append({"case": p["case"], "order": order, "steps": steps})
    return histories


def build(seed: int = SEED) -> tuple[list[Event], dict[str, int]]:
    """Return the event log, and the order_id of each planted case."""
    rng = random.Random(seed)
    histories = _ordinary_orders(rng) + _planted_orders()

    # Order ids follow the time the orders were placed.
    histories.sort(key=lambda h: h["steps"][0][0])
    planted_ids = {}
    raw = []
    for number, h in enumerate(histories, start=FIRST_ORDER_ID):
        order = h["order"]
        order["order_id"] = number
        if h["case"]:
            planted_ids[h["case"]] = number
        for moment, shown, what in h["steps"]:
            if what == "placed":
                raw.append((shown, moment, number, "c", copy.deepcopy(order)))
            elif what == "deleted":
                raw.append((shown, moment, number, "d", None))
            else:
                order = _apply(order, what, moment)
                raw.append((shown, moment, number, "u", order))

    # The log is in the order the source recorded the changes.
    raw.sort(key=lambda r: (r[0], r[1], r[2]))
    events = [
        Event(pos, op, order_id, moment, shown, after)
        for pos, (shown, moment, order_id, op, after) in enumerate(raw, start=1)
    ]
    return events, planted_ids


def orders_as_of(events: list[Event], now: datetime) -> dict[int, dict]:
    """The latest state of every order the API can see at `now`."""
    latest: dict[int, dict | None] = {}
    for event in events:  # already in log order
        if event.visible_at <= now:
            latest[event.order_id] = event.after
    return {oid: order for oid, order in latest.items() if order is not None}
