"""
Photo(s) in, BillExtraction out.

Handles the hard cases from the spec by putting them explicitly in the
prompt rather than hoping the model figures it out:
  - dim light / crumpled paper / steep angle / faded thermal print
  - handwriting
  - two scripts on one bill (e.g. Hindi + English)
  - a bill that needed two photographs (multi-image call, told to
    stitch/dedupe rather than double-count)

The model is asked to self-report a confidence per field. Confidence
is a *model* signal, not ground truth — it's there so the review
screen knows what to point a human at, not to replace the human.
"""
from __future__ import annotations

import base64
import json
import os
from typing import List

from anthropic import Anthropic

from app.models import BillExtraction

_SYSTEM_PROMPT = """You are a meticulous restaurant-bill transcriber. You will be shown one \
or more photographs of the SAME physical bill (multiple photos are used when a long bill \
did not fit in one frame, or as different angles of the same short bill).

Read every line item exactly as printed, including quantity and price. Common failure modes \
to watch for and actively guard against:
- Dim/low light, glare, or a steep camera angle distorting digits (is that a 3 or an 8?).
- Faded thermal-printer paper where some characters are nearly invisible.
- Handwritten additions or corrections on top of the printed bill.
- Two scripts on one bill (e.g. item names in Hindi/Devanagari alongside English) — transcribe \
  the name as printed, in its original script, do not translate.
- If multiple photos are provided, they may show overlapping regions of a long bill. Merge \
  them into ONE list of line items with no duplicates — use position/order and item text to \
  tell whether two rows across photos are the same physical line or different ones.

For every field you extract, also output a confidence score from 0.0 to 1.0 reflecting how \
sure you are of the *exact* value (not just that something is there). Use lower confidence \
liberally when print quality, occlusion, or ambiguity make the reading uncertain — this \
confidence directly controls what a human reviewer is shown before any money changes hands, \
so err toward honesty over false certainty.

Respond with ONLY a single JSON object (no markdown fences, no commentary) matching exactly \
this shape:

{
  "restaurant_name": string or null,
  "bill_number": string or null,
  "date": string or null,
  "line_items": [
    {
      "id": "item_1",
      "name": string,
      "quantity": number,
      "unit_price": number,
      "line_total": number,
      "name_confidence": number,
      "price_confidence": number,
      "qty_confidence": number,
      "source_photo_index": integer (0-based index of which photo you read this line from)
    }
  ],
  "subtotal": number or null,
  "subtotal_confidence": number,
  "discount": number,
  "discount_confidence": number,
  "service_charge": number,
  "service_charge_confidence": number,
  "cgst": number,
  "sgst": number,
  "other_tax": number,
  "tax_confidence": number,
  "printed_total": number or null,
  "printed_total_confidence": number
}

If a field genuinely doesn't appear on the bill (e.g. no service charge line), use 0 for \
amounts and 1.0 confidence for "confidently absent", not a guess."""


def _media_type_for(path: str) -> str:
    ext = path.lower().rsplit(".", 1)[-1]
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp"}.get(ext, "image/jpeg")


def extract_bill(image_paths: List[str], model: str = "claude-sonnet-4-6") -> BillExtraction:
    """
    image_paths: one path for a normal bill, two (or more) for a bill
    that needed multiple photographs to capture in full.
    """
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    content = []
    for path in image_paths:
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": _media_type_for(path), "data": data},
        })
    content.append({
        "type": "text",
        "text": f"Transcribe this bill ({len(image_paths)} photo(s) of the same bill provided above).",
    })

    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    raw_text = "".join(block.text for block in resp.content if block.type == "text").strip()
    raw_text = raw_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {e}\nRaw output:\n{raw_text}") from e

    extraction = BillExtraction(**parsed)
    return extraction.reconcile()
