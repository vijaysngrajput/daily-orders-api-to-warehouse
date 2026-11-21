"""Transform: turn the API's orders into the rows of the `orders` table.

This runs in pandas, before the load. That order is called ETL.
Only the columns the report needs are kept. The rest is thrown away.
"""

from datetime import date, datetime

import pandas as pd

from daily_orders.config import BUSINESS_TZ

ORDER_COLUMNS = [
    "order_id",
    "customer_id",
    "city",
    "status",
    "placed_at",
    "order_date",
    "item_count",
    "total_cents",
    "refunded_cents",
    "updated_at",
    "loaded_at",
    "run_date",
]


def business_day(placed_at: pd.Series) -> pd.Series:
    """The business day of each order: the date of `placed_at` on Arden's clock.

    `placed_at` is in UTC. 23:58 on Saturday in Arden is already Sunday in UTC,
    so the time is moved to Arden's time zone before the date is taken.
    """
    return placed_at.dt.tz_convert(BUSINESS_TZ).dt.date


def to_orders_table(orders: list[dict], loaded_at: datetime, run_date: date) -> pd.DataFrame:
    """One row per order.

    `placed_at` is event time: when the customer ordered.
    `loaded_at` is load time: when this pipeline copied the row.
    """
    if not orders:
        return pd.DataFrame(columns=ORDER_COLUMNS)
    table = pd.DataFrame(orders)
    table["placed_at"] = pd.to_datetime(table["placed_at"], utc=True)
    table["updated_at"] = pd.to_datetime(table["updated_at"], utc=True)
    table["order_date"] = business_day(table["placed_at"])
    table["item_count"] = table["items"].apply(
        lambda items: sum(1 for item in items if item["item_status"] == "ok")
    )
    table["loaded_at"] = pd.Timestamp(loaded_at)
    table["run_date"] = run_date
    return table[ORDER_COLUMNS]
