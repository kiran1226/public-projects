"""The two no-model tiers: keyword rules and the TF-IDF classifier."""

from __future__ import annotations

import random
from dataclasses import replace

import numpy as np
import pytest

from triagedesk.classifier import (
    MIN_POSITIVES,
    LabelClassifier,
    Thresholds,
    choose_thresholds,
)
from triagedesk.models import Issue, LabelDefinition, Taxonomy
from triagedesk.rules import Rules
from triagedesk.text import words

from conftest import REPO, make_issue


def taxonomy_of(*names: str) -> Taxonomy:
    groups = {"topic": "area", "platform": "platform"}
    return Taxonomy(
        repo=REPO,
        labels=[LabelDefinition(name=name, group=groups[name.split(":")[0]]) for name in names],
    )


# -- rules ---------------------------------------------------------------------------


@pytest.fixture
def rules(settings):
    settings = replace(settings, keywords={"topic:dotnet": ("c#", "csharp")})
    return Rules(settings, taxonomy_of("topic:dotnet", "topic:shaders", "platform:web"))


def found(rules: Rules, title: str, body: str = "") -> list[tuple[str, str]]:
    return [(pick.name, pick.confidence) for pick in rules.labels(title, body)]


def test_the_labels_own_name_in_the_title_gives_high_confidence_once(rules):
    body = "The shaders flicker. Reloading the shaders does not help."
    assert found(rules, "Shaders flicker after a resize", body) == [("topic:shaders", "high")]


def test_the_name_twice_in_the_problem_text_gives_medium_confidence(rules):
    body = "The shaders flicker. Reloading the shaders does not help."
    assert found(rules, "Flicker after a resize", body) == [("topic:shaders", "medium")]


def test_the_name_once_in_the_body_gives_nothing(rules):
    assert found(rules, "Flicker after a resize", "The shaders flicker until a reload.") == []


def test_mentions_in_a_setup_section_do_not_count(rules):
    body = (
        "### System information\nWeb export, web browser, web server.\n"
        "### Issue description\nThe button does nothing."
    )
    assert found(rules, "Button does nothing", body) == []


@pytest.mark.parametrize(
    ("title", "matches"),
    [
        ("Web export shows a black screen", True),
        ("WEB export shows a black screen", True),
        ("Black screen (web) after export", True),
        ("Input differs between web/desktop", True),
        ("Webp textures import with wrong colours", False),
        ("Webview closes on resume", False),
        ("Web-export template is missing", False),
    ],
)
def test_a_keyword_must_stand_alone_and_matches_in_any_case(rules, title, matches):
    assert found(rules, title) == ([("platform:web", "high")] if matches else [])


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("C# script loses its exported values", "", [("topic:dotnet", "high")]),
        ("Build fails", "The csharp project fails. Other CSharp projects build.", [("topic:dotnet", "medium")]),
        ("C#12 syntax is rejected", "", []),
    ],
)  # fmt: skip
def test_configured_keywords_count_beside_the_labels_own_name(rules, title, body, expected):
    assert found(rules, title, body) == expected


def test_one_issue_can_match_several_labels(rules):
    assert found(rules, "C# shaders fail on web") == [
        ("topic:dotnet", "high"),
        ("topic:shaders", "high"),
        ("platform:web", "high"),
    ]


# -- classifier ----------------------------------------------------------------------

VOCABULARY = {
    "topic:rendering": "shader vulkan texture lighting shadow mesh viewport material".split(),
    "topic:audio": "sound mixer playback volume stream speaker reverb sample".split(),
    "topic:physics": "collision rigidbody gravity joint raycast velocity impulse solver".split(),
}
FILLER = "editor project scene node window opens after update version build crash freeze".split()
RARE = "topic:network"


def generated_issues(per_label: int = 45, seed: int = 7) -> list[Issue]:
    """Reports whose label can be read off their vocabulary, plus three of a rare label."""
    rng = random.Random(seed)
    issues = []
    for _ in range(per_label):
        for label, vocabulary in VOCABULARY.items():
            number = len(issues) + 1
            title = " ".join(rng.sample(vocabulary, 2) + rng.sample(FILLER, 2))
            body = " ".join(rng.sample(vocabulary, 4) + rng.sample(FILLER, 5))
            issues.append(make_issue(number, title, body, day=number / 10, labels=[label]))
    for offset in range(MIN_POSITIVES - 2):
        number = len(issues) + 1
        issues.append(
            make_issue(number, "socket packet lost", "peer latency packet socket", day=offset, labels=[RARE])
        )  # fmt: skip
    return issues


@pytest.fixture(scope="module")
def trained() -> LabelClassifier:
    return LabelClassifier(taxonomy_of(*VOCABULARY, RARE)).fit(generated_issues())


UNSEEN = [
    ("topic:rendering", "Shadow missing on a mesh", "The texture and the lighting are wrong in the viewport."),
    ("topic:audio", "Playback stops early", "The mixer drops the stream and the speaker stays silent."),
    ("topic:physics", "Rigidbody falls through the floor", "Collision is missed when gravity and velocity are high."),
]  # fmt: skip


def test_the_classifier_learns_labels_with_distinct_vocabularies(trained):
    for label, title, body in UNSEEN:
        scores = trained.scores(make_issue(9001, title, body, day=400))
        assert set(scores) == {*VOCABULARY, RARE}
        assert scores[label] > 0.8
        assert all(score < 0.2 for name, score in scores.items() if name != label)
        assert trained.picks(scores)[0].name == label


def test_thresholds_come_from_the_newest_tenth_and_are_ordered(trained):
    thresholds = trained.thresholds
    assert 0 < thresholds.propose <= thresholds.confident <= 1
    assert trained.trained_on == len(generated_issues())
    assert thresholds.validation["issues"] == len(generated_issues()) // 10


def test_an_unfitted_classifier_has_no_scores():
    classifier = LabelClassifier(taxonomy_of(*VOCABULARY))
    assert classifier.scores(make_issue(1, "Shader fails", "The shader fails.")) == {}


def test_a_label_with_too_few_examples_scores_zero(trained):
    issue = make_issue(9001, "socket packet lost", "peer latency packet socket", day=400)
    assert trained.scores(issue)[RARE] == 0.0
    assert trained.top_terms(RARE, issue) == []


def test_picks_map_probabilities_to_confidence_most_probable_first():
    classifier = LabelClassifier(taxonomy_of(*VOCABULARY))
    classifier.thresholds = Thresholds(propose=0.4, confident=0.8)
    scores = {"topic:physics": 0.4, "topic:audio": 0.39, "topic:rendering": 0.8}
    picks = [(pick.name, pick.confidence) for pick in classifier.picks(scores)]
    assert picks == [("topic:rendering", "high"), ("topic:physics", "medium")]


def test_top_terms_are_words_of_the_issue(trained):
    issue = make_issue(9001, "Shader breaks the shadow", "The vulkan texture turns black.", day=400)
    terms = trained.top_terms("topic:rendering", issue)
    assert terms and len(terms) <= 5
    in_issue = set(words(f"{issue.title} {issue.body}"))
    assert all(word in in_issue for term in terms for word in term.split())
    assert len(trained.top_terms("topic:rendering", issue, limit=2)) == 2


def held_out(*groups: tuple[int, float, bool]) -> tuple[np.ndarray, np.ndarray]:
    """One label column from (how many, probability, truly labelled) groups."""
    rows = [(probability, truth) for count, probability, truth in groups for _ in range(count)]
    return np.array([[p] for p, _ in rows]), np.array([[t] for _, t in rows], dtype=bool)


def test_propose_is_the_best_f1_and_confident_the_lowest_with_19_in_20_right():
    # At 0.15 to 0.50: 34 proposed, 30 right (best F1). From 0.55: 20 proposed, all right.
    thresholds = choose_thresholds(
        *held_out((20, 0.9, True), (10, 0.5, True), (4, 0.5, False), (20, 0.1, False))
    )
    assert thresholds.propose == pytest.approx(0.15)
    assert thresholds.confident == pytest.approx(0.55)
    assert thresholds.validation == {
        "issues": 54,
        "labels_present": 30,
        "at_propose": {"proposed": 34, "right": 30},
        "at_confident": {"proposed": 20, "right": 20},
        "confident_target": 0.95,
        "confident_target_met": True,
    }


def test_confident_stays_at_the_top_of_the_grid_without_twenty_proposals():
    thresholds = choose_thresholds(*held_out((10, 0.9, True), (10, 0.2, False)))
    assert thresholds.propose == pytest.approx(0.25)
    assert thresholds.confident == pytest.approx(0.99)
    assert thresholds.validation["confident_target_met"] is False


def test_when_no_threshold_is_right_19_times_in_20_the_most_precise_one_is_used():
    # 0.99 and above: 30 proposed, 27 right (90%). Lower thresholds are less precise.
    thresholds = choose_thresholds(
        *held_out((27, 0.995, True), (3, 0.995, False), (40, 0.6, True), (30, 0.6, False))
    )
    assert thresholds.confident == pytest.approx(0.99)
    assert thresholds.validation["at_confident"] == {"proposed": 30, "right": 27}
    assert thresholds.validation["confident_target_met"] is False


def test_confident_is_never_below_propose():
    # From 0.05 on 100 of 105 proposals are right, yet F1 is best from 0.35 (99 of 99).
    thresholds = choose_thresholds(*held_out((99, 0.9, True), (1, 0.3, True), (5, 0.3, False)))
    assert thresholds.propose == pytest.approx(0.35)
    assert thresholds.confident == pytest.approx(0.35)
