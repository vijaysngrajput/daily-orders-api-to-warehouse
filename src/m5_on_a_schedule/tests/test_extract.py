from daily_orders.extract import fetch_changed_orders, fetch_deletes


def test_only_changed_orders_come_back(api, waits):
    extract = fetch_changed_orders(api.morning("2026-06-09"), "2026-06-08T03:48:53Z", waits.append)
    assert (len(extract.orders), extract.api_calls) == (29, 2)


def test_a_server_error_is_tried_again_with_longer_waits(api, waits):
    api.morning("2026-06-09").fault("error_500", 3)
    assert len(fetch_changed_orders(api, "2026-06-08T03:48:53Z", waits.append).orders) == 29
    assert waits == [1, 2, 4]


def test_the_change_feed_gives_the_delete(api):
    deletes, _ = fetch_deletes(api.morning("2026-06-14"), 0)
    assert [d["order_id"] for d in deletes] == [api.planted["P8"]]
