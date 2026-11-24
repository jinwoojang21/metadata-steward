"""Build a deliberately messy demo warehouse in DuckDB.

`raw` tables mimic what lands from source systems: cryptic names, PII in odd places,
and look-alike values that are *not* PII. `staging` and `mart` are built from the SQL files in
models/, so their lineage can be parsed from real SQL.

All data is synthetic and generated from a fixed seed.
"""

from __future__ import annotations

import random
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from .models import build_models

FIRST = ["James", "Maria", "Wei", "Aisha", "Carlos", "Priya", "John", "Elena", "Kwame", "Sofia", "Minh",
         "Grace", "Omar", "Hannah", "Diego", "Yuki", "Fatima", "Liam", "Chloe", "Ravi"]
LAST = ["Smith", "Garcia", "Chen", "Khan", "Lopez", "Patel", "Kim", "Nguyen", "Okafor", "Rossi",
        "Johnson", "Silva", "Haddad", "Mueller", "Park", "Brown", "Ali", "Cohen", "Davis", "Moreau"]
STREETS = ["Oak St", "Maple Ave", "Elm Dr", "Westcroft Blvd", "Pine Ct", "Cedar Ln", "Lee Hwy", "Main St"]
DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "example.org", "agency.gov", "corp.example.com"]
NOTE_TEMPLATES = [
    "Gift wrap requested", "Leave at front desk", "Customer called about delivery window",
    "Expedite - backorder resolved", "Price match applied", "Rush order", None, None, None, None,
]
NOTE_PII_TEMPLATES = [
    "Send invoice copy to {email}", "Customer asked for callback at {phone}",
    "Billing contact changed to {email}", "Call {phone} before delivery",
]
PATHS = ["/", "/products", "/cart", "/checkout", "/account", "/search", "/help"]
UAS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)",
       "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)", "Mozilla/5.0 (Linux; Android 14)"]


def _phone(rng: random.Random) -> int:
    return int(f"{rng.choice([571, 703, 202, 301, 410])}555{rng.randint(0, 9999):04d}")


def _ssn(rng: random.Random) -> str:
    return f"{rng.randint(100, 665):03d}-{rng.randint(10, 99):02d}-{rng.randint(1000, 9999):04d}"


def _ip(rng: random.Random) -> str:
    return f"{rng.randint(11, 223)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def seed(path: str | Path, n_contacts: int = 400, n_orders: int = 1500, n_events: int = 3000,
         n_employees: int = 60, seed: int = 11, models_dir: Path | None = None) -> None:
    rng = random.Random(seed)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    for schema in ("raw", "staging", "mart"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    # ---------------------------------------------------------------- raw.crm_contacts
    contacts = []
    for i in range(1, n_contacts + 1):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        dob = date(1950, 1, 1) + timedelta(days=rng.randint(0, 365 * 55))
        contacts.append((
            i,
            fn.upper() if rng.random() < 0.3 else fn,                       # frst_nm
            ln,                                                              # lst_nm
            f"{fn}.{ln}{rng.randint(1, 99)}@{rng.choice(DOMAINS)}".lower()
            if rng.random() > 0.03 else None,                                # eml_addr
            _phone(rng),                                                     # ph_num (BIGINT)
            dob.strftime("%Y%m%d"),                                          # c3: DOB as text
            _ssn(rng),                                                       # tax_id
            f"{rng.randint(100, 9999)} {rng.choice(STREETS)}",               # addr_line1
            f"{rng.choice([20151, 22033, 20001, 22101, 20850, 21201]):05d}",  # zip5
            datetime(2024, 1, 1) + timedelta(minutes=rng.randint(0, 900_000)),  # noqa: DTZ001 naive TIMESTAMP
            rng.choice(["SFDC", "HUBSPOT"]),
        ))
    con.execute("""CREATE TABLE raw.crm_contacts (contact_id INTEGER, frst_nm VARCHAR, lst_nm VARCHAR,
        eml_addr VARCHAR, ph_num BIGINT, c3 VARCHAR, tax_id VARCHAR, addr_line1 VARCHAR, zip5 VARCHAR,
        created_ts TIMESTAMP, src_sys VARCHAR)""")
    con.executemany("INSERT INTO raw.crm_contacts VALUES (?,?,?,?,?,?,?,?,?,?,?)", contacts)

    # ---------------------------------------------------------------- raw.orders_ext
    orders = []
    for i in range(1, n_orders + 1):
        c = rng.choice(contacts)
        note = rng.choice(NOTE_TEMPLATES)
        if rng.random() < 0.08:
            note = rng.choice(NOTE_PII_TEMPLATES).format(
                email=f"{rng.choice(FIRST).lower()}.{rng.choice(LAST).lower()}@{rng.choice(DOMAINS)}",
                phone=f"({rng.randint(200, 999)}) 555-{rng.randint(0, 9999):04d}")
        orders.append((
            100000 + i,
            c[0],
            date(2025, 1, 1) + timedelta(days=rng.randint(0, 600)),
            round(rng.lognormvariate(4, 0.8), 2),
            rng.choices(["USD", "EUR", "GBP"], weights=[75, 15, 10])[0],
            # ref_code: NNN-NN-NNNN like an SSN, but it's warehouse-region / dock / sequence
            f"{rng.choice([110, 120, 130, 210, 220])}-{rng.randint(1, 40):02d}-{i:04d}",
            note,
            _ip(rng),
            rng.choice([None, None, "SPRING25", "WELCOME10", "VIP"]),
        ))
    con.execute("""CREATE TABLE raw.orders_ext (ord_id INTEGER, contact_id INTEGER, ord_dt DATE, amt DOUBLE,
        cur_cd VARCHAR, ref_code VARCHAR, notes VARCHAR, ip_addr VARCHAR, promo_cd VARCHAR)""")
    con.executemany("INSERT INTO raw.orders_ext VALUES (?,?,?,?,?,?,?,?,?)", orders)

    # ---------------------------------------------------------------- raw.web_events
    events = []
    for i in range(1, n_events + 1):
        c = rng.choice(contacts) if rng.random() < 0.4 else None
        events.append((
            i, uuid.UUID(int=rng.getrandbits(128)).hex[:16], c[0] if c else None,
            rng.choice(UAS), _ip(rng), rng.choice(PATHS),
            datetime(2025, 6, 1) + timedelta(seconds=rng.randint(0, 10_000_000)),  # noqa: DTZ001
            str(uuid.UUID(int=rng.getrandbits(128))),
        ))
    con.execute("""CREATE TABLE raw.web_events (evt_id INTEGER, sess_id VARCHAR, contact_id INTEGER,
        ua_str VARCHAR, ip VARCHAR, url_path VARCHAR, evt_ts TIMESTAMP, device_id VARCHAR)""")
    con.executemany("INSERT INTO raw.web_events VALUES (?,?,?,?,?,?,?,?)", events)

    # ---------------------------------------------------------------- raw.hr_employees
    emps = []
    for i in range(1, n_employees + 1):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        emps.append((
            5000 + i, f"{ln}, {fn}", f"{fn[0].lower()}{ln.lower()}@agency.gov",
            rng.choices(["NONE", "SECRET", "TS", "TS/SCI"], weights=[30, 40, 20, 10])[0],
            date(2010, 1, 1) + timedelta(days=rng.randint(0, 5000)),
            round(rng.uniform(65_000, 210_000), -3),
            5000 + rng.randint(1, max(1, i - 1)) if i > 1 else None,
        ))
    con.execute("""CREATE TABLE raw.hr_employees (emp_id INTEGER, full_name VARCHAR, work_email VARCHAR,
        clearance_lvl VARCHAR, hire_dt DATE, salary_amt DOUBLE, mgr_emp_id INTEGER)""")
    con.executemany("INSERT INTO raw.hr_employees VALUES (?,?,?,?,?,?,?)", emps)
    con.close()

    build_models(path, models_dir)
