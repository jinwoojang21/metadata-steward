"""SQL models (dbt-style SELECT files) — discovery, dependency order, build."""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import TopologicalSorter
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


@dataclass
class Model:
    schema: str
    name: str
    sql: str
    path: Path

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def description_hint(self) -> str:
        """Leading `-- comment` lines of the model file (what the author wrote)."""
        lines = []
        for line in self.sql.splitlines():
            if not line.startswith("--"):
                break
            lines.append(line.lstrip("- ").strip())
        return " ".join(lines)

    def refs(self) -> set[str]:
        tree = sqlglot.parse_one(self.sql, dialect="duckdb")
        return {f"{t.db}.{t.name}" for t in tree.find_all(exp.Table) if t.db}


def discover(models_dir: Path | None = None) -> list[Model]:
    """Models in models/<schema>/<name>.sql, returned in dependency order."""
    models_dir = models_dir or DEFAULT_MODELS_DIR
    found = {}
    for path in sorted(models_dir.glob("*/*.sql")):
        m = Model(schema=path.parent.name, name=path.stem, sql=path.read_text(), path=path)
        found[m.fqn] = m
    graph = {fqn: {r for r in m.refs() if r in found} for fqn, m in found.items()}
    return [found[f] for f in TopologicalSorter(graph).static_order()]


def build_models(warehouse: Path, models_dir: Path | None = None) -> list[Model]:
    con = duckdb.connect(str(warehouse))
    models = discover(models_dir)
    for m in models:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {m.schema}")
        con.execute(f"CREATE OR REPLACE TABLE {m.fqn} AS {m.sql}")
    con.close()
    return models
