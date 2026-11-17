# Project plan

> Simulated engagement with a fictional distributor. The plan is what would be agreed
> with the client; the dates are relative because no real rollout took place.

## Goal

The bid desk of Tarnwell Lab Supply reads a ranked list of at most 15 new tenders each
morning instead of category alerts, with the deadline, the value and the conditions
that would rule the company out, and can show that the list finds what matters.

## People

| Role | Who | What they decide |
|---|---|---|
| Sponsor | Sales director | Go or no-go at each gate; which tenders to bid on |
| Users | Three bid managers | Whether the list is usable; bid or skip on each tender |
| Profile owner | Senior bid manager | Categories, countries, value band and exclusions in the profile file |
| Service owner | One engineer | The scheduled run, the spend cap, the feed checks |

## Timeline

| Week | Work | Output |
|---|---|---|
| 1 | Discovery with the bid desk; read the feed; check that awards name winners and link to tenders | Discovery notes, the risk checks in the decision log |
| 2 | Feed client, parser, store, supplier identity, backfill | History in a local store |
| 3 | Rules, rankers, the backtest on real suppliers | Evaluation report without the model |
| 4 | Model step, gate, spend cap; the held-out month scored once | Evaluation report with the model; gate 1 |
| 5 to 8 | Shadow run next to the alerts; the desk marks bid or skip | Decision log of the desk; gate 2 |
| 9 | Alerts switched off for one bid manager | Gate 3 |
| 10 on | All three bid managers; profile revised from the decisions each month | |

## Go-live gates

Each gate is a measurement, and the sponsor decides on it.

| Gate | Condition | Measured by | Status at the end of the build |
|---|---|---|---|
| 1. Start the shadow run | On the held-out month, the fused ranker has more of the later-won tenders in its first three places than the category alert, by at least ten points | `tenderradar eval`, held-out table | **Met.** 197 of 270 (73%) against 133 (49%) |
| 1. Start the shadow run | The model marks "skip" on no more than 1 in 20 of the tenders a supplier went on to win | `tenderradar eval`, model sample | **Not met.** 18 of 176 on the held-out sample, about 1 in 10 |
| 1. Start the shadow run | No quoted passage reaches the list that is not in its notice | The gate, and `tests/test_fixture_demo.py` | Met by construction and tested |
| 2. Switch off the alerts for one bid manager | Over four weeks of shadow running, every tender the desk chose to bid on was in that morning's list | The desk's decisions against the live log | Not started: needs the desk |
| 2. Switch off the alerts for one bid manager | No failed run went unnoticed for more than one working day | Workflow history | Not started |
| 3. All bid managers | One month with no tender found by the alerts and missed by the list | The remaining alerts against the live log | Not started |

A gate that is not met stops the part of the rollout it guards. Here that is the
model's decision, not the list: the shadow run can start with the ranking and with the
model's reasons and conditions shown, and with "skip" not reordering the list until it
is right more often. The first thing to try is a richer profile for the model to
read, and a new month to measure it on.

## Risks

| Risk | Likelihood | Effect | What is done about it |
|---|---|---|---|
| The feed changes its format or its limits | Medium | No list | Every response is parsed into typed records; a response that does not parse fails the run; the runbook says where the field list lives |
| The model takes the right tender off the top | Low | A missed bid | The gate; "skip" is ordered last but stays on the list; the worst error is counted in every evaluation |
| The profile drifts from what the company sells | Medium | A stale list | Bid and skip decisions are kept with reasons; the profile owner reviews them monthly |
| A tender is filed under an unusual category code | Medium | It never passes the alert, and is ranked low here | Embeddings rank on the text, not only the code; the scope is three whole divisions |
| Spend grows unnoticed | Low | Cost | A daily cap in code, read from the log; the scoreboard shows cost per day |
| The run stops and nobody notices | Medium | No list for days | A failed run fails the workflow, which sends GitHub's notice; the scoreboard shows the last run |
| Awards misattributed to a supplier | Medium | Noise in the evaluation, none in the daily list | Conservative clustering; said in the report's limits |
