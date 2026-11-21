"""The tests on the tables: they must fail loudly when the data is wrong."""

import pytest
from psycopg.types.json import Jsonb

from conftest import june, run_mornings, snapshot
from daily_orders import transform, warehouse
from daily_orders.pipeline import run
from daily_orders.report import daily_report
from daily_orders.transform import TransformFailed


def land_changed(con, order_id: int, **changes) -> None:
    """Land a new version of an order, as if the source had sent it. `changes` overwrite its fields."""
    payload = con.execute(
        "SELECT payload FROM raw.orders_landing WHERE (payload->>'order_id')::bigint = %s ORDER BY landing_id DESC LIMIT 1",
        [order_id],
    ).fetchone()[0]
    payload.update(changes, updated_at="2026-06-30T00:00:00Z")
    con.execute(
        "INSERT INTO raw.orders_landing (payload, run_id, page_no, loaded_at) VALUES (%s, 'bad_data', 1, now())",
        [Jsonb(payload)],
    )


def failed(error: TransformFailed) -> list[str]:
    return [name for name, _ in error.result.failures]


@pytest.fixture
def loaded(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    return con


def test_an_unknown_status_fails_the_accepted_values_test(loaded):
    land_changed(loaded, 1130, status="teleported")
    with pytest.raises(TransformFailed) as stopped:
        transform.build(warehouse.database_of(loaded))
    assert any(name.startswith("accepted_values_orders_status") for name in failed(stopped.value))


def test_a_negative_total_fails_the_money_test(loaded):
    land_changed(loaded, 1130, total_cents=-500)
    with pytest.raises(TransformFailed) as stopped:
        transform.build(warehouse.database_of(loaded))
    assert "assert_money_is_never_negative" in failed(stopped.value)


def test_a_total_that_does_not_match_its_items_fails(loaded):
    land_changed(loaded, 1130, total_cents=9999)
    with pytest.raises(TransformFailed) as stopped:
        transform.build(warehouse.database_of(loaded))
    assert failed(stopped.value) == ["assert_order_total_equals_its_items"]


def test_a_missing_city_fails_not_null_and_the_order_is_not_lost(loaded):
    land_changed(loaded, 1130, city="XXX")
    with pytest.raises(TransformFailed) as stopped:
        transform.build(warehouse.database_of(loaded))
    assert any(name.startswith("relationships_orders_city") for name in failed(stopped.value))
    assert (
        loaded.execute("SELECT count(*) FROM core.orders WHERE order_id = 1130").fetchone()[0] == 1
    )  # left join


def test_when_a_test_fails_the_report_keeps_its_last_good_numbers(api, loaded):
    good = daily_report(loaded)
    land_changed(loaded, 1130, status="teleported")
    with pytest.raises(TransformFailed) as stopped:
        run(api.morning("2026-06-10"), loaded, sleep=api.nap)
    assert "daily_orders" in stopped.value.result.skipped  # the report was not rebuilt
    assert daily_report(loaded) == good
    assert (
        loaded.execute(
            "SELECT tests_failed FROM ops.load_runs WHERE run_id = '2026-06-10_1'"
        ).fetchone()[0]
        >= 1
    )

    loaded.execute(
        "DELETE FROM raw.orders_landing WHERE run_id = 'bad_data'"
    )  # the source is fixed
    run(api.morning("2026-06-10"), loaded, sleep=api.nap)
    assert daily_report(loaded) == api.truth("2026-06-10")


def test_the_same_order_landed_many_times_is_one_row(loaded):
    loaded.execute(
        "INSERT INTO raw.orders_landing (payload, run_id, page_no, loaded_at) SELECT payload, 'again', 1, now() FROM raw.orders_landing"
    )
    before = loaded.execute("SELECT count(*) FROM core.orders").fetchone()[0]
    transform.build(warehouse.database_of(loaded))
    assert loaded.execute("SELECT count(*) FROM core.orders").fetchone()[0] == before == 251


def test_a_report_that_disagrees_with_its_orders_is_caught(loaded):
    loaded.execute(
        "UPDATE marts.daily_orders SET revenue_cents = revenue_cents + 100 WHERE order_date = '2026-06-05' AND city = 'NGT'"
    )
    result = transform.run_dbt(
        warehouse.database_of(loaded), "test", "--select", "assert_report_equals_its_orders"
    )
    assert not result.success and result.tests_failed == 1


# ---- The incremental model ---------------------------------------------------


def test_the_incremental_report_equals_a_full_rebuild(api, con):
    run_mornings(api, con, *june(2, 18))
    incremental = snapshot(con)[2]
    transform.build(warehouse.database_of(con), full_refresh=True)
    assert snapshot(con)[2] == incremental


def test_an_incremental_run_touches_only_recent_days(api, con):
    run_mornings(api, con, *june(2, 16))
    database = warehouse.database_of(con)
    con.execute(
        "UPDATE marts.daily_orders SET orders = 999 WHERE order_date IN ('2026-06-02', '2026-06-12')"
    )
    transform.run_dbt(database, "run", "--select", "daily_orders")
    marked = [
        row[0].isoformat()
        for row in con.execute(
            "SELECT DISTINCT order_date FROM marts.daily_orders WHERE orders = 999"
        )
    ]
    assert marked == [
        "2026-06-02"
    ]  # 12 June is inside the 7-day window and was rebuilt; 2 June was not touched
    transform.run_dbt(database, "run", "--select", "daily_orders", "--full-refresh")
    assert daily_report(con) == api.truth("2026-06-16")


# ---- Freshness ----------------------------------------------------------------


def test_fresh_data_passes_and_old_data_fails_the_freshness_check(loaded):
    database = warehouse.database_of(loaded)
    assert transform.source_freshness(database).success
    loaded.execute("UPDATE raw.orders_landing SET loaded_at = now() - interval '3 days'")
    stale = transform.source_freshness(database)
    assert not stale.success and stale.statuses == {"orders_landing": "error"}


def test_after_a_failed_run_the_next_good_run_still_explains_every_change(api, con):
    """The late cancellation lands during a run that fails. The next good run must still name it."""
    run_mornings(api, con, "2026-06-08", "2026-06-09")
    land_changed(con, 1130, status="teleported")
    with pytest.raises(TransformFailed):
        run(api.morning("2026-06-10"), con, sleep=api.nap)
    con.execute("DELETE FROM raw.orders_landing WHERE run_id = 'bad_data'")
    answer = run(api.morning("2026-06-10"), con, sleep=api.nap).answer
    assert [
        (c["order_date"], c["revenue_cents_change"], c["reasons"][0]["what"])
        for c in answer["earlier_changes"]
    ] == [("2026-06-08", -1826, "cancelled")]
