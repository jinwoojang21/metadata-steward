"""Read-only access to the warehouse, with masking applied to everything that leaves it."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb

from .privacy import CATEGORICAL_MAX_DISTINCT, detector_rates, mask_rows, mask_value

SCHEMAS = ("raw", "staging", "mart")
MAX_ROWS = 50
_SQL_ALLOWED = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|create|drop|alter|attach|detach|copy|export|import|install|load|pragma|"
    r"call|set|read_\w+|glob)\b", re.IGNORECASE)


class Warehouse:
    def __init__(self, path: str | Path):
        self.con = duckdb.connect(str(path), read_only=True)
        self._cols: dict[str, list[dict[str, str]]] = {}

    # ---------------------------------------------------------------- metadata
    def tables(self) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema IN ('raw','staging','mart') ORDER BY 1, 2").fetchall()
        out = []
        for s, t in rows:
            (n,) = self.con.execute(f'SELECT count(*) FROM "{s}"."{t}"').fetchone()
            out.append({"table": f"{s}.{t}", "rows": n, "columns": len(self.columns(s, t))})
        return out

    def columns(self, schema: str, table: str) -> list[dict[str, str]]:
        key = f"{schema}.{table}"
        if key not in self._cols:
            rows = self.con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position", [schema, table]
            ).fetchall()
            if not rows:
                raise ValueError(f"no such table {key!r}")
            self._cols[key] = [{"name": n, "type": t} for n, t in rows]
        return self._cols[key]

    def sqlglot_schema(self) -> dict[str, dict[str, dict[str, str]]]:
        out: dict[str, dict[str, dict[str, str]]] = {}
        for t in self.tables():
            s, n = t["table"].split(".")
            out.setdefault(s, {})[n] = {c["name"]: c["type"] for c in self.columns(s, n)}
        return out

    # ---------------------------------------------------------------- profiling
    def profile(self, fqn: str, sample_size: int = 5) -> dict[str, Any]:
        schema, table = _split(fqn)
        cols = self.columns(schema, table)
        (total,) = self.con.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()
        out = {"table": fqn, "rows": total, "columns": []}
        for c in cols:
            name = c["name"]
            q = f'"{schema}"."{table}"."{name}"'
            values = [r[0] for r in self.con.execute(
                f'SELECT {q} FROM "{schema}"."{table}" USING SAMPLE 2000 ROWS (reservoir, 42)').fetchall()]
            non_null = [v for v in values if v is not None]
            distinct = self.con.execute(
                f'SELECT count(DISTINCT {q}), count(*) FILTER (WHERE {q} IS NULL) FROM "{schema}"."{table}"'
            ).fetchone()
            col: dict[str, Any] = {
                "name": name, "type": c["type"],
                "null_rate": round(distinct[1] / total, 4) if total else 0.0,
                "distinct": distinct[0],
            }
            if distinct[0] <= CATEGORICAL_MAX_DISTINCT:
                # Low-cardinality enums cannot single out a person: show values and counts.
                col["categorical_values"] = {str(k): v for k, v in Counter(non_null).most_common()}
            else:
                seen, samples = set(), []
                for v in non_null:
                    mv = mask_value(v)
                    if str(mv) not in seen:
                        seen.add(str(mv))
                        samples.append(mv)
                    if len(samples) >= sample_size:
                        break
                col["masked_samples"] = samples
            if c["type"] in ("VARCHAR", "BIGINT", "INTEGER") or c["type"].startswith("VARCHAR"):
                rates = detector_rates(non_null)
                if rates:
                    col["detector_hit_rates"] = rates
                lengths = [len(str(v)) for v in non_null]
                if lengths:
                    col["length_min_max"] = [min(lengths), max(lengths)]
            if c["type"] in ("DOUBLE", "INTEGER", "BIGINT", "DECIMAL", "FLOAT") and non_null:
                col["min_max"] = [mask_value(min(non_null)), mask_value(max(non_null))]
            out["columns"].append(col)
        return out

    # ---------------------------------------------------------------- guarded SQL
    def query(self, sql: str) -> dict[str, Any]:
        stripped = sql.strip().rstrip(";")
        if ";" in stripped:
            raise ValueError("only a single statement is allowed")
        if not _SQL_ALLOWED.match(stripped) or _SQL_FORBIDDEN.search(stripped):
            raise ValueError("only read-only SELECT/WITH queries are allowed")
        cur = self.con.execute(f"SELECT * FROM ({stripped}) AS q LIMIT {MAX_ROWS + 1}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return {"columns": cols, "rows": mask_rows(rows[:MAX_ROWS]), "truncated": len(rows) > MAX_ROWS,
                "note": "values are masked; aggregate (count/avg/distinct) queries are most informative"}

    def raw_values(self, fqn: str, column: str, limit: int = 2000) -> list[Any]:
        """Unmasked values — for deterministic, in-process detectors only. Never sent to the model."""
        schema, table = _split(fqn)
        return [r[0] for r in self.con.execute(
            f'SELECT "{column}" FROM "{schema}"."{table}" LIMIT {int(limit)}').fetchall()]


def _split(fqn: str) -> tuple[str, str]:
    parts = fqn.split(".")
    if len(parts) != 2 or parts[0] not in SCHEMAS:
        raise ValueError(f"table must be schema.table with schema in {SCHEMAS}, got {fqn!r}")
    return parts[0], parts[1]
