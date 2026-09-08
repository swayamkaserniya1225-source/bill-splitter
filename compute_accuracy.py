"""
Once you've photographed and hand-labeled your 12 test bills (see
docs/TEST_SET_METHODOLOGY.md), run this to score extraction accuracy
per bill and overall — and to check whether low-confidence fields are
actually the ones getting misread (i.e. is the model's confidence
score trustworthy, or just noise?).

Usage:
    python -m app.compute_accuracy sample_data/ground_truth/

Expects, per bill, a pair of files:
    <bill_id>.json        <- output of extract_bill(), saved to disk
    <bill_id>.ground.json <- hand-labeled ground truth, same shape
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

from app.models import BillExtraction


def _close(a, b, tol=Decimal("0.01")) -> bool:
    if a is None or b is None:
        return a == b
    return abs(Decimal(str(a)) - Decimal(str(b))) <= tol


def score_bill(pred: BillExtraction, truth: BillExtraction) -> dict:
    field_results = {}

    # scalar fields
    for field in ["subtotal", "discount", "service_charge", "cgst", "sgst", "printed_total"]:
        p, t = getattr(pred, field), getattr(truth, field)
        field_results[field] = _close(p, t)

    # line items — match by position (bills are read top to bottom;
    # for a real system you'd want fuzzy name matching, this is enough
    # for a 12-bill eval set)
    n = max(len(pred.line_items), len(truth.line_items))
    item_hits, price_hits = 0, 0
    for i in range(n):
        if i >= len(pred.line_items) or i >= len(truth.line_items):
            continue
        p_item, t_item = pred.line_items[i], truth.line_items[i]
        if p_item.name.strip().lower() == t_item.name.strip().lower():
            item_hits += 1
        if _close(p_item.line_total, t_item.line_total):
            price_hits += 1

    field_results["line_item_name_accuracy"] = item_hits / n if n else 1.0
    field_results["line_item_price_accuracy"] = price_hits / n if n else 1.0
    field_results["line_item_count_match"] = len(pred.line_items) == len(truth.line_items)

    # is low confidence actually predictive of errors? (per-bill signal;
    # aggregate across all bills for a meaningful correlation)
    low_conf_items = [li for li in pred.line_items if li.lowest_confidence < 0.7]
    field_results["_low_confidence_item_count"] = len(low_conf_items)

    return field_results


def main(ground_truth_dir: str):
    gt_dir = Path(ground_truth_dir)
    truth_files = sorted(gt_dir.glob("*.ground.json"))
    if not truth_files:
        print(f"No *.ground.json files found in {gt_dir}")
        return

    all_results = {}
    for truth_path in truth_files:
        bill_id = truth_path.name.replace(".ground.json", "")
        pred_path = gt_dir / f"{bill_id}.json"
        if not pred_path.exists():
            print(f"[skip] {bill_id}: no prediction file found, run extract_bill() and save it first")
            continue
        pred = BillExtraction(**json.loads(pred_path.read_text()))
        truth = BillExtraction(**json.loads(truth_path.read_text()))
        all_results[bill_id] = score_bill(pred, truth)

    print(f"\nScored {len(all_results)} bills\n" + "=" * 40)
    for bill_id, res in all_results.items():
        print(f"\n{bill_id}:")
        for k, v in res.items():
            print(f"  {k}: {v}")

    # rollup
    scalar_fields = ["subtotal", "discount", "service_charge", "cgst", "sgst", "printed_total"]
    print("\n" + "=" * 40 + "\nOVERALL")
    for field in scalar_fields:
        hits = sum(1 for r in all_results.values() if r[field])
        print(f"  {field}: {hits}/{len(all_results)} correct")
    avg_name_acc = sum(r["line_item_name_accuracy"] for r in all_results.values()) / len(all_results)
    avg_price_acc = sum(r["line_item_price_accuracy"] for r in all_results.values()) / len(all_results)
    print(f"  avg line-item name accuracy: {avg_name_acc:.1%}")
    print(f"  avg line-item price accuracy: {avg_price_acc:.1%}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample_data/ground_truth")
