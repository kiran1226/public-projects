# Workflow map

> Simulated engagement with a fictional platform. Minutes and counts per shift are
> assumptions used for the impact estimate, not measurements.

## Before

| # | Step | Who | Per edit | Pain |
|---|---|---|---|---|
| 1 | An edit lands in the list, newest first | The platform | none | No order but the clock |
| 2 | Open the next edit | Reviewer | 5 seconds | About 94 in 100 are fine |
| 3 | Read what changed | Reviewer | 25 seconds | Nothing says where to look |
| 4 | Undo it, or move on | Reviewer | 5 seconds | The bad edit a reviewer never opened stays |
| | **A reviewer's shift** | | **about 400 edits** | **About 24 of them worth the time** |

## After

| # | Step | Who | Time | What changed |
|---|---|---|---|---|
| 1 | Read the stream, keep article edits by people | Cascade (code) | milliseconds | Reconnects from the last event after a drop |
| 2 | Score every edit from the event's own fields | Cascade (classical ML) | about 50 milliseconds for a batch | Every edit has a place in the queue at once |
| 3 | For the riskiest fifth: read what the edit changed | Cascade (code) | up to 5 seconds | One request for up to 25 edits |
| 4 | The model reads the change | Cascade (LLM) | 0.6 seconds, median | A risk, a category, the lines to look at. No sentence |
| 5 | Gate, then one risk from two tiers | Cascade (code) | none | A malformed answer cannot move an edit |
| 6 | Work the queue from the top | Reviewer | 25 seconds an edit | The marked lines say where to look |
| 7 | Undo it, or mark it fine | Reviewer | 5 seconds | The decision is kept and compared with the outcome |
| | **A reviewer's shift** | | **about 400 edits** | **About 85 to 90 of them worth the time, if the reviewers work the top 5% of the queue: on the held-out days those 75 edits of 1,500 held 16 to 17 reverted ones, where the clock gives 4** |

## Who decides what

| Decision | Before | After |
|---|---|---|
| Which edit is read next | The clock | The risk order |
| Whether an edit is bad | Reviewer | Reviewer. The cascade never decides this |
| Whether to undo it | Reviewer | Reviewer. The cascade has no way to edit |
| Which edits a model reads | n/a | The share of the stream set in the configuration, by tier-1 score |
| What is given up under load | n/a | Model reads, in order of arrival: the edit keeps its tier-1 score and the skip is counted |

## What changes for the people

- Reviewers stop reading by the clock. The risk is that they stop looking below the
  top of the queue; the evaluation counts the reverted edits that sit in the bottom
  half for exactly that reason.
- The lead gets a number for what the ordering is worth, and a second for what it
  costs.
- The platform engineer gets a latency target, a load test and a count of what was
  skipped.
