"""What the desk may write to a tracker, and the settings that decide it."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import httpx
import pytest
import yaml

from triagedesk.act import MARKER, NotAllowed, Plan, Writer, plan
from triagedesk.config import (
    CONFIG_NAME,
    GroupConfig,
    Settings,
    load_settings,
    settings_from_mapping,
    to_mapping,
)
from triagedesk.models import LabelPick, Proposal

from conftest import REPO, T0

# -- act -----------------------------------------------------------------------------

SURE = Proposal(
    labels=[
        LabelPick(name="topic:rendering", confidence="high"),
        LabelPick(name="topic:audio", confidence="medium"),
        LabelPick(name="platform:linux", confidence="high"),
        LabelPick(name="topic:physics", confidence="low"),
    ],
    duplicate_of=3,
    duplicate_confidence="high",
    source="model",
)


def tracker(status: int = 200, comments: list[dict] | None = None):
    """A fake tracker that records every request it gets."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(status, json=comments or [])
        return httpx.Response(status, json={})

    return requests, httpx.MockTransport(handler)


@pytest.fixture
def allowed(settings):
    return replace(settings, apply_allow=(REPO,))


def test_a_plan_includes_only_the_high_confidence_labels():
    assert plan(7, SURE).add_labels == ["platform:linux", "topic:rendering"]


def test_a_high_confidence_duplicate_gets_a_comment_that_says_nothing_was_closed():
    comment = plan(7, SURE).comment
    assert comment.startswith(MARKER)
    assert "#3" in comment
    assert "nothing has been closed" in comment


def test_the_comment_quotes_the_matching_passage_of_the_named_issue_only():
    passages = {3: "The shader cache is rebuilt.", 4: "Tiles shimmer while panning."}
    comment = plan(7, SURE, passages).comment
    assert "> The shader cache is rebuilt." in comment
    assert "Tiles shimmer" not in comment


@pytest.mark.parametrize(
    "proposal",
    [
        Proposal(duplicate_of=3, duplicate_confidence="medium"),
        Proposal(labels=[LabelPick(name="topic:audio", confidence="high")]),
    ],
    ids=["medium duplicate", "no duplicate"],
)
def test_there_is_no_comment_without_a_high_confidence_duplicate(proposal):
    assert plan(7, proposal).comment is None


def test_a_plan_is_empty_when_it_has_no_label_and_no_comment():
    unsure = Proposal(
        labels=[LabelPick(name="topic:audio", confidence="medium")],
        duplicate_of=3,
        duplicate_confidence="medium",
    )
    assert plan(7, unsure).empty
    assert Plan(number=7).empty
    assert not Plan(number=7, add_labels=["topic:audio"]).empty
    assert not Plan(number=7, comment="A note.").empty
    assert not plan(7, SURE).empty


def test_an_answer_the_gate_changed_is_not_acted_on():
    changed = SURE.model_copy(update={"gate": ["LABEL_NOT_IN_TAXONOMY"]})
    assert plan(7, changed).empty


def test_the_classifier_standing_in_for_an_unavailable_model_is_not_acted_on():
    # At the spend cap, or when the model call fails, the classifier's answer is kept
    # for a person. It must not be applied, however sure the classifier is.
    stand_in = SURE.model_copy(update={"person": ["model_unavailable"], "source": "classifier"})
    assert plan(7, stand_in).empty


def test_the_writer_refuses_a_repository_that_is_not_on_the_allow_list(settings):
    _, transport = tracker()
    with pytest.raises(NotAllowed, match="apply_allow"):
        Writer(settings, "test-token", transport=transport)
    with pytest.raises(NotAllowed, match="apply_allow"):
        Writer(
            replace(settings, apply_allow=("example/sandbox",)), "test-token", transport=transport
        )


@pytest.mark.parametrize("token", [None, ""])
def test_the_writer_refuses_to_work_without_a_token(allowed, token):
    _, transport = tracker()
    with pytest.raises(NotAllowed, match="token"):
        Writer(allowed, token, transport=transport)


def test_the_writer_posts_the_labels_and_one_comment_and_nothing_else(allowed):
    requests, transport = tracker()
    planned = plan(7, SURE)
    done = Writer(allowed, "test-token", transport=transport).apply(planned)
    assert [(r.method, r.url.host, r.url.path) for r in requests] == [
        ("POST", "api.github.com", "/repos/example/engine/issues/7/labels"),
        ("GET", "api.github.com", "/repos/example/engine/issues/7/comments"),
        ("POST", "api.github.com", "/repos/example/engine/issues/7/comments"),
    ]
    assert json.loads(requests[0].content) == {"labels": ["platform:linux", "topic:rendering"]}
    assert json.loads(requests[2].content) == {"body": planned.comment}
    assert not {r.method for r in requests} & {"PATCH", "DELETE", "PUT"}
    assert all(r.headers["authorization"] == "Bearer test-token" for r in requests)
    assert all(r.headers["user-agent"] == allowed.user_agent for r in requests)
    assert done == ["labels:platform:linux,topic:rendering", "comment"]


def test_the_writer_never_posts_a_second_comment_on_an_issue(allowed):
    from triagedesk.act import MARKER

    earlier = [{"body": "Thanks for the report."}, {"body": f"{MARKER}\n\nThis looks like #3."}]
    requests, transport = tracker(comments=earlier)
    done = Writer(allowed, "test-token", transport=transport).apply(plan(7, SURE))
    assert [r.method for r in requests] == ["POST", "GET"]
    assert done[-1] == "comment:already_there"


def test_a_quoted_passage_cannot_notify_the_person_it_mentions():
    planned = plan(
        7, SURE, {SURE.duplicate_of: "Thanks @somebody, the mesh still flickers @ 60 Hz."}
    )
    assert "@somebody" not in planned.comment
    assert "somebody, the mesh still flickers @ 60 Hz." in planned.comment


def test_the_writer_sends_nothing_for_an_empty_plan(allowed):
    requests, transport = tracker()
    assert Writer(allowed, "test-token", transport=transport).apply(Plan(number=7)) == []
    assert requests == []


def test_a_refused_label_request_raises_and_no_comment_is_posted(allowed):
    requests, transport = tracker(status=403)
    with pytest.raises(httpx.HTTPStatusError):
        Writer(allowed, "test-token", transport=transport).apply(plan(7, SURE))
    assert [r.url.path for r in requests] == ["/repos/example/engine/issues/7/labels"]


# -- config --------------------------------------------------------------------------

CUSTOM = Settings(
    repo=REPO,
    groups=(
        GroupConfig("area", "topic:", 12, "The part of the engine at fault."),
        GroupConfig("platform", "platform:", 4),
    ),
    keywords={"topic:dotnet": ("c#", "csharp")},
    history_start=date(2024, 1, 1),
    cut=date(2026, 2, 1),
    test_end=date(2026, 6, 30),
    held_out_start=date(2026, 5, 1),
    live_cut=date(2026, 7, 1),
    truth_after_days=7,
    candidates=10,
    model_candidates=3,
    retrieval="vector",
    duplicate_confident=0.9,
    duplicate_propose=0.8,
    daily_cap_usd=0.1,
    apply_allow=("example/sandbox",),
)

CONFIG_FILE = """\
repo: example/engine
groups:
  area: {prefix: "topic:", min_issues: 3, hint: Which part of the engine is at fault.}
  platform: {prefix: "platform:"}
keywords:
  "topic:dotnet": ["C#", csharp]
cut: 2026-03-01
held_out_start: 2026-04-01
test_end: 2026-05-01
daily_cap_usd: 0.25
apply_allow: [example/sandbox]
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A project home with a settings file, and none of the operator variables set."""
    for name in ("TRIAGEDESK_HOME", "TRIAGEDESK_DAILY_CAP_USD", "TRIAGEDESK_USER_AGENT"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / CONFIG_NAME).write_text(CONFIG_FILE, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("original", [CUSTOM, Settings()], ids=["custom", "defaults"])
def test_settings_written_as_a_mapping_read_back_the_same(original):
    assert settings_from_mapping(to_mapping(original)) == original


def test_settings_survive_a_trip_through_yaml():
    text = yaml.safe_dump(to_mapping(CUSTOM))
    assert settings_from_mapping(yaml.safe_load(text)) == CUSTOM


def test_an_unknown_key_is_an_error_that_names_the_key():
    with pytest.raises(ValueError, match="retreival"):
        settings_from_mapping({"repo": REPO, "retreival": "words"})


@pytest.mark.parametrize(
    "dates",
    [
        {"cut": "2026-06-01", "held_out_start": "2026-05-01", "test_end": "2026-07-01"},
        {"cut": "2026-05-01", "held_out_start": "2026-08-01", "test_end": "2026-07-01"},
    ],
    ids=["cut after held_out_start", "held_out_start after test_end"],
)
def test_dates_out_of_order_are_an_error(dates):
    with pytest.raises(ValueError, match="cut <= held_out_start <= test_end"):
        settings_from_mapping(dates)


def test_a_repo_without_a_slash_is_an_error():
    with pytest.raises(ValueError, match="owner/name"):
        settings_from_mapping({"repo": "engine"})


def test_load_settings_reads_the_file_in_its_home(home):
    settings = load_settings(home)
    assert settings.home == home
    assert settings.repo == REPO
    assert settings.groups == (
        GroupConfig("area", "topic:", 3, "Which part of the engine is at fault."),
        GroupConfig("platform", "platform:", 30, ""),
    )
    assert settings.keywords == {"topic:dotnet": ("c#", "csharp")}
    assert (settings.cut, settings.held_out_start, settings.test_end) == (
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    )
    assert settings.daily_cap_usd == 0.25
    assert settings.apply_allow == ("example/sandbox",)


def test_load_settings_without_a_file_gives_the_defaults_for_that_home(home):
    (home / CONFIG_NAME).unlink()
    assert load_settings(home) == Settings(home=home)


def test_the_environment_sets_the_daily_cap_and_the_user_agent(home, monkeypatch):
    monkeypatch.setenv("TRIAGEDESK_DAILY_CAP_USD", "0.05")
    monkeypatch.setenv("TRIAGEDESK_USER_AGENT", "triage-desk-tests/0")
    settings = load_settings(home)
    assert settings.daily_cap_usd == 0.05
    assert settings.user_agent == "triage-desk-tests/0"
    assert settings.repo == REPO


def test_group_of_goes_by_the_configured_prefix(settings):
    assert settings.group_of("topic:audio") == "area"
    assert settings.group_of("platform:linux") == "platform"
    assert settings.group_of("bug") is None


def test_truth_is_due_after_the_waiting_period(settings):
    assert settings.truth_due(T0) == T0 + timedelta(days=14)
    assert replace(settings, truth_after_days=3).truth_due(T0) == T0 + timedelta(days=3)


def test_recordings_of_real_issues_stay_under_var(settings):
    local = settings.home / "var"
    assert settings.local_cassette_dir.is_relative_to(local)
    assert settings.db_path.is_relative_to(local)
    assert not settings.cassette_dir.is_relative_to(local)
