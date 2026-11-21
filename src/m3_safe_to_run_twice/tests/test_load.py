"""The MERGE: one order, one row, however often it arrives."""

from datetime import UTC, datetime

import psycopg
import pytest

from conftest import run_mornings
from daily_orders.extract import fetch_changed_orders
from daily_orders.load import MERGE_SQL, STAGE_SQL, land, merge_orders, soft_delete

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def counts(con):
    return con.execute(
        "SELECT (SELECT count(*) FROM raw.orders_landing), (SELECT count(*) FROM core.orders)"
    ).fetchone()


def test_a_changed_order_is_one_row_with_its_latest_state(api, con):
    run_mornings(api, con, "2026-06-08", "2026-06-09", lookback_days=0)
    assert counts(con) == (255, 251)  # the raw table keeps both versions; the clean table keeps one
    row = con.execute(
        "SELECT total_cents, refunded_cents, first_run_id, last_run_id FROM core.orders WHERE order_id = %s",
        [api.planted["P4"]],
    ).fetchone()
    assert row == (1475, 578, "2026-06-08_1", "2026-06-09_1")


def test_an_order_that_arrives_twice_in_one_run_is_merged_once(api, con):
    """The API serves a page twice on 2026-06-11. Staging keeps one row per order."""
    run_mornings(api, con, *["2026-06-08", "2026-06-09", "2026-06-10"], lookback_days=0)
    result = run_mornings(api, con, "2026-06-11", lookback_days=0)[0]
    landed, different = con.execute(
        "SELECT count(*), count(DISTINCT payload->>'order_id') FROM raw.orders_landing WHERE run_id = %s",
        [result.run_id],
    ).fetchone()
    assert (landed, different) == (49, 37)
    assert result.orders_inserted + result.orders_updated == 37
    assert counts(con)[1] == 317


def test_without_the_dedupe_step_merge_refuses_the_repeated_page(api, con):
    extract = fetch_changed_orders(api.morning("2026-06-11"), "2026-06-10T04:11:39Z")
    refused = (
        psycopg.errors.UniqueViolation,
        psycopg.errors.CardinalityViolation,
    )  # new order, or known order
    with pytest.raises(refused), con.transaction():
        land(con, extract.orders, extract.pages, "r1", NOW)
        con.execute(STAGE_SQL.replace("SELECT DISTINCT ON (order_id) *", "SELECT *"), ["r1"])
        con.execute(MERGE_SQL, {"run_id": "r1"})
    assert counts(con) == (0, 0)  # the failed transaction left nothing behind


def test_an_older_version_never_overwrites_a_newer_one(api, con):
    new = fetch_changed_orders(api.morning("2026-06-09"), None).orders
    old = fetch_changed_orders(api.morning("2026-06-08"), None).orders
    with con.transaction():
        land(con, new, [1] * len(new), "newer", NOW)
        merge_orders(con, "newer")
    with con.transaction():
        land(con, old, [1] * len(old), "older", NOW)
        inserted, updated, _ = merge_orders(con, "older")
    assert (inserted, updated) == (0, 0)
    assert con.execute(
        "SELECT refunded_cents FROM core.orders WHERE order_id = %s", [api.planted["P4"]]
    ).fetchone() == (578,)


def test_a_soft_delete_keeps_the_row_and_is_safe_to_repeat(api, con):
    run_mornings(api, con, "2026-06-13")
    delete = [{"order_id": api.planted["P8"], "changed_at": "2026-06-13T16:00:00Z"}]
    with con.transaction():
        assert len(soft_delete(con, delete, "r1")) == 1
    with con.transaction():
        assert soft_delete(con, delete, "r2") == []  # already marked: nothing to do
    assert con.execute(
        "SELECT is_deleted, last_run_id, total_cents FROM core.orders WHERE order_id = %s",
        [api.planted["P8"]],
    ).fetchone() == (True, "r1", 1424)
