# Build log — running notes for the "Build Challenges" form field

Kept live during the build, not reconstructed afterwards.

---

## 1. Generator collapsed 30 days of payouts into 13 bank rows

Batching settlements by day meant one payout per day, so a 30-day period
produced ~13 bank credits. Matching against 13 rows is trivial and proves
nothing about the engine.

**Fix:** modelled instant settlement — high-volume days split into 2–3 payout
batches. Bank rows went 13 → 63, with several similar-looking credits landing
on the same date, which is what makes narration matching actually necessary.

---

## 2. 100% match rate on first run — treated as a failure, not a result

The engine matched 43/43 payouts with zero false positives. That was a signal
the synthetic data was too clean, not that the matcher was good. The track
brief says a cherry-picked match proves nothing.

**Fix:** injected four break classes the engine should *not* cleanly resolve —
bank charges netted off the credit, payouts in transit at period close, credits
belonging to a prior settlement period, and a second PSP's settlements sharing
the same current account. Match rate fell to 93.0%, still with zero false
positives. That number is a measurement rather than a decoration.

---

## 3. Same break counted twice, from both sides

A payout short by a ₹15 NEFT charge was raised as `bank_charge_deducted` on the
payout side, and the corresponding bank row was raised *again* as
`opaque_narration` on the bank side. One underlying break, two exceptions, and
an inflated exception count.

**Fix:** exceptions now declare `counterpart_stmt_ids`. Any bank row a
payout-side exception already accounts for is skipped during bank-side
classification. Exception count 15 → 13.

---

## 4. Statement period was being inferred, and the inference was wrong

`period_end` was computed as `max(value_date)` across bank rows. Stray noise
rows dated past the real close pushed the boundary out, so a genuinely
in-transit payout was classified `unexplained` — the worst possible error, since
it escalates a timing artefact to a human as missing money.

Worth noting the first instinct was to widen the in-transit threshold from 2
days to 3 so the case would pass. That would have been fitting the classifier to
one example. The actual defect was in the generator: it had removed a bank
credit for a payout that still had a full T+2 window to land, so `unexplained`
was arguably the correct call on the data as given.

**Fix:** the statement period is now an explicit field on the ledger rather than
inferred, bank rows past the close are trimmed, and the in-transit break targets
a payout genuinely inside the T+2 window of the boundary. `unexplained` went to
zero, and it means something when it is non-zero.

---

## Design decisions worth defending

- **Money is integer paise everywhere.** Float rupees in a reconciliation engine
  is a bug waiting to happen.
- **Amount tolerance is 2 paise, not 2 rupees.** Loosening tolerance is the
  fastest way to buy match rate with false positives. Deltas larger than fee
  rounding get *explained* (see the bank charge schedule) rather than absorbed.
- **The UTR tier is deliberately narrow.** A settlement report carries no bank
  UTR, so matching on UTR alone across unrelated payouts manufactures
  confident-looking false positives.
- **Ambiguity is left unmatched.** Where two candidates tie on amount and the
  narration cannot separate them by a clear margin, nothing is matched. An
  ambiguous match is worse than no match on a finance ledger.
- **No LLM in the matching path at all.** All 13 exceptions in the current run
  are classified by rule. The model is reserved for narration strings that
  survive every deterministic tier.

---

## 5. "Zero false positives" was true for one seed and false in general

Reported zero false positives after testing a single seed. Running 40 seeds
surfaced one at seed 9, where a payout was matched to two bank credits and the
scorer called it wrong.

The matcher was right and the ground truth was wrong, which is the worse of the
two possibilities — a scorer that disagrees with a correct engine will push you
to "fix" working code. The two credits summed exactly to the payout net and one
of them carried the settlement id in its narration.

Root cause: settlement ids were built as `setl_{ddmm}{random 100-999}`. Once
instant-settlement sub-batching put several batches on one day, two distinct
batches could draw the same suffix. Roll-up groups by settlement id, so the two
payouts silently merged into one, and the truth map kept only the second.

**Fix:** monotonic counter instead of a random suffix. False positives are now 0
across 100 seeds. The wider lesson is that single-seed results are anecdotes;
every number quoted anywhere in this project comes from a multi-seed sweep.

---

## 6. The classifier was giving up far too often

Across 40 seeds the `unexplained` class fired 94 times, averaging 2.35 per run.
Seed 42 happened to produce zero, which is what made it look solved. Two real
gaps were hiding behind it:

- **Net-negative settlement days.** When refunds and chargebacks exceed
  collections, the PSP carries the balance against the next payout rather than
  debiting the account. No bank credit is expected. The generator created these
  days; the classifier had no class for them and escalated each one to a human
  as missing money.
- **Direct customer transfers.** A customer paying the merchant's account
  directly bypasses the gateway and never appears in a settlement report. The
  narration token list did not cover it.

**Fix:** added a `net_negative_carried` class and the direct-transfer token.
`unexplained` fell from 94 to 4 across 40 seeds.

Deliberately not driven to zero. Four genuine unknowns remain and they should:
a classifier that never returns "I don't know" is not accurate, it is
overconfident. Those four escalate to a human with the evidence attached and no
guessed cause.

---

## 7. Built an LLM resolver, then discovered its caseload was arithmetic

Day 1 was spent building a resolver for the `opaque_narration` class — bank
credits that look like PSP settlements but carry no settlement id, where
deterministic tiers had run out and only free text remained. Schema-validated
JSON, confidence thresholds, response caching, deterministic fallback.

Before wiring it up, inspected what those cases actually were. Across 50 seeds
there were only 14, and they shared a shape:

```
seed 4   stmt_00016  Rs.73,679.62 + stmt_00015  Rs.62,756.61 = Rs.136,436.23
         payout setl_2007014 net                              = Rs.136,495.23
         delta                                                = Rs.59.00
```

₹59.00 is an RTGS charge of ₹50 plus 18% GST. Every one of the cases was a
**split payout that had also been levied a bank transfer charge**. The
subset-sum tier requires an exact match within 2 paise, so it missed them, and
they fell through to the class marked "send this to a model".

This is precisely the failure the rubric asks about — the wrong tool in the
wrong place. A language model was about to be handed a problem with an exact
arithmetic answer.

**Fix:** a `T4b_subset_sum_charged` tier that tries each published bank charge
as a candidate delta on the subset sum. Six lines and a lookup table.

Result across 100 seeds: match rate 90.0% → 90.5%, false positives still 0, and
the opaque-narration class went from 28 cases to **zero**. The resolver now has
no caseload at all on the bank leg.

The resolver itself is kept, not deleted. It is the correct design for
genuinely ambiguous text, and it moves to the order ↔ settlement leg where free-
text customer references ("Rahul S - inv 4471" against "RAHUL SHARMA INV4471")
are fuzzy in a way bank arithmetic is not. Manufacturing work for it on the bank
leg would have meant loosening a tier that currently has a perfect record.

---

## 8. Fuzzy name matcher scored the wrong customer higher than the right one

The order leg matches settlement lines to orders. Where the channel carries no
order id, the only link is a payer name typed by a human — `PRIYAMEHTA` in the
settlement report against `Priya Mehta` in the order book.

One false positive appeared across 40 seeds:

```
psp payer   : 'PRIYAMEHTA'   Rs.999.00
matched to  : 'Divya Mehta'  Rs.999.00     score 88
truth was   : 'Priya Mehta'  Rs.999.00     score 66
```

The scorer preferred the wrong customer by 22 points.

Cause was in the normaliser. It alphabetises tokens so reordered names compare
equal — `Patel Rohan` against `Rohan Patel`. The similarity function then
stripped spaces from that *sorted* output and ran a substring comparison on it,
which meant `Priya Mehta` was compared as `MEHTAPRIYA`. Sorting had destroyed
the letter order that the substring comparison depends on, while `Divya Mehta`
happened to share the `YAMEHTA` tail.

Sorting is correct for token comparison and wrong for substring comparison.
One normalised string was being fed to both.

**Fix:** `normalise_reference` takes a `sort_tokens` flag. Token comparison gets
the sorted form, substring comparison gets the unsorted one. `PRIYAMEHTA` now
scores 100 against the correct name and 88 against the decoy, and the 12-point
margin rule rejects the ambiguity.

Result across 100 seeds: order-leg match rate 98.4%, **zero false positives**.

---

## 9. Two more generators that were too easy, found the same way as item 2

Building the order leg surfaced the same failure twice more. Both times a tier
reported 100% of the work and the tiers below it never fired — which means the
data, not the engine, was doing the matching.

**Every settlement line carried an order id.** O0 matched everything; the
amount and name tiers were dead code. Real payment links and POS collections
are raised outside the ERP and arrive with no order id at all. Fixed by
dropping the id for 75% of invoice and POS channel lines.

**Amounts never collided.** With random rupee values, amount plus a time window
identified almost every order uniquely, so the name tier still never fired.
Real merchants sell at price points — fifty customers buy the same ₹499 item.
Fixed by drawing 70% of order amounts from a fixed price-point list including
the charm-pricing values that dominate real catalogues.

Only after both fixes did the name tier carry meaningful volume (767 matches
across 100 seeds), and only then did the false positive in item 8 become
visible. A generator that flatters the engine hides the bugs worth finding.
