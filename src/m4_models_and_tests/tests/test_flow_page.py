"""The flow page must never show a number that the code did not produce."""

import re

from daily_orders import flow_data


def test_the_page_holds_exactly_what_the_code_produces_now():
    assert flow_data.page_data() == flow_data.build(), (
        "milestone_4_flow.html is out of date. Run: uv run python -m daily_orders.flow_data"
    )


def test_the_page_shows_the_same_numbers_as_a_fresh_pipeline_run(api, con):
    from conftest import run_mornings

    first, second = run_mornings(api, con, "2026-06-08", "2026-06-09")
    recap = flow_data.page_data()["recap"]
    assert recap["load"]["read"] == second.rows_landed
    assert (recap["dbt"]["models"], recap["dbt"]["tests"]) == (
        second.models_built,
        second.tests_passed,
    )
    assert (recap["monday"]["orders"], recap["monday"]["money"]) == (30, "$807.11")
    assert (
        recap["bad"]["kept"] == "kept its last good numbers"
        and recap["rebuild"]["same"] == "identical"
    )


def test_every_stage_of_the_pipeline_is_drawn():
    data = flow_data.page_data()
    assert [s["id"] for s in data["stages"]] == ["api", "extract", "raw", "orders", "report"]
    assert data["schedule"]["state"] == "by_hand" and data["tests"]["state"] == "new"
    assert any(step.get("rerun") for step in data["steps"])  # "Run it again" is one click


def test_every_step_has_a_word_and_a_line_for_each_followed_order():
    data = flow_data.page_data()
    cases = {c["id"] for c in data["cases"]}
    for step in data["steps"]:
        assert step["word"]["term"] and step["word"]["meaning"]
        assert set(step["follow"]) == cases
    assert data["steps"][-1]["problem"] and "Still wrong" in data["steps"][-1]["title"]


def test_the_recap_page_holds_exactly_what_the_code_produces_now():
    assert flow_data.recap_page_data() == flow_data.build_recap(), (
        "milestone_4_recap.html is out of date. Run: uv run python -m daily_orders.flow_data"
    )


def test_the_recap_counts_are_real(request):
    numbers = flow_data.recap_page_data()["numbers"]
    assert numbers["notebooks"] == 7 and numbers["rows"] == 31 and numbers["words"] >= 10
    if not request.config.args or request.config.args == ["tests"]:  # the whole suite is running
        assert numbers["tests"] == request.session.testscollected


def test_each_page_is_one_self_contained_file():
    for page in (flow_data.PAGE, flow_data.RECAP_PAGE):
        html = page.read_text(encoding="utf-8")
        assert not re.search(r'src="https?://', html), page.name
        assert "<script src" not in html and "<link " not in html, page.name
