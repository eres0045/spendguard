"""PayPal sandbox client — Orders v2 + capture only.

Deliberately NOT used: Payouts (limited-release) and Vault/billing
agreements (merchant-initiated charges need approval). Buyer-present
Orders v2 with approval + capture is the whole money path.

Credentials come only from the environment (PAYPAL_CLIENT_ID,
PAYPAL_CLIENT_SECRET). PAYPAL_BASE_URL defaults to the sandbox host and
must never point at the live host for this demo.
"""

import base64
import os
import time

import requests

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"


class PayPalNotConfigured(Exception):
    """PayPal client id/secret are not set."""


class PayPalError(Exception):
    """PayPal API call failed."""


def get_client():
    client_id = os.environ.get("PAYPAL_CLIENT_ID")
    secret = os.environ.get("PAYPAL_CLIENT_SECRET")
    if not client_id or not secret:
        raise PayPalNotConfigured(
            "PayPal credentials are not configured — set PAYPAL_CLIENT_ID "
            "and PAYPAL_CLIENT_SECRET"
        )
    base = os.environ.get("PAYPAL_BASE_URL", SANDBOX_BASE)
    return PayPalClient(client_id, secret, base)


class PayPalClient:
    def __init__(self, client_id, secret, base_url=SANDBOX_BASE):
        self._auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        self.base_url = base_url.rstrip("/")
        self._token = None
        self._token_expires = 0

    def _access_token(self):
        if self._token and time.time() < self._token_expires:
            return self._token
        resp = requests.post(
            f"{self.base_url}/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {self._auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise PayPalError(f"PayPal token request failed: HTTP {resp.status_code}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 3600)) - 60
        return self._token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
        }

    def create_order(self, amount_usd):
        """Create a CAPTURE order. Returns (order_id, approve_url)."""
        resp = requests.post(
            f"{self.base_url}/v2/checkout/orders",
            headers=self._headers(),
            json={
                "intent": "CAPTURE",
                "purchase_units": [
                    {"amount": {"currency_code": "USD", "value": f"{amount_usd:.2f}"}}
                ],
            },
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise PayPalError(
                f"PayPal order creation failed: HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        data = resp.json()
        approve_url = None
        for link in data.get("links", []):
            if link.get("rel") == "approve":
                approve_url = link.get("href")
        if not approve_url:
            raise PayPalError("PayPal order has no approve link")
        return data["id"], approve_url

    def capture_order(self, order_id):
        """Capture an approved order. Returns the parsed PayPal response."""
        resp = requests.post(
            f"{self.base_url}/v2/checkout/orders/{order_id}/capture",
            headers=self._headers(),
            json={},
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise PayPalError(
                f"PayPal capture failed: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()
