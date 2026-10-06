"""customer.subscription.deleted must leave the key on the plan that's still live.

The webhook used to set the dashboard tier to free on ANY subscription ending
and never touched the API key, so a cancelled Pro/Data key kept its paid
allowance indefinitely -- and ending a secondary subscription (the separate
metered one annual buyers were given) would have marked a paying customer free.

Stripe and Supabase are faked; keys are real rows, so this needs a reachable
DATABASE_URL like test_smoke.py.
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
METERED_PRICE = "price_metered_test"


class _Page:
    def __init__(self, items):
        self._items = items

    def auto_paging_iter(self):
        return iter(self._items)


def _sub(sub_id, status, price, usage_type="licensed"):
    return {"id": sub_id, "status": status,
            "items": {"data": [{"price": {"id": price, "recurring": {"usage_type": usage_type}}}]}}


class _Supabase:
    def __init__(self):
        self.tiers = []

    def table(self, name):
        outer = self

        class _Q:
            def update(self, vals):
                self.vals = vals
                return self

            def eq(self, col, val):
                outer.tiers.append((val, self.vals["tier"]))
                return self

            def execute(self):
                return None
        return _Q()


@pytest.fixture
def world(monkeypatch):
    """Fake Stripe/Supabase. Fill `subs` (customer -> list) and `customers`."""
    state = {"subs": {}, "customers": {}, "email_customers": None, "event": None}
    sb = _Supabase()
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: state["event"])
    monkeypatch.setattr(stripe.Customer, "retrieve",
                        lambda cid: {"id": cid, "email": state["customers"].get(cid)})
    monkeypatch.setattr(stripe.Customer, "list", lambda email, limit: _Page(
        state["email_customers"] if state["email_customers"] is not None
        else [{"id": c} for c, e in state["customers"].items() if e == email]))
    monkeypatch.setattr(stripe.Subscription, "list",
                        lambda customer, status, limit: _Page(state["subs"].get(customer, [])))
    monkeypatch.setattr(supabase, "create_client", lambda *a: sb)
    monkeypatch.setattr(main, "refresh_subscription_gauge", lambda: None)
    monkeypatch.setattr(main, "DATA_PRICE_IDS", {DATA_PRICE})
    state["supabase"] = sb
    db = SessionLocal()
    state["db"] = db
    made = []

    def make_key(**fields):
        email = f"sub-end-{uuid.uuid4().hex[:10]}@example.com"
        key = models.APIKey(key_hash=uuid.uuid4().hex, key_prefix="sfx_" + uuid.uuid4().hex[:8],
                            email=email, free_calls=0, **fields)
        db.add(key)
        db.commit()
        made.append(key)
        return key

    state["make_key"] = make_key
    yield state
    for key in made:
        db.delete(key)
    db.commit()
    db.close()


def _end(state, customer_id, sub_id):
    state["event"] = {"type": "customer.subscription.deleted",
                      "data": {"object": {"id": sub_id, "customer": customer_id}}}
    resp = client.post("/webhook", content=b"{}", headers={"stripe-signature": "t"})
    assert resp.status_code == 200
    return resp


def test_cancelled_plan_drops_key_to_free(world):
    key = world["make_key"](monthly_allowance=15000, stripe_customer_id="cus_a1")
    world["customers"]["cus_a1"] = key.email
    world["subs"]["cus_a1"] = [_sub("sub_pro", "canceled", PRO_PRICE)]
    _end(world, "cus_a1", "sub_pro")

    world["db"].refresh(key)
    assert key.monthly_allowance == 0
    assert key.free_calls == main.FREE_WINDOW_ALLOWANCE
    assert world["supabase"].tiers == [(key.email, "free")]


def test_metered_subscription_ending_keeps_the_plan(world):
    # Annual buyers got a separate monthly metered subscription; cancelling it
    # (the planned Stripe cleanup) must not downgrade them.
    key = world["make_key"](monthly_allowance=200000, stripe_customer_id="cus_b1")
    world["customers"]["cus_b1"] = key.email
    world["subs"]["cus_b1"] = [_sub("sub_meter", "canceled", METERED_PRICE, "metered"),
                               _sub("sub_data", "active", DATA_PRICE)]
    _end(world, "cus_b1", "sub_meter")

    world["db"].refresh(key)
    assert key.monthly_allowance == 200000
    assert world["supabase"].tiers == [(key.email, "data")]


def test_falls_back_to_the_plan_still_live(world):
    key = world["make_key"](monthly_allowance=200000, stripe_customer_id="cus_c1")
    world["customers"]["cus_c1"] = key.email
    world["subs"]["cus_c1"] = [_sub("sub_data", "canceled", DATA_PRICE),
                               _sub("sub_pro", "active", PRO_PRICE)]
    _end(world, "cus_c1", "sub_data")

    world["db"].refresh(key)
    assert key.monthly_allowance == 15000
    assert world["supabase"].tiers == [(key.email, "pro")]


def test_live_plan_on_another_customer_counts(world):
    # The key's own customer (from free signup) holds the live plan; the event
    # is for a different customer, and Stripe's email search finds nothing
    # (it's case-sensitive).  The key's customer id must still be checked.
    key = world["make_key"](monthly_allowance=200000, stripe_customer_id="cus_d_free")
    world["customers"]["cus_d_checkout"] = key.email
    world["email_customers"] = []
    world["subs"]["cus_d_checkout"] = [_sub("sub_old", "canceled", DATA_PRICE)]
    world["subs"]["cus_d_free"] = [_sub("sub_live", "active", DATA_PRICE)]
    _end(world, "cus_d_checkout", "sub_old")

    world["db"].refresh(key)
    assert key.monthly_allowance == 200000


def test_unlimited_key_is_never_touched(world):
    key = world["make_key"](monthly_allowance=0, unlimited=True, stripe_customer_id="cus_e1")
    world["customers"]["cus_e1"] = key.email
    world["subs"]["cus_e1"] = [_sub("sub_pro", "canceled", PRO_PRICE)]
    _end(world, "cus_e1", "sub_pro")

    world["db"].refresh(key)
    assert key.unlimited is True
    assert key.free_calls == 0
