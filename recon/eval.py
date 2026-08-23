"""Evaluation harness — measure the engine across many seeds.

The only number that matters is one you've measured across many seeds, not
a single run. This harness sweeps N seeds (default 100), scores each against
ground truth, and reports:

  - Match rate (mean, min, max, stddev)
  - False positives
  - Exceptions per run
  - Performance by tier (which tiers do actual work)
  - Performance by break class (which injected scenarios matter)

All results are deterministic: same seed → same output, always. A judge can
verify the numbers by running the same command.
"""

from __future__ import annotations

import statistics as st
from collections import defaultdict, Counter
from dataclasses import dataclass

from .generate import generate
from .match import match, Tier
from .exceptions import classify, summarise


@dataclass
class SeedResult:
    seed: int
    payouts: int
    matched: int
    correct: int
    false_positives: int
    exceptions: int
    human_required: int
    auto_disposed: int
    tier_counts: dict[str, int]
    break_classes: list[str]

    @property
    def match_rate(self) -> float:
        return (self.matched / self.payouts * 100) if self.payouts else 0.0


def run_seed(seed: int) -> SeedResult:
    """Run one seed, return the full scorecard."""
    L = generate(seed=seed)
    R = match(L)
    E = classify(L, R)
    S = summarise(E)

    P = len(set(l.settlement_id for l in L.settlements if l.settlement_id))
    ok = sum(
        1
        for m in R.matches
        if L.truth_payout_to_bank.get(m.payout_id)
        and set(L.truth_payout_to_bank[m.payout_id].split("+"))
        == set(m.bank_stmt_ids)
    )

    break_classes = [s["scenario"] for s in L.scenario_log]

    return SeedResult(
        seed=seed,
        payouts=P,
        matched=len(R.matches),
        correct=ok,
        false_positives=len(R.matches) - ok,
        exceptions=S["total"],
        human_required=S["requires_human"],
        auto_disposed=S["auto_dispositioned"],
        tier_counts=R.tier_counts,
        break_classes=break_classes,
    )


def sweep(n_seeds: int = 100) -> list[SeedResult]:
    """Run eval across n_seeds, return all results."""
    results = []
    for seed in range(1, n_seeds + 1):
        try:
            results.append(run_seed(seed))
        except Exception as e:
            print(f"seed {seed} failed: {type(e).__name__}: {e}")
    return results


def report(results: list[SeedResult]) -> dict:
    """Summarise results into a report dict."""
    if not results:
        return {}

    rates = [r.match_rate for r in results]
    fps = [r.false_positives for r in results]
    excs = [r.exceptions for r in results]
    human = [r.human_required for r in results]
    auto = [r.auto_disposed for r in results]

    # Tier contribution
    tier_totals: dict[str, int] = defaultdict(int)
    for r in results:
        for t, c in r.tier_counts.items():
            tier_totals[t] += c

    # Break class impact
    break_totals: dict[str, int] = defaultdict(int)
    for r in results:
        for b in r.break_classes:
            break_totals[b] += 1

    return {
        "seeds": len(results),
        "match_rate": {
            "mean": round(st.mean(rates), 1),
            "min": round(min(rates), 1),
            "max": round(max(rates), 1),
            "stddev": round(st.pstdev(rates), 2),
        },
        "false_positives": sum(fps),
        "exceptions": {
            "mean": round(st.mean(excs), 1),
            "total": sum(excs),
            "human_required_mean": round(st.mean(human), 1),
            "auto_disposed_mean": round(st.mean(auto), 1),
            "auto_disposition_rate": round(sum(auto) / sum(excs) * 100, 0)
            if sum(excs)
            else 0,
        },
        "tiers": dict(sorted(tier_totals.items(), key=lambda x: -x[1])),
        "break_classes": dict(sorted(break_totals.items(), key=lambda x: -x[1])),
    }


def print_report(r: dict) -> None:
    """Pretty-print the report."""
    if not r:
        print("No results to report")
        return

    print(f"\n=== EVAL SWEEP ({r['seeds']} seeds) ===\n")

    mr = r["match_rate"]
    print(f"Match rate     : {mr['mean']}%  (min {mr['min']}%  max {mr['max']}%  sd {mr['stddev']})")
    print(f"False positives: {r['false_positives']} total\n")

    ex = r["exceptions"]
    print(
        f"Exceptions     : {ex['mean']} mean  ({ex['human_required_mean']} human, "
        f"{ex['auto_disposed_mean']} auto)"
    )
    print(f"Auto-disposition: {ex['auto_disposition_rate']:.0f}%\n")

    print("Tier contribution (matches made):")
    for tier, count in r["tiers"].items():
        print(f"  {tier:28s} {count}")

    print("\nInjected break classes (frequency across sweeps):")
    for bc, count in r["break_classes"].items():
        print(f"  {bc:28s} {count}")


# ---------------------------------------------------------------------------
# ABLATION — your task
# ---------------------------------------------------------------------------


def ablation_stub() -> dict[str, float]:
    """
    Skeleton for ablation. Your job to fill this in.

    The ablation disables one tier at a time and re-runs the sweep to show
    what each tier actually contributes. This is the differentiator between
    "here's a number" and "here's a number I measured".

    Pattern:
    1. Run a baseline sweep (already done in the main harness)
    2. For each tier (T0, T1, T2, T3, T4, T4b):
       - Modify the match engine to skip that tier
       - Run the same sweep (e.g., 20 seeds for speed)
       - Record the match rate
    3. Return {tier_name: match_rate_without_that_tier}

    The delta from baseline tells you the contribution.

    IMPORTANT: Don't modify match.py directly. Instead, pass a parameter to
    the match() function that disables a tier, OR make a copy of the match
    logic that skips it. Keep match.py pristine.

    You could also run match() and post-process the results, but that's
    messier.

    Here's a skeleton to get started — fill in the logic:

    ```python
    def ablation_stub(baseline_rate=90.5, n_seeds=20):
        results = {}
        for tier in ['T0', 'T1', 'T2', 'T3', 'T4', 'T4b']:
            # TODO: disable this tier
            # TODO: run sweep
            # TODO: measure match rate
            # results[tier] = match_rate_without_tier
            pass
        return results
    ```

    Call it from main(), print the results, and you're done.
    """
    return {}


if __name__ == "__main__":
    print("Running 100-seed eval sweep...")
    results = sweep(n_seeds=100)
    rep = report(results)
    print_report(rep)

    # TODO: uncomment this line once you fill in ablation_stub()
    # print("\nAblation (match rate without each tier):")
    # abl = ablation_stub(baseline_rate=rep['match_rate']['mean'])
    # for tier, rate in abl.items():
    #     delta = rep['match_rate']['mean'] - rate
    #     print(f"  {tier:28s} {rate:5.1f}%  (delta {delta:+5.1f}%)")