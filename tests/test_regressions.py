"""Regression tests — one per bug in BUILD_LOG.md.

Every test here is named after the build log entry it defends. A reader can go
from "he says he fixed this" to "here is the test that proves it stays fixed".

A bug fixed without a test is a bug scheduled to return.
"""

from __future__ import annotations

import json
from collections import defaultdict

import pytest

from recon.exceptions import ExClass, classify
from recon.generate import generate
from recon.match import match, build_payouts
from recon.models import OrderStatus, TxnType
from recon.order_match import match_orders, name_similarity, normalise_reference

SEEDS = range(1, 31)


# --- item 1: generator collapsed 30 days into 13 bank rows -----------------


def test_item01_bank_rows_not_collapsed():
    """Instant-settlement sub-batching must produce many credits per period.

    One payout per day made bank matching trivial. If this count falls back to
    roughly one per day, the generator has stopped exercising the matcher.
    """
    led = generate(seed=42)
    credits = [b for b in led.bank if b.credit > 0]
    assert len(credits) >= 30, f"only {len(credits)} bank credits - batching regressed"


def test_item01_multiple_payouts_land_on_same_date():
    """Several credits on one date is what makes narration matching necessary."""
    led = generate(seed=42)
    by_date = defaultdict(int)
    for b in led.bank:
        if b.credit > 0:
            by_date[b.value_date] += 1
    assert max(by_date.values()) >= 2


# --- item 2: 100% match rate meant the data was too easy -------------------


def test_item02_match_rate_is_not_perfect():
    """A perfect score means the breaks are not hard enough to measure anything.

    The hard break classes (bank charges, in-transit, prior period, rival PSP)
    exist precisely so the engine has something real to fail on.
    """
    rates = []
    for seed in SEEDS:
        led = generate(seed=seed)
        res = match(led)
        payouts = len({l.settlement_id for l in led.settlements if l.settlement_id})
        rates.append(len(res.matches) / payouts * 100)
    mean_rate = sum(rates) / len(rates)
    assert mean_rate < 99.0, "match rate suspiciously perfect - breaks too easy"
    assert mean_rate > 80.0, f"match rate collapsed to {mean_rate:.1f}%"


def test_item02_hard_break_classes_present():
    seen = set()
    for seed in SEEDS:
        seen.update(s["scenario"] for s in generate(seed=seed).scenario_log)
    for required in (
        "bank_charge_deducted",
        "payout_in_transit",
        "prior_period_credit",
        "other_psp_credit",
    ):
        assert required in seen, f"hard break class {required} no longer generated"


# --- item 3: same break counted twice, from both sides ---------------------


def test_item03_no_double_counted_exceptions():
    """A bank row claimed by a payout exception must not be raised again.

    One underlying break, one exception. Counting it from both sides inflates
    the ledger and sends a controller chasing an item already accounted for.
    """
    for seed in SEEDS:
        led = generate(seed=seed)
        res = match(led)
        exceptions = classify(led, res)
        claimed = set()
        for ex in exceptions:
            claimed.update(ex.counterpart_stmt_ids)
        raised = {ex.ref for ex in exceptions if ex.side == "bank"}
        overlap = claimed & raised
        assert not overlap, f"seed {seed}: {overlap} raised twice"


# --- item 4: statement period was inferred, and the inference was wrong ----


def test_item04_period_is_explicit():
    led = generate(seed=42)
    assert led.period_start is not None
    assert led.period_end is not None


def test_item04_no_bank_rows_past_period_close():
    """A stray row past the close moves an inferred boundary and reclassifies
    in-transit payouts as unexplained."""
    for seed in SEEDS:
        led = generate(seed=seed)
        for b in led.bank:
            assert b.value_date <= led.period_end, f"seed {seed}: {b.stmt_id} past close"


def test_item04_in_transit_not_reported_as_unexplained():
    for seed in SEEDS:
        led = generate(seed=seed)
        transit = {
            s["settlement_id"]
            for s in led.scenario_log
            if s["scenario"] == "payout_in_transit"
        }
        if not transit:
            continue
        exceptions = classify(led, match(led))
        for ex in exceptions:
            if ex.ref in transit:
                assert ex.ex_class != ExClass.UNEXPLAINED, (
                    f"seed {seed}: in-transit payout {ex.ref} escalated as unexplained"
                )


# --- item 5: settlement id collision produced a false positive -------------


def test_item05_settlement_ids_are_unique():
    """Random id suffixes collide once a day carries several batches, silently
    merging two payouts during roll-up and corrupting ground truth."""
    for seed in SEEDS:
        led = generate(seed=seed)
        payouts = build_payouts(led)
        ids = [p.settlement_id for p in payouts]
        assert len(ids) == len(set(ids)), f"seed {seed}: duplicate settlement id"


def test_item05_one_settled_date_per_settlement_id():
    for seed in SEEDS:
        led = generate(seed=seed)
        dates = defaultdict(set)
        for l in led.settlements:
            if l.settlement_id and l.settled_on:
                dates[l.settlement_id].add(l.settled_on)
        for sid, ds in dates.items():
            assert len(ds) == 1, f"seed {seed}: {sid} spans {ds} - ids collided"


# --- item 6: classifier gave up far too often ------------------------------


def test_item06_unexplained_is_rare():
    """`unexplained` should be a real signal, not the default outcome.

    Not asserted to be zero: a classifier that always produces a cause is
    overconfident, not accurate.
    """
    total = 0
    for seed in SEEDS:
        exceptions = classify(generate(seed=seed), match(generate(seed=seed)))
        total += sum(1 for e in exceptions if e.ex_class == ExClass.UNEXPLAINED)
    assert total <= len(list(SEEDS)) * 0.5, f"{total} unexplained across {len(list(SEEDS))} seeds"


def test_item06_net_negative_days_classified():
    """Refunds exceeding collections means no bank credit is expected. That is
    correct PSP behaviour, not missing money."""
    found = False
    for seed in SEEDS:
        led = generate(seed=seed)
        exceptions = classify(led, match(led))
        for ex in exceptions:
            if ex.ex_class == ExClass.NET_NEGATIVE:
                found = True
                assert not ex.requires_human, "carry-forward should auto-dispose"
    assert found, "net_negative_carried never fired - class may be dead"


# --- item 7: LLM resolver was handed an arithmetic problem -----------------


def test_item07_charged_split_payouts_solved_deterministically():
    """No exception should survive that a bank-charge lookup would explain.

    The original assertion here was `opaque == 0`, which held for the Day-1
    dataset and stopped holding once the generator got harder. Zero was a
    property of that data, not a guarantee — some credits are genuinely
    ambiguous and should reach a human.

    The real invariant is narrower and permanent: if an opaque case has a delta
    that lands exactly on a published bank charge, T4b failed to solve
    arithmetic and a deterministic problem is being routed onward.
    """
    from recon.match import BANK_CHARGES

    for seed in SEEDS:
        led = generate(seed=seed)
        res = match(led)
        exceptions = classify(led, res)
        for ex in exceptions:
            if ex.ex_class != ExClass.OPAQUE_NARRATION:
                continue
            row = next(b for b in led.bank if b.stmt_id == ex.ref)
            for p in res.unmatched_payouts:
                if abs((row.value_date - p.settled_on).days) > 3:
                    continue
                delta = p.net - row.credit
                assert delta not in BANK_CHARGES, (
                    f"seed {seed}: {ex.ref} vs {p.settlement_id} differs by "
                    f"{delta} paise, an explainable bank charge - T4b regressed"
                )


def test_item07_opaque_narrations_stay_rare():
    """Ambiguous credits are acceptable; a flood of them is not.

    If this climbs, either the deterministic tiers have weakened or the
    generator has started producing noise rather than realistic breaks.
    """
    opaque = 0
    for seed in SEEDS:
        led = generate(seed=seed)
        exceptions = classify(led, match(led))
        opaque += sum(1 for e in exceptions if e.ex_class == ExClass.OPAQUE_NARRATION)
    n = len(list(SEEDS))
    assert opaque <= n * 0.5, f"{opaque} opaque narrations across {n} seeds"


def test_item07_no_llm_calls_in_matching_path():
    """The matching engine imports nothing that can reach a model."""
    import inspect

    import recon.match as m

    src = inspect.getsource(m)
    for banned in ("anthropic", "openai", "requests.post", "http"):
        assert banned not in src.lower(), f"match.py references {banned}"


# --- item 8: fuzzy scorer preferred the wrong customer ---------------------


def test_item08_sorted_form_not_used_for_substring_comparison():
    """`PRIYAMEHTA` must score higher against `Priya Mehta` than `Divya Mehta`.

    Sorting tokens destroys the letter order that substring comparison depends
    on. Feeding one normalised string to both comparisons scored the wrong
    customer 22 points higher.
    """
    correct = name_similarity("PRIYAMEHTA", "Priya Mehta")
    decoy = name_similarity("PRIYAMEHTA", "Divya Mehta")
    assert correct > decoy, f"scorer prefers decoy: {correct} vs {decoy}"
    assert correct >= 95


@pytest.mark.parametrize(
    "psp,book,floor",
    [
        ("R. Patel", "Rohan Patel", 85),
        ("Patel Rohan", "Rohan Patel", 95),
        ("Meeraa Chopra", "Meera Chopra", 90),
        ("MR AMIT SINGH", "Amit Singh", 95),
        ("DIVYAPATEL", "Divya Patel", 95),
    ],
)
def test_item08_name_variants_score_above_threshold(psp, book, floor):
    assert name_similarity(psp, book) >= floor


def test_item08_different_people_score_low():
    assert name_similarity("Divya Patel", "Divya Joshi") < 82
    assert name_similarity("Rahul Sharma", "Priya Reddy") < 82


def test_item08_normalise_sort_flag_changes_output():
    assert normalise_reference("Rohan Patel") == normalise_reference("Patel Rohan")
    unsorted_a = normalise_reference("Rohan Patel", sort_tokens=False)
    unsorted_b = normalise_reference("Patel Rohan", sort_tokens=False)
    assert unsorted_a != unsorted_b


# --- item 9: generators that were too easy ---------------------------------


def test_item09_order_id_absent_for_some_channels():
    """Payment links and POS collections are raised outside the ERP."""
    led = generate(seed=42)
    absent = [
        l for l in led.settlements if l.txn_type == TxnType.PAYMENT and l.order_id is None
    ]
    assert absent, "every settlement carries an order id - order leg is trivial"


def test_item09_amounts_collide_on_price_points():
    """Merchants sell at price points; many orders share an amount exactly."""
    led = generate(seed=42)
    counts = defaultdict(int)
    for o in led.orders:
        counts[o.amount] += 1
    assert max(counts.values()) >= 3, "no amount collisions - name tier never fires"


def test_item09_name_tier_does_real_work():
    total = 0
    for seed in SEEDS:
        total += match_orders(generate(seed=seed)).tier_counts.get("O2_name_fuzzy", 0)
    assert total > 0, "O2 never fires - fuzzy matching is dead code"


# --- item 10: deterministic engine produced non-identical files ------------


def test_item10_run_output_is_byte_identical(tmp_path):
    """Wall-clock timing in a reproducible artefact breaks hash comparison.

    The obvious response to a flaky check - loosening the comparison - would
    destroy its value, so the timing was moved out instead.
    """
    import time

    from recon.cli import main

    a, b = tmp_path / "a", tmp_path / "b"
    main(["run", "--seed", "7", "--out", str(a)])
    # Force a different wall-clock reading between the two runs. Without this
    # the test can pass by luck when both runs land in the same millisecond,
    # which is how the summary.md leak survived a full green suite locally and
    # only failed on a slower machine.
    time.sleep(0.05)
    main(["run", "--seed", "7", "--out", str(b)])

    assert (a / "run.json").read_bytes() == (b / "run.json").read_bytes()
    assert (a / "summary.md").read_bytes() == (b / "summary.md").read_bytes()
    assert (a / "exceptions.csv").read_bytes() == (b / "exceptions.csv").read_bytes()
    # The dashboard inlines the run payload, so any non-determinism upstream
    # shows up here too.
    assert (a / "dashboard.html").read_bytes() == (b / "dashboard.html").read_bytes()


def test_item10_no_timing_key_in_artefact(tmp_path):
    from recon.cli import main

    main(["run", "--seed", "3", "--out", str(tmp_path)])
    data = json.loads((tmp_path / "run.json").read_text())
    assert "runtime_ms" not in data
    assert not any(k.startswith("_") for k in data)


# --- item 11: ablation compared mismatched seed counts ---------------------


def test_item11_ablation_accepts_seed_count():
    """Baseline and ablation must run over the same population, or the deltas
    are sampling variance rather than tier contribution."""
    import inspect

    from recon.eval import ablation_stub

    sig = inspect.signature(ablation_stub)
    assert "n_seeds" in sig.parameters


def test_item11_disabling_a_tier_changes_the_result():
    """If disabling T2 changes nothing, the ablation is not wired up."""
    led = generate(seed=42)
    full = len(match(led).matches)
    without_t2 = len(match(led, disabled_tiers={"T2_amount_date"}).matches)
    assert without_t2 < full, "disabling T2 had no effect - ablation is inert"


# --- item 21: an initial matched by substring hit the wrong person ---------


def test_item21_initial_compared_structurally_not_by_substring():
    """`M. Rao` must not match `Vikram Rao`.

    Stripping spaces turns an initial into a four-character needle, and
    `MRAO` is contained in `VIKRAMRAO`. The substring comparison returned 100
    for a coincidence and 85 for the true match.
    """
    assert name_similarity("M. Rao", "Manish Rao") > name_similarity("M. Rao", "Vikram Rao")
    assert name_similarity("M. Rao", "Vikram Rao") < 60
    assert name_similarity("M. Rao", "Manish Rao") >= 85


@pytest.mark.parametrize(
    "abbrev,wrong",
    [
        ("A. Sharma", "Rahul Sharma"),
        ("S. Patel", "Karan Patel"),
        ("P. Iyer", "Vikram Iyer"),
        ("D. Singh", "Manish Singh"),
    ],
)
def test_item21_disagreeing_initial_scores_low(abbrev, wrong):
    """A disagreeing initial is evidence of a different person, not weak
    evidence of the same one."""
    assert name_similarity(abbrev, wrong) < 60


def test_item21_surname_alone_is_capped():
    """A surname with no given name on one side is real but thin evidence.

    It is capped below the match threshold, so it can never claim a match on
    its own - but it stays above zero, because it is genuinely weak evidence
    rather than a contradiction.
    """
    from recon.order_match import NAME_MATCH_THRESHOLD

    score = name_similarity("Sharma", "Rahul Sharma")
    assert score < NAME_MATCH_THRESHOLD, "surname alone would claim a match"
    assert score > 50, "surname alone should not read as a contradiction"


def test_item21_no_false_positives_at_200_seeds():
    """The 100-seed sweep reported zero. The 200-seed sweep found one."""
    total = 0
    for seed in range(1, 201):
        led = generate(seed=seed)
        for m in match_orders(led).matches:
            if led.truth_entity_to_order.get(m.entity_id) != m.order_id:
                total += 1
    assert total == 0, f"{total} order-leg false positives across 200 seeds"
