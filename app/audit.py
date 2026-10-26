"""In-memory audit log for SpendGuard.

Every agent decision (mandate, search, compare, approve, refuse, block,
capture, webhook) is recorded here with a UTC timestamp. The log is
intentionally in-memory: it is a demo/audit trail, not a durable ledger —
a process restart clears it. Nothing sensitive is ever logged (no API keys,
no secrets, no full credentials).
"""

import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()
_ENTRIES = []


def log(event, mandate_id=None, detail=None):
    """Append one audit entry and return it."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event,
        "mandate_id": mandate_id,
        "detail": detail or {},
    }
    with _LOCK:
        _ENTRIES.append(entry)
    return entry


def all_entries():
    with _LOCK:
        return list(_ENTRIES)


def for_mandate(mandate_id):
    with _LOCK:
        return [e for e in _ENTRIES if e.get("mandate_id") == mandate_id]


def reset():
    """Test helper: clear the log."""
    with _LOCK:
        _ENTRIES.clear()
