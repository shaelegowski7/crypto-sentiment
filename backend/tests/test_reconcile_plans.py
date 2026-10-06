"""POST /admin/billing/reconcile-plans: repair keys left paid after a cancellation.

Before the subscription-end fix the webhook never touched the key, so keys of
people who cancelled still carry 15,000/200,000 calls a month. Stripe and
Supabase are faked; keys are real rows, so this needs a reachable DATABASE_URL.
"""
import uuid

import pytest
import stripe
import supabase
from fastapi.testclient import TestClient

from app import main, models
from app.database import SessionLocal

client = TestClient(main.app)

DATA_PRICE = "price_1TUqVG2NzVdYK0wrKrPTE28e"
PRO_PRICE = "price_pro_test"


class _Page:
    def __init__(self, items):
        self._items = items

    def auto_paging_iter(self):
        return iter(self._items)


def _sub(status, price):
    return {"status": status,
            "items": {"data": [{"price": {"id": price, "recurring": {"usage_type": "licensed"}}}]}}


class _Supabase:
    """profiles table: select('tier').eq('email', e) and update({...}).eq('email', e)."""

    def __init__(self, tiers):
        self.tiers = tiers      # email -> tier
        self.updates = []

    def table(self, name):
        outer = self

        class _Q:
            mode = vals = email = None

            def select(self, cols):
                self.mode = "select"
                return self

            def update(self, vals):
                self.mode, self.vals = "update", vals
                return self

            def eq(self, col, val):
                self.email = val
                return self

            def execute(self):
                if self.mode == "update":
                    outer.updates.append((self.email, self.vals["tier"]))
                    outer.tiers[self.email] = self.vals["tier"]
                    return None
                tier = outer.tiers.get(self.email)
                return type("R", (), {"data": [{"tier": tier}] if tier else []})()
        return _Q()


@pytest.fixture
def world(monkeypatch):
    state = {"subs": {}, "broken": set(), "tiers": {}}
    sb = _Supabase(state["tiers"])

    def sub_list(customer, status, limit):
        if customer in state["broken"]:
            raise stripe.error.APIConnectionError("stripe down")
        return _Page(state["subs"].get(customer, []))

    monkeypatch.setattr(stripe.Customer, "list", lambda email, limit: _Page([]))
    monkeypatch.setattr(stripe.Subscription, "list", sub_list)
    monkeypatch.setattr(supabase, "create_client", lambda *a: sb)
    monkeypatch.setattr(main, "DATA_PRICE_IDS", {DATA_PRICE})
    monkeypatch.setenv("ADMIN_SECRET", "test-secret")
    state["supabase"] = sb
    db = SessionLocal()
    state["db"] = db
    made = []

    def make_key(allowance, dashboard_tier=None, subs=(), broken=False):
        email = f"recon-{uuid.uuid4().hex[:10]}@example.com"
        customer = "cus_" + uuid.uuid4().hex[:10]
        key = models.APIKey(key_hash=uuid.uuid4().hex, key_prefix="sfx_" + uuid.uuid4().hex[:8],
                            email=email, free_calls=0, monthly_allowance=allowance,
                            stripe_customer_id=customer)
        db.add(key)
        db.commit()
        made.append(key)
        state["subs"][customer] = list(subs)
        if broken:
            state["broken"].add(customer)
        if dashboard_tier:
            state["tiers"][email] = dashboard_tier
        return key

    state["make_key"] = make_key
    yield state
    for key in made:
        db.delete(key)
    db.commit()
    db.close()


def _run(apply=False, secret="test-secret"):
    return client.post("/admin/billing/reconcile-plans",
                       params={"secret": secret, "apply": str(apply).lower()})


def _by_prefix(rows, key):
    return [r for r in rows if r["key_prefix"] == key.key_prefix]


def test_requires_admin_secret(world):
    assert _run(secret="wrong").status_code == 401


def test_dry_run_reports_without_changing(world):
    cancelled = world["make_key"](15000, dashboard_tier="free",
                                  subs=[_sub("canceled", PRO_PRICE)])
    body = _run().json()
    assert body["mode"].startswith("dry-run")
    assert _by_prefix(body["changes"], cancelled)[0]["new_allowance"] == 0
    world["db"].refresh(cancelled)
    assert cancelled.monthly_allowance == 15000


def test_apply_drops_cancelled_key_to_free(world):
    cancelled = world["make_key"](200000, dashboard_tier="free",
                                  subs=[_sub("canceled", DATA_PRICE)])
    _run(apply=True)
    world["db"].refresh(cancelled)
    assert cancelled.monthly_allowance == 0
    assert cancelled.free_calls == main.FREE_WINDOW_ALLOWANCE


def test_comped_account_is_left_alone(world):
    # Pro set by hand in Supabase, nothing in Stripe: not a cancellation.
    comped = world["make_key"](15000, dashboard_tier="pro", subs=[])
    body = _run(apply=True).json()
    assert _by_prefix(body["left_alone_supabase_paid"], comped)
    world["db"].refresh(comped)
    assert comped.monthly_allowance == 15000


def test_live_plan_is_untouched(world):
    live = world["make_key"](200000, dashboard_tier="data", subs=[_sub("active", DATA_PRICE)])
    body = _run(apply=True).json()
    assert not _by_prefix(body["changes"], live)
    world["db"].refresh(live)
    assert live.monthly_allowance == 200000


def test_stripe_error_skips_the_key(world):
    unknown = world["make_key"](15000, dashboard_tier="free", broken=True)
    body = _run(apply=True).json()
    assert _by_prefix(body["errors"], unknown)
    world["db"].refresh(unknown)
    assert unknown.monthly_allowance == 15000


def test_falls_back_to_live_plan_and_fixes_dashboard_tier(world):
    # Data ended, Pro still live; the old webhook had marked them free.
    key = world["make_key"](200000, dashboard_tier="free",
                            subs=[_sub("canceled", DATA_PRICE), _sub("active", PRO_PRICE)])
    _run(apply=True)
    world["db"].refresh(key)
    assert key.monthly_allowance == 15000
    assert (key.email, "pro") in world["supabase"].updates
