from email.message import EmailMessage

import pytest

from rfq2quote.intake import load_document


def test_email_headers_and_body(data_dir):
    doc = load_document(data_dir / "rfqs" / "rfq-002.eml")
    assert doc.received_date == "2026-09-15"
    assert doc.sender_email == "einkauf@elektro-brandt.example"
    assert "300 m NYY-J 5x2,5 mm²" in doc.text
    assert "Grüßen" in doc.text  # umlauts survive
    assert len(doc.sha256) == 64


def test_spreadsheet_rows_become_pipe_separated_lines(data_dir):
    doc = load_document(data_dir / "rfqs" / "rfq-023.xlsx")
    assert "1 | NYY-J 5x16 mm² | 400 | m" in doc.text


def test_every_sheet_of_a_workbook_is_read(data_dir):
    doc = load_document(data_dir / "rfqs" / "rfq-035.xlsx")
    assert "[sheet: Deckblatt]" in doc.text and "[sheet: Positionen]" in doc.text


def test_envelope_file_supplies_the_received_date_for_attachments(data_dir):
    assert load_document(data_dir / "rfqs" / "rfq-023.xlsx").received_date == "2026-09-30"


def test_pdf_text(data_dir):
    doc = load_document(data_dir / "rfqs" / "rfq-028.pdf")
    assert "NA2XS(F)2Y 1x240 mm² 12/20 kV" in doc.text


def test_facts_list_the_addresses_in_the_document(data_dir):
    facts = load_document(data_dir / "rfqs" / "rfq-008.eml").facts()
    assert facts.sender_email == "jens.maurer@helios-supply.example"
    assert "r.oster@oster-anlagenbau.example" in facts.emails_found


def test_text_file_in_a_legacy_encoding_is_still_read(tmp_path):
    path = tmp_path / "note.txt"
    path.write_bytes("20 Stück Kabelverschraubung M25, Grüße".encode("cp1252"))
    assert "Stück" in load_document(path).text


def test_email_attachments_are_read_and_unreadable_ones_are_reported(data_dir, tmp_path):
    msg = EmailMessage()
    msg["From"] = "Buyer <buyer@customer.example>"
    msg["Subject"] = "RFQ, see attachments"
    msg.set_content("Please quote the attached list.")
    msg.add_attachment(
        (data_dir / "rfqs" / "rfq-028.pdf").read_bytes(),
        maintype="application", subtype="pdf", filename="tender.pdf",
    )  # fmt: skip
    msg.add_attachment(b"\x00\x01", maintype="image", subtype="png", filename="site-photo.png")
    path = tmp_path / "with-attachments.eml"
    path.write_bytes(msg.as_bytes())

    doc = load_document(path)
    assert "[attachment: tender.pdf]" in doc.text
    assert "NA2XS(F)2Y 1x240" in doc.text
    assert doc.unread_attachments == ["site-photo.png"]


def test_unsupported_type_is_rejected(tmp_path):
    path = tmp_path / "rfq.docx"
    path.write_text("x")
    with pytest.raises(ValueError, match="Unsupported file type"):
        load_document(path)
