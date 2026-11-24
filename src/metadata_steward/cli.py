"""metadata-steward command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import Settings


def _agent_default() -> str:
    return "claude" if os.getenv("ANTHROPIC_API_KEY") else "baseline"


def cmd_seed(a, s: Settings) -> int:
    from .seed import seed

    seed(s.warehouse, models_dir=s.models_dir)
    print(f"seeded {s.warehouse}")
    return 0


def _steward(a, s: Settings):
    from .steward import Steward

    if not s.warehouse.exists():
        cmd_seed(a, s)
    return Steward(s, agent=getattr(a, "agent", "claude"))


def cmd_document(a, s: Settings) -> int:
    st = _steward(a, s)

    def show(run):
        entry = st.catalog.load(run.table)
        pii = {k: v["pii_category"] for k, v in entry.get("columns", {}).items() if v["pii_category"] != "none"}
        print(f"{run.table:<28} {run.tool_calls:>3} tool calls  PII: {pii or '-'}", flush=True)

    st.document(a.table or None, on_table=show)
    print(f"\nproposals written to {s.catalog_dir}/ — review with `metadata-steward review`")
    return 0


def cmd_review(a, s: Settings) -> int:
    from .catalog import Catalog

    cat = Catalog(s.catalog_dir)
    for t in cat.tables():
        e = cat.load(t)
        print(f"\n{t}  [{e.get('description_status')}]  {e.get('description') or ''}")
        for name, c in e.get("columns", {}).items():
            flag = "!" if c["sensitivity"] == "restricted" else " "
            print(f"  {flag} {name:<18} {c['pii_category']:<26} {c['confidence']:.2f}  {c['status']:<8} "
                  f"{c['description'][:70]}")
    return 0


def cmd_approve(a, s: Settings) -> int:
    from .catalog import Catalog

    n = Catalog(s.catalog_dir).approve(a.target, a.by)
    print(f"approved {n} entr{'y' if n == 1 else 'ies'} in {a.target}")
    return 0


def cmd_ask(a, s: Settings) -> int:
    st = _steward(a, s)
    done, tb, usage = st.ask(a.question)
    if not done:
        print("no answer within the turn budget")
        return 1
    print(done["answer"])
    if done.get("columns"):
        print("\ncolumns:\n  " + "\n  ".join(done["columns"]))
    print(f"\n({len(tb.trace)} tool calls, {usage['input_tokens']:,}/{usage['output_tokens']:,} tokens)")
    return 0


def cmd_lineage(a, s: Settings) -> int:
    st = _steward(a, s)
    fn = st.lineage.upstream if a.direction == "upstream" else st.lineage.downstream
    for hop in fn(a.column):
        print(f"{'  ' * hop['depth']}{hop['column']}    <- {hop['via']}")
    return 0


def cmd_export(a, s: Settings) -> int:
    from .catalog import Catalog

    cat = Catalog(s.catalog_dir)
    out = ["# Data catalog", ""]
    for t in cat.tables():
        e = cat.load(t)
        out += [f"## `{t}`", "", e.get("description") or "_no description_", "",
                "| column | description | PII | sensitivity | conf. | status |", "|---|---|---|---|---|---|"]
        for name, c in e.get("columns", {}).items():
            out.append(f"| `{name}` | {c['description']} | {c['pii_category']} | {c['sensitivity']} | "
                       f"{c['confidence']:.2f} | {c['status']} |")
        out.append("")
    Path(a.out).write_text("\n".join(out))
    print(f"wrote {a.out}")
    return 0


def cmd_evals(a, s: Settings) -> int:
    from .evals import eval_classification, eval_lineage_qa, format_classification, format_qa

    report = []
    rep = eval_classification(a.agent, s.models_dir,
                              keep_catalog=Path(a.save_catalog) if a.save_catalog else None,
                              on_table=lambda r: print(f"  documented {r.table}", file=sys.stderr, flush=True))
    report.append(format_classification(a.agent, rep))
    metrics = {"classification": rep.metrics()}
    if a.qa:
        if a.agent != "claude":
            print("lineage Q&A requires --agent claude", file=sys.stderr)
            return 2
        qa = eval_lineage_qa(s.models_dir)
        report.append(format_qa(qa))
        metrics["qa_correct"] = sum(r.correct for r in qa)
        metrics["qa_total"] = len(qa)
    print("\n\n".join(report))
    if a.json:
        Path(a.json).write_text(json.dumps(metrics, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="metadata-steward",
                                description="LLM data steward: documents a warehouse and classifies PII "
                                            "without seeing raw PII.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="build the demo warehouse").set_defaults(fn=cmd_seed)

    d = sub.add_parser("document", help="propose descriptions + PII classifications")
    d.add_argument("--table", action="append", help="schema.table (repeatable); default: all")
    d.add_argument("--agent", choices=["claude", "baseline"], default=_agent_default())
    d.set_defaults(fn=cmd_document)

    sub.add_parser("review", help="show the catalog with proposal status").set_defaults(fn=cmd_review)

    ap = sub.add_parser("approve", help="approve proposals for schema.table or schema.table.column")
    ap.add_argument("target")
    ap.add_argument("--by", default=os.getenv("USER", "reviewer"))
    ap.set_defaults(fn=cmd_approve)

    q = sub.add_parser("ask", help="ask a lineage / impact question (Claude)")
    q.add_argument("question")
    q.set_defaults(fn=cmd_ask, agent="claude")

    ln = sub.add_parser("lineage", help="print column lineage (no LLM)")
    ln.add_argument("column")
    ln.add_argument("--direction", choices=["upstream", "downstream"], default="downstream")
    ln.set_defaults(fn=cmd_lineage, agent="baseline")

    ex = sub.add_parser("export", help="render the catalog as Markdown")
    ex.add_argument("--out", default="CATALOG.md")
    ex.set_defaults(fn=cmd_export)

    e = sub.add_parser("evals", help="score PII classification (and lineage Q&A) against ground truth")
    e.add_argument("--agent", choices=["claude", "baseline"], default=_agent_default())
    e.add_argument("--qa", action="store_true", help="also run lineage Q&A (Claude only)")
    e.add_argument("--save-catalog", metavar="DIR")
    e.add_argument("--json", metavar="FILE")
    e.set_defaults(fn=cmd_evals)

    a = p.parse_args(argv)
    return a.fn(a, Settings())


if __name__ == "__main__":
    sys.exit(main())
