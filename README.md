# Recon Loop

Three-way reconciliation across a merchant's order book, a PSP settlement
report, and a bank statement. It matches what was sold against what was
collected against what actually landed in the account, and produces a ranked
list of everything it could not resolve, with a named cause and a proposed
action for each.

Built for the Razorpay AI Buildathon, Track 4 — AI Finance Controller.

---

## Run it

```bash
pip install -r requirements.txt
python -m recon run
```

Writes four files to `out/`. Open `dashboard.html` in a browser.

No API key. No configuration. No server. The engine is deterministic — the same
seed produces byte-identical output on any machine.

```bash
python -m recon run --seed 7      # a different dataset
python -m recon eval --seeds 100  # multi-seed sweep with ablation
pytest                            # 52 tests
```

---

## Result

Measured across 300 seeds. Every figure below comes from a sweep, never a
single run.

| | |
|---|---|
| Order → settlement | **98.4%** matched (min 97.3, sd 0.18) |
| Payout → bank | **90.1%** matched (min 81.8, sd 3.03) |
| False positives | **0** — both legs, all 300 seeds |
| Exceptions | 14.7 per run — 1.6 need a person, 13.1 auto-dispositioned |
| Auto-disposition | **89%** |
| Reconciliation statement ties | **300 / 300** |
| Model calls | **0** |
| Runtime | 5ms per run |

False positives are checked against ground truth the generator records
separately. A matcher that pairs everything at random scores 100% on match rate
alone, so match rate is only reported alongside the count of matches that were
actually correct.

The match rate is not higher because it is not allowed to be. Amount tolerance
is two paise. Where two candidates tie and nothing separates them, the engine
matches neither. Both decisions cost match rate and buy the zero in the row
above.

---

## What it produces

```
out/
  dashboard.html    four views, self-contained, opens from disk
  run.json          machine-readable, full decision record
  summary.md        what a person reads first
  exceptions.csv    the exception ledger, for a spreadsheet
```

The dashboard leads with a bank reconciliation statement — balance per bank,
adjustments, balance per books, and whether the two tie. It is the document
finance already reads, so it needs no explanation.

---

## How it matches

Two legs, each a cascade of tiers run cheapest and most certain first, stopping
at the first confident hit.

**Order → settlement**

| Tier | Basis |
|---|---|
| `O0` | settlement line carries the order id |
| `O1` | amount and capture window, unique candidate only |
| `O2` | payer name, normalised and scored |

**Payout → bank**

| Tier | Basis |
|---|---|
| `T0` | settlement id printed in the bank narration |
| `T1` | a UTR already tied to this payout |
| `T2` | amount inside the T+2 credit window |
| `T3` | amount matches, narration breaks the tie |
| `T4` | several credits sum to the payout |
| `T4b` | several credits sum to it, less a published bank charge |

Anything surviving every tier becomes an exception with a named class, the
evidence behind it, a confidence score, and a proposed resolution.

---

## What each tier is worth

Ablation, run at the same seed count as the baseline it is compared against.

| Disabled | Match rate | Delta |
|---|---|---|
| `T0_settlement_id` | 89.1% | 0.0 |
| `T1_utr` | 89.1% | 0.0 |
| `T2_amount_date` | 54.5% | **34.6** |
| `T4_subset_sum` | 84.4% | 4.7 |
| `T4b_subset_sum_charged` | 89.1% | 0.0 |

`T2` does most of the work. `T0` contributes nothing to match rate — `T2`
reaches the same payouts by amount and date.

`T0` stays anyway, and the reason is the interesting part. Removing it leaves
mean confidence at 0.90 instead of 0.96. A settlement id printed in a narration
is *proof*; an amount landing inside a date window is *inference*. Both produce
a match and only one produces evidence. **T0 buys certainty, not coverage.**

That distinction is not visible from a match rate, which is why the ablation
exists.

---

## Where AI is used

Nowhere in the matching path. Across 300 seeds the model is called **zero**
times.

That is a measured result, not a design slogan, and it was not the plan. A
resolver was built for bank narrations that survive every deterministic tier —
schema-validated, confidence-thresholded, cached, with a deterministic fallback.
Before wiring it up, its actual caseload was inspected:

```
stmt_00016  Rs.73,679.62  +  stmt_00015  Rs.62,756.61  =  Rs.136,436.23
payout      setl_2007014                                =  Rs.136,495.23
difference                                              =      Rs.59.00
```

₹59.00 is an RTGS charge of ₹50 plus 18% GST. Every case was a split payout that
had also been levied a bank transfer charge — an exact arithmetic problem about
to be handed to a language model. Six lines and a lookup table (`T4b`) solved
them, and the class went to zero.

The order leg was expected to need a model for the same reason: matching
`M. Rao` against `Manish Rao` is genuinely fuzzy in a way bank arithmetic is
not. Normalisation and similarity scoring with a margin rule handle it at 98.4%
with no false positives.

The resolver is kept, not deleted. It is the correct design for genuinely
ambiguous text and it runs without an API key. It simply has no caseload, and
manufacturing one for it would have meant loosening a tier that currently has a
perfect record.

---

## Engine proposes, human disposes

Nothing writes to a ledger. Every exception carries `requires_human`, the
evidence behind its classification, and a suggested resolution. The dashboard
renders approve and reject as proposals, not mutations.

This is a design position, not an unfinished feature. Regulated finance
operations do not accept an autonomous agent adjusting the books, and building
one that did would have been the wrong answer regardless of how it scored.

The useful measure is how much a person is left holding: **1.6 items per run out
of 14.7**, each with its cause named and its arithmetic shown.

---

## Tests

```bash
pytest
```

52 tests. Twenty-one are regressions named after entries in
[`BUILD_LOG.md`](BUILD_LOG.md) — a reader can go from "he says he fixed this" to
the test that proves it stays fixed. The rest are invariants: zero false
positives, byte-identical output for a given seed, `net = gross - fee - tax` on
every line, the exception ledger ranked by severity.

CI runs on Python 3.10, 3.11 and 3.12, and additionally verifies that the CLI
runs from a clean checkout with no configuration.

---

## What this does not do

- **Synthetic data only.** The break classes are modelled on real Indian PSP
  settlement behaviour — T+2 timing, instant-settlement sub-batching, refunds
  netted mid-batch, chargeback handling fees, NEFT and RTGS charges, prior-period
  credits, a rival PSP sharing the current account. No production statement has
  been through it.
- **India-specific.** Fee schedules, GST at 18%, narration templates and price
  points would all need reworking elsewhere.
- **No multi-currency.** Single currency throughout.
- **The bank leg sits at 90%.** Roughly 10% of payouts land in the exception
  ledger. Most are correctly there — in transit at close, carried forward,
  belonging to another PSP — but the engine does not claim to resolve them.
- **`unexplained` is not zero, deliberately.** A classifier that always produces
  a cause is overconfident rather than accurate. Genuine unknowns escalate with
  their evidence attached and no guessed cause.
- **Read-only.** No ledger integration, no posting, no write path of any kind.

---

## Layout

```
recon/
  models.py        domain types; money is integer paise throughout
  generate.py      synthetic three-source generator, 14 break classes
  match.py         payout to bank, six tiers, no model calls
  order_match.py   order to settlement, three tiers, name normalisation
  exceptions.py    classification, evidence, proposed resolutions
  resolve.py       LLM resolver for ambiguous text (currently no caseload)
  eval.py          multi-seed sweep and per-tier ablation
  dashboard.py     self-contained HTML report
  cli.py           entry point
tests/             52 tests
BUILD_LOG.md       21 bugs found during the build, and what each one taught
DECISIONS.md       every non-obvious choice, with the reasoning
```

[`BUILD_LOG.md`](BUILD_LOG.md) is the file worth reading. It records the bugs as
they happened, including the ones where the first fix was wrong.
