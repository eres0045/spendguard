"""SpendGuard Flask app.

Endpoints (all JSON):
  GET  /                 service info + endpoint list
  GET  /health           readiness flags (booleans only, never secret values)
  POST /mandate          plain-language mandate -> structured mandate
  POST /search           Channel3 catalog search for a mandate
  POST /compare          Groq comparison + numeric recommendation
  POST /checkout         guardrail check -> PayPal sandbox order (or refusal)
  POST /capture          guardrail re-check -> PayPal capture
  GET  /audit             full audit log
  GET  /audit/<id>       audit log for one mandate
  POST /webhook/paypal   PayPal webhook receiver (PAYMENT.CAPTURE.COMPLETED)

Security invariants:
  * The guardrail (guardrail.check_purchase) runs on the SERVER-side price
    from the search cache at BOTH checkout and capture. A client-supplied
    price is ignored outright.
  * Checkout only accepts products from that mandate's own search results.
  * Missing keys produce honest 503 errors, never fake data.
"""

import os

from flask import Flask, jsonify, request

from app import audit, catalog, guardrail, llm, mandate as mandate_mod, paypal as paypal_mod
from app.store import Store


def _err(status, error, detail):
    return jsonify({"error": error, "detail": detail}), status


def create_app(store=None):
    app = Flask(__name__)
    app.store = store or Store()

    @app.get("/")
    def index():
        return jsonify(
            {
                "app": "spendguard",
                "description": "Guardrail-enforced shopping agent: mandate -> catalog -> "
                "compare -> PayPal sandbox checkout, every decision logged.",
                "currency": "USD (Channel3 catalog is US-only)",
                "endpoints": [
                    "GET /health",
                    "POST /mandate",
                    "POST /search",
                    "POST /compare",
                    "POST /checkout",
                    "POST /capture",
                    "GET /audit",
                    "GET /audit/<mandate_id>",
                    "POST /webhook/paypal",
                ],
            }
        )

    @app.get("/health")
    def health():
        return jsonify(
            {
                "app": "spendguard",
                "status": "ready",
                "catalog_configured": bool(os.environ.get("CHANNEL3_API_KEY")),
                "paypal_configured": bool(
                    os.environ.get("PAYPAL_CLIENT_ID")
                    and os.environ.get("PAYPAL_CLIENT_SECRET")
                ),
                "llm_mode": (
                    "env"
                    if os.environ.get("GROQ_API_KEY")
                    else "connector-or-fallback"
                ),
            }
        )

    @app.post("/mandate")
    def create_mandate():
        body = request.get_json(silent=True) or {}
        try:
            if body.get("text"):
                parsed = mandate_mod.parse_mandate_text(body["text"])
            else:
                parsed = mandate_mod.validate_mandate_fields(
                    body.get("item"),
                    body.get("max_budget_usd"),
                    body.get("weekly_cap_usd"),
                )
        except mandate_mod.MandateError as exc:
            return _err(400, "invalid_mandate", str(exc))
        created = app.store.new_mandate(parsed)
        audit.log("mandate_created", created["mandate_id"], parsed)
        return jsonify(created), 201

    def _get_mandate(body):
        mandate_id = body.get("mandate_id")
        if not mandate_id:
            return None, _err(400, "missing_mandate_id", "mandate_id is required")
        found = app.store.mandates.get(mandate_id)
        if not found:
            return None, _err(404, "mandate_not_found", f"no mandate {mandate_id}")
        return found, None

    @app.post("/search")
    def search():
        body = request.get_json(silent=True) or {}
        found, error = _get_mandate(body)
        if error:
            return error
        query = body.get("query") or found["item"]
        try:
            products = catalog.search_catalog(query, limit=10)
        except catalog.CatalogNotConfigured as exc:
            audit.log("block", found["mandate_id"], {"reason": "catalog_not_configured", "query": query})
            return _err(503, "catalog_not_configured", str(exc))
        except catalog.CatalogError as exc:
            return _err(502, "catalog_error", str(exc))
        app.store.searches[found["mandate_id"]] = {p["id"]: p for p in products}
        audit.log("search", found["mandate_id"], {"query": query, "result_count": len(products)})
        return jsonify({"mandate_id": found["mandate_id"], "query": query, "products": products})

    @app.post("/compare")
    def compare():
        body = request.get_json(silent=True) or {}
        found, error = _get_mandate(body)
        if error:
            return error
        cached = app.store.searches.get(found["mandate_id"])
        if not cached:
            return _err(400, "search_first", "run /search for this mandate before /compare")
        products = list(cached.values())
        # Recommendation is computed numerically, never taken from LLM prose.
        in_budget = [
            p for p in products
            if p["currency"] == "USD" and p["price"] <= found["max_budget_usd"]
        ]
        recommendation = min(in_budget, key=lambda p: p["price"]) if in_budget else None
        explanation, llm_used = llm.explain_comparison(found, products)
        audit.log(
            "compare",
            found["mandate_id"],
            {
                "recommendation_id": recommendation["id"] if recommendation else None,
                "llm_used": llm_used,
                "in_budget_count": len(in_budget),
                "over_budget_count": len(products) - len(in_budget),
            },
        )
        return jsonify(
            {
                "mandate_id": found["mandate_id"],
                "explanation": explanation,
                "recommendation": recommendation,
                "llm_used": llm_used,
            }
        )

    def _refuse(mandate_id, reason, product_id=None):
        """Log both the refusal decision and the enforcement block."""
        detail = {"reason": reason, "product_id": product_id, "blocked": True}
        audit.log("refuse", mandate_id, detail)
        audit.log("block", mandate_id, detail)
        return jsonify({"refused": True, "blocked": True, "reason": reason}), 403

    @app.post("/checkout")
    def checkout():
        body = request.get_json(silent=True) or {}
        found, error = _get_mandate(body)
        if error:
            return error
        product_id = body.get("product_id")
        cached = app.store.searches.get(found["mandate_id"], {})
        product = cached.get(product_id) if product_id else None
        if product is None:
            return _err(
                404,
                "product_not_in_search",
                "product must come from this mandate's /search results — search first",
            )
        # NOTE: any client-supplied price is ignored; the server-side cached
        # price is the only price the guardrail ever sees.
        spent = app.store.weekly_spent(found["mandate_id"])
        allowed, reason = guardrail.check_purchase(
            found, product["price"], product["currency"], spent
        )
        if not allowed:
            return _refuse(found["mandate_id"], reason, product_id)
        try:
            client = paypal_mod.get_client()
        except paypal_mod.PayPalNotConfigured as exc:
            audit.log("block", found["mandate_id"], {"reason": "paypal_not_configured", "product_id": product_id})
            return _err(503, "paypal_not_configured", str(exc))
        try:
            order_id, approve_url = client.create_order(product["price"])
        except paypal_mod.PayPalError as exc:
            return _err(502, "paypal_error", str(exc))
        app.store.orders[order_id] = {
            "order_id": order_id,
            "mandate_id": found["mandate_id"],
            "product_id": product_id,
            "amount_usd": product["price"],
            "currency": "USD",
            "status": "created",
        }
        audit.log(
            "approve",
            found["mandate_id"],
            {"order_id": order_id, "product_id": product_id, "amount_usd": product["price"]},
        )
        return (
            jsonify(
                {
                    "order_id": order_id,
                    "approve_url": approve_url,
                    "amount_usd": product["price"],
                    "status": "created — buyer approval required before capture",
                }
            ),
            201,
        )

    @app.post("/capture")
    def capture():
        body = request.get_json(silent=True) or {}
        found, error = _get_mandate(body)
        if error:
            return error
        order_id = body.get("order_id")
        order = app.store.orders.get(order_id) if order_id else None
        if order is None or order["mandate_id"] != found["mandate_id"]:
            return _err(404, "order_not_found", "no such order for this mandate")
        if order["status"] == "captured":
            return _err(409, "already_captured", "this order was already captured")
        # Guardrail re-check at capture time: even if something changed since
        # checkout, an over-budget capture can never go through.
        spent_excluding = app.store.weekly_spent(found["mandate_id"]) - order["amount_usd"]
        allowed, reason = guardrail.check_purchase(
            found, order["amount_usd"], order["currency"], spent_excluding
        )
        if not allowed:
            return _refuse(found["mandate_id"], reason, order["product_id"])
        try:
            client = paypal_mod.get_client()
        except paypal_mod.PayPalNotConfigured as exc:
            audit.log("block", found["mandate_id"], {"reason": "paypal_not_configured", "order_id": order_id})
            return _err(503, "paypal_not_configured", str(exc))
        try:
            result = client.capture_order(order_id)
        except paypal_mod.PayPalError as exc:
            audit.log("capture_failed", found["mandate_id"], {"order_id": order_id, "error": str(exc)})
            return _err(502, "paypal_error", str(exc))
        order["status"] = "captured"
        audit.log(
            "capture",
            found["mandate_id"],
            {"order_id": order_id, "paypal_status": result.get("status"), "amount_usd": order["amount_usd"]},
        )
        return jsonify({"order_id": order_id, "status": "captured", "paypal": result})

    @app.get("/audit")
    def audit_all():
        return jsonify({"entries": audit.all_entries()})

    @app.get("/audit/<mandate_id>")
    def audit_one(mandate_id):
        return jsonify({"mandate_id": mandate_id, "entries": audit.for_mandate(mandate_id)})

    @app.post("/webhook/paypal")
    def webhook():
        body = request.get_json(silent=True) or {}
        event_type = body.get("event_type", "unknown")
        resource = body.get("resource") or {}
        # TODO: verify the webhook signature against PayPal's cert chain
        # (needs a webhook ID + PayPal's signature verification). Until that
        # lands, events are logged as UNVERIFIED and never trusted for
        # money movement — capture state comes from the Orders API, not
        # from webhooks.
        audit.log(
            "webhook",
            None,
            {
                "event_type": event_type,
                "resource_id": resource.get("id"),
                "verified": False,
                "note": "signature verification TODO — logged, not trusted",
            },
        )
        return jsonify({"received": True, "event_type": event_type})

    return app
