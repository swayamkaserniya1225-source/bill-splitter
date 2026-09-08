# Test set: 12 real bills

This tool can't collect physical photographs on its own — that part is
inherently manual (a camera, real restaurant bills). This doc is the
protocol for doing it properly, plus the tooling to score it once done.

## What to collect

At minimum one bill each of:

| # | Condition | Why it matters |
|---|---|---|
| 1 | Normal, good lighting | Baseline — should be ~100% accurate |
| 2 | Dim light | Tests low-light digit recognition |
| 3 | Crumpled paper | Tests robustness to distorted/broken line geometry |
| 4 | Steep camera angle | Tests perspective distortion handling |
| 5 | Faded thermal print | Common failure mode — thermal paper fades in weeks |
| 6 | Handwritten additions/corrections | Mixed print + handwriting recognition |
| 7 | Two scripts (e.g. Hindi + English item names) | Non-Latin script transcription, not translation |
| 8 | Long bill needing 2 photographs | Multi-image stitching without double-counting |
| 9 | **Printed total is wrong** | Verifies the tool doesn't blindly trust the total — flip a digit, mis-add manually, whatever actually produces a real wrong bill |
| 10 | Bill with a discount line | Discount must be distributed proportionally too |
| 11 | Bill with only CGST/SGST breakdown (no combined "GST" line) | Common on Indian GST bills |
| 12 | Bill with a shared item + a solo item (e.g. the biryani/Coke case) | End-to-end realistic scenario |

Feel free to combine conditions in one bill (e.g. a faded thermal bill
in two scripts) — 12 photographs covering all conditions at least once
is the bar, not 12 independent single-condition bills.

## Labeling ground truth

For each bill:
1. Save the photo(s) as `data/photos/<bill_id>_1.jpg` (`_2.jpg` if two
   photos were needed).
2. Manually type out the *actual* bill content into a ground-truth
   `BillExtraction` JSON, saved as `<bill_id>.ground.json`. Use
   confidence `1.0` for everything in the ground truth (a human wrote
   it while looking at the paper — trust it).
3. Run `extract_bill()` on the same photo(s), save the result as
   `<bill_id>.json` next to it.
4. For bill #9 (wrong printed total), the ground truth's
   `printed_total` should be the number that's *actually printed*,
   even though it's arithmetically wrong — ground truth records what's
   on the paper. The tool's own `total_mismatch` flag is what proves
   it caught the error, not the ground truth file.

## Scoring

```
python -m app.compute_accuracy sample_data/ground_truth/
```

This reports, per bill and in aggregate: line-item name accuracy,
line-item price accuracy, and per-field correctness for
subtotal/discount/service/tax/total. It also surfaces how many items
the model flagged as low-confidence, so you can sanity-check whether
confidence scores are actually predictive of errors (they should
correlate — if a "confident" field is wrong, that's a bigger problem
than a low-confidence field being wrong, because the review screen
won't have flagged it for a human to check).
