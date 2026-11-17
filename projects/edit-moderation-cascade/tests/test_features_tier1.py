"""Tier 1: the features an event carries, and the classifier trained on them."""

from __future__ import annotations

import math

import numpy as np
import pytest

from editwatch.features import FEATURES, features
from editwatch.tier1 import SHARES, train

from conftest import make_edit, training_edits


def named(edit) -> dict[str, float]:
    return dict(zip(FEATURES, features(edit), strict=True))


# -- features -------------------------------------------------------------------------


def test_features_are_one_finite_number_per_name_in_order():
    edits = [
        make_edit(5),
        make_edit(6, parent_id=0, old_len=0, new_len=0, comment="", title=""),
        make_edit(7, old_len=900_000, new_len=0, comment="/* A */ " + "X" * 900, title="T" * 300),
    ]
    for edit in edits:
        row = features(edit)
        assert len(row) == len(FEATURES)
        assert all(isinstance(value, float) and math.isfinite(value) for value in row)
    assert (
        named(make_edit(5, temp=True))["temp"]
        == features(make_edit(5, temp=True))[FEATURES.index("temp")]
    )


def test_a_removal_has_a_negative_delta_and_a_share_of_the_page_removed():
    row = named(make_edit(5, old_len=4000, new_len=1000))
    assert row["delta"] == -3000.0
    assert row["shrink_share"] == 0.75
    assert row["abs_delta_log"] == pytest.approx(math.log1p(3000))
    assert row["new_len_log"] < row["old_len_log"]


def test_an_addition_has_a_positive_delta_and_removes_no_share_of_the_page():
    row = named(make_edit(5, old_len=1000, new_len=4000))
    assert row["delta"] == 3000.0
    assert row["shrink_share"] == 0.0
    assert row["abs_delta_log"] == pytest.approx(math.log1p(3000))
    assert row["new_len_log"] > row["old_len_log"]


def test_a_very_large_change_is_capped_in_delta_but_not_in_its_logarithm():
    blanked = named(make_edit(5, old_len=90_000, new_len=0))
    pasted = named(make_edit(5, old_len=0, new_len=90_000))
    assert (blanked["delta"], pasted["delta"]) == (-5000.0, 5000.0)
    assert blanked["abs_delta_log"] == pytest.approx(math.log1p(90_000))
    assert blanked["shrink_share"] == 1.0


@pytest.mark.parametrize(
    ("fields", "feature", "value"),
    [
        ({"comment": "Undid revision 4 by [name]"}, "comment_is_revert", 1.0),
        ({"comment": ""}, "comment_empty", 1.0),
        ({"comment": "/* Early life */"}, "comment_section_only", 1.0),
        ({"comment": "source: http://example.org/report"}, "comment_has_link", 1.0),
        ({"comment": "linked [[Example Bridge]]"}, "comment_has_link", 1.0),
        ({"temp": True}, "temp", 1.0),
        ({"minor": True}, "minor", 1.0),
        ({"comment": "THIS IS WRONG"}, "comment_upper_share", 1.0),
        ({"comment": "typo fixes using AWB"}, "comment_auto", 1.0),
        ({"title": "2019 Example Cup (football)"}, "title_has_year", 1.0),
    ],
)
def test_one_property_of_an_edit_sets_its_feature(fields, feature, value):
    assert named(make_edit(5, **fields))[feature] == value


def test_a_summary_that_is_only_a_section_marker_is_not_empty_and_has_no_length():
    row = named(make_edit(5, comment="/* Early life */"))
    assert (row["comment_empty"], row["comment_len"]) == (0.0, 0.0)
    assert named(make_edit(5, comment="   "))["comment_empty"] == 1.0
    assert named(make_edit(5, comment="x" * 500))["comment_len"] == 300.0


def test_an_ordinary_edit_sets_none_of_the_warning_features():
    row = named(make_edit(5, comment="/* History */ added a reference"))
    for feature in ("comment_is_revert", "comment_empty", "comment_section_only", "comment_has_link",
                    "temp", "minor", "comment_auto", "title_has_year", "title_has_brackets"):  # fmt: skip
        assert row[feature] == 0.0, feature
    assert row["comment_len"] == float(len("added a reference"))


def test_a_small_gap_to_the_parent_revision_gives_a_smaller_value_than_a_large_one():
    moments_ago = named(make_edit(5000, parent_id=4998))["parent_gap_log"]
    long_ago = named(make_edit(5000, parent_id=12))["parent_gap_log"]
    no_parent = named(make_edit(5000, parent_id=0))["parent_gap_log"]
    assert moments_ago == pytest.approx(math.log1p(2))
    assert moments_ago < long_ago < no_parent


def test_the_hour_and_the_weekday_come_from_the_time_of_the_edit():
    # T0 is Sunday 1 November 2026, 00:00 UTC.
    row = named(make_edit(5, minute=13 * 60 + 59))
    assert (row["hour"], row["weekday"]) == (13.0, 6.0)
    row = named(make_edit(5, minute=24 * 60 + 5))
    assert (row["hour"], row["weekday"]) == (0.0, 0.0)


# -- the classifier -------------------------------------------------------------------


def test_training_learns_which_edits_were_reverted(trained):
    tier1, edits, reverted = trained
    scores = tier1.scores(edits)
    bad = np.array([reverted[e.rev_id] for e in edits])
    assert scores[bad].mean() - scores[~bad].mean() > 0.4


def test_what_was_learned_carries_over_to_edits_not_trained_on(trained):
    tier1, _, _ = trained
    removal = make_edit(
        77_000_000, parent_id=76_999_995, temp=True, old_len=6000, new_len=3100, comment=""
    )
    addition = make_edit(77_000_010, parent_id=76_950_000, old_len=6000, new_len=6200, comment="/* Section 2 */ added a reference")  # fmt: skip
    assert tier1.score(removal) - tier1.score(addition) > 0.4


def test_training_records_how_many_edits_it_saw_and_the_share_reverted(trained):
    tier1, edits, reverted = trained
    assert tier1.trained_on == 300
    assert tier1.reverted_share == pytest.approx(sum(reverted.values()) / 300)
    assert 0.15 < tier1.reverted_share < 0.35
    assert tier1.summary()["trained_on"] == 300
    assert tier1.summary()["features"] == list(FEATURES)


def test_training_uses_only_edits_with_an_outcome():
    edits, reverted = training_edits(120)
    known = {rev: was for rev, was in reverted.items() if rev % 20 == 0}
    assert train(edits, known).trained_on == 60


def test_training_refuses_fewer_than_fifty_edits_with_an_outcome():
    edits, reverted = training_edits(120)
    with pytest.raises(ValueError, match="at least 50"):
        train(edits[:49], reverted)
    few = {e.rev_id: reverted[e.rev_id] for e in edits[:49]}
    with pytest.raises(ValueError, match="at least 50"):
        train(edits, few)


@pytest.mark.parametrize("outcome", [True, False])
def test_training_refuses_a_set_with_one_outcome_only(outcome):
    edits, _ = training_edits(60)
    with pytest.raises(ValueError, match="both reverted and kept"):
        train(edits, {e.rev_id: outcome for e in edits})


def test_the_threshold_does_not_rise_as_the_share_grows(trained):
    tier1, edits, _ = trained
    thresholds = [tier1.threshold(share) for share in SHARES]
    assert thresholds == sorted(thresholds, reverse=True)
    assert thresholds[0] > thresholds[-1]
    assert tier1.threshold(0.2001) == tier1.threshold(0.20)
    with pytest.raises(ValueError, match="No slice threshold"):
        tier1.threshold(0.19)


def test_the_scores_of_no_edits_are_empty(trained):
    assert len(trained[0].scores([])) == 0


def test_scores_are_probabilities_one_per_edit_and_match_scoring_one_at_a_time(trained):
    tier1, edits, _ = trained
    some = edits[:12]
    scores = tier1.scores(some)
    assert scores.shape == (12,)
    assert ((scores >= 0) & (scores <= 1)).all()
    assert [tier1.score(e) for e in some] == pytest.approx(list(scores))
    assert isinstance(tier1.score(some[0]), float)
