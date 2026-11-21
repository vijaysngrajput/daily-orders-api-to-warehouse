"""The flow page must never show a number that the code did not produce."""

import re

import pytest

from daily_orders import flow_data


@pytest.fixture(scope="module")
def built():
    """Drive the real Airflow once, for all the tests in this file. It takes a few minutes."""
    try:
        return flow_data.build()
    except (ConnectionError, TimeoutError) as error:
        pytest.exit(str(error), returncode=1)


def test_the_page_holds_exactly_what_the_code_produces_now(built):
    assert flow_data.page_data() == built, (
        "milestone_5_flow.html is out of date. Run: uv run python -m daily_orders.flow_data"
    )


def test_the_drill_on_the_page_is_what_really_happened(built):
    drill = built["recap"]["drill"]
    assert drill["states"] == "running → up_for_retry → running → success"
    assert drill["rows"] > drill["orders"] and drill["right"] == "equals the answer key"
    assert built["recap"]["bad"] == {
        "transform": "failed",
        "publish": "upstream_failed",
        "kept": "kept its last good numbers",
        "after": "equals the answer key",
    }
    assert built["recap"]["replay"]["same"] == "identical" and built["done"] is True


def test_every_stage_of_the_pipeline_is_drawn():
    data = flow_data.page_data()
    assert [s["id"] for s in data["stages"]] == ["api", "extract", "raw", "orders", "report"]
    assert data["schedule"]["state"] == "new" and data["tests"]["state"] == "built"
    assert any(step.get("rerun") for step in data["steps"])  # "Run it again" is one click


def test_every_step_has_a_word_and_a_line_for_each_followed_order():
    data = flow_data.page_data()
    cases = {c["id"] for c in data["cases"]}
    for step in data["steps"]:
        assert step["word"]["term"] and step["word"]["meaning"]
        assert set(step["follow"]) == cases
    assert data["steps"][-1]["title"] == "Nothing is still wrong" and data["steps"][0]["problem"]


def test_the_recap_page_holds_exactly_what_the_code_produces_now(built):
    assert flow_data.recap_page_data() == flow_data.build_recap(built), (
        "milestone_5_recap.html is out of date. Run: uv run python -m daily_orders.flow_data"
    )


def test_the_recap_counts_are_real(request):
    numbers = flow_data.recap_page_data()["numbers"]
    assert numbers["notebooks"] == 7 and numbers["rows"] == 31 and numbers["words"] >= 12
    if not request.config.args or request.config.args == ["tests"]:  # the whole suite is running
        assert numbers["tests"] == request.session.testscollected


def test_each_page_is_one_self_contained_file():
    for page in (flow_data.PAGE, flow_data.RECAP_PAGE):
        html = page.read_text(encoding="utf-8")
        assert not re.search(r'src="https?://', html), page.name
        assert "<script src" not in html and "<link " not in html, page.name
