"""The policy between the tiers, the verdict, the prediction log, the outcomes and the scoring."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from editwatch import gate
from editwatch.budget import PacedBudget
from editwatch.cascade import BUDGET, QUEUE_FULL, Admission, Cascade, edit_seconds_into_day
from editwatch.combine import DEFAULT, Combiner
from editwatch.config import Settings
from editwatch.evals import (
    SHARES,
    arrival_order,
    at_share,
    percentile,
    rank,
    score,
    score_queue,
    wilson_interval,
)
from editwatch.log import PredictionLog, from_row, to_row
from editwatch.models import Change, Outcome, Prediction
from editwatch.truth import OutcomeLog, collect, due
from editwatch.wiki import BATCH, REVERTED_TAG

from conftest import FakeWiki, answer, at, make_client, make_edit, make_prediction, make_tier1

CHANGE = Change(
    rev_id=5,
    lines=[
        "1 - The lighthouse was built in 1874.",
        "2 + The lighthouse was built in 1784.",
        "3 + It is painted green on Tuesdays.",
    ],
)
NOON = 43_200.0

# -- admission -----------------------------------------------------------------------


def test_an_edit_below_the_threshold_is_below_the_slice_and_is_not_counted_as_a_skip():
    admission = Admission(threshold=0.5, queue_bound=2)
    assert admission.offer(0.49, 0.0) == "below_slice"
    assert admission.counts == {"tier1_only": 1, "admitted": 0, QUEUE_FULL: 0, BUDGET: 0}
    assert admission.waiting == 0


def test_edits_are_admitted_until_the_queue_bound_is_waiting_then_the_queue_is_full():
    admission = Admission(threshold=0.5, queue_bound=3)
    said = [admission.offer(score, 0.0) for score in (0.5, 0.9, 0.7, 0.9, 0.9)]
    assert said == [None, None, None, "queue_full", "queue_full"]
    assert admission.waiting == 3
    assert admission.counts["admitted"] == 3 and admission.counts[QUEUE_FULL] == 2


def test_done_frees_a_place_in_the_queue_and_an_empty_queue_stays_empty():
    admission = Admission(threshold=0.5, queue_bound=1)
    assert admission.offer(0.9, 0.0) is None
    assert admission.offer(0.9, 0.0) == "queue_full"
    admission.done()
    assert admission.offer(0.9, 0.0) is None
    admission.done()
    admission.done()
    assert admission.waiting == 0


def test_a_budget_that_refuses_turns_the_edit_away_and_counts_it():
    budget = PacedBudget(cap_usd=1.0, spent_usd=1.0)
    admission = Admission(threshold=0.5, queue_bound=5, budget=budget)
    assert admission.offer(0.9, NOON) == "budget"
    assert admission.counts[BUDGET] == 1 and admission.counts["admitted"] == 0
    assert admission.waiting == 0 and budget.refused == 1


def test_the_budget_is_asked_for_the_time_of_day_of_the_edit():
    # 0.30 of a 1.00 cap is spent: more than 01:00 allows, less than noon allows.
    budget = PacedBudget(cap_usd=1.0, spent_usd=0.3, headroom_share=0.0)
    admission = Admission(threshold=0.5, queue_bound=5, budget=budget)
    assert admission.offer(0.9, 3_600.0) == "budget"
    assert admission.offer(0.9, NOON) is None


def test_a_full_queue_is_reported_before_the_budget_is_asked():
    budget = PacedBudget(cap_usd=1.0, spent_usd=1.0)
    admission = Admission(threshold=0.5, queue_bound=0, budget=budget)
    assert admission.offer(0.9, 0.0) == "queue_full"
    assert budget.refused == 0 and admission.counts[BUDGET] == 0


def test_admission_keeps_a_count_of_every_answer_it_gave():
    budget = PacedBudget(cap_usd=1.0, spent_usd=0.3, headroom_share=0.0)
    admission = Admission(threshold=0.5, queue_bound=2, budget=budget)
    offers = [(0.1, NOON), (0.9, NOON), (0.2, NOON), (0.9, 3_600.0), (0.8, NOON), (0.7, NOON),
              (0.3, NOON)]  # fmt: skip
    said = [admission.offer(score, seconds) for score, seconds in offers]
    assert said == ["below_slice", None, "below_slice", "budget", None, "queue_full", "below_slice"]
    assert admission.counts == {"tier1_only": 3, "admitted": 2, QUEUE_FULL: 1, BUDGET: 1}
    assert Admission(0.5, 2).counts["admitted"] == 0, "a new admission starts its own counts"


# -- the system version --------------------------------------------------------------


def make_cascade(tmp_path, *, tier1=None, settings=None, **fields) -> Cascade:
    fields.setdefault("model", "scripted-model")
    return Cascade(settings or Settings(home=tmp_path), tier1 or make_tier1(), **fields)


def version(tmp_path, config: str = "cascade", **fields) -> str:
    return make_cascade(tmp_path, **fields).version(config)


def test_the_version_differs_between_configurations(tmp_path):
    assert version(tmp_path, "cascade") != version(tmp_path, "tier1")
    assert len(version(tmp_path)) == 10


def test_the_version_is_stable_for_the_same_system(tmp_path):
    base = version(tmp_path)
    assert version(tmp_path) == base
    assert version(tmp_path, share=0.20) == base, "the share the settings already name"
    assert version(tmp_path, settings=Settings(home=tmp_path / "elsewhere")) == base
    load = Settings(home=tmp_path, queue_bound=7, workers=1, daily_cap_usd=9.0)
    assert version(tmp_path, settings=load) == base
    assert version(tmp_path, combiner=replace(DEFAULT, fitted_on=900, reverted=80)) == base


def test_the_version_changes_with_the_model_and_with_how_much_of_a_change_it_is_shown(tmp_path):
    base = version(tmp_path)
    assert version(tmp_path, model="another-model") != base
    assert version(tmp_path, settings=Settings(home=tmp_path, change_chars=900)) != base


def test_the_version_changes_with_the_share_the_model_reads(tmp_path):
    base = version(tmp_path)
    assert version(tmp_path, share=0.10) != base
    assert version(tmp_path, settings=Settings(home=tmp_path, model_share=0.30)) != base


def test_the_version_changes_with_each_weight_of_the_combiner(tmp_path):
    for weight in ("intercept", "weight_tier1", "weight_model"):
        moved = replace(DEFAULT, **{weight: getattr(DEFAULT, weight) + 0.25})
        assert version(tmp_path, combiner=moved) != version(tmp_path), weight


def test_the_version_changes_when_tier_one_is_trained_again(tmp_path):
    base = version(tmp_path)
    assert version(tmp_path, tier1=make_tier1(trained_on=1001)) != base
    assert version(tmp_path, tier1=make_tier1({0.2: 0.55})) != base


def test_without_a_model_the_version_ignores_the_share_and_the_combiner(tmp_path):
    alone = version(tmp_path, "tier1", model=None)
    other = version(tmp_path, "tier1", model=None, share=0.05, combiner=Combiner(0.0, 1.0, 1.0))
    assert alone == other
    assert alone != version(tmp_path, "tier1")


# -- the slice -----------------------------------------------------------------------


def test_the_slice_starts_at_the_tier_one_quantile_of_the_settings_share(tmp_path):
    cascade = make_cascade(tmp_path)  # a fifth of the stream; make_tier1 puts that at 0.5
    assert cascade.model_share == 0.20 and cascade.threshold == 0.5
    assert cascade.in_slice(0.5) and cascade.in_slice(0.93)
    assert not cascade.in_slice(0.4999)


@pytest.mark.parametrize(("share", "threshold"), [(0.02, 0.9), (0.10, 0.7), (0.50, 0.2)])
def test_a_share_given_to_the_cascade_moves_the_slice_to_that_quantile(tmp_path, share, threshold):
    cascade = make_cascade(tmp_path, share=share)
    assert cascade.model_share == share and cascade.threshold == threshold
    assert cascade.in_slice(threshold) and not cascade.in_slice(threshold - 0.01)


def test_a_share_of_one_puts_every_edit_in_the_slice(tmp_path):
    cascade = make_cascade(tmp_path, share=1.0)
    assert cascade.in_slice(0.0) and cascade.in_slice(1e-9) and cascade.in_slice(1.0)


# -- the verdict ---------------------------------------------------------------------


def test_a_valid_answer_is_combined_with_the_tier_one_score(tmp_path):
    combiner = Combiner(intercept=-2.0, weight_tier1=0.5, weight_model=3.0)
    cascade = make_cascade(tmp_path, combiner=combiner)
    verdict = cascade.model_verdict(0.62, answer(85, "false_or_unsourced_claim", 2, 3), CHANGE)
    assert verdict.risk == combiner.risk(0.62, 85) != DEFAULT.risk(0.62, 85)
    assert (verdict.tier1, verdict.model_risk, verdict.source) == (0.62, 85, "model")
    assert (verdict.category, verdict.first_line, verdict.last_line) == ("false_or_unsourced_claim", 2, 3)  # fmt: skip
    assert verdict.skipped is None and verdict.gate == []


def test_a_valid_answer_without_a_range_marks_no_lines(tmp_path):
    verdict = make_cascade(tmp_path).model_verdict(0.9, answer(0, "none"), CHANGE)
    assert verdict.source == "model" and verdict.risk < 0.9
    assert (verdict.first_line, verdict.last_line) == (None, None)


def test_an_answer_the_gate_rejects_falls_back_to_the_tier_one_score(tmp_path):
    verdict = make_cascade(tmp_path).model_verdict(0.62, answer(95, "vandalism", 2, 9), CHANGE)
    assert (verdict.risk, verdict.tier1, verdict.source) == (0.62, 0.62, "tier1")
    assert verdict.skipped == "gate"
    assert verdict.gate == [gate.RANGE_OUTSIDE_CHANGE]
    assert (verdict.model_risk, verdict.category, verdict.first_line) == (None, None, None)


def test_an_answer_the_gate_repairs_still_counts_and_names_the_rule(tmp_path):
    verdict = make_cascade(tmp_path).model_verdict(0.62, answer(35, "none", 1, 2), CHANGE)
    assert verdict.source == "model" and verdict.model_risk == 35 and verdict.skipped is None
    assert verdict.gate == [gate.RANGE_WITHOUT_CATEGORY]
    assert (verdict.first_line, verdict.last_line) == (None, None)


def test_no_answer_or_no_change_falls_back_to_the_tier_one_score_as_a_model_error(tmp_path):
    cascade = make_cascade(tmp_path)
    for verdict in (
        cascade.model_verdict(0.62, None, CHANGE),
        cascade.model_verdict(0.62, answer(95, "vandalism"), None),
    ):
        assert (verdict.risk, verdict.source, verdict.skipped) == (0.62, "tier1", "model_error")
        assert verdict.gate == [] and verdict.model_risk is None


def test_seconds_into_the_day_come_from_the_time_the_edit_was_made():
    assert edit_seconds_into_day(make_edit(1, minute=0)) == 0
    assert edit_seconds_into_day(make_edit(1, minute=61.5)) == 3690
    assert edit_seconds_into_day(make_edit(1, minute=24 * 60 + 2)) == 120


# -- the prediction log --------------------------------------------------------------

TIER1_ROW = make_prediction(101, 0.12345, minute=3)
MODEL_ROW = make_prediction(
    102, 0.81234, tier1=0.4321, model_risk=90, category="vandalism", lines=(2, 3),
    gate=["RANGE_WITHOUT_CATEGORY"], cost_usd=0.000333, latency_s=4.25, minute=4,
)  # fmt: skip
ROW_KEYS = {"r", "t", "w", "v", "cfg", "risk", "t1", "m", "cat", "ln", "skip", "g", "usd", "s"}


def test_a_tier_one_row_holds_only_ids_times_and_the_score_and_reads_back_as_it_was():
    row = to_row(TIER1_ROW)
    assert row == {
        "r": 101,
        "t": "2026-11-01T00:03:00+00:00",
        "w": "2026-11-01T00:03:05+00:00",
        "v": "v1",
        "cfg": "cascade",
        "risk": 0.12345,
        "t1": 0.12345,
    }
    back = from_row(row)
    assert back == TIER1_ROW
    assert back.cost_usd == 0.0 and back.latency_s is None


def test_a_model_row_with_a_range_gate_rules_cost_and_latency_reads_back_through_json():
    row = to_row(MODEL_ROW)
    assert (row["m"], row["cat"], row["ln"]) == (90, "vandalism", [2, 3])
    assert (row["g"], row["usd"], row["s"]) == (["RANGE_WITHOUT_CATEGORY"], 0.000333, 4.25)
    assert (row["risk"], row["t1"]) == (0.81234, 0.4321)
    assert "skip" not in row
    assert from_row(json.loads(json.dumps(row))) == MODEL_ROW


def test_a_skipped_row_keeps_the_reason_and_the_gate_rules():
    skipped = make_prediction(104, 0.4, skipped="gate", gate=["RANGE_OUTSIDE_CHANGE"])
    row = to_row(skipped)
    assert row["skip"] == "gate" and "m" not in row and "cat" not in row
    back = from_row(row)
    assert back == skipped and back.verdict.source == "tier1"


def test_a_model_row_with_a_risk_of_zero_and_no_range_still_reads_back_as_a_model_row():
    calm = make_prediction(105, 0.02, tier1=0.6, model_risk=0, category="none", latency_s=0.0)
    row = to_row(calm)
    assert "ln" not in row
    back = from_row(row)
    assert back == calm and back.verdict.source == "model" and back.latency_s == 0.0


def test_scores_cost_and_latency_are_rounded_in_the_row():
    fine = make_prediction(106, 0.123456789, tier1=0.987654321, cost_usd=0.00012345678, latency_s=3.14159)  # fmt: skip
    row = to_row(fine)
    assert (row["risk"], row["t1"], row["usd"], row["s"]) == (0.12346, 0.98765, 0.000123, 3.142)


def test_a_log_that_was_never_written_reads_as_empty(tmp_path):
    log = PredictionLog(tmp_path / "missing" / "log.jsonl")
    assert log.read() == [] and log.keys() == set()
    assert not log.has(101, "cascade", "v1")


def test_appending_creates_the_folder_and_returns_how_many_rows_were_added(tmp_path):
    log = PredictionLog(tmp_path / "data" / "log" / "run.jsonl")
    assert log.append([TIER1_ROW, MODEL_ROW]) == 2
    assert log.read() == [TIER1_ROW, MODEL_ROW]
    assert log.has(101, "cascade", "v1") and not log.has(101, "tier1", "v1")


def test_appending_the_same_rows_again_adds_nothing(tmp_path):
    log = PredictionLog(tmp_path / "run.jsonl")
    assert log.append([TIER1_ROW, MODEL_ROW, TIER1_ROW]) == 2
    assert log.append([TIER1_ROW, MODEL_ROW]) == 0
    assert len((tmp_path / "run.jsonl").read_text().splitlines()) == 2


def test_a_log_opened_again_never_rewrites_a_row_it_already_holds(tmp_path):
    PredictionLog(tmp_path / "run.jsonl").append([TIER1_ROW])
    again = PredictionLog(tmp_path / "run.jsonl")
    changed_its_mind = make_prediction(101, 0.99, minute=3)
    assert again.append([changed_its_mind, MODEL_ROW]) == 1
    assert again.read() == [TIER1_ROW, MODEL_ROW]


def test_one_edit_has_a_row_per_configuration_and_per_version(tmp_path):
    log = PredictionLog(tmp_path / "run.jsonl")
    rows = [
        make_prediction(101, 0.2),
        make_prediction(101, 0.2, config="tier1"),
        make_prediction(101, 0.2, version="v2"),
    ]
    assert log.append(rows) == 3
    assert log.keys() == {(101, "cascade", "v1"), (101, "tier1", "v1"), (101, "cascade", "v2")}


@pytest.mark.parametrize("name", ["run.jsonl", "run.jsonl.gz"])
def test_the_log_reads_back_what_was_appended_in_two_goes(tmp_path, name):
    log = PredictionLog(tmp_path / name)
    assert log.append([TIER1_ROW]) == 1
    assert log.append([MODEL_ROW]) == 1
    assert PredictionLog(tmp_path / name).read() == [TIER1_ROW, MODEL_ROW]


def test_a_log_named_gz_is_compressed_and_a_plain_log_is_one_json_object_to_a_line(tmp_path):
    PredictionLog(tmp_path / "run.jsonl.gz").append([TIER1_ROW, MODEL_ROW])
    PredictionLog(tmp_path / "run.jsonl").append([TIER1_ROW, MODEL_ROW])
    assert (tmp_path / "run.jsonl.gz").read_bytes()[:2] == b"\x1f\x8b"
    lines = (tmp_path / "run.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == [to_row(TIER1_ROW), to_row(MODEL_ROW)]


def test_the_log_file_holds_no_title_no_summary_and_no_text_of_the_change(tmp_path):
    edit = make_edit(5, title="Saltmarsh Lighthouse", comment="fixed the year it was built")
    cascade = make_cascade(tmp_path)
    verdict = cascade.model_verdict(0.62, answer(85, "false_or_unsourced_claim", 2, 3), CHANGE)
    row = Prediction(
        rev_id=edit.rev_id, made_at=edit.timestamp, written_at=at(1),
        system_version=cascade.version("cascade"), config="cascade", verdict=verdict,
        cost_usd=0.0004, latency_s=2.5,
    )  # fmt: skip
    path = tmp_path / "run.jsonl"
    PredictionLog(path).append([row])
    written = path.read_text()
    for kept_out in ("Saltmarsh", "Lighthouse", "fixed the year", "lighthouse", "painted green"):
        assert kept_out not in written
    assert set(json.loads(written)) <= ROW_KEYS
    assert json.loads(written)["ln"] == [2, 3]


# -- truth ---------------------------------------------------------------------------


def outcome(rev_id: int, reverted: bool, *, found: bool = True, minute: float = 0) -> Outcome:
    return Outcome(rev_id=rev_id, made_at=at(minute), read_at=at(minute, hours=49),
                   reverted=reverted, found=found)  # fmt: skip


def test_a_prediction_is_due_once_the_waiting_period_is_over(settings):
    predictions = [make_prediction(1, 0.5, minute=0), make_prediction(2, 0.5, minute=1)]
    assert settings.truth_after_hours == 48
    assert due(predictions, {}, settings, at(0, hours=47, seconds=3599)) == {}
    assert due(predictions, {}, settings, at(0, hours=48)) == {1: at(0)}
    assert due(predictions, {}, settings, at(1, hours=48)) == {1: at(0), 2: at(1)}


def test_a_prediction_with_an_outcome_is_not_due_again_and_an_edit_is_due_once(settings):
    predictions = [make_prediction(1, 0.5), make_prediction(2, 0.5),
                   make_prediction(2, 0.4, config="tier1")]  # fmt: skip
    known = {1: outcome(1, True)}
    assert due(predictions, known, settings, at(0, days=5)) == {2: at(0)}


def test_outcomes_say_which_revisions_were_reverted_kept_or_can_no_longer_be_read():
    fake = FakeWiki(tags={11: ["mobile edit", REVERTED_TAG], 12: ["mobile edit"], 13: []})
    outcomes = collect(make_client(fake), {11: at(1), 12: at(2), 13: at(3), 14: at(4)}, at(0, days=3))  # fmt: skip
    assert [(o.rev_id, o.reverted, o.found) for o in outcomes] == [
        (11, True, True),
        (12, False, True),
        (13, False, True),
        (14, False, False),
    ]
    assert [o.made_at for o in outcomes] == [at(1), at(2), at(3), at(4)]
    assert all(o.read_at == at(0, days=3) for o in outcomes)


def test_tags_are_read_fifty_revisions_to_a_request():
    ids = list(range(1000, 1120))
    fake = FakeWiki(tags={rev: [REVERTED_TAG] if rev % 10 == 0 else [] for rev in ids})
    lines: list[str] = []
    outcomes = collect(make_client(fake), {rev: at(0) for rev in reversed(ids)}, at(0, days=3), lines.append)  # fmt: skip
    assert BATCH == 50
    assert fake.revids_asked() == [ids[:50], ids[50:100], ids[100:]]
    assert all(params["rvprop"] == "ids|tags" for params in fake.asked("revisions"))
    assert [o.rev_id for o in outcomes] == ids
    assert sum(o.reverted for o in outcomes) == 12
    assert lines == ["50 of 120 outcomes read", "100 of 120 outcomes read", "120 of 120 outcomes read"]  # fmt: skip


def test_the_outcome_log_reads_back_what_was_appended(tmp_path):
    log = OutcomeLog(tmp_path / "data" / "log" / "outcomes.jsonl")
    assert log.read() == {}
    rows = [outcome(1, True), outcome(2, False), outcome(3, False, found=False)]
    assert log.append(rows) == 3
    assert log.read() == {1: rows[0], 2: rows[1], 3: rows[2]}


def test_the_outcome_log_keeps_the_first_outcome_of_a_revision(tmp_path):
    path = tmp_path / "outcomes.jsonl"
    log = OutcomeLog(path)
    log.append([outcome(1, False)])
    assert log.append([outcome(1, True), outcome(2, True)]) == 1
    assert log.read()[1].reverted is False
    assert len(path.read_text().splitlines()) == 2
    # A second row for a revision, however it got into the file, is not read.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n" + outcome(2, False).model_dump_json() + "\n")
    assert log.read()[2].reverted is True


def test_a_revision_given_twice_in_one_call_gets_one_row_in_the_outcome_log(tmp_path):
    log = OutcomeLog(tmp_path / "outcomes.jsonl")
    assert log.append([outcome(1, True), outcome(1, False)]) == 1
    assert len((tmp_path / "outcomes.jsonl").read_text().splitlines()) == 1


# -- scoring a queue -----------------------------------------------------------------

# Thirty edits, revision 1 the riskiest and revision 30 the safest. Five were reverted.
RISKS = {rev: (31 - rev) / 100 for rev in range(1, 31)}
REVERTED = {rev: rev in (1, 3, 6, 16, 30) for rev in range(1, 31)}


def test_the_depth_of_each_share_is_rounded_up_to_a_whole_edit():
    s = score_queue("cascade", "v1", RISKS, REVERTED)
    assert SHARES == (0.02, 0.05, 0.10, 0.20)
    assert s.depth == {0.02: 1, 0.05: 2, 0.10: 3, 0.20: 6}


def test_the_hits_at_each_share_are_the_reverted_edits_inside_that_part_of_the_queue():
    shuffled = {rev: RISKS[rev] for rev in sorted(RISKS, key=lambda rev: (rev * 7) % 31)}
    s = score_queue("cascade", "v1", shuffled, REVERTED)
    assert (s.config, s.system_version, s.edits, s.reverted) == ("cascade", "v1", 30, 5)
    assert s.hits == {0.02: 1, 0.05: 1, 0.10: 2, 0.20: 3}
    assert s.share_found(0.02) == pytest.approx(0.2) and s.share_found(0.20) == pytest.approx(0.6)


def test_reverted_edits_in_the_bottom_half_of_the_queue_are_counted():
    assert score_queue("cascade", "v1", RISKS, REVERTED).bottom_half == 2


def test_the_middle_edit_of_an_odd_queue_belongs_to_the_bottom_half():
    risks = {1: 0.9, 2: 0.8, 3: 0.5, 4: 0.2, 5: 0.1}
    middle = dict.fromkeys(risks, False) | {3: True}
    above = dict.fromkeys(risks, False) | {2: True}
    assert score_queue("cascade", "v1", risks, middle).bottom_half == 1
    assert score_queue("cascade", "v1", risks, above).bottom_half == 0


def test_an_edit_without_an_outcome_is_left_out_of_the_score():
    s = score_queue("cascade", "v1", RISKS | {99: 0.99}, REVERTED)
    assert s.edits == 30 and s.hits[0.02] == 1


def test_equal_risks_are_ordered_by_revision_id():
    assert rank([(30, 0.5), (10, 0.5), (20, 0.5), (40, 0.9)]) == [40, 10, 20, 30]
    risks = dict.fromkeys(range(1, 11), 0.5)
    earlier = score_queue("cascade", "v1", risks, dict.fromkeys(risks, False) | {1: True})
    later = score_queue("cascade", "v1", risks, dict.fromkeys(risks, False) | {2: True})
    assert earlier.depth[0.10] == 1
    assert earlier.hits[0.10] == 1 and later.hits[0.10] == 0


def test_a_queue_that_is_empty_or_has_no_reverted_edit_has_no_share_found():
    empty = score_queue("cascade", "v1", {}, {})
    assert (empty.edits, empty.reverted, empty.bottom_half) == (0, 0, 0)
    assert empty.share_found(0.05) is None and empty.cost_per_1000 == 0.0
    kept = score_queue("cascade", "v1", {1: 0.9, 2: 0.1}, {1: False, 2: False})
    assert kept.hits[0.20] == 0 and kept.share_found(0.20) is None


# -- scoring the log -----------------------------------------------------------------


def log_rows() -> list[Prediction]:
    return [
        make_prediction(
            1, 0.95, tier1=0.7, model_risk=95, category="vandalism", cost_usd=0.002, latency_s=6.0
        ),  # fmt: skip
        make_prediction(
            2,
            0.05,
            tier1=0.6,
            model_risk=2,
            category="none",
            cost_usd=0.002,
            gate=["RANGE_WITHOUT_CATEGORY"],
            latency_s=8.0,
        ),  # fmt: skip
        make_prediction(3, 0.55, skipped="gate", gate=["RANGE_OUTSIDE_CHANGE"], cost_usd=0.002),
        make_prediction(4, 0.52, skipped="queue_full", latency_s=0.5),
        make_prediction(5, 0.51, skipped="queue_full"),
        make_prediction(6, 0.10),
        make_prediction(1, 0.70, config="tier1", version="t1"),
        make_prediction(2, 0.60, config="tier1", version="t1"),
        make_prediction(3, 0.55, config="tier1", version="t1"),
        make_prediction(9, 0.99),  # no outcome yet
    ]


OUTCOMES = {1: True, 2: False, 3: True, 4: False, 5: True, 6: True}


def test_the_log_is_scored_per_configuration_and_version_over_edits_with_an_outcome():
    older = [make_prediction(1, 0.3, version="v0"), make_prediction(2, 0.4, version="v0")]
    scores = score(log_rows() + older, OUTCOMES)
    assert [(s.config, s.system_version, s.edits, s.reverted) for s in scores] == [
        ("cascade", "v0", 2, 1),
        ("cascade", "v1", 6, 4),
        ("tier1", "t1", 3, 2),
    ]
    assert score(log_rows(), {}) == []


def test_the_score_counts_the_edits_the_model_read_and_the_reverted_edits_it_did_not_read():
    cascade, tier1 = score(log_rows(), OUTCOMES)
    assert cascade.model_read == 2 and tier1.model_read == 0
    assert cascade.categories == {"vandalism": 1, "none": 1}
    assert cascade.reverted_not_read == 3  # revisions 3, 5 and 6
    assert tier1.reverted_not_read == 2  # revisions 1 and 3


def test_the_score_counts_gate_rules_and_skips_by_name():
    cascade, tier1 = score(log_rows(), OUTCOMES)
    assert cascade.gate == {"RANGE_WITHOUT_CATEGORY": 1, "RANGE_OUTSIDE_CHANGE": 1}
    assert cascade.skipped == {"gate": 1, "queue_full": 2}
    assert tier1.gate == {} and tier1.skipped == {}


def test_the_score_adds_up_the_cost_and_collects_the_latencies():
    cascade, tier1 = score(log_rows(), OUTCOMES)
    assert cascade.cost_usd == pytest.approx(0.006)
    assert cascade.cost_per_1000 == pytest.approx(1.0)
    assert cascade.latencies == [6.0, 8.0, 0.5]
    assert tier1.cost_usd == 0.0 and tier1.latencies == []


def test_the_score_of_the_log_orders_the_queue_by_the_final_risk():
    cascade, tier1 = score(log_rows(), OUTCOMES)
    # Queue: 1, 3, 4, 5, 6, 2. A fifth of six edits is two.
    assert cascade.depth[0.20] == 2 and cascade.hits[0.20] == 2
    assert cascade.bottom_half == 2  # revisions 5 and 6
    assert tier1.hits[0.20] == 1 and tier1.bottom_half == 1


def test_the_score_can_be_limited_to_a_set_of_edits():
    cascade, tier1 = score(log_rows(), OUTCOMES, only={1, 2, 9})
    assert (cascade.edits, cascade.reverted, cascade.model_read) == (2, 1, 2)
    assert tier1.edits == 2 and cascade.skipped == {}


# -- a smaller slice -----------------------------------------------------------------


def slice_rows() -> list[Prediction]:
    return [
        make_prediction(1, 0.10, tier1=0.90, model_risk=3, category="none", cost_usd=0.002),
        make_prediction(2, 0.95, tier1=0.15, model_risk=97, category="vandalism", cost_usd=0.003),
        make_prediction(3, 0.20),
        make_prediction(4, 0.05),
    ]


SLICE_OUTCOMES = {1: False, 2: True, 3: False, 4: False}


def test_a_model_answer_below_the_threshold_is_set_aside_for_the_tier_one_score():
    s = at_share(slice_rows(), 0.5, SLICE_OUTCOMES, "cascade@10")
    # Revision 2 keeps tier 1 (0.15), revision 1 keeps the model (0.10): 3, 2, 1, 4.
    assert (s.config, s.system_version) == ("cascade@10", "v1")
    assert s.model_read == 1 and s.cost_usd == pytest.approx(0.002)
    assert s.hits[0.20] == 0 and s.bottom_half == 0
    # With the threshold at revision 2's own score both answers count: 2, 3, 1, 4.
    both = at_share(slice_rows(), 0.15, SLICE_OUTCOMES, "cascade")
    assert both.model_read == 2 and both.cost_usd == pytest.approx(0.005)
    assert both.hits[0.20] == 1


def test_with_a_threshold_no_edit_reaches_the_queue_is_tier_one_alone():
    rows = slice_rows()
    alone = at_share(rows, 2.0, SLICE_OUTCOMES, "cascade@0")
    by_tier1 = score_queue("cascade@0", "v1", {p.rev_id: p.verdict.tier1 for p in rows}, SLICE_OUTCOMES)  # fmt: skip
    assert (alone.model_read, alone.cost_usd) == (0, 0.0)
    assert (alone.hits, alone.bottom_half) == (by_tier1.hits, by_tier1.bottom_half)
    assert alone.bottom_half == 1  # tier 1 has the reverted revision 2 in the bottom half
    assert at_share([], 0.5, {}, "cascade").edits == 0


# -- arrival order, percentiles and intervals ----------------------------------------


def test_arrival_order_finds_the_reverted_edits_in_proportion_to_the_depth():
    s = arrival_order(1000, 60)
    assert (s.config, s.edits, s.reverted) == ("arrival", 1000, 60)
    assert s.depth == {0.02: 20, 0.05: 50, 0.10: 100, 0.20: 200}
    assert s.hits == {0.02: 1, 0.05: 3, 0.10: 6, 0.20: 12}
    assert s.bottom_half == 30
    assert s.share_found(0.10) == pytest.approx(0.10)
    assert arrival_order(30, 5).depth == score_queue("cascade", "v1", RISKS, REVERTED).depth


def test_a_percentile_is_the_smallest_value_with_that_share_at_or_below_it():
    values = [3, 1, 2, 10, 5, 4, 7, 6, 9, 8]
    found = [percentile(values, q) for q in (0.0, 0.5, 0.51, 0.95, 1.0)]
    assert found == [1, 5, 6, 10, 10]


def test_a_percentile_of_no_values_is_none_and_of_one_value_is_that_value():
    assert percentile([], 0.95) is None
    assert percentile([2.5], 0.95) == 2.5
    values = [9.0, 2.0, 4.0]
    assert percentile(values, 0.5) == 4.0 and values == [9.0, 2.0, 4.0]


@pytest.mark.parametrize(
    ("successes", "total", "low", "high"),
    [
        (5, 10, 0.2366, 0.7634),
        (0, 10, 0.0, 0.2775),
        (50, 100, 0.4038, 0.5962),
    ],
)
def test_the_wilson_interval_of_known_counts(successes, total, low, high):
    assert wilson_interval(successes, total) == pytest.approx((low, high), abs=5e-5)


def test_the_wilson_interval_of_no_trials_is_the_whole_range():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_a_share_with_no_known_threshold_is_refused(tmp_path):
    cascade = make_cascade(tmp_path, share=0.12)
    with pytest.raises(ValueError, match="Known shares"):
        cascade.in_slice(0.5)
