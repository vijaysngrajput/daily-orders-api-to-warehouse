"""The flow page must never show a number that the code did not produce."""

import re

from daily_orders import flow_data
from daily_orders.pipeline import run


def test_the_page_holds_exactly_what_the_code_produces_now():
    assert flow_data.page_data() == flow_data.build(), (
        "milestone_2_flow.html is out of date. Run: uv run python -m daily_orders.flow_data"
    )


def test_the_page_shows_the_same_numbers_as_a_fresh_pipeline_run(api, con):
    first = run(api.morning("2026-06-08"), con)
    second = run(api.morning("2026-06-09"), con)
    data = flow_data.page_data()
    step = {s["id"]: s for s in data["steps"]}
    assert step["first_load"]["stages"]["raw"]["count"] == f"{first.rows_landed} rows"
    assert f"gets {second.rows_landed}." in step["incremental"]["text"]
    assert data["recap"]["compare"]["watermark"] == first.watermark_after


def test_every_stage_of_the_pipeline_is_drawn():
    data = flow_data.page_data()
    assert [s["id"] for s in data["stages"]] == ["api", "extract", "raw", "orders", "report"]
    assert data["schedule"]["state"] == "by_hand" and data["tests"]["state"] == "not_yet"
    assert all(not s.get("ghost") for s in data["stages"])


def test_every_step_has_a_word_and_a_line_for_each_followed_order():
    data = flow_data.page_data()
    cases = {c["id"] for c in data["cases"]}
    for step in data["steps"]:
        assert step["word"]["term"] and step["word"]["meaning"]
        assert set(step["follow"]) == cases
    assert data["steps"][-1]["problem"] and "Still wrong" in data["steps"][-1]["title"]


def test_the_recap_page_holds_exactly_what_the_code_produces_now():
    assert flow_data.recap_page_data() == flow_data.build_recap(), (
        "milestone_2_recap.html is out of date. Run: uv run python -m daily_orders.flow_data"
    )


def test_the_recap_counts_are_real(request):
    numbers = flow_data.recap_page_data()["numbers"]
    assert numbers["notebooks"] == 8 and numbers["rows"] == 29 and numbers["words"] >= 12
    if not request.config.args or request.config.args == ["tests"]:  # the whole suite is running
        assert numbers["tests"] == request.session.testscollected


def test_each_page_is_one_self_contained_file():
    for page in (flow_data.PAGE, flow_data.RECAP_PAGE):
        html = page.read_text(encoding="utf-8")
        assert not re.search(r'src="https?://', html), page.name
        assert "<script src" not in html and "<link " not in html, page.name
