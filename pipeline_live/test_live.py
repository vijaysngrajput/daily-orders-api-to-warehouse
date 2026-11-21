"""The live page's server: it must run the real pipeline and tell the truth about it."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402


@pytest.fixture(scope="module")
def page():
    try:
        return TestClient(server.create_live_app())
    except SystemExit as error:
        pytest.exit(str(error), returncode=1)


def run(page, morning):
    return page.post("/api/run", json={"morning": morning}).json()


def test_a_morning_runs_through_all_five_stages(page):
    page.post("/api/reset")
    result = run(page, "2026-06-08")
    assert result["ok"] and [s["stage"] for s in result["steps"]] == [
        "api",
        "extract",
        "raw",
        "orders",
        "report",
    ]
    assert "30 orders, $807.11" in result["steps"][-1]["text"]
    assert result["state"]["report"]["all_right"] is True


def test_the_next_morning_explains_the_refund(page):
    result = run(page, "2026-06-09")
    assert "because order 1130: refund of $5.78" in result["steps"][-1]["text"]
    assert result["state"]["report"]["all_right"] is True


def test_the_same_morning_again_changes_nothing(page):
    result = run(page, "2026-06-09")
    orders = next(s for s in result["steps"] if s["stage"] == "orders")
    assert (
        "0 are new and 0 changed" in orders["text"]
        and result["state"]["report"]["all_right"] is True
    )


def test_a_broken_api_and_a_repeated_page_do_no_harm(page):
    page.post("/api/break", json={"kind": "api_down"})
    page.post("/api/break", json={"kind": "page_twice"})
    result = run(page, "2026-06-10")
    extract = next(s for s in result["steps"] if s["stage"] == "extract")
    assert "3 retries" in extract["count"] and result["ok"]
    assert "a page arrived twice" in next(s for s in result["steps"] if s["stage"] == "raw")["text"]
    assert result["state"]["report"]["all_right"] is True and result["state"]["planned"] == []


def test_a_bad_status_stops_the_report_until_the_source_is_fixed(page):
    page.post("/api/break", json={"kind": "bad_status"})
    result = run(page, "2026-06-11")
    assert not result["ok"] and result["bad_status"]
    assert "accepted_values_orders_status" in result["steps"][-2]["text"]
    assert "still shows its last good numbers" in result["steps"][-1]["text"]
    page.post("/api/fix")
    fixed = run(page, "2026-06-11")
    assert fixed["ok"] and fixed["state"]["report"]["all_right"] is True


def test_jumping_ahead_and_back_still_equals_the_answer_key(page):
    assert run(page, "2026-06-18")["state"]["report"]["all_right"] is True
    assert run(page, "2026-06-14")["ok"]


def test_the_page_is_one_self_contained_file():
    html = (Path(server.HERE) / "index.html").read_text(encoding="utf-8")
    assert "<script src" not in html and "<link " not in html and 'src="http' not in html


def test_every_run_says_in_plain_words_what_happened(page):
    """A learner must never have to guess whether a button did anything."""
    page.post("/api/reset")
    first = run(page, "2026-06-08")
    assert first["outcome"][0]["you"] == "You ran the morning of Mon 06-08."
    refund = run(page, "2026-06-09")
    assert refund["outcome"][0]["you"].startswith("An earlier day changed: Fri 06-05")
    again = run(page, "2026-06-09")
    assert again["outcome"][0]["you"] == "You ran the same morning a second time."

    page.post("/api/break", json={"kind": "api_down"})
    survived = run(page, "2026-06-10")
    told = [o for o in survived["outcome"] if o["you"] == "You took the API down for 3 calls."][0]
    assert survived["ok"] and "did not break" in told["it"] and told["tone"] == "good"

    page.post("/api/break", json={"kind": "bad_status"})
    stopped = run(page, "2026-06-11")
    assert not stopped["ok"] and stopped["outcome"][0]["tone"] == "stopped"
    assert "on purpose" in stopped["outcome"][0]["it"]
    assert all(
        o["from"].startswith("Milestone") or o["from"] == "The whole pipeline"
        for r in (first, refund, again, survived, stopped)
        for o in r["outcome"]
    )


def test_the_page_explains_what_it_is_for():
    html = (Path(server.HERE) / "index.html").read_text(encoding="utf-8")
    assert "What is this, and why run it?" in html and "Try these four things, in order" in html


def test_a_normal_thursday_runs_and_only_the_bad_row_stops_it(page):
    """The day does not break the pipeline. The bad row does."""
    page.post("/api/reset")
    for morning in ("2026-06-08", "2026-06-09", "2026-06-10"):
        run(page, morning)
    normal = run(page, "2026-06-11")
    assert normal["ok"] and normal["outcome"][0]["you"] == "You ran the morning of Thu 06-11."
    assert page.post("/api/break", json={"kind": "bad_status"}).json()["planned"] == ["bad_status"]
    stopped = run(page, "2026-06-11")
    assert not stopped["ok"] and stopped["outcome"][0]["tone"] == "stopped"
    assert stopped["state"]["planned"] == []  # it applies to one run only


def test_each_experiment_is_one_button():
    html = (Path(server.HERE) / "index.html").read_text(encoding="utf-8")
    assert html.count('class="btn try"') == 5
    assert 'data-morning="2026-06-11" data-break="bad_status"' in html


def test_after_fix_the_source_the_next_run_says_so_and_the_report_is_right(page):
    page.post("/api/reset")
    for morning in ("2026-06-08", "2026-06-09", "2026-06-10"):
        run(page, morning)
    page.post("/api/break", json={"kind": "bad_status"})
    assert not run(page, "2026-06-11")["ok"]
    page.post("/api/fix")
    fixed = run(page, "2026-06-11")
    assert (
        fixed["ok"]
        and fixed["outcome"][0]["you"] == "You fixed the source, and the morning ran again."
    )
    assert fixed["state"]["report"]["all_right"] is True
    assert (
        run(page, "2026-06-12")["outcome"][0]["you"]
        != "You fixed the source, and the morning ran again."
    )


def test_the_guide_ticks_what_is_done_and_points_at_what_is_next():
    html = (Path(server.HERE) / "index.html").read_text(encoding="utf-8")
    for needed in (
        "function updateGuide()",
        "function afterExperiment(result)",
        "moveTo('toolbar')",
        "moveTo('guide')",
        ".btn.try.done::before",
        "@keyframes hint",
    ):
        assert needed in html, needed
