# Project plan

> Simulated engagement. The client and its people are fictional; the tracker the
> desk is measured on is real. Dates are those of the build.

## Goal

Do the first pass on every new issue within the hour: propose area and platform
labels, and point at an earlier report of the same problem where there is one. Prove
how often the desk is wrong before it is allowed to change anything on the tracker.

## People

| Role | Who | What they decide |
|---|---|---|
| Support lead | Client | Whether the desk may act; the gates below |
| Triagers on the rota (five) | Client | Each proposal, on the queue page, during the shadow run |
| A maintainer who owns the labels | Client | What a label means when the desk and the triagers disagree |
| Forward deployed engineer | Supplier | The build, the evaluation, the runbook, the handover |

## Timeline

| Week | What happens | Status |
|---|---|---|
| 0 | Discovery: two days with the rota, an afternoon on the tracker | Done (simulated) |
| 1 | Build, backtest on six months of issues, first live run | Done on 17 November 2026 |
| 2 to 5 | Shadow run: the desk answers every new issue, the rota works as before and marks each proposal on the queue page | Not started. The live log is running; nobody is reviewing it |
| 6 | Read the live scoreboard and the reviews; decide gate 2 | |
| 7 onwards | Only if gate 2 is met: apply mode for labels, on the client's own tracker | |

## Go-live gates

A gate is a number agreed before the build. The status column is what the held-out
months (issues opened September and October 2026, run once) say.

| # | Gate | Needed for | Status |
|---|---|---|---|
| 1 | The desk gets the area right on more issues than the keyword rules and than the classifier | Starting the shadow run | **Met.** 412 to 419 of 638 over three runs, against 347 for the classifier and 206 for the rules |
| 2 | Labels the desk would apply on its own are wrong at most 1 time in 20, on at least 150 live predictions | Apply mode for labels | **Not met on the backtest.** 134 to 135 wrong of 520 to 525 (26%); half of those are on issues the maintainers left without any label. Not yet measurable live |
| 3 | A new issue is called a duplicate at high confidence in at most 1 of 100 issues | A comment on a duplicate | **Not met by the maintainers' record** (19 to 20 of 754), and the record cannot settle it: 18 of the 20 name an earlier report of the same defect when read. Needs maintainers to judge a sample |
| 4 | The first pass costs under one US dollar per 1,000 issues | Running it at all | **Met.** US$0.89 per 1,000 |

What follows from the table: the shadow run can start, and nothing in the backtest
supports switching apply mode on. That is the recommendation.

## Risks

| Risk | What is done about it | What remains |
|---|---|---|
| The desk is wrong and nobody notices | Shadow mode by default; every answer logged with its system version; scored fourteen days later | The scoreboard has no live score until 30 November 2026 |
| Triagers agree with proposals without checking | The queue shows the confidence and why a person was asked; corrections are kept | Not measured. Compare review decisions with the outcomes after the shadow run |
| An issue tells the model what to do | Text passed as data; the gate drops labels outside the taxonomy and duplicates that were not retrieved; nothing can be closed | A label that is in the taxonomy and wrong passes the gate |
| The maintainers' labels drift | A health check fails the run when an unknown label appears; `live_cut` retrains | Scores fell from the development months to the held-out months for every configuration, the untuned baselines included |
| The reference for duplicates is too narrow to judge the duplicate step | A written review of the calls it does not confirm | That review is the AI's. Maintainers have to judge a sample |
| GitHub changes a response shape or a limit | Typed parsing, `FeedError`, four feed checks, a job that fails loudly | The schedule has never run on GitHub |
| Spend | A daily cap in code, checked before every call | None known. A day costs about one US cent |
| Issue text is someone else's | Never committed; a local store; the demo runs on invented issues | The model provider receives public issue text in a live run |
