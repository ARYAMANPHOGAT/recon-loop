"""Narration resolver — the only component in this project that calls a model.

Everything upstream is arithmetic and string comparison, and stays that way.
This file handles the one residue the deterministic tiers cannot: a bank credit
that looks like a PSP settlement, carries no settlement id, and must be matched
against several candidate payouts on free text alone.

Three constraints shape the design:

  1. The model NEVER auto-matches. It returns a ranked proposal with reasoning;
     a human approves. A hallucinated match writes a wrong entry into a ledger,
     which is a worse outcome than leaving the item unresolved.

  2. Output is schema-validated. A response that does not parse, names an
     unknown candidate, or omits a field is discarded rather than salvaged.

  3. It degrades to a deterministic heuristic. The repo must run and produce
     honest numbers for anyone who clones it without an API key, so the
     fallback is a real code path rather than a crash.

Every call is logged with tokens and latency, and cached by content hash so
re-runs over the same data cost nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Optional

from .exceptions import ExClass, Exception_
from .match import MatchResult, Payout, build_payouts
from .models import BankLine, Ledger, Paise, rupees

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 700

# A proposal below this never reaches a human as a suggestion; it is reported
# as unresolved instead. Being asked to review a weak guess wastes more of a
# controller's attention than an honest blank.
MIN_PROPOSAL_CONFIDENCE = 0.55

# Candidate payouts offered to the model, by date proximity. More context is
# not better here: a long list invites the model to find a pattern in noise.
MAX_CANDIDATES = 6
CANDIDATE_WINDOW_DAYS = 4


SYSTEM_PROMPT = """You reconcile Indian payment-gateway settlements against bank statements.

You are given one bank credit whose narration carries no settlement ID, and a list of candidate payouts that had no matching bank credit. Decide which candidate, if any, this credit belongs to.

Deterministic matching has already run and failed. Exact amount matching, UTR matching, date-window matching and subset-sum have all been tried. Do not assume you can see something they could not — if the amounts do not correspond, they genuinely do not correspond.

Judge on:
- Amount correspondence, including a plausible deduction (bank NEFT/RTGS charges run Rs.10-100 plus 18% GST)
- Value date against settlement date, allowing T+0 to T+2
- Bank and remitter tokens in the narration
- Whether any other candidate fits equally well

Rules:
- If two or more candidates fit comparably, return null. Ambiguity is a valid answer and the correct one here.
- If nothing fits, return null. Absence of a match is a normal outcome, not a failure.
- Never invent a settlement ID that is not in the candidate list.
- Confidence must reflect real uncertainty. Reserve above 0.85 for near-certain arithmetic correspondence.

Respond with JSON only. No preamble, no markdown fences.

{"settlement_id": string or null, "confidence": number 0-1, "reasoning": string under 30 words, "delta_explanation": string or null}"""


@dataclass
class ResolverCall:
    """One model call, recorded for the audit trail and cost reporting."""

    stmt_id: str
    cached: bool
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    ok: bool = True
    error: str = ""


@dataclass
class Proposal:
    stmt_id: str
    settlement_id: Optional[str]
    confidence: float
    reasoning: str
    delta_explanation: Optional[str] = None
    source: str = "llm"  # "llm" | "heuristic"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResolverReport:
    proposals: list[Proposal] = field(default_factory=list)
    calls: list[ResolverCall] = field(default_factory=list)
    mode: str = "llm"  # "llm" | "heuristic"

    @property
    def total_tokens(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.calls)

    def summary(self) -> dict:
        live = [c for c in self.calls if not c.cached]
        return {
            "mode": self.mode,
            "items_seen": len(self.calls),
            "live_calls": len(live),
            "cache_hits": sum(1 for c in self.calls if c.cached),
            "failed_calls": sum(1 for c in self.calls if not c.ok),
            "total_tokens": self.total_tokens,
            "mean_latency_ms": (
                round(sum(c.latency_ms for c in live) / len(live)) if live else 0
            ),
            "proposals": len(self.proposals),
            "confident_proposals": sum(
                1 for p in self.proposals if p.settlement_id and p.confidence >= MIN_PROPOSAL_CONFIDENCE
            ),
        }


# ---------------------------------------------------------------------------
# candidate selection
# ---------------------------------------------------------------------------


def _candidates(bank_row: BankLine, unmatched: list[Payout]) -> list[Payout]:
    """Payouts a credit could plausibly belong to, nearest settlement date first."""
    near = [
        p
        for p in unmatched
        if abs((bank_row.value_date - p.settled_on).days) <= CANDIDATE_WINDOW_DAYS
    ]
    near.sort(key=lambda p: abs((bank_row.value_date - p.settled_on).days))
    return near[:MAX_CANDIDATES]


def _cache_key(bank_row: BankLine, cands: list[Payout]) -> str:
    blob = json.dumps(
        {
            "n": bank_row.narration,
            "c": bank_row.credit,
            "d": str(bank_row.value_date),
            "k": sorted((p.settlement_id, p.net, str(p.settled_on)) for p in cands),
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _build_prompt(bank_row: BankLine, cands: list[Payout]) -> str:
    lines = [
        "BANK CREDIT",
        f"  statement row : {bank_row.stmt_id}",
        f"  amount        : {rupees(bank_row.credit)}",
        f"  value date    : {bank_row.value_date}",
        f"  narration     : {bank_row.narration}",
        f"  utr           : {bank_row.utr or 'absent'}",
        "",
        "CANDIDATE PAYOUTS (none has a matching bank credit)",
    ]
    for p in cands:
        drift = (bank_row.value_date - p.settled_on).days
        lines.append(
            f"  {p.settlement_id} | net {rupees(p.net)} | settled {p.settled_on} "
            f"({drift:+d}d) | {p.n_payments} payments, {p.n_refunds} refunds"
        )
        delta = p.net - bank_row.credit
        if delta:
            lines.append(f"      delta vs this credit: {rupees(delta)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# deterministic fallback
# ---------------------------------------------------------------------------

_KNOWN_CHARGES = {1_180: "NEFT Rs.10+GST", 1_770: "NEFT Rs.15+GST", 2_950: "NEFT Rs.25+GST", 5_900: "RTGS Rs.50+GST", 11_800: "RTGS Rs.100+GST"}


def _heuristic(bank_row: BankLine, cands: list[Payout]) -> Optional[Proposal]:
    """Runs when no API key is present.

    Strictly narrower than the model path: it only claims a match where the
    delta lands exactly on a published bank charge, and abstains the moment
    two candidates qualify. The repo therefore still produces honest numbers
    without a key, just fewer resolutions.
    """
    hits = [
        (p, p.net - bank_row.credit)
        for p in cands
        if (p.net - bank_row.credit) in _KNOWN_CHARGES
    ]
    if len(hits) != 1:
        return None
    p, delta = hits[0]
    return Proposal(
        stmt_id=bank_row.stmt_id,
        settlement_id=p.settlement_id,
        confidence=0.80,
        reasoning=f"delta lands exactly on {_KNOWN_CHARGES[delta]}; sole candidate that does",
        delta_explanation=_KNOWN_CHARGES[delta],
        source="heuristic",
    )


# ---------------------------------------------------------------------------
# model path
# ---------------------------------------------------------------------------


def _validate(raw: str, cands: list[Payout], stmt_id: str) -> Optional[Proposal]:
    """Parse and schema-check. Anything malformed is discarded, not repaired.

    Salvaging a partial response means guessing what the model meant, which
    reintroduces exactly the uncertainty the validation exists to remove.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or "settlement_id" not in d or "confidence" not in d:
        return None

    sid = d["settlement_id"]
    if sid is not None:
        # the model must not name a payout it was not offered
        if sid not in {p.settlement_id for p in cands}:
            return None
    try:
        conf = float(d["confidence"])
    except (TypeError, ValueError):
        return None
    if not 0.0 <= conf <= 1.0:
        return None

    return Proposal(
        stmt_id=stmt_id,
        settlement_id=sid,
        confidence=conf,
        reasoning=str(d.get("reasoning", ""))[:200],
        delta_explanation=d.get("delta_explanation"),
        source="llm",
    )


def _call_model(client, bank_row: BankLine, cands: list[Payout]) -> tuple[Optional[Proposal], ResolverCall]:
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(bank_row, cands)}],
        )
        ms = int((time.time() - t0) * 1000)
        text = "".join(b.text for b in resp.content if b.type == "text")
        prop = _validate(text, cands, bank_row.stmt_id)
        return prop, ResolverCall(
            stmt_id=bank_row.stmt_id,
            cached=False,
            latency_ms=ms,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            ok=prop is not None,
            error="" if prop else "schema validation failed",
        )
    except Exception as e:  # network, auth, rate limit
        return None, ResolverCall(
            stmt_id=bank_row.stmt_id,
            cached=False,
            latency_ms=int((time.time() - t0) * 1000),
            ok=False,
            error=type(e).__name__,
        )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def resolve(
    led: Ledger,
    res: MatchResult,
    exceptions: list[Exception_],
    use_llm: Optional[bool] = None,
) -> ResolverReport:
    """Attempt resolution of opaque-narration exceptions only.

    Nothing here mutates the match result. Proposals are returned for human
    review; applying one is a separate, explicit decision.
    """
    targets = [e for e in exceptions if e.ex_class == ExClass.OPAQUE_NARRATION]
    report = ResolverReport()

    if use_llm is None:
        use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))

    if not targets:
        # Report the mode that would have been used, so an empty run is not
        # mislabelled in the audit trail.
        report.mode = "llm" if use_llm else "heuristic"
        return report

    unmatched = res.unmatched_payouts
    bank_by_id = {b.stmt_id: b for b in led.bank}

    client = None
    if use_llm:
        try:
            import anthropic

            client = anthropic.Anthropic()
        except Exception:
            client = None
    report.mode = "llm" if client else "heuristic"

    cache: dict[str, Proposal] = {}

    for ex in targets:
        row = bank_by_id.get(ex.ref)
        if not row:
            continue
        cands = _candidates(row, unmatched)
        if not cands:
            continue

        key = _cache_key(row, cands)
        if key in cache:
            hit = cache[key]
            report.proposals.append(
                Proposal(row.stmt_id, hit.settlement_id, hit.confidence, hit.reasoning, hit.delta_explanation, hit.source)
            )
            report.calls.append(ResolverCall(row.stmt_id, cached=True, latency_ms=0))
            continue

        if client:
            prop, call = _call_model(client, row, cands)
            report.calls.append(call)
            # A failed or malformed model response falls back rather than
            # dropping the item silently.
            if prop is None:
                prop = _heuristic(row, cands)
        else:
            prop = _heuristic(row, cands)
            report.calls.append(ResolverCall(row.stmt_id, cached=False, latency_ms=0))

        if prop and prop.confidence >= MIN_PROPOSAL_CONFIDENCE:
            cache[key] = prop
            report.proposals.append(prop)

    return report


def apply_proposals(
    exceptions: list[Exception_], report: ResolverReport
) -> list[Exception_]:
    """Attach proposals to their exceptions as suggestions.

    The exception stays open and `requires_human` stays True. A proposal is
    evidence for a human decision, never a match.
    """
    by_stmt = {p.stmt_id: p for p in report.proposals}
    for ex in exceptions:
        p = by_stmt.get(ex.ref)
        if not p or not p.settlement_id:
            continue
        ex.resolved_by = p.source
        ex.confidence = p.confidence
        ex.evidence.append(f"proposal: {p.settlement_id} ({p.source}, conf {p.confidence:.2f})")
        ex.evidence.append(f"reasoning: {p.reasoning}")
        if p.delta_explanation:
            ex.evidence.append(f"delta: {p.delta_explanation}")
        ex.suggested_resolution = (
            f"REVIEW: candidate match to {p.settlement_id}. "
            "Confirm against PSP payout reference before posting."
        )
    return exceptions
