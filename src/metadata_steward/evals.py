"""Evaluation against ground truth.

1. PII classification over every column in the warehouse:
   - category accuracy (prediction is one of the acceptable categories)
   - binary PII precision / recall / F1 (columns where both PII and none are acceptable are skipped)
   - accuracy on the `hard` columns (misleading names or shapes)
2. Lineage Q&A: exact-set match of the columns named in the answer (Claude only).
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .seed import seed
from .steward import Steward

EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"


@dataclass
class ColumnResult:
    column: str
    predicted: str
    accepted: list[str]
    hard: str | None

    @property
    def correct(self) -> bool:
        return self.predicted in self.accepted


@dataclass
class ClassificationReport:
    columns: list[ColumnResult]
    tokens: tuple[int, int] = (0, 0)
    tool_calls: int = 0

    def metrics(self) -> dict[str, Any]:
        tp = fp = fn = tn = 0
        for c in self.columns:
            acc_pii = [a for a in c.accepted if a != "none"]
            if acc_pii and "none" in c.accepted:
                continue  # either answer is fine
            gt, pred = bool(acc_pii), c.predicted != "none"
            tp += gt and pred
            fp += (not gt) and pred
            fn += gt and not pred
            tn += (not gt) and not pred
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        hard = [c for c in self.columns if c.hard]
        return {
            "columns": len(self.columns),
            "category_accuracy": round(sum(c.correct for c in self.columns) / len(self.columns), 3),
            "pii_precision": round(p, 3), "pii_recall": round(r, 3),
            "pii_f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
            "false_positives": fp, "false_negatives": fn,
            "hard_correct": f"{sum(c.correct for c in hard)}/{len(hard)}",
        }


def load_ground_truth(path: Path | None = None) -> dict[str, dict[str, tuple[list[str], str | None]]]:
    raw = yaml.safe_load((path or EVALS_DIR / "ground_truth.yaml").read_text())
    out: dict[str, dict[str, tuple[list[str], str | None]]] = {}
    for table, cols in raw.items():
        out[table] = {}
        for col, spec in cols.items():
            out[table][col] = (spec["accept"], spec.get("hard")) if isinstance(spec, dict) else (spec, None)
    return out


def _fresh_env(tmp: Path, models_dir: Path) -> Settings:
    s = Settings(warehouse=tmp / "warehouse.duckdb", models_dir=models_dir, catalog_dir=tmp / "catalog")
    seed(s.warehouse, models_dir=models_dir)
    return s


def eval_classification(agent: str, models_dir: Path, client: Any | None = None, model: str | None = None,
                        keep_catalog: Path | None = None, tables: list[str] | None = None,
                        on_table=None) -> ClassificationReport:
    gt = load_ground_truth()
    tmp = Path(tempfile.mkdtemp(prefix="ms-eval-"))
    try:
        s = _fresh_env(tmp, models_dir)
        if model:
            s.model = model
        st = Steward(s, agent=agent, client=client)
        runs = st.document(tables, on_table=on_table)
        results = []
        for table, cols in gt.items():
            if tables and table not in tables:
                continue
            entry = st.catalog.load(table).get("columns", {})
            for col, (accepted, hard) in cols.items():
                results.append(ColumnResult(f"{table}.{col}", entry.get(col, {}).get("pii_category", "MISSING"),
                                            accepted, hard))
        if keep_catalog:
            shutil.copytree(s.catalog_dir, keep_catalog, dirs_exist_ok=True)
        return ClassificationReport(
            results,
            tokens=(sum(r.usage["input_tokens"] for r in runs), sum(r.usage["output_tokens"] for r in runs)),
            tool_calls=sum(r.tool_calls for r in runs),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class QAResult:
    id: str
    expected: list[str]
    got: list[str]
    answer: str
    tool_calls: int
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        return not self.missing and not self.extra


def eval_lineage_qa(models_dir: Path, client: Any | None = None, model: str | None = None) -> list[QAResult]:
    questions = yaml.safe_load((EVALS_DIR / "lineage_questions.yaml").read_text())
    tmp = Path(tempfile.mkdtemp(prefix="ms-qa-"))
    try:
        s = _fresh_env(tmp, models_dir)
        if model:
            s.model = model
        st = Steward(s, agent="claude", client=client)
        out = []
        for q in questions:
            done, tb, _ = st.ask(q["question"])
            got = (done or {}).get("columns", [])
            exp = sorted(q["expected"])
            out.append(QAResult(q["id"], exp, got, (done or {}).get("answer", "(no answer)"), len(tb.trace),
                                missing=sorted(set(exp) - set(got)), extra=sorted(set(got) - set(exp))))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def format_classification(name: str, rep: ClassificationReport) -> str:
    m = rep.metrics()
    lines = [f"### PII classification — {name}", "",
             "| columns | category accuracy | PII precision | PII recall | PII F1 | FP | FN | hard columns |",
             "|---|---|---|---|---|---|---|---|",
             (f"| {m['columns']} | {m['category_accuracy']:.0%} | {m['pii_precision']:.0%} | "
              f"{m['pii_recall']:.0%} | {m['pii_f1']:.2f} | {m['false_positives']} | {m['false_negatives']} | "
              f"{m['hard_correct']} |"), ""]
    wrong = [c for c in rep.columns if not c.correct]
    if wrong:
        lines += ["Misclassified:", "", "| column | predicted | acceptable | hard? |", "|---|---|---|---|"]
        lines += [f"| {c.column} | {c.predicted} | {', '.join(c.accepted)} | {c.hard or ''} |" for c in wrong]
    if rep.tokens[0]:
        lines += ["", f"_{rep.tool_calls} tool calls, tokens in/out: {rep.tokens[0]:,}/{rep.tokens[1]:,}_"]
    return "\n".join(lines)


def format_qa(results: list[QAResult]) -> str:
    lines = ["### Lineage Q&A", "",
             "| question | result | missing | extra | tool calls |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.id} | {'PASS' if r.correct else 'FAIL'} | {', '.join(r.missing)} | "
                     f"{', '.join(r.extra)} | {r.tool_calls} |")
    lines.append(f"\n**{sum(r.correct for r in results)}/{len(results)} correct**")
    return "\n".join(lines)
