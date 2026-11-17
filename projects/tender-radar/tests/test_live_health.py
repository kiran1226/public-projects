from __future__ import annotations

import json
from datetime import UTC, date, datetime

from tenderradar.awards import SupplierIndex, supplier_hash, wins_by_supplier
from tenderradar.health import (
    assess_health,
    check_freshness,
    check_parsing,
    check_volume,
)
from tenderradar.live import (
    DayIndex,
    alias_hash,
    days_to_process,
    export_aliases,
    facts_only,
    live_outcomes,
    profile_ids_for,
    run_live,
)
from tenderradar.log import PredictionLog
from tenderradar.models import GatedAssessment, Profile
from tenderradar.notices import parse_award
from tenderradar.rank import AlertRanker, CpvRanker, FusedRanker

from conftest import make_notice, raw_award

TODAY = date(2026, 11, 17)
RUN_DAY = date(2026, 11, 18)
NOW = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)
VERSIONS = {"alert": "va", "fused": "vf", "fused+model": "vm"}


def _award(number, procedure, day, name="Alfa Lab sp. z o.o.", ident="5270000001"):
    return parse_award(
        raw_award(
            **{
                "publication-number": number,
                "procedure-identifier": procedure,
                "publication-date": f"{day}+01:00",
                "winner-name": {"pol": [name]},
                "winner-identifier": [ident],
                "winner-country": ["POL"],
                "winner-size": [],
                "buyer-country": ["POL"],
            }
        )
    )


def rankers():
    fused = FusedRanker([CpvRanker()])
    return [AlertRanker(), fused]


def panel_profile(profile_id="s_panel"):
    return Profile(
        profile_id=profile_id,
        countries=["DEU"],
        cpv_weights={"38434000": 1.0},
        texts=["Spektrometer"],
    )


def live_kwargs(tmp_path, notices_by_day, **over):
    kwargs = dict(
        new_procedures=lambda day: notices_by_day.get(day, []),
        available_days=sorted(notices_by_day),
        day_index=DayIndex(tmp_path / "live-days.jsonl"),
        log=PredictionLog(tmp_path / "live.jsonl"),
        panel=[panel_profile()],
        client=Profile(
            profile_id="client", kind="client", countries=["DEU"], cpv_weights={"38434000": 1.0}
        ),
        rankers=rankers(),
        versions=VERSIONS,
        list_size=15,
        today=RUN_DAY,
        now=NOW,
    )
    kwargs.update(over)
    return kwargs


def test_first_run_processes_only_the_latest_day(tmp_path):
    days = {
        date(2026, 11, 13): [make_notice("1-2026", "p1")],
        date(2026, 11, 17): [make_notice("2-2026", "p2")],
    }
    report = run_live(**live_kwargs(tmp_path, days))
    assert report.days == [date(2026, 11, 17)] and report.tenders == 1
    # Panel: alert and fused. Client: fused. No model was given.
    assert report.lists_written == 3


def test_later_runs_catch_up_and_never_repeat_a_day(tmp_path):
    days = {date(2026, 11, 13): [make_notice("1-2026", "p1")]}
    kwargs = live_kwargs(tmp_path, days)
    assert run_live(**kwargs).days == [date(2026, 11, 13)]
    days[date(2026, 11, 16)] = [make_notice("2-2026", "p2")]
    days[date(2026, 11, 17)] = [make_notice("3-2026", "p3")]
    second = run_live(**live_kwargs(tmp_path, days))
    assert second.days == [date(2026, 11, 16), date(2026, 11, 17)]
    third = run_live(**live_kwargs(tmp_path, days))
    assert third.days == [] and third.lists_written == 0
    assert len(list(PredictionLog(tmp_path / "live.jsonl").read())) == 9


def test_a_new_version_of_a_known_tender_is_not_listed_again(tmp_path):
    days = {date(2026, 11, 16): [make_notice("1-2026", "p1")]}
    run_live(**live_kwargs(tmp_path, days))
    days[date(2026, 11, 17)] = [make_notice("9-2026", "p1"), make_notice("2-2026", "p2")]
    report = run_live(**live_kwargs(tmp_path, days))
    assert report.tenders == 1
    lists = [
        s for s in PredictionLog(tmp_path / "live.jsonl").read() if s.day == date(2026, 11, 17)
    ]
    assert all([e.publication_number for e in s.entries] in (["2-2026"], []) for s in lists)


def test_the_client_list_carries_assessments_when_a_model_is_given(tmp_path):
    open_until = {"deadline-receipt-tender-date-lot": ["2026-12-20+01:00"]}
    days = {
        TODAY: [
            make_notice("1-2026", "p1", **open_until),
            make_notice("2-2026", "p2", **open_until),
        ]
    }
    answer = GatedAssessment(fit=70, decision="bid", reason="fits", cost_usd=0.001)
    report = run_live(**live_kwargs(tmp_path, days, assess=lambda _p, _n: answer))
    model_lists = [s for s in report.client_lists if s.ranker == "fused+model"]
    assert len(model_lists) == 1 and model_lists[0].mode == "live"
    assert [e.assessment for e in model_lists[0].entries] == [answer, answer]
    assert model_lists[0].cost_usd == 0.002


def test_days_to_process():
    available = [date(2026, 11, 13), date(2026, 11, 16), date(2026, 11, 17)]
    assert days_to_process([], [], RUN_DAY) == []
    assert days_to_process(available, [], RUN_DAY) == [date(2026, 11, 17)]
    assert days_to_process(available, [date(2026, 11, 13)], RUN_DAY) == available[1:]


def test_todays_issue_is_left_for_tomorrow_because_it_may_still_be_arriving():
    available = [date(2026, 11, 13), date(2026, 11, 16), date(2026, 11, 17)]
    assert days_to_process(available, [], TODAY) == [date(2026, 11, 16)]
    assert days_to_process(available, [date(2026, 11, 13)], TODAY) == [date(2026, 11, 16)]
    assert days_to_process([TODAY], [], TODAY) == []


def test_day_index_keeps_facts_but_no_text(tmp_path, notice):
    index = DayIndex(tmp_path / "days.jsonl")
    assert index.append(TODAY, [notice]) is True
    assert index.append(TODAY, [notice]) is False
    stored = index.procedures()["proc-0001"]
    assert stored == facts_only(notice)
    assert (stored.buyer_country, stored.deadline, stored.estimated_value) == (
        "DEU",
        date(2026, 5, 18),
        480000.0,
    )
    text = (tmp_path / "days.jsonl").read_text(encoding="utf-8")
    assert "Analysegeräten" not in text and "Universitätsklinikum" not in text
    assert json.loads(text)["day"] == "2026-11-17"


def test_aliases_are_hashed_and_find_a_winner_under_any_known_spelling():
    awards = [
        _award("1", "old-1", "2025-05-01"),
        _award(
            "2",
            "old-2",
            "2025-06-01",
            name="ALFA LAB spółka z ograniczoną odpowiedzialnością",
            ident="NIP5270000001",
        ),
        _award("3", "old-3", "2025-06-01", name="Beta Med S.A.", ident="5270000002"),
    ]
    index = SupplierIndex.build(awards)
    wins = wins_by_supplier(awards, index)
    key = next(k for k, v in wins.items() if len(v) == 2)
    aliases = export_aliases(index, {supplier_hash(key): key})
    assert set(aliases.values()) == {supplier_hash(key)}
    assert all(len(k) == 16 for k in aliases)
    assert "alfa" not in json.dumps(aliases).lower() and "5270000001" not in json.dumps(aliases)
    new_spelling = _award(
        "9", "new-1", "2026-11-20", name="Alfa Lab Sp. z o. o.", ident="PL5270000001"
    )
    assert profile_ids_for(new_spelling, aliases) == {supplier_hash(key)}
    assert profile_ids_for(awards[2], aliases) == set()
    assert alias_hash("N:POL:alfa lab") in aliases


def test_live_outcomes_join_awards_to_tenders_the_run_has_listed(tmp_path):
    history = [_award("1", "old-1", "2025-05-01")]
    index = SupplierIndex.build(history)
    key = next(iter(wins_by_supplier(history, index)))
    profile = Profile(profile_id=supplier_hash(key), countries=["POL"])
    aliases = export_aliases(index, {profile.profile_id: key})
    days = DayIndex(tmp_path / "days.jsonl")
    days.append(
        TODAY,
        [
            make_notice("5-2026", "new-1", **{"buyer-country": ["POL"]}),
            make_notice("6-2026", "new-2", **{"buyer-country": ["CZE"]}),
        ],
    )
    awards = [
        _award("70", "new-1", "2027-02-01"),
        _award("71", "new-1", "2027-03-01"),  # a later notice for the same award
        _award("72", "new-2", "2027-02-05"),
        _award("73", "never-listed", "2027-02-05"),
        _award("74", "new-1", "2027-02-01", name="Someone Else GmbH", ident="5270000099"),
    ]
    outcomes = live_outcomes(awards, aliases, days, [profile])
    assert [
        (o.procedure_id, o.arrived, o.publication_number, o.day, o.filter_hits) for o in outcomes
    ] == [
        ("new-1", date(2027, 2, 1), "5-2026", date(2026, 4, 14), []),
        ("new-2", date(2027, 2, 5), "6-2026", date(2026, 4, 14), ["outside_territory"]),
    ]


def test_health_checks():
    assert check_parsing(1000, 5).ok and not check_parsing(1000, 50).ok
    assert check_parsing(0, 0).ok
    assert check_freshness(date(2026, 11, 13), TODAY).ok
    assert not check_freshness(date(2026, 11, 10), TODAY).ok
    assert not check_freshness(None, TODAY).ok
    usual = {date(2026, 11, d): 200 for d in range(2, 14)}
    assert check_volume({**usual, date(2026, 11, 16): 180}).ok
    low = check_volume({**usual, date(2026, 11, 16): 12})
    assert not low.ok and "12 notices" in low.detail
    assert check_volume({date(2026, 11, 16): 1}).ok  # too little history to judge


def test_a_feed_error_fails_health_and_skips_the_other_checks(tmp_path):
    health = assess_health(
        now=NOW,
        today=TODAY,
        feed_error="TED answered 200 with a body that is not JSON",
        fetched=0,
        parse_failures=0,
        latest_day=None,
        counts_by_day={},
    )
    assert not health.ok and [c.name for c in health.checks] == ["feed_answers"]
    health.write(tmp_path / "out" / "health.json")
    written = json.loads((tmp_path / "out" / "health.json").read_text())
    assert written["ok"] is False and "not JSON" in written["checks"][0]["detail"]


def test_a_normal_run_is_healthy():
    counts = {date(2026, 11, d): 200 for d in range(2, 18)}
    health = assess_health(
        now=NOW,
        today=TODAY,
        feed_error=None,
        fetched=400,
        parse_failures=0,
        latest_day=TODAY,
        counts_by_day=counts,
    )
    assert health.ok and len(health.checks) == 4
