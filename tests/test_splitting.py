"""
Pure-logic tests — no network, no photos needed. These encode the exact
scenarios from the assessment brief so the splitting algorithm's
correctness doesn't depend on OCR quality.
"""
from decimal import Decimal

import pytest

from app.models import BillExtraction, ItemAssignment, LineItem, Person
from app.splitting import split_bill


def make_people(n):
    return [Person(id=f"p{i}", name=f"Person{i}") for i in range(1, n + 1)]


def test_seven_people_two_share_biryani_one_only_coke():
    """
    The exact scenario from the brief: 7 people, biryani shared by 2,
    one person only has a Coke. Tax/service must NOT be divided by 7.
    """
    people = make_people(7)
    biryani = LineItem(id="biryani", name="Biryani", quantity=1, unit_price=Decimal("600"), line_total=Decimal("600"))
    coke = LineItem(id="coke", name="Coke", quantity=1, unit_price=Decimal("60"), line_total=Decimal("60"))
    other_food = LineItem(id="mains", name="Mains for the rest", quantity=1,
                           unit_price=Decimal("1000"), line_total=Decimal("1000"))

    ext = BillExtraction(
        line_items=[biryani, coke, other_food],
        subtotal=Decimal("1660"),
        service_charge=Decimal("83"),   # 5% of subtotal
        cgst=Decimal("41.5"),           # 2.5%
        sgst=Decimal("41.5"),           # 2.5%
        printed_total=Decimal("1826"),
    ).reconcile()

    assignments = [
        ItemAssignment(item_id="biryani", person_ids=["p1", "p2"]),
        ItemAssignment(item_id="coke", person_ids=["p1"]),
        ItemAssignment(item_id="mains", person_ids=[p.id for p in people[2:]]),  # p3..p7
    ]

    result = split_bill(ext, people, assignments)
    by_id = {b.person_id: b for b in result.people}

    # p1 had half the biryani (300) + the whole coke (60) = 360 of food
    assert by_id["p1"].item_subtotal == Decimal("360.00")
    # p2 had half the biryani only = 300
    assert by_id["p2"].item_subtotal == Decimal("300.00")
    # p3..p7 split 1000 five ways = 200 each
    assert by_id["p3"].item_subtotal == Decimal("200.00")

    # the Coke-only portion of p1's bill should carry Coke's tax share,
    # not a 1/7 share of the whole table's tax
    assert by_id["p1"].share_of_tax > by_id["p3"].share_of_tax  # p1 ate more (360 vs 200)
    naive_seventh = (ext.total_tax / 7).quantize(Decimal("0.01"))
    assert by_id["p3"].share_of_tax != naive_seventh  # proportional, not equal split

    # shares must reconcile exactly to the bill total (no lost paise)
    assert result.sum_of_shares == ext.computed_total


def test_someone_left_early_excluded_from_later_items():
    """A person who left before dessert shouldn't be in that item's assignment."""
    people = make_people(3)
    starter = LineItem(id="starter", name="Starters", quantity=1, unit_price=Decimal("300"), line_total=Decimal("300"))
    dessert = LineItem(id="dessert", name="Dessert", quantity=1, unit_price=Decimal("150"), line_total=Decimal("150"))

    ext = BillExtraction(line_items=[starter, dessert], subtotal=Decimal("450"),
                          printed_total=Decimal("450")).reconcile()

    assignments = [
        ItemAssignment(item_id="starter", person_ids=["p1", "p2", "p3"]),
        ItemAssignment(item_id="dessert", person_ids=["p1", "p2"]),  # p3 left early
    ]
    result = split_bill(ext, people, assignments)
    by_id = {b.person_id: b for b in result.people}
    assert by_id["p3"].item_subtotal == Decimal("100.00")  # only 1/3 of starters
    assert by_id["p1"].item_subtotal == Decimal("175.00")  # 1/3 starter + 1/2 dessert


def test_custom_ratio_split():
    """Two people share a dish unequally (one had 2 of 3 portions)."""
    people = make_people(2)
    dish = LineItem(id="d", name="Sharing platter", quantity=1, unit_price=Decimal("300"), line_total=Decimal("300"))
    ext = BillExtraction(line_items=[dish], subtotal=Decimal("300"), printed_total=Decimal("300")).reconcile()
    assignments = [ItemAssignment(item_id="d", person_ids=["p1", "p2"], ratios={"p1": 2, "p2": 1})]
    result = split_bill(ext, people, assignments)
    by_id = {b.person_id: b for b in result.people}
    assert by_id["p1"].item_subtotal == Decimal("200.00")
    assert by_id["p2"].item_subtotal == Decimal("100.00")


def test_wrong_printed_total_is_detected_and_computed_total_used():
    """The bill whose printed total is genuinely wrong: we must not trust it blindly."""
    people = make_people(2)
    item = LineItem(id="i1", name="Thali", quantity=2, unit_price=Decimal("250"), line_total=Decimal("500"))
    ext = BillExtraction(
        line_items=[item], subtotal=Decimal("500"),
        cgst=Decimal("12.5"), sgst=Decimal("12.5"),
        printed_total=Decimal("600"),  # wrong on purpose — should be 525
    ).reconcile()

    assert ext.total_mismatch is True
    assert ext.computed_total == Decimal("525.00")

    assignments = [ItemAssignment(item_id="i1", person_ids=["p1", "p2"])]
    result = split_bill(ext, people, assignments)
    assert result.discrepancy_flagged is True
    # shares must sum to the CORRECT (computed) total, not the wrong printed one
    assert result.sum_of_shares == Decimal("525.00")
    assert result.sum_of_shares != Decimal("600.00")


def test_every_item_must_be_assigned():
    people = make_people(2)
    item = LineItem(id="i1", name="Unassigned dish", quantity=1, unit_price=Decimal("100"), line_total=Decimal("100"))
    ext = BillExtraction(line_items=[item], printed_total=Decimal("100")).reconcile()
    with pytest.raises(ValueError, match="no assignment"):
        split_bill(ext, people, assignments=[])


def test_rounding_reconciliation_sums_exactly():
    """Splitting an odd amount 3 ways must still sum to the exact total, to the paisa."""
    people = make_people(3)
    item = LineItem(id="i1", name="Something", quantity=1, unit_price=Decimal("100"), line_total=Decimal("100"))
    ext = BillExtraction(line_items=[item], subtotal=Decimal("100"), printed_total=Decimal("100")).reconcile()
    assignments = [ItemAssignment(item_id="i1", person_ids=["p1", "p2", "p3"])]
    result = split_bill(ext, people, assignments)
    assert result.sum_of_shares == Decimal("100.00")
    # each share should be ~33.33/33.33/33.34, never off by more than a paisa from equal
    totals = sorted(b.final_total for b in result.people)
    assert totals[-1] - totals[0] <= Decimal("0.01")
