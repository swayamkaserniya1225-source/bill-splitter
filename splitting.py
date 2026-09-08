"""
The actual engineering problem: turn (bill, people, who-ate-what) into
a per-person amount that is *correct*, not just plausible.

Algorithm
---------
1. Every line item is split among its assigned people, using custom
   ratios if given, else equally. This gives each person a pre-tax
   "item subtotal".
2. Discount, service charge, and tax are distributed across people in
   proportion to each person's share of the pre-tax subtotal — i.e. if
   you ate 20% of the food, you carry 20% of the service charge and
   20% of the tax. NOT divided by headcount. Someone who only had a
   Coke pays tax on a Coke, not a seventh of the biryani's tax.
3. Rounding: splitting money into fractions of paise means the naive
   per-person totals won't sum exactly to the bill total. We fix this
   with a largest-remainder pass so the sum of what people owe always
   equals the reconciled bill total to the paisa/cent — nobody's
   ₹0.30 vanishes into the ether.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List

from app.models import (
    BillExtraction,
    BillSplitResult,
    ItemAssignment,
    Person,
    PersonBreakdown,
    PersonLineShare,
    money,
)


def _item_shares(item_total: Decimal, person_ids: List[str], ratios: Dict[str, float] | None) -> Dict[str, Decimal]:
    """Split one item's total across its assigned people (unrounded internally)."""
    if ratios:
        total_weight = sum(ratios[p] for p in person_ids)
        return {p: item_total * Decimal(str(ratios[p])) / Decimal(str(total_weight)) for p in person_ids}
    n = len(person_ids)
    return {p: item_total / Decimal(n) for p in person_ids}


def _largest_remainder_fix(raw: Dict[str, Decimal], target_sum: Decimal) -> Dict[str, Decimal]:
    """
    Round every value to 2dp, then nudge the rounded values so they sum
    exactly to target_sum, giving the extra/missing paise to whoever's
    rounding error was largest (standard largest-remainder apportionment).
    """
    rounded = {k: money(v) for k, v in raw.items()}
    diff = money(target_sum - sum(rounded.values(), Decimal("0.00")))
    if diff == 0 or not rounded:
        return rounded

    step = Decimal("0.01") if diff > 0 else Decimal("-0.01")
    remainders = sorted(
        raw.keys(),
        key=lambda k: abs(raw[k] - rounded[k]),
        reverse=True,
    )
    n_steps = int(abs(diff) / Decimal("0.01"))
    for i in range(n_steps):
        k = remainders[i % len(remainders)]
        rounded[k] = money(rounded[k] + step)
    return rounded


def split_bill(
    extraction: BillExtraction,
    people: List[Person],
    assignments: List[ItemAssignment],
) -> BillSplitResult:
    extraction = extraction.reconcile()
    person_by_id = {p.id: p for p in people}
    assigned_item_ids = {a.item_id for a in assignments}
    for li in extraction.line_items:
        if li.id not in assigned_item_ids:
            raise ValueError(f"Line item '{li.name}' ({li.id}) has no assignment — every item must be assigned.")

    assignment_by_item = {a.item_id: a for a in assignments}

    # 1. per-person item subtotals + per-line shares
    item_subtotal_raw: Dict[str, Decimal] = {p.id: Decimal("0.00") for p in people}
    lines_by_person: Dict[str, List[PersonLineShare]] = {p.id: [] for p in people}

    for li in extraction.line_items:
        a = assignment_by_item[li.id]
        shares = _item_shares(li.line_total, a.person_ids, a.ratios)
        shared_with_names = [person_by_id[pid].name for pid in a.person_ids]
        for pid, amt in shares.items():
            item_subtotal_raw[pid] += amt
            lines_by_person[pid].append(
                PersonLineShare(
                    item_id=li.id,
                    item_name=li.name,
                    share_amount=money(amt),
                    full_item_total=li.line_total,
                    shared_with=shared_with_names,
                )
            )

    bill_subtotal = extraction.computed_subtotal or Decimal("0.00")

    # 2. proportional distribution of discount / service charge / tax
    discount_raw, service_raw, tax_raw, final_raw = {}, {}, {}, {}
    for p in people:
        proportion = (item_subtotal_raw[p.id] / bill_subtotal) if bill_subtotal > 0 else Decimal("0.00")
        discount_raw[p.id] = extraction.discount * proportion
        service_raw[p.id] = extraction.service_charge * proportion
        tax_raw[p.id] = extraction.total_tax * proportion
        final_raw[p.id] = (
            item_subtotal_raw[p.id] - discount_raw[p.id] + service_raw[p.id] + tax_raw[p.id]
        )

    target_total = extraction.printed_total if not extraction.total_mismatch and extraction.printed_total else extraction.computed_total
    final_rounded = _largest_remainder_fix(final_raw, target_total)

    breakdowns = []
    for p in people:
        rounded_pre_adjust = money(
            item_subtotal_raw[p.id] - discount_raw[p.id] + service_raw[p.id] + tax_raw[p.id]
        )
        adjustment = money(final_rounded[p.id] - rounded_pre_adjust)
        breakdowns.append(
            PersonBreakdown(
                person_id=p.id,
                name=p.name,
                lines=lines_by_person[p.id],
                item_subtotal=money(item_subtotal_raw[p.id]),
                share_of_discount=money(discount_raw[p.id]),
                share_of_service_charge=money(service_raw[p.id]),
                share_of_tax=money(tax_raw[p.id]),
                rounding_adjustment=adjustment,
                final_total=final_rounded[p.id],
            )
        )

    sum_of_shares = money(sum((b.final_total for b in breakdowns), Decimal("0.00")))

    return BillSplitResult(
        people=breakdowns,
        bill_computed_total=extraction.computed_total,
        bill_printed_total=extraction.printed_total,
        sum_of_shares=sum_of_shares,
        discrepancy_flagged=extraction.total_mismatch,
        discrepancy_amount=extraction.total_mismatch_amount,
    )


def payout_summary(result: BillSplitResult) -> List[str]:
    """Human-readable one-liners, good for pasting into a group chat."""
    lines = []
    for b in result.people:
        item_bits = ", ".join(
            f"{ln.item_name}" + (f" (shared w/ {len(ln.shared_with)-1} other{'s' if len(ln.shared_with) > 2 else ''})"
                                  if len(ln.shared_with) > 1 else "")
            for ln in b.lines
        )
        lines.append(f"{b.name} owes ₹{b.final_total} — for: {item_bits}")
    return lines
