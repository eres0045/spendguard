"""Plain-language mandate parsing.

The budget is ALWAYS extracted deterministically with regex — never from an
LLM. An LLM may misread a number and silently raise the budget; a regex
cannot. The LLM is used only for explanation text (see llm.py), never to
set, raise, or waive a budget cap.

Supported phrasings (all USD — the Channel3 catalog is US-only):
  "wireless earbuds under $60"
  "wireless earbuds under $60, max $100/week"
  "headphones, budget $80"
  "running shoes up to $120"
Explicit JSON fields (item, max_budget_usd, weekly_cap_usd) are accepted too
and validated as positive numbers.
"""

import re

_MONEY = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")
_WEEKLY = re.compile(
    r"(?:max|up\s*to|limit|budget|capped\s*at)?\s*\$?\s*(\d+(?:\.\d{1,2})?)"
    r"\s*(?:/|per\s+)?\s*week\b",
    re.IGNORECASE,
)
_BUDGET_PHRASE = re.compile(
    r"\b(?:under|below|at\s+most|up\s+to|max(?:imum)?|budget(?:\s+of)?|"
    r"less\s+than|no\s+more\s+than)\s+\$?\s*\d+(?:\.\d{1,2})?"
    r"(?:\s*(?:/|per\s+)?\s*week\b)?",
    re.IGNORECASE,
)
_WEEKLY_SPAN = re.compile(
    r"\$?\s*\d+(?:\.\d{1,2})?\s*(?:/|per\s+)?\s*week\b", re.IGNORECASE
)


class MandateError(ValueError):
    """Raised when a mandate cannot be parsed or validated."""


def parse_mandate_text(text):
    """Parse free text into {item, max_budget_usd, weekly_cap_usd}."""
    if not text or not text.strip():
        raise MandateError("mandate text is empty")
    text = text.strip()

    weekly_cap = None
    weekly_match = _WEEKLY.search(text)
    if weekly_match:
        weekly_cap = float(weekly_match.group(1))

    # Per-item cap: first dollar amount that is NOT part of a weekly phrase.
    without_weekly = _WEEKLY_SPAN.sub(" ", text)
    amounts = _MONEY.findall(without_weekly)
    if not amounts:
        raise MandateError(
            "no per-item budget found — include a cap like 'under $60'"
        )
    max_budget = float(amounts[0])

    # Item = text with all budget phrases stripped, tidied up.
    item = _BUDGET_PHRASE.sub(" ", text)
    item = _WEEKLY_SPAN.sub(" ", item)
    item = _MONEY.sub(" ", item)
    item = re.sub(r"\s+", " ", item)
    item = re.sub(r"\s+,", ",", item)
    item = re.sub(r",+", ",", item)
    item = item.strip(" ,;:.").strip()
    if not item:
        raise MandateError("no item found — describe what to buy")
    if max_budget <= 0:
        raise MandateError("budget must be greater than zero")
    if weekly_cap is not None and weekly_cap <= 0:
        raise MandateError("weekly cap must be greater than zero")
    return {
        "item": item,
        "max_budget_usd": max_budget,
        "weekly_cap_usd": weekly_cap,
    }


def validate_mandate_fields(item, max_budget_usd, weekly_cap_usd=None):
    """Validate explicit JSON mandate fields."""
    if not item or not str(item).strip():
        raise MandateError("item is required")
    try:
        budget = float(max_budget_usd)
    except (TypeError, ValueError):
        raise MandateError("max_budget_usd must be a number")
    if budget <= 0:
        raise MandateError("max_budget_usd must be greater than zero")
    weekly = None
    if weekly_cap_usd is not None:
        try:
            weekly = float(weekly_cap_usd)
        except (TypeError, ValueError):
            raise MandateError("weekly_cap_usd must be a number")
        if weekly <= 0:
            raise MandateError("weekly_cap_usd must be greater than zero")
    return {
        "item": str(item).strip(),
        "max_budget_usd": budget,
        "weekly_cap_usd": weekly,
    }
