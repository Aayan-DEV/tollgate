"""Read-only fact functions that contracts call by name (e.g. `erp.invoice`).

A production deployment swaps the `erp.*` functions for its real ERP / bank
adapters; contracts stay the same. Nothing here can change any system.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Callable

from tollgate.facts import Facts
from tollgate.normalize import scan_text
from tollgate.pickle_scan import scan_pickle

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b")
NATIONAL_ID_RE = re.compile(r"\bSYN\d{8}\b")


def _recipients(to: object) -> list[str]:
    return [t.strip().lower() for t in str(to or "").replace(";", ",").split(",") if t.strip()]


def build_fact_functions(facts: Facts, model_dir: Path, today: date | None = None) -> dict[str, Callable]:
    today = today or date.today()

    def similar_payments(vendor_id, amount, invoice_id, document, window_days, tolerance):
        """Possible duplicate: same vendor and amount, recent, AND the new invoice refers to the paid one
        (its number appears in the document, or both share a base number like INV-3101 / INV-3101-R)."""
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return []
        if not vendor_id:
            return []
        doc, base = str(document or ""), re.split(r"[-_/ ](?:R|RE|RESEND|COPY|DUP)\d*$", str(invoice_id or ""), flags=re.I)[0]
        return [
            f"{p['invoice_id']} {p['amount_eur']:.2f} EUR on {p['date']}"
            for p in facts.payments(vendor_id=vendor_id)
            if p["invoice_id"] != invoice_id and abs(p["amount_eur"] - amount) <= float(tolerance or 0)
            and (today - date.fromisoformat(p["date"])).days <= int(window_days or 0)
            and (p["invoice_id"] in doc or p["invoice_id"] == base)
        ]

    def external_recipients(to, internal_domains):
        return [t for t in _recipients(to) if t.split("@")[-1] not in set(internal_domains or [])]

    def external_if_sensitive(to, subject, body, internal_domains):
        text = scan_text(subject, body)   # canonical + base64-decoded + IBAN spellings compacted
        sensitive = IBAN_RE.search(text) or NATIONAL_ID_RE.search(text)
        return external_recipients(to, internal_domains) if sensitive else []

    def pickle_file(path):
        base = model_dir.resolve()
        raw = str(path or "")
        target = (base / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        inside = base in target.parents
        out = {"inside": inside, "exists": inside and target.is_file(), "ok": False, "dangerous": []}
        if out["exists"]:
            scan = scan_pickle(target)
            out.update(ok=scan["ok"], dangerous=scan["dangerous"] or ([scan["error"]] if scan["error"] else []))
        return out

    return {
        "erp.invoice": facts.invoice,
        "erp.vendor": facts.vendor,
        "erp.payments_for_invoice": lambda iid: [f"{p['payment_id']} {p['date']} {p['status']}"
                                                 for p in facts.payments(invoice_id=iid)] if iid else [],
        "erp.similar_payments": similar_payments,
        "mail.external_recipients": external_recipients,
        "mail.external_if_sensitive": external_if_sensitive,
        "files.pickle_scan": pickle_file,
    }
