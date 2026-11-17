"""Records in the shape the feed returns them. Company names and numbers are invented."""

from __future__ import annotations

from datetime import date

import pytest

from tenderradar.models import Notice, Profile
from tenderradar.notices import parse_notice


def raw_notice(**over) -> dict:
    record = {
        "publication-number": "300100-2026",
        "publication-date": "2026-04-14+02:00",
        "notice-type": "cn-standard",
        "procedure-identifier": "proc-0001",
        "deadline-receipt-tender-date-lot": ["2026-05-20+02:00", "2026-05-18+02:00"],
        "classification-cpv": ["38434000", "38000000", "38434000"],
        "buyer-country": ["DEU"],
        "buyer-name": {"deu": ["Universitätsklinikum Beispielstadt"]},
        "title-proc": {"deu": "Lieferung von Analysegeräten für das Zentrallabor"},
        "description-proc": {
            "deu": "Beschaffung von zwei  Spektrometern\n einschließlich Wartung für vier Jahre."
        },
        "title-lot": {"deu": ["Los 1 Spektrometer", "Los 2 Wartung"]},
        "description-lot": {"deu": ["Zwei Spektrometer.", "Wartung für vier Jahre."]},
        "estimated-value-proc": "480000.00",
        "estimated-value-cur-proc": "EUR",
        "procedure-type": "open",
        "contract-nature-main-proc": "supplies",
        "official-language": ["DEU"],
        "selection-criterion-description-lot": {
            "deu": [
                "Mindestjahresumsatz von 1 000 000 EUR in den letzten drei Geschäftsjahren.",
                "Mindestjahresumsatz von 1 000 000 EUR in den letzten drei Geschäftsjahren.",
            ]
        },
    }
    record.update(over)
    return record


def raw_award(**over) -> dict:
    record = {
        "publication-number": "700200-2026",
        "publication-date": "2026-08-03+02:00",
        "notice-type": "can-standard",
        "procedure-identifier": "proc-0001",
        "winner-name": {"deu": ["Musterlabor Technik GmbH"]},
        "winner-identifier": ["DE811111111"],
        "winner-country": ["DEU"],
        "winner-size": ["medium"],
        "classification-cpv": ["38434000"],
        "buyer-country": ["DEU"],
        "title-proc": {"deu": "Lieferung von Analysegeräten für das Zentrallabor"},
        "description-proc": {"deu": "Beschaffung von zwei Spektrometern."},
        "result-value-notice": "455000.00",
        "result-value-cur-notice": "EUR",
    }
    record.update(over)
    return record


def make_notice(number: str = "300100-2026", procedure: str = "proc-0001", **over) -> Notice:
    return parse_notice(
        raw_notice(**{"publication-number": number, "procedure-identifier": procedure, **over})
    )


@pytest.fixture
def notice() -> Notice:
    return make_notice()


@pytest.fixture
def profile() -> Profile:
    return Profile(
        profile_id="s_test",
        countries=["DEU", "AUT"],
        cpv_weights={"38434000": 3.0, "38000000": 1.0},
        texts=["Lieferung von Spektrometern", "Analysegeräte für Labor"],
        past_wins=4,
    )


@pytest.fixture
def today() -> date:
    return date(2026, 4, 14)
