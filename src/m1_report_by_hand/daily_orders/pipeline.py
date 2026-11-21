"""The whole pipeline for one morning: extract, transform, load, report."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from daily_orders.config import API_URL, WAREHOUSE_PATH
from daily_orders.extract import RateLimited, fetch_all_orders, read_run_date
from daily_orders.load import connect, replace_orders
from daily_orders.report import format_answer, manager_answer
from daily_orders.transform import to_orders_table


@dataclass
class RunResult:
    run_date: str
    orders_loaded: int
    api_calls: int
    answer: dict


def run(client: httpx.Client, warehouse: Path | str = WAREHOUSE_PATH) -> RunResult:
    """Run the pipeline once, for the morning on the order system's clock."""
    run_date = read_run_date(client)
    extract = fetch_all_orders(client)  # stops here if the API refuses: nothing is loaded
    table = to_orders_table(extract.orders, datetime.now(timezone.utc), run_date)
    con = connect(warehouse)
    try:
        replace_orders(con, table, run_date, extract.api_calls)
        answer = manager_answer(con)
    finally:
        con.close()
    return RunResult(run_date.isoformat(), len(table), extract.api_calls, answer)


def main() -> int:
    try:
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            result = run(client)
    except httpx.ConnectError:
        print(f"Cannot reach the order system at {API_URL}.")
        print("Start it in another terminal, from the project folder:")
        print("  uv run python -m order_system")
        return 1
    except RateLimited as error:
        print(f"The load failed: {error}")
        return 1
    print(f"Loaded {result.orders_loaded} orders in {result.api_calls} API calls.\n")
    print(format_answer(result.answer))
    return 0
