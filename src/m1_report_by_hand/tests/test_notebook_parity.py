"""The answer worked out by hand in notebook 3 equals the production answer."""

import json

from daily_orders.config import MILESTONE_DIR
from daily_orders.pipeline import run

KEY = json.loads((MILESTONE_DIR / "notebooks" / "results" / "answer_key.json").read_text())


def test_the_program_gives_the_hand_worked_answer(api, warehouse):
    answer = run(api.morning(KEY["run_date"]), warehouse).answer
    for part in ("yesterday", "day_before"):
        assert answer[part]["order_date"] == KEY[part]["order_date"]
        assert answer[part]["orders"] == KEY[part]["orders"]
        assert answer[part]["revenue_cents"] == KEY[part]["revenue_cents"]
        for city, row in KEY[part]["by_city"].items():
            assert answer[part]["by_city"][city]["orders"] == row["orders"]
            assert answer[part]["by_city"][city]["revenue_cents"] == row["revenue_cents"]
    assert answer["change"] == KEY["change"]


def test_the_notebooks_were_saved_with_their_outputs():
    notebooks = sorted((MILESTONE_DIR / "notebooks").glob("0*.ipynb"))
    assert len(notebooks) == 7
    for path in notebooks:
        cells = json.loads(path.read_text(encoding="utf-8"))["cells"]
        code = [c for c in cells if c["cell_type"] == "code"]
        assert all(c["execution_count"] for c in code), path.name
        assert not any(o.get("output_type") == "error" for c in code for o in c["outputs"]), (
            path.name
        )
