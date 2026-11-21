"""The three properties of a safe pipeline, and everything milestone 2 left wrong, now right."""

from datetime import date

import pytest

from conftest import june, run_mornings, snapshot
from daily_orders import pipeline
from daily_orders.extract import ExtractFailed
from daily_orders.load import read_watermark
from daily_orders.report import daily_report, day_totals, rebuild_days

# ---- Property 1: run it twice, and nothing changes --------------------------


def test_running_the_same_morning_twice_changes_nothing(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    once = snapshot(con)
    again = run_mornings(api, con, "2026-06-09")[0]
    assert again.run_id == "2026-06-09_2" and again.rows_landed > 0  # it did read rows again ...
    assert (again.orders_inserted, again.orders_updated) == (0, 0)  # ... and changed nothing
    assert snapshot(con) == once and again.answer["earlier_changes"] == []


def test_five_runs_in_a_row_equal_one(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    once = snapshot(con)
    run_mornings(api, con, *["2026-06-09"] * 5)
    assert snapshot(con) == once


# ---- Property 2: re-run an old day, and that day is corrected ---------------


def test_rebuilding_one_day_corrects_it_and_touches_no_other(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    right = snapshot(con)[1]
    con.execute(
        "UPDATE analytics.daily_orders SET revenue_cents = 1, orders = 99 WHERE order_date = '2026-06-05'"
    )
    con.execute(
        "DELETE FROM analytics.daily_orders WHERE order_date = '2026-06-06' AND city = 'LKS'"
    )
    rebuild_days(con, [date(2026, 6, 5)])
    report = snapshot(con)[1]
    assert [r for r in report if r[0] == date(2026, 6, 5)] == [
        r for r in right if r[0] == date(2026, 6, 5)
    ]
    assert (
        len([r for r in report if r[0] == date(2026, 6, 6)]) == 2
    )  # the other broken day was left alone
    rebuild_days(con, [date(2026, 6, 6)])
    assert snapshot(con)[1] == right


# ---- Property 3: an order cancelled later lowers the old day's total --------


def test_an_order_cancelled_later_lowers_the_old_days_total(api, con):
    run_mornings(api, con, *june(2, 4))
    before = day_totals(daily_report(con))["2026-06-03"]
    result = run_mornings(api, con, "2026-06-05")[0]
    after = day_totals(daily_report(con))["2026-06-03"]
    assert after["orders"] == before["orders"] - 1
    assert after["revenue_cents"] == before["revenue_cents"] - 2056
    assert after["cancelled_orders"] == before["cancelled_orders"] + 1
    assert result.answer["earlier_changes"] == [
        {
            "order_date": "2026-06-03",
            "orders_change": -1,
            "revenue_cents_change": -2056,
            "reasons": [{"order_id": api.planted["P2"], "what": "cancelled"}],
        }
    ]


# ---- The report is right, every morning -------------------------------------


def test_the_report_equals_the_answer_key_on_every_morning(api, con):
    for day in june(2, 18):
        run_mornings(api, con, day)
        assert daily_report(con) == api.truth(day), day


def test_every_planted_change_is_explained(api, con):
    explained = {}
    for result in run_mornings(api, con, *june(2, 18)):
        for change in result.answer["earlier_changes"]:
            explained[result.run_date] = (
                change["order_date"],
                change["revenue_cents_change"],
                change["reasons"],
            )
    assert explained == {
        "2026-06-05": ("2026-06-03", -2056, [{"order_id": api.planted["P2"], "what": "cancelled"}]),
        "2026-06-06": ("2026-06-04", -459, [{"order_id": api.planted["P3"], "what": "total changed from $36.94 to $32.35"}]),
        "2026-06-09": ("2026-06-05", -578, [{"order_id": api.planted["P4"], "what": "refund of $5.78"}]),
        "2026-06-10": ("2026-06-08", -1826, [{"order_id": api.planted["P6"], "what": "cancelled"}]),
        "2026-06-14": ("2026-06-12", -1424, [{"order_id": api.planted["P8"], "what": "deleted from the order system"}]),
    }  # fmt: skip


def test_starting_late_or_skipping_mornings_still_gives_the_right_report(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-12", "2026-06-18")
    assert daily_report(con) == api.truth("2026-06-18")


# ---- What milestone 2 left wrong --------------------------------------------


def test_fixed_a_repeated_page_changes_nothing(api, con):
    run_mornings(api, con, *june(8, 11))  # 06-11: the API serves one page twice
    assert daily_report(con) == api.truth("2026-06-11")
    assert con.execute("SELECT count(*) FROM core.orders").fetchone()[0] == 317


def test_fixed_the_late_update_is_caught_by_the_lookback(api, con):
    run_mornings(api, con, *june(8, 10))
    assert con.execute(
        "SELECT status FROM core.orders WHERE order_id = %s", [api.planted["P6"]]
    ).fetchone() == ("cancelled",)


def test_without_a_lookback_the_late_update_is_still_missed(api, con):
    run_mornings(api, con, *june(8, 12), lookback_days=0)
    assert con.execute(
        "SELECT status FROM core.orders WHERE order_id = %s", [api.planted["P6"]]
    ).fetchone() == ("out_for_delivery",)
    assert daily_report(con) != api.truth("2026-06-12")


def test_fixed_a_deleted_order_is_marked_and_not_counted(api, con):
    run_mornings(api, con, *june(12, 14))
    assert con.execute(
        "SELECT is_deleted, deleted_at IS NOT NULL FROM core.orders WHERE order_id = %s",
        [api.planted["P8"]],
    ).fetchone() == (True, True)
    assert daily_report(con) == api.truth("2026-06-14")
    assert (
        con.execute("SELECT count(*) FROM core.orders WHERE is_deleted").fetchone()[0] == 1
    )  # the row is kept


# ---- Failure ----------------------------------------------------------------


def test_a_crash_in_the_middle_saves_nothing_and_the_next_run_recovers(api, con, monkeypatch):
    run_mornings(api, con, "2026-06-08")
    before, watermark = snapshot(con), read_watermark(con)
    raw_before = con.execute("SELECT count(*) FROM raw.orders_landing").fetchone()[0]

    def crash(*args):
        raise RuntimeError("the job crashes after the merge, before the report")

    monkeypatch.setattr(pipeline, "rebuild_days", crash)
    with pytest.raises(RuntimeError):
        run_mornings(api, con, "2026-06-09")
    assert snapshot(con) == before and read_watermark(con) == watermark
    assert con.execute("SELECT count(*) FROM raw.orders_landing").fetchone()[0] == raw_before

    monkeypatch.undo()
    run_mornings(api, con, "2026-06-09")
    assert daily_report(con) == api.truth("2026-06-09")


def test_a_failed_extract_changes_nothing(api, con, waits):
    run_mornings(api, con, "2026-06-08")
    before = snapshot(con)
    api.morning("2026-06-09").fault("error_500", 99)
    with pytest.raises(ExtractFailed):
        pipeline.run(api, con, sleep=waits.append)
    assert snapshot(con) == before
