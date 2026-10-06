"""Free-key quota: 1000 calls per rolling 30-day window from first billable use.

Free keys are gated on `free_window_calls` within a window that track_usage
opens on the first billable call and re-opens on the first billable call after
it lapses. Paid keys are gated on `calls_this_month` (calendar, reset by cron).
The usage report must use the same clock as enforcement, on both surfaces —
/v1/usage and the MCP get_usage tool share _usage_payload for that reason.

Imports app.main, so like test_smoke.py this needs a reachable DATABASE_URL.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import mcp_server
from app.main import _allowance_exhausted, _usage_payload, app, get_api_key, track_usage

NOW = datetime.utcnow()


def _key(**overrides):
    fields = dict(
        key_prefix="sfx_test", free_calls=1000, monthly_allowance=0,
        calls_used=0, calls_this_month=0, unlimited=False,
        stripe_customer_id="cus_free",  # free signup creates a customer too
        free_window_started_at=None, free_window_calls=0, created_at=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _days_ago(n):
    return NOW - timedelta(days=n)


# (key, expected included_remaining, expected exhausted, expected resets_at set)
CASES = [
    pytest.param(_key(), 1000, False, False, id="free-never-used"),
    pytest.param(_key(free_window_started_at=_days_ago(10), free_window_calls=250,
                      calls_used=5000, calls_this_month=0),
                 750, False, True, id="free-mid-window-ignores-lifetime-and-month"),
    pytest.param(_key(free_window_started_at=_days_ago(10), free_window_calls=1000),
                 0, True, True, id="free-spent-in-window"),
    # Lapsed window: full allowance back, nothing running until the next call.
    pytest.param(_key(free_window_started_at=_days_ago(31), free_window_calls=1000),
                 1000, False, False, id="free-window-lapsed"),
    pytest.param(_key(monthly_allowance=1000, free_calls=0, calls_used=5000,
                      calls_this_month=10, stripe_customer_id="cus_paid"),
                 990, False, True, id="paid-monthly"),
    pytest.param(_key(unlimited=True, free_calls=0, calls_used=9999),
                 None, False, True, id="unlimited"),
]


@pytest.mark.parametrize("key, remaining, exhausted, has_reset", CASES)
def test_v1_usage(key, remaining, exhausted, has_reset):
    app.dependency_overrides[get_api_key] = lambda: key
    try:
        resp = TestClient(app).get("/v1/usage")
    finally:
        app.dependency_overrides.pop(get_api_key, None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["included_remaining"] == remaining
    assert (body["resets_at"] is not None) == has_reset
    assert _allowance_exhausted(key) is exhausted


@pytest.mark.parametrize("key, remaining, exhausted, has_reset", CASES)
def test_mcp_get_usage(key, remaining, exhausted, has_reset, monkeypatch):
    db = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(mcp_server, "_open_authed_session",
                        lambda ctx, enforce_quota=True: (key, db))
    usage = mcp_server.get_usage(None)
    assert usage["included_remaining"] == remaining
    assert (usage["resets_at"] is not None) == has_reset


def test_free_key_reports_free_plan_despite_stripe_customer():
    # Free signup creates a Stripe customer, which used to read as "metered".
    usage = _usage_payload(_key())
    assert usage["plan"] == "free"
    assert usage["overage_billing"] is False


def test_free_window_resets_at_is_window_end():
    start = _days_ago(10)
    usage = _usage_payload(_key(free_window_started_at=start, free_window_calls=1))
    assert usage["resets_at"] == (start + timedelta(days=30)).isoformat() + "Z"


# --- track_usage opens and rolls the window ---------------------------------

_DB = SimpleNamespace(commit=lambda: None)


def test_first_billable_call_opens_window():
    key = _key()
    track_usage(key, _DB, count=3)
    assert key.free_window_calls == 3
    assert abs((datetime.utcnow() - key.free_window_started_at).total_seconds()) < 5


def test_calls_accumulate_within_window():
    start = _days_ago(5)
    key = _key(free_window_started_at=start, free_window_calls=10)
    track_usage(key, _DB, count=2)
    assert key.free_window_started_at == start
    assert key.free_window_calls == 12


def test_call_after_lapse_opens_fresh_window():
    key = _key(free_window_started_at=_days_ago(31), free_window_calls=1000)
    track_usage(key, _DB, count=1)
    assert key.free_window_calls == 1
    assert key.free_window_started_at > _days_ago(1)
    assert _allowance_exhausted(key) is False


def test_paid_key_never_opens_free_window():
    key = _key(monthly_allowance=1000, free_calls=0, stripe_customer_id=None)
    track_usage(key, _DB, count=1)
    assert key.free_window_started_at is None
    assert key.free_window_calls == 0
    assert key.calls_this_month == 1
