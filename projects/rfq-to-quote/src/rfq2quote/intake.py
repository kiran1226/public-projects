"""Intake: turn an email, PDF, spreadsheet or text note into plain text. No LLM.

Emails are read together with their attachments. Anything that cannot be read is
reported in `unread_attachments`, and a guardrail holds the request for a person.
"""

from __future__ import annotations

import csv
import email
import email.policy
import hashlib
import io
import json
import re
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import SourceFacts

SUPPORTED = {".eml", ".txt", ".pdf", ".xlsx", ".csv"}
ENVELOPE_FILE = "envelope.json"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class Document(BaseModel):
    doc_id: str
    source: str
    kind: str
    text: str
    sha256: str = ""
    received_date: str | None = None
    sender_name: str | None = None
    sender_email: str | None = None
    unread_attachments: list[str] = Field(default_factory=list)

    def facts(self) -> SourceFacts:
        found = sorted({m.lower() for m in _EMAIL.findall(self.text)})
        return SourceFacts(
            sender_email=self.sender_email,
            emails_found=found,
            unread_attachments=self.unread_attachments,
        )


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _rows_to_text(rows) -> str:
    lines = []
    for row in rows:
        cells = ["" if c is None else str(c).strip() for c in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages).strip()


def _xlsx_text(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = [
        f"[sheet: {ws.title}]\n{_rows_to_text(ws.iter_rows(values_only=True))}"
        for ws in wb.worksheets
    ]
    wb.close()
    return "\n\n".join(parts).strip()


def _csv_text(data: bytes) -> str:
    return _rows_to_text(csv.reader(io.StringIO(_decode(data))))


_BODY_READERS = {
    ".pdf": _pdf_text,
    ".xlsx": _xlsx_text,
    ".csv": _csv_text,
    ".txt": lambda data: _decode(data).strip(),
}


def _load_eml(data: bytes) -> dict:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain",))
    name, addr = parseaddr(msg.get("From", ""))
    received = None
    if msg.get("Date"):
        try:
            received = parsedate_to_datetime(msg["Date"]).date().isoformat()
        except (TypeError, ValueError):
            received = None
    header = "\n".join(
        f"{key}: {msg[key]}" for key in ("From", "To", "Date", "Subject") if msg.get(key)
    )
    parts = [f"{header}\n\n{body.get_content() if body else ''}".strip()]
    unread = []
    for attachment in msg.iter_attachments():
        filename = attachment.get_filename() or "unnamed attachment"
        reader = _BODY_READERS.get(Path(filename).suffix.lower())
        try:
            if reader is None:
                raise ValueError("unsupported type")
            parts.append(f"[attachment: {filename}]\n{reader(attachment.get_content())}")
        except Exception:  # noqa: BLE001 - any unreadable attachment goes to a person
            unread.append(filename)
    return {
        "text": "\n\n".join(parts),
        "received_date": received,
        "sender_name": name or None,
        "sender_email": addr.lower() or None,
        "unread_attachments": unread,
    }


def _envelope(path: Path) -> dict:
    """Metadata for files that arrived as attachments: `envelope.json` next to the file.

    {"rfq-023.xlsx": {"received_date": "2026-09-30", "sender_email": null}}
    """
    sidecar = path.parent / ENVELOPE_FILE
    if not sidecar.exists():
        return {}
    return json.loads(sidecar.read_text(encoding="utf-8")).get(path.name, {})


def load_document(path: Path) -> Document:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"Unsupported file type {suffix!r}; expected one of {sorted(SUPPORTED)}")
    data = path.read_bytes()
    fields = _load_eml(data) if suffix == ".eml" else {"text": _BODY_READERS[suffix](data)}
    for key, value in _envelope(path).items():
        if value and not fields.get(key):
            fields[key] = value
    return Document(
        doc_id=path.stem,
        source=str(path),
        kind=suffix.lstrip("."),
        sha256=hashlib.sha256(data).hexdigest(),
        **fields,
    )
