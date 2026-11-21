from datetime import date, datetime, timezone

import duckdb
import pytest

from daily_orders.extract import fetch_all_orders
from daily_orders.load import connect, loaded_run_date, replace_orders
from daily_orders.transform import to_orders_table

LOADED = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)


def load(api, con, day: str):
    extract = fetch_all_orders(api.morning(day))
    run_date = date.fromisoformat(day)
    replace_orders(
        con, to_orders_table(extract.orders, LOADED, run_date), run_date, extract.api_calls
    )


def count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchall()[0][0]


def snapshot(con, table):
    return con.execute(f"SELECT * EXCLUDE (loaded_at) FROM {table} ORDER BY order_id").fetchall()


def test_first_load_fills_orders_only(api, warehouse):
    con = connect(warehouse)
    load(api, con, "2026-06-08")
    assert count(con, "orders") == 226 and count(con, "orders_previous") == 0
    assert con.execute("SELECT * EXCLUDE (loaded_at) FROM load_log").fetchall() == [
        (date(2026, 6, 8), 226, 10)
    ]


def test_a_new_morning_keeps_the_old_table(api, warehouse):
    con = connect(warehouse)
    load(api, con, "2026-06-08")
    load(api, con, "2026-06-09")
    assert count(con, "orders") == 251 and count(con, "orders_previous") == 226
    assert loaded_run_date(con) == date(2026, 6, 9)
    assert loaded_run_date(con, "orders_previous") == date(2026, 6, 8)


def test_running_the_same_morning_twice_changes_nothing(api, warehouse):
    con = connect(warehouse)
    load(api, con, "2026-06-08")
    load(api, con, "2026-06-09")
    once = snapshot(con, "orders"), snapshot(con, "orders_previous"), count(con, "load_log")
    load(api, con, "2026-06-09")
    assert (
        snapshot(con, "orders"),
        snapshot(con, "orders_previous"),
        count(con, "load_log"),
    ) == once


def test_a_failed_load_leaves_the_old_table(api, warehouse):
    con = connect(warehouse)
    load(api, con, "2026-06-08")
    extract = fetch_all_orders(api.morning("2026-06-09"))
    broken = to_orders_table(extract.orders, LOADED, date(2026, 6, 9))
    broken.loc[1, "order_id"] = broken.loc[0, "order_id"]  # the same key twice
    with pytest.raises(duckdb.Error):
        replace_orders(con, broken, date(2026, 6, 9), extract.api_calls)
    assert count(con, "orders") == 226 and loaded_run_date(con) == date(2026, 6, 8)
    assert count(con, "orders_previous") == 0
