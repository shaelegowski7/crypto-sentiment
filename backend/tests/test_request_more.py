"""Request-more: the replacement for per-call overage.

A caller past their allowance asks for more via POST /api/keys/request-more;
an admin grants it with POST /admin/keys/grant, which sets APIKey.extra_calls.
Needs a reachable DATABASE_URL (rows are written for real), like test_smoke.py.
"""
import uuid

import pytest
import resend
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import _allowance_exhausted, _hash_key, app

client = TestClient(app)


@pytest.fixture
def sent(monkeypatch):
    """Capture notification emails instead of calling Resend."""
    outbox = []
    monkeypatch.setattr(resend.Emails, "send", lambda msg: outbox.append(msg))
    return outbox


@pytest.fixture
def paid_key():
    """A real paid key that has spent its whole monthly allowance."""
    db = SessionLocal()
    email = f"rm-{uuid.uuid4().hex[:10]}@example.com"
    key = models.APIKey(key_hash=_hash_key(email), key_prefix="sfx_rmtest",
                        email=email, free_calls=0, monthly_allowance=1000,
                        calls_this_month=1000)
    db.add(key)
    db.commit()
    yield db, key
    db.query(models.AllowanceRequest).filter(models.AllowanceRequest.email == email).delete()
    db.delete(key)
    db.commit()
    db.close()


def test_request_is_stored_and_notified(paid_key, sent):
    db, key = paid_key
    resp = client.post("/api/keys/request-more", json={
        "email": key.email.upper(), "calls_wanted": 50000,
        "use_case": "<b>backtest</b> across all 42 tickers"})
    assert resp.status_code == 200

    req = (db.query(models.AllowanceRequest)
           .filter(models.AllowanceRequest.email == key.email).one())
    assert req.key_prefix == "sfx_rmtest"
    assert req.calls_wanted == 50000
    assert len(sent) == 1
    assert "&lt;b&gt;backtest&lt;/b&gt;" in sent[0]["html"]   # requester text is escaped
    assert "<b>backtest</b>" not in sent[0]["html"]


def test_reply_does_not_reveal_whether_a_key_exists(paid_key, sent):
    _, key = paid_key
    known = client.post("/api/keys/request-more", json={"email": key.email}).json()
    unknown_email = f"nobody-{uuid.uuid4().hex[:8]}@example.com"
    unknown = client.post("/api/keys/request-more", json={"email": unknown_email}).json()
    assert known["message"].replace(key.email, "X") == unknown["message"].replace(unknown_email, "X")
    db = SessionLocal()
    db.query(models.AllowanceRequest).filter(models.AllowanceRequest.email == unknown_email).delete()
    db.commit()
    db.close()


def test_bad_input_is_rejected(sent):
    assert client.post("/api/keys/request-more", json={"email": "not-an-email"}).status_code == 400
    assert not sent


def test_admin_grant_lifts_a_spent_key(paid_key, monkeypatch, sent):
    db, key = paid_key
    monkeypatch.setenv("ADMIN_SECRET", "test-secret")
    assert _allowance_exhausted(key) is True

    assert client.post("/admin/keys/grant", params={
        "secret": "wrong", "email": key.email, "extra_calls": 5000}).status_code == 401

    resp = client.post("/admin/keys/grant", params={
        "secret": "test-secret", "email": key.email, "extra_calls": 5000})
    assert resp.status_code == 200
    assert resp.json()["remaining"] == 5000

    db.refresh(key)
    assert key.extra_calls == 5000
    assert _allowance_exhausted(key) is False
