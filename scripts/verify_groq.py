"""One-off live check of the Groq chat path via the Muse connector surrogate.

Usage: python scripts/verify_groq.py
Prints only the model id and the model's short reply — never any key.
This proves the llm.py connector path works; it is NOT part of the pytest
suite (the surrogate only exists inside Muse's runtime).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm import _chat_via_connector, DEFAULT_MODEL


def main():
    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    text = _chat_via_connector(
        model,
        [
            {"role": "system", "content": "Reply with the single word OK."},
            {"role": "user", "content": "ping"},
        ],
    )
    if text is None:
        print("GROQ_CONNECTOR_UNAVAILABLE")
        return 1
    print(f"model={model}")
    print(f"reply={text.strip()[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
