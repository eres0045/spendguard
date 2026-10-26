"""Channel3 catalog search (US-only catalog, USD-denominated).

Honesty rule: if CHANNEL3_API_KEY is absent, search fails with an explicit
"not configured" error — the app NEVER invents catalog data or falls back
to a fake seeded catalog presented as live.

Environment quirk (sandbox VM only): the VM's egress proxy list contains
entries that break httpx's default client construction (it chokes parsing
NO_PROXY entries). The Channel3 SDK accepts an httpx_client, so we pass one
built with trust_env=False. On Vercel this is harmless.
"""

import os

import httpx


class CatalogNotConfigured(Exception):
    """CHANNEL3_API_KEY is not set."""


class CatalogError(Exception):
    """Channel3 call failed (network, auth, or API error)."""


def _client():
    from channel3_sdk import Channel3

    return Channel3(
        api_key=os.environ["CHANNEL3_API_KEY"],
        httpx_client=httpx.Client(trust_env=False, timeout=30.0),
    )


def _brand_name(product):
    brands = getattr(product, "brands", None) or []
    if not brands:
        return None
    return getattr(brands[0], "name", None)


def _normalize(product):
    """One Channel3 product -> plain dict with the first priced offer."""
    offers = getattr(product, "offers", None) or []
    chosen = None
    for offer in offers:
        price = getattr(offer, "price", None)
        if price is not None and getattr(price, "price", None) is not None:
            chosen = offer
            break
    if chosen is None:
        return None
    try:
        amount = float(chosen.price.price)
    except (TypeError, ValueError):
        return None
    return {
        "id": str(getattr(product, "id", "")),
        "title": getattr(product, "title", "") or "",
        "brand": _brand_name(product),
        "price": amount,
        "currency": getattr(chosen.price, "currency", None) or "USD",
        "url": getattr(chosen, "url", None),
        "domain": getattr(chosen, "domain", None),
        "availability": getattr(chosen, "availability", None),
    }


def search_catalog(query, limit=10):
    """Search Channel3 and return normalized products (never fake data)."""
    api_key = os.environ.get("CHANNEL3_API_KEY")
    if not api_key:
        raise CatalogNotConfigured(
            "Channel3 API key is not configured — set CHANNEL3_API_KEY"
        )
    try:
        client = _client()
        pager = client.products.search(query=query, limit=limit)
        products = []
        for product in pager:
            item = _normalize(product)
            if item is not None:
                products.append(item)
            if len(products) >= limit:
                break
        return products
    except CatalogNotConfigured:
        raise
    except Exception as exc:  # SDK/network/auth errors surface honestly
        raise CatalogError(f"Channel3 search failed: {exc}") from exc
