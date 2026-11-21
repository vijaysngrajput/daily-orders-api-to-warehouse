"""Extract: read every order out of the order system.

This is a full load. It reads every row on every run.
"""

from dataclasses import dataclass
from datetime import date

import httpx


class RateLimited(Exception):
    """The API refused a call because too many came too fast (HTTP 429)."""


@dataclass
class Extract:
    orders: list[dict]  # every order, exactly as the API sent it
    api_calls: int  # how many pages were asked for


def read_run_date(client: httpx.Client) -> date:
    """The morning this run is for: today's date on the order system's clock."""
    answer = client.get("/admin/clock")
    answer.raise_for_status()
    return date.fromisoformat(answer.json()["now_local"][:10])


def fetch_all_orders(client: httpx.Client) -> Extract:
    """Follow the pages to the end.

    Each page holds up to 25 orders and a `next_cursor`.
    The last page has no `next_cursor`. That is the signal to stop.
    """
    orders: list[dict] = []
    cursor = None
    calls = 0
    while True:
        answer = client.get("/orders", params={"cursor": cursor} if cursor else None)
        calls += 1
        if answer.status_code == 429:
            raise RateLimited(
                f"The API refused call {calls} after {len(orders)} orders. "
                f"It said to wait {answer.headers.get('Retry-After', '?')} seconds. "
                "Nothing was loaded."
            )
        answer.raise_for_status()
        body = answer.json()
        orders += body["data"]
        cursor = body["next_cursor"]
        if cursor is None:
            return Extract(orders, calls)
