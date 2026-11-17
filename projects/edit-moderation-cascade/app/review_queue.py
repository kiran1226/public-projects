"""The review queue: edits ordered by risk, each with what it changed and a decision.

    uv run --extra ui streamlit run app/review_queue.py

Two sources. The demo wiki is the invented one in `data/fixture`: it needs no key and
no network, and replays the model's recorded answers from `data/cassettes`. The last
live run is what `editwatch run` wrote on this machine, joined with the local store,
which holds the edits and what each one changed.

Article titles and the lines of a change are text from the public. They are shown
with `st.text`, which reads nothing as Markdown or HTML. The few names that are
placed inside Markdown (gate rules, the reviewer's comment) are escaped first. The
sentence on a card is worded by this project from the model's category: the model
writes no text of its own.

A decision is appended to `data/corrections/reviews.jsonl`, or to the file named by
EDITWATCH_REVIEW_LOG. A later decision on the same entry supersedes the earlier one,
and both stay in the file. The page shows whether an account is temporary or
registered and never a name: none is stored. It never writes to a wiki.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from editwatch.config import CATEGORIES, load_settings
from editwatch.demo import run_demo
from editwatch.live import live_paths, read_runs
from editwatch.llm import DEFAULT_EFFORT, CassetteProvider
from editwatch.log import PredictionLog
from editwatch.models import Change, Prediction
from editwatch.queue import QueueItem, build_queue
from editwatch.review import (
    COMMENT_MAX,
    Key,
    Review,
    ReviewLog,
    Summary,
    fine,
    path_for,
    problem,
    summary,
    unsure,
)
from editwatch.store import Store
from editwatch.tier2 import Tier2
from editwatch.truth import OutcomeLog

DEMO = "Demo wiki (invented edits)"
LIVE = "Last live run"
SOURCES = (DEMO, LIVE)

REVIEW_LOG_VAR = "EDITWATCH_REVIEW_LOG"

DEFAULT_SHOWN = 20
MAX_SHOWN = 200
# The whole queue is loaded, so that an entry's place and the counts are those of the
# queue and not of the part shown. One ten-minute window is several hundred edits.
QUEUE_DEPTH = 5000
# A live run's rows are those written within this long of its newest row.
RUN_SPAN_S = 3600

MARK = ">>"
NOT_SAID = "(not said)"
# What a reviewer can call a problem: the model's list without "none".
PROBLEM_CATEGORIES = tuple(name for name in CATEGORIES if name != "none")
CATEGORY_NAME = {
    "vandalism": "Vandalism",
    "spam_or_promotion": "Promotion or spam",
    "false_or_unsourced_claim": "A fact changed without a source",
    "test_or_accident": "A test or an accident",
    "content_removal": "Content removed without a reason",
    "other": "Something else",
}
VERDICT_SHOWN = {"problem": "error", "fine": "success", "unsure": "warning"}

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


def review_log_path(settings) -> Path:
    """EDITWATCH_REVIEW_LOG if it is set, else the file next to the logs."""
    named = os.environ.get(REVIEW_LOG_VAR)
    return Path(named) if named else path_for(settings)


def demo_provider(settings) -> CassetteProvider:
    """The recorded model answers, with no live model behind them.

    Model name and parameters are those `editwatch demo` records with: they are part
    of a recording's key, so any other value would find no answer.
    """
    return CassetteProvider(
        settings.cassette_dir / "fixture.jsonl",
        settings.live_model,
        params={"effort": DEFAULT_EFFORT, "max_tokens": 1000},
    )


def _stamp(paths: list[Path]) -> tuple[int, ...]:
    """Changes when one of the files does, so a new file is not answered from the cache."""
    return tuple(path.stat().st_mtime_ns if path.exists() else 0 for path in paths)


@st.cache_resource(show_spinner="Ordering the invented wiki's new edits.")
def _demo(home: str, stamp: tuple[int, ...]) -> list[QueueItem]:
    """The demo queue, riskiest first. Built once per version of the fixture's files.

    `stamp` is only part of the cache key.
    """
    settings = load_settings(Path(home))
    rows, outcomes, _ = run_demo(settings, Tier2(demo_provider(settings)))
    return build_queue(rows, outcomes)


def _demo_items(settings) -> list[QueueItem]:
    edits = settings.fixture_dir / "edits.jsonl"
    if not edits.exists():
        raise SourceUnavailable(
            f"The demo wiki is not in this folder: {edits.relative_to(settings.home)} is "
            "missing. It is part of the repository; fetch a complete copy, or choose the "
            "last live run in the sidebar."
        )
    try:
        return _demo(str(settings.home), _stamp([edits, settings.cassette_dir / "fixture.jsonl"]))
    except Exception as exc:  # a fixture that does not parse, or too small to train on
        raise SourceUnavailable(
            f"The demo could not be built ({type(exc).__name__}: {exc})."
        ) from exc


@st.cache_resource(show_spinner="Reading the last live run.")
def _live(home: str, stamp: tuple[int, ...]) -> list[QueueItem]:
    """The rows of the newest live run, joined with the store, as `editwatch queue` does.

    `stamp` is only part of the cache key: a new run, a new outcome or a change to the
    store is read afresh.
    """
    settings = load_settings(Path(home))
    paths = live_paths(settings)
    version = read_runs(settings)[-1]["version"]
    last = [p for p in PredictionLog(paths["log"]).read() if p.system_version == version]
    newest = max((p.written_at for p in last), default=None)
    rows = []
    store = Store(settings.db_path)
    try:
        for p in last:
            if (newest - p.written_at).total_seconds() > RUN_SPAN_S:
                continue
            edit = store.edit(p.rev_id)
            if edit is not None:
                rows.append((edit, p, store.change(p.rev_id)))
    finally:
        store.close()
    if last and not rows:
        raise SourceUnavailable(
            "The live log has a run, but the local store (`var/editwatch.db`) holds none "
            "of its edits: the run was made on another machine. `uv run editwatch run` "
            "makes one here."
        )
    outcomes = {o.rev_id: o.reverted for o in OutcomeLog(paths["outcomes"]).read().values()}
    return build_queue(rows, outcomes)


def _live_items(settings) -> list[QueueItem]:
    paths = live_paths(settings)
    # Opening a store creates the file, so look first.
    if not settings.db_path.exists():
        raise SourceUnavailable(
            "The local store (`var/editwatch.db`) is not on this machine, and the edits "
            "and what each one changed are only there. `uv run editwatch backfill` "
            "creates it; then `uv run editwatch run` reads the stream for ten minutes."
        )
    if not paths["runs"].exists() or not paths["log"].exists() or not read_runs(settings):
        raise SourceUnavailable(
            "There is no live run yet (`data/log/live-runs.jsonl`). "
            "`uv run editwatch run` writes it."
        )
    stamp = _stamp([paths["runs"], paths["log"], paths["outcomes"], settings.db_path])
    return _live(str(settings.home), stamp)


def load_items(source: str, limit: int) -> list[QueueItem]:
    """The first `limit` entries of a queue, riskiest first. All items come from here.

    Raises SourceUnavailable, with a message for the reader, when the source has
    nothing to read on this machine.
    """
    settings = load_settings()
    items = _live_items(settings) if source == LIVE else _demo_items(settings)
    return items[:limit]


# --- wording -------------------------------------------------------------------------


def review_key(prediction: Prediction) -> Key:
    """What a decision is filed under: the edit and the system version that answered."""
    return (prediction.rev_id, prediction.system_version)


def item_key(prediction: Prediction) -> str:
    """The same pair as text, for the keys of a card's widgets."""
    return f"{prediction.rev_id}-{prediction.system_version}"


def source_caption(source: str, host: str) -> str:
    if source == DEMO:
        return (
            "Demo: every article, edit and outcome on this page is invented. Nothing "
            "here is from a real wiki or about a real person."
        )
    return (
        f"Last live run on `{host}`. The system only orders this queue: it has changed "
        "nothing on the wiki, and it stores no account names. A decision made here is a "
        "note on this machine."
    )


def risk_text(item: QueueItem) -> str:
    return f"**{item.place}.** Risk **{item.prediction.verdict.risk:.2f}**"


def bar_value(risk: float) -> float:
    """A risk as the length of the bar: between nothing and all of it."""
    return min(1.0, max(0.0, risk))


def account_text(item: QueueItem) -> str:
    """Which kind of account made the edit. Never which account."""
    return "temporary account" if item.edit.temp else "registered account"


def size_text(item: QueueItem) -> str:
    delta = item.edit.delta
    return f"{'+' if delta >= 0 else '-'}{abs(delta):,} bytes"


def facts_text(item: QueueItem, link: str | None = None) -> str:
    """The line under the title: size change, kind of account, time, revision."""
    edit = item.edit
    revision = f"[{edit.rev_id}]({link}{edit.rev_id})" if link else str(edit.rev_id)
    parts = [size_text(item), account_text(item)]
    if edit.minor:
        parts.append("marked as minor")
    parts.append(f"made {edit.timestamp:%Y-%m-%d %H:%M} UTC")
    parts.append(f"revision {revision}")
    return " · ".join(parts)


def tier_text(item: QueueItem) -> str:
    """Which tier decided, with the model's risk and the tier-1 score."""
    v = item.prediction.verdict
    if v.source == "model":
        return (
            f"Decided by the model. Model risk {v.model_risk} of 100 · tier 1 score {v.tier1:.2f}"
        )
    # The sentence above it says why the model's answer does not count, if it was asked.
    return f"Decided by tier 1 alone. Tier 1 score {v.tier1:.2f}"


def gate_text(item: QueueItem) -> str | None:
    fired = item.prediction.verdict.gate
    if not fired:
        return None
    return "Gate rules that fired: " + ", ".join(code(rule) for rule in fired)


def change_text(change: Change, marked: list[int]) -> str:
    """The change line by line, as plain text. A line the model marked starts with >>."""
    flagged = set(marked)
    width = len(MARK)
    return "\n".join(
        f"{MARK if number in flagged else ' ' * width} {line}"
        for number, line in enumerate(change.lines, start=1)
    )


def marked_text(marked: list[int]) -> str:
    lines = f"line {marked[0]}" if len(marked) == 1 else f"lines {marked[0]} to {marked[-1]}"
    return f"{MARK} marked by the model: {lines}"


def no_change_text(item: QueueItem) -> str:
    """Why a card has no change to show."""
    if item.change is None:
        return (
            "What this edit changed was not fetched: only the edits handed to the model "
            "are read from the wiki."
        )
    if not item.change.available:
        return "The text of this revision could not be read: it was deleted or hidden."
    return "No line of text differs."


def outcome_text(reverted: bool, source: str, hours: int) -> str:
    """What became of the edit. In the demo the outcome is part of the invented fixture."""
    what = "reverted" if reverted else "not reverted"
    if source == DEMO:
        return f"Outcome in the invented wiki: {what}."
    return f"Outcome: {what}. Read at least {hours} hours after the edit was made."


def category_name(name: str) -> str:
    return CATEGORY_NAME.get(name, name)


def review_text(review: Review) -> str:
    text = f"Reviewed: **{review.verdict}** on {review.decided_at:%Y-%m-%d %H:%M %Z}."
    if review.category:
        text += f" Kind: {plain(category_name(review.category))}."
    if review.comment:
        text += f" Comment: {plain(review.comment)}"
    return text


def summary_text(counts: Summary) -> str:
    text = f"Problem {counts.problem} · fine {counts.fine} · unsure {counts.unsure}."
    if counts.placed:
        text += (
            f" Of the {counts.placed} reviewed, the model had decided {counts.by_model} "
            f"and tier 1 alone {counts.by_tier1}."
        )
    if counts.problems_placed:
        text += (
            f" Of {counts.problems_placed} called a problem, {counts.problems_in_top} "
            "stood in the top 10% of the queue."
        )
    return text


def read_by_model(items: list[QueueItem]) -> int:
    return sum(1 for item in items if item.prediction.verdict.source == "model")


def skipped(items: list[QueueItem], reason: str) -> int:
    return sum(1 for item in items if item.prediction.verdict.skipped == reason)


def missing_answers_text(count: int, source: str) -> str:
    text = (
        f"{count} edits were handed to the model and have no answer from it. "
        "Each keeps its tier-1 score."
    )
    if source == DEMO:
        text += (
            " The demo replays recorded answers from `data/cassettes/fixture.jsonl`: "
            "the file is missing, or it was recorded for another prompt or model."
        )
    else:
        text += ' See "Rotating the model key" in `docs/runbook.md`.'
    return text


def no_model_text(count: int) -> str:
    return (
        f"This run had no model: {count} edits in the model's slice are ordered by tier 1 "
        'alone. `uv run editwatch run` says "No model key found" when that is the reason.'
    )


def empty_text(source: str) -> str:
    if source == DEMO:
        return "The demo wiki has no new edits to show."
    return "The last live run ordered no edits. `uv run editwatch run` makes the next one."


def chosen(items: list[QueueItem], only_model: bool, min_risk: float) -> list[QueueItem]:
    """The items that pass the sidebar's filters, in the order given."""
    return [
        item
        for item in items
        if (not only_model or item.prediction.verdict.source == "model")
        and item.prediction.verdict.risk >= min_risk
    ]


# --- deciding ------------------------------------------------------------------------


def submit(
    log: ReviewLog,
    prediction: Prediction,
    verdict: str,
    *,
    category: str | None = None,
    comment: str = "",
) -> str | None:
    """Record a decision. Returns the message to show if it was refused, else None.

    `category` is the reviewer's choice for a problem; it is ignored for the others.
    """
    try:
        if verdict == "problem":
            made = problem(prediction, category, comment=comment)
        elif verdict == "fine":
            made = fine(prediction, comment=comment)
        else:
            made = unsure(prediction, comment=comment)
    except ValidationError as exc:
        return "Not recorded: " + "; ".join(error["msg"] for error in exc.errors())
    log.add(made)
    return None


def _decide(log: ReviewLog, prediction: Prediction, verdict: str) -> None:
    """Button callback: record, then clear the comment or keep the refusal to show."""
    key = item_key(prediction)
    state = st.session_state
    picked = state.get(f"category-{key}", NOT_SAID)
    error = submit(
        log,
        prediction,
        verdict,
        category=None if picked == NOT_SAID else picked,
        comment=state.get(f"comment-{key}", ""),
    )
    if error:
        state[f"error-{key}"] = error
    else:
        state[f"comment-{key}"] = ""
        state.pop(f"error-{key}", None)


# --- the page ------------------------------------------------------------------------


def _render_change(item: QueueItem) -> None:
    change = item.change
    if change is None or not change.available or not change.lines:
        st.caption(no_change_text(item))
        return
    st.caption("What the edit changed: a line with - was removed, a line with + was added")
    # Untrusted text: `st.text` shows it as written and reads none of it as formatting.
    st.text(change_text(change, item.marked))
    if item.marked:
        st.caption(marked_text(item.marked))
    if change.truncated:
        st.caption("The change is longer than this: it is cut where the model's view of it ends.")


def _render_controls(item: QueueItem, review: Review | None, log: ReviewLog) -> None:
    prediction = item.prediction
    key = item_key(prediction)
    if review is not None:
        getattr(st, VERDICT_SHOWN[review.verdict])(review_text(review))
    error = st.session_state.pop(f"error-{key}", None)
    if error:
        st.error(error)
    kind_column, comment_column = st.columns([2, 5])
    kind_column.selectbox(
        "Kind of problem (recorded with Problem)",
        (NOT_SAID, *PROBLEM_CATEGORIES),
        format_func=category_name,
        key=f"category-{key}",
    )
    comment_column.text_input(
        "Comment (optional)" if review is None else "Comment for a new decision (optional)",
        key=f"comment-{key}",
        max_chars=COMMENT_MAX,
    )
    problem_column, fine_column, unsure_column, _ = st.columns([1, 1, 1, 5])
    problem_column.button(
        "Problem", key=f"problem-{key}", on_click=_decide, args=(log, prediction, "problem")
    )
    fine_column.button("Fine", key=f"fine-{key}", on_click=_decide, args=(log, prediction, "fine"))
    unsure_column.button(
        "Unsure", key=f"unsure-{key}", on_click=_decide, args=(log, prediction, "unsure")
    )


def _render_card(item: QueueItem, review: Review | None, log: ReviewLog, source: str) -> None:
    settings = load_settings()
    link = f"https://{settings.wiki_host}/w/index.php?diff=" if source == LIVE else None
    with st.container(border=True):
        risk_column, title_column = st.columns([1, 5])
        risk_column.markdown(risk_text(item))
        risk_column.progress(bar_value(item.prediction.verdict.risk))
        with title_column:
            st.text(item.edit.title)
            st.markdown(facts_text(item, link))
        st.markdown(f"**{plain(item.sentence)}**")
        st.markdown(tier_text(item))
        fired = gate_text(item)
        if fired:
            st.markdown(fired)
        _render_change(item)
        if item.reverted is not None:
            st.markdown(outcome_text(item.reverted, source, settings.truth_after_hours))
        _render_controls(item, review, log)


def render() -> None:
    st.set_page_config(page_title="Review queue", layout="wide")
    st.title("Review queue")

    source = st.sidebar.radio("Source", SOURCES, key="source")
    only_model = st.sidebar.checkbox("Only edits the model read", key="only_model")
    min_risk = float(
        st.sidebar.slider(
            "Only edits with a risk of at least",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.05,
            key="min_risk",
        )
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
    st.caption(source_caption(source, settings.wiki_host))

    try:
        queue = load_items(source, QUEUE_DEPTH)
    except SourceUnavailable as exc:
        st.info(str(exc))
        return
    if not queue:
        st.info(empty_text(source))
        return
    passing = chosen(queue, only_model, min_risk)
    shown = passing[:limit]

    log = ReviewLog(review_log_path(settings))
    reviews = log.latest()
    decided = {
        key: reviews[key]
        for key in (review_key(item.prediction) for item in shown)
        if key in reviews
    }
    count_shown, count_model, count_decided = st.columns(3)
    count_shown.metric("Shown", f"{len(shown)} of {len(queue)}")
    count_model.metric("Read by the model", read_by_model(shown))
    count_decided.metric("Decided", f"{len(decided)} of {len(shown)}")
    if decided:
        # Places are those of the whole queue, not of the part shown.
        st.caption(summary_text(summary(decided.values(), [item.prediction for item in queue])))
    if only_model or min_risk > 0:
        st.caption(f"Filters are on: {len(passing)} of {len(queue)} edits pass them.")
    unanswered = skipped(queue, "model_error")
    if unanswered:
        st.warning(missing_answers_text(unanswered, source))
    without_model = skipped(queue, "no_model")
    if without_model:
        st.info(no_model_text(without_model))
    if not shown:
        st.info("No edit matches the filters.")
        return

    for item in shown:
        _render_card(item, decided.get(review_key(item.prediction)), log, source)


if __name__ == "__main__":
    render()
