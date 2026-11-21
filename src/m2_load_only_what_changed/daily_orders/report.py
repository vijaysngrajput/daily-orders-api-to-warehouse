"""Report: read the daily report from the warehouse."""

from datetime import date, timedelta

import psycopg

MEASURES = ("orders", "cancelled_orders", "revenue_cents", "refunded_cents")


def dollars(cents: int) -> str:
    """Cents as money: 80711 -> $807.11"""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def daily_report(con: psycopg.Connection) -> list[dict]:
    """The report: one row per business day, per city."""
    rows = con.execute(
        "SELECT order_date, city, orders, cancelled_orders, revenue_cents, refunded_cents "
        "FROM analytics.daily_orders ORDER BY order_date, city"
    ).fetchall()
    return [
        {"order_date": day.isoformat(), "city": city, **dict(zip(MEASURES, numbers))}
        for day, city, *numbers in rows
    ]


def day_totals(report: list[dict]) -> dict[str, dict]:
    """Add the cities together: one total per business day."""
    totals: dict[str, dict] = {}
    for row in report:
        total = totals.setdefault(row["order_date"], dict.fromkeys(MEASURES, 0))
        for name in MEASURES:
            total[name] += row[name]
    return totals


def manager_answer(con: psycopg.Connection, run_date: date) -> dict:
    """Yesterday and the day before, as the report shows them."""
    totals = day_totals(daily_report(con))
    zero = dict.fromkeys(MEASURES, 0)
    yesterday, day_before = (run_date - timedelta(days=n) for n in (1, 2))
    y, b = totals.get(yesterday.isoformat(), zero), totals.get(day_before.isoformat(), zero)
    return {
        "run_date": run_date.isoformat(),
        "yesterday": {"order_date": yesterday.isoformat(), **y},
        "day_before": {"order_date": day_before.isoformat(), **b},
        "change": {
            "orders": y["orders"] - b["orders"],
            "revenue_cents": y["revenue_cents"] - b["revenue_cents"],
        },
    }


def format_answer(answer: dict) -> str:
    """The report as the manager reads it."""

    def name(day: str) -> str:
        return date.fromisoformat(day).strftime("%a %Y-%m-%d")

    y, b, c = answer["yesterday"], answer["day_before"], answer["change"]
    money = ("+" if c["revenue_cents"] >= 0 else "") + dollars(c["revenue_cents"])
    return "\n".join(
        [
            f"Arden daily orders. Report for the morning of {name(answer['run_date'])}",
            "",
            f"  Yesterday   {name(y['order_date'])}   {y['orders']:>3} orders   {dollars(y['revenue_cents']):>10}",
            f"  Day before  {name(b['order_date'])}   {b['orders']:>3} orders   {dollars(b['revenue_cents']):>10}",
            f"  Change                        {c['orders']:>+3d} orders   {money:>10}",
        ]
    )
