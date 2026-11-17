"""The committed fixture: one real publication day, run end to end without feed or key."""

from __future__ import annotations

from datetime import date

import pytest

from tenderradar import backtest as bt
from tenderradar.assess import Assessor, notice_text
from tenderradar.config import load_settings
from tenderradar.demo import client_list, fixture_path, format_list, load_fixture
from tenderradar.embed import HashEmbedder
from tenderradar.gate import quote_in_source
from tenderradar.llm import DEFAULT_EFFORT, DEFAULT_MODEL, CassetteProvider
from tenderradar.pipeline import keep_and_count
from tenderradar.profile import load_client_profile, load_profiles

SETTINGS = load_settings()
DAY = date(2026, 5, 12)


@pytest.fixture(scope="module")
def notices():
    loaded, failures = load_fixture(fixture_path(SETTINGS.fixture_dir))
    assert failures == 0
    return loaded


@pytest.fixture(scope="module")
def client():
    return load_client_profile(SETTINGS.client_profile)


def recorded_assessor() -> Assessor:
    provider = CassetteProvider(
        SETTINGS.cassette_dir / "fixture.jsonl",
        model=DEFAULT_MODEL,
        params={"effort": DEFAULT_EFFORT, "max_tokens": 4000},
    )
    return Assessor(provider)


def test_every_real_record_parses(notices):
    assert len(notices) == 191
    assert {n.publication_date for n in notices} == {DAY}
    assert len({n.procedure_id for n in notices}) == 191
    assert all(n.title and n.cpv and n.buyer_country for n in notices)


def test_real_notices_come_in_many_languages_and_some_lack_fields(notices):
    assert len({n.language for n in notices}) >= 10
    # The parser must cope with what buyers leave out; these are facts about the day.
    assert any(n.estimated_value is None for n in notices)
    assert any(not n.conditions for n in notices)
    assert all(n.deadline is None or n.deadline >= DAY for n in notices)


def test_the_fixture_holds_only_the_fields_the_feed_client_asks_for():
    import gzip
    import json

    from tenderradar.feed import COMPETITION_FIELDS

    keys: set[str] = set()
    with gzip.open(fixture_path(SETTINGS.fixture_dir), "rt", encoding="utf-8") as handle:
        for line in handle:
            keys |= set(json.loads(line))
    assert keys <= set(COMPETITION_FIELDS)
    # No supplier is named in a competition notice, and no contact field or link is fetched.
    assert not {k for k in keys if "winner" in k or "email" in k or "contact" in k or k == "links"}


def test_the_clients_rules_on_a_real_day(notices, client):
    kept, removed = keep_and_count(client, notices, DAY)
    assert len(kept) == 74
    assert removed == {
        "deadline_too_close": 1,
        "excluded_procedure": 1,
        "outside_territory": 106,
        "value_out_of_band": 9,
    }
    assert {n.buyer_country for n in kept} <= {"DEU", "AUT", "POL", "CZE"}


def test_demo_list_is_built_from_recordings_alone(notices, client):
    assessor = recorded_assessor()
    rankers = bt.build_rankers(None, HashEmbedder())
    shortlist = client_list(notices, client, rankers, assess=assessor, list_size=15, assess_top=10)
    assert len(shortlist.entries) == 15 and shortlist.candidates == 74
    assert assessor.calls == 10 and assessor.replayed == 10
    assert assessor.no_recording == 0 and assessor.spent_usd == 0
    assert sum(1 for e in shortlist.entries if e.assessment) == 10
    text = format_list(shortlist, notices)
    assert "Passed the rules: 74. Listed: 15." in text
    assert "outside_territory: 106" in text and "https://ted.europa.eu/en/notice/-/detail/" in text


def test_every_recorded_claim_that_reached_the_list_is_in_its_notice(notices, client):
    assessor = recorded_assessor()
    rankers = bt.build_rankers(None, HashEmbedder())
    shortlist = client_list(notices, client, rankers, assess=assessor, list_size=74, assess_top=74)
    by_number = {n.publication_number: n for n in notices}
    assessed = [e for e in shortlist.entries if e.assessment]
    assert len(assessed) == 74
    for entry in assessed:
        source = notice_text(by_number[entry.publication_number])
        if entry.assessment.reason_quote:
            assert quote_in_source(entry.assessment.reason_quote, source)
        else:
            assert entry.assessment.decision == "ask_person"
        for condition in entry.assessment.conditions:
            assert quote_in_source(condition.quote, source)
    decisions = {e.assessment.decision for e in assessed}
    assert decisions == {"bid", "ask_person", "skip"}


def test_what_the_model_would_bid_on_comes_before_what_it_would_skip(notices, client):
    rankers = bt.build_rankers(None, HashEmbedder())
    shortlist = client_list(
        notices, client, rankers, assess=recorded_assessor(), list_size=15, assess_top=10
    )
    order = {"bid": 0, "ask_person": 1, None: 2, "skip": 3}
    ranks = [order[e.assessment.decision if e.assessment else None] for e in shortlist.entries]
    assert ranks == sorted(ranks)


def test_committed_panel_profiles_hold_hashed_ids_and_no_names():
    profiles = load_profiles(SETTINGS.profiles_dir / "panel.jsonl.gz")
    assert len(profiles) == 200
    assert all(p.profile_id.startswith("s_") and len(p.profile_id) == 14 for p in profiles)
    assert all(p.name == "" and p.kind == "supplier" for p in profiles)
    assert all(p.past_wins >= 5 and p.countries and p.cpv_weights for p in profiles)
    assert all(key.startswith("b_") for p in profiles for key in p.buyers)
