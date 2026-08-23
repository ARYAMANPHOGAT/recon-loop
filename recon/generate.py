"""Synthetic three-source data generator.

The point of this file is NOT to make data that reconciles. It is to make data
that fails to reconcile in the specific ways real Indian PSP settlement data
fails to reconcile, so that the matching engine is tested against something
honest.

Every break we inject is recorded in ledger.scenario_log so the eval harness
can score per failure class rather than reporting one blended number.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from .models import (
    BankLine,
    Ledger,
    Order,
    OrderStatus,
    Paise,
    SettlementLine,
    TxnType,
)

# --- Indian PSP economics (test-mode realistic) ---------------------------
FEE_BPS = {
    "upi": 0,  # UPI is zero-MDR for most merchants
    "card": 200,  # 2.00%
    "netbanking": 175,  # 1.75%
    "wallet": 220,  # 2.20%
}
GST_BPS = 1800  # 18% GST on the commission

METHOD_WEIGHTS = [("upi", 0.62), ("card", 0.22), ("netbanking", 0.10), ("wallet", 0.06)]

BANKS = ["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "PYTM"]

FIRST_NAMES = [
    "Rahul", "Priya", "Amit", "Sneha", "Vikram", "Anjali", "Rohan", "Kavya",
    "Arjun", "Meera", "Karan", "Divya", "Suresh", "Nisha", "Manish", "Pooja",
]
LAST_NAMES = [
    "Sharma", "Patel", "Reddy", "Iyer", "Gupta", "Nair", "Singh", "Mehta",
    "Desai", "Kulkarni", "Banerjee", "Chopra", "Rao", "Joshi",
]
CHANNELS = ["web", "app", "pos", "invoice"]

# Common Indian retail price points, in paise. Deliberately includes the
# charm-pricing values (x99) that dominate real catalogues.
PRICE_POINTS = [
    29_900, 49_900, 59_900, 79_900, 99_900,
    129_900, 149_900, 199_900, 249_900, 299_900,
    399_900, 499_900, 599_900, 799_900, 999_900,
    1_499_900, 1_999_900, 2_499_900,
]


def _weighted_method(rng: random.Random) -> str:
    r = rng.random()
    cum = 0.0
    for m, w in METHOD_WEIGHTS:
        cum += w
        if r <= cum:
            return m
    return "upi"


def _fee_for(gross: Paise, method: str) -> tuple[Paise, Paise]:
    """Return (fee, tax). Banker-unfriendly rounding is deliberate:
    PSPs round the fee to the nearest paise, and that residue is a real
    source of one- and two-paise recon breaks."""
    fee = round(gross * FEE_BPS[method] / 10_000)
    tax = round(fee * GST_BPS / 10_000)
    return fee, tax


def _mangle_name(rng: random.Random, first: str, last: str) -> str:
    """Produce the payer description a PSP would actually capture.

    Merchant staff type a name into an order book; a customer types their own
    name at checkout; a bank truncates it. The two strings refer to the same
    person and are rarely identical. Every variant below is drawn from a real
    failure mode, which is why exact-match on names is useless here.
    """
    style = rng.random()
    if style < 0.22:
        return f"{first} {last}".upper()
    if style < 0.40:
        return f"{first[0]}. {last}"                      # initial only
    if style < 0.55:
        return f"{last} {first}"                          # order swapped
    if style < 0.68:
        return f"{first} {last}".replace("a", "aa", 1)    # phonetic spelling
    if style < 0.78:
        return f"{first}{last}".upper()                   # whitespace lost
    if style < 0.87:
        return f"{first} {last[:4]}"                      # truncated by field limit
    if style < 0.94:
        return f"MR {first} {last}".upper()               # honorific added
    return f"{first} {last}"                              # occasionally clean


def _utr(rng: random.Random) -> str:
    return f"{rng.randint(10**11, 10**12 - 1)}"


def _narration(rng: random.Random, settlement_id: str, utr: str, bank: str) -> str:
    """Bank narrations are inconsistent across banks and across time.
    These templates are drawn from the real shapes you see in Indian
    current-account statements."""
    templates = [
        f"NEFT-{utr}-RAZORPAY SOFTWARE PVT LTD-{settlement_id}",
        f"UPI/CR/{utr}/RAZORPAYSOFTW/{bank}/{settlement_id}",
        f"IMPS/{utr}/RAZORPAY SOFTW PL/SETTLEMENT",
        f"RTGS CR {bank}R{utr} RAZORPAY SOFTWARE PRIVATE LIMITED",
        f"NEFT CR-{bank}0000123-RAZORPAY SOFTWARE PVT L-{settlement_id[:14]}",
        f"MB:NEFT:{utr}:RZRPY SOFTWARE:{settlement_id}",
    ]
    return rng.choice(templates)


def generate(
    n_orders: int = 220,
    seed: int = 42,
    start: date | None = None,
    days: int = 30,
) -> Ledger:
    rng = random.Random(seed)
    start = start or date(2026, 7, 1)
    led = Ledger()

    def log(kind: str, **kw):
        led.scenario_log.append({"scenario": kind, **kw})

    # ---------------- 1. Orders ------------------------------------------
    orders: list[Order] = []
    name_pairs: dict[str, tuple[str, str]] = {}
    for i in range(n_orders):
        day_offset = rng.randint(0, days - 1)
        created = datetime.combine(
            start + timedelta(days=day_offset),
            datetime.min.time(),
        ) + timedelta(hours=rng.randint(6, 23), minutes=rng.randint(0, 59))

        # Merchants sell at price points, not at random amounts. Roughly 70% of
        # orders land on a fixed SKU price, which means many orders share an
        # amount exactly. This is what makes amount-plus-time insufficient on
        # its own and forces the name tier to do real work - generating random
        # amounts would have made the order leg look far easier than it is.
        if rng.random() < 0.70:
            amount = rng.choice(PRICE_POINTS)
        else:
            amount = rng.choice(
                [
                    rng.randint(9_900, 99_900),
                    rng.randint(100_000, 500_000),
                    rng.randint(500_000, 2_500_000),
                    rng.randint(2_500_000, 15_000_000),
                ]
            )

        # ~86% of orders convert; rest sit as attempted/created and should
        # never appear in settlement. They are noise the matcher must ignore.
        roll = rng.random()
        status = (
            OrderStatus.PAID
            if roll < 0.86
            else (OrderStatus.ATTEMPTED if roll < 0.95 else OrderStatus.CREATED)
        )

        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        orders.append(
            Order(
                order_id=f"order_{seed}{i:05d}",
                customer_ref=f"cust_{rng.randint(1000, 9999)}",
                customer_name=f"{first} {last}",
                invoice_ref=f"INV{rng.randint(1000, 9999)}",
                amount=amount,
                currency="INR",
                status=status,
                created_at=created,
                channel=rng.choice(CHANNELS),
            )
        )
        name_pairs[f"order_{seed}{i:05d}"] = (first, last)
    led.orders = orders

    paid_orders = [o for o in orders if o.status == OrderStatus.PAID]

    # ---------------- 2. Settlement lines --------------------------------
    lines: list[SettlementLine] = []
    for idx, o in enumerate(paid_orders):
        method = _weighted_method(rng)
        gross = o.amount
        fee, tax = _fee_for(gross, method)
        pay_id = f"pay_{seed}{idx:05d}"
        lines.append(
            SettlementLine(
                entity_id=pay_id,
                order_id=o.order_id,
                txn_type=TxnType.PAYMENT,
                gross=gross,
                fee=fee,
                tax=tax,
                net=gross - fee - tax,
                captured_at=o.created_at + timedelta(minutes=rng.randint(0, 8)),
                settlement_id=None,  # assigned in the payout step
                settled_on=None,
                method=method,
                payer_description=_mangle_name(rng, *name_pairs[o.order_id]),
            )
        )
        led.truth_entity_to_order[pay_id] = o.order_id

        # BREAK M: not every channel round-trips the order id. Payment links
        # and POS collections are frequently raised outside the ERP, so the
        # settlement line arrives carrying only an amount, a timestamp and a
        # payer name. Dropping the id here is what forces the order leg to
        # earn its match rather than reading a key off the record.
        if o.channel in ("invoice", "pos") and rng.random() < 0.75:
            lines[-1].order_id = None
            log("order_id_absent", entity_id=pay_id, channel=o.channel)

    # --- BREAK A: refunds issued after capture, netted into a later payout
    n_refunds = max(3, int(len(lines) * 0.045))
    for r in rng.sample(lines, n_refunds):
        # partial refunds are the nastier case - include both
        full = rng.random() < 0.55
        amt = r.gross if full else round(r.gross * rng.uniform(0.2, 0.7))
        rid = f"rfnd_{rng.randint(10000, 99999)}"
        lines.append(
            SettlementLine(
                entity_id=rid,
                order_id=r.order_id,
                txn_type=TxnType.REFUND,
                gross=-amt,
                fee=0,  # PSP does not return commission on refund
                tax=0,
                net=-amt,
                captured_at=r.captured_at + timedelta(days=rng.randint(1, 5)),
                settlement_id=None,
                settled_on=None,
                method=r.method,
            )
        )
        led.truth_entity_to_order[rid] = r.order_id
        log("refund_netted", entity_id=rid, order_id=r.order_id, full=full)

    # --- BREAK B: chargebacks - debited from a payout with no merchant record
    n_cb = max(2, int(len(paid_orders) * 0.012))
    for c in rng.sample(lines[: len(paid_orders)], n_cb):
        cid = f"cb_{rng.randint(10000, 99999)}"
        lines.append(
            SettlementLine(
                entity_id=cid,
                order_id=c.order_id,
                txn_type=TxnType.CHARGEBACK,
                gross=-c.gross,
                fee=-50_000,  # Rs.500 chargeback handling fee
                tax=0,
                net=-c.gross - 50_000,
                captured_at=c.captured_at + timedelta(days=rng.randint(6, 20)),
                settlement_id=None,
                settled_on=None,
                method=c.method,
            )
        )
        led.truth_entity_to_order[cid] = c.order_id
        log("chargeback", entity_id=cid, order_id=c.order_id)

    # --- BREAK C: a payment with no order in the merchant book
    #     (payment link / manual invoice collected outside the ERP)
    for k in range(3):
        oid_missing = f"pay_orphan_{k}"
        gross = rng.randint(50_000, 800_000)
        fee, tax = _fee_for(gross, "card")
        lines.append(
            SettlementLine(
                entity_id=oid_missing,
                order_id=None,
                txn_type=TxnType.PAYMENT,
                gross=gross,
                fee=fee,
                tax=tax,
                net=gross - fee - tax,
                captured_at=datetime.combine(
                    start + timedelta(days=rng.randint(0, days - 1)),
                    datetime.min.time(),
                )
                + timedelta(hours=14),
                settlement_id=None,
                settled_on=None,
                method="card",
            )
        )
        log("orphan_payment", entity_id=oid_missing)

    # ---------------- 3. Payouts -> bank credits -------------------------
    # PSP batches everything captured on day D into a payout on D+2.
    by_settle_day: dict[date, list[SettlementLine]] = {}
    for ln in lines:
        settle_day = (ln.captured_at.date()) + timedelta(days=2)
        # weekends push to Monday - a classic timing break
        while settle_day.weekday() >= 5:
            settle_day += timedelta(days=1)
        by_settle_day.setdefault(settle_day, []).append(ln)

    bank_rows: list[BankLine] = []
    balance: Paise = 5_000_000
    stmt_i = 0

    # Merchants on instant settlement get several payouts a day, not one.
    # Sub-batching here is what makes bank-side matching non-trivial: several
    # credits land on the same date with similar-looking narrations.
    day_batches: list[tuple[date, list[SettlementLine]]] = []
    for sday in sorted(by_settle_day):
        lines_today = by_settle_day[sday]
        if len(lines_today) >= 6 and rng.random() < 0.7:
            k = rng.randint(2, 3)
            rng.shuffle(lines_today)
            size = len(lines_today) // k
            for j in range(k):
                chunk = (
                    lines_today[j * size :]
                    if j == k - 1
                    else lines_today[j * size : (j + 1) * size]
                )
                if chunk:
                    day_batches.append((sday, chunk))
        else:
            day_batches.append((sday, lines_today))

    # Settlement ids must be globally unique. A random suffix collides when a
    # day carries several batches, which silently merges two distinct payouts
    # into one during roll-up and corrupts the ground-truth map. A monotonic
    # counter costs nothing and removes the failure mode entirely.
    payout_seq = 0
    for sday, batch in day_batches:
        payout_seq += 1
        sid = f"setl_{sday.strftime('%d%m')}{payout_seq:03d}"
        for ln in batch:
            ln.settlement_id = sid
            ln.settled_on = sday

        payout = sum(ln.net for ln in batch)
        if payout <= 0:
            # net-negative day: PSP carries it forward instead of debiting
            log("negative_payout_carried", settlement_id=sid, amount=payout)
            continue

        bank = rng.choice(BANKS)
        utr = _utr(rng)

        roll = rng.random()

        # --- BREAK D: payout split across two bank credits (RTGS limit /
        #     partial release). Same settlement_id, two stmt rows.
        if roll < 0.08 and payout > 100_000:
            part1 = round(payout * rng.uniform(0.35, 0.65))
            part2 = payout - part1
            for part_i, amt in enumerate((part1, part2)):
                stmt_i += 1
                balance += amt
                row = BankLine(
                    stmt_id=f"stmt_{stmt_i:05d}",
                    value_date=sday,
                    narration=_narration(rng, sid, utr if part_i == 0 else _utr(rng), bank),
                    credit=amt,
                    debit=0,
                    balance=balance,
                    utr=utr if part_i == 0 else _utr(rng),
                )
                bank_rows.append(row)
            led.truth_payout_to_bank[sid] = f"stmt_{stmt_i-1:05d}+stmt_{stmt_i:05d}"
            log("split_payout", settlement_id=sid, parts=2, total=payout)
            continue

        # --- BREAK E: bank credits a day late (cutoff miss)
        credit_date = sday
        if roll < 0.14:
            credit_date = sday + timedelta(days=1)
            log("late_credit", settlement_id=sid, expected=str(sday), actual=str(credit_date))

        # --- BREAK F: narration missing the settlement id entirely
        narr = _narration(rng, sid, utr, bank)
        if roll < 0.20:
            narr = f"NEFT CR {utr} RAZORPAY SOFTWARE"
            log("opaque_narration", settlement_id=sid)

        stmt_i += 1
        balance += payout
        bank_rows.append(
            BankLine(
                stmt_id=f"stmt_{stmt_i:05d}",
                value_date=credit_date,
                narration=narr,
                credit=payout,
                debit=0,
                balance=balance,
                utr=utr,
            )
        )
        led.truth_payout_to_bank[sid] = f"stmt_{stmt_i:05d}"

    # --- BREAK G: unrelated bank activity the matcher must leave alone
    n_noise = int(len(bank_rows) * 0.35) + 3
    for k in range(n_noise):
        stmt_i += 1
        amt = rng.randint(20_000, 900_000)
        is_credit = rng.random() < 0.35
        noise = rng.choice(
            [
                "NEFT DR-VENDOR PAYMENT-ACME SUPPLIES",
                "SALARY JUL2026 PAYROLL BATCH",
                "GST PMT-06 CHALLAN 24073100012345",
                "NEFT CR-CUSTOMER DIRECT TRANSFER",
                "BANK CHARGES-QTRLY MAINT",
                "TDS 194J DEDUCTION REMITTANCE",
            ]
        )
        balance += amt if is_credit else -amt
        bank_rows.append(
            BankLine(
                stmt_id=f"stmt_{stmt_i:05d}",
                value_date=start + timedelta(days=rng.randint(0, days + 2)),
                narration=noise,
                credit=amt if is_credit else 0,
                debit=0 if is_credit else amt,
                balance=balance,
                utr=_utr(rng) if rng.random() < 0.5 else None,
            )
        )
    log("bank_noise_rows", count=n_noise)

    # --- BREAK H: duplicate UTR reused by the bank (rare but brutal)
    credits = [b for b in bank_rows if b.credit > 0 and b.utr]
    if len(credits) >= 2:
        a, b = rng.sample(credits, 2)
        b.utr = a.utr
        log("duplicate_utr", utr=a.utr, rows=[a.stmt_id, b.stmt_id])

    bank_rows.sort(key=lambda r: (r.value_date, r.stmt_id))
    led.bank = bank_rows
    led.settlements = lines
    _inject_hard_breaks(led, rng, start, days, log)
    return led


def _inject_hard_breaks(led: Ledger, rng, start: date, days: int, log) -> None:
    """Breaks that a competent engine should NOT fully resolve.

    An exception list is only honest if some exceptions are genuinely
    unresolvable from the data available. These four classes exist so the
    engine has something real to fail on, and so the reported match rate is
    a measurement rather than a decoration.
    """
    credits = [b for b in led.bank if b.credit > 0]
    if len(credits) < 8:
        return

    # --- BREAK I: bank levies NEFT/RTGS charges on the credit -------------
    # Credit lands short by a fixed charge. Amount tier will miss it; the
    # engine must either explain the delta or list it as an exception.
    for b in rng.sample(credits, 2):
        charge = rng.choice([1_770, 2_950, 5_900])  # Rs.15/25/50 + 18% GST
        b.credit -= charge
        log("bank_charge_deducted", stmt_id=b.stmt_id, charge=charge)

    # --- BREAK J: payout in transit at period close -----------------------
    # The statement closes on a fixed date. A payout settled inside the T+2
    # credit window of that close has not had time to land, so its absence is
    # a timing fact rather than missing money. Targeting the boundary
    # specifically is the whole point: a payout settled a week earlier with
    # no credit is a genuine exception, not transit.
    payouts_by_day: dict[str, date] = {}
    for l in led.settlements:
        if l.settlement_id and l.settled_on:
            payouts_by_day[l.settlement_id] = l.settled_on

    if payouts_by_day:
        period_end = max(payouts_by_day.values()) + timedelta(days=1)
        led.period_start = start
        led.period_end = period_end
        # anything the bank dated past the close is not on this statement
        led.bank = [b for b in led.bank if b.value_date <= period_end]

        boundary = [
            sid for sid, d in payouts_by_day.items() if (period_end - d).days <= 2
        ]
        if boundary:
            target = sorted(boundary)[-1]
            drop = [b for b in led.bank if target.lower() in b.narration.lower()]
            for b in drop:
                led.bank.remove(b)
            log(
                "payout_in_transit",
                settlement_id=target,
                settled_on=str(payouts_by_day[target]),
                period_end=str(period_end),
                rows_removed=len(drop),
            )

    # --- BREAK K: credit belonging to a payout from before the window -----
    # Looks exactly like a real settlement credit. Has no counterpart in this
    # batch because its settlement report was last month's.
    for k in range(2):
        amt = rng.randint(200_000, 1_500_000)
        led.bank.append(
            BankLine(
                stmt_id=f"stmt_prior_{k}",
                value_date=start + timedelta(days=rng.randint(0, 2)),
                narration=f"NEFT-{_utr(rng)}-RAZORPAY SOFTWARE PVT LTD-setl_2806{rng.randint(100,999)}",
                credit=amt,
                debit=0,
                balance=0,
                utr=_utr(rng),
            )
        )
        log("prior_period_credit", stmt_id=f"stmt_prior_{k}")

    # --- BREAK L: a second PSP's settlements in the same account ----------
    # Must NOT be matched to Razorpay payouts. Tests restraint, not recall.
    for k in range(3):
        amt = rng.randint(80_000, 600_000)
        led.bank.append(
            BankLine(
                stmt_id=f"stmt_psp2_{k}",
                value_date=start + timedelta(days=rng.randint(0, days)),
                narration=f"NEFT-{_utr(rng)}-PAYU PAYMENTS PVT LTD-PYU{rng.randint(10000,99999)}",
                credit=amt,
                debit=0,
                balance=0,
                utr=_utr(rng),
            )
        )
        log("other_psp_credit", stmt_id=f"stmt_psp2_{k}")

    led.bank.sort(key=lambda r: (r.value_date, r.stmt_id))


if __name__ == "__main__":
    L = generate()
    print(f"orders      : {len(L.orders)}")
    print(f"settlements : {len(L.settlements)}")
    print(f"bank lines  : {len(L.bank)}")
    print(f"breaks      : {len(L.scenario_log)}")
    from collections import Counter

    for k, v in Counter(s["scenario"] for s in L.scenario_log).most_common():
        print(f"  {k:28s} {v}")
