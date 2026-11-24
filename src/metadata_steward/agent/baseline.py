"""Rule-based baseline: column-name patterns + regex detector rates.

This is roughly what many scanners do. It goes through the same Toolbox as the Claude agent,
so the eval compares judgment, not plumbing.
"""

from __future__ import annotations

import json
import re

from .tools import Toolbox

NAME_RULES: list[tuple[str, str]] = [
    (r"hash", "pseudonymized_identifier"),
    (r"(^|_)(ssn|tax_id|tin)(_|$)", "government_id"),
    (r"e?ma?i?l|eml", "email"),
    (r"(^|_)(ph|phone|tel|mobile)(_|$)|phone", "phone"),
    (r"dob|birth", "date_of_birth"),
    (r"(^|_)(zip|zip\d|postal)(_|$)|zip", "postal_code"),
    (r"addr|street", "street_address"),
    (r"(^|_)ip(_|$)|ip_addr", "ip_address"),
    (r"device", "device_id"),
    (r"(^|_)(nm|name)(_|$)|_name$|_nm$", "person_name"),
]
DETECTOR_RULES = {
    "email": "email", "phone": "phone", "ssn_shape": "government_id", "ipv4": "ip_address",
    "uuid": "device_id",
}


def classify(name: str, detector_rates: dict[str, float]) -> tuple[str, list[str]]:
    for det, cat in DETECTOR_RULES.items():
        if detector_rates.get(det, 0) >= 0.5:
            return cat, [f"detector {det} matched {detector_rates[det]:.0%} of values"]
    for pattern, cat in NAME_RULES:
        if re.search(pattern, name, re.IGNORECASE):
            return cat, [f"column name matches /{pattern}/"]
    return "none", ["no detector or name rule matched"]


def run_baseline_table(tb: Toolbox, table: str) -> dict:
    prof = json.loads(tb.call("profile_table", {"table": table})[0])
    for c in prof["columns"]:
        cat, ev = classify(c["name"], c.get("detector_hit_rates", {}))
        tb.call("propose_column", {
            "column": c["name"], "description": f"{c['name']} ({c['type']}).",
            "pii_category": cat, "confidence": 0.6 if cat != "none" else 0.5, "evidence": ev})
    tb.call("propose_table_description", {"description": f"{table} ({prof['rows']} rows)."})
    tb.call("finish_table", {"summary": "rule-based baseline"})
    return {"turns": 0, "input_tokens": 0, "output_tokens": 0, "completed": tb.done is not None}
