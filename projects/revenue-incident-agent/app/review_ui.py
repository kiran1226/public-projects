"""The review queue.

    uv run revwatch replay --world dev      # fill the queue
    uv run revwatch admin --world dev       # the mock admin service, in another terminal
    uv run streamlit run app/review_ui.py

A reviewer sees each incident with its evidence, the full tool-call trace, the
proposed mitigation and every check that held it, and approves, corrects the
cause, or rejects. Approving an incident whose proposal passed every check sends
that action to the admin service, once. Nothing else on this page changes
anything outside the queue.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import get_args

import pandas as pd
import streamlit as st

from revwatch.actions import DEFAULT_URL, Executor, approve
from revwatch.guardrails import REASONS
from revwatch.scenarios import Cause
from revwatch.store import Record, Store, StoreError
from revwatch.tools import Toolbox
from revwatch.warehouse import Warehouse

PROJECT = Path(__file__).resolve().parents[1]
STORE = Path(os.environ.get("REVWATCH_STORE", PROJECT / "var" / "revwatch.sqlite"))
WORLD = Path(os.environ.get("REVWATCH_WORLD", PROJECT / "data" / "worlds" / "dev.duckdb"))
ADMIN_URL = os.environ.get("REVWATCH_ADMIN_URL", DEFAULT_URL)
STATUSES = (
    "open",
    "action_failed",
    "action_pending",
    "mitigated",
    "approved",
    "corrected",
    "rejected",
)
CHART_HOURS = 36

st.set_page_config(page_title="Incident review queue", layout="wide")


def label(record: Record) -> str:
    where = ", ".join(f"{k}={v}" for k, v in record.slice.items()) or "all"
    return f"{record.severity.upper()}  {record.metric}  {where}  ({record.incident_id[-6:]})"


def series(record: Record) -> pd.DataFrame | None:
    """Observed against the usual value for the alert's slice, up to the alert hour."""
    if not WORLD.exists():
        return None
    with Warehouse(WORLD) as warehouse:
        if warehouse.world_hash != record.world_hash:
            return None
        now = record.alert_tick + 1
        start = warehouse.hour(max(0, now - CHART_HOURS))
        result = Toolbox(warehouse, now).call(
            "metric_series",
            {
                "metric": record.metric,
                "slice": record.slice,
                "start": f"{start:%Y-%m-%dT%H:%M}",
                "end": f"{start + timedelta(hours=min(CHART_HOURS, now)):%Y-%m-%dT%H:%M}",
            },
        )
    if result.error or not result.rows:
        return None
    frame = pd.DataFrame(result.rows).set_index("key")
    return frame[["observed", "expected"]].rename(columns={"expected": "usual for this hour"})


def show(record: Record, store: Store, reviewer: str) -> None:
    finding = record.finding
    side = "above" if record.direction > 0 else "below"
    where = ", ".join(f"{k}={v}" for k, v in record.slice.items()) or "the whole business"
    st.header(f"{record.metric} {side} expected for {where}")
    st.caption(
        f"{record.incident_id} · alert hour {record.opened_at:%Y-%m-%d %H:%M} · "
        f"detector {record.detector} · investigator {record.investigator}"
        + (f" ({record.model})" if record.model else "")
    )
    a, b, c, d = st.columns(4)
    a.metric("Status", record.status)
    b.metric("Severity", record.severity)
    c.metric("Impact per hour", f"EUR {record.impact_eur_per_hour:,.0f}")
    d.metric("Owner", record.owner)

    chart = series(record)
    if chart is None:
        st.info("No chart: the world this incident came from is not at REVWATCH_WORLD.")
    else:
        st.line_chart(chart)

    st.subheader("Finding")
    st.write(f"**{finding.cause}**, {finding.confidence} confidence. Change blamed: "
             f"{finding.change_id or 'none'}.")  # fmt: skip
    st.write(finding.summary)
    st.write(f"Evidence: {record.evidence_ok} of {record.evidence_total} cited values recompute.")
    if finding.evidence:
        st.dataframe(pd.DataFrame([c.model_dump() for c in finding.evidence]), hide_index=True)

    st.subheader("Proposed mitigation")
    if record.proposal is not None:
        st.success(f"{record.proposal.action} on {record.proposal.target}. Passed every check.")
    elif finding.mitigation is not None:
        st.warning(
            f"Withheld: {finding.mitigation.action} on {finding.mitigation.target}. "
            "It cannot be executed from here."
        )
    else:
        st.write("None.")
    for reason in record.reasons:
        st.error(f"{reason['code']}: {reason['detail']}  \n_{REASONS.get(reason['code'], '')}_")

    with st.expander(f"Trace: {record.tool_calls} tool calls"):
        for call in record.trace:
            st.code(f"{call['call_id']} {call['name']} {call['args']}")
            if call["error"]:
                st.write(f"error: {call['error']}")
            elif call["rows"]:
                st.dataframe(pd.DataFrame(call["rows"]), hide_index=True)

    action = store.action_for(record.incident_id)
    if action is not None:
        st.subheader("Action")
        st.write(f"{action.action} on {action.target}: **{action.status}** after "
                 f"{action.attempts} attempt(s). {action.detail}")  # fmt: skip

    st.subheader("Decision")
    key = record.incident_id
    note = st.text_input("Note", key=f"note-{key}")
    if record.status in ("open", "action_failed", "action_pending"):
        retry = record.status != "open"
        text = (
            "Retry the action"
            if retry
            else "Approve and execute"
            if record.proposal is not None
            else "Approve the cause"
        )
        if st.button(text, key=f"approve-{key}", type="primary"):
            status = approve(store, Executor(store, ADMIN_URL), key, reviewer, note)
            st.session_state["flash"] = f"{key}: {status}"
            st.rerun()
    if record.status == "open":
        causes = [c for c in get_args(Cause) if c != finding.cause]
        cause = st.selectbox("The cause is", causes, key=f"cause-{key}")
        left, right = st.columns(2)
        if left.button("Correct the cause", key=f"correct-{key}"):
            store.correct(key, reviewer, cause, note)
            st.session_state["flash"] = f"{key}: corrected to {cause}"
            st.rerun()
        if right.button("Reject: not an incident", key=f"reject-{key}"):
            store.reject(key, reviewer, note)
            st.session_state["flash"] = f"{key}: rejected"
            st.rerun()

    with st.expander("Event log"):
        st.dataframe(pd.DataFrame(store.events(key)), hide_index=True)


def main() -> None:
    st.sidebar.title("Incident review")
    if not STORE.exists():
        st.info(f"No queue at {STORE}. Run `uv run revwatch replay --world dev` first.")
        return
    store = Store(STORE)
    if "flash" in st.session_state:
        st.toast(st.session_state.pop("flash"))
    reviewer = st.sidebar.text_input("Reviewer", value="reviewer")
    status = st.sidebar.selectbox("Show", STATUSES)
    records = store.incidents(status)
    st.sidebar.caption(f"{len(records)} incident(s), most urgent first")
    if not records:
        st.info(f"Nothing is {status}.")
        return
    by_label = {label(record): record for record in records}
    chosen = st.sidebar.radio("Incidents", list(by_label))
    try:
        show(by_label[chosen], store, reviewer)
    except StoreError as exc:
        st.error(str(exc))


main()
