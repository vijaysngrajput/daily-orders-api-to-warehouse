"""The API: pages, the changed-since filter, the rate limit and the clock."""

from fastapi.testclient import TestClient

from order_system.app import PAGE_SIZE, RATE_LIMIT_CALLS, create_app


class FakeTimer:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def client(timer=None):
    return TestClient(create_app(timer=timer or FakeTimer()))


def read_all(api, **params):
    rows, cursor, calls = [], None, 0
    while True:
        body = api.get("/orders", params={**params, "cursor": cursor}).json()
        calls += 1
        rows += body["data"]
        cursor = body["next_cursor"]
        if cursor is None:
            return rows, calls


def test_pages_give_every_order_exactly_once():
    api = client()
    api.post("/admin/clock", json={"morning": "2026-06-08"})
    rows, calls = read_all(api)
    assert len(rows) == 226 and calls == 10
    assert len({o["order_id"] for o in rows}) == 226
    assert all(len(o["items"]) >= 1 for o in rows)


def test_a_page_is_never_larger_than_the_limit():
    api = client()
    assert len(api.get("/orders").json()["data"]) == PAGE_SIZE
    assert api.get("/orders", params={"limit": 500}).status_code == 422


def test_updated_since_returns_only_changed_orders():
    api = client()
    api.post("/admin/clock", json={"morning": "2026-06-09"})
    rows, _ = read_all(api, updated_since="2026-06-08T12:00:00Z")
    assert 0 < len(rows) < 60
    assert all(o["updated_at"] >= "2026-06-08T12:00:00Z" for o in rows)
    refunded = [o for o in rows if o["refunded_cents"]]
    assert [o["refunded_cents"] for o in refunded] == [578]  # P4, refunded on 06-08


def test_rate_limit_answers_429_then_recovers():
    timer = FakeTimer()
    api = client(timer)
    for _ in range(RATE_LIMIT_CALLS):
        assert api.get("/orders").status_code == 200
    refused = api.get("/orders")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    timer.now += int(refused.headers["Retry-After"])
    assert api.get("/orders").status_code == 200


def test_the_full_load_passes_early_and_fails_later():
    """The reason milestone 2 exists: the full load outgrows the rate limit."""
    api = client()
    api.post("/admin/clock", json={"morning": "2026-06-10"})
    assert read_all(api)[1] == 12  # 12 calls: just allowed
    api.post("/admin/clock", json={"morning": "2026-06-11"})
    codes = []
    cursor = None
    while True:
        answer = api.get("/orders", params={"cursor": cursor})
        codes.append(answer.status_code)
        if answer.status_code != 200 or answer.json()["next_cursor"] is None:
            break
        cursor = answer.json()["next_cursor"]
    assert codes == [200] * 12 + [429]


def test_clock_moves_and_changes_what_the_api_shows():
    api = client()
    assert api.get("/admin/clock").json()["now_local"] == "2026-06-02 07:00:00"
    first = len(read_all(api)[0])
    moved = api.post("/admin/clock", json={"advance_days": 1}).json()
    assert moved["now_utc"] == "2026-06-03T12:00:00Z"
    assert len(read_all(api)[0]) > first
    assert api.post("/admin/clock", json={}).status_code == 400
    assert api.post("/admin/reset").json()["now_local"] == "2026-06-02 07:00:00"


def test_a_deleted_order_is_gone():
    api = client()
    p8 = api.app.state.world.planted["P8"]
    api.post("/admin/clock", json={"morning": "2026-06-13"})
    assert api.get(f"/orders/{p8}").json()["customer_id"] == 9999
    api.post("/admin/clock", json={"morning": "2026-06-14"})
    assert api.get(f"/orders/{p8}").status_code == 404
