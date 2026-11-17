"""The morning list: the client's tenders for one publication day, with bid and skip buttons.

    uv run tenderradar run                      # write today's list
    uv run streamlit run app/morning_list.py

The page reads the list from the live prediction log and shows what the model wrote
about each tender. The title, buyer, country, deadline and value come from the notice's
own fields in the local database, never from the model. Without the database the page
still shows the list; each number links to the notice on TED.

A decision is appended to `data/corrections/decisions.jsonl`. A later decision on the
same tender supersedes the earlier one, and both stay in the file.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import streamlit as st

from tenderradar import report
from tenderradar.config import load_settings
from tenderradar.decisions import Decision, DecisionLog, path_for
from tenderradar.log import PredictionLog
from tenderradar.models import Condition, GatedAssessment, Notice, Shortlist
from tenderradar.store import Store

CLIENT = "client"
# The list the model has read comes first; the ranker's own list is the fallback.
PREFERRED_RANKERS = ("fused+model", "fused")
NOTICE_URL = "https://ted.europa.eu/en/notice/-/detail/{number}"
RUN_COMMAND = "uv run tenderradar run"

DECISION_LABEL = {"bid": "bid", "skip": "skip", "ask_person": "ask a person"}
MET_LABEL = {
    "yes": "the profile meets it",
    "no": "the profile does not meet it",
    "unknown": "the profile does not say",
}
SKIP_NEEDS_REASON = "A skip needs a reason. Write one and press Skip again."

_WS = re.compile(r"\s+")
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()<>#|~$!])")


def plain(text: str | None) -> str:
    """Text from a notice or from the model, made safe to place inside Markdown.

    Notice text is data. It is shown as written, on one line, and none of it is read
    as formatting, a link or an image.
    """
    return _MARKDOWN.sub(r"\\\1", _WS.sub(" ", text or "").strip())


def notice_url(publication_number: str) -> str:
    return NOTICE_URL.format(number=publication_number)


def _preference(shortlist: Shortlist) -> int:
    name = shortlist.ranker
    return PREFERRED_RANKERS.index(name) if name in PREFERRED_RANKERS else len(PREFERRED_RANKERS)


def client_lists(log_path: Path) -> dict[date, Shortlist]:
    """The client's list for each publication day, newest day first.

    A day can have several lists: one per ranker, and one more per system version if
    the day was run again after a change. The model's list wins over the ranker's, and
    among equals the one written last wins.
    """
    best: dict[date, Shortlist] = {}
    for shortlist in PredictionLog(log_path).read():
        if shortlist.profile_id != CLIENT:
            continue
        held = best.get(shortlist.day)
        if (
            held is None
            or _preference(shortlist) < _preference(held)
            or (
                _preference(shortlist) == _preference(held)
                and shortlist.written_at >= held.written_at
            )
        ):
            best[shortlist.day] = shortlist
    return dict(sorted(best.items(), reverse=True))


def notices_for(db_path: Path, day: date) -> dict[str, Notice]:
    """The day's notices by publication number, or nothing if this machine has no database."""
    if not Path(db_path).exists():
        return {}
    store = Store(db_path)
    try:
        return {notice.publication_number: notice for notice in store.notices(day, day)}
    finally:
        store.close()


def removed_text(shortlist: Shortlist) -> str:
    """How many tenders each rule removed, by rule name."""
    if not shortlist.removed:
        return "No tender was removed by a rule."
    parts = [f"`{rule}` {count}" for rule, count in sorted(shortlist.removed.items())]
    return f"Removed by rule ({sum(shortlist.removed.values())}): " + ", ".join(parts)


def deadline_text(deadline: date | None, today: date) -> str:
    if deadline is None:
        return "not stated"
    days = (deadline - today).days
    if days < 0:
        return f"{deadline.isoformat()} (passed {-days} days ago)"
    return f"{deadline.isoformat()} ({days} days from today)"


def value_text(notice: Notice) -> str:
    if notice.estimated_value is None:
        return "not stated"
    return f"{notice.currency or ''} {notice.estimated_value:,.0f}".strip()


def notice_facts(notice: Notice, today: date) -> list[tuple[str, str]]:
    """What the notice itself states, as label and value. Nothing here comes from the model."""
    lots = [lot for lot in notice.lots if lot.title or lot.description]
    return [
        ("Buyer", plain(notice.buyer_name) or "not stated"),
        ("Country", plain(notice.buyer_country) or "not stated"),
        ("Deadline", deadline_text(notice.deadline, today)),
        ("Estimated value", plain(value_text(notice))),
        ("Language", plain(notice.language) or "not stated"),
        ("Lots", str(len(lots)) if lots else "not stated"),
    ]


def verdict_text(assessment: GatedAssessment | None) -> str:
    if assessment is None:
        return "Not read by the model."
    label = DECISION_LABEL.get(assessment.decision, assessment.decision)
    return f"Model: **{label}**, fit {assessment.fit} of 100"


def reason_lines(assessment: GatedAssessment) -> list[str]:
    """The model's reason and the passage it rests on, as Markdown lines."""
    lines = [plain(assessment.reason)]
    if assessment.reason_quote:
        lines.append(f"> {plain(assessment.reason_quote)}")
    else:
        note = "The passage the model quoted for this is not in the notice."
        if assessment.downgraded:
            note += " Its decision was changed to ask a person."
        lines.append(f"*{note}*")
    return lines


def condition_lines(condition: Condition) -> list[str]:
    met = MET_LABEL.get(condition.met, condition.met)
    return [f"- {plain(condition.requirement)} ({met})", f"  > {plain(condition.quote)}"]


def decision_text(made: Decision) -> str:
    text = f"Decided: **{made.decision}** on {made.decided_at:%Y-%m-%d %H:%M} UTC."
    return f"{text} Reason: {plain(made.reason)}" if made.reason else text


def submit(
    log: DecisionLog, publication_number: str, day: date, choice: str, reason: str
) -> str | None:
    """Record a decision. Returns the message to show if it was refused, else None."""
    try:
        log.record(publication_number, day, choice, reason)
    except ValueError:
        return SKIP_NEEDS_REASON if choice == "skip" else f"Not recorded: {choice!r}."
    return None


def _decide(log: DecisionLog, publication_number: str, day: date, choice: str) -> None:
    """Button callback: record, then clear the reason field or keep the refusal to show."""
    reason_key, error_key = f"reason-{publication_number}", f"error-{publication_number}"
    error = submit(log, publication_number, day, choice, st.session_state.get(reason_key, ""))
    if error:
        st.session_state[error_key] = error
    else:
        st.session_state[reason_key] = ""
        st.session_state.pop(error_key, None)


def _render_summary(shortlist: Shortlist, have_database: bool) -> None:
    read = sum(1 for entry in shortlist.entries if entry.assessment is not None)
    st.subheader(f"Tenders published {shortlist.day.isoformat()}")
    passed, listed, by_model = st.columns(3)
    passed.metric("Passed the rules", shortlist.candidates)
    listed.metric("On the list", len(shortlist.entries))
    by_model.metric("Read by the model", read)
    st.markdown(removed_text(shortlist))
    st.caption(
        f"List written {shortlist.written_at:%Y-%m-%d %H:%M} UTC by `{shortlist.ranker}`, "
        f"system version `{shortlist.system_version}`."
    )
    if shortlist.skipped_assessments:
        st.warning(
            f"{shortlist.skipped_assessments} tenders were not read by the model: "
            "the daily spend cap was reached or the model did not answer."
        )
    elif not read and shortlist.entries:
        st.info("The model did not read this list. The order is the ranker's.")
    if not have_database:
        st.info(
            "The local database is not on this machine, so titles, buyers, deadlines and "
            "values are not shown. Each number links to the notice on TED."
        )


def _render_entry(entry, notice: Notice | None, made: Decision | None, log, day, today) -> None:
    number = entry.publication_number
    with st.container(border=True):
        title = f" {plain(notice.title)}" if notice and notice.title else ""
        st.markdown(f"**{entry.rank}.** [{number}]({notice_url(number)}){title}")
        if notice is not None:
            st.markdown(
                " · ".join(f"{label}: {value}" for label, value in notice_facts(notice, today))
            )
        st.markdown(verdict_text(entry.assessment))
        if entry.assessment is not None:
            st.markdown("\n\n".join(reason_lines(entry.assessment)))
            if entry.assessment.conditions:
                lines = ["Conditions to meet:"]
                for condition in entry.assessment.conditions:
                    lines += condition_lines(condition)
                st.markdown("\n".join(lines))
            if entry.assessment.dropped_conditions:
                st.caption(
                    f"{entry.assessment.dropped_conditions} more conditions are not shown: "
                    "the passage quoted for them is not in the notice, or they came after "
                    "the first five."
                )
        if made is not None:
            (st.success if made.decision == "bid" else st.warning)(decision_text(made))
        error = st.session_state.pop(f"error-{number}", None)
        if error:
            st.error(error)
        st.text_input(
            "Reason (needed for a skip)" if made is None else "Reason for a new decision",
            key=f"reason-{number}",
        )
        bid, skip, _ = st.columns([1, 1, 6])
        bid.button("Bid", key=f"bid-{number}", on_click=_decide, args=(log, number, day, "bid"))
        skip.button("Skip", key=f"skip-{number}", on_click=_decide, args=(log, number, day, "skip"))


def main() -> None:
    st.set_page_config(page_title="Morning list", layout="wide")
    settings = load_settings()
    st.title("Morning list")

    lists = client_lists(settings.log_dir / report.LIVE_LOG)
    if not lists:
        st.info(f"No list has been written for the client yet. Run `{RUN_COMMAND}` to write one.")
        return

    day = st.sidebar.selectbox(
        "Publication day", list(lists), format_func=lambda d: d.isoformat(), key="day"
    )
    shortlist = lists[day]
    notices = notices_for(settings.db_path, day)
    log = DecisionLog(path_for(settings))
    decisions = log.latest()

    _render_summary(shortlist, have_database=settings.db_path.exists())
    decided = sum(1 for entry in shortlist.entries if entry.publication_number in decisions)
    st.sidebar.metric("Decided", f"{decided} of {len(shortlist.entries)}")
    if not shortlist.entries:
        st.info("No tender made the list on this day.")
    today = date.today()
    for entry in shortlist.entries:
        _render_entry(
            entry,
            notices.get(entry.publication_number),
            decisions.get(entry.publication_number),
            log,
            day,
            today,
        )


if __name__ == "__main__":
    main()
