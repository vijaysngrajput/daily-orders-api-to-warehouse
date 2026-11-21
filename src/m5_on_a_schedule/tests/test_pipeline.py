"""The pipeline, as steps a scheduler can run. Everything earlier milestones proved must still hold."""

import pytest

from conftest import june, run_mornings, snapshot
from daily_orders import pipeline
from daily_orders.extract import ExtractFailed
from daily_orders.report import daily_report, day_totals


def test_python_only_lands_raw_and_dbt_builds_the_rest(api, con):
    result = run_mornings(api, con, "2026-06-08")[0]
    assert (result.rows_landed, result.models_built) == (226, 7)  # 6 models and 1 seed
    assert result.tests_passed > 25
    built = con.execute(
        "SELECT table_schema, table_name, table_type FROM information_schema.tables "
        "WHERE table_schema IN ('staging', 'core', 'marts') ORDER BY 1, 2"
    ).fetchall()
    assert built == [
        ("core", "cities", "BASE TABLE"),
        ("core", "order_items", "BASE TABLE"),
        ("core", "orders", "BASE TABLE"),
        ("marts", "daily_orders", "BASE TABLE"),
        ("staging", "stg_order_deletes", "VIEW"),
        ("staging", "stg_order_items", "VIEW"),
        ("staging", "stg_orders", "VIEW"),
    ]


def test_the_report_equals_the_answer_key_on_every_morning(api, con):
    for day in june(2, 18):
        run_mornings(api, con, day)
        assert daily_report(con) == api.truth(day), day


def test_the_managers_numbers_for_the_morning_of_june_8(api, con):
    answer = run_mornings(api, con, "2026-06-08")[0].answer
    assert (answer["yesterday"]["orders"], answer["yesterday"]["revenue_cents"]) == (30, 80711)
    assert (answer["day_before"]["orders"], answer["day_before"]["revenue_cents"]) == (33, 78671)


# ---- The three properties, again --------------------------------------------


def test_running_the_same_morning_twice_changes_nothing(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    once = snapshot(con)
    again = run_mornings(api, con, "2026-06-09", "2026-06-09")[-1]
    assert snapshot(con) == once and again.answer["earlier_changes"] == []


def test_an_order_cancelled_later_lowers_the_old_days_total(api, con):
    run_mornings(api, con, *june(2, 4))
    before = day_totals(daily_report(con))["2026-06-03"]
    result = run_mornings(api, con, "2026-06-05")[0]
    after = day_totals(daily_report(con))["2026-06-03"]
    assert (after["orders"], after["revenue_cents"]) == (
        before["orders"] - 1,
        before["revenue_cents"] - 2056,
    )
    assert result.answer["earlier_changes"][0]["reasons"] == [
        {"order_id": api.planted["P2"], "what": "cancelled"}
    ]


def test_every_planted_change_is_explained(api, con):
    explained = {}
    for result in run_mornings(api, con, *june(2, 18)):
        for change in result.answer["earlier_changes"]:
            explained[result.run_date] = (
                change["order_date"],
                change["revenue_cents_change"],
                change["reasons"][0]["what"],
            )
    assert explained == {
        "2026-06-05": ("2026-06-03", -2056, "cancelled"),
        "2026-06-06": ("2026-06-04", -459, "total changed from $36.94 to $32.35"),
        "2026-06-09": ("2026-06-05", -578, "refund of $5.78"),
        "2026-06-10": ("2026-06-08", -1826, "cancelled"),
        "2026-06-14": ("2026-06-12", -1424, "deleted from the order system"),
    }


def test_the_repeated_page_the_late_update_and_the_delete_are_all_handled(api, con):
    run_mornings(api, con, *june(8, 14))
    assert daily_report(con) == api.truth("2026-06-14")
    row = con.execute(
        "SELECT status FROM core.orders WHERE order_id = %s", [api.planted["P6"]]
    ).fetchone()
    assert row == ("cancelled",)
    assert con.execute(
        "SELECT is_deleted FROM core.orders WHERE order_id = %s", [api.planted["P8"]]
    ).fetchone() == (True,)


def test_a_failed_extract_changes_nothing(api, con, waits):
    run_mornings(api, con, "2026-06-08")
    before = snapshot(con)
    api.morning("2026-06-09").fault("error_500", 99)
    with pytest.raises(ExtractFailed):
        pipeline.run(api, con, sleep=waits.append)
    assert snapshot(con) == before


# ---- What ELT gives that ETL did not -----------------------------------------


def test_a_new_question_is_answered_from_the_warehouse(api, con):
    """Milestone 1 dropped the items before the load. Now they are a table."""
    run_mornings(api, con, "2026-06-08")
    top = con.execute(
        """
        SELECT i.product_name, sum(i.quantity) AS units
        FROM core.order_items AS i JOIN core.orders AS o USING (order_id)
        WHERE o.order_date = '2026-06-07' AND o.status <> 'cancelled' AND i.item_status = 'ok'
        GROUP BY 1 ORDER BY units DESC, 1 LIMIT 2
        """
    ).fetchall()
    assert top == [("Basmati rice 1kg", 12), ("Butter 250g", 12)]


def test_the_clean_tables_can_be_rebuilt_from_raw_alone(api, con):
    """Drop everything dbt built. One build brings it all back, with no call to the source."""
    from daily_orders import transform, warehouse

    run_mornings(api, con, *june(8, 12))
    before = snapshot(con)
    for schema in warehouse.DBT_SCHEMAS:
        con.execute(f"DROP SCHEMA {schema} CASCADE")
    transform.build(warehouse.database_of(con))
    assert snapshot(con) == before


# ---- The steps a scheduler runs ------------------------------------------------


def test_the_two_steps_one_after_the_other_equal_the_whole_run(api, con):
    api.morning("2026-06-08")
    loaded = pipeline.extract_and_load(api, con, sleep=api.nap)
    assert (loaded["run_id"], loaded["rows_landed"]) == ("2026-06-08_1", 226)
    result = pipeline.transform_and_test(con)
    assert result.run_id == "2026-06-08_1" and daily_report(con) == api.truth("2026-06-08")
    assert "Sun 2026-06-07    30 orders      $807.11" in pipeline.report_text(con)


def test_a_step_can_be_retried_without_harm(api, con):
    """A scheduler may run a failed task again. Each step must be safe to repeat."""
    run_mornings(api, con, "2026-06-08")
    api.morning("2026-06-09")
    pipeline.extract_and_load(api, con, sleep=api.nap)
    pipeline.extract_and_load(api, con, sleep=api.nap)  # the task is retried
    pipeline.transform_and_test(con)
    once = snapshot(con)
    pipeline.transform_and_test(con)  # and this one too
    assert snapshot(con) == once and daily_report(con) == api.truth("2026-06-09")


def test_a_run_for_the_wrong_morning_stops_before_it_loads(api, con):
    from datetime import date

    api.morning("2026-06-09")
    with pytest.raises(pipeline.WrongDay, match="2026-06-08"):
        pipeline.extract_and_load(api, con, expect_run_date=date(2026, 6, 8))
    assert con.execute("SELECT count(*) FROM raw.orders_landing").fetchone()[0] == 0


def test_the_report_step_shows_the_last_run_that_passed(api, con):
    assert "no report" in pipeline.report_text(con)
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    assert "Report for the morning of Tue 2026-06-09" in pipeline.report_text(con)
