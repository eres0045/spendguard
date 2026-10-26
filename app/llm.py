"""Groq LLM comparison/explanation.

Two credential paths, in order:
  1. GROQ_API_KEY env var (the deploy path — Vercel env). Used as a Bearer
     token directly against api.groq.com.
  2. The Muse connector surrogate (local verification only): the vault-stored
     Groq key is injected by the runtime for registered host api.groq.com.
     This path only works inside Muse's runtime; it is NOT available on
     Vercel, which is why path 1 exists.

If neither works, explain_comparison returns a deterministic, template-built
comparison with llm_used=False — never text presented as LLM output when it
is not. The recommendation itself is always computed numerically by the
caller (cheapest within budget), never taken from LLM prose.
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = "openai/gpt-oss-20b"
_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_USER_AGENT = "SpendGuard/1.0 (PayPal AI Hackathon demo)"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused")


def _chat_via_key(api_key, model, messages):
    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": 0.3}
    ).encode("utf-8")
    request = urllib.request.Request(
        _API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _chat_via_connector(model, messages):
    """Surrogate path: works only inside Muse's runtime. Returns None when
    the connector machinery is unavailable (e.g. on Vercel)."""
    try:
        import sys

        sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
        from dynamic_credentials import add_surrogate_to_request
    except ImportError:
        return None
    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": 0.3}
    ).encode("utf-8")
    request = urllib.request.Request(
        _API_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    add_surrogate_to_request(request, "custom.groq", allowed_hosts=["api.groq.com"])
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _fallback_text(mandate, products):
    lines = [
        f"Comparison for '{mandate['item']}' (budget ${mandate['max_budget_usd']:.2f})."
    ]
    in_budget = [p for p in products if p["currency"] == "USD" and p["price"] <= mandate["max_budget_usd"]]
    if not in_budget:
        lines.append("No product in this list is within budget — buying any of them would be refused.")
    else:
        cheapest = min(in_budget, key=lambda p: p["price"])
        lines.append(f"Cheapest within budget: {cheapest['title']} at ${cheapest['price']:.2f}.")
        for p in sorted(products, key=lambda p: p["price"]):
            flag = "within budget" if p["currency"] == "USD" and p["price"] <= mandate["max_budget_usd"] else "over budget"
            lines.append(f"- {p['title']} (${p['price']:.2f}) — {flag}.")
    lines.append("(Template comparison — the LLM was unavailable, so no AI text is shown.)")
    return "\n".join(lines)


def explain_comparison(mandate, products):
    """Return (explanation_text, llm_used: bool)."""
    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    product_lines = "\n".join(
        f"- {p['title']} ({p.get('brand') or 'unknown brand'}): "
        f"${p['price']:.2f} {p['currency']}"
        for p in products
    )
    messages = [
        {
            "role": "system",
            "content": "You are SpendGuard, a shopping guardrail agent. Compare products "
            "against a budget in plain language, briefly. Never recommend "
            "an over-budget item. Keep it under 120 words.",
        },
        {
            "role": "user",
            "content": f"Mandate: {mandate['item']}, max ${mandate['max_budget_usd']:.2f} per item.\n"
            f"Products:\n{product_lines}\n"
            "Compare them and say which (if any) fits the budget best, and why.",
        },
    ]
    try:
        api_key = os.environ.get("GROQ_API_KEY")
        if api_key:
            return _chat_via_key(api_key, model, messages), True
        text = _chat_via_connector(model, messages)
        if text:
            return text, True
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, KeyError, IndexError):
        pass
    return _fallback_text(mandate, products), False
