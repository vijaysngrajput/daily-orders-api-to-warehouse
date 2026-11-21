"""Report: build the daily report one day at a time, and read it."""

from datetime import date, timedelta

import psycopg

MEASURES = ("orders", "cancelled_orders", "revenue_cents", "refunded_cents")

# Replace one day: delete that day's rows, then insert them again from core.orders.
# Run it once or five times: the day ends up the same. Other days are not touched.
DELETE_DAYS_SQL = "DELETE FROM analytics.daily_orders WHERE order_date = ANY(%(days)s)"

INSERT_DAYS_SQL = """
INSERT INTO analytics.daily_orders
SELECT
    order_date,
    city,
    count(*) FILTER (WHERE status <> 'cancelled'),
    count(*) FILTER (WHERE status = 'cancelled'),
    coalesce(sum(total_cents - refunded_cents) FILTER (WHERE status <> 'cancelled'), 0),
    coalesce(sum(refunded_cents) FILTER (WHERE status <> 'cancelled'), 0)
FROM core.orders
WHERE order_date = ANY(%(days)s)
  AND NOT is_deleted                    -- a deleted order is not counted
GROUP BY order_date, city
"""


def dollars(cents: int) -> str:
    """Cents as money: 80711 -> $807.11"""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def rebuild_days(con: psycopg.Connection, days: list[date]) -> None:
    """Rebuild the report for these business days. Each day is one partition of the table."""
    if not days:
        return
    with con.transaction():
        con.execute(DELETE_DAYS_SQL, {"days": list(days)})
        con.execute(INSERT_DAYS_SQL, {"days": list(days)})


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


def earlier_changes(con: psycopg.Connection, run_id: str) -> list[dict]:
    """The earlier days this run changed, each with the orders that caused it."""
    rows = con.execute(
        "SELECT order_date, order_id, what, orders_change, revenue_cents_change "
        "FROM ops.report_changes WHERE run_id = %s ORDER BY order_date, order_id",
        [run_id],
    ).fetchall()
    days: dict[str, dict] = {}
    for day, order_id, what, orders_change, money in rows:
        entry = days.setdefault(
            day.isoformat(),
            {
                "order_date": day.isoformat(),
                "orders_change": 0,
                "revenue_cents_change": 0,
                "reasons": [],
            },
        )
        entry["orders_change"] += orders_change
        entry["revenue_cents_change"] += money
        entry["reasons"].append({"order_id": order_id, "what": what})
    return list(days.values())


def manager_answer(con: psycopg.Connection, run_date: date, run_id: str) -> dict:
    """The four parts of the manager's question, as data."""
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
        "earlier_changes": earlier_changes(con, run_id),
    }


def format_answer(answer: dict) -> str:
    """The report as the manager reads it."""

    def name(day: str) -> str:
        return date.fromisoformat(day).strftime("%a %Y-%m-%d")

    def signed(cents: int) -> str:
        return ("+" if cents >= 0 else "") + dollars(cents)

    y, b, c = answer["yesterday"], answer["day_before"], answer["change"]
    lines = [
        f"Arden daily orders. Report for the morning of {name(answer['run_date'])}",
        "",
        f"  Yesterday   {name(y['order_date'])}   {y['orders']:>3} orders   {dollars(y['revenue_cents']):>10}",
        f"  Day before  {name(b['order_date'])}   {b['orders']:>3} orders   {dollars(b['revenue_cents']):>10}",
        f"  Change                        {c['orders']:>+3d} orders   {signed(c['revenue_cents']):>10}",
        "",
    ]
    if not answer["earlier_changes"]:
        lines.append("  Earlier days: no change in this run.")
    else:
        lines.append("  Earlier days that changed in this run:")
        for change in answer["earlier_changes"]:
            lines.append(
                f"    {name(change['order_date'])}   {change['orders_change']:+d} orders   "
                f"{signed(change['revenue_cents_change'])}"
            )
            for reason in change["reasons"]:
                lines.append(f"      because order {reason['order_id']}: {reason['what']}")
    return "\n".join(lines)
