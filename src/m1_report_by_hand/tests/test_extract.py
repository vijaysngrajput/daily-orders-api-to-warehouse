import pytest

from daily_orders.extract import RateLimited, fetch_all_orders, read_run_date


def test_reads_every_order_once(api):
    extract = fetch_all_orders(api.morning("2026-06-08"))
    assert len(extract.orders) == 226
    assert extract.api_calls == 10
    assert len({o["order_id"] for o in extract.orders}) == 226


def test_orders_arrive_exactly_as_the_api_sent_them(api):
    extract = fetch_all_orders(api.morning("2026-06-08"))
    one = next(o for o in extract.orders if o["order_id"] == api.planted["P5"])
    assert one == api.get(f"/orders/{api.planted['P5']}").json()


def test_run_date_is_the_local_date_on_the_api_clock(api):
    assert read_run_date(api.morning("2026-06-08")).isoformat() == "2026-06-08"


def test_a_refused_call_stops_the_extract(api):
    with pytest.raises(RateLimited, match="refused call 13 after 300 orders"):
        fetch_all_orders(api.morning("2026-06-11"))
