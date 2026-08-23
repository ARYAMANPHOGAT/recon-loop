"""Order ↔ settlement matching — the second leg.

The bank leg (match.py) answers "did the money arrive?". This answers "does
the money correspond to something we actually sold?". Both are needed before
the reconciliation is three-way.

Tier structure mirrors the bank leg, and for the same reason: cheapest and
most certain first, stopping on the first confident hit.

  O0  order_id present on the settlement line   -> exact
  O1  amount + capture window, unique candidate -> strong
  O2  normalised payer name + amount            -> probable
  --  anything left                             -> exception

O2 is where this leg differs from the bank leg. Bank narrations are machine
generated and yield to arithmetic; payer descriptions are typed by humans and
do not. `R. Patel` and `Rohan Patel` are the same person, and no amount of
exact matching will discover that.

Normalisation is handled by `normalise_reference`, which is the one place in
this project where fuzzy text is genuinely the right tool.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from rapidfuzz import fuzz

from .models import Ledger, Order, OrderStatus, Paise, SettlementLine, TxnType, rupees

# A settlement is captured within minutes of the order, not days.
CAPTURE_WINDOW_HOURS = 24

# Below this, two names are not the same person. Set high deliberately: a
# false order match misattributes revenue, which is worse than an unmatched
# line a human reviews.
NAME_MATCH_THRESHOLD = 82

# The margin the best candidate must beat the runner-up by. Without it, two
# customers with similar names and identical amounts produce a coin flip.
NAME_MARGIN = 12


class OTier:
    EXACT_ORDER_ID = "O0_order_id"
    AMOUNT_WINDOW = "O1_amount_window"
    NAME_FUZZY = "O2_name_fuzzy"


OCONFIDENCE = {
    OTier.EXACT_ORDER_ID: 1.00,
    OTier.AMOUNT_WINDOW: 0.90,
    OTier.NAME_FUZZY: 0.75,
}


@dataclass
class OrderMatch:
    order_id: str
    entity_id: str
    tier: str
    confidence: float
    note: str = ""


@dataclass
class OrderMatchResult:
    matches: list[OrderMatch] = field(default_factory=list)
    # Paid in the order book, never appeared in settlement. Revenue leakage
    # if real, so this list is the commercially important output.
    unsettled_orders: list[Order] = field(default_factory=list)
    # Settled with no corresponding order. Payment links and manual invoices
    # collected outside the ERP land here.
    orphan_settlements: list[SettlementLine] = field(default_factory=list)
    tier_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


# ---------------------------------------------------------------------------
# reference normalisation
# ---------------------------------------------------------------------------

_HONORIFICS = {"MR", "MRS", "MS", "DR", "SHRI", "SMT", "M/S"}
_PUNCT = re.compile(r"[^A-Z0-9 ]")
_SPACES = re.compile(r"\s+")


def normalise_reference(raw: str, sort_tokens: bool = True) -> str:
    """Reduce a human-typed name to a comparable form.

    Handles the variants the generator produces and that real payer
    descriptions contain: case, punctuation, honorifics, and lost whitespace.

    `sort_tokens` alphabetises the result so that reordered names compare
    equal. That is correct for token comparison and WRONG for substring
    comparison, which depends on original letter order - see name_similarity.

    Deliberately does NOT attempt to reverse initials ("R. Patel" to "Rohan
    Patel") or phonetic spellings ("Paatel" to "Patel"). Those need similarity
    scoring rather than normalisation, and pretending a rule can resolve them
    would push wrong matches through at high confidence.
    """
    if not raw:
        return ""
    s = _PUNCT.sub(" ", raw.upper())
    tokens = [t for t in _SPACES.split(s) if t and t not in _HONORIFICS]
    return " ".join(sorted(tokens) if sort_tokens else tokens)


def name_similarity(a: str, b: str) -> int:
    """Score two payer references 0-100.

    Two comparisons, each fed the form it actually needs:

      token_sort_ratio  gets the SORTED form, so "Patel Rohan" and "Rohan
                        Patel" compare equal.
      partial_ratio     gets the UNSORTED, despaced form, so "DIVYAPATEL"
                        matches "Divya Patel". Feeding it the sorted form
                        scrambles letter order and scores the wrong candidate
                        higher - see build log item 8.
    """
    sa, sb = normalise_reference(a), normalise_reference(b)
    if not sa or not sb:
        return 0
    ua = normalise_reference(a, sort_tokens=False).replace(" ", "")
    ub = normalise_reference(b, sort_tokens=False).replace(" ", "")
    return max(
        int(fuzz.token_sort_ratio(sa, sb)),
        int(fuzz.partial_ratio(ua, ub)),
    )


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------


def match_orders(led: Ledger, disabled_tiers: set[str] | None = None) -> OrderMatchResult:
    if disabled_tiers is None:
        disabled_tiers = set()

    payments = [l for l in led.settlements if l.txn_type == TxnType.PAYMENT]
    paid_orders = [o for o in led.orders if o.status == OrderStatus.PAID]

    res = OrderMatchResult()
    claimed_orders: set[str] = set()
    claimed_entities: set[str] = set()

    def claim(o: Order, ln: SettlementLine, tier: str, note: str = ""):
        res.matches.append(
            OrderMatch(
                order_id=o.order_id,
                entity_id=ln.entity_id,
                tier=tier,
                confidence=OCONFIDENCE[tier],
                note=note,
            )
        )
        res.tier_counts[tier] += 1
        claimed_orders.add(o.order_id)
        claimed_entities.add(ln.entity_id)

    order_by_id = {o.order_id: o for o in paid_orders}

    # --- O0: the settlement line carries the order id ---------------------
    if OTier.EXACT_ORDER_ID not in disabled_tiers:
        for ln in payments:
            if ln.entity_id in claimed_entities or not ln.order_id:
                continue
            o = order_by_id.get(ln.order_id)
            if o and o.order_id not in claimed_orders:
                claim(o, ln, OTier.EXACT_ORDER_ID)

    # --- O1: amount + capture window, unique candidate --------------------
    if OTier.AMOUNT_WINDOW not in disabled_tiers:
        by_amount: dict[Paise, list[Order]] = defaultdict(list)
        for o in paid_orders:
            if o.order_id not in claimed_orders:
                by_amount[o.amount].append(o)

        for ln in payments:
            if ln.entity_id in claimed_entities:
                continue
            cands = [
                o
                for o in by_amount.get(ln.gross, [])
                if o.order_id not in claimed_orders
                and abs((ln.captured_at - o.created_at).total_seconds())
                <= CAPTURE_WINDOW_HOURS * 3600
            ]
            # Only claim when the candidate is unique. Two orders for the same
            # amount in the same window are indistinguishable on this evidence,
            # and guessing between them misattributes revenue.
            if len(cands) == 1:
                claim(cands[0], ln, OTier.AMOUNT_WINDOW)

    # --- O2: fuzzy payer name --------------------------------------------
    if OTier.NAME_FUZZY not in disabled_tiers:
        for ln in payments:
            if ln.entity_id in claimed_entities or not ln.payer_description:
                continue
            scored = []
            for o in paid_orders:
                if o.order_id in claimed_orders or not o.customer_name:
                    continue
                if abs((ln.captured_at - o.created_at).total_seconds()) > CAPTURE_WINDOW_HOURS * 3600:
                    continue
                scored.append((name_similarity(ln.payer_description, o.customer_name), o))
            if not scored:
                continue
            scored.sort(key=lambda x: -x[0])
            best_score, best = scored[0]
            runner_up = scored[1][0] if len(scored) > 1 else 0
            if best_score >= NAME_MATCH_THRESHOLD and (best_score - runner_up) >= NAME_MARGIN:
                claim(
                    best,
                    ln,
                    OTier.NAME_FUZZY,
                    note=f"name score {best_score} vs next {runner_up}",
                )

    res.unsettled_orders = [o for o in paid_orders if o.order_id not in claimed_orders]
    res.orphan_settlements = [l for l in payments if l.entity_id not in claimed_entities]
    return res
