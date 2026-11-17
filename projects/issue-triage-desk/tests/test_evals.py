"""Scoring, on answers and outcomes written out by hand."""

from __future__ import annotations

from datetime import timedelta

import pytest

from triagedesk.duplicates import Families
from triagedesk.evals import Counts, families_from, score, wilson_interval
from triagedesk.models import LabelDefinition, LabelPick, Outcome, Prediction, Proposal, Taxonomy
from triagedesk.truth import NOT_FETCHED, OUTSIDE, RESOLVED

from conftest import T0

TAXONOMY = Taxonomy(
    repo="example/engine",
    labels=[
        LabelDefinition(name="topic:gui", group="area"),
        LabelDefinition(name="topic:audio", group="area"),
        LabelDefinition(name="platform:web", group="platform"),
    ],
)
READ = T0 + timedelta(days=30)


def prediction(number, labels=(), duplicate=None, confidence="high", person=(), candidates=(), config="model",
               version="v1", cost=0.0, source="model", gate=(), **flags) -> Prediction:  # fmt: skip
    return Prediction(
        number=number,
        created_at=T0,
        written_at=T0 + timedelta(hours=1),
        system_version=version,
        config=config,
        proposal=Proposal(
            labels=[LabelPick(name=n, confidence=c) for n, c in labels],
            duplicate_of=duplicate,
            duplicate_confidence=confidence if duplicate is not None else None,
            person=list(person),
            gate=list(gate),
            source=source,
        ),
        candidates=list(candidates),
        cost_usd=cost,
        latency_ms=100,
        **flags,
    )


def outcome(
    number, labels=(), duplicate=False, original=None, status=None, read_at=READ
) -> Outcome:
    if duplicate and status is None:
        status = RESOLVED if original is not None else NOT_FETCHED
    return Outcome(number=number, created_at=T0, read_at=read_at, labels=sorted(labels),
                   duplicate=duplicate, original=original, original_status=status)  # fmt: skip


def one(predictions, outcomes, **kwargs):
    scores = score(predictions, {o.number: o for o in outcomes}, TAXONOMY, "area", **kwargs)
    assert len(scores) == 1
    return scores[0]


def test_counts_give_precision_and_recall_or_none_when_empty():
    counts = Counts(right=3, wrong=1, missed=2)
    assert (counts.proposed, counts.present) == (4, 5)
    assert counts.precision == 0.75 and counts.recall == 0.6
    assert Counts().precision is None and Counts().recall is None


def test_labels_are_scored_as_sets_per_group():
    s = one(
        [
            prediction(
                1, [("topic:gui", "high"), ("topic:audio", "medium"), ("platform:web", "high")]
            )
        ],
        [outcome(1, ["topic:gui", "platform:web"])],
    )
    assert (s.labels["area"].right, s.labels["area"].wrong, s.labels["area"].missed) == (1, 1, 0)
    assert (s.labels["platform"].right, s.labels["platform"].wrong) == (1, 0)
    assert s.all_labels().proposed == 3 and s.all_labels().right == 2


def test_low_confidence_labels_are_not_proposals():
    s = one([prediction(1, [("topic:gui", "low")])], [outcome(1, ["topic:gui"])])
    assert s.labels["area"].proposed == 0 and s.labels["area"].missed == 1


def test_sure_labels_are_counted_apart_from_all_proposals():
    s = one(
        [prediction(1, [("topic:gui", "high"), ("topic:audio", "medium")])],
        [outcome(1, ["topic:audio"])],
    )
    assert (s.sure["area"].right, s.sure["area"].wrong) == (0, 1)
    assert (s.labels["area"].right, s.labels["area"].wrong) == (1, 1)
    assert s.all_labels(sure=True).wrong == 1


@pytest.mark.parametrize(
    ("proposed", "truth", "right", "exact"),
    [
        (["topic:gui"], ["topic:gui"], 1, 1),
        (["topic:gui"], ["topic:gui", "topic:audio"], 1, 0),
        (["topic:gui", "topic:audio"], ["topic:gui"], 0, 0),
        ([], ["topic:gui"], 0, 0),
        (["topic:audio"], ["topic:gui"], 0, 0),
    ],
)
def test_primary_right_means_every_proposed_area_is_on_the_issue(proposed, truth, right, exact):
    s = one([prediction(1, [(n, "high") for n in proposed])], [outcome(1, truth)])
    assert (s.labelled, s.primary_right, s.primary_exact) == (1, right, exact)


def test_an_issue_the_maintainers_left_unlabelled_counts_against_a_proposal():
    s = one(
        [prediction(1, [("topic:gui", "high")]), prediction(2, [])],
        [outcome(1, []), outcome(2, [])],
    )
    assert s.labelled == 0 and s.labels["area"].wrong == 1
    assert s.primary_exact == 1
    assert (s.on_unlabelled, s.sure_on_unlabelled) == (1, 1)


def test_a_right_duplicate_and_the_place_of_the_original_among_the_candidates():
    s = one(
        [prediction(1, duplicate=40, candidates=[41, 40, 39])],
        [outcome(1, duplicate=True, original=40)],
    )
    assert (s.duplicates, s.flagged, s.flagged_right, s.false_duplicates) == (1, 1, 1, 0)
    assert s.hits == {1: 0, 3: 1, 5: 1, 8: 1, 20: 1}
    assert s.duplicate_precision == 1.0 and s.duplicate_recall == 1.0


def test_naming_another_issue_of_the_same_family_is_right():
    families = Families()
    families.link(41, 40)
    s = one(
        [prediction(1, duplicate=41, candidates=[41])],
        [outcome(1, duplicate=True, original=40)],
        families=families,
    )
    assert s.flagged_right == 1 and s.hits[1] == 1


def test_naming_the_wrong_issue_for_a_real_duplicate_is_not_the_worst_error():
    s = one([prediction(1, duplicate=7)], [outcome(1, duplicate=True, original=40)])
    assert (s.flagged_other_issue, s.flagged_right, s.false_duplicates) == (1, 0, 0)
    assert s.duplicate_recall == 0.0


def test_a_new_issue_called_a_duplicate_is_the_worst_error():
    s = one(
        [
            prediction(1, duplicate=40, confidence="high"),
            prediction(2, duplicate=40, confidence="medium", person=["possible_duplicate"]),
        ],
        [outcome(1), outcome(2)],
    )
    assert s.false_duplicates == 2 and s.false_duplicates_sure == 1
    assert s.duplicate_precision == 0.0


@pytest.mark.parametrize("status", [OUTSIDE, NOT_FETCHED, "unresolved"])
def test_a_duplicate_without_a_known_original_is_left_out_of_recall(status):
    s = one(
        [prediction(1, duplicate=40), prediction(2)],
        [outcome(1, duplicate=True, status=status), outcome(2, duplicate=True, status=status)],
    )
    assert (s.duplicates, s.duplicates_unresolved) == (0, 2)
    assert (s.flagged_unresolved, s.false_duplicates) == (1, 0)
    assert s.duplicate_recall is None


def test_issues_handled_alone_are_clean_only_if_nothing_applied_is_wrong():
    s = one(
        [
            prediction(1, [("topic:gui", "high")]),
            prediction(2, [("topic:gui", "high")]),
            prediction(3, [("topic:gui", "high")], duplicate=40),
            prediction(4, [("topic:gui", "medium")], person=["uncertain_label"]),
        ],
        [
            outcome(1, ["topic:gui", "topic:audio"]),
            outcome(2, ["topic:audio"]),
            outcome(3, ["topic:gui"]),
            outcome(4, ["topic:gui"]),
        ],
    )
    assert (s.alone, s.alone_clean, s.person) == (3, 1, 1)
    assert s.reasons == {"uncertain_label": 1}


def test_cost_latency_gate_rules_and_model_answers_add_up():
    s = one(
        [
            prediction(1, cost=0.001, gate=["LABEL_NOT_IN_TAXONOMY"]),
            prediction(
                2,
                cost=0.002,
                source="classifier",
                gate=["LABEL_NOT_IN_TAXONOMY", "TOO_MANY_LABELS"],
            ),
        ],
        [outcome(1), outcome(2)],
    )
    assert s.cost_usd == pytest.approx(0.003) and s.latency_ms == 200
    assert s.gate == {"LABEL_NOT_IN_TAXONOMY": 2, "TOO_MANY_LABELS": 1}
    assert s.model_answers == 1


def test_predictions_without_an_outcome_are_not_scored():
    assert score([prediction(1)], {}, TAXONOMY, "area") == []
    s = one([prediction(1), prediction(2)], [outcome(1)])
    assert s.issues == 1


def test_each_configuration_and_version_gets_its_own_score():
    scores = score(
        [prediction(1, config="rules"), prediction(1, config="model", version="v1"), prediction(1, config="model", version="v2")],
        {1: outcome(1)}, TAXONOMY, "area",
    )  # fmt: skip
    assert [(s.config, s.system_version) for s in scores] == [
        ("model", "v1"),
        ("model", "v2"),
        ("rules", "v1"),
    ]


def test_live_scoring_leaves_out_answers_the_tracker_already_showed():
    predictions = [
        prediction(1, [("topic:gui", "high")]),
        prediction(2, [("topic:gui", "high")], seen_labels=True),
        prediction(3, [("topic:gui", "high")], seen_closed=True),
    ]
    outcomes = [outcome(n, ["topic:gui"]) for n in (1, 2, 3)]
    live = one(predictions, outcomes, live=True)
    assert (live.issues, live.seen_first) == (1, 2)
    assert one(predictions, outcomes).issues == 3


def test_live_scoring_needs_the_prediction_written_before_the_outcome_was_read():
    late = outcome(1, ["topic:gui"], read_at=T0 + timedelta(minutes=30))
    s = one([prediction(1, [("topic:gui", "high")])], [late], live=True)
    assert (s.issues, s.seen_first) == (0, 1)


def test_families_come_from_resolved_outcomes_only():
    families = families_from(
        {
            1: outcome(1, duplicate=True, original=40),
            2: outcome(2, duplicate=True, original=1),
            3: outcome(3, duplicate=True, status=OUTSIDE),
        }
    )
    assert families.same(2, 40) and not families.same(3, 40)


@pytest.mark.parametrize(
    ("successes", "total", "low", "high"),
    [(0, 0, 0.0, 1.0), (6, 243, 0.011, 0.053), (50, 100, 0.404, 0.596), (10, 10, 0.722, 1.0)],
)
def test_wilson_interval(successes, total, low, high):
    interval = wilson_interval(successes, total)
    assert interval[0] == pytest.approx(low, abs=0.002)
    assert interval[1] == pytest.approx(high, abs=0.002)
