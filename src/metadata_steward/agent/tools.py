"""Tools for the steward agent. All warehouse reads go through the masking layer."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..catalog import PII_CATEGORIES, ApprovedEntryError, Catalog
from ..lineage import LineageGraph
from ..models import Model
from ..warehouse import Warehouse

_PII_HELP = "; ".join(f"{k}: {v}" for k, v in PII_CATEGORIES.items())

READ_TOOLS: list[dict[str, Any]] = [
    {"name": "list_tables", "description": "All tables in raw, staging and mart with row/column counts.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "profile_table",
     "description": "Column profile: type, null rate, distinct count, regex detector hit rates (computed on "
                    "real values in-database), and MASKED samples. Digits are shown as 9, capitalised words "
                    "keep only their first letter, emails/phones/IPs become <email>/<phone>/<ip_address>. "
                    "Columns with <=10 distinct values show real values (enums).",
     "input_schema": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}},
    {"name": "get_model_sql",
     "description": "SQL (and author comment) that builds a staging/mart table. raw tables have none.",
     "input_schema": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}},
    {"name": "get_lineage",
     "description": "Column-level lineage parsed from model SQL. direction='upstream' lists sources, "
                    "'downstream' lists dependants; each hop includes the SQL expression used.",
     "input_schema": {"type": "object", "properties": {
         "column": {"type": "string", "description": "schema.table.column"},
         "direction": {"type": "string", "enum": ["upstream", "downstream"]},
         "max_depth": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10}},
         "required": ["column", "direction"]}},
    {"name": "get_catalog_entry",
     "description": "Current catalog entry for a table (descriptions + PII classifications, with status). "
                    "Use it to stay consistent with upstream tables you already documented.",
     "input_schema": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}},
    {"name": "run_sql",
     "description": "One read-only SELECT/WITH query, max 50 rows; string/date results are masked. "
                    "Aggregates are most useful, e.g. count(DISTINCT split_part(x,'-',1)), "
                    "avg(length(x)), count(*) FILTER (WHERE x LIKE '%@%').",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]

DOC_TOOLS: list[dict[str, Any]] = READ_TOOLS + [
    {"name": "propose_column",
     "description": "Write a PROPOSED catalog entry for one column (a human approves later).",
     "input_schema": {"type": "object", "properties": {
         "column": {"type": "string", "description": "column name in the table being documented"},
         "description": {"type": "string", "description": "1-2 sentences, business meaning first."},
         "pii_category": {"type": "string", "enum": sorted(PII_CATEGORIES), "description": _PII_HELP},
         "confidence": {"type": "number", "minimum": 0, "maximum": 1},
         "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
         "required": ["column", "description", "pii_category", "confidence", "evidence"]}},
    {"name": "propose_table_description",
     "description": "Write a PROPOSED description for the table being documented.",
     "input_schema": {"type": "object", "properties": {"description": {"type": "string"}},
                      "required": ["description"]}},
    {"name": "finish_table",
     "description": "Call when every column has a proposal. Fails if any column is missing.",
     "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}},
                      "required": ["summary"]}},
]

QA_TOOLS: list[dict[str, Any]] = READ_TOOLS + [
    {"name": "submit_answer",
     "description": "Final answer. `columns` must list every fully-qualified column (schema.table.column) "
                    "the answer refers to, and nothing else.",
     "input_schema": {"type": "object", "properties": {
         "answer": {"type": "string"},
         "columns": {"type": "array", "items": {"type": "string"}}},
         "required": ["answer", "columns"]}},
]


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    ok: bool
    ms: int


@dataclass
class Toolbox:
    wh: Warehouse
    lineage: LineageGraph
    models: dict[str, Model]
    catalog: Catalog
    agent_name: str
    table: str | None = None              # table being documented (doc mode)
    trace: list[ToolCall] = field(default_factory=list)
    done: dict[str, Any] | None = None    # set by the terminal tool
    transcript: list[str] = field(default_factory=list)  # every string returned to the model

    def call(self, name: str, args: dict[str, Any] | None) -> tuple[str, bool]:
        args = args or {}
        fn: Callable[..., Any] | None = getattr(self, f"t_{name}", None)
        start = time.perf_counter()
        if fn is None:
            out, ok = {"error": f"unknown tool {name!r}"}, False
        else:
            try:
                out, ok = fn(**args), True
            except Exception as exc:  # noqa: BLE001 — errors go back to the model
                out, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
        text = json.dumps(out, default=str)
        self.trace.append(ToolCall(name, args, ok, int((time.perf_counter() - start) * 1000)))
        self.transcript.append(text)
        return text, not ok

    # ---------------------------------------------------------------- read tools
    def t_list_tables(self) -> dict[str, Any]:
        return {"tables": self.wh.tables()}

    def t_profile_table(self, table: str) -> dict[str, Any]:
        return self.wh.profile(table)

    def t_get_model_sql(self, table: str) -> dict[str, Any]:
        m = self.models.get(table)
        if not m:
            return {"table": table, "sql": None, "note": "raw source table; not built from SQL"}
        return {"table": table, "sql": m.sql}

    def t_get_lineage(self, column: str, direction: str, max_depth: int = 10) -> dict[str, Any]:
        if column.count(".") != 2:
            raise ValueError("column must be schema.table.column")
        fn = self.lineage.upstream if direction == "upstream" else self.lineage.downstream
        d = self.lineage.derivations.get(column)
        return {"column": column, "derived_as": d.expression if d else "(source column)",
                direction: fn(column, max_depth)}

    def t_get_catalog_entry(self, table: str) -> dict[str, Any]:
        e = self.catalog.load(table)
        for c in e.get("columns", {}).values():
            c.pop("updated_at", None)
        return e

    def t_run_sql(self, query: str) -> dict[str, Any]:
        return self.wh.query(query)

    # ---------------------------------------------------------------- doc tools
    def _require_table(self) -> str:
        if not self.table:
            raise ValueError("not documenting a table in this session")
        return self.table

    def t_propose_column(self, column: str, description: str, pii_category: str, confidence: float,
                         evidence: list[str]) -> dict[str, Any]:
        table = self._require_table()
        names = {c["name"] for c in self.wh.columns(*table.split("."))}
        if column not in names:
            raise ValueError(f"{table} has no column {column!r}; columns: {sorted(names)}")
        try:
            entry = self.catalog.propose_column(table, column, description=description,
                                                pii_category=pii_category, confidence=confidence,
                                                evidence=evidence, by=self.agent_name)
        except ApprovedEntryError as e:
            return {"skipped": str(e)}
        return {"saved": f"{table}.{column}", "sensitivity": entry["sensitivity"]}

    def t_propose_table_description(self, description: str) -> dict[str, Any]:
        table = self._require_table()
        try:
            self.catalog.propose_table(table, description, by=self.agent_name)
        except ApprovedEntryError as e:
            return {"skipped": str(e)}
        return {"saved": table}

    def t_finish_table(self, summary: str) -> dict[str, Any]:
        table = self._require_table()
        names = [c["name"] for c in self.wh.columns(*table.split("."))]
        have = self.catalog.load(table).get("columns", {})
        missing = [n for n in names if n not in have]
        if missing:
            raise ValueError(f"no proposal yet for: {missing}")
        self.done = {"summary": summary}
        return {"finished": table}

    def t_submit_answer(self, answer: str, columns: list[str]) -> dict[str, Any]:
        if not isinstance(columns, list):
            raise TypeError("columns must be a list")
        self.done = {"answer": answer, "columns": sorted(set(columns))}
        return {"accepted": True}
