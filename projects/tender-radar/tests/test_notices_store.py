from __future__ import annotations

from datetime import date

import pytest

from tenderradar.notices import ParseError, parse_award, parse_notice
from tenderradar.store import AWARD, COMPETITION, Store

from conftest import raw_award, raw_notice


def test_notice_is_flattened_to_its_own_language():
    notice = parse_notice(raw_notice())
    assert notice.title == "Lieferung von Analysegeräten für das Zentrallabor"
    assert notice.buyer_name == "Universitätsklinikum Beispielstadt"
    assert notice.language == "deu"
    assert notice.buyer_country == "DEU"
    assert notice.url == "https://ted.europa.eu/en/notice/-/detail/300100-2026"


def test_whitespace_in_text_is_collapsed():
    notice = parse_notice(raw_notice())
    assert notice.description == (
        "Beschaffung von zwei Spektrometern einschließlich Wartung für vier Jahre."
    )


def test_lots_are_zipped_and_the_earliest_deadline_wins():
    notice = parse_notice(raw_notice())
    assert [(lot.title, lot.description) for lot in notice.lots] == [
        ("Los 1 Spektrometer", "Zwei Spektrometer."),
        ("Los 2 Wartung", "Wartung für vier Jahre."),
    ]
    assert notice.deadline == date(2026, 5, 18)


def test_repeated_codes_and_conditions_are_kept_once_in_order():
    notice = parse_notice(raw_notice())
    assert notice.cpv == ["38434000", "38000000"]
    assert len(notice.conditions) == 1


def test_value_and_currency():
    notice = parse_notice(raw_notice())
    assert (notice.estimated_value, notice.currency) == (480000.0, "EUR")


def test_missing_optional_fields_stay_none_and_nothing_is_guessed():
    raw = raw_notice()
    for key in (
        "deadline-receipt-tender-date-lot",
        "estimated-value-proc",
        "estimated-value-cur-proc",
        "title-lot",
        "description-lot",
        "selection-criterion-description-lot",
        "buyer-name",
    ):
        raw.pop(key)
    notice = parse_notice(raw)
    assert notice.deadline is None
    assert notice.estimated_value is None and notice.currency is None
    assert notice.lots == [] and notice.conditions == []
    assert notice.buyer_name is None
    assert notice.text.startswith("Lieferung von Analysegeräten")


def test_a_zero_or_unreadable_value_is_treated_as_missing():
    assert parse_notice(raw_notice(**{"estimated-value-proc": "0"})).estimated_value is None
    assert parse_notice(raw_notice(**{"estimated-value-proc": "n/a"})).estimated_value is None


def test_text_falls_back_to_the_first_language_given():
    raw = raw_notice(**{"title-proc": {"eng": "Supply of analysers"}})
    assert parse_notice(raw).title == "Supply of analysers"


@pytest.mark.parametrize("key", ["publication-number", "publication-date", "procedure-identifier"])
def test_a_record_without_an_essential_field_is_rejected(key):
    raw = raw_notice()
    raw.pop(key)
    with pytest.raises(ParseError, match=key):
        parse_notice(raw)


def test_a_record_of_the_wrong_type_is_rejected():
    with pytest.raises(ParseError):
        parse_notice(["not", "a", "notice"])
    with pytest.raises(ParseError, match="not a date"):
        parse_notice(raw_notice(**{"publication-date": "soon"}))


def test_award_pairs_names_with_identifiers_when_the_lists_line_up():
    award = parse_award(raw_award())
    assert [(w.name, w.identifier, w.country, w.size) for w in award.winners] == [
        ("Musterlabor Technik GmbH", "DE811111111", "DEU", "medium")
    ]
    assert (award.value, award.currency) == (455000.0, "EUR")


def test_award_with_several_winners_keeps_names_and_pairs_no_numbers():
    award = parse_award(
        raw_award(
            **{
                "winner-name": {
                    "pol": ["Alfa Lab sp. z o.o.", "Beta Med S.A.", "Alfa Lab sp. z o.o."]
                },
                "winner-identifier": ["5270000001", "5270000002"],
                "winner-country": ["POL", "POL"],
            }
        )
    )
    assert [(w.name, w.identifier) for w in award.winners] == [
        ("Alfa Lab sp. z o.o.", None),
        ("Beta Med S.A.", None),
    ]
    assert {w.country for w in award.winners} == {"POL"}


def test_two_winners_and_two_numbers_are_not_paired_by_position():
    # The lists are the same length, but nothing says they are in the same order.
    award = parse_award(
        raw_award(
            **{
                "winner-name": {"pol": ["Alfa Lab sp. z o.o.", "Beta Med S.A."]},
                "winner-identifier": ["5270000002", "5270000001"],
                "winner-country": ["POL", "CZE"],
            }
        )
    )
    assert [(w.name, w.identifier, w.country) for w in award.winners] == [
        ("Alfa Lab sp. z o.o.", None, None),
        ("Beta Med S.A.", None, None),
    ]


def test_one_winner_named_once_per_lot_is_still_one_winner_with_its_number():
    award = parse_award(
        raw_award(
            **{
                "winner-name": {"pol": ["Alfa Lab sp. z o.o.", "Alfa Lab sp. z o.o."]},
                "winner-identifier": ["5270000001", "5270000001"],
                "winner-country": ["POL", "POL"],
                "winner-size": ["sme", "sme"],
            }
        )
    )
    assert [(w.name, w.identifier, w.country, w.size) for w in award.winners] == [
        ("Alfa Lab sp. z o.o.", "5270000001", "POL", "sme")
    ]


def test_award_with_no_winner_has_an_empty_list():
    raw = raw_award()
    for key in ("winner-name", "winner-identifier", "winner-country", "winner-size"):
        raw.pop(key)
    assert parse_award(raw).winners == []


def test_award_with_identifiers_only():
    raw = raw_award()
    raw.pop("winner-name")
    assert [(w.name, w.identifier) for w in parse_award(raw).winners] == [(None, "DE811111111")]


def test_store_upsert_is_idempotent_and_skips_records_without_a_number():
    store = Store(":memory:")
    assert store.upsert_raw(COMPETITION, [raw_notice(), {"publication-date": "2026-04-14"}]) == 1
    assert store.upsert_raw(COMPETITION, [raw_notice()]) == 0
    assert store.count(COMPETITION) == 1
    assert store.parse_failures == 1


def test_store_returns_parsed_notices_in_date_order_and_counts_bad_ones():
    store = Store(":memory:")
    later = raw_notice(**{"publication-number": "2-2026", "publication-date": "2026-04-16+02:00"})
    broken = raw_notice(**{"publication-number": "3-2026", "procedure-identifier": None})
    store.upsert_raw(COMPETITION, [later, raw_notice(), broken])
    notices = store.notices(date(2026, 4, 1), date(2026, 4, 30))
    assert [n.publication_number for n in notices] == ["300100-2026", "2-2026"]
    assert store.parse_failures == 1


def test_a_later_version_does_not_make_a_procedure_new_again():
    store = Store(":memory:")
    first = raw_notice(**{"publication-number": "1-2026", "publication-date": "2026-03-20+01:00"})
    correction = raw_notice(
        **{"publication-number": "2-2026", "publication-date": "2026-04-02+02:00"}
    )
    fresh = raw_notice(
        **{
            "publication-number": "3-2026",
            "publication-date": "2026-04-02+02:00",
            "procedure-identifier": "proc-0002",
        }
    )
    second_version = raw_notice(
        **{
            "publication-number": "4-2026",
            "publication-date": "2026-04-09+02:00",
            "procedure-identifier": "proc-0002",
        }
    )
    store.upsert_raw(COMPETITION, [first, correction, fresh, second_version])
    new = store.new_procedures(date(2026, 4, 1), date(2026, 4, 30))
    assert [n.publication_number for n in new] == ["3-2026"]
    assert store.first_publication()["proc-0001"] == date(2026, 3, 20)


def test_store_state_and_days():
    store = Store(":memory:")
    assert store.get_state("k") is None
    store.set_state("k", "1")
    store.set_state("k", "2")
    assert store.get_state("k") == "2"
    store.upsert_raw(AWARD, [raw_award()])
    assert store.days(AWARD, date(2026, 1, 1), date(2026, 12, 31)) == [date(2026, 8, 3)]
    assert store.latest_day(AWARD) == date(2026, 8, 3)
    assert store.latest_day(COMPETITION) is None
    assert len(store.awards(date(2026, 8, 1), date(2026, 8, 31))) == 1


def test_store_does_not_keep_the_links_the_feed_sends_unasked():
    store = Store(":memory:")
    store.upsert_raw(COMPETITION, [raw_notice(links={"pdf": {"ENG": "https://example.org/x.pdf"}})])
    (record,) = store.raw_records(COMPETITION, date(2026, 4, 1), date(2026, 4, 30))
    assert "links" not in record and record["publication-number"] == "300100-2026"


def test_an_email_address_typed_into_a_notice_is_removed_when_it_is_parsed():
    raw = raw_notice(
        **{
            "description-proc": {
                "deu": "Fragen an Vergabe.Stelle@klinikum-beispiel.de bis Freitag."
            },
            "selection-criterion-description-lot": {
                "deu": ["Nachweise an a.b@example.org senden."]
            },
        }
    )
    notice = parse_notice(raw)
    assert notice.description == "Fragen an [email removed] bis Freitag."
    assert notice.conditions == ["Nachweise an [email removed] senden."]
    assert "@" not in notice.text
    award = parse_award(raw_award(**{"description-proc": {"deu": "Kontakt: x@y.example"}}))
    assert "@" not in award.text
