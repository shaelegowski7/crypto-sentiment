"""The morning brief's UNSUBSCRIBE link must work, and only for its recipient.

It used to be POST-only (an email link is a GET, so every click 405'd), the
handler called create_client without importing it (so a POST 500'd), and it
would switch off the brief for any user_id with no proof of ownership.

Supabase is faked; needs a reachable DATABASE_URL like test_smoke.py.
"""
from urllib.parse import parse_qs, urlparse

import pytest
import supabase
from fastapi.testclient import TestClient

from app import brief, main

client = TestClient(main.app)

USER = "11111111-2222-3333-4444-555555555555"


class _Supabase:
    def __init__(self):
        self.updates = []

    def from_(self, table):
        outer = self

        class _Q:
            def update(self, vals):
                self.vals = vals
                return self

            def eq(self, col, val):
                outer.updates.append((table, self.vals, col, val))
                return self

            def execute(self):
                return None
        return _Q()


@pytest.fixture
def fake_sb(monkeypatch):
    sb = _Supabase()
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(supabase, "create_client", lambda *a, **k: sb)
    return sb


def _path_and_params(url):
    u = urlparse(url)
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


@pytest.mark.parametrize("method", ["get", "post"])
def test_link_from_email_unsubscribes(fake_sb, method):
    path, params = _path_and_params(brief.unsubscribe_url(USER))
    r = getattr(client, method)(path, params=params)
    assert r.status_code == 200
    assert "unsubscribed" in r.text
    assert fake_sb.updates == [("profiles", {"morning_brief_enabled": False}, "id", USER)]


@pytest.mark.parametrize("token", [None, "", "0" * 64])
def test_missing_or_forged_token_changes_nothing(fake_sb, token):
    params = {"user_id": USER}
    if token is not None:
        params["token"] = token
    r = client.get("/api/brief/unsubscribe", params=params)
    assert r.status_code == 400
    assert "Account settings" in r.text
    assert fake_sb.updates == []


def test_token_is_bound_to_its_user(fake_sb):
    other = "99999999-8888-7777-6666-555555555555"
    r = client.get("/api/brief/unsubscribe",
                   params={"user_id": other, "token": brief.unsubscribe_token(USER)})
    assert r.status_code == 400
    assert fake_sb.updates == []


def test_no_signing_key_rejects_everything(fake_sb, monkeypatch):
    token = brief.unsubscribe_token(USER)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY")
    assert brief.verify_unsubscribe_token(USER, token) is False
