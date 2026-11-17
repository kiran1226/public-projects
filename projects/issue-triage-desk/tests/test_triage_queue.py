from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from triagedesk.log import PredictionLog
from triagedesk.models import LabelPick, Outcome, Prediction, Proposal
from triagedesk.queue import Earlier, QueueItem
from triagedesk.review import ReviewLog
from triagedesk.store import Store

from conftest import make_issue, world_issues

pytest.importorskip("streamlit")
APP = Path(__file__).resolve().parents[1] / "app" / "triage_queue.py"
PAGE = "triage_queue_page"
VERSION = "abc1234567"
LABELS = ["platform:web", "topic:audio", "topic:gui", "topic:physics"]
HOSTILE = "<script>alert(1)</script> **bold** [click](http://example.invalid) # heading"


@pytest.fixture(scope="module")
def page():
    """The page's functions, loaded from the script without running the page."""
    spec = importlib.util.spec_from_file_location(PAGE, APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[PAGE] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(PAGE, None)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch) -> Path:
    """Every test reads and writes under its own folder, never the project's."""
    monkeypatch.setenv("TRIAGEDESK_HOME", str(tmp_path))
    monkeypatch.setenv("TRIAGEDESK_REVIEW_LOG", str(tmp_path / "reviews.jsonl"))
    monkeypatch.setenv("TRIAGEDESK_STUB_EMBEDDER", "1")
    return tmp_path


def prediction(number: int = 501, config: str = "model", **proposal) -> Prediction:
    fields = dict(
        labels=[
            LabelPick(name="topic:physics", confidence="high"),
            LabelPick(name="platform:web", confidence="medium"),
        ],
        duplicate_of=312,
        duplicate_confidence="medium",
        person=["no_confident_area", "uncertain_label", "possible_duplicate"],
        gate=["DUPLICATE_NOT_A_CANDIDATE"],
        source="model",
    )
    issue_day = datetime(2026, 4, 3, 9, 30, tzinfo=UTC)
    return Prediction(
        number=number,
        created_at=issue_day,
        written_at=issue_day,
        system_version=VERSION,
        config=config,
        proposal=Proposal(**{**fields, **proposal}),
        candidates=[312, 290],
    )


def uncertain(number: int = 501, title: str = "Rigid bodies fall through the floor", **over):
    """An item that goes to a person, with a proposed duplicate and one more candidate."""
    fields = dict(
        issue=make_issue(number, title, "The body sinks through a static floor.", day=92),
        prediction=prediction(number),
        note="Physics report; the web export is mentioned once.",
        earlier=[
            Earlier(
                make_issue(312, "Bodies tunnel through thin floors", day=40),
                "A fast body passes through a thin static floor.",
                proposed=True,
            ),
            Earlier(
                make_issue(290, "Raycast misses the floor", day=30),
                "The raycast returns nothing.",
            ),
        ],
    )
    return QueueItem(**{**fields, **over})


def sure(number: int = 502, title: str = "Slider ignores the theme font") -> QueueItem:
    """An item the desk would handle alone: one confident label, no duplicate."""
    proposal = dict(
        labels=[LabelPick(name="topic:gui", confidence="high")],
        duplicate_of=None,
        duplicate_confidence=None,
        person=[],
        gate=[],
    )
    return QueueItem(
        issue=make_issue(number, title, "The slider keeps the default font.", day=93),
        prediction=prediction(number, **proposal),
    )


def _script():
    import sys

    sys.modules["triage_queue_page"].render()


def open_page(page, monkeypatch, items, labels=LABELS):
    """Run the page with `load_items` replaced. Returns the app and the calls made."""
    from streamlit.testing.v1 import AppTest

    calls: list[tuple[str, str, int]] = []

    def load_items(source: str, config: str, limit: int) -> list[QueueItem]:
        calls.append((source, config, limit))
        return list(items)[:limit]

    monkeypatch.setattr(page, "load_items", load_items)
    monkeypatch.setattr(page, "load_labels", lambda source: list(labels))
    app = AppTest.from_function(_script, default_timeout=30).run()
    assert not app.exception
    return app, calls


def open_file():
    """Run the script itself, with nothing replaced."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    return app


def texts(app) -> str:
    """Everything the page wrote as Markdown, as one string."""
    blocks = [*app.markdown, *app.caption, *app.info, *app.warning, *app.success, *app.error]
    return "\n".join(block.value for block in blocks)


def plain_texts(app) -> list[str]:
    """Everything the page wrote as plain text."""
    return [block.value for block in app.text]


def metrics(app) -> dict[str, str]:
    return {metric.label: metric.value for metric in app.metric}


def reviews(home: Path) -> ReviewLog:
    return ReviewLog(home / "reviews.jsonl")


# --- the page's functions


@pytest.mark.parametrize(
    ("code", "words"),
    [
        ("no_confident_area", "No area label it is sure of"),
        ("uncertain_label", "A label it is unsure of"),
        ("possible_duplicate", "A duplicate it is unsure of"),
        ("gate_changed_answer", "The gate changed the answer"),
        ("model_unavailable", "No model answer; the classifier's answer is shown"),
        ("no_confident_component", "No component label it is sure of"),
    ],
)
def test_each_reason_for_a_person_is_put_in_plain_words(page, code, words):
    assert page.reason_text(code) == words


def test_text_placed_inside_markdown_is_never_read_as_formatting(page):
    safe = page.plain("topic:gui](http://example.invalid) **b**\n# x :smile: <b>")
    assert "\n" not in safe
    assert "](" not in safe and "**" not in safe and "<b>" not in safe
    assert safe.replace("\\", "") == "topic:gui](http://example.invalid) **b** # x :smile: <b>"
    assert page.plain(None) == ""
    assert page.code("topic:`gui`\nx") == "`topic:'gui' x`"
    chips = page.label_chips(Proposal(labels=[LabelPick(name="a]:red[b", confidence="low")]))
    assert chips == "Proposed labels: :gray-badge[a\\]\\:red\\[b · low]"


def test_the_plan_names_what_apply_mode_would_do(page):
    # The default test answer was changed by the gate, so nothing is applied.
    assert (
        page.plan_text(uncertain().plan) == "In apply mode the desk would: do nothing on its own."
    )
    untouched = uncertain(prediction=prediction(gate=[]))
    assert page.plan_text(untouched.plan) == (
        "In apply mode the desk would: add labels `topic:physics`."
    )
    confident = uncertain(prediction=prediction(duplicate_confidence="high", gate=[]))
    assert page.plan_text(confident.plan) == (
        "In apply mode the desk would: add labels `topic:physics`; post one comment."
    )
    nothing = uncertain(prediction=prediction(labels=[], duplicate_of=None))
    assert page.plan_text(nothing.plan) == "In apply mode the desk would: do nothing on its own."


def test_the_outcome_says_what_the_maintainers_did(page):
    read = datetime(2026, 4, 17, tzinfo=UTC)
    created = datetime(2026, 4, 3, tzinfo=UTC)
    duplicate = Outcome(
        number=501, created_at=created, read_at=read, labels=["topic:physics"], duplicate=True,
        original=312, original_status="resolved",
    )  # fmt: skip
    assert page.outcome_text(duplicate) == (
        "The maintainers: `topic:physics`; closed as a duplicate of #312. Read 2026-04-17."
    )
    open_one = Outcome(number=502, created_at=created, read_at=read)
    assert page.outcome_text(open_one) == (
        "The maintainers: no label of the taxonomy. Read 2026-04-17."
    )
    unresolved = duplicate.model_copy(update={"original": None, "original_status": "unresolved"})
    assert "closed as a duplicate of an issue that was not resolved" in page.outcome_text(
        unresolved
    )


def test_the_demo_replays_with_the_parameters_the_demo_command_records_with(page, tmp_path):
    from triagedesk import cli

    path = tmp_path / "fixture.jsonl"
    ours = page.demo_provider(path)
    theirs = cli._provider(None, cassette=path, live=False, record=False, run=1, concurrency=4)
    assert (ours.model, ours.params) == (theirs.model, theirs.params)
    assert ours.inner is None and not ours.record


def test_the_review_log_is_the_named_file_or_the_one_next_to_the_logs(page, home, monkeypatch):
    from triagedesk.config import load_settings

    assert page.review_log_path(load_settings()) == home / "reviews.jsonl"
    monkeypatch.delenv("TRIAGEDESK_REVIEW_LOG")
    assert page.review_log_path(load_settings()) == (
        home / "data" / "corrections" / "reviews.jsonl"
    )


# --- the page, with its items given


def test_with_no_items_the_page_says_so_and_shows_no_card(page, monkeypatch):
    app, calls = open_page(page, monkeypatch, [])
    assert calls == [(page.DEMO, "model", 20)]
    assert [block.value for block in app.info] == ["The demo tracker has no new arrivals to show."]
    assert not app.button and not app.metric


def test_the_demo_says_that_its_issues_are_invented(page, monkeypatch):
    app, _ = open_page(page, monkeypatch, [uncertain()])
    assert app.radio(key="source").value == page.DEMO
    assert "every issue on this page is invented and the tracker is fictional" in (
        app.caption[0].value
    )


def test_the_live_log_says_that_nothing_was_written_to_the_tracker(page, monkeypatch):
    app, calls = open_page(page, monkeypatch, [uncertain()])
    app.radio(key="source").set_value(page.LIVE).run()
    assert not app.exception
    assert calls[-1] == (page.LIVE, "model", 20)
    assert app.caption[0].value == (
        "Live log of `godotengine/godot`. The desk is in shadow mode: it has written nothing "
        "to the tracker. Its own prediction log is the only thing it writes."
    )
    # In the live log an issue number links to the issue on the tracker.
    assert "[#501](https://github.com/godotengine/godot/issues/501)" in texts(app)


def test_the_live_caption_does_not_claim_shadow_mode_after_apply_was_used(page, monkeypatch, home):
    runs = home / "data" / "log" / "live-runs.jsonl"
    runs.parent.mkdir(parents=True)
    runs.write_text(
        json.dumps({"mode": "shadow", "applied": 0}) + "\n"
        + json.dumps({"mode": "apply", "applied": 2}) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    app, _ = open_page(page, monkeypatch, [uncertain()])
    app.radio(key="source").set_value(page.LIVE).run()
    assert "Apply mode has been used: 2 answers were written" in app.caption[0].value
    assert "shadow mode" not in app.caption[0].value


def test_a_card_shows_the_proposal_beside_its_issue(page, monkeypatch):
    outcome = Outcome(
        number=501,
        created_at=datetime(2026, 4, 3, tzinfo=UTC),
        read_at=datetime(2026, 4, 17, tzinfo=UTC),
        labels=["topic:physics"],
    )
    app, _ = open_page(page, monkeypatch, [uncertain(outcome=outcome)])
    shown, written = texts(app), plain_texts(app)

    assert "**Issue #501** · opened 2026-04-03 00:00 UTC" in shown
    assert "Rigid bodies fall through the floor" in written
    assert "The body sinks through a static floor." in written

    # Labels as chips, each with its confidence.
    assert (
        "Proposed labels: :green-badge[topic\\:physics · high] "
        ":orange-badge[platform\\:web · medium]"
    ) in shown

    # The route, with every reason in plain words, and the gate rules by name.
    assert (
        "**To a person**\n- No area label it is sure of\n- A label it is unsure of\n"
        "- A duplicate it is unsure of"
    ) in shown
    assert "no_confident_area" not in shown
    assert "Gate rules that fired: `DUPLICATE_NOT_A_CANDIDATE`" in shown

    # Earlier issues with the matching passage in quotes; the proposed one is marked.
    assert "Proposed duplicate: **#312**, confidence medium." in shown
    assert "**#312** · the proposed duplicate" in shown
    assert "**#290**" in shown and "**#290** ·" not in shown
    assert (
        "Bodies tunnel through thin floors\n“A fast body passes through a thin static floor.”"
        in written
    )
    assert "Raycast misses the floor\n“The raycast returns nothing.”" in written

    assert "Physics report; the web export is mentioned once." in written
    assert "In apply mode the desk would: do nothing on its own." in shown
    assert "The maintainers: `topic:physics`. Read 2026-04-17." in shown


def test_a_card_the_desk_would_handle_alone_says_so(page, monkeypatch):
    app, _ = open_page(page, monkeypatch, [sure()])
    shown = texts(app)
    assert "**Handled alone**" in shown and "To a person" not in shown
    assert "Gate rules" not in shown
    assert "Proposed duplicate: none" in shown
    assert "No earlier issue that may be the same report was found." in shown
    assert "In apply mode the desk would: add labels `topic:gui`." in shown
    assert "The maintainers" not in shown
    assert "The model's note" not in [caption.value for caption in app.caption]


def test_the_header_counts_what_is_shown_what_goes_to_a_person_and_what_is_decided(
    page, monkeypatch
):
    app, _ = open_page(page, monkeypatch, [sure(503), uncertain(501), sure(502)])
    assert metrics(app) == {"Shown": "3", "To a person": "1", "Decided": "0 of 3"}

    app.button(key=f"accept-502-{VERSION}").click().run()
    assert metrics(app)["Decided"] == "1 of 3"
    assert "Accepted 1 · corrected 0 · rejected 0." in texts(app)


def test_the_filter_for_items_that_go_to_a_person(page, monkeypatch):
    app, calls = open_page(page, monkeypatch, [sure(503), uncertain(501), sure(502)])
    app.checkbox(key="only_person").check().run()
    assert not app.exception
    assert metrics(app) == {"Shown": "1", "To a person": "1", "Decided": "0 of 1"}
    assert "Rigid bodies fall through the floor" in plain_texts(app)
    assert "Slider ignores the theme font" not in plain_texts(app)
    # With a filter on, more of the log is searched than is shown.
    assert calls[-1] == (page.DEMO, "model", page.SEARCH_DEPTH)


def test_the_filter_for_items_with_a_proposed_duplicate(page, monkeypatch):
    items = [sure(503), uncertain(501), uncertain(504, "Bodies sink on slopes"), sure(502)]
    app, _ = open_page(page, monkeypatch, items)
    app.checkbox(key="only_duplicate").check().run()
    assert metrics(app)["Shown"] == "2"
    assert "Slider ignores the theme font" not in plain_texts(app)

    # Both filters together, and nothing left: a message, not an empty page.
    monkeypatch.setattr(page, "load_items", lambda source, config, limit: [sure(502)])
    app.checkbox(key="only_person").check().run()
    assert not app.exception
    assert "No item matches the filters." in texts(app)
    assert not app.button


def test_the_configuration_and_the_number_to_show_are_passed_on(page, monkeypatch):
    items = [sure(510 + n, f"Invented issue {n}") for n in range(6)]
    app, calls = open_page(page, monkeypatch, items)
    assert app.selectbox(key="config").options == ["Model", "Classifier", "Rules"]

    app.selectbox(key="config").set_value("classifier").run()
    assert calls[-1] == (page.DEMO, "classifier", 20)
    app.number_input(key="limit").set_value(2).run()
    assert calls[-1] == (page.DEMO, "classifier", 2)
    assert metrics(app)["Shown"] == "2"
    assert plain_texts(app).count("Invented issue 2") == 0


def test_answers_without_the_model_are_counted_at_the_top(page, monkeypatch):
    fallback = uncertain(
        prediction=prediction(person=["model_unavailable", "uncertain_label"], source="classifier")
    )
    app, _ = open_page(page, monkeypatch, [fallback, sure()])
    assert "1 of 2 issues have no model answer." in app.warning[0].value
    assert "data/cassettes/fixture.jsonl" in app.warning[0].value
    assert "- No model answer; the classifier's answer is shown" in texts(app)


def test_accept_records_the_proposal_as_right(page, monkeypatch, home):
    app, _ = open_page(page, monkeypatch, [uncertain(), sure()])
    app.button(key=f"accept-501-{VERSION}").click().run()
    assert not app.exception

    made = reviews(home).latest()
    assert set(made) == {(501, VERSION)}
    review = made[(501, VERSION)]
    assert (review.verdict, review.config) == ("accepted", "model")
    assert review.labels == ["platform:web", "topic:physics"]
    assert review.duplicate_of == 312
    assert review.decided_at.utcoffset() is not None
    assert "Reviewed: **accepted** on" in app.success[0].value


def test_correct_records_the_reviewers_labels_and_duplicate(page, monkeypatch, home):
    app, _ = open_page(page, monkeypatch, [uncertain()])
    labels = app.multiselect(key=f"labels-501-{VERSION}")
    # Every label of the taxonomy can be chosen; the proposal is filled in.
    assert labels.options == LABELS
    assert labels.value == ["platform:web", "topic:physics"]
    assert app.number_input(key=f"duplicate-501-{VERSION}").value == 312

    labels.set_value(["topic:physics", "topic:audio"])
    app.number_input(key=f"duplicate-501-{VERSION}").set_value(0)
    app.text_input(key=f"comment-501-{VERSION}").input("Not web only; not the same report.")
    app.button(key=f"correct-501-{VERSION}").click().run()
    assert not app.exception and not app.error

    review = reviews(home).latest()[(501, VERSION)]
    assert review.verdict == "corrected"
    assert review.labels == ["topic:audio", "topic:physics"]
    assert review.duplicate_of is None
    assert review.comment == "Not web only; not the same report."
    assert app.warning[0].value.startswith("Reviewed: **corrected** on")
    assert "Labels: `topic:audio`, `topic:physics`. Duplicate of: none." in app.warning[0].value
    assert "Corrections added 1 labels, removed 1 and changed 1 duplicate answers." in texts(app)
    assert app.text_input(key=f"comment-501-{VERSION}").value == ""


def test_a_correction_can_name_another_original(page, monkeypatch, home):
    app, _ = open_page(page, monkeypatch, [uncertain()])
    app.number_input(key=f"duplicate-501-{VERSION}").set_value(290)
    app.button(key=f"correct-501-{VERSION}").click().run()
    review = reviews(home).latest()[(501, VERSION)]
    assert (review.verdict, review.duplicate_of) == ("corrected", 290)
    assert review.labels == ["platform:web", "topic:physics"]


def test_an_issue_cannot_be_corrected_to_a_duplicate_of_itself(page, monkeypatch, home):
    app, _ = open_page(page, monkeypatch, [uncertain()])
    app.number_input(key=f"duplicate-501-{VERSION}").set_value(501)
    app.button(key=f"correct-501-{VERSION}").click().run()
    assert not app.exception
    assert [error.value for error in app.error] == [
        "Not recorded: an issue cannot be a duplicate of itself."
    ]
    assert reviews(home).read() == []

    # The message goes away on the next run of the page.
    app.run()
    assert not app.error


def test_reject_records_no_answer_and_keeps_the_comment(page, monkeypatch, home):
    app, _ = open_page(page, monkeypatch, [uncertain()])
    app.text_input(key=f"comment-501-{VERSION}").input("This is a rendering report.")
    app.button(key=f"reject-501-{VERSION}").click().run()
    assert not app.exception

    review = reviews(home).latest()[(501, VERSION)]
    assert (review.verdict, review.labels, review.duplicate_of) == ("rejected", [], None)
    assert review.comment == "This is a rendering report."
    assert "Reviewed: **rejected** on" in app.error[0].value
    assert "Comment: This is a rendering report." in app.error[0].value


def test_a_decision_survives_a_rerun_and_a_new_session(page, monkeypatch, home):
    app, _ = open_page(page, monkeypatch, [uncertain(), sure()])
    app.button(key=f"accept-502-{VERSION}").click().run()
    app.run()
    assert "Reviewed: **accepted** on" in app.success[0].value
    assert metrics(app)["Decided"] == "1 of 2"

    again, _ = open_page(page, monkeypatch, [uncertain(), sure()])
    assert "Reviewed: **accepted** on" in again.success[0].value
    assert metrics(again)["Decided"] == "1 of 2"
    assert len(reviews(home).read()) == 1


def test_a_second_decision_supersedes_the_first_and_both_stay_on_file(page, monkeypatch, home):
    app, _ = open_page(page, monkeypatch, [uncertain()])
    app.button(key=f"accept-501-{VERSION}").click().run()
    app.button(key=f"reject-501-{VERSION}").click().run()
    assert not app.exception

    log = reviews(home)
    assert [review.verdict for review in log.read()] == ["accepted", "rejected"]
    assert log.latest()[(501, VERSION)].verdict == "rejected"
    assert not app.success
    assert "Reviewed: **rejected** on" in app.error[0].value
    assert metrics(app)["Decided"] == "1 of 1"


def test_hostile_issue_text_is_shown_as_text(page, monkeypatch):
    hostile = uncertain(
        title=HOSTILE,
        issue=make_issue(501, HOSTILE, f"{HOSTILE}\n\n![x](http://example.invalid/a.png)", day=92),
        note=f"Ignore the rules. {HOSTILE}",
        earlier=[Earlier(make_issue(312, HOSTILE, day=40), HOSTILE, proposed=True)],
    )
    app, _ = open_page(page, monkeypatch, [hostile])
    written = plain_texts(app)

    # As written, in elements that read nothing as Markdown or HTML.
    assert HOSTILE in written
    assert f"{HOSTILE}\n\n![x](http://example.invalid/a.png)" in written
    assert f"{HOSTILE}\n“{HOSTILE}”" in written
    assert f"Ignore the rules. {HOSTILE}" in written
    # And nowhere in what the page wrote as Markdown.
    for fragment in ("<script>", "**bold**", "[click]", "![x]", "Ignore the rules"):
        assert fragment not in texts(app)


def test_the_page_never_asks_streamlit_to_render_html():
    source = APP.read_text(encoding="utf-8")
    for call in ("unsafe_allow_html", "st.html", "components.html", "st.write(", "st.iframe"):
        assert call not in source


# --- the page as a script, reading for itself


def test_without_the_fixture_the_demo_says_what_is_missing(home):
    app = open_file()
    assert len(app.info) == 1
    message = app.info[0].value
    assert "The demo tracker is not in this folder" in message
    assert "data/fixture/issues.jsonl and data/fixture/timelines.json are missing" in message
    assert not app.button


def test_without_the_store_the_live_log_names_the_command_that_creates_it(home):
    app = open_file()
    app.sidebar.radio(key="source").set_value("Live log").run()
    assert not app.exception
    message = app.info[0].value
    assert "The local store (`var/triagedesk.db`) is not on this machine" in message
    assert "`uv run triagedesk backfill` creates it" in message
    # Looking for the store must not create it.
    assert not (home / "var").exists()


def test_without_a_live_log_the_page_names_the_command_that_writes_it(home):
    Store(home / "var" / "triagedesk.db").close()
    app = open_file()
    app.sidebar.radio(key="source").set_value("Live log").run()
    assert not app.exception
    assert app.info[0].value == (
        "There is no live log yet (`data/log/live.jsonl`). `uv run triagedesk run` writes it."
    )


def test_the_live_log_is_joined_with_the_issue_text_in_the_store(home):
    store = Store(home / "var" / "triagedesk.db")
    store.add_issues(
        [
            make_issue(312, "Bodies tunnel through thin floors", "A fast body passes through a thin static floor.", day=40),
            make_issue(501, "Rigid bodies fall through the floor", "The body passes through a static floor.", day=92),
        ]
    )  # fmt: skip
    store.set_note(501, VERSION, "Looks like the tunnelling report.")
    store.close()
    log = PredictionLog(home / "data" / "log" / "live.jsonl")
    log.append([prediction(501), prediction(501, config="classifier", person=[], gate=[])])

    app = open_file()
    app.sidebar.radio(key="source").set_value("Live log").run()
    assert not app.exception
    assert metrics(app) == {"Shown": "1", "To a person": "1", "Decided": "0 of 1"}
    assert "[#501](https://github.com/godotengine/godot/issues/501)" in texts(app)
    assert (
        "Bodies tunnel through thin floors\n“A fast body passes through a thin static floor.”"
        in plain_texts(app)
    )
    assert "Looks like the tunnelling report." in plain_texts(app)

    # A configuration the log has no answers of.
    app.sidebar.selectbox(key="config").set_value("rules").run()
    assert "The live log has no answers of the rules configuration." in app.info[0].value


def test_the_demo_runs_on_a_fixture_without_a_key_or_a_recording(page, home):
    """A small invented tracker in the test's own folder, not the repository's fixture."""
    arrivals = [
        make_issue(201, "Collision fails with gravity", "The collision breaks when the gravity changes and the solver is reset.", day=92),
        make_issue(202, "Volume fails with mixer", "The volume breaks when the mixer changes and the playback is reset.", day=93),
    ]  # fmt: skip
    fixture = home / "data" / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "issues.jsonl").write_text(
        "".join(issue.model_dump_json() + "\n" for issue in [*world_issues(), *arrivals]),
        encoding="utf-8",
    )
    (fixture / "timelines.json").write_text("{}", encoding="utf-8")

    by_classifier = page.load_items(page.DEMO, "classifier", 10)
    assert [item.issue.number for item in by_classifier] == [202, 201]
    assert by_classifier[0].prediction.proposal.names() == ["topic:audio"]
    assert len(page.load_items(page.DEMO, "classifier", 1)) == 1
    assert page.load_labels(page.DEMO) == [
        "topic:audio", "topic:gui", "topic:physics", "platform:web"
    ]  # fmt: skip

    # No recording: the model configuration falls back to the classifier and says so.
    app = open_file()
    assert metrics(app) == {"Shown": "2", "To a person": "2", "Decided": "0 of 2"}
    assert "2 of 2 issues have no model answer." in app.warning[0].value
    assert "Collision fails with gravity" in plain_texts(app)
    assert app.multiselect[0].options == [
        "platform:web", "topic:audio", "topic:gui", "topic:physics"
    ]  # fmt: skip
