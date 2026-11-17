# Project plan

> Simulated engagement with a fictional bank. The plan shows how the work would be
> staged with a customer; the repository contains what weeks 1 to 3 produce.

## Goal

Within four weeks, level-1 analysts review prepared cases instead of building them,
with measured safety and no automatic closing until the numbers support it.

## People

| Role | Involvement |
|---|---|
| Head of financial crime (sponsor) | Agrees success criteria and go-live gates; weekly 30 minutes |
| Two level-1 analysts | Label closed alerts, test the case viewer; about 3 hours a week |
| Level-2 investigator | Reviews escalation narratives; about 1 hour a week |
| Data protection officer | Approves the gateway controls and the model channel |
| IT security lead | Read access to source systems, network path to the model |
| Forward deployed engineer | Discovery, build, evaluation, rollout |

## Timeline

| Week | Focus | Deliverables | Gate to continue |
|---|---|---|---|
| 1 | Discovery and data | Discovery notes, workflow map, user stories, 40 labelled closed alerts chosen with the analysts | Sponsor agrees success criteria; data protection officer agrees the list of fields a model may see |
| 2 | First working version | Tools over read replicas, privacy gateway, rules baseline, evaluation report | Leak scan clean; analysts confirm the labels |
| 3 | The agent | Triage and lead on the labelled set, policy gate, case viewer, failure handling | No suspicious alert closed on the labelled set; every citation valid |
| 4 | Shadow mode | The agent prepares every real alert; analysts work as before and compare; disagreements feed the labelled set | Two weeks of shadow data reviewed with the sponsor |

## Rollout after shadow mode

Each stage needs the sponsor's decision.

1. **Assisted.** Every disposition is a recommendation in the analyst's queue.
2. **Auto-close for the lowest-risk class.** Tier-one closes that pass every rule,
   with a fixed share sampled for quality checks.
3. **Widen** the class only as the sampled error rate allows.

## Go-live gates

| Gate | Measure | Threshold |
|---|---|---|
| Safety | Suspicious alerts closed on the labelled set and in shadow mode | 0 |
| Data protection | Real identifiers in anything sent to a model | 0 |
| Trust | Cited evidence that was really retrieved | 100% |
| Usefulness | Alerts arriving with a recommendation the analyst accepts | agreed with the sponsor after shadow mode |
| Cost | Cost per alert, with the spend cap in place | within the agreed budget |

## Risks

| Risk | Mitigation |
|---|---|
| Real notes are messier than the synthetic ones | Label real alerts in week 1; expect more escalations at first |
| Analysts over-trust a fluent write-up | Evidence beside every claim; sampled quality checks; overrides are logged and reviewed |
| A model or prompt change shifts behaviour | Recorded evaluation re-run before any change goes live |
| The approved model channel differs from the one used here | The provider interface isolates the vendor in one class |
