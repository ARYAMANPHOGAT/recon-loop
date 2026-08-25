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

---

## 10. A deterministic engine was producing non-identical output files

Determinism is one of this project's claims: same seed, same result, every
time. Adding the CLI broke it — two runs at seed 7 produced different
`run.json` files.

The engine was fine. The reporting was not: `runtime_ms` was written into the
JSON artefact, and wall-clock timing varies by a few milliseconds run to run.
So a byte-comparison of two outputs from an entirely deterministic pipeline
failed on a field that had nothing to do with the reconciliation.

Worth noting because it is the kind of thing that quietly invalidates a
regression test. A CI job comparing output hashes would have failed
intermittently, and the obvious response — loosening the comparison — would
have destroyed the check's value.

**Fix:** timing moved to an underscore-prefixed key that is stripped before
serialisation. It still prints to the console where it is useful, and no longer
contaminates an artefact that must be reproducible. Two runs at the same seed
now produce byte-identical `run.json` and `summary.md`.

---

## 11. Ablation was comparing a 100-seed baseline to a 20-seed ablation

The first ablation implementation ran the baseline sweep at 100 seeds and each
tier ablation at 20. The deltas were therefore differences between two
different populations, not measurements of a tier's contribution — a tier could
appear to add or remove match rate purely through sampling variance between the
two sweep sizes.

**Fix:** `ablation_stub` takes the seed count from the caller and the CLI passes
the same value used for the baseline. The deltas now mean what they claim to.

---

## 12. The first test run found two bugs the engine had been carrying silently

Writing the invariant suite immediately failed on two properties the project
had been quietly violating.

**Chargeback fee had the wrong sign.** The identity `net = gross - fee - tax`
held on every line except chargebacks, where the handling fee was written as
`-50_000`. A chargeback reverses the payment *and* levies a ₹500 fee; the fee is
a positive charge like any other. Written negative, the identity breaks and
batch fee totals are understated by ₹1,000 per chargeback. The net figure was
right, so every match still balanced — which is exactly why it survived: the
number the engine used was correct and the number an accountant would reconcile
was not.

**Ground truth referenced deleted bank rows.** Rows are removed in two places:
the period-close trim and the in-transit break. The truth map kept pointing at
them. This is the same failure as item 5 — a scorer that disagrees with a
correct engine — and it would have manifested as phantom false positives that
push you to "fix" working code.

Fixed by reconciling the truth map once, after every bank mutation is complete,
rather than at the point of each removal.

Both bugs had been present through the 100-seed sweeps that produced the
headline numbers. Neither was visible from match rate or false-positive counts,
because both quantities were computed from figures that happened to be correct.
Only asserting the underlying identities surfaced them.

---

## 13. A test asserted something that was true of one dataset, not of the system

The regression test for item 7 asserted `opaque_narration == 0`, because after
adding the T4b tier that class went to zero.

It failed once the Day-3 generator changes landed. Investigating the two
surviving cases showed deltas of ₹2,756.93 and ₹1,953.39 — not bank charges, not
explainable arithmetic, genuinely ambiguous credits that should reach a human.

The code was right and the test was wrong. Zero was a property of the Day-1
dataset, not a guarantee the system makes, and asserting it would have created
pressure to suppress correct behaviour to keep a test green.

**Fix:** the test now asserts the permanent invariant instead — no opaque case
may have a delta that a published bank charge explains. If one does, T4b has
regressed and a deterministic problem is being routed onward. A separate,
looser test guards against opaque cases becoming common.

---

## 14. The determinism fix was incomplete, and the test passed anyway

Item 10 moved wall-clock timing out of `run.json`. It was still being written
into `summary.md`, so two runs at the same seed still produced different files.

The test suite passed locally. It failed on a different machine, where the two
runs took 7ms and 10ms instead of landing in the same millisecond.

The test was correct and its *timing* made it unreliable — it compared two runs
executed back to back, so on a fast machine both readings were identical and the
leak stayed invisible. A test that only fails on slower hardware is worse than
no test, because a green suite is taken as proof.

**Fix, two parts.** Timing removed from `summary.md` as well as `run.json`; it
now appears only on the console, where nothing compares it. And the test now
sleeps between the two runs to force a different clock reading, so it cannot
pass by coincidence.

The underlying lesson is the one worth keeping: the first fix addressed the
instance rather than the class. `run.json` was the file that had failed, so
`run.json` was the file that got fixed, and the same defect sat untouched in the
artefact next to it.

---

## 15. The dashboard's tier chart drew two different scales as one chart

The method view plots matches claimed per tier. The bank leg and the order leg
were rendered as one continuous list, each scaled to its own maximum:

```
T0_settlement_id   ████████████████░░░░   17
O0_order_id        ███████████████████░  119
```

Seventeen and a hundred and nineteen drew at nearly the same length. Anyone
reading bar lengths — which is the only reason to draw bars rather than print a
table — would compare them directly and conclude the two tiers do comparable
work.

A shared scale was not the fix either: the order leg is an order of magnitude
larger, so a shared maximum flattens every bank tier to a stub and the view
stops saying anything about the leg that matters most.

**Fix:** two charts, each with its own header stating its total and the maximum
its bars are scaled to. The scale is now visible rather than implied.

Worth recording because it was not a coding error — the code did exactly what
it was told. It was a presentation choice that made a correct number read
incorrectly, which is the failure mode a dashboard is uniquely good at
producing.

---

## 16. The reconciliation channel labelled distinct findings with one word

Rows with nothing opposite them showed `carry` in the channel regardless of
cause. A payout carried forward because refunds exceeded collections, a credit
belonging to the previous statement, and another PSP's settlement all read the
same.

Those are three different findings with three different actions, and the view
was flattening them into one. The channel now carries the classification —
`carry fwd`, `prior period`, `other psp`, `in transit` — taken from the
exception the classifier already produced.

The information existed the whole time. The interface simply was not showing it.

---

## 17. The dashboard argued its best point in prose and hid it in the view

The Method tab explains at length that `T0` buys certainty rather than
coverage: removing it leaves the match rate unchanged but drops mean confidence
from 0.96 to 0.90. A settlement id printed in a narration is proof; an amount
landing inside a date window is inference.

The reconciliation view drew both as an identical line. The single most
interesting finding in the project was stated in a paragraph on a different tab
and contradicted by the main view, which showed every tie as equally certain.

**Fix:** link weight now encodes confidence — solid for proof, medium for
strong, thin for inference — with the reading given in the legend. Hovering a
link names the tier and the score.

The point is now visible before it is read, which is the only reason to draw a
picture rather than write a sentence.

---

## 18. Empty cells read as a rendering gap rather than as absence

A payout with no bank credit rendered as a faint dashed outline at 50% opacity.
On screen it read as blank space — as though the interface had failed to draw
something, rather than reporting that there is nothing there.

That inverts the meaning. In reconciliation an empty cell is not missing data;
it *is* the finding.

**Fix:** stranded cells now carry a hatched fill and an explicit label — `no
payout` or `no credit`. The absence is stated rather than implied.

Also added arrow-key navigation to the tab strip, which a tablist is expected
to support and which anyone working without a mouse will reach for first.

---

## 19. The dashboard opened with a KPI strip, which is nobody's document

The first dashboard led with five cards: big number, small label, one per metric.
It is the default answer for any dashboard and it was reached for on autopilot.
It also says nothing a reader could not get from one line of text, and it looks
like every other dashboard because it is.

Reconciliation already has a document. The bank reconciliation statement has
been set the same way for a century: balance per bank, adjust for what the bank
has not seen, balance per books, adjust for what the books have not seen, and
the two sides tie or the difference is named. Anyone in finance reads it without
instruction.

**Fix:** the statement replaced the cards as the hero, with its conventions kept
rather than restyled — negatives in parentheses, figures right-aligned in
tabular numerals, a double rule under a total that is final. Match rates moved
to a thin data line beneath it, which is their correct weight: they are evidence
for the claim, not the claim.

Set on green-bar ledger stock with the printed band alternating down the
reconciliation view, because that banding exists to stop the eye losing its line
across a wide table, which is exactly what the gutter is.

---

## 20. Building the statement exposed a real accounting error

The first version summed exception classes to produce each line. It reported a
difference of ₹2,172.00 on seed 42 and refused to tie.

The engine was right. The statement was wrong: it subtracted net-negative
batches as "carried forward" from a total those batches had never been part of,
since only positive payouts are summed in the first place. A figure was being
deducted twice, once by omission and once by subtraction.

**Fix:** the statement is computed from the actual figures — credits on the
statement, credits matched, payouts claimed, payouts matched, deltas explained —
rather than by adding up classifications. Carried-forward became a memo line,
which is what it is: disclosed, not deducted.

Both sides now tie to the paise on **60 of 60 seeds**.

The general point is worth keeping. Summing categories to reconstruct a total is
a step removed from the underlying facts, and it silently drifts from them.
Working from the facts is longer and does not drift.

---

## 21. "Zero false positives" was true at 100 seeds and false at 200

Writing the README meant re-measuring every quoted figure rather than repeating
remembered ones. Running 200 seeds instead of 100 surfaced an order-leg false
positive that every previous sweep had missed.

```
psp payer : 'M. Rao'      Rs.7,999.00
matched   : 'Vikram Rao'  score 100
truth     : 'Manish Rao'  score  85
```

The scorer preferred the wrong person, at maximum confidence.

Cause: the similarity function strips whitespace before substring comparison,
so `M. Rao` becomes the four-character needle `MRAO` — and `VIKRAMRAO` contains
it, as `VIKRA·MRAO`. A coincidental substring hit scoring 100.

An initial carries one letter of evidence and must be compared as one letter.

**Fix:** names containing an initial are now compared structurally — the surname
must genuinely match, and the initial must agree with the first letter of the
corresponding given name. A disagreeing initial returns 40, because a
disagreeing initial is evidence of a *different person*, not weak evidence of
the same one. `M. Rao` now scores 92 against Manish Rao and 40 against Vikram
Rao.

This is the second time the same underlying mistake has appeared — build log
item 8 was also a substring comparison being fed input it could not safely
handle. The class of bug is: *insufficient evidence scoring as certainty*.

---

## 22. Writing the test for item 21 found a third instance of the same class

Asserting that a bare surname cannot claim a match failed immediately:
`Sharma` against `Rahul Sharma` scored **100**, because `SHARMA` is contained in
`RAHULSHARMA`. Any customer sharing a surname would have matched at full
confidence.

It does not fire on the current generator, which always produces a given name,
so no sweep would ever have caught it. It is a live weakness regardless.

The first fix capped every single-token name — and broke `DIVYAPATEL` against
`Divya Patel`, which is also a single token but is a full name with the space
lost, not a surname standing alone. Two different situations that look identical
to a token count.

**Fix:** the discriminator is whether the lone token accounts for the *whole* of
the other side or only *part* of it. `DIVYAPATEL` matches `Divya`+`Patel`
joined, so it is the full name and scores fully. `Sharma` matches only the
surname, so a given name is missing and the score is capped below the match
threshold — able to support a match, never to make one.

A side effect worth recording: `PRIYAMEHTA` against the decoy `Divya Mehta`
dropped from 88 to 60, widening the margin over the true match from 12 points to
40. Tightening the evidence rules made an unrelated near-miss substantially
safer.

Final position: **0 false positives across 300 seeds**, both legs.
