# Project plan

> Simulated engagement with a fictional retailer. The plan shows how the work
> would be staged with a customer; the repository contains what weeks 1 to 4
> produce, on synthetic data.

## Goal

Within six weeks, the on-call receives an alert within hours of an incident
starting, with a named slice, verified evidence and a draft cause, and approves
any mitigation in a queue. Nothing acts on its own.

## People

| Role | Involvement |
|---|---|
| Head of e-commerce (sponsor) | Agrees success criteria and go-live gates; weekly 30 minutes |
| On-call engineers (rota of four) | Label past incidents, test the queue; about 2 hours a week |
| Payments, marketing and pricing leads | Confirm causes and the mitigation catalogue; about 1 hour a week |
| Analytics owner | Metric definitions, access to hourly aggregates |
| Platform engineer | Read access to the change sources; admin API credentials for stage 3 |
| Forward deployed engineer | Discovery, build, evaluation, rollout |

## Timeline

| Week | Focus | Deliverables | Gate to continue |
|---|---|---|---|
| 1 | Discovery and data | Discovery notes, workflow map, user stories, metric definitions, a list of past incidents with causes | Sponsor agrees success criteria; analytics owner agrees the metric definitions |
| 2 | Detection | Detector ladder, false-alarm control, grouping, detection evaluation against the static baseline | The ML detector finds the labelled incidents the dashboards missed, at a false-alert rate the on-call accepts |
| 3 | Investigation | Read-only tools, scripted investigator, evidence verifier, guardrails, severity and routing, queue | Every cited number recomputes; no unsafe proposal on the labelled set |
| 4 | The agent and the queue | Agent loop with recorded runs, review page, mock admin service, executor | The agent is compared with the script on the same alerts; the sponsor sees who won and at what cost |
| 5 to 6 | Shadow mode | The system runs on live hourly data and posts to a channel nobody is paged from; the on-call compares it with what they found themselves; disagreements become labels | Two weeks of shadow data reviewed with the sponsor |

## Rollout after shadow mode

Each stage needs the sponsor's decision.

1. **Paging.** High-severity incidents page. Mitigations are shown as proposals.
2. **Approved mitigations.** A person approves each proposal in the queue, and
   the executor carries it out.
3. **Auto-execution for one class**, only after enough clean proposals in that
   class to bound the error rate.

## Go-live gates

| Gate | Measure | Threshold | Where the synthetic evaluation stands |
|---|---|---|---|
| Safety | Unsafe mitigation proposals on the labelled sets and in shadow mode | 0 | **Not met.** The script made 1 in 39 alerts. The agent made 0 in the published run and 1 in 117 alerts over three runs on the same model: one repeat run made a proposal that passed every check. A check was added afterwards and stops that proposal on replay; it has not been tested on a run recorded after it was written |
| Trust | Cited values that recompute | 100% | **Not met as written.** 100% in the published run; in two repeat runs 264 of 267 and 81 of 82. Each value that did not verify was caught by `EVIDENCE_NOT_VERIFIED` and the finding reached the reviewer marked as held. What holds is the weaker statement: no unverified value reaches a reviewer unmarked |
| Detection | Labelled incidents found, and hours to detect | agreed with the sponsor after week 2 | 14 of 14 on the development world, 5 of 7 held-out; two hours for a sharp break, five to seven for a slow one |
| Noise | False pages per week | at most one, agreed with the on-call rota | 0.1 on the development world, 0 held-out |
| Cost | Model cost per alert | within the agreed budget | about US$0.002 on `claude-haiku-5-5` |

The two gates that are not met are the reason the rollout above starts with
proposals a person approves, and why auto-execution needs its own evidence.
Neither would be signed off on these numbers: four runs on two synthetic worlds
give no rate for a rare failure.

## Risks

| Risk | Mitigation |
|---|---|
| Real data is harder than the synthetic world: holidays, launches, tracking changes | Shadow mode before paging; thresholds re-tuned on real history; the held-out discipline kept |
| The on-call over-trusts a fluent brief | Evidence beside every claim; the proposal is withheld unless every check passes; overrides are logged and reviewed |
| A promotion larger than any before it draws alerts | Calendar check closes what the event explains; the report counts alerts per planned event |
| An admin API has no idempotency key | Stage 2 does not start for that action until it has one, or the action stays manual |
| A model or prompt change shifts behaviour | Recorded evaluation re-run before any change goes live; the recording key covers prompt, tools and model |
