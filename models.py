"""
Pydantic models for the bill-splitting pipeline.

Design notes
------------
- Money is stored as Decimal (quantized to paise/cents) — never float —
  because floating point errors compound across 7+ people and several
  tax fields, and the whole point of this tool is "get the correct
  number for each person."
- Every OCR'd field carries a confidence score in [0, 1]. Confidence is
  produced by the extraction step (see extraction.py) and consumed by
  the review UI to decide what to highlight for a human to check
  *before* any arithmetic happens.
- BillExtraction is deliberately "dumb" — it holds what the model read
  off the paper, plus an automatic reconciliation flag. It does not
  know about people or assignments. splitting.py is the only place
  where money actually moves between people.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

TWO_PLACES = Decimal("0.01")


def money(x) -> Decimal:
    """Coerce anything numeric into a 2-decimal Decimal."""
    if x is None:
        return Decimal("0.00")
    return Decimal(str(x)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class LineItem(BaseModel):
    id: str = Field(..., description="Stable id, e.g. 'item_1'")
    name: str
    quantity: float = 1.0
    unit_price: Decimal = Decimal("0.00")
    line_total: Decimal = Decimal("0.00")

    # per-field confidence, 0..1, from the OCR/extraction step
    name_confidence: float = 1.0
    price_confidence: float = 1.0
    qty_confidence: float = 1.0

    # which uploaded photo this line came from (0-indexed) — used when
    # a bill needed two photographs and items had to be stitched
    source_photo_index: int = 0

    # set to True if a human edited this row on the review screen
    human_corrected: bool = False

    @field_validator("unit_price", "line_total", mode="before")
    @classmethod
    def _coerce_money(cls, v):
        return money(v)

    @property
    def lowest_confidence(self) -> float:
        return min(self.name_confidence, self.price_confidence, self.qty_confidence)


class BillExtraction(BaseModel):
    restaurant_name: Optional[str] = None
    bill_number: Optional[str] = None
    date: Optional[str] = None

    line_items: List[LineItem] = Field(default_factory=list)

    subtotal: Optional[Decimal] = None
    subtotal_confidence: float = 1.0

    discount: Decimal = Decimal("0.00")
    discount_confidence: float = 1.0

    service_charge: Decimal = Decimal("0.00")
    service_charge_confidence: float = 1.0

    # India-style bills often split GST into CGST + SGST; we keep both
    # but also accept a single combined "tax" figure via cgst-only use
    cgst: Decimal = Decimal("0.00")
    sgst: Decimal = Decimal("0.00")
    other_tax: Decimal = Decimal("0.00")
    tax_confidence: float = 1.0

    printed_total: Optional[Decimal] = None
    printed_total_confidence: float = 1.0

    # --- filled in automatically by reconcile(), not by the OCR step ---
    computed_subtotal: Optional[Decimal] = None
    computed_total: Optional[Decimal] = None
    subtotal_mismatch: bool = False
    total_mismatch: bool = False
    total_mismatch_amount: Optional[Decimal] = None

    @field_validator(
        "subtotal", "discount", "service_charge", "cgst", "sgst",
        "other_tax", "printed_total", "computed_subtotal", "computed_total",
        "total_mismatch_amount",
        mode="before",
    )
    @classmethod
    def _coerce_money(cls, v):
        return None if v is None else money(v)

    @property
    def total_tax(self) -> Decimal:
        return money(self.cgst + self.sgst + self.other_tax)

    def reconcile(self, tolerance: Decimal = Decimal("1.00")) -> "BillExtraction":
        """
        Recompute subtotal/total from the line items and compare against
        what was printed on the bill. This is what catches the bill
        whose printed total is genuinely wrong: we don't trust the
        printed number, we verify it.
        """
        computed_subtotal = money(sum((li.line_total for li in self.line_items), Decimal("0.00")))
        computed_total = money(
            computed_subtotal - self.discount + self.service_charge + self.total_tax
        )
        self.computed_subtotal = computed_subtotal
        self.computed_total = computed_total

        if self.subtotal is not None:
            self.subtotal_mismatch = abs(computed_subtotal - self.subtotal) > tolerance
        if self.printed_total is not None:
            diff = computed_total - self.printed_total
            self.total_mismatch = abs(diff) > tolerance
            self.total_mismatch_amount = diff if self.total_mismatch else Decimal("0.00")
        return self


class Person(BaseModel):
    id: str
    name: str
    left_early: bool = False  # informational only; enforcement is via assignments


class ItemAssignment(BaseModel):
    """
    Who ate a given line item.

    - person_ids: the people this item is split across. A single id ->
      that person pays it all. All person ids in the bill -> "everyone".
      A subset -> a shared item (e.g. two people split the biryani).
    - ratios: optional custom weights keyed by person_id (e.g. one
      person had 2 of 3 portions). If omitted, the item splits equally
      across person_ids.
    """
    item_id: str
    person_ids: List[str]
    ratios: Optional[Dict[str, float]] = None

    @model_validator(mode="after")
    def _check(self):
        if not self.person_ids:
            raise ValueError(f"Item {self.item_id} has no one assigned to it")
        if self.ratios is not None:
            missing = set(self.person_ids) - set(self.ratios.keys())
            if missing:
                raise ValueError(f"Item {self.item_id}: ratios missing for {missing}")
        return self


class PersonLineShare(BaseModel):
    item_id: str
    item_name: str
    share_amount: Decimal
    full_item_total: Decimal
    shared_with: List[str]  # names of everyone this item was split with (incl. self)


class PersonBreakdown(BaseModel):
    person_id: str
    name: str
    lines: List[PersonLineShare]
    item_subtotal: Decimal = Decimal("0.00")
    share_of_discount: Decimal = Decimal("0.00")
    share_of_service_charge: Decimal = Decimal("0.00")
    share_of_tax: Decimal = Decimal("0.00")
    rounding_adjustment: Decimal = Decimal("0.00")
    final_total: Decimal = Decimal("0.00")


class BillSplitResult(BaseModel):
    people: List[PersonBreakdown]
    bill_computed_total: Decimal
    bill_printed_total: Optional[Decimal]
    sum_of_shares: Decimal
    discrepancy_flagged: bool
    discrepancy_amount: Optional[Decimal] = None
