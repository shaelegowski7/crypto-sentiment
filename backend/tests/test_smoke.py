"""Boot smoke test.

Exists to catch the exact class of bug that shipped to HEAD on 2026-07-03:
`scheduler.add_job(_run_signal_quality_refresh, ...)` referenced a name
~2600 lines before its definition and NameError'd at module import, so
`uvicorn app.main:app` couldn't start at all. No test suite caught it
because there was no test suite.

This does not test business logic — it only proves the app *boots* (every
top-level statement in main.py runs without raising) and a couple of routes
answer with the right shape. Requires a real Postgres reachable at
DATABASE_URL: `models.Base.metadata.create_all()` runs at import time, so a
mocked/absent DB fails the import before any test body even runs.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_responds():
    resp = client.get("/")
    assert resp.status_code == 200


def test_health_reports_ok_with_db_reachable():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["scheduler"] == "ok"


def test_v1_route_requires_api_key():
    # Confirms routing + the get_api_key dependency are wired, without
    # needing a real key fixture.
    resp = client.get("/v1/usage")
    assert resp.status_code == 401


def test_unknown_route_is_404_not_500():
    resp = client.get("/this-route-does-not-exist")
    assert resp.status_code == 404
