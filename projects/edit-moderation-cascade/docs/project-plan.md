# Project plan

> Simulated engagement. The client and its people are fictional; the stream the
> cascade is measured on is real. Dates are those of the build.

## Goal

Put the edits most likely to be bad at the top of the reviewers' queue within
seconds, at a known cost per 1,000 edits, without a model call on every edit. Prove
what the ordering is worth, and what the system gives up under load, before anyone
relies on it.

## People

| Role | Who | What they decide |
|---|---|---|
| Trust and safety lead | Client | Whether reviewers work from the ordered queue; the criteria below |
| Reviewers (twelve, in shifts) | Client | Every edit. The cascade decides none |
| Platform engineer | Client | The latency target, the queue bound, where the loop runs |
| Budget owner | Client | The daily cap, and whether a model may read every edit |
| Policy owner | Client | That no record names an editor |
| Forward deployed engineer | Supplier | The build, the evaluation, the runbook, the handover |

## Timeline

| Week | What happens | Status |
|---|---|---|
| 0 | Discovery: two days with the review team, a conversation each with the platform engineer and the budget owner | Done (simulated) |
| 1 | Build, backtest on three weeks of edits, load test, first live run | Done on 17 November 2026 |
| 2 to 3 | The live loop orders ten minutes of every hour and reads each edit's outcome 48 hours later. Reviewers work as before | Not started: the schedule has never run on GitHub |
| 3 | Tier 1 gets features from the change itself; the backtest is run again on new held-out days | Not built |
| 4 | Read the live scoreboard; decide whether the model tier stays, and whether a model may read every edit | |
| 5 onwards | Reviewers use the ordered queue next to their usual list for a week; their decisions are compared with the outcomes | |

## Success criteria

Agreed before the build. The status column is what the held-out days say: a random
1,500 edits of 13 to 15 November 2026, 74 of them later reverted, run once.

| # | Criterion | Status |
|---|---|---|
| 1 | The top 10% of the queue holds at least four times as many reverted edits as arrival order | **Not met by the cascade.** Arrival order holds 7 of 74. The cascade holds 21 to 23 with Haiku over three runs (three times) and 27 with Sonnet. Haiku on every edit holds 32 and Wikimedia's own model 31 |
| 2 | The cascade does at least as well in the top 5% as tier 1 alone, or the model tier is not worth its cost | **Met, not established.** 16 to 17 of 74 with Haiku against 14: two or three edits, which is what three runs of one configuration differ by |
| 3 | An edit the model reads is in the queue within 15 seconds at the 95th percentile, at the stream's real rate | **Met, after a fix.** 7.1 and 7.8 seconds in the second and third live runs and 6.8 in the load test, with a median of about 4. The first live run took 17.9 and exposed a defect in the loop. In the load test the target holds at twice the stream's rate and is missed at five times |
| 4 | The whole cascade costs under one US dollar per 1,000 edits | **Met.** US$0.03 with Haiku, US$0.50 with Sonnet |

What follows from the table: order the queue by risk now, with tier 1, which costs
nothing. Do not treat the model tier as proven. Decide about it from the live log,
which gives in a day ten times the edits this evaluation had.

## Risks

| Risk | What is done about it | What remains |
|---|---|---|
| Reviewers stop looking below the top of the queue | The evaluation counts the reverted edits in the bottom half: 13 to 16 of 74 | Not measured with reviewers. No reviewer has used the page |
| The model tier adds nothing | Three runs, a tier-1-only line on the scoreboard, the same edits for every configuration | On 74 reverted edits it cannot be told from tier 1 alone |
| Tier 1 passes too few bad edits to the model | Counted: its slice held 28 of 74 | Features from the change itself are not built |
| An edit tells the model what to do | The change is passed as data; the answer is three fields, each checked; the system repeats none of the model's words | A well-formed low risk on a bad edit passes the gate. The tier-1 score still counts |
| The stream runs faster than the model tier | A bounded queue and a count of what was skipped; a load test to 50 times the rate | The load test is a simulation. One live run of ten minutes |
| Wikimedia limits or blocks the client | One request every five seconds, `maxlag`, a stop when told to wait over two minutes | The client gives no contact address, which Wikimedia asks for |
| Spend | A daily cap in code, spent evenly through the day | A full day under the cap was never run |
| It becomes a tool against editors | No field for an account name in any record; summaries scrubbed; a test for each | Who made an edit is the strongest signal there is. The system gives it up and scores lower for it |
| Page text is someone else's | Never committed; a local store; the demo runs on an invented wiki | The model provider receives public page text in a live run |
