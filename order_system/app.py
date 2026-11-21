"""The order system's API.

Start it from the project folder:

    uv run python -m order_system

Then open http://127.0.0.1:8010/docs to try it in a browser.
"""

import base64
import time
from datetime import date, datetime, timedelta

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from order_system.catalog import (
    BUSINESS_TZ,
    BUSINESS_TZ_NAME,
    FIRST_DAY,
    business_day,
    from_text,
    morning_of,
    to_text,
)
from order_system.generate import SEED, build, orders_as_of

PAGE_SIZE = 25  # the most orders one call returns
RATE_LIMIT_CALLS = 12  # calls to GET /orders allowed ...
RATE_LIMIT_SECONDS = 10  # ... in this many seconds


REPEATED_PAGE_DAY = date(2026, 6, 11)  # planted: on this day one page is served twice
FAULTS = ("error_500", "rate_limit", "repeat_page")


class ClockChange(BaseModel):
    """Send exactly one of these."""

    morning: date | None = None  # 07:00 local on this date, the time of the daily run
    now: str | None = None  # any UTC moment, like 2026-06-08T12:00:00Z
    advance_days: int | None = None  # move forward this many days
    advance_minutes: int | None = None  # move forward this many minutes


class Fault(BaseModel):
    """Make the next calls to GET /orders go wrong."""

    kind: str  # error_500, rate_limit or repeat_page
    count: int = 1  # how many calls it affects


class Webhook(BaseModel):
    url: str  # the address that receives each change


class World:
    """Everything the API knows: the event log, the clock, and recent calls."""

    def __init__(self, seed: int = SEED, timer=time.monotonic, post=None):
        self.events, self.planted = build(seed)
        self.timer = timer
        self.post = post or (lambda url, body: httpx.post(url, json=body, timeout=2))
        self.now = morning_of(FIRST_DAY + timedelta(days=1))  # the first run: 2026-06-02
        self.calls: list[float] = []
        self.faults = dict.fromkeys(FAULTS, 0)
        self.webhooks: list[str] = []
        self.before: dict[int, dict | None] = {}  # pos -> the order before that change
        latest: dict[int, dict | None] = {}
        for event in self.events:
            self.before[event.pos] = latest.get(event.order_id)
            latest[event.order_id] = event.after

    def change(self, event, with_before: bool = False) -> dict:
        """One entry of the event log, as the API sends it."""
        body = {
            "pos": event.pos,
            "op": event.op,
            "order_id": event.order_id,
            "changed_at": to_text(event.event_time),
            "after": event.after,
        }
        if with_before:
            body["before"] = self.before[event.pos]
        return body

    def set_clock(self, moment: datetime) -> dict:
        """Move the clock. Returns what the webhooks did, if any are registered."""
        old, self.now = self.now, moment
        self.calls = []  # a new morning: the rate limit starts again
        # The planted fault: in each run of 2026-06-11, one page is served twice.
        self.planted_repeat = business_day(moment) == REPEATED_PAGE_DAY
        sent = failed = 0
        if self.webhooks and moment > old:
            for event in self.events:
                if old < event.visible_at <= moment:
                    for url in self.webhooks:
                        try:
                            self.post(url, self.change(event))
                            sent += 1
                        except Exception:
                            failed += 1  # a push is sent once; nobody tries again
        return {"sent": sent, "failed": failed}

    def seconds_to_wait(self) -> int:
        """0 if a call is allowed now. Otherwise the seconds until one is."""
        moment = self.timer()
        self.calls = [t for t in self.calls if moment - t < RATE_LIMIT_SECONDS]
        if len(self.calls) < RATE_LIMIT_CALLS:
            self.calls.append(moment)
            return 0
        return max(1, int(RATE_LIMIT_SECONDS - (moment - self.calls[0])) + 1)

    def take(self, fault: str) -> bool:
        """Use up one planned fault of this kind, if there is one."""
        if self.faults[fault] > 0:
            self.faults[fault] -= 1
            return True
        return False

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


def create_app(seed: int = SEED, timer=time.monotonic, post=None) -> FastAPI:
    app = FastAPI(
        title="Arden order system",
        description="A fake order API for the Daily Orders project. All data is fictional.",
    )
    world = World(seed, timer, post)
    world.planted_repeat = False
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
        updated_since: str | None = Query(
            None, description="Only orders changed at or after this UTC time"
        ),
        cursor: str | None = Query(None, description="The next_cursor of the page before"),
        limit: int = Query(PAGE_SIZE, ge=1, le=PAGE_SIZE),
    ) -> dict:
        if world.take("error_500"):
            raise HTTPException(500, "The order system had an internal error. Try again.")
        if world.take("rate_limit"):
            raise HTTPException(
                429, "Too many requests. Try again in 1 second.", headers={"Retry-After": "1"}
            )
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
        next_cursor = _encode(page[-1]) if more else None
        if cursor and (world.planted_repeat or world.take("repeat_page")):
            # The fault: the bookmark does not move, so the next call gets this page again.
            world.planted_repeat = False
            next_cursor = cursor
        return {"data": page, "next_cursor": next_cursor}

    @app.get("/orders/{order_id}")
    def one_order(order_id: int) -> dict:
        order = orders_as_of(world.events, world.now).get(order_id)
        if order is None:
            raise HTTPException(404, f"Order {order_id} does not exist.")
        return order

    @app.get("/changes")
    def change_feed(
        after_pos: int = Query(0, ge=0, description="Only changes after this position"),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict:
        """The change feed: every insert, update and delete, in order. A lab, not the pipeline."""
        seen = [e for e in world.events if e.visible_at <= world.now and e.pos > after_pos]
        page = seen[:limit]
        return {
            "data": [world.change(e, with_before=True) for e in page],
            "last_pos": page[-1].pos if page else after_pos,
            "more": len(seen) > limit,
        }

    @app.get("/admin/clock")
    def read_clock() -> dict:
        return clock()

    @app.post("/admin/clock")
    def change_clock(change: ClockChange) -> dict:
        given = [
            v
            for v in (change.morning, change.now, change.advance_days, change.advance_minutes)
            if v is not None
        ]
        if len(given) != 1:
            raise HTTPException(
                400, "Send exactly one of: morning, now, advance_days, advance_minutes."
            )
        if change.morning is not None:
            pushed = world.set_clock(morning_of(change.morning))
        elif change.now is not None:
            pushed = world.set_clock(from_text(change.now))
        elif change.advance_minutes is not None:
            pushed = world.set_clock(world.now + timedelta(minutes=change.advance_minutes))
        else:
            pushed = world.set_clock(world.now + timedelta(days=change.advance_days))
        return {**clock(), "webhooks": pushed}

    @app.post("/admin/faults")
    def plan_fault(fault: Fault) -> dict:
        if fault.kind not in FAULTS:
            raise HTTPException(400, f"kind must be one of: {', '.join(FAULTS)}")
        world.faults[fault.kind] = max(0, fault.count)
        return world.faults

    @app.post("/admin/reset")
    def reset() -> dict:
        world.faults = dict.fromkeys(FAULTS, 0)
        world.webhooks = []
        world.set_clock(morning_of(FIRST_DAY + timedelta(days=1)))
        return clock()

    @app.post("/webhooks")
    def add_webhook(hook: Webhook) -> dict:
        """Register an address. Each change is pushed to it when the clock passes it."""
        if hook.url not in world.webhooks:
            world.webhooks.append(hook.url)
        return {"webhooks": world.webhooks}

    @app.delete("/webhooks")
    def clear_webhooks() -> dict:
        world.webhooks = []
        return {"webhooks": []}

    return app


app = create_app()
