from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

from amlagent.models import Alert

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CUSTOMER_COLS = [
    "customer_id",
    "kind",
    "name",
    "dob",
    "nationality",
    "country",
    "city",
    "address",
    "email",
    "phone",
    "occupation",
    "risk_rating",
    "pep",
    "expected_monthly_turnover",
    "onboarded",
    "kyc_notes",
]
CORE = {
    "customers": (
        CUSTOMER_COLS,
        [
            [
                "C0001",
                "retail",
                "Anna Becker",
                "1990-05-17",
                "DE",
                "DE",
                "Hamburg",
                "Lindenweg 12",
                "anna.becker@example.com",
                "+00 555 0101 234",
                "nurse",
                "low",
                0,
                3000,
                "2019-03-01",
                "Customer told the branch about the sale of a vehicle to Jonas Wolff.",
            ],
            [
                "C0002",
                "business",
                "Becker Handels GmbH",
                "",
                "DE",
                "DE",
                "Hamburg",
                "Hafenstrasse 3",
                "office@becker-handel.example",
                "+00 555 0102 345",
                "wholesale",
                "high",
                0,
                40000,
                "2016-01-01",
                "Seasonal business.",
            ],
            [
                "C0003",
                "retail",
                "Lea Hartmann",
                "1984-11-02",
                "DE",
                "DE",
                "Kiel",
                "Birkenallee 7",
                "lea.hartmann@example.com",
                "+00 555 0103 456",
                "engineer",
                "low",
                0,
                0,
                "2021-06-01",
                "",
            ],
        ],
    ),
    "accounts": (
        ["account_id", "customer_id", "iban", "opened"],
        [
            ["A0001", "C0001", "DE44HARB1234567890", "2019-03-01"],
            ["A0002", "C0002", "DE55HARB2222222222", "2016-01-01"],
            ["A0003", "C0003", "DE77HARB4444444444", "2021-06-01"],
        ],
    ),
    "counterparties": (
        [
            "counterparty_id",
            "name",
            "kind",
            "country",
            "iban",
            "customer_id",
            "sensitive_category",
            "business_type",
            "dob",
            "first_seen",
        ],
        [
            [
                "P0001",
                "Jonas Wolff",
                "person",
                "DE",
                "DE66HARB3333333333",
                "",
                "",
                "private individual",
                "1985-02-02",
                "2026-04-01",
            ],
            [
                "P0002",
                "Praxis Sonnental",
                "org",
                "DE",
                "DE88HARB5555555555",
                "",
                "health",
                "medical practice",
                "",
                "2026-04-01",
            ],
            [
                "P0003",
                "Orvane Trading Ltd",
                "org",
                "VD",
                "VD12HARB6666666666",
                "",
                "",
                "trading company",
                "",
                "2026-06-10",
            ],
            [
                "P0004",
                "Becker Handels GmbH",
                "org",
                "DE",
                "DE55HARB2222222222",
                "C0002",
                "",
                "wholesale",
                "",
                "2026-04-01",
            ],
        ],
    ),
    "transactions": (
        [
            "txn_id",
            "account_id",
            "booked_at",
            "direction",
            "amount",
            "channel",
            "counterparty_id",
            "description",
        ],
        [
            ["T000001", "A0001", "2026-06-01T09:00", "in", 9500, "cash", "", "Cash deposit"],
            ["T000002", "A0001", "2026-06-02T09:00", "in", 9200, "cash", "", "Cash deposit"],
            ["T000003", "A0001", "2026-06-03T09:00", "in", 9900, "cash", "", "Cash deposit"],
            [
                "T000004",
                "A0001",
                "2026-06-03T15:00",
                "out",
                20000,
                "transfer",
                "P0001",
                "Loan repayment Jonas Wolff IBAN DE66 HARB 3333 3333 33",
            ],
            [
                "T000005",
                "A0001",
                "2026-06-10T10:00",
                "out",
                480,
                "transfer",
                "P0002",
                "Treatment invoice 5512",
            ],
            [
                "T000006",
                "A0001",
                "2026-06-12T10:00",
                "in",
                300,
                "transfer",
                "P0001",
                "Gift from Aunt Marta Vesely, mail marta@vesely.example",
            ],
            [
                "T000007",
                "A0001",
                "2026-06-27T09:00",
                "in",
                2700,
                "transfer",
                "P0004",
                "Salary June",
            ],
            [
                "T000008",
                "A0002",
                "2026-06-15T09:00",
                "out",
                30000,
                "transfer",
                "P0003",
                "Consulting services",
            ],
            [
                "T000009",
                "A0003",
                "2026-06-27T09:00",
                "in",
                9000,
                "transfer",
                "P0004",
                "Salary June incl. annual bonus",
            ],
            ["T000010", "A0003", "2026-06-28T12:00", "out", 60, "card", "", "Card payment"],
        ],
    ),
    "watchlist": (
        ["entry_id", "name", "aliases", "kind", "dob", "country", "list"],
        [
            [
                "W001",
                "Jonas Wolf",
                "J. Wolff;Jonas Wolff",
                "person",
                "1985-02-02",
                "DE",
                "sanctions",
            ],
            ["W002", "Dmitar Kovalenko", "", "person", "1970-01-01", "VD", "pep"],
        ],
    ),
    "media": (
        ["media_id", "subject_name", "published", "headline", "snippet"],
        [
            [
                "M001",
                "Orvane Trading Ltd",
                "2026-05-02",
                "Orvane Trading Ltd named in invoice fraud inquiry",
                "Prosecutors are examining payments routed through Orvane Trading Ltd.",
            ],
        ],
    ),
    "prior_alerts": (
        ["prior_id", "customer_id", "raised", "rule", "outcome", "note"],
        [
            [
                "H001",
                "C0001",
                "2026-01-15",
                "STRUCTURING",
                "escalated",
                "Cash deposits under threshold; referred.",
            ],
            [
                "H002",
                "C0002",
                "2025-08-01",
                "CASH_FREQUENCY",
                "closed_false_positive",
                "Seasonal cash pattern confirmed with Anna Becker.",
            ],
        ],
    ),
    "links": (
        ["customer_id", "related_customer_id", "link_type"],
        [
            ["C0001", "C0002", "shared_director"],
        ],
    ),
}


@pytest.fixture(scope="session")
def core_dir(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("core")
    for table, (cols, rows) in CORE.items():
        with (path / f"{table}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(cols)
            writer.writerows(rows)
    return path


@pytest.fixture(scope="session")
def db(core_dir):
    from amlagent.bankdb import BankDB

    return BankDB(core_dir)


@pytest.fixture
def alert_flagged() -> Alert:
    """Structuring-like alert on C0001: watchlist candidate and a prior escalation."""
    return Alert(
        alert_id="AL-900",
        customer_id="C0001",
        account_id="A0001",
        rule="STRUCTURING",
        raised=date(2026, 6, 30),
        as_of=date(2026, 6, 30),
        window_start=date(2026, 6, 1),
        window_end=date(2026, 6, 3),
        amount=28600.0,
        txn_ids=["T000001", "T000002", "T000003"],
        counterparty_ids=["P0001"],
    )


@pytest.fixture
def alert_clean() -> Alert:
    """Bonus alert on C0003: no hard flag."""
    return Alert(
        alert_id="AL-901",
        customer_id="C0003",
        account_id="A0003",
        rule="PROFILE_DEVIATION",
        raised=date(2026, 6, 28),
        as_of=date(2026, 6, 28),
        window_start=date(2026, 6, 27),
        window_end=date(2026, 6, 27),
        amount=9000.0,
        txn_ids=["T000009"],
        counterparty_ids=["P0004"],
    )


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def tool_use(name: str, tool_input: dict, id: str = "tu_1") -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": tool_input}


def reply(*blocks, stop: str = "tool_use", model: str = "claude-haiku-5-5", tokens=(1000, 200)):
    from amlagent.llm import StepResponse, Usage

    usage = Usage(input_tokens=tokens[0], output_tokens=tokens[1])
    return StepResponse(
        content=list(blocks), stop_reason=stop, model=model, usage=usage, latency_s=0.5
    )
