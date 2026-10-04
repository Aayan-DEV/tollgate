"""Read-only view of the systems of record (ERP, bank, mail) used to resolve effects.

The gateway never trusts what the agent claims about the world; it looks it up
here. Production adapters query the real ERP read-only; the demo's
erp.store.ApStore implements the same methods.
"""

from typing import Protocol


class Facts(Protocol):
    def invoice(self, invoice_id: str) -> dict | None: ...

    def vendor(self, vendor_id: str) -> dict | None: ...

    def payments(self, vendor_id: str | None = None, invoice_id: str | None = None) -> list[dict]:
        """Completed and in-flight payments, newest first."""
        ...
