"""Salary extraction from free-text job descriptions.

Why deterministic regex instead of asking the LLM: the LLM call is the
slowest and priciest part of the pipeline. Salaries follow a small,
well-known set of textual patterns, so we can parse them in microseconds
without a model round-trip. The LLM scorer still gets the description
unchanged and can override our parse if it wants to.

Returns: (salary_text, salary_min, salary_max) where the integers are
whole dollars per year. Min/max are None if not confidently parsed.
"""
from __future__ import annotations

import re


# Match a money figure: optional $, optional K/k for thousands, with commas.
# Examples: "$120,000", "120K", "$120k", "150,000.00"
_NUM = r"\$?\s*([0-9]{1,3}(?:[,.][0-9]{3})*(?:\.[0-9]+)?|[0-9]+)\s*([kKmM])?"

# Range patterns: "$120K-$150K", "$120,000 to $150,000", "120K - 150K"
_RANGE = re.compile(
    rf"{_NUM}\s*(?:to|[-–—])\s*{_NUM}",
    re.IGNORECASE,
)

# Single salary patterns: "starting at $120K", "salary: $150,000"
_SINGLE = re.compile(
    r"(?:salary|compensation|pay|base|annual|starting at|up to)\s*[:;is\s]*"
    rf"{_NUM}",
    re.IGNORECASE,
)

# Disqualify obviously-not-salary numbers: phone numbers, years, zip codes,
# percentages, "401k" matching the K, etc.
_NEGATIVE_CONTEXT = re.compile(
    r"(401\s*k|403\s*b|529|zip|phone|year[s]?\s+experience|%|percent|"
    r"customers|users|members)",
    re.IGNORECASE,
)


def _to_dollars(num: str, suffix: str | None) -> int | None:
    """Normalize a captured number to whole dollars per year."""
    try:
        # Strip commas. If a single dot is present and followed by exactly 3
        # digits we treat it as a thousands separator ("120.000"); otherwise
        # it's a decimal point ("120.50").
        n = num.replace(",", "")
        if "." in n:
            head, tail = n.rsplit(".", 1)
            if len(tail) == 3 and tail.isdigit():
                n = head + tail
        val = float(n)
    except ValueError:
        return None

    if suffix and suffix.lower() == "k":
        val *= 1_000
    elif suffix and suffix.lower() == "m":
        val *= 1_000_000

    # Filter implausible values: anything below $20K or above $2M for a
    # yearly base is almost certainly not the salary we're after.
    if val < 20_000 or val > 2_000_000:
        return None
    return int(val)


def extract_salary(description: str) -> tuple[str, int | None, int | None]:
    """Return (text, min, max) for the most prominent salary in the description.

    "text" is the raw matched substring so the dashboard can show whatever
    the JD actually wrote. min/max are whole-dollar annual figures, or None.
    """
    if not description:
        return "", None, None

    # Limit to first 4K chars so we don't waste time on description footers
    # that often contain boilerplate with stray numbers.
    haystack = description[:4000]

    # 1) try range first since it's more informative
    for m in _RANGE.finditer(haystack):
        span_start = max(0, m.start() - 40)
        context = haystack[span_start:m.end() + 40]
        if _NEGATIVE_CONTEXT.search(context):
            continue
        lo = _to_dollars(m.group(1), m.group(2))
        hi = _to_dollars(m.group(3), m.group(4))
        if lo and hi and lo <= hi:
            text = m.group(0).strip()
            return text, lo, hi

    # 2) fall back to single salary mentions
    for m in _SINGLE.finditer(haystack):
        span_start = max(0, m.start() - 40)
        context = haystack[span_start:m.end() + 40]
        if _NEGATIVE_CONTEXT.search(context):
            continue
        val = _to_dollars(m.group(1), m.group(2))
        if val:
            text = m.group(0).strip()
            return text, val, val

    return "", None, None
