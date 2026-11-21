"""Shared test set-up: a private order system, and a private database in the warehouse.

The warehouse must be running:  docker compose up -d warehouse
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from daily_orders import warehouse
from order_system.app import create_app
from order_system.catalog import morning_of
from order_system.truth import daily_report as true_report

TEST_DATABASE = "arden_m4_test"


class Api(TestClient):
    """The order system, running inside the test. No network, no server to start."""

    def morning(self, day: str) -> "Api":
        self.post("/admin/clock", json={"morning": day})
        return self

    def fault(self, kind: str, count: int = 1) -> "Api":
        self.post("/admin/faults", json={"kind": kind, "count": count})
        return self

    def truth(self, day: str) -> list[dict]:
        """The answer key for a morning, worked out from the event log by other code."""
        world = self.app.state.world
        return true_report(world.events, morning_of(date.fromisoformat(day)))

    @property
    def planted(self) -> dict[str, int]:
        return self.app.state.world.planted


class NoClock:
    """Stands in for the wall clock, so the API's own rate limit never slows a test."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def api() -> Api:
    """An order system whose rate limit runs on a pretend clock, so no test ever waits."""
    clock = NoClock()
    api = Api(create_app(timer=clock))
    api.nap = lambda seconds: setattr(clock, "now", clock.now + seconds)
    return api


@pytest.fixture(scope="session")
def database():
    try:
        warehouse.drop_database(TEST_DATABASE)
        warehouse.create_database(TEST_DATABASE)
    except ConnectionError as error:
        pytest.exit(str(error), returncode=1)
    yield TEST_DATABASE
    warehouse.drop_database(TEST_DATABASE)


@pytest.fixture
def con(database):
    """An empty warehouse for each test."""
    connection = warehouse.connect(database)
    warehouse.reset(connection)
    yield connection
    connection.close()


@pytest.fixture
def waits():
    """Collects the seconds the code wanted to sleep, without sleeping."""
    return []


def run_mornings(api, con, *days, **options):
    """Run the pipeline on each of these mornings. Returns the results."""
    from daily_orders.pipeline import run

    return [run(api.morning(day), con, sleep=api.nap, **options) for day in days]


def june(first: int, last: int) -> list[str]:
    return [f"2026-06-{day:02d}" for day in range(first, last + 1)]


def snapshot(con) -> tuple:
    """Everything people read: the clean orders, the items and the report."""
    orders = con.execute(
        "SELECT order_id, status, total_cents, refunded_cents, updated_at, is_deleted FROM core.orders ORDER BY 1"
    ).fetchall()
    items = con.execute("SELECT * FROM core.order_items ORDER BY order_id, line_no").fetchall()
    report = con.execute("SELECT * FROM marts.daily_orders ORDER BY 1, 2").fetchall()
    return orders, items, report
