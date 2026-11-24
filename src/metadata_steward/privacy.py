"""PII detectors and the masking layer.

Everything the LLM sees (profiles, samples, SQL results) passes through `mask_value`.
The model gets enough *shape* to reason about a column — "999-99-9999", "<EMAIL>",
"J**** S****", "<email>", "1987-04" — without seeing values that identify a person.

The goal is to prevent accidental exposure. A model trying hard to leak data could still
recover fragments through SQL arithmetic. See README: Threat model.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

DETECTORS: dict[str, re.Pattern] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "ssn_shape": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phone": re.compile(r"(?:\(\d{3}\)\s?|\b\d{3}[-.\s])\d{3}[-.\s]\d{4}\b|\b[2-9]\d{9}\b"),
    "ipv4": re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    "uuid": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),
    "date_yyyymmdd": re.compile(r"\b(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\b"),
    "card_number": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}
# Placeholders are lowercase and digit-free so the later masking passes leave them intact.
_TOKENS = {"email": "<email>", "uuid": "<uuid>", "ipv4": "<ip_address>", "ssn_shape": None,
           "phone": "<phone>", "date_yyyymmdd": "<yyyymmdd>", "card_number": "<card>"}
_WORD = re.compile(r"[A-Za-z]+")
CATEGORICAL_MAX_DISTINCT = 10


def _luhn(digits: str) -> bool:
    s, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        s, alt = s + d, not alt
    return s % 10 == 0


def detect(value: Any) -> set[str]:
    """Which detectors fire on a single value."""
    if value is None:
        return set()
    s = str(value)
    hits = {name for name, rx in DETECTORS.items() if name != "card_number" and rx.search(s)}
    for m in DETECTORS["card_number"].finditer(s):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            hits.add("card_number")
    return hits


def detector_rates(values: Iterable[Any]) -> dict[str, float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return {}
    counts: dict[str, int] = {}
    for v in vals:
        for h in detect(v):
            counts[h] = counts.get(h, 0) + 1
    return {k: round(c / len(vals), 4) for k, c in sorted(counts.items())}


def _mask_words(s: str) -> str:
    # Capitalised words (names, places) keep their first letter; lowercase vocabulary survives
    # so free text stays readable ("S*** invoice copy to <EMAIL>").
    return _WORD.sub(lambda m: m.group() if m.group().islower() else m.group()[0] + "*" * (len(m.group()) - 1), s)


def mask_string(s: str) -> str:
    out = s
    for name in ("email", "uuid", "ipv4", "date_yyyymmdd", "phone"):
        out = DETECTORS[name].sub(_TOKENS[name], out)
    out = DETECTORS["card_number"].sub(
        lambda m: "<card>" if _luhn(re.sub(r"\D", "", m.group())) else m.group(), out)
    out = re.sub(r"\d", "9", out)          # every remaining digit (incl. SSN-shaped) keeps only its shape
    return _mask_words(out)


def mask_value(v: Any) -> Any:
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, datetime):
        return v.strftime("%Y-%m")
    if isinstance(v, date):
        return v.strftime("%Y-%m")
    if isinstance(v, int):
        return v if abs(v) < 1_000_000 else re.sub(r"\d", "9", str(v))
    if isinstance(v, float):
        return v if abs(v) < 1e9 else round(v, -6)
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    return mask_string(str(v))


def mask_rows(rows: list[tuple], exempt_cols: set[int] | None = None) -> list[list[Any]]:
    exempt_cols = exempt_cols or set()
    return [[v if i in exempt_cols else mask_value(v) for i, v in enumerate(r)] for r in rows]
