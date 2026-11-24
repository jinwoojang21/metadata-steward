"""The catalog: one YAML file per table, reviewed like code.

Every entry carries a status. The agent writes `proposed` entries only; a human promotes them
to `approved` with `steward approve`, and the agent can never overwrite an approved entry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

PII_CATEGORIES = {
    "none": "Not personal data.",
    "person_name": "First, last or full name.",
    "email": "Email address (personal or work).",
    "phone": "Phone number in any format, including integers.",
    "government_id": "SSN, TIN, passport, driver's licence — or a partial of one (e.g. last 4).",
    "date_of_birth": "Full date of birth in any encoding.",
    "street_address": "Street-level address.",
    "postal_code": "ZIP / postal code.",
    "ip_address": "IPv4/IPv6 address.",
    "device_id": "Persistent device or advertising identifier.",
    "free_text_may_contain_pii": "Free-text field where people sometimes type contact details.",
    "pseudonymized_identifier": "Hash/token derived from personal data (re-identifiable by joining).",
    "quasi_identifier": "Not identifying alone, identifying in combination (birth year, zip3, age band).",
    "sensitive_employment": "Salary, clearance, performance or similar HR-sensitive attributes.",
}
SENSITIVITY = {
    "none": "internal",
    "quasi_identifier": "confidential",
    "postal_code": "confidential",
    "pseudonymized_identifier": "confidential",
    "free_text_may_contain_pii": "restricted",
    "sensitive_employment": "restricted",
}  # everything else: restricted


def sensitivity_for(category: str) -> str:
    return SENSITIVITY.get(category, "restricted")


class ApprovedEntryError(ValueError):
    pass


class Catalog:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, table: str) -> Path:
        return self.root / f"{table}.yaml"

    def load(self, table: str) -> dict[str, Any]:
        p = self.path(table)
        if p.exists():
            return yaml.safe_load(p.read_text()) or {}
        return {"table": table, "description": None, "description_status": None, "columns": {}}

    def save(self, table: str, entry: dict[str, Any]) -> None:
        self.path(table).write_text(yaml.safe_dump(entry, sort_keys=False, allow_unicode=True, width=100))

    def tables(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.yaml"))

    # ---------------------------------------------------------------- writes from the agent
    def propose_table(self, table: str, description: str, by: str) -> None:
        e = self.load(table)
        if e.get("description_status") == "approved":
            raise ApprovedEntryError(f"{table} description is approved by a human; leave it as is")
        e.update(description=description, description_status="proposed", proposed_by=by)
        self.save(table, e)

    def propose_column(self, table: str, column: str, *, description: str, pii_category: str,
                       confidence: float, evidence: list[str], by: str) -> dict[str, Any]:
        if pii_category not in PII_CATEGORIES:
            raise ValueError(f"pii_category must be one of {sorted(PII_CATEGORIES)}")
        if not 0 <= float(confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        e = self.load(table)
        cols = e.setdefault("columns", {})
        if cols.get(column, {}).get("status") == "approved":
            raise ApprovedEntryError(f"{table}.{column} is approved by a human; leave it as is")
        cols[column] = {
            "description": description,
            "pii_category": pii_category,
            "sensitivity": sensitivity_for(pii_category),
            "confidence": round(float(confidence), 2),
            "evidence": list(evidence),
            "status": "proposed",
            "proposed_by": by,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.save(table, e)
        return cols[column]

    # ---------------------------------------------------------------- human workflow
    def approve(self, target: str, reviewer: str) -> int:
        """Approve `schema.table` (everything proposed in it) or `schema.table.column`."""
        parts = target.split(".")
        table, column = ".".join(parts[:2]), (parts[2] if len(parts) == 3 else None)
        e = self.load(table)
        n = 0
        if column is None and e.get("description_status") == "proposed":
            e["description_status"] = "approved"
            n += 1
        for name, c in e.get("columns", {}).items():
            if (column is None or name == column) and c.get("status") == "proposed":
                c["status"], c["approved_by"] = "approved", reviewer
                n += 1
        self.save(table, e)
        return n
