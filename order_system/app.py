"""The order system's API.

Start it from the project folder:

    uv run python -m order_system

Then open http://127.0.0.1:8010/docs to try it in a browser.
"""

import base64
import time
from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

from order_system.catalog import (
    BUSINESS_TZ,
    BUSINESS_TZ_NAME,
    FIRST_DAY,
    from_text,
    morning_of,
    to_text,
)
from order_system.generate import SEED, build, orders_as_of

PAGE_SIZE = 25  # the most orders one call returns
RATE_LIMIT_CALLS = 12  # calls to GET /orders allowed ...
RATE_LIMIT_SECONDS = 10  # ... in this many seconds


class ClockChange(BaseModel):
    """Send exactly one of the three."""

    morning: date | None = None  # 07:00 local on this date, the time of the daily run
    now: str | None = None  # any UTC moment, like 2026-06-08T12:00:00Z
    advance_days: int | None = None  # move forward this many days


class World:
    """Everything the API knows: the event log, the clock, and recent calls."""

    def __init__(self, seed: int = SEED, timer=time.monotonic):
        self.events, self.planted = build(seed)
        self.timer = timer
        self.now = morning_of(FIRST_DAY + timedelta(days=1))  # the first run: 2026-06-02
        self.calls: list[float] = []

    def set_clock(self, moment: datetime) -> None:
        self.now = moment
        self.calls = []  # a new morning: the rate limit starts again

    def seconds_to_wait(self) -> int:
        """0 if a call is allowed now. Otherwise the seconds until one is."""
        moment = self.timer()
        self.calls = [t for t in self.calls if moment - t < RATE_LIMIT_SECONDS]
        if len(self.calls) < RATE_LIMIT_CALLS:
            self.calls.append(moment)
            return 0
        return max(1, int(RATE_LIMIT_SECONDS - (moment - self.calls[0])) + 1)

    def orders(self) -> list[dict]:
        """Every order the API shows now, sorted by updated_at, then order_id."""
        rows = orders_as_of(self.events, self.now).values()
        return sorted(rows, key=lambda o: (o["updated_at"], o["order_id"]))


def _encode(order: dict) -> str:
    text = f"{order['updated_at']}|{order['order_id']}"
    return base64.urlsafe_b64encode(text.encode()).decode()


def _decode(cursor: str) -> tuple[str, int]:
    try:
        updated_at, order_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return updated_at, int(order_id)
    except Exception as error:
        raise HTTPException(
            400, "The cursor is not valid. Use the next_cursor you were given."
        ) from error


def create_app(seed: int = SEED, timer=time.monotonic) -> FastAPI:
    app = FastAPI(
        title="Arden order system",
        description="A fake order API for the Daily Orders project. All data is fictional.",
    )
    world = World(seed, timer)
    app.state.world = world

    def clock() -> dict:
        local = world.now.astimezone(BUSINESS_TZ)
        return {
            "now_utc": to_text(world.now),
            "now_local": local.strftime("%Y-%m-%d %H:%M:%S"),
            "business_time_zone": BUSINESS_TZ_NAME,
        }

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", **clock()}

    @app.get("/orders")
    def list_orders(
        response: Response,
        updated_since: str | None = Query(
            None, description="Only orders changed at or after this UTC time"
        ),
        cursor: str | None = Query(None, description="The next_cursor of the page before"),
        limit: int = Query(PAGE_SIZE, ge=1, le=PAGE_SIZE),
    ) -> dict:
        wait = world.seconds_to_wait()
        if wait:
            raise HTTPException(
                429,
                f"Too many requests. This API allows {RATE_LIMIT_CALLS} calls "
                f"in {RATE_LIMIT_SECONDS} seconds. Try again in {wait} seconds.",
                headers={"Retry-After": str(wait)},
            )
        rows = world.orders()
        if updated_since:
            try:
                since = to_text(from_text(updated_since))
            except ValueError as error:
                raise HTTPException(
                    400, "updated_since must look like 2026-06-08T12:00:00Z"
                ) from error
            rows = [o for o in rows if o["updated_at"] >= since]
        if cursor:
            after = _decode(cursor)
            rows = [o for o in rows if (o["updated_at"], o["order_id"]) > after]
        page = rows[:limit]
        more = len(rows) > limit
        return {"data": page, "next_cursor": _encode(page[-1]) if more else None}

    @app.get("/orders/{order_id}")
    def one_order(order_id: int) -> dict:
        order = orders_as_of(world.events, world.now).get(order_id)
        if order is None:
            raise HTTPException(404, f"Order {order_id} does not exist.")
        return order

    @app.get("/admin/clock")
    def read_clock() -> dict:
        return clock()

    @app.post("/admin/clock")
    def change_clock(change: ClockChange) -> dict:
        given = [v for v in (change.morning, change.now, change.advance_days) if v is not None]
        if len(given) != 1:
            raise HTTPException(400, "Send exactly one of: morning, now, advance_days.")
        if change.morning is not None:
            world.set_clock(morning_of(change.morning))
        elif change.now is not None:
            world.set_clock(from_text(change.now))
        else:
            world.set_clock(world.now + timedelta(days=change.advance_days))
        return clock()

    @app.post("/admin/reset")
    def reset() -> dict:
        world.set_clock(morning_of(FIRST_DAY + timedelta(days=1)))
        return clock()

    return app


app = create_app()
