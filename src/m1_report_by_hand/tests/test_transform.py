from datetime import date, datetime, timezone

import pandas as pd

from daily_orders.extract import fetch_all_orders
from daily_orders.transform import ORDER_COLUMNS, business_day, to_orders_table

LOADED = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
RUN = date(2026, 6, 8)


def table(api):
    return to_orders_table(fetch_all_orders(api.morning("2026-06-08")).orders, LOADED, RUN)


def test_one_row_per_order(api):
    orders = table(api)
    assert list(orders.columns) == ORDER_COLUMNS
    assert len(orders) == 226 and orders["order_id"].is_unique


def test_the_midnight_order_lands_on_saturday(api):
    orders = table(api).set_index("order_id")
    midnight = orders.loc[api.planted["P5"]]
    assert midnight["placed_at"] == pd.Timestamp("2026-06-07 04:58:12", tz="UTC")
    assert midnight["order_date"] == date(2026, 6, 6)


def test_business_day_moves_every_evening_order_back_a_day():
    times = pd.to_datetime(
        pd.Series(
            [
                "2026-06-07T23:59:59Z",
                "2026-06-08T00:00:00Z",
                "2026-06-08T04:59:59Z",
                "2026-06-08T05:00:00Z",
            ]
        ),
        utc=True,
    )
    assert list(business_day(times)) == [date(2026, 6, 7)] * 3 + [date(2026, 6, 8)]


def test_out_of_stock_items_are_not_counted(api):
    orders = to_orders_table(fetch_all_orders(api.morning("2026-06-06")).orders, LOADED, RUN)
    short = orders.set_index("order_id").loc[api.planted["P3"]]
    assert short["item_count"] == 3 and short["total_cents"] == 3235


def test_load_time_and_event_time_are_different_columns(api):
    orders = table(api)
    assert (orders["loaded_at"] == pd.Timestamp(LOADED)).all()
    assert (orders["placed_at"] < orders["loaded_at"]).all()


def test_no_orders_gives_an_empty_table_with_the_same_columns():
    empty = to_orders_table([], LOADED, RUN)
    assert list(empty.columns) == ORDER_COLUMNS and len(empty) == 0
