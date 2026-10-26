"""Guardrail enforcement — the single choke point for all spending.

Every purchase path (order creation AND capture) must call check_purchase.
The guardrail compares the SERVER-side price (from the catalog search cache),
never a client-supplied price, so a crafted request cannot talk the agent
into approving an over-budget item.

Rules, in order:
  1. Price must be a finite positive number.
  2. Currency must be USD (the catalog is US-only; anything else is refused
     rather than converted — no FX guessing).
  3. Price must not exceed the mandate's per-item cap.
  4. If the mandate has a weekly cap, price + already-spent this week must
     not exceed it.
"""

import math


def check_purchase(mandate, price, currency, weekly_spent_usd=0.0):
    """Return (allowed: bool, reason: str)."""
    if price is None or not isinstance(price, (int, float)) or not math.isfinite(price):
        return False, "invalid price: no usable price for this item"
    if price <= 0:
        return False, "invalid price: price must be greater than zero"
    if (currency or "USD") != "USD":
        return False, f"non-USD price ({currency}) — refused, no FX conversion"
    cap = mandate["max_budget_usd"]
    if price > cap:
        return (
            False,
            f"over budget: ${price:.2f} exceeds per-item cap ${cap:.2f}",
        )
    weekly_cap = mandate.get("weekly_cap_usd")
    if weekly_cap is not None and weekly_spent_usd + price > weekly_cap:
        return (
            False,
            f"over weekly cap: ${weekly_spent_usd + price:.2f} would exceed "
            f"weekly cap ${weekly_cap:.2f} (already spent ${weekly_spent_usd:.2f})",
        )
    return True, "within budget"
