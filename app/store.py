"""In-memory stores for mandates, search results, and orders.

Kept in-memory on purpose for the hackathon demo: mandates and search
results are per-process state, and the audit log (see audit.py) is the
record. A restart clears them; nothing here is durable or sensitive.
"""

import uuid


class Store:
    def __init__(self):
        self.mandates = {}  # mandate_id -> mandate dict
        self.searches = {}  # mandate_id -> {product_id -> product dict}
        self.orders = {}  # order_id -> order dict

    def new_mandate(self, parsed):
        mandate_id = uuid.uuid4().hex[:12]
        mandate = {"mandate_id": mandate_id, **parsed}
        self.mandates[mandate_id] = mandate
        return mandate

    def weekly_spent(self, mandate_id):
        """Sum of approved/captured order amounts for one mandate."""
        total = 0.0
        for order in self.orders.values():
            if order["mandate_id"] == mandate_id and order["status"] in (
                "created",
                "captured",
            ):
                total += order["amount_usd"]
        return total
