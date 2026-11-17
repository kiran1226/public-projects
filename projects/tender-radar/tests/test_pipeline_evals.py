from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tenderradar.awards import SupplierIndex, wins_by_supplier
from tenderradar.evals import score, table, wilson_interval
from tenderradar.log import PredictionLog, system_version
from tenderradar.models import GatedAssessment, Outcome, Profile, Shortlist, ShortlistEntry
from tenderradar.notices import parse_award
from tenderradar.panel import panel_outcomes, select_panel
from tenderradar.pipeline import keep_and_count, order_with_assessments, process_day
from tenderradar.rank import AlertRanker, CpvRanker
from tenderradar.truth import OutcomeLog

from conftest import make_notice, raw_award

DAY = date(2026, 4, 14)
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
VERSIONS = {"alert": "v-alert", "cpv": "v-cpv", "fused+model": "v-model"}


def day_notices():
    return [
        make_notice("10-2026", "p1", **{"classification-cpv": ["38434540"]}),
        make_notice("20-2026", "p2", **{"classification-cpv": ["38000000"]}),
        make_notice(
            "30-2026", "p3", **{"classification-cpv": ["38434000"], "buyer-country": ["POL"]}
        ),
        make_notice("40-2026", "p4", **{"classification-cpv": ["45000000"]}),
    ]


def run(profile, **kwargs):
    return process_day(
        DAY,
        day_notices(),
        [profile],
        [AlertRanker(), CpvRanker()],
        versions=VERSIONS,
        mode="backtest",
        now=NOW,
        **kwargs,
    )


def test_rules_remove_before_ranking_and_are_counted_by_name(profile):
    kept, removed = keep_and_count(profile, day_notices(), DAY)
    assert [n.publication_number for n in kept] == ["10-2026", "20-2026", "40-2026"]
    assert removed == {"outside_territory": 1}


def test_one_list_per_profile_per_ranker(profile):
    lists = run(profile)
    assert [(s.ranker, s.system_version) for s in lists] == [("alert", "v-alert"), ("cpv", "v-cpv")]
    cpv_list = lists[1]
    assert [e.publication_number for e in cpv_list.entries] == ["10-2026", "20-2026", "40-2026"]
    assert [e.rank for e in cpv_list.entries] == [1, 2, 3]
    assert cpv_list.candidates == 3 and cpv_list.removed == {"outside_territory": 1}
    assert cpv_list.mode == "backtest" and cpv_list.written_at == NOW


def test_list_size_is_respected(profile):
    assert len(run(profile, list_size=2)[1].entries) == 2


def test_a_day_without_tenders_still_writes_an_empty_list(profile):
    lists = process_day(DAY, [], [profile], [CpvRanker()], versions=VERSIONS, mode="live", now=NOW)
    assert len(lists) == 1 and lists[0].entries == [] and lists[0].candidates == 0


def gated(decision, fit, cost=0.001):
    return GatedAssessment(fit=fit, decision=decision, reason="r", cost_usd=cost)


def test_the_model_reads_only_the_top_and_reorders_it(profile):
    answers = {"10-2026": gated("skip", 10), "20-2026": gated("bid", 80)}
    asked = []

    def assess(_profile, notice):
        asked.append(notice.publication_number)
        return answers[notice.publication_number]

    lists = run(profile, assess=assess, assess_ranker="cpv", assess_top=2)
    model_list = next(s for s in lists if s.ranker == "fused+model")
    assert asked == ["10-2026", "20-2026"]
    # Bid first, the unread entry next, the skip last.
    assert [e.publication_number for e in model_list.entries] == ["20-2026", "40-2026", "10-2026"]
    assert [e.rank for e in model_list.entries] == [1, 2, 3]
    assert model_list.cost_usd == pytest.approx(0.002)
    assert model_list.system_version == "v-model"
    # The plain list is untouched.
    assert [e.publication_number for e in lists[1].entries] == ["10-2026", "20-2026", "40-2026"]


def test_when_the_model_is_not_available_the_list_is_still_written(profile):
    lists = run(profile, assess=lambda _p, _n: None, assess_ranker="cpv", assess_top=2)
    model_list = next(s for s in lists if s.ranker == "fused+model")
    assert model_list.skipped_assessments == 2
    assert [e.publication_number for e in model_list.entries] == ["10-2026", "20-2026", "40-2026"]
    assert model_list.cost_usd == 0


def test_a_rerun_does_not_ask_the_model_again(profile):
    calls = []

    def assess(_profile, notice):
        calls.append(notice.publication_number)
        return gated("bid", 50)

    first = run(profile, assess=assess, assess_ranker="cpv")
    written = {s.key for s in first}
    second = run(profile, assess=assess, assess_ranker="cpv", already_written=written.__contains__)
    assert second == []
    assert len(calls) == 3


def test_order_with_assessments_keeps_ranker_order_inside_a_group():
    entries = [
        ShortlistEntry(rank=1, publication_number="a", score=3, assessment=gated("ask_person", 50)),
        ShortlistEntry(rank=2, publication_number="b", score=2, assessment=gated("bid", 50)),
        ShortlistEntry(rank=3, publication_number="c", score=1, assessment=gated("bid", 50)),
    ]
    assert [e.publication_number for e in order_with_assessments(entries)] == ["b", "c", "a"]


def test_log_appends_once_and_round_trips(tmp_path, profile):
    log = PredictionLog(tmp_path / "log" / "backtest.jsonl.gz")
    lists = run(profile)
    assert log.append(lists) == 2
    assert log.append(lists) == 0
    reopened = PredictionLog(tmp_path / "log" / "backtest.jsonl.gz")
    stored = list(reopened.read())
    assert [s.key for s in stored] == [s.key for s in lists]
    assert [[e.publication_number for e in s.entries] for s in stored] == [
        [e.publication_number for e in s.entries] for s in lists
    ]
    assert stored[1].removed == {"outside_territory": 1} and stored[1].written_at == NOW
    assert reopened.has(lists[0].key) and not reopened.has("other")


def test_plain_log_file_and_daily_cost(tmp_path, profile):
    log = PredictionLog(tmp_path / "live.jsonl")
    lists = run(profile, assess=lambda _p, _n: gated("bid", 50, cost=0.01), assess_ranker="cpv")
    log.append(lists)
    assert log.cost_on("2026-11-17") == pytest.approx(0.03)
    assert log.cost_on("2026-11-18") == 0
    assert len((tmp_path / "live.jsonl").read_text().splitlines()) == 3


def test_system_version_changes_with_any_part():
    base = system_version(ranker="cpv", list_size=15)
    assert base == system_version(list_size=15, ranker="cpv")
    assert base != system_version(ranker="cpv", list_size=10)
    assert len(base) == 8


def shortlist(ranker, numbers, profile_id="s1", day=DAY, mode="backtest", written=NOW, **over):
    entries = [
        ShortlistEntry(rank=i, publication_number=n, score=round(1.0 / i, 4))
        for i, n in enumerate(numbers, 1)
    ]
    return Shortlist(
        profile_id=profile_id,
        day=day,
        ranker=ranker,
        system_version="v",
        mode=mode,
        written_at=written,
        candidates=len(numbers) + 2,
        entries=entries,
        **over,
    )


def outcome(number, profile_id="s1", day=DAY, arrived=date(2026, 8, 3), hits=()):
    return Outcome(
        profile_id=profile_id,
        procedure_id=f"proc-{number}",
        arrived=arrived,
        publication_number=number,
        day=day,
        filter_hits=list(hits),
    )


def test_scoring_counts_ranks_and_misses():
    lists = [
        shortlist("cpv", ["a", "b", "c", "d"]),
        shortlist("alert", ["d", "c"]),
        shortlist("cpv", ["x"], profile_id="s2"),
    ]
    outcomes = [
        outcome("a"),
        outcome("d"),
        outcome("zz"),
        outcome("gone", hits=["outside_territory"]),
        outcome("x", profile_id="s2"),
        outcome("q", profile_id="s3"),
    ]
    by_ranker = {s.ranker: s for s in score(lists, outcomes)}
    cpv = by_ranker["cpv"]
    assert cpv.outcomes == 5
    assert cpv.found == {1: 2, 3: 2, 5: 3, 15: 3}
    assert cpv.ranks == [1, 4, 1] and cpv.median_rank == 1
    assert cpv.not_listed == 1 and cpv.removed_by_rule == 1
    assert cpv.lists == 2 and cpv.mean_list_length == 2.5
    alert = by_ranker["alert"]
    assert alert.outcomes == 4 and alert.found[1] == 1 and alert.not_listed == 2


def test_a_live_list_written_after_the_answer_is_not_scored():
    early = shortlist("cpv", ["a"], mode="live", written=datetime(2026, 8, 2, 7, tzinfo=UTC))
    late = shortlist(
        "cpv", ["b"], mode="live", profile_id="s2", written=datetime(2026, 8, 3, 7, tzinfo=UTC)
    )
    (result,) = score([early, late], [outcome("a"), outcome("b", profile_id="s2")])
    assert result.outcomes == 1 and result.found[1] == 1 and result.written_too_late == 1


def test_backtest_and_live_are_scored_apart():
    lists = [
        shortlist("cpv", ["a"]),
        shortlist("cpv", ["a"], mode="live", written=datetime(2026, 5, 1, tzinfo=UTC)),
    ]
    results = score(lists, [outcome("a")])
    assert [(s.mode, s.outcomes) for s in results] == [("backtest", 1), ("live", 1)]


def test_date_range_and_same_sample_comparison():
    june = date(2026, 6, 2)
    lists = [
        shortlist("cpv", ["a"]),
        shortlist("cpv", ["b"], day=june),
        shortlist("fused+model", ["b"], day=june),
    ]
    outcomes = [outcome("a"), outcome("b", day=june)]
    held_out = score(lists, outcomes, start=date(2026, 6, 1))
    assert {s.ranker: s.outcomes for s in held_out} == {"cpv": 1, "fused+model": 1}
    paired = score(lists, outcomes, only_lists_of="fused+model")
    assert {s.ranker: s.outcomes for s in paired} == {"cpv": 1, "fused+model": 1}
    assert {s.ranker: s.outcomes for s in score(lists, outcomes)}["cpv"] == 2


def test_a_win_the_model_marked_skip_is_counted():
    skipped = shortlist("fused+model", ["a"])
    skipped.entries[0].assessment = gated("skip", 5)
    (result,) = score([skipped], [outcome("a")])
    assert result.skipped_by_model == 1 and result.assessments == 1
    assert "Marked skip" in table([result]) and "| 1 |" in table([result])


def test_wilson_interval():
    low, high = wilson_interval(8, 10)
    assert 0.49 < low < 0.5 and 0.94 < high < 0.95
    assert wilson_interval(0, 0) == (0.0, 0.0)
    assert wilson_interval(10, 10)[1] == 1.0


def test_outcome_log_keeps_the_first_arrival(tmp_path):
    log = OutcomeLog(tmp_path / "outcomes.jsonl")
    assert log.append([outcome("a")]) == 1
    assert log.append([outcome("a", arrived=date(2026, 9, 9)), outcome("b")]) == 1
    assert [o.arrived for o in log.read()] == [date(2026, 8, 3), date(2026, 8, 3)]


def _award(number, procedure, day, name="Alfa Lab sp. z o.o.", ident="5270000001", buyer="POL"):
    return parse_award(
        raw_award(
            **{
                "publication-number": number,
                "procedure-identifier": procedure,
                "publication-date": f"{day}+02:00",
                "winner-name": {"pol": [name]},
                "winner-identifier": [ident],
                "winner-country": [buyer],
                "winner-size": [],
                "buyer-country": [buyer],
                "classification-cpv": ["38434000"],
            }
        )
    )


def test_panel_uses_only_history_before_the_cut_and_scores_only_test_tenders():
    cut = date(2026, 4, 1)
    awards = [_award(f"h{i}", f"old-{i}", "2025-06-01") for i in range(5)]
    awards += [
        _award("t1", "new-1", "2026-08-01"),
        _award("t2", "new-2", "2026-08-02"),
        # Awarded after the cut for a tender published before it: neither history nor test.
        _award("t3", "old-late", "2026-05-05"),
        # A second supplier with history but only one test win: not on the panel.
        *[
            _award(f"g{i}", f"other-{i}", "2025-06-01", "Beta Med S.A.", "5270000002")
            for i in range(5)
        ],
        _award("g9", "new-3", "2026-08-01", "Beta Med S.A.", "5270000002"),
        # A sole trader with enough wins: kept out, the name has no legal form.
        *[
            _award(f"k{i}", f"solo-{i}", "2025-06-01", "Jan Kowalski", "5270000003")
            for i in range(5)
        ],
        _award("k8", "new-4", "2026-08-01", "Jan Kowalski", "5270000003"),
        _award("k9", "new-5", "2026-08-01", "Jan Kowalski", "5270000003"),
    ]
    test_procedures = {
        f"new-{i}": make_notice(
            f"n{i}-2026", f"new-{i}", **{"buyer-country": ["POL" if i != 2 else "CZE"]}
        )
        for i in range(1, 6)
    }
    index = SupplierIndex.build(awards)
    wins = wins_by_supplier(awards, index)
    panel = select_panel(wins, index, cut, test_procedures)
    assert panel.eligible == 1 and len(panel.profiles) == 1
    profile = panel.profiles[0]
    assert profile.past_wins == 5 and profile.countries == ["POL"]
    assert profile.profile_id.startswith("s_")
    outcomes = panel_outcomes(panel, wins, cut, test_procedures)
    assert [(o.procedure_id, o.publication_number, o.filter_hits) for o in outcomes] == [
        ("new-1", "n1-2026", []),
        ("new-2", "n2-2026", ["outside_territory"]),
    ]
    assert all(o.profile_id == profile.profile_id for o in outcomes)


def test_panel_takes_countries_in_turn():
    cut = date(2026, 4, 1)
    awards, test_procedures = [], {}
    for s in range(6):
        buyer = "POL" if s < 4 else "CZE"
        name, ident = f"Firma{s} sp. z o.o.", f"52700001{s:02d}"
        awards += [
            _award(f"h{s}-{i}", f"old-{s}-{i}", "2025-06-01", name, ident, buyer) for i in range(5)
        ]
        for i in range(2):
            awards.append(_award(f"t{s}-{i}", f"new-{s}-{i}", "2026-08-01", name, ident, buyer))
            test_procedures[f"new-{s}-{i}"] = make_notice(
                f"n{s}{i}-2026", f"new-{s}-{i}", **{"buyer-country": [buyer]}
            )
    index = SupplierIndex.build(awards)
    panel = select_panel(wins_by_supplier(awards, index), index, cut, test_procedures, size=4)
    assert panel.eligible == 6
    homes = sorted(p.countries[0] for p in panel.profiles)
    assert homes == ["CZE", "CZE", "POL", "POL"]
    again = select_panel(wins_by_supplier(awards, index), index, cut, test_procedures, size=4)
    assert [p.profile_id for p in again.profiles] == [p.profile_id for p in panel.profiles]


def test_profile_without_history_gets_an_empty_alert_list():
    lists = process_day(
        DAY,
        day_notices(),
        [Profile(profile_id="s_new")],
        [AlertRanker()],
        versions=VERSIONS,
        mode="live",
        now=NOW,
    )
    assert lists[0].entries == []
