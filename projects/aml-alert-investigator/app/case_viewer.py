"""Analyst case viewer: the queue, the agent's recommendation with real values restored,
the audit trail, and approve or override.

    uv run streamlit run app/case_viewer.py
"""

from __future__ import annotations

import json

import streamlit as st

from amlagent.store import CaseStore

st.set_page_config(page_title="AML alert desk", layout="wide")
store = CaseStore()
cases = store.list()
if not cases:
    st.info("No cases yet. Run `uv run aml-agent run --config baseline` first.")
    st.stop()

# A link such as ?config=routed&alert=AL-011 opens that case.
params = st.query_params
configs = sorted({c.config for c in cases})
config = st.sidebar.selectbox(
    "Configuration",
    configs,
    index=configs.index(params["config"]) if params.get("config") in configs else 0,
)
queues = ["all", "analyst_review", "senior_review", "manual_investigation", "auto_closed"]
queue = st.sidebar.selectbox("Queue", queues)
reveal = st.sidebar.toggle("Show real values (analyst view)", value=True)
shown = [c for c in cases if c.config == config and queue in ("all", c.queue)]
if not shown:
    st.info("No case in this queue.")
    st.stop()
ids = [c.alert_id for c in shown]
selected = st.sidebar.radio(
    "Alert", ids, index=ids.index(params["alert"]) if params.get("alert") in ids else 0
)

result, vault = store.get(selected, config)
show = vault.reidentify if reveal else (lambda value: value)

st.title(f"{result.alert_id}: {result.queue.replace('_', ' ')}")
left, middle, right = st.columns(3)
left.metric("Handled by", result.tier)
middle.metric("Model steps", len(result.steps))
right.metric("Cost", f"${result.cost_usd:.4f}")
if result.hard_flags:
    st.warning("Hard flags: " + ", ".join(result.hard_flags))
for rule in result.rules:
    st.error(f"{rule.code}: {rule.message}")

d = result.disposition
if d is None:
    st.info("No recommendation. Investigate this alert manually.")
else:
    st.subheader("Recommendation")
    st.markdown(
        f"**{d.outcome.replace('_', ' ')}** ({d.reason_code or d.typology or 'no code'}), "
        f"confidence {d.confidence}"
    )
    if d.narrative:
        st.markdown(show(d.narrative))
    for finding in d.findings:
        evidence = ", ".join(show(e) for e in finding.evidence_ids)
        st.markdown(f"- {show(finding.statement)} (evidence: {evidence})")
    for question in d.open_questions:
        st.markdown(f"- Open question: {show(question)}")

if result.triage_notes:
    st.caption("Triage notes: " + show(result.triage_notes))
for entry in result.sensitive_requests:
    state = "granted" if entry["granted"] else "denied"
    st.warning(
        f"Sensitive category request ({state}) for {show(entry['counterparty'])}: "
        f"{entry['justification']}"
    )

with st.expander("Audit trail"):
    for number, step in enumerate(result.steps, 1):
        st.markdown(
            f"**{number}. {step.role}** · {step.model} · {step.input_tokens} in / "
            f"{step.output_tokens} out · ${step.cost_usd:.4f}"
        )
        for call in step.tool_calls:
            st.code(f"{call.name}({show(json.dumps(call.input))})\n{show(call.output)[:800]}")

st.subheader("Decision")
note = st.text_input("Note")
approve, override = st.columns(2)
if approve.button("Approve"):
    store.decide(result.alert_id, config, "approved", note)
    st.rerun()
if override.button("Override"):
    store.decide(result.alert_id, config, "overridden", note)
    st.rerun()
for decision in store.decisions(result.alert_id, config):
    st.caption(
        f"{decision['decided_at'][:19]} {decision['analyst']}: {decision['decision']} "
        f"{decision['note']}"
    )
