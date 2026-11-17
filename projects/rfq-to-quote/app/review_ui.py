"""Human review queue.

    uv run rfq2quote run data/rfqs --mode baseline --as-of received   # fill the queue
    uv run streamlit run app/review_ui.py

A reviewer sees the source document next to the draft quote and the reasons it
was held, fixes the header, SKUs or quantities, and approves or rejects. Every
edit is re-priced and re-checked by the guardrails before it can be approved, the
decision is logged with the reviewer's name, and a correction is saved as an eval
case.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from rfq2quote.guardrails import Policy
from rfq2quote.intake import load_document
from rfq2quote.models import LineItem, MatchedLine, PipelineResult
from rfq2quote.pipeline import load_catalog, price_and_route
from rfq2quote.store import ReviewStore, save_correction

st.set_page_config(page_title="RFQ review queue", layout="wide")
HEADER = {
    "customer_name": "Customer",
    "customer_email": "Send quote to",
    "delivery_date": "Delivery date (YYYY-MM-DD)",
    "delivery_location": "Delivery location",
}


@st.cache_resource
def get_catalog():
    return load_catalog()


def lines_frame(result: PipelineResult) -> pd.DataFrame:
    rows = [
        {
            "description": m.item.description,
            "quantity": m.item.quantity,
            "unit": m.item.unit,
            "sku": m.sku,
            "confidence": m.confidence,
        }
        for m in result.matches
    ]
    return pd.DataFrame(rows, columns=["description", "quantity", "unit", "sku", "confidence"])


def apply_edits(
    result: PipelineResult, edited: pd.DataFrame, header: dict, resolved: bool, catalog
) -> PipelineResult:
    """Rebuild the result from the reviewer's inputs, then re-price and re-check it."""
    matches = []
    for row in edited.to_dict("records"):
        if not str(row.get("description") or "").strip():
            continue
        quantity = None if pd.isna(row["quantity"]) else float(row["quantity"])
        sku = None if pd.isna(row["sku"]) or not str(row["sku"]).strip() else str(row["sku"])
        unit = None if pd.isna(row["unit"]) or not row["unit"] else row["unit"]
        item = LineItem(description=str(row["description"]), quantity=quantity, unit=unit)
        matches.append(
            MatchedLine(
                item=item,
                sku=sku,
                confidence="high" if sku else "low",
                reason="Confirmed by reviewer." if sku else "No SKU assigned by reviewer.",
            )
        )
    rfq = result.rfq.model_copy(
        update={
            **{key: (value.strip() or None) for key, value in header.items()},
            "line_items": [m.item for m in matches],
            "missing_info": [] if resolved else result.rfq.missing_info,
        }
    )
    return price_and_route(
        catalog,
        Policy(**{**result.provenance.policy, "assisted": False})
        if result.provenance.policy
        else Policy(),
        result.rfq_id,
        result.source,
        rfq,
        matches,
        date.fromisoformat(result.as_of),
        result.usage,
        result.facts,
        result.provenance,
    )


def _snapshot(result: PipelineResult) -> tuple:
    header = tuple(getattr(result.rfq, key) for key in HEADER)
    return header, [(m.sku, m.item.quantity, m.item.unit) for m in result.matches]


def main() -> None:
    catalog = get_catalog()
    store = ReviewStore()
    counts = store.counts()

    st.title("RFQ review queue")
    statuses = ["pending", "auto_approved", "approved", "corrected", "rejected"]
    for col, status in zip(st.columns(5), statuses, strict=True):
        col.metric(status.replace("_", " ").capitalize(), counts.get(status, 0))

    reviewer = st.sidebar.text_input("Reviewer", value="reviewer")
    pending = store.list("pending")
    if not pending:
        st.info("Nothing waiting for review. Run `rfq2quote run data/rfqs` to process RFQs.")
        return

    chosen = st.sidebar.radio("Waiting for review", [rfq_id for rfq_id, _, _ in pending])
    result = next(r for rfq_id, _, r in pending if rfq_id == chosen)

    left, right = st.columns(2)
    with left:
        st.subheader("Source document")
        source = Path(result.source)
        if source.exists():
            st.code(load_document(source).text, language=None)
        else:
            st.warning(f"Source file not found: {result.source}")
    with right:
        st.subheader("Why this was held")
        for flag in result.flags:
            (st.error if flag.severity == "review" else st.info)(f"**{flag.code}** {flag.detail}")
        st.subheader("Request (editable)")
        header = {
            key: st.text_input(label, value=getattr(result.rfq, key) or "", key=f"{key}-{chosen}")
            for key, label in HEADER.items()
        }
        resolved = False
        if result.rfq.missing_info:
            resolved = st.checkbox("Open points are resolved", key=f"resolved-{chosen}")

    st.subheader("Line items (editable)")
    edited = st.data_editor(
        lines_frame(result),
        num_rows="dynamic",
        width="stretch",
        key=f"lines-{chosen}",
        column_config={
            "unit": st.column_config.SelectboxColumn(options=["m", "pcs"]),
            "sku": st.column_config.TextColumn(help="Catalog SKU id. Leave empty if none fits."),
            "confidence": st.column_config.TextColumn(disabled=True),
        },
    )
    final = apply_edits(result, edited, header, resolved, catalog)
    changed = _snapshot(final) != _snapshot(result)

    st.subheader(f"Quote after your edits: EUR {final.quote.total:,.2f}")
    if final.quote.lines:
        st.dataframe(
            pd.DataFrame([line.model_dump() for line in final.quote.lines]), width="stretch"
        )
    remaining = sorted({f.code for f in final.flags if f.severity == "review"})
    if remaining:
        st.caption("Still flagged (approving overrides these): " + ", ".join(remaining))
    unknown = [m.sku for m in final.matches if m.sku and catalog.get(m.sku) is None]
    if unknown:
        st.error(f"Not in the catalog: {', '.join(unknown)}")

    with st.expander("Find a SKU"):
        query = st.text_input("Product text", key=f"search-{chosen}")
        if query:
            hits = [
                {"sku": s.sku, "description": s.description, "unit": s.unit, "price": s.list_price}
                for s, _ in catalog.search(query, 8)
            ]
            st.dataframe(pd.DataFrame(hits), width="stretch")

    note = st.text_input("Reviewer note", key=f"note-{chosen}")
    approve, reject = st.columns(2)
    label = "Approve with corrections" if changed else "Approve"
    if approve.button(label, type="primary", disabled=bool(unknown) or not reviewer.strip()):
        store.decide(chosen, "corrected" if changed else "approved", reviewer, note, final)
        if changed:
            path = save_correction(final)
            st.toast(f"Correction saved as eval case: {path.name}")
        st.rerun()
    if reject.button("Reject", disabled=not reviewer.strip()):
        store.decide(chosen, "rejected", reviewer, note)
        st.rerun()


main()
