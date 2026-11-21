"""Extract: ask the order system for orders that changed, and for orders that were deleted.

Orders: an incremental load on `updated_at`, with retries.
Deletes: the order system's change feed. A deleted row has no `updated_at` to find.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import httpx

from daily_orders.config import BACKOFF_SECONDS, MAX_TRIES


class ExtractFailed(Exception):
    """The API kept failing, or gave an error that a retry cannot fix."""


@dataclass
class Extract:
    orders: list[dict] = field(default_factory=list)  # every order, exactly as the API sent it
    pages: list[int] = field(default_factory=list)  # the page each order arrived on
    api_calls: int = 0  # every call, also the ones that failed
    retries: int = 0  # calls that had to be made again
    waited_seconds: float = 0.0


def read_run_date(client: httpx.Client) -> date:
    """The morning this run is for: today's date on the order system's clock."""
    answer = client.get("/admin/clock")
    answer.raise_for_status()
    return date.fromisoformat(answer.json()["now_local"][:10])


def get_page(
    client: httpx.Client,
    params: dict,
    extract: Extract,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Ask for one page. Wait and try again when the API says "not now".

    429 (too many requests): wait as long as the Retry-After header says.
    500 to 504 (the server failed): wait 1, 2, 4, 8 seconds. That is backoff.
    Anything else is our mistake. A retry cannot fix it, so stop.
    """
    for attempt in range(1, MAX_TRIES + 1):
        answer = client.get("/orders", params=params)
        extract.api_calls += 1
        if answer.status_code == 200:
            return answer.json()
        if answer.status_code == 429:
            wait = float(answer.headers.get("Retry-After", 1))
        elif 500 <= answer.status_code <= 504:
            wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1]
        else:
            raise ExtractFailed(f"The API answered {answer.status_code}: {answer.text[:200]}")
        if attempt == MAX_TRIES:
            break
        extract.retries += 1
        extract.waited_seconds += wait
        sleep(wait)
    raise ExtractFailed(
        f"The API still answered {answer.status_code} after {MAX_TRIES} tries. Nothing was loaded."
    )


def fetch_changed_orders(
    client: httpx.Client,
    updated_since: str | None,
    sleep: Callable[[float], None] = time.sleep,
) -> Extract:
    """Read every order changed at or after `updated_since`. None means: read everything.

    The API includes the boundary: an order changed exactly at `updated_since`
    comes back again. A repeat is safe to handle. A lost row is not.
    """
    extract = Extract()
    cursor = None
    page_no = 0
    while True:
        params = {}
        if updated_since:
            params["updated_since"] = updated_since
        if cursor:
            params["cursor"] = cursor
        body = get_page(client, params, extract, sleep)
        page_no += 1
        extract.orders += body["data"]
        extract.pages += [page_no] * len(body["data"])
        cursor = body["next_cursor"]
        if cursor is None:
            return extract


def fetch_deletes(client: httpx.Client, after_pos: int) -> tuple[list[dict], int]:
    """Read the change feed after `after_pos`, and keep the deletes.

    Returns the deletes, and the last position read.

    The change feed stands in for a log-based CDC tool. In a real company this
    list would come from the source database's own log, through a tool such as Debezium.
    """
    deletes, pos = [], after_pos
    while True:
        answer = client.get("/changes", params={"after_pos": pos, "limit": 500})
        answer.raise_for_status()
        body = answer.json()
        deletes += [change for change in body["data"] if change["op"] == "d"]
        pos = body["last_pos"]
        if not body["more"]:
            return deletes, pos
