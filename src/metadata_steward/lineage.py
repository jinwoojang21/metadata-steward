"""Column-level lineage parsed from model SQL with sqlglot.

Each model output column gets edges to the upstream columns it reads, plus the SQL expression
that derives it — so an agent (or a reviewer) can tell `email` from `sha256(email)`.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from sqlglot import exp
from sqlglot.lineage import lineage as sg_lineage

from .models import Model
from .warehouse import Warehouse


@dataclass
class ColumnDerivation:
    column: str                 # schema.table.column
    expression: str             # SQL that produces it
    sources: list[str]          # upstream schema.table.column
    passthrough: bool           # True if the value is copied unchanged (possibly renamed)


@dataclass
class LineageGraph:
    derivations: dict[str, ColumnDerivation] = field(default_factory=dict)
    downstream_edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(self, d: ColumnDerivation) -> None:
        self.derivations[d.column] = d
        for s in d.sources:
            self.downstream_edges[s].add(d.column)

    def upstream(self, column: str, max_depth: int = 10) -> list[dict]:
        return self._walk(column, max_depth, lambda c: self.derivations[c].sources
                          if c in self.derivations else [])

    def downstream(self, column: str, max_depth: int = 10) -> list[dict]:
        return self._walk(column, max_depth, lambda c: sorted(self.downstream_edges.get(c, ())))

    def _walk(self, start: str, max_depth: int, nxt) -> list[dict]:
        out, seen, q = [], {start}, deque([(start, 0)])
        while q:
            col, depth = q.popleft()
            if depth >= max_depth:
                continue
            for n in nxt(col):
                if n in seen:
                    continue
                seen.add(n)
                d = self.derivations.get(n) or self.derivations.get(col)
                out.append({"column": n, "depth": depth + 1,
                            "via": d.expression if d else None})
                q.append((n, depth + 1))
        return out


def _leaf_column(node) -> str | None:
    if isinstance(node.expression, exp.Table):
        tbl = node.expression
        return f"{tbl.db}.{tbl.name}.{node.name.split('.')[-1]}"
    return None


def build_lineage(models: list[Model], wh: Warehouse) -> LineageGraph:
    schema = wh.sqlglot_schema()
    g = LineageGraph()
    for m in models:
        for col in wh.columns(m.schema, m.name):
            node = sg_lineage(col["name"], m.sql, schema=schema, dialect="duckdb")
            sources = sorted({c for n in node.walk() if (c := _leaf_column(n))})
            expr_node = node.expression
            inner = expr_node.this if isinstance(expr_node, exp.Alias) else expr_node
            passthrough = isinstance(inner, exp.Column)
            g.add(ColumnDerivation(
                column=f"{m.fqn}.{col['name']}",
                expression=inner.sql(dialect="duckdb"),
                sources=sources,
                passthrough=passthrough,
            ))
    return g
