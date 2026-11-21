"""The whole pipeline: what it now does well, and what is still wrong."""

import pytest

from daily_orders.extract import ExtractFailed
from daily_orders.load import read_watermark
from daily_orders.pipeline import run
from daily_orders.report import daily_report, day_totals, dollars
from order_system.truth import day_totals as true_totals


def run_mornings(api, con, *days):
    return [run(api.morning(day), con) for day in days]


def landed(con):
    return con.execute(
        "SELECT count(*), count(DISTINCT order_id) FROM analytics.orders_as_landed"
    ).fetchone()


# ---- What is fixed ----------------------------------------------------------


def test_the_second_morning_reads_only_what_changed(api, con):
    first, second = run_mornings(api, con, "2026-06-08", "2026-06-09")
    assert (first.rows_landed, first.api_calls, first.watermark_before) == (226, 10, None)
    assert (second.rows_landed, second.api_calls) == (29, 2)  # milestone 1 read 251 in 11 calls
    assert second.watermark_before == first.watermark_after == "2026-06-08T03:48:53Z"


def test_the_load_no_longer_dies_on_june_11(api, con):
    results = run_mornings(
        api, con, "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"
    )
    assert [r.api_calls for r in results] == [10, 2, 2, 3, 2]
    assert all(r.retries == 0 for r in results)


def test_the_first_morning_alone_is_still_right(api, con):
    answer = run(api.morning("2026-06-08"), con).answer
    assert (answer["yesterday"]["orders"], answer["yesterday"]["revenue_cents"]) == (30, 80711)
    assert daily_report(con) == api.truth("2026-06-08")


def test_a_failed_extract_lands_nothing_and_keeps_the_watermark(api, con, waits):
    run(api.morning("2026-06-08"), con)
    before = read_watermark(con)
    api.morning("2026-06-09").fault("error_500", 99)
    with pytest.raises(ExtractFailed):
        run(api, con, sleep=waits.append)
    assert landed(con) == (226, 226) and read_watermark(con) == before
    api.fault("error_500", 0)
    assert run(api, con).rows_landed == 29  # the next run reads the same rows: nothing was lost


def test_the_landing_table_keeps_every_version_of_an_order(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    versions = con.execute(
        "SELECT refunded_cents, run_id FROM analytics.orders_as_landed WHERE order_id = %s ORDER BY landing_id",
        [api.planted["P4"]],
    ).fetchall()
    assert versions == [(0, "2026-06-08_1"), (578, "2026-06-09_1")]


# ---- What is still wrong ----------------------------------------------------


def test_still_wrong_a_changed_order_is_counted_twice(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    assert landed(con) == (255, 251)  # 4 orders are in the table twice
    report, truth = day_totals(daily_report(con)), true_totals(api.truth("2026-06-09"))
    friday = "2026-06-05"
    assert truth[friday]["orders"] == 37 and report[friday]["orders"] == 38
    # The refunded order is counted once at its old value and once at its new value.
    assert report[friday]["revenue_cents"] - truth[friday]["revenue_cents"] == 1475
    assert [day for day in truth if report[day] != truth[day]] == ["2026-06-05", "2026-06-07"]


def test_still_wrong_running_twice_makes_it_worse(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    once = day_totals(daily_report(con))
    again = run(api, con)  # the same morning, a second time
    assert again.run_id == "2026-06-09_2" and again.rows_landed == 1  # the boundary order, again
    twice = day_totals(daily_report(con))
    assert twice["2026-06-08"]["orders"] == once["2026-06-08"]["orders"] + 1
    assert twice != once


def test_still_wrong_a_repeated_page_doubles_its_orders(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09", "2026-06-10")
    result = run(api.morning("2026-06-11"), con)  # the planted fault: page 2 is served twice
    ids = [
        row[0]
        for row in con.execute(
            "SELECT order_id FROM analytics.orders_as_landed WHERE run_id = %s", [result.run_id]
        )
    ]
    assert result.rows_landed == 49 and len(set(ids)) == 37


def test_still_wrong_a_late_update_is_never_loaded(api, con):
    """Order P6 was cancelled before the watermark, but the API showed it after. Milestone 3 fixes it."""
    run_mornings(api, con, "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11")
    statuses = [
        row[0]
        for row in con.execute(
            "SELECT status FROM analytics.orders_as_landed WHERE order_id = %s", [api.planted["P6"]]
        )
    ]
    assert "cancelled" not in statuses
    assert api.get(f"/orders/{api.planted['P6']}").json()["status"] == "cancelled"


def test_dollars():
    assert dollars(98579) == "$985.79" and dollars(-1475) == "-$14.75"
