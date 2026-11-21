"""Small helpers for the milestone 4 lab.

Nothing here is hidden magic. Open this file any time you want to see how
something works. It also holds the finished version of what an earlier
notebook built, so each notebook can stay short.
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
LAB_DATABASE = "arden_m4_lab"  # the lab's own database in the warehouse

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


def read_orders(updated_since: str | None = None) -> tuple[list[dict], int]:
    """Read orders from the API, following the pages. Returns the orders and the number of calls.

    With `updated_since`, only orders changed at or after that time come back.
    It uses the production extract, so it waits and retries when the API says "not now".
    A read of everything can take about ten seconds for that reason.
    """
    from daily_orders.extract import fetch_changed_orders

    extract = fetch_changed_orders(api, updated_since)
    return extract.orders, extract.api_calls


def true_day_totals() -> dict[str, dict]:
    """The answer key: read every order now, and add up each day by hand, as in milestone 1."""
    from daily_orders.extract import fetch_changed_orders

    # Keep one copy of each order: the API can send a page twice.
    api.post(
        "/admin/clock", json={"now": api.get("/admin/clock").json()["now_utc"]}
    )  # a fresh allowance of calls
    orders = {order["order_id"]: order for order in fetch_changed_orders(api, None).orders}
    totals: dict[str, dict] = {}
    for order in orders.values():
        if order["status"] == "cancelled":
            continue
        day = totals.setdefault(business_day(order), {"orders": 0, "revenue_cents": 0})
        day["orders"] += 1
        day["revenue_cents"] += order["total_cents"] - order["refunded_cents"]
    return dict(sorted(totals.items()))


# ---- The warehouse ----------------------------------------------------------


def lab_warehouse(fresh: bool = False):
    """Open the lab's own Postgres database, with this milestone's tables. `fresh=True` empties it first."""
    from daily_orders import warehouse

    try:
        if fresh:
            warehouse.drop_database(LAB_DATABASE)
        return warehouse.connect(LAB_DATABASE)
    except ConnectionError:
        print("The warehouse is not running.")
        print("Open a terminal in the project folder and run:")
        print("  docker compose up -d warehouse")
        raise


def load_only(con, *days: str) -> None:
    """Extract and load these mornings: land the raw rows, and nothing else. No dbt."""
    from daily_orders import pipeline

    for day in days:
        api.post("/admin/clock", json={"morning": day})
        loaded = pipeline.extract_and_load(api, con)
        print(f"Landed {loaded['rows_landed']:>3} raw rows for the morning of {day}.")


def dbt(*args: str, show: bool = True) -> None:
    """Run a dbt command on the lab database, as you would type it in a terminal.

    Afterwards `dbt.passed` is True when the command succeeded.

    dbt("build") is the same as:  dbt build   (typed inside the dbt_project folder)
    """
    import subprocess

    project = MILESTONE_DIR / "dbt_project"
    done = subprocess.run(
        [
            str(Path(sys.executable).parent / "dbt"),
            "--no-use-colors",
            *args,
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "ARDEN_DBT_DATABASE": LAB_DATABASE},
        check=False,
    )
    if show:
        for line in done.stdout.splitlines():
            line = re.sub(
                r"^\d\d:\d\d:\d\d\s*", "", line
            )  # drop the clock at the start of each line
            line = re.sub(r" in \d+\.\d+s", "", line)  # and the timings, which differ on every run
            line = re.sub(r"\[(.+?) in \d+\.\d+s\]", r"[\1]", line)
            quiet = (
                "Running with dbt",
                "Registered adapter",
                "Found ",
                "Concurrency:",
                "Finished running",
                "Unable to do partial",
            )
            if line.strip() and not line.startswith(quiet) and " START " not in line:
                print(line)
    dbt.passed = done.returncode == 0


def run_pipeline(con, *days: str, **options):
    """Run this milestone's production pipeline on each of these mornings. Returns the last result."""
    from daily_orders import pipeline

    result = None
    for day in days:
        api.post("/admin/clock", json={"morning": day})
        result = pipeline.run(api, con, **options)
    return result


def table(con, query: str, params=None):
    """Run a SQL query and show the result as a table."""
    import pandas as pd

    cursor = con.execute(query, params)
    return pd.DataFrame(cursor.fetchall(), columns=[column.name for column in cursor.description])


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
