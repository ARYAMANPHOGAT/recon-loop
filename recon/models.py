"""Domain models for three-way reconciliation.

Three sources that must agree but rarely do:
  1. Merchant order book  - what the merchant thinks was sold
  2. Settlement report    - what the PSP says it collected and paid out
  3. Bank statement       - what actually hit the current account
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from typing import Optional


# Money is handled in paise (integer) everywhere internally.
# Floating point rupees are a reconciliation bug waiting to happen.
Paise = int


def rupees(p: Paise) -> str:
    return f"Rs.{p / 100:,.2f}"


class OrderStatus(str, Enum):
    PAID = "paid"
    ATTEMPTED = "attempted"
    CREATED = "created"


class TxnType(str, Enum):
    PAYMENT = "payment"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    ADJUSTMENT = "adjustment"


@dataclass
class Order:
    """A row from the merchant's own order book / ERP export."""

    order_id: str
    customer_ref: str
    amount: Paise
    currency: str
    status: OrderStatus
    created_at: datetime
    channel: str  # web / app / pos / invoice
    # How the merchant's own staff labelled this order. Free text, entered by
    # a human, inconsistent by nature. For invoice and POS channels this is
    # often the only link back to a settlement line.
    customer_name: str = ""
    invoice_ref: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["status"] = self.status.value
        return d


@dataclass
class SettlementLine:
    """A row from the PSP settlement report.

    One line per payment/refund/chargeback. Many lines roll up into a single
    payout that lands in the bank as one credit.
    """

    entity_id: str  # pay_xxx / rfnd_xxx / cb_xxx
    order_id: Optional[str]
    txn_type: TxnType
    gross: Paise  # what the customer was charged
    fee: Paise  # PSP commission
    tax: Paise  # GST on the commission
    net: Paise  # gross - fee - tax  (negative for refunds/chargebacks)
    captured_at: datetime
    settlement_id: Optional[str]  # payout batch this belongs to
    settled_on: Optional[date]
    method: str  # upi / card / netbanking / wallet
    # What the PSP captured at checkout - derived from what the customer typed
    # or what the merchant passed into the payment link, so it rarely matches
    # the order book byte for byte.
    payer_description: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["captured_at"] = self.captured_at.isoformat()
        d["settled_on"] = self.settled_on.isoformat() if self.settled_on else None
        d["txn_type"] = self.txn_type.value
        return d


@dataclass
class BankLine:
    """A row from the bank statement CSV.

    The narration is a semi-structured string. Parsing it is where the
    fuzzy work lives - everything else is arithmetic.
    """

    stmt_id: str
    value_date: date
    narration: str
    credit: Paise  # 0 if this is a debit
    debit: Paise  # 0 if this is a credit
    balance: Paise
    utr: Optional[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["value_date"] = self.value_date.isoformat()
        return d


@dataclass
class Ledger:
    """The full synthetic universe, plus ground truth for scoring."""

    orders: list[Order] = field(default_factory=list)
    settlements: list[SettlementLine] = field(default_factory=list)
    bank: list[BankLine] = field(default_factory=list)
    # The bank statement covers an explicit period. Inferring it from
    # max(value_date) is unsafe: one stray row moves the boundary and
    # silently changes how in-transit items are classified.
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    # ground truth: settlement_id -> stmt_id, and entity_id -> order_id
    truth_payout_to_bank: dict[str, str] = field(default_factory=dict)
    truth_entity_to_order: dict[str, str] = field(default_factory=dict)
    # scenarios injected, for reporting what the generator actually created
    scenario_log: list[dict] = field(default_factory=list)
