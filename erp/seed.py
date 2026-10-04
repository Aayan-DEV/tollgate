"""Builds data/nordwind.db: a synthetic finance group with ~250 columns of data.

All data is fictional and marked as such: emails use the reserved .example
domain, personal IDs carry a SYN prefix, generated bank accounts use the
unassigned 9999 bank code range, and cards are stored as last-4 only.
A fixed seed makes the file identical on every machine.

Run: uv run python -m erp.seed
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nordwind.db"
TODAY = date(2026, 10, 3)

SCHEMA = """
CREATE TABLE legal_entities (entity_id TEXT PRIMARY KEY, name TEXT, country TEXT, vat_id TEXT, tax_office TEXT, street TEXT,
  city TEXT, postal_code TEXT, base_currency TEXT, fiscal_year_start TEXT, registration_no TEXT, created_at TEXT, status TEXT,
  parent_entity TEXT, industry_code TEXT);
CREATE TABLE company_bank_accounts (account_id TEXT PRIMARY KEY, entity_id TEXT, bank_name TEXT, iban TEXT, swift TEXT,
  currency TEXT, balance REAL, overdraft_limit REAL, opened_at TEXT, purpose TEXT, signatory_1 TEXT, signatory_2 TEXT,
  status TEXT, last_reconciled TEXT);
CREATE TABLE vendors (vendor_id TEXT PRIMARY KEY, entity_id TEXT, name TEXT, legal_form TEXT, vat_id TEXT, country TEXT,
  street TEXT, city TEXT, postal_code TEXT, iban TEXT, swift TEXT, bank_name TEXT, bank_verified_at TEXT, bank_verified_by TEXT,
  payment_terms_days INTEGER, currency TEXT, category TEXT, risk_rating TEXT, sanctions_screened_at TEXT, sanctions_hit INTEGER,
  contact_name TEXT, contact_email TEXT, contact_phone TEXT, onboarding_date TEXT, status TEXT, annual_spend_eur REAL,
  last_invoice_date TEXT, notes TEXT);
CREATE TABLE vendor_bank_change_requests (request_id TEXT PRIMARY KEY, vendor_id TEXT, requested_at TEXT, old_iban TEXT,
  new_iban TEXT, channel TEXT, requester_email TEXT, status TEXT, verified_by TEXT, verification_method TEXT, decided_at TEXT,
  fraud_flag INTEGER);
CREATE TABLE purchase_orders (po_id TEXT PRIMARY KEY, entity_id TEXT, vendor_id TEXT, created_at TEXT, created_by TEXT,
  cost_center TEXT, gl_account TEXT, description TEXT, amount_net REAL, currency TEXT, status TEXT, approved_by TEXT);
CREATE TABLE goods_receipts (gr_id TEXT PRIMARY KEY, po_id TEXT, received_at TEXT, received_by TEXT, quantity_ok INTEGER, notes TEXT);
CREATE TABLE invoices (invoice_id TEXT PRIMARY KEY, entity_id TEXT, vendor_id TEXT, vendor_name TEXT, po_id TEXT,
  vendor_invoice_no TEXT, issue_date TEXT, due_date TEXT, received_at TEXT, channel TEXT, currency TEXT, amount_net REAL,
  vat_rate REAL, vat_amount REAL, amount REAL, status TEXT, approved_by TEXT, approved_at TEXT, cost_center TEXT,
  gl_account TEXT, ocr_confidence REAL, three_way_match TEXT, payment_terms_days INTEGER, discount_pct REAL,
  discount_until TEXT, paid_at TEXT, payment_id TEXT, dispute_flag INTEGER, document TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE invoice_lines (line_id TEXT PRIMARY KEY, invoice_id TEXT, line_no INTEGER, description TEXT, quantity REAL,
  unit_price REAL, net REAL, vat_rate REAL, gl_account TEXT);
CREATE TABLE payments (payment_id TEXT PRIMARY KEY, entity_id TEXT, invoice_id TEXT, vendor_id TEXT, amount_eur REAL,
  currency TEXT, iban TEXT, swift TEXT, status TEXT, date TEXT, executed_at TEXT, batch_id TEXT, initiated_by TEXT,
  initiated_by_type TEXT, approved_by TEXT, bank_reference TEXT, value_date TEXT, fx_rate REAL);
CREATE TABLE bank_transactions (tx_id TEXT PRIMARY KEY, account_id TEXT, booking_date TEXT, value_date TEXT, amount REAL,
  currency TEXT, direction TEXT, counterparty_name TEXT, counterparty_iban TEXT, reference TEXT, category TEXT,
  balance_after REAL, matched_payment_id TEXT, matched_invoice_id TEXT, imported_at TEXT, source TEXT);
CREATE TABLE employees (employee_id TEXT PRIMARY KEY, entity_id TEXT, first_name TEXT, last_name TEXT, national_id TEXT,
  date_of_birth TEXT, gender TEXT, home_street TEXT, home_city TEXT, home_postal_code TEXT, private_email TEXT,
  private_phone TEXT, work_email TEXT, department TEXT, job_title TEXT, manager_id TEXT, hire_date TEXT, contract_type TEXT,
  salary_gross_monthly REAL, currency TEXT, bonus_target_pct REAL, private_iban TEXT, tax_office TEXT, health_insurer TEXT,
  emergency_contact TEXT, status TEXT, termination_date TEXT, performance_rating TEXT, work_location TEXT, cost_center TEXT);
CREATE TABLE payroll_runs (run_id TEXT PRIMARY KEY, entity_id TEXT, period TEXT, run_date TEXT, status TEXT,
  total_gross REAL, total_net REAL, approved_by TEXT);
CREATE TABLE payroll_lines (line_id TEXT PRIMARY KEY, run_id TEXT, employee_id TEXT, gross REAL, tax REAL,
  social_security REAL, health REAL, net REAL, bonus REAL, iban TEXT, paid_at TEXT, status TEXT);
CREATE TABLE customers (customer_id TEXT PRIMARY KEY, entity_id TEXT, name TEXT, vat_id TEXT, contact_name TEXT,
  contact_email TEXT, phone TEXT, street TEXT, city TEXT, country TEXT, credit_limit_eur REAL, payment_terms_days INTEGER,
  risk_score REAL, balance_due REAL, created_at TEXT, status TEXT);
CREATE TABLE corporate_cards (card_id TEXT PRIMARY KEY, employee_id TEXT, card_last4 TEXT, brand TEXT,
  monthly_limit_eur REAL, status TEXT, issued_at TEXT, expires TEXT);
CREATE TABLE app_users (user_id TEXT PRIMARY KEY, name TEXT, email TEXT, role TEXT, entity_scope TEXT, cost_centers TEXT,
  created_at TEXT, mfa INTEGER, status TEXT, last_login TEXT);
"""

# Four fixed vendors the scenarios rely on (public textbook example IBANs).
CORE_VENDORS = [
    ("V-101", "PL01", "Baltic Paper Sp. z o.o.", "PL", "PL61109010140000071219812874", "balticpaper", "paper"),
    ("V-102", "PL01", "Krakow Logistics S.A.", "PL", "PL27114020040000300201355387", "krakowlogistics", "logistics"),
    ("V-103", "PL01", "CloudHost GmbH", "DE", "DE89370400440532013000", "cloudhost", "it services"),
    ("V-104", "PL01", "Vistula Office Supplies", "PL", "PL83101010230000261395100000", "vistula-office", "office"),
]

FIRST = ["Anna", "Piotr", "Marta", "Jakub", "Ewa", "Tomasz", "Kasia", "Lukas", "Jana", "Felix", "Petra", "Ola", "Marek",
         "Zofia", "Pavel", "Lena", "Igor", "Nina", "Adam", "Iga"]
LAST = ["Nowak", "Kowalska", "Wisniewski", "Lewandowska", "Schmidt", "Novak", "Dvorak", "Mazur", "Krol", "Weber",
        "Zielinska", "Fischer", "Horak", "Wojcik", "Kaminska", "Bauer", "Cerny", "Pawlak", "Sikora", "Lang"]
WORD_A = ["Amber", "Granite", "Silver", "Northwind", "Oak", "Delta", "Falcon", "Harbor", "Prime", "Vertex", "Willow",
          "Bright", "Iron", "Summit", "Blue", "Polar", "Nova", "Crest", "Maple", "Cobalt"]
WORD_B = ["Logistics", "Packaging", "Systems", "Supplies", "Consulting", "Freight", "Print", "Cleaning", "Energy",
          "Software", "Security", "Catering", "Metals", "Plastics", "Labs"]
FORMS = {"PL": "Sp. z o.o.", "DE": "GmbH", "CZ": "s.r.o."}
CITIES = {"PL": ["Krakow", "Warsaw", "Gdansk", "Wroclaw"], "DE": ["Berlin", "Leipzig", "Dresden"], "CZ": ["Prague", "Brno"]}
DEPARTMENTS = ["Finance", "Operations", "Sales", "IT", "HR", "Procurement", "Warehouse"]


def iban(country: str, rng: random.Random) -> str:
    """IBAN-shaped value with valid check digits in the unassigned 9999 bank range."""
    lengths = {"PL": (8, 16), "DE": (8, 10), "CZ": (4, 16)}
    bank_len, acct_len = lengths[country]
    bban = "9999" + "".join(str(rng.randint(0, 9)) for _ in range(bank_len - 4 + acct_len))
    numeric = "".join(str(int(c, 36)) for c in bban + country + "00")
    check = 98 - int(numeric) % 97
    return f"{country}{check:02d}{bban}"


def d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def build(path: Path = DB_PATH, seed: int = 42) -> Path:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    ins = lambda table, row: db.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(row))})", row)  # noqa: E731

    entities = [("PL01", "Nordwind Supplies Sp. z o.o.", "PL"), ("DE01", "Nordwind Handel GmbH", "DE"), ("CZ01", "Nordwind CZ s.r.o.", "CZ")]
    for eid, name, c in entities:
        ins("legal_entities", (eid, name, c, f"{c}SYN{rng.randint(10**7, 10**8 - 1)}", f"Tax office {CITIES[c][0]}",
                               "Dluga 12" if c == "PL" else "Hauptstrasse 3", CITIES[c][0], "00-000", "EUR", "01-01",
                               f"REG-SYN-{rng.randint(1000, 9999)}", "2015-03-01", "active", None if eid == "PL01" else "PL01", "46.90"))
        for k in range(rng.randint(2, 3)):
            ins("company_bank_accounts", (f"ACC-{eid}-{k}", eid, f"Synthetic Bank {k + 1}", iban(c, rng), "SYNTXXXX", "EUR",
                                          round(rng.uniform(50_000, 2_500_000), 2), 100_000, d(rng.randint(400, 3000)),
                                          ["operating", "payroll", "tax"][k], "Anna Nowak", "Marek Krol", "active", d(rng.randint(1, 9))))

    vendors = []
    for vid, eid, name, c, acct, slug, cat in CORE_VENDORS:
        vendors.append((vid, eid, name, c, acct, slug, cat))
    for n in range(146):
        c = rng.choice(["PL", "PL", "PL", "DE", "CZ"])
        name = f"{rng.choice(WORD_A)} {rng.choice(WORD_B)} {FORMS[c]}"
        vendors.append((f"V-{200 + n}", rng.choice(["PL01", "PL01", "DE01", "CZ01"]), name, c, iban(c, rng),
                        name.split()[0].lower() + str(n), rng.choice(WORD_B).lower()))
    for vid, eid, name, c, acct, slug, cat in vendors:
        contact = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        ins("vendors", (vid, eid, name, FORMS[c], f"{c}SYN{rng.randint(10**7, 10**8 - 1)}", c, f"Street {rng.randint(1, 99)}",
                        rng.choice(CITIES[c]), "00-000", acct, "SYNTXXXX", "Synthetic Bank", d(rng.randint(30, 900)),
                        "Treasury team", rng.choice([14, 30, 45, 60]), "EUR", cat, rng.choice(["low", "low", "medium", "high"]),
                        d(rng.randint(1, 60)), 0, contact, f"billing@{slug}.example", f"+00 000 {rng.randint(100000, 999999)}",
                        d(rng.randint(200, 3000)), "active", round(rng.uniform(2_000, 400_000), 2), d(rng.randint(5, 90)), None))

    for n in range(40):
        vid = rng.choice(vendors)[0]
        ins("vendor_bank_change_requests", (f"BCR-{n:04d}", vid, d(rng.randint(5, 700)), None, iban("PL", rng),
                                            rng.choice(["email", "invoice note", "phone", "portal"]),
                                            f"finance@{rng.choice(['secure-pay', 'vendor-update', 'billing-team'])}.example",
                                            rng.choice(["rejected", "rejected", "approved", "pending"]), "Treasury team",
                                            rng.choice(["callback", "none", "portal 2FA"]), d(rng.randint(1, 5)), rng.randint(0, 1)))

    inv_n = pay_n = 0
    for _ in range(1500):
        vid, eid, vname, c = (lambda v: (v[0], v[1], v[2], v[3]))(rng.choice(vendors))
        net = round(rng.uniform(80, 18_000), 2)
        vat = 0.23 if c == "PL" else 0.19 if c == "DE" else 0.21
        gross = round(net * (1 + vat), 2)
        issued = rng.randint(20, 700)
        po = f"PO-{inv_n:05d}"
        ins("purchase_orders", (po, eid, vid, d(issued + 10), "Procurement", f"CC-{rng.randint(100, 140)}",
                                f"4{rng.randint(100, 999)}", f"{rng.choice(WORD_B)} services", net, "EUR", "closed", "Anna Nowak"))
        ins("goods_receipts", (f"GR-{inv_n:05d}", po, d(issued + 2), "Warehouse", 1, None))
        status = rng.choice(["paid"] * 8 + ["rejected"])
        iid = f"H-{inv_n:05d}"
        payment_id = None
        if status == "paid":
            payment_id = f"P-{pay_n:05d}"
            acct = next(v[4] for v in vendors if v[0] == vid)
            ins("payments", (payment_id, eid, iid, vid, gross, "EUR", acct, "SYNTXXXX", "completed", d(issued - 14),
                             d(issued - 14), f"B-{issued // 7}", "Anna Nowak", "human", "Marek Krol",
                             f"REF{rng.randint(10**8, 10**9)}", d(issued - 15), 1.0))
            pay_n += 1
        ins("invoices", (iid, eid, vid, vname, po, f"FV/{rng.randint(1, 999)}/2026", d(issued), d(issued - 30), d(issued - 1),
                         rng.choice(["email", "portal", "edi"]), "EUR", net, vat, round(gross - net, 2), gross, status,
                         "Anna Nowak", d(issued - 3), f"CC-{rng.randint(100, 140)}", f"4{rng.randint(100, 999)}",
                         round(rng.uniform(0.82, 0.99), 2), "matched", 30, 0, None, d(issued - 14) if payment_id else None,
                         payment_id, 0, f"Invoice {iid} from {vname}. Total {gross:.2f} EUR.", d(issued), d(issued - 14)))
        for ln in range(rng.randint(1, 4)):
            ins("invoice_lines", (f"{iid}-{ln}", iid, ln + 1, f"{rng.choice(WORD_B)} item", 1, round(net / (ln + 1), 2),
                                  round(net / (ln + 1), 2), vat, f"4{rng.randint(100, 999)}"))
        inv_n += 1

    accounts = [r[0] for r in db.execute("SELECT account_id FROM company_bank_accounts")]
    for n in range(5000):
        amt = round(rng.uniform(-25_000, 30_000), 2)
        ins("bank_transactions", (f"TX-{n:06d}", rng.choice(accounts), d(rng.randint(0, 700)), d(rng.randint(0, 700)), amt, "EUR",
                                  "credit" if amt > 0 else "debit", rng.choice(vendors)[2], rng.choice(vendors)[4],
                                  f"REF{rng.randint(10**6, 10**7)}", rng.choice(["supplier", "customer", "tax", "fee"]),
                                  round(rng.uniform(10_000, 900_000), 2), None, None, d(0), "camt.053"))

    employees = []
    for n in range(120):
        eid = rng.choice(["PL01", "PL01", "PL01", "DE01", "CZ01"])
        c = eid[:2]
        first, last = rng.choice(FIRST), rng.choice(LAST)
        emp = f"E-{n:04d}"
        salary = round(rng.uniform(5_200, 31_000), 2)
        employees.append((emp, eid, salary, c))
        ins("employees", (emp, eid, first, last, f"SYN{rng.randint(10**7, 10**8 - 1)}",
                          f"{rng.randint(1965, 2002)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", rng.choice(["F", "M"]),
                          f"Home street {rng.randint(1, 120)}", rng.choice(CITIES[c]), "00-000",
                          f"{first.lower()}.{last.lower()}{n}@mail.example", f"+00 600 {rng.randint(100000, 999999)}",
                          f"{first.lower()}.{last.lower()}@nordwind.example", rng.choice(DEPARTMENTS), "Specialist",
                          f"E-{rng.randint(0, 10):04d}", d(rng.randint(60, 4000)), rng.choice(["permanent", "fixed", "b2b"]),
                          salary, "PLN" if c == "PL" else "EUR", rng.choice([0, 5, 10, 15]), iban(c, rng), f"Tax office {c}",
                          rng.choice(["Public fund", "Synthetic Health"]), f"Contact {rng.choice(FIRST)}", "active", None,
                          rng.choice(["meets", "exceeds", "below"]), rng.choice(CITIES[c]), f"CC-{rng.randint(100, 140)}"))
        if n % 5 == 0:
            ins("corporate_cards", (f"CARD-{n:04d}", emp, f"{rng.randint(1000, 9999)}", rng.choice(["Visa", "Mastercard"]),
                                    rng.choice([1000, 2500, 5000]), "active", d(rng.randint(30, 900)), "2028-12"))
    for m in range(12):
        period = (TODAY.replace(day=1) - timedelta(days=30 * (m + 1))).strftime("%Y-%m")
        run = f"PR-{period}"
        gross_total = 0.0
        for emp, eid, salary, c in employees:
            tax = round(salary * 0.12, 2)
            ss = round(salary * 0.1371, 2)
            health = round(salary * 0.09, 2)
            gross_total += salary
            ins("payroll_lines", (f"{run}-{emp}", run, emp, salary, tax, ss, health, round(salary - tax - ss - health, 2),
                                  0.0, iban(c, rng), f"{period}-28", "paid"))
        ins("payroll_runs", (run, "PL01", period, f"{period}-28", "paid", round(gross_total, 2),
                             round(gross_total * 0.65, 2), "Anna Nowak"))

    for n in range(300):
        c = rng.choice(["PL", "DE", "CZ"])
        name = f"{rng.choice(WORD_A)} {rng.choice(WORD_B)} {FORMS[c]}"
        ins("customers", (f"C-{n:04d}", rng.choice(["PL01", "DE01", "CZ01"]), name, f"{c}SYN{rng.randint(10**7, 10**8 - 1)}",
                          f"{rng.choice(FIRST)} {rng.choice(LAST)}", f"buyer{n}@{name.split()[0].lower()}{n}.example",
                          f"+00 000 {rng.randint(100000, 999999)}", f"Street {rng.randint(1, 99)}", rng.choice(CITIES[c]), c,
                          rng.choice([10_000, 50_000, 150_000]), 30, round(rng.uniform(0, 1), 2), round(rng.uniform(0, 80_000), 2),
                          d(rng.randint(100, 2000)), "active"))

    users = [("U-ANNA", "Anna Nowak", "anna.nowak@nordwind.example", "cfo", "PL01,DE01,CZ01", "*"),
             ("U-PIOTR", "Piotr Mazur", "piotr.mazur@nordwind.example", "ap_clerk", "PL01", "CC-100,CC-120"),
             ("U-JONAS", "Jonas Weber", "jonas.weber@nordwind.example", "ap_clerk", "DE01", "CC-130")]
    for u in users:
        ins("app_users", (*u, "2024-01-10", 1, "active", d(1)))

    db.commit()
    db.close()
    return path


if __name__ == "__main__":
    p = build()
    con = sqlite3.connect(p)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    cols = rows = 0
    for t in tables:
        c = len(con.execute(f"PRAGMA table_info({t})").fetchall())
        r = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        cols, rows = cols + c, rows + r
        print(f"{t:<30} {r:>6} rows  {c:>3} cols")
    print(f"{'TOTAL':<30} {rows:>6} rows  {cols:>3} cols  -> {p}")
