"""Small helpers for the milestone 1 lab.

Nothing here is hidden magic. Open this file any time you want to see how
something works. From notebook 3 on, it also holds the finished version of
what an earlier notebook built, so each notebook can stay short.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from IPython.display import Markdown, display

LAB_DIR = Path(__file__).resolve().parent
MILESTONE_DIR = LAB_DIR.parent
RESULTS_DIR = LAB_DIR / "results"
LAB_WAREHOUSE = LAB_DIR / "lab_warehouse.duckdb"

# Lets a notebook write `from daily_orders import ...` (the production code).
if str(MILESTONE_DIR) not in sys.path:
    sys.path.insert(0, str(MILESTONE_DIR))

API_URL = os.environ.get("ARDEN_API_URL", "http://127.0.0.1:8010")
ARDEN = ZoneInfo("America/Chicago")  # the clock in Arden's three cities
CHAPTERS = "https://www.d8loop.com/modules/knowledge-corner/data-pipeline/"

api = httpx.Client(base_url=API_URL, timeout=30)


# ---- The order system -------------------------------------------------------


def check_api() -> None:
    """Say whether the order system is running."""
    try:
        clock = api.get("/admin/clock").json()
    except httpx.ConnectError:
        print("The order system is not running.")
        print("Open a terminal in the project folder and run:")
        print("  uv run python -m order_system")
        return
    print(f"The order system is running. Its clock says {clock['now_local']} in Arden.")


def set_morning(day: str) -> None:
    """Move the order system's clock to 07:00 on `day`, the time of the daily run."""
    clock = api.post("/admin/clock", json={"morning": day}).json()
    name = datetime.fromisoformat(clock["now_local"]).strftime("%A")
    print(f"It is now {name} {clock['now_local']} in Arden.")


def get_all_orders() -> tuple[list[dict], int]:
    """Read every order. The finished version of the loop you write in notebook 2.

    Returns the orders, and the number of API calls it took.
    """
    orders, cursor, calls = [], None, 0
    while True:
        answer = api.get("/orders", params={"cursor": cursor} if cursor else None)
        calls += 1
        answer.raise_for_status()
        body = answer.json()
        orders += body["data"]
        cursor = body["next_cursor"]
        if cursor is None:
            return orders, calls


def full_load() -> tuple[list[dict], int, bool]:
    """Read every order, and say if the API refused.

    Returns the orders read so far, the number of calls, and True when a call
    was refused with status 429 (too many requests).
    """
    orders, cursor, calls = [], None, 0
    while True:
        answer = api.get("/orders", params={"cursor": cursor} if cursor else None)
        calls += 1
        if answer.status_code == 429:
            return orders, calls, True
        body = answer.json()
        orders += body["data"]
        cursor = body["next_cursor"]
        if cursor is None:
            return orders, calls, False


# ---- Time and money ---------------------------------------------------------


def arden_time(utc_text: str) -> datetime:
    """A UTC time from the API, shown on Arden's clock."""
    return datetime.fromisoformat(utc_text.replace("Z", "+00:00")).astimezone(ARDEN)


def business_day(order: dict) -> str:
    """The day an order belongs to: the date it was placed, on Arden's clock."""
    return arden_time(order["placed_at"]).date().isoformat()


def money(cents: int) -> str:
    """Cents as money: 80711 -> $807.11"""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


# ---- Showing things ---------------------------------------------------------


def show_doc(file_name: str, heading: str) -> None:
    """Show one section of a design document, right here in the notebook."""
    text = (MILESTONE_DIR / "docs" / file_name).read_text(encoding="utf-8")
    pattern = rf"^(#+) {re.escape(heading)}\n(.*?)(?=^#{{1,3}} |\Z)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    if match is None:
        raise ValueError(f"No section called {heading!r} in {file_name}")
    section = re.sub(
        r"```mermaid.*?```",
        f"*(There is a diagram here. Open `docs/{file_name}` to see it.)*",
        match.group(0).strip(),
        flags=re.DOTALL,
    )
    quoted = "\n".join("> " + line for line in section.splitlines())
    display(Markdown(f"**From `docs/{file_name}`**\n\n{quoted}"))


def bars(values: dict[str, float], unit: str = "", width: int = 40) -> None:
    """A small bar chart made of text."""
    biggest = max(values.values()) or 1
    longest = max(len(str(k)) for k in values)
    for label, value in values.items():
        bar = "█" * max(1, round(width * value / biggest)) if value else ""
        print(f"{str(label):<{longest}}  {bar} {value:,}{unit}")


# ---- Results passed from one notebook to the next ---------------------------


def save_result(name: str, value) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(f"Saved results/{name}.json")


def load_result(name: str):
    path = RESULTS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"results/{name}.json is missing. Run the earlier notebooks first.")
    return json.loads(path.read_text(encoding="utf-8"))
