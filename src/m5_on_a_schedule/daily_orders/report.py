"""Report: read the daily report that dbt built, and explain what changed."""

from datetime import date, timedelta

import psycopg

from daily_orders.warehouse import table_exists

MEASURES = ("orders", "cancelled_orders", "revenue_cents", "refunded_cents")


def dollars(cents: int) -> str:
    """Cents as money: 80711 -> $807.11"""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def daily_report(con: psycopg.Connection) -> list[dict]:
    """The report: one row per business day, per city."""
    rows = con.execute(
        "SELECT order_date, city, orders, cancelled_orders, revenue_cents, refunded_cents "
        "FROM marts.daily_orders ORDER BY order_date, city"
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


STATE_SQL = (
    "SELECT order_id, order_date, status, total_cents, refunded_cents, is_deleted FROM {table}"
)


def orders_state(con: psycopg.Connection, table: str = "core.orders") -> dict[int, tuple]:
    """Every order in a table, as (order_date, status, total, refunded, is_deleted)."""
    if not table_exists(con, table):
        return {}
    return {row[0]: row[1:] for row in con.execute(STATE_SQL.format(table=table)).fetchall()}


def remember_reported(con: psycopg.Connection) -> None:
    """Save the orders as this report counted them. Called only after a run that passed every test."""
    with con.transaction():
        con.execute("TRUNCATE ops.orders_reported")
        con.execute("INSERT INTO ops.orders_reported " + STATE_SQL.format(table="core.orders"))


def _counted(state: tuple) -> tuple[int, int]:
    """What one order adds to the report: (orders, cents)."""
    _, status, total, refunded, deleted = state
    return (0, 0) if deleted or status == "cancelled" else (1, total - refunded)


def explain_changes(con: psycopg.Connection, run_id: str, previous_run_date: date | None) -> None:
    """Write down which orders changed an earlier day's number in this run, and by how much.

    It compares the orders now with the orders as the last good report counted them.
    """
    before = orders_state(con, "ops.orders_reported")
    if previous_run_date is None or not before:
        return
    rows = []
    for order_id, now in orders_state(con).items():
        old = before.get(order_id)
        if old is None or now[0] >= previous_run_date or _counted(old) == _counted(now):
            continue  # a new order, a day no report showed yet, or nothing the report can see
        if now[4] and not old[4]:
            what = "deleted from the order system"
        elif now[1] == "cancelled" and old[1] != "cancelled":
            what = "cancelled"
        elif now[3] != old[3]:
            what = f"refund of {dollars(now[3] - old[3])}"
        else:
            what = f"total changed from {dollars(old[2])} to {dollars(now[2])}"
        orders_change = _counted(now)[0] - _counted(old)[0]
        rows.append(
            (run_id, now[0], order_id, what, orders_change, _counted(now)[1] - _counted(old)[1])
        )
    with con.cursor() as cursor:
        cursor.executemany("INSERT INTO ops.report_changes VALUES (%s, %s, %s, %s, %s, %s)", rows)


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
