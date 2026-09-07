"""Tests for POST /webhooks/tradingview/{secret}.

Uses the same TestClient pattern as test_smoke.py (real DB reachable at
DATABASE_URL, models.Base.metadata.create_all() runs at app import time).
"""
import os

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app import models

client = TestClient(app)

SECRET = os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", "test-webhook-secret")
os.environ["TRADINGVIEW_WEBHOOK_SECRET"] = SECRET


def _cleanup(ticker: str):
    db = SessionLocal()
    db.query(models.TradingViewSignal).filter(models.TradingViewSignal.ticker == ticker).delete()
    db.commit()
    db.close()


def test_wrong_secret_is_rejected():
    resp = client.post("/webhooks/tradingview/not-the-secret", content="hello")
    assert resp.status_code == 404


def test_missing_secret_env_rejects_even_the_right_looking_path(monkeypatch):
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET", raising=False)
    resp = client.post(f"/webhooks/tradingview/{SECRET}", content="hello")
    assert resp.status_code == 404
    os.environ["TRADINGVIEW_WEBHOOK_SECRET"] = SECRET  # restore for later tests


def test_json_body_is_parsed_and_stored():
    payload = '{"ticker": "MCPWEBHOOKTEST", "action": "buy", "price": 12345.6}'
    resp = client.post(f"/webhooks/tradingview/{SECRET}", content=payload,
                        headers={"Content-Type": "application/json"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "received"
    assert body["parsed"] is True

    db = SessionLocal()
    row = db.query(models.TradingViewSignal).filter(models.TradingViewSignal.id == body["id"]).first()
    assert row is not None
    assert row.ticker == "MCPWEBHOOKTEST"
    assert row.action == "buy"
    assert row.price == 12345.6
    assert row.parsed_ok is True
    db.close()
    _cleanup("MCPWEBHOOKTEST")


def test_non_json_body_is_still_stored():
    resp = client.post(f"/webhooks/tradingview/{SECRET}",
                        content="BTC crossed 50000 on the 4h chart")
    assert resp.status_code == 200
    body = resp.json()
    assert body["parsed"] is False

    db = SessionLocal()
    row = db.query(models.TradingViewSignal).filter(models.TradingViewSignal.id == body["id"]).first()
    assert row is not None
    assert row.parsed_ok is False
    assert "50000" in row.raw_body
    db.close()


def test_admin_endpoint_requires_auth():
    resp = client.get("/admin/tradingview-signals")
    assert resp.status_code == 401
