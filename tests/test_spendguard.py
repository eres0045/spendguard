"""SpendGuard tests — guardrail, audit logging, mandate parsing, honest
no-key errors. No test touches the network or any real credential: PayPal
and Channel3 are monkeypatched, and Groq is either patched or forced to
its deterministic fallback. Run from the project directory:

    .venv/bin/python -m pytest tests/ -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import audit, create_app
from app import catalog as catalog_mod
from app import llm as llm_mod
from app import paypal as paypal_mod
from app.guardrail import check_purchase
from app.mandate import parse_mandate_text, validate_mandate_fields, MandateError
from app.store import Store


@pytest.fixture()
def client(monkeypatch):
    for var in ("CHANNEL3_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    audit.reset()
    app = create_app(Store())
    app.config["TESTING"] = True
    return app.test_client()


def _mandate(client, text="wireless earbuds under $60"):
    resp = client.post("/mandate", json={"text": text})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _fake_products():
    return [
        {"id": "p-cheap", "title": "Buds Lite", "brand": "Acme", "price": 49.99,
         "currency": "USD", "url": "https://example.com/lite", "domain": "example.com",
         "availability": "in_stock"},
        {"id": "p-expensive", "title": "Buds Pro", "brand": "Acme", "price": 89.99,
         "currency": "USD", "url": "https://example.com/pro", "domain": "example.com",
         "availability": "in_stock"},
        {"id": "p-euro", "title": "Buds EU", "brand": "Acme", "price": 40.00,
         "currency": "EUR", "url": "https://example.com/eu", "domain": "example.com",
         "availability": "in_stock"},
    ]


def _search(client, monkeypatch, mandate_id):
    monkeypatch.setattr(catalog_mod, "search_catalog", lambda q, limit=10: _fake_products())
    resp = client.post("/search", json={"mandate_id": mandate_id})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


class FakePayPal:
    def create_order(self, amount):
        return ("ORDER-1", "https://www.sandbox.paypal.com/checkoutnow?token=ORDER-1")

    def capture_order(self, order_id):
        return {"status": "COMPLETED", "id": order_id}


# ---- health / mandate parsing ----

def test_health_flags_no_keys(client):
    data = client.get("/health").get_json()
    assert data["status"] == "ready"
    assert data["catalog_configured"] is False
    assert data["paypal_configured"] is False


def test_mandate_text_parsing():
    m = parse_mandate_text("wireless earbuds under $60")
    assert m["item"] == "wireless earbuds"
    assert m["max_budget_usd"] == 60.0
    assert m["weekly_cap_usd"] is None


def test_mandate_text_with_weekly_cap():
    m = parse_mandate_text("wireless earbuds under $60, only from these stores, max $100/week")
    assert "earbuds" in m["item"]
    assert m["max_budget_usd"] == 60.0
    assert m["weekly_cap_usd"] == 100.0


def test_mandate_explicit_fields():
    m = validate_mandate_fields("headphones", 80, 200)
    assert m == {"item": "headphones", "max_budget_usd": 80.0, "weekly_cap_usd": 200.0}


def test_mandate_invalid(client):
    assert client.post("/mandate", json={"text": "wireless earbuds"}).status_code == 400
    assert client.post("/mandate", json={"text": ""}).status_code == 400
    assert client.post("/mandate", json={"item": "x", "max_budget_usd": -5}).status_code == 400
    with pytest.raises(MandateError):
        parse_mandate_text("just some words, no budget")


# ---- catalog: honest no-key errors ----

def test_search_no_key_is_honest_503(client):
    m = _mandate(client)
    resp = client.post("/search", json={"mandate_id": m["mandate_id"]})
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "catalog_not_configured"
    events = [e["event"] for e in client.get("/audit").get_json()["entries"]]
    assert "block" in events


def test_search_mocked_caches_products(client, monkeypatch):
    m = _mandate(client)
    data = _search(client, monkeypatch, m["mandate_id"])
    assert len(data["products"]) == 3
    assert data["products"][0]["title"] == "Buds Lite"


# ---- compare ----

def test_compare_fallback_and_numeric_recommendation(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    monkeypatch.setattr(llm_mod, "_chat_via_connector", lambda model, messages: None)
    resp = client.post("/compare", json={"mandate_id": m["mandate_id"]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["llm_used"] is False
    assert "Template comparison" in data["explanation"]
    assert data["recommendation"]["id"] == "p-cheap"  # cheapest within $60


def test_compare_all_over_budget_recommends_none(client, monkeypatch):
    m = _mandate(client, "wireless earbuds under $10")
    _search(client, monkeypatch, m["mandate_id"])
    monkeypatch.setattr(llm_mod, "_chat_via_connector", lambda model, messages: None)
    data = client.post("/compare", json={"mandate_id": m["mandate_id"]}).get_json()
    assert data["recommendation"] is None
    assert data["llm_used"] is False


def test_compare_requires_search_first(client):
    m = _mandate(client)
    assert client.post("/compare", json={"mandate_id": m["mandate_id"]}).status_code == 400


def test_compare_llm_path_wiring(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    monkeypatch.setattr(llm_mod, "_chat_via_connector", lambda model, messages: "AI says: buy the cheap one.")
    data = client.post("/compare", json={"mandate_id": m["mandate_id"]}).get_json()
    assert data["llm_used"] is True
    assert "AI says" in data["explanation"]


# ---- guardrail unit ----

def test_guardrail_unit():
    mandate = {"max_budget_usd": 60.0, "weekly_cap_usd": 100.0}
    assert check_purchase(mandate, 49.99, "USD")[0] is True
    assert check_purchase(mandate, 60.00, "USD")[0] is True  # exactly at cap: allowed
    allowed, reason = check_purchase(mandate, 60.01, "USD")
    assert allowed is False and "over budget" in reason
    allowed, reason = check_purchase(mandate, 40.00, "EUR")
    assert allowed is False and "non-USD" in reason
    allowed, reason = check_purchase(mandate, 60.00, "USD", weekly_spent_usd=50.0)
    assert allowed is False and "weekly cap" in reason
    assert check_purchase(mandate, 0, "USD")[0] is False


# ---- checkout: the guardrail centerpiece ----

def test_checkout_over_budget_always_refused(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    # Attack: client claims the $89.99 item costs $1.00 — server must ignore it.
    resp = client.post("/checkout", json={
        "mandate_id": m["mandate_id"], "product_id": "p-expensive", "price": 1.00})
    assert resp.status_code == 403
    data = resp.get_json()
    assert data["refused"] is True and data["blocked"] is True
    assert "over budget" in data["reason"]
    events = [e["event"] for e in client.get(f"/audit/{m['mandate_id']}").get_json()["entries"]]
    assert "refuse" in events and "block" in events
    assert "approve" not in events


def test_checkout_non_usd_refused(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    resp = client.post("/checkout", json={"mandate_id": m["mandate_id"], "product_id": "p-euro"})
    assert resp.status_code == 403
    assert "non-USD" in resp.get_json()["reason"]


def test_checkout_product_not_in_search(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    resp = client.post("/checkout", json={"mandate_id": m["mandate_id"], "product_id": "p-unknown"})
    assert resp.status_code == 404


def test_checkout_under_budget_no_paypal_is_honest_503(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    resp = client.post("/checkout", json={"mandate_id": m["mandate_id"], "product_id": "p-cheap"})
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "paypal_not_configured"
    events = [e["event"] for e in client.get(f"/audit/{m['mandate_id']}").get_json()["entries"]]
    assert "block" in events and "approve" not in events


def test_checkout_under_budget_mocked_paypal(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    monkeypatch.setattr(paypal_mod, "get_client", lambda: FakePayPal())
    resp = client.post("/checkout", json={"mandate_id": m["mandate_id"], "product_id": "p-cheap"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["order_id"] == "ORDER-1"
    assert "sandbox.paypal.com" in data["approve_url"]
    events = [e["event"] for e in client.get(f"/audit/{m['mandate_id']}").get_json()["entries"]]
    assert "approve" in events


def test_weekly_cap_blocks_second_purchase(client, monkeypatch):
    m = _mandate(client, "wireless earbuds under $100, max $100/week")
    _search(client, monkeypatch, m["mandate_id"])
    monkeypatch.setattr(paypal_mod, "get_client", lambda: FakePayPal())
    assert client.post("/checkout", json={"mandate_id": m["mandate_id"], "product_id": "p-cheap"}).status_code == 201
    # $49.99 spent this week; the $89.99 item fits the per-item cap but would
    # push the week to $139.98 — the weekly guardrail must refuse it.
    resp = client.post("/checkout", json={"mandate_id": m["mandate_id"], "product_id": "p-expensive"})
    assert resp.status_code == 403
    assert "weekly cap" in resp.get_json()["reason"]


# ---- capture ----

def test_capture_mocked_and_double_capture_rejected(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    monkeypatch.setattr(paypal_mod, "get_client", lambda: FakePayPal())
    client.post("/checkout", json={"mandate_id": m["mandate_id"], "product_id": "p-cheap"})
    resp = client.post("/capture", json={"mandate_id": m["mandate_id"], "order_id": "ORDER-1"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "captured"
    events = [e["event"] for e in client.get(f"/audit/{m['mandate_id']}").get_json()["entries"]]
    assert "capture" in events
    assert client.post("/capture", json={"mandate_id": m["mandate_id"], "order_id": "ORDER-1"}).status_code == 409


def test_capture_rerefuses_if_amount_now_over_budget(monkeypatch):
    # Defense-in-depth: if a stored order's amount somehow exceeds the mandate
    # (tampering/drift between checkout and capture), capture must refuse.
    audit.reset()
    for var in ("CHANNEL3_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    app = create_app(Store())
    app.config["TESTING"] = True
    c = app.test_client()
    m = c.post("/mandate", json={"text": "wireless earbuds under $60"}).get_json()
    monkeypatch.setattr(catalog_mod, "search_catalog", lambda q, limit=10: _fake_products())
    assert c.post("/search", json={"mandate_id": m["mandate_id"]}).status_code == 200
    monkeypatch.setattr(paypal_mod, "get_client", lambda: FakePayPal())
    assert c.post("/checkout", json={"mandate_id": m["mandate_id"], "product_id": "p-cheap"}).status_code == 201
    app.store.orders["ORDER-1"]["amount_usd"] = 89.99  # tampered
    resp = c.post("/capture", json={"mandate_id": m["mandate_id"], "order_id": "ORDER-1"})
    assert resp.status_code == 403
    assert "over budget" in resp.get_json()["reason"]


# ---- webhook + audit ----

def test_webhook_logged_unverified(client):
    resp = client.post("/webhook/paypal", json={
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAP-123"}})
    assert resp.status_code == 200
    entries = client.get("/audit").get_json()["entries"]
    webhooks = [e for e in entries if e["event"] == "webhook"]
    assert len(webhooks) == 1
    assert webhooks[0]["detail"]["event_type"] == "PAYMENT.CAPTURE.COMPLETED"
    assert webhooks[0]["detail"]["verified"] is False


def test_audit_entries_have_timestamps(client, monkeypatch):
    m = _mandate(client)
    _search(client, monkeypatch, m["mandate_id"])
    entries = client.get("/audit").get_json()["entries"]
    assert all(e["timestamp"] for e in entries)
    assert all(e["event"] in ("mandate_created", "search") for e in entries)
