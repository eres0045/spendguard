"""Vercel serverless entrypoint for the SpendGuard Flask app.

Vercel maps files under api/ to serverless functions; vercel.json rewrites
all paths to this function so the Flask routes (/mandate, /search, /compare,
/checkout, /capture, /audit, /webhook/paypal, /health) serve at the root.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

app = create_app()
