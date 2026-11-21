"""The answer key, worked out straight from the event log.

It does not use any pipeline code. Tests compare every pipeline against it.
"""

from collections import defaultdict
from datetime import datetime

from order_system.catalog import business_day, from_text
from order_system.generate import Event, orders_as_of


def daily_report(events: list[Event], now: datetime) -> list[dict]:
    """The true report at `now`: one row per business day, per city."""
    rows: dict[tuple, dict] = defaultdict(
        lambda: {"orders": 0, "cancelled_orders": 0, "revenue_cents": 0, "refunded_cents": 0}
    )
    for order in orders_as_of(events, now).values():
        key = (business_day(from_text(order["placed_at"])).isoformat(), order["city"])
        row = rows[key]
        if order["status"] == "cancelled":
            row["cancelled_orders"] += 1
            continue
        row["orders"] += 1
        row["revenue_cents"] += order["total_cents"] - order["refunded_cents"]
        row["refunded_cents"] += order["refunded_cents"]
    return [{"order_date": day, "city": city, **rows[(day, city)]} for day, city in sorted(rows)]


def day_totals(report: list[dict]) -> dict[str, dict]:
    """Add the cities together: one total per business day."""
    totals: dict[str, dict] = {}
    for row in report:
        total = totals.setdefault(
            row["order_date"],
            {"orders": 0, "cancelled_orders": 0, "revenue_cents": 0, "refunded_cents": 0},
        )
        for name in total:
            total[name] += row[name]
    return totals
