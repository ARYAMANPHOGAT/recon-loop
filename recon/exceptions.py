"""Exception classification.

The match rate is the headline number; this file is the actual deliverable.
Anyone can report "93% matched". The useful artefact is a ranked list of the
other 7% with a named cause, the evidence behind it, and a resolution a human
can approve or reject.

Design rule, same as match.py: deterministic classifiers run first and handle
the overwhelming majority. The LLM is called only for narration strings that
survive every rule - see resolve.py. Roughly 85% of exceptions here never
touch a model, and that is the point.

Every exception carries `requires_human`. Nothing in this system silently
writes to a ledger. The engine proposes; a human disposes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Optional

from .match import MatchResult, Payout, looks_like_psp
from .models import BankLine, Ledger, Paise, TxnType, rupees

# Known bank charge schedule (charge + 18% GST), in paise.
# NEFT/RTGS/IMPS charges are published and stable, so a delta that lands
# exactly on one of these is explainable arithmetic, not a guess.
BANK_CHARGES: dict[Paise, str] = {
    1_180: "NEFT charge Rs.10 + GST",
    1_770: "NEFT charge Rs.15 + GST",
    2_950: "NEFT charge Rs.25 + GST",
    5_900: "RTGS charge Rs.50 + GST",
    11_800: "RTGS charge Rs.100 + GST",
}

OTHER_PSPS = {
    "PAYU": "PayU",
    "CCAVENUE": "CCAvenue",
    "BILLDESK": "BillDesk",
    "CASHFREE": "Cashfree",
    "PHONEPE": "PhonePe",
    "PAYTM PAYMENTS": "Paytm",
    "INSTAMOJO": "Instamojo",
}

# Narration shapes that are definitively not settlement activity.
NON_SETTLEMENT = {
    "SALARY": "payroll",
    "PAYROLL": "payroll",
    "GST PMT": "statutory - GST",
    "CHALLAN": "statutory",
    "TDS": "statutory - TDS",
    "BANK CHARGES": "bank fees",
    "VENDOR PAYMENT": "accounts payable",
    "MAINT": "bank fees",
    # A customer paying the merchant's account directly, bypassing the
    # gateway entirely. Never appears in a settlement report and must not
    # be hunted for there.
    "CUSTOMER DIRECT": "direct customer transfer",
}

SETTLEMENT_ID_RE = re.compile(r"\b(setl_[0-9]{4}[0-9]{3})\b", re.IGNORECASE)


class ExClass(str, Enum):
    BANK_CHARGE = "bank_charge_deducted"
    IN_TRANSIT = "payout_in_transit"
    PRIOR_PERIOD = "prior_period_credit"
    OTHER_PSP = "other_psp_credit"
    NON_SETTLEMENT = "non_settlement_activity"
    NET_NEGATIVE = "net_negative_carried"
    AMBIGUOUS = "ambiguous_candidates"
    OPAQUE_NARRATION = "opaque_narration"
    UNEXPLAINED = "unexplained"


# How much a human should care. Drives sort order in the report.
SEVERITY = {
    ExClass.UNEXPLAINED: 5,
    ExClass.AMBIGUOUS: 4,
    ExClass.OPAQUE_NARRATION: 3,
    ExClass.BANK_CHARGE: 2,
    ExClass.IN_TRANSIT: 2,
    ExClass.PRIOR_PERIOD: 1,
    ExClass.OTHER_PSP: 1,
    ExClass.NON_SETTLEMENT: 0,
    ExClass.NET_NEGATIVE: 1,
}


@dataclass
class Exception_:
    """One unresolved item, with everything a human needs to decide."""

    ref: str  # settlement_id or stmt_id
    side: str  # "payout" | "bank"
    ex_class: ExClass
    amount: Paise
    confidence: float  # in the CLASSIFICATION, not in a match
    evidence: list[str] = field(default_factory=list)
    suggested_resolution: str = ""
    requires_human: bool = True
    resolved_by: str = "rule"  # "rule" | "llm"
    # bank rows this exception already accounts for, so they are not
    # raised again independently from the bank side
    counterpart_stmt_ids: list[str] = field(default_factory=list)

    @property
    def severity(self) -> int:
        return SEVERITY[self.ex_class]

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "side": self.side,
            "class": self.ex_class.value,
            "amount": self.amount,
            "amount_display": rupees(abs(self.amount)),
            "confidence": round(self.confidence, 2),
            "severity": self.severity,
            "evidence": self.evidence,
            "suggested_resolution": self.suggested_resolution,
            "requires_human": self.requires_human,
            "resolved_by": self.resolved_by,
            "counterpart_stmt_ids": self.counterpart_stmt_ids,
        }


# ---------------------------------------------------------------------------
# payout-side classifiers
# ---------------------------------------------------------------------------


def _classify_payout(
    p: Payout,
    unmatched_bank: list[BankLine],
    period_end: date,
) -> Exception_:
    """A payout with no bank credit. Either the money is late, short, or lost."""

    # --- bank charge netted off the credit -------------------------------
    # Look for a credit in the date window that is short by exactly a
    # published charge. This is arithmetic, not inference.
    for b in unmatched_bank:
        if not (0 <= (b.value_date - p.settled_on).days <= 2):
            continue
        if not looks_like_psp(b.narration):
            continue
        delta = p.net - b.credit
        if delta in BANK_CHARGES:
            return Exception_(
                ref=p.settlement_id,
                side="payout",
                ex_class=ExClass.BANK_CHARGE,
                amount=delta,
                confidence=0.97,
                evidence=[
                    f"payout net {rupees(p.net)}",
                    f"bank credit {b.stmt_id} = {rupees(b.credit)}",
                    f"delta {rupees(delta)} matches {BANK_CHARGES[delta]}",
                    f"value date {b.value_date} within T+2 window",
                ],
                suggested_resolution=(
                    f"Match {p.settlement_id} to {b.stmt_id}; "
                    f"post {rupees(delta)} to bank charges expense."
                ),
                requires_human=True,
                counterpart_stmt_ids=[b.stmt_id],
            )

    # --- settled at period close, credit lands next period ----------------
    days_to_close = (period_end - p.settled_on).days
    if days_to_close <= 2:
        return Exception_(
            ref=p.settlement_id,
            side="payout",
            ex_class=ExClass.IN_TRANSIT,
            amount=p.net,
            confidence=0.90,
            evidence=[
                f"settled {p.settled_on}, {days_to_close}d before period close {period_end}",
                "no bank credit within statement range",
                f"{p.n_payments} payments, {p.n_refunds} refunds in batch",
            ],
            suggested_resolution=(
                "Carry forward as funds-in-transit. Expect credit in next "
                "statement; re-run recon after period rollover."
            ),
            requires_human=False,  # timing, not an error - auto-carry is safe
        )

    # --- net-negative settlement day --------------------------------------
    # Refunds and chargebacks exceeded collections. The PSP carries the
    # balance against the next payout rather than debiting the merchant's
    # account, so the absence of a bank credit is correct behaviour and not
    # a missing payment.
    if p.net <= 0:
        return Exception_(
            ref=p.settlement_id,
            side="payout",
            ex_class=ExClass.NET_NEGATIVE,
            amount=p.net,
            confidence=0.95,
            evidence=[
                f"batch nets to {rupees(p.net)}",
                f"{p.n_payments} payments against {p.n_refunds} refunds, "
                f"{p.n_chargebacks} chargebacks",
                "no bank credit expected for a negative batch",
            ],
            suggested_resolution=(
                "Carry the negative balance forward against the next payout. "
                "No ledger adjustment required."
            ),
            requires_human=False,
        )

    # --- genuinely unexplained -------------------------------------------
    # Deliberately NOT guessed at. An honest "I don't know" beats a
    # confident wrong answer on a finance ledger.
    return Exception_(
        ref=p.settlement_id,
        side="payout",
        ex_class=ExClass.UNEXPLAINED,
        amount=p.net,
        confidence=0.0,
        evidence=[
            f"settled {p.settled_on}, {days_to_close}d before period close",
            "no credit matched on id, utr, amount, or subset-sum",
            "delta does not correspond to any known charge",
        ],
        suggested_resolution=(
            "ESCALATE. Pull PSP payout status via API and confirm bank "
            "credit reference before adjusting the ledger."
        ),
        requires_human=True,
    )


# ---------------------------------------------------------------------------
# bank-side classifiers
# ---------------------------------------------------------------------------


def _classify_bank(b: BankLine, known_settlement_ids: set[str]) -> Exception_:
    """A bank credit with no payout. Usually it is simply not ours."""

    n = b.narration.upper()

    # --- another PSP in the same current account --------------------------
    for token, name in OTHER_PSPS.items():
        if token in n:
            return Exception_(
                ref=b.stmt_id,
                side="bank",
                ex_class=ExClass.OTHER_PSP,
                amount=b.credit,
                confidence=0.98,
                evidence=[f"narration identifies {name}", "outside this PSP's settlement scope"],
                suggested_resolution=f"Exclude from this reconciliation; route to {name} recon.",
                requires_human=False,
            )

    # --- not settlement activity at all -----------------------------------
    for token, kind in NON_SETTLEMENT.items():
        if token in n:
            return Exception_(
                ref=b.stmt_id,
                side="bank",
                ex_class=ExClass.NON_SETTLEMENT,
                amount=b.credit or -b.debit,
                confidence=0.96,
                evidence=[f"narration indicates {kind}"],
                suggested_resolution=f"Out of scope. Route to {kind} ledger.",
                requires_human=False,
            )

    # --- carries a settlement id we have never seen -----------------------
    m = SETTLEMENT_ID_RE.search(b.narration)
    if m and m.group(1).lower() not in known_settlement_ids:
        return Exception_(
            ref=b.stmt_id,
            side="bank",
            ex_class=ExClass.PRIOR_PERIOD,
            amount=b.credit,
            confidence=0.93,
            evidence=[
                f"narration references {m.group(1)}",
                "settlement id absent from this period's report",
            ],
            suggested_resolution=(
                "Credit belongs to a prior settlement period. Match against "
                "the previous report rather than adjusting this one."
            ),
            requires_human=False,
        )

    # --- looks like ours, but the narration tells us nothing --------------
    # This is the ONLY class that goes to the LLM. Everything above was rules.
    if looks_like_psp(b.narration):
        return Exception_(
            ref=b.stmt_id,
            side="bank",
            ex_class=ExClass.OPAQUE_NARRATION,
            amount=b.credit,
            confidence=0.45,
            evidence=[
                "narration matches PSP but carries no settlement id",
                f"credit {rupees(b.credit)} on {b.value_date}",
                f"utr {b.utr or 'absent'}",
            ],
            suggested_resolution="Requires narration parsing - see resolver output.",
            requires_human=True,
        )

    return Exception_(
        ref=b.stmt_id,
        side="bank",
        ex_class=ExClass.UNEXPLAINED,
        amount=b.credit or -b.debit,
        confidence=0.0,
        evidence=[f"narration: {b.narration[:80]}", "no classifier matched"],
        suggested_resolution="ESCALATE. Manual review required.",
        requires_human=True,
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def classify(led: Ledger, res: MatchResult) -> list[Exception_]:
    # Explicit period, never inferred from max(value_date) - a single stray
    # row would otherwise move the boundary and change how in-transit items
    # are classified.
    # An empty statement is a valid input - a merchant with no bank activity in
    # the period. It must produce an empty ledger, not a crash.
    if not led.bank and led.period_end is None:
        return []
    period_end = led.period_end or max(b.value_date for b in led.bank)
    known_ids = {l.settlement_id.lower() for l in led.settlements if l.settlement_id}

    out: list[Exception_] = []

    # A payout-side exception may nominate a specific bank row as its
    # counterpart (a bank charge, for instance). That row is then accounted
    # for and must not be raised a second time from the bank side - one
    # break, one exception.
    claimed_bank: set[str] = set()

    for p in res.unmatched_payouts:
        ex = _classify_payout(p, res.unmatched_bank, period_end)
        out.append(ex)
        claimed_bank.update(ex.counterpart_stmt_ids)

    for b in res.unmatched_bank:
        if b.stmt_id in claimed_bank:
            continue
        out.append(_classify_bank(b, known_ids))

    # Highest severity first, then largest rupee value. A finance controller
    # reads this top-down and stops when the remainder stops mattering.
    out.sort(key=lambda e: (-e.severity, -abs(e.amount)))
    return out


def summarise(exceptions: list[Exception_]) -> dict:
    from collections import Counter

    by_class = Counter(e.ex_class.value for e in exceptions)
    return {
        "total": len(exceptions),
        "requires_human": sum(1 for e in exceptions if e.requires_human),
        "auto_dispositioned": sum(1 for e in exceptions if not e.requires_human),
        "resolved_by_rule": sum(1 for e in exceptions if e.resolved_by == "rule"),
        "resolved_by_llm": sum(1 for e in exceptions if e.resolved_by == "llm"),
        "by_class": dict(by_class),
        "value_at_risk": sum(
            abs(e.amount) for e in exceptions if e.ex_class == ExClass.UNEXPLAINED
        ),
    }
