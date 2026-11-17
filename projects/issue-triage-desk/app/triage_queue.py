"""The triage queue: each proposal beside its issue, with accept, correct and reject.

    uv run --extra ui streamlit run app/triage_queue.py

Two sources. The demo tracker is the invented one in `data/fixture`: it needs no key
and no network once the embedding model is on the machine, and none at all with
TRIAGEDESK_STUB_EMBEDDER=1. The live log is what `triagedesk run` wrote, joined with
the issue text in the local store.

Issue titles and bodies, earlier issues, matching passages and the model's note are
text from the public or derived from it. They are shown with `st.text`, which reads
nothing as Markdown or HTML. The few names that are placed inside Markdown (labels,
reason codes, the reviewer's comment) are escaped first.

A decision is appended to `data/corrections/reviews.jsonl`, or to the file named by
TRIAGEDESK_REVIEW_LOG. A later decision on the same proposal supersedes the earlier
one, and both stay in the file. The page never writes to a tracker.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from triagedesk.act import Plan
from triagedesk.config import load_settings
from triagedesk.demo import fixture_desk, fixture_settings, run_demo
from triagedesk.embed import FastEmbedder, HashEmbedder
from triagedesk.live import live_paths, read_runs
from triagedesk.llm import DEFAULT_EFFORT, DEFAULT_MODEL, CassetteProvider
from triagedesk.log import PredictionLog
from triagedesk.models import Outcome, Prediction, Proposal
from triagedesk.queue import Earlier, QueueItem, build_queue
from triagedesk.review import (
    COMMENT_MAX,
    Key,
    Review,
    ReviewLog,
    Summary,
    accept,
    correct,
    path_for,
    reject,
    summary,
)
from triagedesk.store import Store
from triagedesk.taxonomy import load as load_taxonomy
from triagedesk.truth import OutcomeLog

DEMO = "Demo tracker (invented issues)"
LIVE = "Live log"
SOURCES = (DEMO, LIVE)
# Whose answers are shown. The model's come first: they are what a reviewer reads.
CONFIGS = ("model", "classifier", "rules")
CONFIG_NAME = {"model": "Model", "classifier": "Classifier", "rules": "Rules"}

STUB_EMBEDDER_VAR = "TRIAGEDESK_STUB_EMBEDDER"
REVIEW_LOG_VAR = "TRIAGEDESK_REVIEW_LOG"

DEFAULT_SHOWN = 20
MAX_SHOWN = 200
# With a filter on, this many of the newest answers are searched for matches.
SEARCH_DEPTH = 500
BODY_CHARS = 6000

# Why a person has to look, in plain words. The codes are the gate's.
REASON = {
    "uncertain_label": "A label it is unsure of",
    "possible_duplicate": "A duplicate it is unsure of",
    "gate_changed_answer": "The gate changed the answer",
    "model_unavailable": "No model answer; the classifier's answer is shown",
}
BADGE_COLOUR = {"high": "green", "medium": "orange", "low": "gray"}
VERDICT_SHOWN = {"accepted": "success", "corrected": "warning", "rejected": "error"}
OWN_DUPLICATE = "Not recorded: an issue cannot be a duplicate of itself."

_WS = re.compile(r"\s+")
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()<>#|~$!:])")


class SourceUnavailable(Exception):
    """A source has nothing to read on this machine. The message says what creates it."""


def plain(text: str | None) -> str:
    """A name or a comment, made safe to place inside Markdown.

    It is shown as written, on one line, and none of it is read as formatting, a link,
    an image or a shortcode.
    """
    return _MARKDOWN.sub(r"\\\1", _WS.sub(" ", text or "").strip())


def code(name: str) -> str:
    """A name as an inline code span. Nothing inside one is read as formatting."""
    return "`" + _WS.sub(" ", name).replace("`", "'").strip() + "`"


# --- reading -------------------------------------------------------------------------


def stub_embedder() -> bool:
    return os.environ.get(STUB_EMBEDDER_VAR) == "1"


def review_log_path(settings) -> Path:
    """TRIAGEDESK_REVIEW_LOG if it is set, else the file next to the logs."""
    named = os.environ.get(REVIEW_LOG_VAR)
    return Path(named) if named else path_for(settings)


def demo_provider(cassette: Path) -> CassetteProvider:
    """The recorded model answers, with no live model behind them.

    Model name and parameters are those `triagedesk demo` records with: they are part
    of a recording's key, so any other value would find no answer.
    """
    return CassetteProvider(
        cassette,
        DEFAULT_MODEL,
        inner=None,
        record=False,
        params={"effort": DEFAULT_EFFORT, "max_tokens": 4000},
    )


def _newest_first(items: list[QueueItem]) -> list[QueueItem]:
    return sorted(items, key=lambda i: (i.issue.created_at, i.issue.number), reverse=True)


def _stamp(paths: list[Path]) -> tuple[int, ...]:
    """Changes when one of the files does, so a new fixture is not answered from the cache."""
    return tuple(path.stat().st_mtime_ns if path.exists() else 0 for path in paths)


@st.cache_resource(show_spinner="Training the demo desk on the invented tracker.")
def _demo(home: str, stub: bool, stamp: tuple[int, ...]):
    """The demo's answers under every configuration, and the labels of its taxonomy.

    Built once per session of the server: training takes a moment. `stamp` is only
    part of the cache key.
    """
    settings = fixture_settings(Path(home))
    provider = demo_provider(settings.cassette_dir / "fixture.jsonl")
    desk = fixture_desk(settings, HashEmbedder() if stub else FastEmbedder(), provider)
    try:
        by_config = {}
        for config in CONFIGS:
            _, _, items, _ = run_demo(desk, (config,))
            by_config[config] = _newest_first(items)
        return by_config, desk.taxonomy.names()
    finally:
        desk.store.close()


def _demo_files(home: Path) -> list[Path]:
    settings = fixture_settings(home)
    return [
        settings.fixture_dir / "issues.jsonl",
        settings.fixture_dir / "timelines.json",
        settings.cassette_dir / "fixture.jsonl",
    ]


def _demo_answers(home: Path):
    issues, timelines, _ = files = _demo_files(home)
    missing = [str(path.relative_to(home)) for path in (issues, timelines) if not path.exists()]
    if missing:
        raise SourceUnavailable(
            f"The demo tracker is not in this folder: {' and '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} missing. They are part of the "
            "repository; fetch a complete copy, or choose the live log in the sidebar."
        )
    try:
        return _demo(str(home), stub_embedder(), _stamp(files))
    except Exception as exc:  # a broken fixture, or an embedding model that will not load
        hint = (
            ""
            if stub_embedder()
            else f" The embedding model is downloaded on first use; with {STUB_EMBEDDER_VAR}=1 "
            "the demo runs without it."
        )
        raise SourceUnavailable(
            f"The demo could not be built ({type(exc).__name__}: {exc}).{hint}"
        ) from exc


def _live_items(settings, config: str, limit: int) -> list[QueueItem]:
    paths = live_paths(settings)
    # Opening a store creates the file, so look first.
    if not settings.db_path.exists():
        raise SourceUnavailable(
            "The local store (`var/triagedesk.db`) is not on this machine, and the issue "
            "text is only there. `uv run triagedesk backfill` creates it; then "
            "`uv run triagedesk index` and `uv run triagedesk run`."
        )
    if not paths["log"].exists():
        raise SourceUnavailable(
            "There is no live log yet (`data/log/live.jsonl`). `uv run triagedesk run` writes it."
        )
    store = Store(settings.db_path)
    try:
        return build_queue(
            store,
            PredictionLog(paths["log"]).read(),
            OutcomeLog(paths["outcomes"]).read(),
            config=config,
            limit=limit,
        )
    finally:
        store.close()


def load_items(source: str, config: str, limit: int) -> list[QueueItem]:
    """The proposals of one configuration, newest issue first. All items come from here.

    Raises SourceUnavailable, with a message for the reader, when the source has
    nothing to read on this machine.
    """
    settings = load_settings()
    if source == LIVE:
        return _live_items(settings, config, limit)
    by_config, _ = _demo_answers(settings.home)
    return by_config.get(config, [])[:limit]


def load_labels(source: str) -> list[str]:
    """The labels a reviewer can choose from: the taxonomy's. Empty if there is none yet."""
    settings = load_settings()
    if source == LIVE:
        for name in ("taxonomy-live.json", "taxonomy.json"):
            path = settings.home / "data" / name
            if path.exists():
                try:
                    return load_taxonomy(path).names()
                except (OSError, ValueError):
                    return []
        return []
    try:
        return list(_demo_answers(settings.home)[1])
    except SourceUnavailable:
        return []


def applied_live(settings) -> int:
    """How many answers the live runs applied to the tracker. Zero in shadow mode."""
    try:
        return sum(int(run.get("applied", 0)) for run in read_runs(settings))
    except (OSError, ValueError):
        return 0


# --- wording -------------------------------------------------------------------------


def review_key(prediction: Prediction) -> Key:
    """What a decision is filed under: the issue and the system version that answered."""
    return (prediction.number, prediction.system_version)


def item_key(prediction: Prediction) -> str:
    """The same pair as text, for the keys of a card's widgets."""
    return f"{prediction.number}-{prediction.system_version}"


def source_caption(source: str, repo: str, applied: int = 0) -> str:
    if source == DEMO:
        return (
            "Demo: every issue on this page is invented and the tracker is fictional. "
            "Nothing here is a real report, a real project or a real person."
        )
    if applied:
        return (
            f"Live log of `{repo}`. Apply mode has been used: {applied} answers were "
            "written to the tracker. This page itself writes nothing to it."
        )
    return (
        f"Live log of `{repo}`. The desk is in shadow mode: it has written nothing to "
        "the tracker. Its own prediction log is the only thing it writes."
    )


def reason_text(reason: str) -> str:
    """One reason a person has to look, in plain words. `reason` is the gate's code."""
    if reason in REASON:
        return REASON[reason]
    if reason.startswith("no_confident_"):
        return f"No {plain(reason.removeprefix('no_confident_'))} label it is sure of"
    return plain(reason)


def label_chips(proposal: Proposal) -> str:
    """The proposed labels as coloured chips, each with its confidence."""
    if not proposal.labels:
        return "Proposed labels: none"
    chips = " ".join(
        f":{BADGE_COLOUR[pick.confidence]}-badge[{plain(pick.name)} · {pick.confidence}]"
        for pick in proposal.labels
    )
    return f"Proposed labels: {chips}"


def route_text(proposal: Proposal) -> str:
    if not proposal.person:
        return "**Handled alone**"
    reasons = "\n".join(f"- {reason_text(reason)}" for reason in proposal.person)
    return f"**To a person**\n{reasons}"


def gate_text(proposal: Proposal) -> str | None:
    if not proposal.gate:
        return None
    return "Gate rules that fired: " + ", ".join(code(rule) for rule in proposal.gate)


def duplicate_text(item: QueueItem) -> str:
    proposal = item.prediction.proposal
    if proposal.duplicate_of is None:
        return "Proposed duplicate: none"
    text = (
        f"Proposed duplicate: **#{proposal.duplicate_of}**, "
        f"confidence {proposal.duplicate_confidence or 'low'}"
    )
    if not any(earlier.proposed for earlier in item.earlier):
        text += ". Its text is not in the local store"
    return text + "."


def earlier_heading(earlier: Earlier) -> str:
    return f"**#{earlier.issue.number}**" + (
        " · the proposed duplicate" if earlier.proposed else ""
    )


def earlier_text(earlier: Earlier) -> str:
    """Title and matching passage of an earlier issue, as plain text."""
    return f"{earlier.issue.title}\n“{earlier.passage}”"


def plan_text(plan: Plan) -> str:
    """What the desk would do on its own with this proposal in apply mode."""
    steps = []
    if plan.add_labels:
        steps.append("add labels " + ", ".join(code(name) for name in plan.add_labels))
    if plan.comment:
        steps.append("post one comment")
    return "In apply mode the desk would: " + ("; ".join(steps) or "do nothing on its own") + "."


def outcome_text(outcome: Outcome) -> str:
    """What the maintainers did, read after the waiting period."""
    labels = ", ".join(code(name) for name in outcome.labels)
    text = f"The maintainers: {labels or 'no label of the taxonomy'}"
    if outcome.duplicate:
        original = f"#{outcome.original}" if outcome.original else "an issue that was not resolved"
        text += f"; closed as a duplicate of {original}"
    elif outcome.state_reason:
        text += f"; closed as {plain(outcome.state_reason)}"
    return text + f". Read {outcome.read_at:%Y-%m-%d}."


def review_text(review: Review) -> str:
    text = f"Reviewed: **{review.verdict}** on {review.decided_at:%Y-%m-%d %H:%M %Z}."
    if review.verdict == "corrected":
        labels = ", ".join(code(name) for name in review.labels) or "none"
        duplicate = f"#{review.duplicate_of}" if review.duplicate_of else "none"
        text += f" Labels: {labels}. Duplicate of: {duplicate}."
    if review.comment:
        text += f" Comment: {plain(review.comment)}"
    return text


def summary_text(counts: Summary) -> str:
    text = f"Accepted {counts.accepted} · corrected {counts.corrected} · rejected {counts.rejected}"
    if counts.compared:
        text += (
            f". Corrections added {counts.labels_added} labels, removed "
            f"{counts.labels_removed} and changed {counts.duplicates_changed} duplicate answers"
        )
    return text + "."


def without_model(items: list[QueueItem]) -> int:
    return sum(1 for item in items if "model_unavailable" in item.prediction.proposal.person)


def missing_answers_text(count: int, total: int, source: str) -> str:
    text = (
        f"{count} of {total} issues have no model answer. "
        "The classifier's answer is shown for them and each goes to a person."
    )
    if source == DEMO:
        text += (
            " The demo replays recorded answers from `data/cassettes/fixture.jsonl`: "
            "the file is missing, or it was recorded for other prompts or another embedder."
        )
    return text


def empty_text(source: str, config: str) -> str:
    if source == DEMO:
        return "The demo tracker has no new arrivals to show."
    return (
        f"The live log has no answers of the {CONFIG_NAME.get(config, config).lower()} "
        "configuration. `uv run triagedesk run` writes them. Without a model key it "
        "writes the classifier's and the rules' answers only: try another configuration."
    )


def chosen(items: list[QueueItem], only_person: bool, only_duplicate: bool) -> list[QueueItem]:
    """The items that pass the sidebar's filters, in the order given."""
    return [
        item
        for item in items
        if (not only_person or item.prediction.proposal.needs_person)
        and (not only_duplicate or item.prediction.proposal.duplicate_of is not None)
    ]


def label_options(labels: list[str], items: list[QueueItem]) -> list[str]:
    """The taxonomy's labels, and any label a shown proposal carries."""
    proposed = {pick.name for item in items for pick in item.prediction.proposal.labels}
    return sorted(set(labels) | proposed)


# --- deciding ------------------------------------------------------------------------


def submit(
    log: ReviewLog,
    prediction: Prediction,
    verdict: str,
    *,
    labels: list[str] | None = None,
    duplicate: int = 0,
    comment: str = "",
) -> str | None:
    """Record a decision. Returns the message to show if it was refused, else None.

    `duplicate` is the issue number the reviewer typed, with 0 for "not a duplicate".
    """
    try:
        if verdict == "accepted":
            made = accept(prediction, comment=comment)
        elif verdict == "rejected":
            made = reject(prediction, comment=comment)
        elif duplicate == prediction.number:
            return OWN_DUPLICATE
        else:
            made = correct(prediction, labels or [], duplicate or None, comment=comment)
    except ValidationError as exc:
        return "Not recorded: " + "; ".join(error["msg"] for error in exc.errors())
    log.add(made)
    return None


def _decide(log: ReviewLog, prediction: Prediction, verdict: str) -> None:
    """Button callback: record, then clear the comment or keep the refusal to show."""
    key = item_key(prediction)
    state = st.session_state
    error = submit(
        log,
        prediction,
        verdict,
        labels=list(state.get(f"labels-{key}", [])),
        duplicate=int(state.get(f"duplicate-{key}", 0) or 0),
        comment=state.get(f"comment-{key}", ""),
    )
    if error:
        state[f"error-{key}"] = error
    else:
        state[f"comment-{key}"] = ""
        state.pop(f"error-{key}", None)


# --- the page ------------------------------------------------------------------------


def _render_proposal(item: QueueItem) -> None:
    proposal = item.prediction.proposal
    st.markdown(label_chips(proposal))
    st.markdown(route_text(proposal))
    fired = gate_text(proposal)
    if fired:
        st.markdown(fired)
    st.markdown(duplicate_text(item))
    if item.earlier:
        st.markdown("Earlier issues that may be the same report:")
        for earlier in item.earlier:
            st.markdown(earlier_heading(earlier))
            st.text(earlier_text(earlier))
    else:
        st.markdown("No earlier issue that may be the same report was found.")
    if item.note:
        st.caption("The model's note")
        st.text(item.note)
    st.markdown(plan_text(item.plan))
    if item.outcome is not None:
        st.markdown(outcome_text(item.outcome))


def _render_controls(item: QueueItem, review: Review | None, options: list[str], log) -> None:
    prediction = item.prediction
    proposal = prediction.proposal
    key = item_key(prediction)
    if review is not None:
        getattr(st, VERDICT_SHOWN[review.verdict])(review_text(review))
    error = st.session_state.pop(f"error-{key}", None)
    if error:
        st.error(error)
    st.text_input(
        "Comment (optional)" if review is None else "Comment for a new decision (optional)",
        key=f"comment-{key}",
        max_chars=COMMENT_MAX,
    )
    accept_column, reject_column, _ = st.columns([1, 1, 6])
    accept_column.button(
        "Accept", key=f"accept-{key}", on_click=_decide, args=(log, prediction, "accepted")
    )
    reject_column.button(
        "Reject", key=f"reject-{key}", on_click=_decide, args=(log, prediction, "rejected")
    )
    with st.expander("Correct"):
        st.multiselect(
            "The labels that are right", options, default=proposal.names(), key=f"labels-{key}"
        )
        st.number_input(
            "Duplicate of issue number (0: not a duplicate)",
            min_value=0,
            step=1,
            value=proposal.duplicate_of or 0,
            key=f"duplicate-{key}",
        )
        st.button(
            "Save correction",
            key=f"correct-{key}",
            on_click=_decide,
            args=(log, prediction, "corrected"),
        )


def _render_card(item: QueueItem, review: Review | None, options: list[str], log, link) -> None:
    issue = item.issue
    with st.container(border=True):
        number = f"[#{issue.number}]({link}/{issue.number})" if link else f"#{issue.number}"
        st.markdown(f"**Issue {number}** · opened {issue.created_at:%Y-%m-%d %H:%M} UTC")
        st.text(issue.title)
        with st.expander("Issue text"):
            body = issue.body or "(The issue has no text.)"
            st.text(body[:BODY_CHARS])
            if len(body) > BODY_CHARS:
                st.caption(f"Cut after {BODY_CHARS} of {len(body)} characters.")
        _render_proposal(item)
        _render_controls(item, review, options, log)


def render() -> None:
    st.set_page_config(page_title="Triage queue", layout="wide")
    st.title("Triage queue")

    source = st.sidebar.radio("Source", SOURCES, key="source")
    config = st.sidebar.selectbox(
        "Configuration", CONFIGS, format_func=lambda name: CONFIG_NAME[name], key="config"
    )
    only_person = st.sidebar.checkbox("Only items that go to a person", key="only_person")
    only_duplicate = st.sidebar.checkbox(
        "Only items with a proposed duplicate", key="only_duplicate"
    )
    limit = int(
        st.sidebar.number_input(
            "How many to show",
            min_value=1,
            max_value=MAX_SHOWN,
            value=DEFAULT_SHOWN,
            step=5,
            key="limit",
        )
    )

    settings = load_settings()
    live = source == LIVE
    st.caption(source_caption(source, settings.repo, applied_live(settings) if live else 0))

    filtered = only_person or only_duplicate
    try:
        loaded = load_items(source, config, SEARCH_DEPTH if filtered else limit)
    except SourceUnavailable as exc:
        st.info(str(exc))
        return
    if not loaded:
        st.info(empty_text(source, config))
        return
    shown = chosen(loaded, only_person, only_duplicate)[:limit]

    log = ReviewLog(review_log_path(settings))
    reviews = log.latest()
    keys = [review_key(item.prediction) for item in shown]
    decided = {key: reviews[key] for key in keys if key in reviews}
    count_shown, count_person, count_decided = st.columns(3)
    count_shown.metric("Shown", len(shown))
    count_person.metric(
        "To a person", sum(1 for item in shown if item.prediction.proposal.needs_person)
    )
    count_decided.metric("Decided", f"{len(decided)} of {len(shown)}")
    if decided:
        proposals = {review_key(item.prediction): item.prediction.proposal for item in shown}
        st.caption(summary_text(summary(decided.values(), proposals)))
    if filtered:
        st.caption(f"Filters are on: the newest {SEARCH_DEPTH} answers were searched.")
    missing = without_model(shown) if config == "model" else 0
    if missing:
        st.warning(missing_answers_text(missing, len(shown), source))
    if not shown:
        st.info("No item matches the filters.")
        return

    options = label_options(load_labels(source), shown)
    link = f"https://github.com/{settings.repo}/issues" if live else None
    for item in shown:
        _render_card(item, decided.get(review_key(item.prediction)), options, log, link)


if __name__ == "__main__":
    render()
