"""Three-way matching engine.

Design rule: every tier below is deterministic and cheap. No LLM is called
anywhere in this file. Narration text that survives all tiers is handed to
the resolver (resolve.py), which is the only place a model is used.

Tiers run in order and stop on first confident hit:

  T0  settlement_id present in narration      -> exact
  T1  UTR match against payout reference      -> exact
  T2  amount + date window                    -> strong
  T3  amount + date window, fuzzy narration   -> probable
  T4  subset-sum over same-day credits        -> probable (split payouts)
  --  anything left                           -> exception, sent to resolver

Every match carries the tier that produced it, so the eval harness can report
which tiers are doing real work and which are decoration.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
from typing import Iterable, Optional

from rapidfuzz import fuzz

from .models import BankLine, Ledger, Paise, SettlementLine, TxnType, rupees

# Tolerance for fee-rounding residue. Two paise, not two rupees - if we need
# more slack than this the match is wrong, not rounded.
AMOUNT_TOLERANCE: Paise = 2

# Published bank transfer charges (fee + 18% GST), in paise. A subset of
# credits that sums to the payout minus exactly one of these is a split
# payout that was levied a transfer charge - arithmetic, not inference.
BANK_CHARGES: dict[Paise, str] = {
    1_180: "NEFT Rs.10 + GST",
    1_770: "NEFT Rs.15 + GST",
    2_950: "NEFT Rs.25 + GST",
    5_900: "RTGS Rs.50 + GST",
    11_800: "RTGS Rs.100 + GST",
}

# Banks credit on the settlement date, but cutoff misses push it out.
DATE_WINDOW_DAYS = 2

SETTLEMENT_ID_RE = re.compile(r"\b(setl_[0-9]{4}[0-9]{3})\b", re.IGNORECASE)
UTR_RE = re.compile(r"\b(\d{11,12})\b")

# Narration tokens that indicate a PSP settlement rather than other activity.
PSP_TOKENS = ("RAZORPAY", "RZRPY", "RAZORPAYSOFTW", "RAZORPAY SOFTW")


class Tier:
    EXACT_SETTLEMENT_ID = "T0_settlement_id"
    EXACT_UTR = "T1_utr"
    AMOUNT_DATE = "T2_amount_date"
    AMOUNT_DATE_FUZZY = "T3_amount_date_fuzzy"
    SUBSET_SUM = "T4_subset_sum"
    SUBSET_SUM_CHARGED = "T4b_subset_sum_charged"


CONFIDENCE = {
    Tier.EXACT_SETTLEMENT_ID: 1.00,
    Tier.EXACT_UTR: 0.99,
    Tier.AMOUNT_DATE: 0.92,
    Tier.AMOUNT_DATE_FUZZY: 0.80,
    Tier.SUBSET_SUM: 0.72,
    Tier.SUBSET_SUM_CHARGED: 0.70,
}


@dataclass
class Payout:
    """A settlement batch: many settlement lines, one expected bank credit."""

    settlement_id: str
    settled_on: date
    lines: list[SettlementLine]

    @property
    def net(self) -> Paise:
        return sum(l.net for l in self.lines)

    @property
    def n_payments(self) -> int:
        return sum(1 for l in self.lines if l.txn_type == TxnType.PAYMENT)

    @property
    def n_refunds(self) -> int:
        return sum(1 for l in self.lines if l.txn_type == TxnType.REFUND)

    @property
    def n_chargebacks(self) -> int:
        return sum(1 for l in self.lines if l.txn_type == TxnType.CHARGEBACK)


@dataclass
class Match:
    payout_id: str
    bank_stmt_ids: list[str]
    tier: str
    confidence: float
    delta: Paise = 0
    note: str = ""


@dataclass
class MatchResult:
    matches: list[Match] = field(default_factory=list)
    unmatched_payouts: list[Payout] = field(default_factory=list)
    unmatched_bank: list[BankLine] = field(default_factory=list)
    tier_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def build_payouts(led: Ledger) -> list[Payout]:
    """Roll settlement lines up into payout batches."""
    grouped: dict[str, list[SettlementLine]] = defaultdict(list)
    for ln in led.settlements:
        if ln.settlement_id:
            grouped[ln.settlement_id].append(ln)
    out = []
    for sid, lines in grouped.items():
        settled = next((l.settled_on for l in lines if l.settled_on), None)
        if settled:
            out.append(Payout(settlement_id=sid, settled_on=settled, lines=lines))
    return sorted(out, key=lambda p: (p.settled_on, p.settlement_id))


def looks_like_psp(narration: str) -> bool:
    n = narration.upper()
    return any(t in n for t in PSP_TOKENS)


def within_window(bank_date: date, settle_date: date) -> bool:
    return 0 <= (bank_date - settle_date).days <= DATE_WINDOW_DAYS


def close_enough(a: Paise, b: Paise) -> bool:
    return abs(a - b) <= AMOUNT_TOLERANCE


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------


def match(led: Ledger) -> MatchResult:
    payouts = build_payouts(led)
    credits = [b for b in led.bank if b.credit > 0]

    res = MatchResult()
    claimed_bank: set[str] = set()
    claimed_payout: set[str] = set()

    def claim(p: Payout, rows: list[BankLine], tier: str, delta: Paise = 0, note: str = ""):
        res.matches.append(
            Match(
                payout_id=p.settlement_id,
                bank_stmt_ids=[r.stmt_id for r in rows],
                tier=tier,
                confidence=CONFIDENCE[tier],
                delta=delta,
                note=note,
            )
        )
        res.tier_counts[tier] += 1
        claimed_payout.add(p.settlement_id)
        for r in rows:
            claimed_bank.add(r.stmt_id)

    # --- T0: settlement id printed in the narration ------------------------
    narr_index: dict[str, list[BankLine]] = defaultdict(list)
    for b in credits:
        m = SETTLEMENT_ID_RE.search(b.narration)
        if m:
            narr_index[m.group(1).lower()].append(b)

    for p in payouts:
        if p.settlement_id in claimed_payout:
            continue
        hits = [b for b in narr_index.get(p.settlement_id.lower(), []) if b.stmt_id not in claimed_bank]
        if not hits:
            continue
        # single credit carrying the id
        exact = [b for b in hits if close_enough(b.credit, p.net)]
        if exact:
            claim(p, [exact[0]], Tier.EXACT_SETTLEMENT_ID)
            continue
        # several credits carrying the same id -> split payout
        if len(hits) > 1 and close_enough(sum(b.credit for b in hits), p.net):
            claim(p, hits, Tier.EXACT_SETTLEMENT_ID, note="split across bank credits")

    # --- T1: UTR -----------------------------------------------------------
    # A settlement report does not carry the bank UTR, so this tier only helps
    # where the narration lost the settlement id but kept a UTR we already
    # associated with a payout via a sibling row. Kept deliberately narrow:
    # matching on UTR alone across unrelated payouts is how you create
    # false positives that look confident.
    utr_to_payout: dict[str, str] = {}
    for m in res.matches:
        for sid in m.bank_stmt_ids:
            row = next((b for b in credits if b.stmt_id == sid), None)
            if row and row.utr:
                utr_to_payout[row.utr] = m.payout_id

    for p in payouts:
        if p.settlement_id in claimed_payout:
            continue
        cands = [
            b
            for b in credits
            if b.stmt_id not in claimed_bank
            and b.utr
            and utr_to_payout.get(b.utr) == p.settlement_id
        ]
        if cands and close_enough(sum(b.credit for b in cands), p.net):
            claim(p, cands, Tier.EXACT_UTR)

    # --- T2 / T3: amount + date window ------------------------------------
    by_date: dict[date, list[BankLine]] = defaultdict(list)
    for b in credits:
        by_date[b.value_date].append(b)

    for p in payouts:
        if p.settlement_id in claimed_payout:
            continue
        window = []
        for d in range(DATE_WINDOW_DAYS + 1):
            window.extend(by_date.get(p.settled_on + timedelta(days=d), []))
        window = [b for b in window if b.stmt_id not in claimed_bank]

        amt_hits = [b for b in window if close_enough(b.credit, p.net)]
        if len(amt_hits) == 1:
            b = amt_hits[0]
            tier = Tier.AMOUNT_DATE if looks_like_psp(b.narration) else Tier.AMOUNT_DATE_FUZZY
            claim(p, [b], tier)
            continue
        if len(amt_hits) > 1:
            # ambiguous on amount alone - break the tie on narration
            scored = sorted(
                amt_hits,
                key=lambda b: fuzz.partial_ratio("RAZORPAY SOFTWARE", b.narration.upper()),
                reverse=True,
            )
            best, second = scored[0], scored[1]
            s1 = fuzz.partial_ratio("RAZORPAY SOFTWARE", best.narration.upper())
            s2 = fuzz.partial_ratio("RAZORPAY SOFTWARE", second.narration.upper())
            if s1 >= 80 and s1 - s2 >= 15:
                claim(p, [best], Tier.AMOUNT_DATE_FUZZY, note=f"tie broken on narration ({s1} vs {s2})")
            # else: leave it. An ambiguous match is worse than no match.

    # --- T4: subset sum for split payouts ---------------------------------
    for p in payouts:
        if p.settlement_id in claimed_payout:
            continue
        window = []
        for d in range(DATE_WINDOW_DAYS + 1):
            window.extend(by_date.get(p.settled_on + timedelta(days=d), []))
        window = [
            b for b in window if b.stmt_id not in claimed_bank and looks_like_psp(b.narration)
        ]
        if not (2 <= len(window) <= 12):
            continue
        found = None
        for k in (2, 3):
            for combo in combinations(window, k):
                if close_enough(sum(b.credit for b in combo), p.net):
                    found = list(combo)
                    break
            if found:
                break
        if found:
            claim(p, found, Tier.SUBSET_SUM, note=f"{len(found)} credits summed to payout")
            continue

        # A split payout that was also levied a bank transfer charge lands
        # short by exactly that charge. Solving it here keeps a deterministic
        # problem out of the resolver - handing arithmetic to a language
        # model is the wrong tool in the wrong place.
        for k in (2, 3):
            for combo in combinations(window, k):
                delta = p.net - sum(b.credit for b in combo)
                if delta in BANK_CHARGES:
                    found = list(combo)
                    break
            if found:
                break
        if found:
            delta = p.net - sum(b.credit for b in found)
            claim(
                p,
                found,
                Tier.SUBSET_SUM_CHARGED,
                delta=delta,
                note=f"{len(found)} credits summed to payout less {BANK_CHARGES[delta]}",
            )

    res.unmatched_payouts = [p for p in payouts if p.settlement_id not in claimed_payout]
    res.unmatched_bank = [b for b in credits if b.stmt_id not in claimed_bank]
    return res
