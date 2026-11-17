from datetime import date

from rfq2quote.guardrails import Policy, evaluate, route_for
from rfq2quote.models import RFQ, LineItem, MatchedLine
from rfq2quote.quote import build_quote, discount_for

AS_OF = date(2026, 9, 1)


def rfq(items, delivery="2026-12-31", email="buyer@customer.example"):
    return RFQ(customer_name="Customer GmbH", customer_email=email, delivery_date=delivery,
               delivery_location="Berlin", line_items=items, missing_info=[])  # fmt: skip


def line(sku, quantity, unit, confidence="high"):
    item = LineItem(description=f"request for {sku}", quantity=quantity, unit=unit)
    return MatchedLine(item=item, sku=sku, confidence=confidence, reason="test")


def check(catalog, matches, **rfq_kw):
    request = rfq([m.item for m in matches], **rfq_kw)
    quote = build_quote(matches, catalog)
    flags = evaluate(request, matches, quote, catalog, AS_OF, Policy())
    return quote, {f.code for f in flags if f.severity == "review"}, route_for(flags)


def test_discount_tiers():
    assert [discount_for(v) for v in (4_999, 5_000, 20_000, 50_000)] == [0, 0.02, 0.04, 0.06]


def test_clean_small_order_is_auto_approved(catalog):
    quote, codes, route = check(catalog, [line("CAB-NYY-5X2.5", 300, "m")])
    sku = catalog.get("CAB-NYY-5X2.5")
    assert quote.total == round(300 * sku.list_price, 2)
    assert codes == set() and route == "auto_approve"


def test_volume_discount_on_thin_margin_goods_needs_sign_off(catalog):
    quote, codes, route = check(catalog, [line("TRF-OIL-630-20", 2, "pcs")])
    assert quote.lines[0].discount_pct == 4
    assert quote.lines[0].margin_pct < 12
    assert "MARGIN_BELOW_FLOOR" in codes and route == "human_review"


def test_high_order_value(catalog):
    _, codes, _ = check(catalog, [line("TRF-OIL-2500-20", 2, "pcs")])
    assert "HIGH_ORDER_VALUE" in codes


def test_unmatched_and_missing_quantity_are_not_priced(catalog):
    quote, codes, route = check(
        catalog, [line(None, 50, "pcs", "low"), line("CAB-NYM-3X1.5", None, None)]
    )
    assert quote.lines == []
    assert {"UNMATCHED_ITEM", "MISSING_QUANTITY"} <= codes and route == "human_review"


def test_unit_mismatch_and_moq(catalog):
    _, codes, _ = check(catalog, [line("CAB-NYY-4X95", 2, "pcs")])
    assert {"UNIT_MISMATCH", "BELOW_MOQ"} <= codes


def test_low_confidence_match_is_reviewed(catalog):
    _, codes, _ = check(catalog, [line("CAB-NYY-5X2.5", 300, "m", "medium")])
    assert codes == {"LOW_MATCH_CONFIDENCE"}


def test_lead_time_risk(catalog):
    _, codes, _ = check(catalog, [line("TRF-OIL-630-10", 1, "pcs")], delivery="2026-10-01")
    assert "LEAD_TIME_RISK" in codes


def test_missing_email_blocks_but_missing_date_does_not(catalog):
    matches = [line("CAB-NYY-5X2.5", 300, "m")]
    _, codes, _ = check(catalog, matches, email=None)
    assert codes == {"MISSING_CUSTOMER_EMAIL"}
    _, codes, route = check(catalog, matches, delivery=None)
    assert codes == set() and route == "auto_approve"


def test_no_line_items(catalog):
    _, codes, route = check(catalog, [])
    assert "NO_LINE_ITEMS" in codes and route == "human_review"


# --- rules added after the independent review ---------------------------------

from rfq2quote.models import SourceFacts  # noqa: E402

GOOD = [line("CAB-NYY-5X2.5", 300, "m")]


def codes_for(catalog, matches=None, facts=None, policy=None, **rfq_kw):
    matches = matches or GOOD
    request = rfq([m.item for m in matches], **rfq_kw)
    quote = build_quote(matches, catalog)
    flags = evaluate(request, matches, quote, catalog, AS_OF, policy or Policy(), facts)
    return {f.code for f in flags if f.severity == "review"}


def test_quantity_without_a_unit_is_held(catalog):
    assert codes_for(catalog, [line("CAB-NYY-4X95", 150, None)]) == {"UNKNOWN_UNIT"}


def test_missing_customer_name_is_held(catalog):
    request = rfq([m.item for m in GOOD]).model_copy(update={"customer_name": None})
    flags = evaluate(request, GOOD, build_quote(GOOD, catalog), catalog, AS_OF)
    assert "MISSING_CUSTOMER_NAME" in {f.code for f in flags}


def test_open_question_from_the_extractor_is_held(catalog):
    request = rfq([m.item for m in GOOD]).model_copy(
        update={"missing_info": ["150 drums or 150 m?"]}
    )
    flags = evaluate(request, GOOD, build_quote(GOOD, catalog), catalog, AS_OF)
    assert [f.detail for f in flags if f.code == "OPEN_QUESTION"] == ["150 drums or 150 m?"]


def test_quote_is_never_addressed_to_a_colleague(catalog):
    assert codes_for(catalog, email="jens.maurer@helios-supply.example") == {"INTERNAL_RECIPIENT"}


def test_recipient_must_appear_in_the_source(catalog):
    facts = SourceFacts(sender_email=None, emails_found=["someone@else.example"])
    assert codes_for(catalog, facts=facts) == {"EMAIL_NOT_IN_SOURCE"}


def test_recipient_must_be_the_external_sender(catalog):
    # An address planted in the mail body (prompt injection) differs from the sender.
    facts = SourceFacts(
        sender_email="orders@real-customer.example",
        emails_found=["orders@real-customer.example", "buyer@customer.example"],
    )
    assert codes_for(catalog, facts=facts) == {"RECIPIENT_NOT_SENDER"}


def test_forwarded_mail_from_a_colleague_may_name_an_external_customer(catalog):
    facts = SourceFacts(
        sender_email="jens.maurer@helios-supply.example", emails_found=["buyer@customer.example"]
    )
    assert codes_for(catalog, facts=facts) == set()


def test_unreadable_attachment_is_held(catalog):
    facts = SourceFacts(emails_found=["buyer@customer.example"], unread_attachments=["scan.png"])
    assert codes_for(catalog, facts=facts) == {"UNREAD_ATTACHMENT"}


def test_assisted_mode_holds_an_otherwise_clean_quote(catalog):
    assert codes_for(catalog, policy=Policy(assisted=True)) == {"ASSISTED_MODE"}


def described(description, sku, quantity=300, unit="m"):
    """A line as the customer worded it, matched to a SKU with high confidence."""
    item = LineItem(description=description, quantity=quantity, unit=unit)
    return MatchedLine(item=item, sku=sku, confidence="high", reason="test")


def test_requested_size_that_the_matched_product_does_not_have_is_held(catalog):
    # Found by repeating the evaluation: a size the catalog does not carry (4x16) was
    # matched to the neighbouring size (4x25) with high confidence and auto-approved.
    _, codes, route = check(catalog, [described("NYY-J 4x16 mm² power cable", "CAB-NYY-4X25")])
    assert codes == {"SIZE_MISMATCH"} and route == "human_review"


def test_size_written_differently_but_equal_is_not_held(catalog):
    for wording in ("NYY-J 5x2,5 mm²", "nyy j 5 X 2.5mm2", "NYY 5G2.5", "5×2.5 qmm Erdkabel"):
        _, codes, route = check(catalog, [described(wording, "CAB-NYY-5X2.5")])
        assert codes == set() and route == "auto_approve", wording


def test_request_without_a_size_is_not_held_for_size(catalog):
    _, codes, _ = check(catalog, [described("the usual underground power cable", "CAB-NYY-5X2.5")])
    assert "SIZE_MISMATCH" not in codes


def test_a_count_in_front_of_a_rating_is_not_read_as_a_size(catalog):
    # "2 x 630 kVA" is two transformers, not a cross-section.
    sku = next(s for s in ("TRF-OIL-630-20", "TRF-630KVA-20KV", "TRF-630-20") if catalog.get(s))
    line_ = described("2 x 630 kVA oil transformer 20 kV", sku, quantity=2, unit="pcs")
    _, codes, _ = check(catalog, [line_])
    assert "SIZE_MISMATCH" not in codes


def test_size_mismatch_names_both_sizes(catalog):
    matches = [described("NYY-J 4x16 mm²", "CAB-NYY-4X25")]
    flags = evaluate(rfq([m.item for m in matches]), matches, build_quote(matches, catalog), catalog, AS_OF, Policy())  # fmt: skip
    detail = next(f.detail for f in flags if f.code == "SIZE_MISMATCH")
    assert "4x16" in detail and "4x25" in detail
