import pytest

from daily_orders.extract import ExtractFailed, fetch_changed_orders, fetch_deletes


def test_only_changed_orders_come_back(api, waits):
    extract = fetch_changed_orders(api.morning("2026-06-09"), "2026-06-08T03:48:53Z", waits.append)
    assert (len(extract.orders), extract.api_calls) == (29, 2)


def test_a_server_error_is_tried_again_with_longer_waits(api, waits):
    api.morning("2026-06-09").fault("error_500", 3)
    extract = fetch_changed_orders(api, "2026-06-08T03:48:53Z", waits.append)
    assert waits == [1, 2, 4] and len(extract.orders) == 29


def test_it_gives_up_after_five_tries(api, waits):
    api.morning("2026-06-09").fault("error_500", 99)
    with pytest.raises(ExtractFailed, match="after 5 tries"):
        fetch_changed_orders(api, "2026-06-08T03:48:53Z", waits.append)


def test_the_change_feed_gives_the_delete_that_updated_at_cannot(api):
    assert fetch_deletes(api.morning("2026-06-13"), 0)[0] == []
    deletes, position = fetch_deletes(api.morning("2026-06-14"), 0)
    assert [d["order_id"] for d in deletes] == [api.planted["P8"]]
    assert fetch_deletes(api, position) == (
        [],
        position,
    )  # read from the saved position: nothing new
    changed = fetch_changed_orders(api, "2026-06-13T00:00:00Z").orders
    assert api.planted["P8"] not in {o["order_id"] for o in changed}  # the query cannot see it
