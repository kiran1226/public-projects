"""The invented tracker in data/fixture, and the demo that runs on it without a key."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from triagedesk.cli import app
from triagedesk.demo import fixture_desk, fixture_settings, load_fixture, run_demo
from triagedesk.embed import HashEmbedder
from triagedesk.llm import DEFAULT_EFFORT, DEFAULT_MODEL, CassetteProvider
from triagedesk.truth import RESOLVED

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_LABELS = {
    "topic:editor", "topic:rendering", "topic:physics", "topic:audio", "topic:gui",
    "topic:scripting", "platform:windows", "platform:linux", "platform:web",
    "platform:android", "bug", "enhancement", "needs testing",
}  # fmt: skip


@pytest.fixture(scope="module")
def settings():
    return fixture_settings(ROOT)


@pytest.fixture(scope="module")
def recorded(settings):
    """The demo, replayed from the committed recording. No key, no network."""
    provider = CassetteProvider(
        settings.cassette_dir / "fixture.jsonl",
        DEFAULT_MODEL,
        params={"effort": DEFAULT_EFFORT, "max_tokens": 4000},
    )
    desk = fixture_desk(settings, HashEmbedder(), provider)
    predictions, outcomes, items, scores = run_demo(desk, ("rules", "classifier", "model"))
    return desk, provider, predictions, outcomes, items, scores


def test_the_fixture_has_72_history_issues_and_12_arrivals(settings):
    issues, timelines = load_fixture(settings.fixture_dir)
    assert [i.number for i in issues] == list(range(1, 85))
    assert sum(i.created_at.date() < settings.cut for i in issues) == 72
    assert all(a.created_at < b.created_at for a, b in zip(issues, issues[1:], strict=False))
    assert {label for i in issues for label in i.labels} <= ALLOWED_LABELS
    assert sorted(timelines) == [i.number for i in issues if i.closed_as_duplicate]


def test_the_fixture_holds_nothing_that_points_at_a_real_person_or_site(settings):
    text = (settings.fixture_dir / "issues.jsonl").read_text("utf-8")
    assert "http" not in text.lower()
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    assert not re.search(r"(?<![\w`])@[A-Za-z][\w-]+", text)
    keys = {key for line in text.splitlines() for key in json.loads(line)}
    assert keys == {
        "number", "title", "body", "created_at", "updated_at", "closed_at", "state",
        "state_reason", "labels",
    }  # fmt: skip


def test_every_fixture_duplicate_resolves_to_an_earlier_issue(recorded):
    desk, _, _, _, _, _ = recorded
    from triagedesk.truth import collect

    outcomes = collect(desk.store, desk.taxonomy, desk.store.issues())
    duplicates = [o for o in outcomes if o.duplicate]
    assert len(duplicates) == 9
    assert all(o.original_status == RESOLVED and o.original < o.number for o in duplicates)
    assert {o.number: o.original for o in duplicates}[82] == 75


def test_the_fixture_taxonomy_has_six_areas_and_four_platforms(recorded):
    desk = recorded[0]
    assert len(desk.taxonomy.names("area")) == 6
    assert len(desk.taxonomy.names("platform")) == 4
    assert desk.classifier.trained_on == 72


def test_the_demo_replays_every_model_answer_from_the_recording(recorded):
    _, provider, predictions, _, _, _ = recorded
    assert provider.misses == 0 and provider.hits == 12
    model = [p for p in predictions if p.config == "model"]
    assert len(model) == 12 and all(p.proposal.source == "model" for p in model)


def test_the_hostile_issue_gets_ordinary_labels_and_no_duplicate(recorded):
    desk, _, predictions, outcomes, _, _ = recorded
    issue = desk.store.issue(76)
    assert "priority:critical" in issue.body and "duplicate of #1" in issue.body
    answer = next(p for p in predictions if p.number == 76 and p.config == "model").proposal
    assert {pick.name for pick in answer.labels} <= set(desk.taxonomy.names())
    assert "topic:editor" in answer.names()
    assert answer.duplicate_of is None
    assert outcomes[76].labels == ["topic:editor"]


def test_the_model_finds_duplicates_and_calls_no_new_issue_a_duplicate(recorded):
    scores = {s.config: s for s in recorded[5]}
    assert scores["model"].duplicates == 3
    assert scores["model"].flagged_right >= 2
    assert scores["model"].false_duplicates == 0
    assert scores["rules"].flagged == 0


def test_the_similar_but_different_report_is_not_called_a_duplicate(recorded):
    predictions = recorded[2]
    answer = next(p for p in predictions if p.number == 81 and p.config == "model")
    assert 20 in answer.candidates[:5], "the look-alike was shown to the model"
    assert answer.proposal.duplicate_of is None


def test_queue_items_show_passages_that_are_in_the_earlier_issue(recorded):
    desk, _, _, _, items, _ = recorded
    assert len(items) == 12
    for item in items:
        for earlier in item.earlier:
            source = f"{earlier.issue.title}\n{earlier.issue.body}"
            assert earlier.passage.removesuffix(" …") in source


def test_the_demo_command_runs_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TRIAGEDESK_HOME", raising=False)
    result = CliRunner().invoke(app, ["demo", "--stub-embedder"])
    assert result.exit_code == 0, result.output
    assert "#76" in result.output and "maintainers:" in result.output
    assert "Model with definitions and candidates" in result.output
    assert "model_unavailable" not in result.output


def test_the_recording_holds_only_answers_about_the_invented_tracker(settings):
    lines = (settings.cassette_dir / "fixture.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 24
    for line in lines:
        entry = json.loads(line)
        assert set(entry) == {"key", "schema", "output", "stats"}
        assert entry["schema"] == "Triage"
