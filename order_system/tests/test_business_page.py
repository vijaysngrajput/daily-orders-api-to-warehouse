"""The business page shows only what the event log says."""

from order_system import business_page


def test_the_page_holds_exactly_what_the_event_log_says():
    assert business_page.page_data() == business_page.build(), (
        "arden_orders.html is out of date. Run: uv run python -m order_system.business_page"
    )


def test_every_planted_order_has_a_story():
    stories = {s["case"]: s for s in business_page.build()["stories"]}
    assert set(stories) == {"P1", "P2", "P3", "P4", "P5", "P6", "P8"}
    assert [s["title"] for s in stories["P4"]["steps"]][-1] == "Refund given"
    assert stories["P4"]["steps"][-1]["gap"] == "3 days later"
    assert (
        stories["P6"]["steps"][-1]["late"]
        and "12 hours and 40 minutes late" in stories["P6"]["steps"][-1]["late"]
    )
    assert stories["P8"]["steps"][-1]["status"] == "deleted"
    assert "2026-06-07T04:58:12Z" in stories["P5"]["utc_note"]
