"""SpendGuard entrypoint.

Run from the project directory:
    python app/main.py
or:
    flask --app app.main run --port 5000

All configuration is via environment variables (see .env.example):
    CHANNEL3_API_KEY, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET,
    GROQ_API_KEY (optional), PAYPAL_BASE_URL (optional, sandbox default).
No secrets live in code or in files.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
