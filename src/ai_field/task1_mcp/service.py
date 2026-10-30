"""Local-only customer and refund operations for Task 1."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class CustomerRecord:
    """A safe mock customer record."""

    customer_id: str
    name: str
    email: str
    status: str


CUSTOMERS: dict[str, CustomerRecord] = {
    "CUST-00001": CustomerRecord(
        customer_id="CUST-00001",
        name="Avery Chen",
        email="avery.chen@example.test",
        status="active",
    ),
    "CUST-12345": CustomerRecord(
        customer_id="CUST-12345",
        name="Jordan Rivera",
        email="jordan.rivera@example.test",
        status="active",
    ),
    "CUST-54321": CustomerRecord(
        customer_id="CUST-54321",
        name="Morgan Patel",
        email="morgan.patel@example.test",
        status="suspended",
    ),
}


def get_customer(customer_id: str) -> dict[str, str] | None:
    """Return a copy of a mock record, or ``None`` when it does not exist."""
    customer = CUSTOMERS.get(customer_id)
    return asdict(customer) if customer is not None else None


def create_refund(customer_id: str, amount: float, reason: str) -> dict[str, str | float]:
    """Simulate a refund locally; no payment provider is contacted."""
    return {
        "refund_id": f"RFND-{uuid4().hex[:12].upper()}",
        "customer_id": customer_id,
        "amount": amount,
        "reason": reason,
        "status": "simulated",
    }
