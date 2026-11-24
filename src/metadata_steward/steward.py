"""Orchestration: document tables in dependency order, answer questions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agent.baseline import run_baseline_table
from .agent.loop import run_loop
from .agent.prompts import DOC_SYSTEM, QA_SYSTEM, doc_brief
from .agent.tools import DOC_TOOLS, QA_TOOLS, Toolbox
from .catalog import Catalog
from .config import Settings
from .lineage import build_lineage
from .models import discover
from .warehouse import Warehouse


@dataclass
class TableRun:
    table: str
    tool_calls: int
    usage: dict[str, Any]
    trace: list = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)


class Steward:
    def __init__(self, settings: Settings, agent: str = "claude", client: Any | None = None):
        if agent not in ("claude", "baseline"):
            raise ValueError("agent must be claude|baseline")
        self.s, self.agent, self.client = settings, agent, client
        self.wh = Warehouse(settings.warehouse)
        self.models = {m.fqn: m for m in discover(settings.models_dir)}
        self.lineage = build_lineage(list(self.models.values()), self.wh)
        self.catalog = Catalog(settings.catalog_dir)
        self.agent_name = f"claude ({settings.model})" if agent == "claude" else "rule-based baseline"

    def ordered_tables(self) -> list[str]:
        """Raw sources first, then models in dependency order, so upstream docs exist first."""
        raw = sorted(t["table"] for t in self.wh.tables() if t["table"].startswith("raw."))
        return raw + list(self.models)

    def _toolbox(self, table: str | None = None) -> Toolbox:
        return Toolbox(self.wh, self.lineage, self.models, self.catalog, self.agent_name, table=table)

    def document(self, tables: list[str] | None = None, on_table=None) -> list[TableRun]:
        runs = []
        for t in tables or self.ordered_tables():
            tb = self._toolbox(t)
            if self.agent == "baseline":
                usage = run_baseline_table(tb, t)
            else:
                usage = run_loop(tb, system=DOC_SYSTEM, tools=DOC_TOOLS, brief=doc_brief(t),
                                 model=self.s.model, max_turns=self.s.max_agent_turns, client=self.client)
            run = TableRun(t, len(tb.trace), usage, tb.trace, tb.transcript)
            runs.append(run)
            if on_table:
                on_table(run)
        return runs

    def ask(self, question: str) -> tuple[dict[str, Any] | None, Toolbox, dict[str, Any]]:
        if self.agent != "claude":
            raise ValueError("ask requires the Claude agent (set ANTHROPIC_API_KEY)")
        tb = self._toolbox()
        usage = run_loop(tb, system=QA_SYSTEM, tools=QA_TOOLS, brief=question, model=self.s.model,
                         max_turns=self.s.max_agent_turns, client=self.client)
        return tb.done, tb, usage
