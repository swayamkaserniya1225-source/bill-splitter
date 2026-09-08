"""
Run with:  streamlit run app/streamlit_app.py
Requires ANTHROPIC_API_KEY in the environment for the OCR step.

Flow: upload photo(s) -> extract -> REVIEW SCREEN (fix what the model
misread, confidence-highlighted, discrepancy banner) -> add people ->
assign each item -> computed, reconciled, per-person breakdown.
"""
from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.extraction import extract_bill
from app.models import BillExtraction, ItemAssignment, LineItem, Person
from app.splitting import payout_summary, split_bill

st.set_page_config(page_title="Bill Splitter", page_icon="🧾", layout="wide")
st.title("🧾 Bill Splitter")
st.caption("Photo in → who ate what → the correct number for each person out.")

if "extraction" not in st.session_state:
    st.session_state.extraction = None
if "people" not in st.session_state:
    st.session_state.people = []
if "assignments" not in st.session_state:
    st.session_state.assignments = {}  # item_id -> list of person_ids

# ---------- Step 1: upload + extract ----------
st.header("1. Upload the bill")
st.caption("Upload one photo, or several if the bill was too long for one frame — they'll be merged.")
files = st.file_uploader("Bill photo(s)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True)

col_a, col_b = st.columns([1, 3])
with col_a:
    extract_clicked = st.button("Extract bill", type="primary", disabled=not files)
with col_b:
    manual_clicked = st.button("Skip OCR — enter bill manually")

if extract_clicked and files:
    with st.spinner("Reading the bill..."):
        tmp_paths = []
        for f in files:
            suffix = os.path.splitext(f.name)[1] or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(f.read())
                tmp_paths.append(tmp.name)
        try:
            st.session_state.extraction = extract_bill(tmp_paths)
        except Exception as e:
            st.error(f"Extraction failed: {e}")

if manual_clicked:
    st.session_state.extraction = BillExtraction(line_items=[]).reconcile()

# ---------- Step 2: review screen ----------
if st.session_state.extraction is not None:
    ext = st.session_state.extraction
    st.header("2. Review & fix")
    st.caption("Low-confidence fields are flagged. Nothing below this point does arithmetic until you confirm.")

    if ext.total_mismatch:
        st.warning(
            f"⚠️ Printed total (₹{ext.printed_total}) doesn't match the line items + tax/service/discount "
            f"(₹{ext.computed_total}), a difference of ₹{ext.total_mismatch_amount}. "
            f"This bill may have a printing error — the computed total will be used for splitting "
            f"unless you correct the line items below."
        )

    def confidence_tag(c: float) -> str:
        if c >= 0.9:
            return ""
        if c >= 0.7:
            return " 🟡"
        return " 🔴 low confidence — check this"

    st.subheader("Line items")
    for i, li in enumerate(ext.line_items):
        c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
        li.name = c1.text_input(f"Item{confidence_tag(li.name_confidence)}", value=li.name, key=f"name_{i}")
        li.quantity = c2.number_input("Qty", value=float(li.quantity), key=f"qty_{i}", step=1.0)
        li.unit_price = Decimal(str(c3.number_input("Unit price", value=float(li.unit_price), key=f"price_{i}")))
        li.line_total = Decimal(str(c4.number_input("Line total", value=float(li.line_total), key=f"total_{i}")))

    with st.expander("Add a missed line item"):
        c1, c2, c3 = st.columns(3)
        new_name = c1.text_input("Name", key="new_item_name")
        new_qty = c2.number_input("Qty", value=1.0, key="new_item_qty")
        new_total = c3.number_input("Line total", value=0.0, key="new_item_total")
        if st.button("Add item") and new_name:
            ext.line_items.append(LineItem(
                id=f"item_{len(ext.line_items)+1}", name=new_name, quantity=new_qty,
                unit_price=Decimal(str(new_total / new_qty if new_qty else new_total)),
                line_total=Decimal(str(new_total)),
            ))
            st.rerun()

    st.subheader("Totals")
    c1, c2, c3, c4 = st.columns(4)
    subtotal_val = c1.number_input("Subtotal (printed)", value=float(ext.subtotal or 0))
    ext.subtotal = Decimal(str(subtotal_val)) if subtotal_val else None
    ext.discount = Decimal(str(c2.number_input("Discount", value=float(ext.discount))))
    ext.service_charge = Decimal(str(c3.number_input("Service charge", value=float(ext.service_charge))))
    ext.cgst = Decimal(str(c4.number_input("CGST", value=float(ext.cgst))))
    c5, c6 = st.columns(2)
    ext.sgst = Decimal(str(c5.number_input("SGST", value=float(ext.sgst))))
    printed_total_val = c6.number_input("Printed total", value=float(ext.printed_total or 0))
    ext.printed_total = Decimal(str(printed_total_val)) if printed_total_val else None

    ext = ext.reconcile()
    st.session_state.extraction = ext
    st.info(f"Computed subtotal: ₹{ext.computed_subtotal} · Computed total: ₹{ext.computed_total}")

    # ---------- Step 3: people ----------
    st.header("3. Who's at the table")
    new_person = st.text_input("Add a person by name", key="new_person_name")
    if st.button("Add person") and new_person:
        pid = f"p{len(st.session_state.people) + 1}"
        st.session_state.people.append(Person(id=pid, name=new_person))
        st.rerun()

    if st.session_state.people:
        st.write(", ".join(p.name for p in st.session_state.people))

    # ---------- Step 4: assignment ----------
    if st.session_state.people and ext.line_items:
        st.header("4. Who ate what")
        all_ids = [p.id for p in st.session_state.people]
        name_by_id = {p.id: p.name for p in st.session_state.people}
        for li in ext.line_items:
            default = st.session_state.assignments.get(li.id, all_ids)
            chosen = st.multiselect(
                f"{li.name}  (₹{li.line_total})",
                options=all_ids,
                default=default,
                format_func=lambda pid: name_by_id[pid],
                key=f"assign_{li.id}",
            )
            st.session_state.assignments[li.id] = chosen

        ready = all(st.session_state.assignments.get(li.id) for li in ext.line_items)

        if st.button("Compute split", type="primary", disabled=not ready):
            assignments = [
                ItemAssignment(item_id=iid, person_ids=pids)
                for iid, pids in st.session_state.assignments.items()
            ]
            try:
                result = split_bill(ext, st.session_state.people, assignments)
                st.header("5. The breakdown")
                if result.discrepancy_flagged:
                    st.warning(
                        f"Split against the computed total (₹{result.bill_computed_total}), "
                        f"not the printed total (₹{result.bill_printed_total}) — they didn't match."
                    )
                cols = st.columns(len(result.people))
                for col, b in zip(cols, result.people):
                    with col:
                        st.metric(b.name, f"₹{b.final_total}")
                        st.caption(
                            f"Food: ₹{b.item_subtotal}\n\n"
                            f"− Discount: ₹{b.share_of_discount}\n\n"
                            f"+ Service: ₹{b.share_of_service_charge}\n\n"
                            f"+ Tax: ₹{b.share_of_tax}"
                        )
                        with st.expander("Items"):
                            for ln in b.lines:
                                st.write(f"- {ln.item_name}: ₹{ln.share_amount}")

                st.divider()
                st.write(f"Sum of shares: ₹{result.sum_of_shares}  ·  Bill total: ₹{result.bill_computed_total}")
                st.subheader("Copy-paste summary")
                st.code("\n".join(payout_summary(result)))
            except ValueError as e:
                st.error(str(e))
