# Bill Splitter — photo in, correct number per person out

Seven people, one bill, one biryani split two ways, one person who only
had a Coke, service charge and GST on top, and a printed total that
might just be wrong. This is that argument, automated.

## Architecture

```
Photo(s) ──▶ extraction.py ──▶ BillExtraction (Pydantic, per-field confidence)
                                        │
                                        ▼
                              reconcile() — recomputes subtotal/total
                              from line items, flags mismatches
                              (catches the wrong-printed-total bill)
                                        │
                                        ▼
                          Review screen (streamlit_app.py) — human
                          fixes anything flagged before money moves
                                        │
                                        ▼
                          Assign each item to 1, several, or all people
                                        │
                                        ▼
                              splitting.py — split_bill()
                          proportional tax/service/discount, exact
                          rounding reconciliation
                                        │
                                        ▼
                          BillSplitResult — per-person breakdown
```

- **`app/models.py`** — every data shape as a Pydantic model:
  `LineItem`, `BillExtraction`, `Person`, `ItemAssignment`,
  `PersonBreakdown`, `BillSplitResult`. Money is `Decimal`, never
  `float` — floats compound rounding errors across 7 people and 4 tax
  fields, and being wrong by ₹0.30 in a way nobody can explain is
  exactly the kind of thing that restarts the argument.
- **`app/extraction.py`** — sends photo(s) to Claude's vision API with
  a prompt that explicitly names the hard cases (dim light, thermal
  fade, handwriting, mixed scripts, multi-photo bills) and asks for a
  confidence score per field, not just a value.
- **`app/splitting.py`** — the actual algorithm. See "The algorithm"
  below.
- **`app/streamlit_app.py`** — the demo: upload → review/correct →
  add people → assign items → breakdown. No chatbot, just the
  workflow the brief asked for.
- **`app/compute_accuracy.py`** — scores extraction accuracy against
  hand-labeled ground truth, once you've built the 12-bill test set.

## The algorithm (why "divide by 7" is wrong)

1. Each line item is split across the people assigned to it — equally
   by default, or by a custom ratio (someone had 2 of 3 portions).
   This gives every person a pre-tax **item subtotal**.
2. Discount, service charge, and tax are distributed **in proportion
   to each person's share of the pre-tax subtotal** — if you ate 20%
   of the food, you carry 20% of the service charge and 20% of the
   tax. The person who only had a Coke pays tax on a Coke.
3. Splitting money into fractions of a paisa means naive per-person
   totals won't sum exactly to the bill total. A largest-remainder
   pass fixes this so the sum of everyone's share is **exactly** the
   reconciled bill total — nobody's rounding error just evaporates.
4. Before any of this runs, `BillExtraction.reconcile()` recomputes
   the subtotal and total from the line items and compares them to
   what's printed. If they don't match beyond a ₹1 tolerance, the
   bill is flagged and **the computed total is used for splitting,
   not the printed one** — this is what catches the bill that's
   genuinely wrong.

All of this is covered by `tests/test_splitting.py`, which encodes
the exact scenarios from the brief (7 people / shared biryani / solo
Coke, someone leaving before dessert, a custom-ratio shared dish, and
the wrong-total bill) as deterministic unit tests — no photos or API
calls needed to verify the arithmetic is correct.

## Running it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
pytest tests/ -v                       # verify the algorithm, no photos needed
streamlit run app/streamlit_app.py     # the actual demo
```

## The 12-bill test set

I can't take physical photographs from inside this environment, so I
built the collection protocol and scoring tooling instead of faking
photos:

- **`docs/TEST_SET_METHODOLOGY.md`** — exactly which 12 conditions to
  capture (dim light, crumpled paper, steep angle, faded thermal,
  handwriting, mixed scripts, two-photo long bill, wrong printed
  total, discount line, CGST/SGST-only bill, shared+solo item mix)
  and how to hand-label ground truth for each.
- **`sample_data/ground_truth/bill_09_wrong_total.ground.json`** — a
  worked example of the ground-truth format for the "printed total is
  wrong" bill (₹620 food + ₹31 service + ₹31 tax = ₹682 actual, but
  the bill prints ₹700).
- **`app/compute_accuracy.py`** — once your 12 bills are labeled, this
  reports per-field and line-item accuracy, and checks whether the
  model's confidence scores actually predict where it's wrong (a
  confidence score that doesn't correlate with errors isn't earning
  its place on the review screen).

## Beyond the brief

A few things I added because they came up naturally while building this:

- **Automatic discrepancy detection**, not manual — the tool
  recomputes and compares rather than trusting the printed total, and
  tells you by how much and why before you split anything.
- **Multi-photo stitching** — the extraction prompt is told two images
  may be the same bill continued, and to merge/dedupe rather than
  double-count, rather than treating "long bill" as an unhandled edge
  case.
- **Custom split ratios**, not just equal shares — for the sharing
  platter where one person had two portions and the other had one.
- **Exact rounding reconciliation** (largest-remainder method) so
  per-person shares always sum to the true total to the paisa.
- **Copy-paste payout summary** — a one-line-per-person message ready
  to paste into the group chat, instead of a table people have to
  transcribe by hand.
- **Confidence-driven review UI** — fields are colour-flagged by
  confidence (🔴 below 0.7, 🟡 below 0.9) so a human's attention goes
  where the model was actually unsure, not spent re-checking every
  field uniformly.
- **`left_early` on `Person`** is informational, not enforced by
  magic — leaving early is just "don't include them in later item
  assignments," which the assignment step already supports naturally
  rather than needing special-case logic.

## What I'd do next with more time

- Fuzzy line-item matching in `compute_accuracy.py` (currently
  position-based, which is fine for a 12-bill eval set but wouldn't
  scale if OCR ever drops or duplicates a row).
- A confidence-calibration pass: log every human correction made on
  the review screen, and periodically check whether the model's
  stated confidence actually predicts correction rate — recalibrate
  the review screen's thresholds against real data instead of the
  0.7/0.9 constants used now.
- UPI deep-link generation per person for the payout summary, since
  "who owes what" usually ends with someone actually paying.
