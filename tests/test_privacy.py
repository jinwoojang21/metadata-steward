"""The core promise: no raw PII value ever reaches the model."""

import re

import duckdb
import pytest

from metadata_steward.privacy import detect, mask_value
from metadata_steward.steward import Steward


@pytest.mark.parametrize("value,expected", [
    ("Send invoice copy to wei.chen@gmail.com", "S*** invoice copy to <email>"),
    ("Call (571) 555-0182 before delivery", "C*** <phone> before delivery"),
    ("123-45-6789", "999-99-9999"),
    ("Smith, James", "S****, J****"),
    ("19870412", "<yyyymmdd>"),
    ("10.2.33.4", "<ip_address>"),
    ("4111 1111 1111 1111", "<card>"),
    (5715550123, "9999999999"),
    (42, 42),
])
def test_mask_value(value, expected):
    assert mask_value(value) == expected


def test_detectors():
    assert {"email"} <= detect("a.b@c.org")
    assert "card_number" not in detect("1234 5678 9012 3456")  # fails Luhn
    assert "ssn_shape" in detect("120-05-0001")


def _raw_pii(warehouse_path) -> set[str]:
    con = duckdb.connect(str(warehouse_path), read_only=True)
    vals: set[str] = set()
    for sql in [
        ("SELECT eml_addr, CAST(ph_num AS VARCHAR), c3, tax_id, addr_line1, frst_nm || ' ' || lst_nm "
         "FROM raw.crm_contacts"),
        "SELECT ip_addr, NULL, NULL, NULL, NULL, NULL FROM raw.orders_ext",
        "SELECT ip, device_id, NULL, NULL, NULL, NULL FROM raw.web_events",
        "SELECT full_name, work_email, NULL, NULL, NULL, NULL FROM raw.hr_employees",
        "SELECT email, phone, CAST(birth_date AS VARCHAR), ssn, street_address, NULL FROM staging.stg_contacts",
    ]:
        for row in con.execute(sql).fetchall():
            vals |= {str(v) for v in row if v}
    for (note,) in con.execute("SELECT notes FROM raw.orders_ext WHERE notes LIKE '%@%' OR notes LIKE '%555%'").fetchall():
        vals |= set(re.findall(r"[\w.+-]+@[\w.-]+|\(\d{3}\) 555-\d{4}", note))
    con.close()
    return vals


def test_no_raw_pii_reaches_the_model(settings, warehouse_path):
    st = Steward(settings, agent="baseline")
    tb = st._toolbox()
    for t in st.wh.tables():
        tb.call("profile_table", {"table": t["table"]})
        tb.call("get_catalog_entry", {"table": t["table"]})
        tb.call("get_model_sql", {"table": t["table"]})
        cols = [c["name"] for c in st.wh.columns(*t["table"].split("."))]
        tb.call("run_sql", {"query": f"SELECT {', '.join(cols)} FROM {t['table']}"})
        for c in cols:
            tb.call("get_lineage", {"column": f"{t['table']}.{c}", "direction": "downstream"})
    st.document()  # baseline writes proposals; those must be clean too
    for t in st.catalog.tables():
        tb.call("get_catalog_entry", {"table": t})

    blob = "\n".join(tb.transcript)
    pii = _raw_pii(warehouse_path)
    assert len(pii) > 5000  # the reference set is real
    leaked = sorted(v for v in pii if v in blob)
    assert not leaked, f"{len(leaked)} raw PII values reached the model, e.g. {leaked[:5]}"
    assert len(tb.transcript) > 120 and len(blob) > 50_000  # the sweep really ran


@pytest.mark.parametrize("query", [
    "DROP TABLE raw.crm_contacts",
    "select 1; select 2",
    "COPY raw.crm_contacts TO '/tmp/x.csv'",
    "select * from read_csv('/etc/passwd')",
    "ATTACH 'x.db'",
])
def test_sql_guard(settings, query):
    st = Steward(settings, agent="baseline")
    _, err = st._toolbox().call("run_sql", {"query": query})
    assert err


def test_warehouse_is_opened_read_only(settings):
    st = Steward(settings, agent="baseline")
    with pytest.raises(duckdb.Error):
        st.wh.con.execute("CREATE TABLE raw.x AS SELECT 1")
