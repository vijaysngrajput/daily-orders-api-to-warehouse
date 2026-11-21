import pytest

from daily_orders.extract import ExtractFailed, fetch_changed_orders


def test_no_watermark_means_a_full_load(api, waits):
    extract = fetch_changed_orders(api.morning("2026-06-08"), None, waits.append)
    assert (len(extract.orders), extract.api_calls, extract.retries) == (226, 10, 0)


def test_only_changed_orders_come_back(api, waits):
    extract = fetch_changed_orders(api.morning("2026-06-09"), "2026-06-08T03:48:53Z", waits.append)
    assert (len(extract.orders), extract.api_calls) == (29, 2)
    assert all(o["updated_at"] >= "2026-06-08T03:48:53Z" for o in extract.orders)
    assert extract.pages == [1] * 25 + [2] * 4


def test_the_boundary_order_is_read_again_not_lost(api, waits):
    """Ask again from the newest updated_at we hold: that one order comes back, never zero."""
    first = fetch_changed_orders(api.morning("2026-06-09"), "2026-06-08T03:48:53Z", waits.append)
    newest = max(o["updated_at"] for o in first.orders)
    again = fetch_changed_orders(api, newest, waits.append)
    assert [o["updated_at"] for o in again.orders] == [newest] == ["2026-06-09T04:17:31Z"]


def test_a_server_error_is_tried_again_with_longer_waits(api, waits):
    api.morning("2026-06-09").fault("error_500", 3)
    extract = fetch_changed_orders(api, "2026-06-08T03:48:53Z", waits.append)
    assert waits == [1, 2, 4]  # backoff: each wait is longer than the last
    assert (len(extract.orders), extract.api_calls, extract.retries) == (29, 5, 3)


def test_a_rate_limit_waits_as_long_as_the_api_says(api, waits):
    api.morning("2026-06-09").fault("rate_limit", 1)
    extract = fetch_changed_orders(api, "2026-06-08T03:48:53Z", waits.append)
    assert waits == [1.0] and len(extract.orders) == 29


def test_it_gives_up_after_five_tries(api, waits):
    api.morning("2026-06-09").fault("error_500", 99)
    with pytest.raises(ExtractFailed, match="after 5 tries"):
        fetch_changed_orders(api, "2026-06-08T03:48:53Z", waits.append)
    assert waits == [1, 2, 4, 8]


def test_our_own_mistake_is_not_retried(api, waits):
    with pytest.raises(ExtractFailed, match="400"):
        fetch_changed_orders(api.morning("2026-06-09"), "not a time", waits.append)
    assert waits == []


def test_a_first_load_that_is_too_big_now_waits_and_finishes(waits):
    """Milestone 1 died here. The retry carries the same load over the rate limit."""
    from fastapi.testclient import TestClient

    from order_system.app import create_app

    clock = type("Clock", (), {"now": 0.0, "__call__": lambda self: self.now})()

    def sleep(seconds):
        waits.append(seconds)
        clock.now += seconds

    api = TestClient(create_app(timer=clock))
    api.post("/admin/clock", json={"morning": "2026-06-10"})
    api.post("/admin/clock", json={"advance_days": 1})
    extract = fetch_changed_orders(api, None, sleep)
    assert len({o["order_id"] for o in extract.orders}) == 317
    assert extract.retries == 1 and len(waits) == 1 and waits[0] >= 10
