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


def _tokens(raw: str) -> list[str]:
    if not raw:
        return []
    s = _PUNCT.sub(" ", raw.upper())
    return [t for t in _SPACES.split(s) if t and t not in _HONORIFICS]


def name_similarity(a: str, b: str) -> int:
    """Score two payer references 0-100.

    Three comparisons, each used only where it is valid.

    An **initial** ("M. Rao") is handled structurally, never by substring. A
    stripped initial produces a four-character needle, and `MRAO` is contained
    in `VIKRAMRAO` — so `M. Rao` scored 100 against Vikram Rao and 85 against
    the true Manish Rao. An initial carries one letter of evidence and must be
    compared as one letter, against the initial of the other name.

    Otherwise: token_sort_ratio on the sorted form, so reordered names compare
    equal; partial_ratio on the unsorted despaced form, so lost whitespace
    still matches. Feeding the sorted form to the substring comparison
    scrambles letter order — see build log item 8.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0

    has_initial = any(len(t) == 1 for t in ta + tb)
    if has_initial:
        return _initial_similarity(ta, tb)

    # A single token is either a full name with the space lost ("DIVYAPATEL")
    # or a surname with no given name at all ("Sharma"). They carry completely
    # different weight and must be told apart.
    #
    # The test: does the lone token account for the WHOLE of the other side, or
    # only for one part of it? `DIVYAPATEL` matches `Divya`+`Patel` joined, so
    # it is the full name. `Sharma` matches only the surname of `Rahul Sharma`,
    # so a given name is missing and the evidence is thin - capped below the
    # threshold, because substring comparison would otherwise return 100 and
    # claim any Sharma with full confidence.
    if len(ta) == 1 or len(tb) == 1:
        lone, other = (ta[0], tb) if len(ta) == 1 else (tb[0], ta)
        joined = "".join(other)
        if int(fuzz.ratio(lone, joined)) >= 88:
            return int(fuzz.ratio(lone, joined))
        surname_score = int(fuzz.ratio(lone, other[-1]))
        if surname_score < 85:
            return min(surname_score, 60)
        return min(75, surname_score)

    sa, sb = " ".join(sorted(ta)), " ".join(sorted(tb))
    ua, ub = "".join(ta), "".join(tb)
    return max(
        int(fuzz.token_sort_ratio(sa, sb)),
        int(fuzz.partial_ratio(ua, ub)),
    )


def _initial_similarity(ta: list[str], tb: list[str]) -> int:
    """Compare an abbreviated name against a full one.

    The surname must genuinely match; the initial must agree with the first
    letter of the corresponding given name. An initial that disagrees is
    positive evidence of a different person, not weak evidence of the same one.
    """
    surname_a, surname_b = ta[-1], tb[-1]
    surname_score = int(fuzz.ratio(surname_a, surname_b))
    if surname_score < 85:
        return min(surname_score, 60)

    given_a = [t for t in ta[:-1]]
    given_b = [t for t in tb[:-1]]
    if not given_a or not given_b:
        # Only a surname on one side. Real, but thin - never enough on its own.
        return min(surname_score, 70)

    if given_a[0][0] != given_b[0][0]:
        return 40  # different person

    # Initial agrees. If both are spelled out, score them properly.
    if len(given_a[0]) > 1 and len(given_b[0]) > 1:
        return int((surname_score + fuzz.ratio(given_a[0], given_b[0])) / 2)

    # One is an initial: matching surname plus matching initial is good
    # evidence, but never as strong as two spelled-out names agreeing.
    return min(92, surname_score)


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
