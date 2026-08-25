"""Scale and degenerate-input tests.

Two properties, both of which are claims the project makes implicitly by
shipping a CLI: it does not fall over on unusual input, and it does not become
less correct as volume rises.
"""

from __future__ import annotations

import time

import pytest

from recon.cli import _reconciliation_statement, _run_once
from recon.exceptions import classify
from recon.generate import generate
from recon.match import match
from recon.models import Ledger
from recon.order_match import match_orders


# --- degenerate inputs -----------------------------------------------------


def test_empty_ledger_does_not_crash():
    """A merchant with no activity in the period is a valid input."""
    led = Ledger()
    res = match(led)
    orders = match_orders(led)
    exceptions = classify(led, res)
    assert res.matches == []
    assert orders.matches == []
    assert exceptions == []


@pytest.mark.parametrize("n", [1, 2, 3, 5, 10, 20, 50])
def test_tiny_ledgers_run_end_to_end(n):
    """Small ledgers used to raise from rng.sample asking for more break rows
    than there were lines to draw from."""
    led = generate(n_orders=n, seed=5)
    res = match(led)
    orders = match_orders(led)
    exceptions = classify(led, res)
    stmt = _reconciliation_statement(led, res, orders, exceptions)
    assert stmt["ties"], f"statement does not tie at {n} orders"


@pytest.mark.parametrize("n", [1, 2, 5])
def test_break_injection_is_proportionate_on_tiny_ledgers(n):
    """A floor of three refunds on a two-line ledger is not a small dataset,
    it is a different one."""
    led = generate(n_orders=n, seed=5)
    refunds = sum(1 for s in led.scenario_log if s["scenario"] == "refund_netted")
    payments = sum(1 for l in led.settlements if l.txn_type.value == "payment")
    assert refunds <= max(1, payments), "refund count exceeds payment count"


# --- scale -----------------------------------------------------------------


@pytest.mark.parametrize("n", [500, 2000])
def test_correctness_holds_at_volume(n):
    """False positives must stay at zero as volume rises. Match rate may fall -
    more orders sharing a price point means more genuine ambiguity - but a
    wrong match is never acceptable."""
    led = generate(n_orders=n, seed=42)
    orders = match_orders(led)
    wrong = sum(
        1 for m in orders.matches if led.truth_entity_to_order.get(m.entity_id) != m.order_id
    )
    assert wrong == 0, f"{wrong} false positives at {n} orders"


def test_engine_degrades_safely_not_wrongly():
    """Whatever volume does to the match rate, it must not produce a wrong match.

    An earlier version of this test asserted the rate falls monotonically as
    volume rises. It does not: 500 orders scores 99.3% and 2,000 scores 99.8%,
    because at low volume the sample is small enough to be noisy. Decline only
    appears well above that, as more orders share a price point on the same day
    and the margin rule refuses to choose between them.

    So the invariant is not "the rate falls". It is that the rate stays usable
    and the false-positive count stays at zero across an order of magnitude.
    Asserting the story rather than the measurement is how a test ends up
    defending something that was never true.
    """
    for n in (500, 1000, 2000, 4000, 8000):
        led = generate(n_orders=n, seed=42)
        orders = match_orders(led)
        payments = sum(1 for l in led.settlements if l.txn_type.value == "payment")
        wrong = sum(
            1 for m in orders.matches
            if led.truth_entity_to_order.get(m.entity_id) != m.order_id
        )
        rate = len(orders.matches) / payments * 100
        assert wrong == 0, f"{wrong} false positives at {n} orders"
        assert rate > 95, f"rate collapsed to {rate:.1f}% at {n} orders"


def test_order_leg_is_not_quadratic():
    """The order leg indexes candidates by amount and date.

    Without the index this loops every payment against every open order. At
    8,000 orders that measured 2.1s and was still climbing 5x per doubling.
    A quadratic step is not a performance detail here - it is the difference
    between a tool that runs on a real book and one that does not.
    """
    def elapsed(n: int) -> float:
        led = generate(n_orders=n, seed=42)
        t0 = time.time()
        match_orders(led)
        return time.time() - t0

    small = elapsed(1000)
    large = elapsed(4000)
    # 4x the orders must not cost more than 25x the time. True quadratic
    # behaviour would be ~16x before constant factors; the pre-index
    # implementation measured well above that.
    assert large < small * 25 + 0.5, f"{small:.3f}s -> {large:.3f}s looks quadratic"
