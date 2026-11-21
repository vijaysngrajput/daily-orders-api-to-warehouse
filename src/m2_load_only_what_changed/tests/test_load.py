from datetime import UTC, date, datetime

import psycopg
import pytest

from daily_orders.extract import fetch_changed_orders
from daily_orders.load import land, next_run_id, read_watermark, to_api_text

LOADED = datetime(2030, 1, 1, tzinfo=UTC)  # far from any order time, on purpose
RUN = date(2026, 6, 8)


def land_morning(api, con, run_id="2026-06-08_1"):
    extract = fetch_changed_orders(api.morning("2026-06-08"), None)
    return extract, land(
        con, extract.orders, extract.pages, run_id, RUN, LOADED, extract.api_calls, 0
    )


def test_orders_land_exactly_as_received_with_load_metadata(api, con):
    extract, _ = land_morning(api, con)
    rows = con.execute(
        "SELECT payload, run_id, page_no, loaded_at FROM raw.orders_landing ORDER BY landing_id"
    ).fetchall()
    assert [r[0] for r in rows] == extract.orders
    assert {r[1] for r in rows} == {"2026-06-08_1"} and {r[3] for r in rows} == {LOADED}
    assert [r[2] for r in rows] == extract.pages


def test_the_watermark_is_the_newest_updated_at_not_the_time_of_the_run(api, con):
    assert read_watermark(con) is None
    extract, after = land_morning(api, con)
    assert (
        to_api_text(after) == max(o["updated_at"] for o in extract.orders) == "2026-06-08T03:48:53Z"
    )
    assert read_watermark(con) == after != LOADED


def test_a_load_with_no_rows_keeps_the_watermark(api, con):
    _, first = land_morning(api, con)
    assert land(con, [], [], "2026-06-08_2", RUN, LOADED, 1, 0) == first
    assert read_watermark(con) == first


def test_the_watermark_never_moves_back(api, con):
    extract, first = land_morning(api, con)
    land(con, extract.orders[:5], [1] * 5, "2026-06-08_2", RUN, LOADED, 1, 0)
    assert read_watermark(con) == first


def test_rows_and_watermark_are_saved_together_or_not_at_all(api, con):
    extract = fetch_changed_orders(api.morning("2026-06-08"), None)
    half = len(extract.orders) // 2
    land(con, extract.orders[:half], extract.pages[:half], "2026-06-08_1", RUN, LOADED, 5, 0)
    before = read_watermark(con)
    with pytest.raises(
        psycopg.errors.UniqueViolation
    ):  # the same run_id again: the last step fails
        land(con, extract.orders[half:], extract.pages[half:], "2026-06-08_1", RUN, LOADED, 5, 0)
    assert con.execute("SELECT count(*) FROM raw.orders_landing").fetchone()[0] == half
    assert read_watermark(con) == before


def test_run_ids_count_the_runs_of_a_morning(api, con):
    assert next_run_id(con, RUN) == "2026-06-08_1"
    land_morning(api, con)
    assert next_run_id(con, RUN) == "2026-06-08_2"
