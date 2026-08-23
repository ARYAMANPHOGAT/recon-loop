# Decision record

Every non-obvious choice in this codebase, with the reasoning. If a decision
here cannot be defended out loud, it should not be in the code.

Current measured position, 100 seeds:

| Metric | Value |
|---|---|
| Match rate | 90.5% mean, 80.5% worst, sd 3.02 |
| False positives | 0 across 100 runs |
| Exceptions | 15.1 mean — 2.1 human, 13.0 auto |
| Auto-disposition rate | 86% |
| Runtime | 3ms per run |
| Determinism | same seed → byte-identical output |
| LLM calls in matching path | 0 |

---

## 1. Money is integer paise, never float rupees

`0.1 + 0.2 != 0.3`. A reconciliation engine built on floats accumulates
residue and then either reports phantom breaks or hides real ones behind a
widened tolerance. Every amount in the system is an integer count of paise;
rupees exist only in display formatting.

## 2. Amount tolerance is 2 paise, not 2 rupees

Loosening tolerance is the fastest way to buy match rate, and every paise of
extra slack buys it with false positives. Two paise covers PSP fee-rounding
residue and nothing else.

Deltas larger than that get **explained rather than absorbed**. A payout short
by exactly ₹17.70 is not a rounding error; it is a ₹15 NEFT charge plus 18%
GST, and the engine says so, names the charge, and proposes the journal entry.

## 3. The UTR tier is deliberately narrow

A settlement report carries no bank UTR — the two systems have no shared key.
Matching on UTR alone across unrelated payouts manufactures matches that look
highly confident and are not. The tier only fires where a UTR has already been
associated with a payout through a sibling row.

## 4. Ambiguity is left unmatched

Where two bank credits tie on amount and date and narration cannot separate
them by a clear margin, the engine matches neither. On a finance ledger an
ambiguous match is worse than no match: an unmatched item gets reviewed, a
wrongly matched one gets filed.

## 5. No LLM anywhere in the matching path — and currently none at all

Across 100 seeds the model is called **zero** times. That is a measured result,
not an aspiration.

A resolver was built for narration strings that survive every deterministic
tier. Inspecting its caseload showed every case was a split payout carrying a
bank transfer charge — an exact arithmetic problem that a new tier now solves
(build log item 7). The class it served went from 28 cases to zero.

The resolver is retained for the order ↔ settlement leg, where free-text
customer references are genuinely ambiguous. It is not wired into the bank leg,
because loosening a tier with a perfect record to manufacture work for a model
would be the wrong trade.

## 5b. Original reasoning, unchanged

All five tiers are deterministic. Across 100 seeds, 86% of exceptions are also
disposed of by rule. The model is reserved for one class —
`opaque_narration`, roughly 8 cases in 40 seeds — where a bank narration
carries no settlement id and only free text remains.

This is a capability claim, not a limitation. Matching is arithmetic and string
comparison; a model adds latency, cost, and non-determinism to a problem that
has an exact answer. The interesting judgment was deciding where it does *not*
belong.

## 6. Engine proposes, human disposes

Nothing writes to a ledger. Every exception carries `requires_human`, the
evidence behind the classification, and a suggested resolution. The dashboard
renders approve and reject as proposals, not mutations. Regulated finance ops
does not accept an autonomous agent adjusting the books, and building one that
does would be the wrong answer regardless of how well it scored.

## 7. The statement period is explicit, never inferred

`period_end` was originally `max(value_date)` across bank rows. One stray row
past the close moved the boundary and reclassified an in-transit payout as
unexplained — escalating a timing artefact to a human as missing money. The
period is now a field on the ledger.

## 8. Settlement ids are sequential, not random

A random suffix collides once a day carries several batches, silently merging
two distinct payouts during roll-up and corrupting the ground-truth map. See
build log item 5 — this produced the only false positive the project has had.

## 9. `unexplained` is not driven to zero

Four genuine unknowns remain across 40 seeds and they stay. A classifier that
always produces a cause is not accurate, it is overconfident, and on a finance
ledger a confident wrong cause is more expensive than an honest escalation.

## 10. Every number comes from a multi-seed sweep

Single-seed results are anecdotes. Both of the project's worst errors — the
false positive and the 94 over-escalations — were invisible at one seed and
obvious at forty. No figure is quoted anywhere from a single run.

---

## What this does not do

- Only the payout ↔ bank leg is implemented. Order ↔ settlement is scheduled
  and until it lands, the three-way claim would be overstating the build.
- Runs on synthetic data. The break classes are modelled on real Indian PSP
  settlement behaviour, but no production statement has been through it.
- Fee schedules and narration templates are India-specific and would need
  reworking for other markets.
- Multi-currency is not handled at all.
