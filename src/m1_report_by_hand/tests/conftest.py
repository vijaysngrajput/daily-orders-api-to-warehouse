"""Shared test set-up: a private order system and a private warehouse for each test."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from order_system.app import create_app
from order_system.catalog import morning_of
from order_system.truth import daily_report as true_report


class Api(TestClient):
    """The order system, running inside the test. No network, no server to start."""

    def morning(self, day: str) -> "Api":
        self.post("/admin/clock", json={"morning": day})
        return self

    def truth(self, day: str) -> list[dict]:
        """The answer key for a morning, worked out from the event log by other code."""
        world = self.app.state.world
        return true_report(world.events, morning_of(date.fromisoformat(day)))

    @property
    def planted(self) -> dict[str, int]:
        return self.app.state.world.planted


@pytest.fixture
def api() -> Api:
    return Api(create_app())


@pytest.fixture
def warehouse(tmp_path):
    return tmp_path / "arden.duckdb"
