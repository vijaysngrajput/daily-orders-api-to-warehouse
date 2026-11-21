"""Report: answer the manager's question from the warehouse."""

from datetime import date, timedelta

import duckdb

from daily_orders.load import loaded_run_date

# One row per business day, per city. A cancelled order is not a sale.
DAILY_REPORT_SQL = """
SELECT
    order_date,
    city,
    count(*) FILTER (WHERE status <> 'cancelled')                       AS orders,
    count(*) FILTER (WHERE status = 'cancelled')                        AS cancelled_orders,
    coalesce(sum(total_cents - refunded_cents)
             FILTER (WHERE status <> 'cancelled'), 0)                   AS revenue_cents,
    coalesce(sum(refunded_cents) FILTER (WHERE status <> 'cancelled'), 0) AS refunded_cents
FROM {table}
GROUP BY order_date, city
ORDER BY order_date, city
"""

# Orders that are different in the two tables, or are in only one of them.
CHANGED_ORDERS_SQL = """
SELECT
    coalesce(now.order_id, old.order_id)     AS order_id,
    coalesce(now.order_date, old.order_date) AS order_date,
    old.status AS old_status, now.status AS new_status,
    old.total_cents AS old_total, now.total_cents AS new_total,
    old.refunded_cents AS old_refunded, now.refunded_cents AS new_refunded
FROM orders AS now
FULL JOIN orders_previous AS old USING (order_id)
WHERE coalesce(now.order_date, old.order_date) <= ?
  AND (now.order_id IS NULL OR old.order_id IS NULL
       OR (now.status = 'cancelled') <> (old.status = 'cancelled')
       OR now.total_cents <> old.total_cents
       OR now.refunded_cents <> old.refunded_cents)
ORDER BY order_id
"""

MEASURES = ("orders", "cancelled_orders", "revenue_cents", "refunded_cents")


def dollars(cents: int) -> str:
    """Cents as money: 80711 -> $807.11"""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def daily_report(con: duckdb.DuckDBPyConnection, table: str = "orders") -> list[dict]:
    """The report table: one row per business day, per city."""
    cursor = con.execute(DAILY_REPORT_SQL.format(table=table))
    names = [column[0] for column in cursor.description]
    rows = [dict(zip(names, values)) for values in cursor.fetchall()]
    for row in rows:
        row["order_date"] = row["order_date"].isoformat()
    return rows


def day_totals(report: list[dict]) -> dict[str, dict]:
    """Add the cities together: one total per business day."""
    totals: dict[str, dict] = {}
    for row in report:
        total = totals.setdefault(row["order_date"], dict.fromkeys(MEASURES, 0))
        for name in MEASURES:
            total[name] += row[name]
    return totals


def _one_day(report: list[dict], day: date) -> dict:
    rows = [r for r in report if r["order_date"] == day.isoformat()]
    total = day_totals(rows).get(day.isoformat(), dict.fromkeys(MEASURES, 0))
    cities = {r["city"]: {name: r[name] for name in MEASURES} for r in rows}
    return {"order_date": day.isoformat(), **total, "by_city": cities}


def _reason(row: dict) -> str:
    """Say in words what happened to one order."""
    if row["new_status"] is None:
        return "deleted from the order system"
    if row["old_status"] is None:
        return "new in the order system"
    if row["new_status"] == "cancelled" and row["old_status"] != "cancelled":
        return "cancelled"
    if row["new_refunded"] != row["old_refunded"]:
        return f"refund of {dollars(row['new_refunded'] - row['old_refunded'])}"
    return f"total changed from {dollars(row['old_total'])} to {dollars(row['new_total'])}"


def earlier_changes(con: duckdb.DuckDBPyConnection) -> tuple[date | None, list[dict]]:
    """Days whose numbers changed since the last look, with the orders that caused it.

    Returns the morning of the last look, and one entry per changed day.
    Only days the last look could already report are compared.
    """
    now_date, old_date = loaded_run_date(con), loaded_run_date(con, "orders_previous")
    if old_date is None or old_date >= now_date:
        return None, []
    last_reported = old_date - timedelta(days=1)
    now = day_totals(daily_report(con))
    old = day_totals(daily_report(con, "orders_previous"))

    cursor = con.execute(CHANGED_ORDERS_SQL, [last_reported])
    names = [column[0] for column in cursor.description]
    reasons: dict[str, list[dict]] = {}
    for values in cursor.fetchall():
        row = dict(zip(names, values))
        reasons.setdefault(row["order_date"].isoformat(), []).append(
            {"order_id": row["order_id"], "what": _reason(row)}
        )

    zero = dict.fromkeys(MEASURES, 0)
    changes = []
    for day in sorted(set(now) | set(old)):
        if day > last_reported.isoformat():
            continue
        before, after = old.get(day, zero), now.get(day, zero)
        if before != after:
            changes.append(
                {
                    "order_date": day,
                    **{f"{name}_change": after[name] - before[name] for name in MEASURES},
                    "reasons": reasons.get(day, []),
                }
            )
    return old_date, changes


def manager_answer(con: duckdb.DuckDBPyConnection) -> dict:
    """The four parts of the manager's question, as data."""
    run_date = loaded_run_date(con)
    if run_date is None:
        raise ValueError("The warehouse is empty. Run the load first.")
    report = daily_report(con)
    yesterday = _one_day(report, run_date - timedelta(days=1))
    day_before = _one_day(report, run_date - timedelta(days=2))
    compared_with, changes = earlier_changes(con)
    return {
        "run_date": run_date.isoformat(),
        "yesterday": yesterday,
        "day_before": day_before,
        "change": {
            "orders": yesterday["orders"] - day_before["orders"],
            "revenue_cents": yesterday["revenue_cents"] - day_before["revenue_cents"],
        },
        "compared_with": compared_with.isoformat() if compared_with else None,
        "earlier_changes": changes,
    }


def _signed(number: int) -> str:
    return f"{number:+d}"


def format_answer(answer: dict) -> str:
    """The report as the manager reads it."""

    def name(day: str) -> str:
        return date.fromisoformat(day).strftime("%a %Y-%m-%d")

    y, b, c = answer["yesterday"], answer["day_before"], answer["change"]
    lines = [
        f"Arden daily orders. Report for the morning of {name(answer['run_date'])}",
        "",
        f"  Yesterday   {name(y['order_date'])}   {y['orders']:>3} orders   {dollars(y['revenue_cents']):>10}",
        f"  Day before  {name(b['order_date'])}   {b['orders']:>3} orders   {dollars(b['revenue_cents']):>10}",
        f"  Change                        {_signed(c['orders']):>3} orders   "
        f"{('+' if c['revenue_cents'] >= 0 else '') + dollars(c['revenue_cents']):>10}",
        "",
        "  Yesterday by city",
    ]
    for city, row in sorted(y["by_city"].items()):
        lines.append(
            f"    {city}   {row['orders']:>3} orders   {dollars(row['revenue_cents']):>10}"
        )
    lines.append("")
    if answer["compared_with"] is None:
        lines.append("  Earlier days: there is no earlier report to compare with.")
    elif not answer["earlier_changes"]:
        lines.append(
            f"  Earlier days: no change since the report of {name(answer['compared_with'])}."
        )
    else:
        lines.append(
            f"  Earlier days that changed since the report of {name(answer['compared_with'])}:"
        )
        for change in answer["earlier_changes"]:
            money = change["revenue_cents_change"]
            lines.append(
                f"    {name(change['order_date'])}   {_signed(change['orders_change'])} orders   "
                f"{('+' if money >= 0 else '') + dollars(money)}"
            )
            for reason in change["reasons"]:
                lines.append(f"      because order {reason['order_id']}: {reason['what']}")
    return "\n".join(lines)
