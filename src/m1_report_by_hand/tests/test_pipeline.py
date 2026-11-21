"""The whole pipeline, held up against the answer key."""

import pytest

from daily_orders.extract import RateLimited
from daily_orders.load import connect
from daily_orders.pipeline import run
from daily_orders.report import daily_report, dollars, format_answer


def test_the_managers_numbers_for_the_morning_of_june_8(api, warehouse):
    answer = run(api.morning("2026-06-08"), warehouse).answer
    assert (answer["yesterday"]["orders"], answer["yesterday"]["revenue_cents"]) == (30, 80711)
    assert (answer["day_before"]["orders"], answer["day_before"]["revenue_cents"]) == (33, 78671)
    assert answer["change"] == {"orders": -3, "revenue_cents": 2040}
    assert answer["yesterday"]["by_city"]["LKS"]["revenue_cents"] == 14498
    assert answer["compared_with"] is None and answer["earlier_changes"] == []


@pytest.mark.parametrize("day", ["2026-06-03", "2026-06-06", "2026-06-08", "2026-06-10"])
def test_the_report_equals_the_answer_key(api, warehouse, day):
    run(api.morning(day), warehouse)
    con = connect(warehouse)
    assert daily_report(con) == api.truth(day)


def test_the_refund_changes_friday_and_names_the_order(api, warehouse):
    run(api.morning("2026-06-08"), warehouse)
    answer = run(api.morning("2026-06-09"), warehouse).answer
    assert answer["compared_with"] == "2026-06-08"
    assert answer["earlier_changes"] == [
        {
            "order_date": "2026-06-05",
            "orders_change": 0,
            "cancelled_orders_change": 0,
            "revenue_cents_change": -578,
            "refunded_cents_change": 578,
            "reasons": [{"order_id": api.planted["P4"], "what": "refund of $5.78"}],
        }
    ]
    assert "because order 1130: refund of $5.78" in format_answer(answer)


def test_every_planted_change_is_found_with_its_reason(api, warehouse):
    """A full load sees everything: a cancel, a smaller total, a late update, even a delete."""
    expected = {
        "2026-06-05": ("2026-06-03", "P2", "cancelled"),
        "2026-06-06": ("2026-06-04", "P3", "total changed from $36.94 to $32.35"),
        "2026-06-10": ("2026-06-08", "P6", "cancelled"),
    }
    for day in range(4, 11):
        morning = f"2026-06-{day:02d}"
        answer = run(api.morning(morning), warehouse).answer
        if morning in expected:
            changed_day, case, what = expected[morning]
            assert [c["order_date"] for c in answer["earlier_changes"]] == [changed_day]
            assert answer["earlier_changes"][0]["reasons"] == [
                {"order_id": api.planted[case], "what": what}
            ]
        elif morning != "2026-06-09":
            assert answer["earlier_changes"] == []


def test_the_same_morning_twice_gives_the_same_answer(api, warehouse):
    run(api.morning("2026-06-08"), warehouse)
    first = run(api.morning("2026-06-09"), warehouse).answer
    assert run(api.morning("2026-06-09"), warehouse).answer == first


def test_a_refused_call_loads_nothing(api, warehouse):
    run(api.morning("2026-06-10"), warehouse)
    with pytest.raises(RateLimited):
        run(api.morning("2026-06-11"), warehouse)
    con = connect(warehouse)
    assert con.execute("SELECT max(run_date)::VARCHAR, count(*) FROM orders").fetchall()[0] == (
        "2026-06-10",
        282,
    )


def test_what_is_still_wrong_is_measured(api, warehouse):
    """LLD section 8: the load grows every morning until the API refuses it."""
    measured = []
    for day in ("2026-06-08", "2026-06-09", "2026-06-10"):
        result = run(api.morning(day), warehouse)
        measured.append((result.orders_loaded, result.api_calls))
    assert measured == [(226, 10), (251, 11), (282, 12)]


def test_dollars():
    assert (
        dollars(80711) == "$807.11" and dollars(-578) == "-$5.78" and dollars(106975) == "$1,069.75"
    )
