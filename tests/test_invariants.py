"""Invariant tests — properties that must hold on every run, always.

Regression tests defend against specific bugs returning. These defend the
claims the project makes about itself. If any of these fail, a number quoted
in the README or the pitch has stopped being true.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from recon.exceptions import classify, summarise
from recon.generate import generate
from recon.match import build_payouts, match
from recon.models import OrderStatus, TxnType
from recon.order_match import match_orders

SEEDS = range(1, 51)


# --- the headline claim ----------------------------------------------------


def test_zero_false_positives_bank_leg():
    """The strongest claim in the project. A wrong match files bad data into a
    ledger, which is worse than leaving an item for review."""
    total = 0
    for seed in SEEDS:
        led = generate(seed=seed)
        res = match(led)
        for m in res.matches:
            truth = led.truth_payout_to_bank.get(m.payout_id)
            if truth and set(truth.split("+")) != set(m.bank_stmt_ids):
                total += 1
    assert total == 0, f"{total} false positives on the bank leg"


def test_zero_false_positives_order_leg():
    """A wrong order match misattributes revenue to the wrong customer."""
    total = 0
    for seed in SEEDS:
        led = generate(seed=seed)
        res = match_orders(led)
        for m in res.matches:
            if led.truth_entity_to_order.get(m.entity_id) != m.order_id:
                total += 1
    assert total == 0, f"{total} false positives on the order leg"


def test_match_rates_within_expected_band():
    """Guards both directions. A collapse means something broke; a jump toward
    100% usually means the data got easier, not the engine better."""
    bank, order = [], []
    for seed in SEEDS:
        led = generate(seed=seed)
        payouts = len({l.settlement_id for l in led.settlements if l.settlement_id})
        payments = sum(1 for l in led.settlements if l.txn_type == TxnType.PAYMENT)
        bank.append(len(match(led).matches) / payouts * 100)
        order.append(len(match_orders(led).matches) / payments * 100)

    bank_mean = sum(bank) / len(bank)
    order_mean = sum(order) / len(order)
    assert 82 <= bank_mean <= 96, f"bank leg at {bank_mean:.1f}%"
    assert 92 <= order_mean <= 99.5, f"order leg at {order_mean:.1f}%"


# --- money handling --------------------------------------------------------


def test_all_amounts_are_integer_paise():
    """Float rupees in a reconciliation engine accumulate residue and then
    either report phantom breaks or hide real ones behind a wide tolerance."""
    led = generate(seed=42)
    for o in led.orders:
        assert isinstance(o.amount, int), f"{o.order_id} amount is {type(o.amount)}"
    for l in led.settlements:
        for field in (l.gross, l.fee, l.tax, l.net):
            assert isinstance(field, int)
    for b in led.bank:
        assert isinstance(b.credit, int) and isinstance(b.debit, int)


def test_settlement_arithmetic_ties_out():
    """net must equal gross - fee - tax on every line, with no rounding slack."""
    for seed in SEEDS:
        led = generate(seed=seed)
        for l in led.settlements:
            assert l.net == l.gross - l.fee - l.tax, f"seed {seed}: {l.entity_id}"


def test_matched_payouts_tie_to_their_bank_credits():
    """Every exact-tier match must balance to the paise. Where it does not, the
    delta must be explicitly recorded rather than silently absorbed."""
    for seed in SEEDS:
        led = generate(seed=seed)
        res = match(led)
        payouts = {p.settlement_id: p for p in build_payouts(led)}
        rows = {b.stmt_id: b for b in led.bank}
        for m in res.matches:
            expected = payouts[m.payout_id].net
            got = sum(rows[s].credit for s in m.bank_stmt_ids)
            assert abs(expected - got - m.delta) <= 2, (
                f"seed {seed}: {m.payout_id} off by {expected - got - m.delta}"
            )


# --- determinism -----------------------------------------------------------


def test_same_seed_produces_identical_results():
    for seed in (1, 17, 42):
        a = classify(generate(seed=seed), match(generate(seed=seed)))
        b = classify(generate(seed=seed), match(generate(seed=seed)))
        assert [x.to_dict() for x in a] == [x.to_dict() for x in b]


def test_different_seeds_produce_different_data():
    """Guards against a seeding bug making every run identical."""
    a = generate(seed=1)
    b = generate(seed=2)
    assert [o.order_id for o in a.orders] != [o.order_id for o in b.orders]


# --- exception ledger ------------------------------------------------------


def test_every_exception_has_a_cause_and_a_resolution():
    """An exception without a proposed action is a task handed to a human with
    no starting point."""
    for seed in SEEDS:
        for ex in classify(generate(seed=seed), match(generate(seed=seed))):
            assert ex.ex_class is not None
            assert ex.suggested_resolution, f"seed {seed}: {ex.ref} has no resolution"
            assert ex.evidence, f"seed {seed}: {ex.ref} has no evidence"


def test_exceptions_sorted_by_severity_then_value():
    """A controller reads this top-down and stops when the rest stops mattering."""
    for seed in (1, 9, 23, 42):
        exceptions = classify(generate(seed=seed), match(generate(seed=seed)))
        keys = [(-e.severity, -abs(e.amount)) for e in exceptions]
        assert keys == sorted(keys), f"seed {seed}: ledger not severity-ranked"


def test_auto_disposition_rate_is_high_but_not_total():
    """If everything auto-disposes, the classifier has stopped escalating things
    it should. If nothing does, it is not doing useful work."""
    auto = human = 0
    for seed in SEEDS:
        s = summarise(classify(generate(seed=seed), match(generate(seed=seed))))
        auto += s["auto_dispositioned"]
        human += s["requires_human"]
    rate = auto / (auto + human) * 100
    assert 70 <= rate <= 97, f"auto-disposition at {rate:.0f}%"


def test_nothing_mutates_the_ledger():
    """Engine proposes, human disposes. No exception may mark itself resolved."""
    for seed in (1, 42):
        for ex in classify(generate(seed=seed), match(generate(seed=seed))):
            assert ex.resolved_by in ("rule", "llm", "heuristic")
            assert "POST TO LEDGER" not in ex.suggested_resolution.upper()


# --- generator honesty -----------------------------------------------------


def test_unpaid_orders_never_appear_in_settlement():
    """Attempted and created orders are noise the matcher must ignore."""
    for seed in SEEDS:
        led = generate(seed=seed)
        unpaid = {o.order_id for o in led.orders if o.status != OrderStatus.PAID}
        for l in led.settlements:
            if l.txn_type == TxnType.PAYMENT and l.order_id:
                assert l.order_id not in unpaid, f"seed {seed}: {l.order_id} settled unpaid"


def test_refunds_and_chargebacks_are_negative():
    for seed in SEEDS:
        for l in generate(seed=seed).settlements:
            if l.txn_type in (TxnType.REFUND, TxnType.CHARGEBACK):
                assert l.net < 0, f"seed {seed}: {l.entity_id} net {l.net}"


def test_ground_truth_covers_every_matchable_payout():
    """A scorer that disagrees with a correct engine pushes you to break working
    code — see build log item 5."""
    for seed in SEEDS:
        led = generate(seed=seed)
        rows = {b.stmt_id for b in led.bank}
        for sid, target in led.truth_payout_to_bank.items():
            for stmt in target.split("+"):
                assert stmt in rows, f"seed {seed}: truth points at missing {stmt}"
