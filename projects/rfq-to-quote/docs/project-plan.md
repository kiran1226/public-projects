# Project plan

> Simulated engagement with a fictional distributor. The plan shows how the work
> would be staged with a customer; the repository contains what weeks 1 to 3 produce.

## Goal

Within four weeks, the inside-sales desk reviews drafted quotes instead of building
them, with measured accuracy and no automatic sending until the numbers support it.

## People

| Role | Involvement |
|---|---|
| Head of sales (sponsor) | Agrees success criteria and go-live gates; weekly 30 minutes |
| Two sales reps | Label sample requests, test the review queue; about 3 hours a week |
| Pricing lead | Owns discount table, margin floor and sign-off limit |
| IT contact | Mailbox access, catalog export |
| Forward deployed engineer | Discovery, build, evaluation, rollout |

## Timeline

| Week | Focus | Deliverables | Gate to continue |
|---|---|---|---|
| 1 | Discovery and data | Discovery notes, workflow map, catalog export, 30 labelled requests chosen with the reps | Sponsor agrees success criteria and the list of hold reasons |
| 2 | First working version | Pipeline end to end on the samples, baseline and LLM eval report, flow diagram | Reps confirm the labels and the hold reasons match their judgement |
| 3 | Reliability | Prompt iteration against the eval, guardrail thresholds confirmed by pricing lead, review queue, failure handling | No unsafe auto-approval on the labelled set; routing recall at 100% |
| 4 | Shadow mode | Agent drafts every real request, reps keep working as before and compare; corrections flow into the eval set | Two weeks of shadow data reviewed with the sponsor |

After the shadow period, rollout is staged, and each stage needs the sponsor's
decision:

1. **Assisted** (`--assisted`). Every quote is reviewed in the queue; nothing is sent
   automatically.
2. **Auto-approve for the simplest class.** Small orders with confident matches
   only, with a daily sample checked by a rep.
3. **Widen** the auto-approve class as the correction rate allows.

## Go-live gates

| Metric | Gate for assisted mode | Gate for any auto-approval |
|---|---|---|
| Must-review quotes that reach a human | 100% on the labelled set | 100% on the labelled set and in shadow data |
| Unsafe auto-approvals | 0 on the labelled sets | 0 in about 300 shadow quotes that would have been auto-approved |
| End-to-end line accuracy | Reported, no threshold | Agreed with the sponsor from shadow data |
| Reviewer time per held quote | Under 4 minutes median | Same |

## Risks

| Risk | Mitigation |
|---|---|
| The sample is not representative | Reps pick the 30 requests, including the worst ones; shadow mode adds real cases weekly |
| A line is silently missed and nothing flags it | Measured in the eval; daily sampling of auto-approved quotes; corrections become eval cases |
| Reps stop trusting it after one bad match | The matcher may decline; held quotes always show the reason and the source |
| Catalog names drift from customer names | Synonyms live in the catalog data; unmatched lines are reviewed monthly |
| Model or price changes | Provider behind one interface; recorded responses pin the behaviour that was evaluated |
| Customer data in prompts | Only the request text and catalog candidates are sent; retention terms agreed with IT before shadow mode |

## What I report each week

- The eval table, with the change since last week
- Requests the agent got wrong and what was changed
- Open decisions that need the sponsor
