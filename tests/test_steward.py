from types import SimpleNamespace

import pytest

from metadata_steward.catalog import Catalog
from metadata_steward.evals import eval_classification
from metadata_steward.models import discover
from metadata_steward.steward import Steward

from .conftest import ROOT


def test_models_discovered_in_dependency_order():
    order = [m.fqn for m in discover(ROOT / "models")]
    assert order.index("mart.fct_orders") < order.index("mart.agg_daily_revenue")
    assert order.index("staging.stg_contacts") < order.index("mart.dim_customer")


def test_column_lineage(settings):
    st = Steward(settings, agent="baseline")
    down = {h["column"] for h in st.lineage.downstream("raw.crm_contacts.eml_addr")}
    assert down == {"staging.stg_contacts.email", "mart.dim_customer.email_domain", "mart.dim_customer.email_hash"}
    up = {h["column"] for h in st.lineage.upstream("mart.agg_daily_revenue.revenue_usd")}
    assert {"raw.orders_ext.amt", "raw.orders_ext.cur_cd"} <= up
    assert st.lineage.derivations["mart.dim_customer.email_hash"].expression.startswith("SHA256")


def test_approved_entries_are_never_overwritten(settings):
    st = Steward(settings, agent="baseline")
    st.document(["raw.orders_ext"])
    cat = Catalog(settings.catalog_dir)
    e = cat.load("raw.orders_ext")
    e["columns"]["ref_code"]["pii_category"] = "none"     # human corrects the scanner…
    cat.save("raw.orders_ext", e)
    assert cat.approve("raw.orders_ext.ref_code", "justin") == 1
    st.document(["raw.orders_ext"])                       # …and a re-run must not undo it
    c = cat.load("raw.orders_ext")["columns"]["ref_code"]
    assert (c["pii_category"], c["status"], c["approved_by"]) == ("none", "approved", "justin")


def test_finish_requires_every_column(settings):
    st = Steward(settings, agent="baseline")
    tb = st._toolbox("raw.hr_employees")
    out, err = tb.call("finish_table", {"summary": "done"})
    assert err and "emp_id" in out


def test_propose_rejects_unknown_column_and_category(settings):
    tb = Steward(settings, agent="baseline")._toolbox("raw.hr_employees")
    base = {"description": "d", "confidence": 0.9, "evidence": ["e"]}
    assert tb.call("propose_column", base | {"column": "nope", "pii_category": "email"})[1]
    assert tb.call("propose_column", base | {"column": "emp_id", "pii_category": "secret_sauce"})[1]
    out, err = tb.call("propose_column", base | {"column": "emp_id", "pii_category": "none",
                                                 "evidence": "a bare string"})
    assert err and "list of strings" in out


# ------------------------------------------------------------------ fake Claude client
def _b(**kw):
    return SimpleNamespace(**kw)


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = script, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=self.script(kw), usage=SimpleNamespace(input_tokens=50, output_tokens=10))


def test_claude_loop_documents_a_table_with_scripted_model(settings):
    """Model profiles, proposes all columns, tries to finish early (error), then finishes."""
    cols = ["emp_id", "full_name", "work_email", "clearance_lvl", "hire_dt", "salary_amt", "mgr_emp_id"]
    turns = iter([
        [_b(type="tool_use", id="1", name="profile_table", input={"table": "raw.hr_employees"})],
        [_b(type="tool_use", id="2", name="finish_table", input={"summary": "too early"})],
        [_b(type="tool_use", id=f"p{i}", name="propose_column", input={
            "column": c, "description": f"{c} desc", "evidence": ["x"], "confidence": 0.9,
            "pii_category": {"full_name": "person_name", "work_email": "email",
                             "salary_amt": "sensitive_employment",
                             "clearance_lvl": "sensitive_employment"}.get(c, "none")})
         for i, c in enumerate(cols)],
        [_b(type="tool_use", id="t", name="propose_table_description", input={"description": "Employees."}),
         _b(type="tool_use", id="f", name="finish_table", input={"summary": "ok"})],
    ])
    fake = FakeClient(lambda kw: next(turns))
    st = Steward(settings, agent="claude", client=fake)
    run = st.document(["raw.hr_employees"])[0]
    assert run.usage["completed"] and run.usage["turns"] == 4
    e = st.catalog.load("raw.hr_employees")
    assert e["columns"]["salary_amt"]["sensitivity"] == "restricted"
    assert e["description"] == "Employees." and e["description_status"] == "proposed"
    assert sum(not c.ok for c in run.trace) == 1  # the early finish was rejected


def test_baseline_eval_has_expected_blind_spots():
    rep = eval_classification("baseline", ROOT / "models")
    m = rep.metrics()
    assert m["columns"] == 81
    assert m["hard_correct"] == "0/9"
    assert 0.8 < m["category_accuracy"] < 0.95


@pytest.mark.parametrize("cmd", [["seed"], ["document", "--agent", "baseline", "--table", "raw.orders_ext"],
                                 ["review"], ["approve", "raw.orders_ext"],
                                 ["lineage", "raw.crm_contacts.tax_id"], ["export", "--out", "CAT.md"]])
def test_cli(tmp_path, monkeypatch, cmd):
    from metadata_steward.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MS_MODELS_DIR", str(ROOT / "models"))
    assert main(cmd) == 0
