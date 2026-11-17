# Workflow map

> Simulated engagement with a fictional distributor. Minutes are assumptions used for
> the impact estimate, not measurements.

## Before

| # | Step | Who | Minutes a day | Pain |
|---|---|---|---:|---|
| 1 | Open the alert emails, one per saved search | Bid manager | 5 | Publication order, no ranking; the same tender in several emails |
| 2 | Read titles, open the notices that look right | Bid manager | 25 | Three languages; a broad category brings noise, a narrow one misses tenders |
| 3 | Read the conditions for tenderers | Bid manager | 20 | A third of this time goes on tenders that one condition rules out |
| 4 | Type the tender and its deadline into the shared spreadsheet | Bid manager | 5 | Typed by hand |
| 5 | Decide what to bid on | Sales director, weekly | 5 | Reasons for skipping are not recorded |
| | **Total per day** | | **60** | |

## After

| # | Step | Who | Minutes a day | What changed |
|---|---|---|---:|---|
| 1 | Read the feed for new tenders and awards | Radar (code) | 0 | Every notice in scope, once, however many codes it carries |
| 2 | Hard rules: deadline, territory, value band, procedure | Radar (code) | 0 | Named rules; the list says how many each one removed |
| 3 | Rank: categories, text meaning, buyer history | Radar (code and a local embedding model) | 0 | Works across languages; a known buyer rises |
| 4 | Read the first ten against the profile | Radar (LLM) | 0 | A decision, a reason and the conditions, each with a quoted passage |
| 5 | Gate: every quoted passage must be in the notice | Radar (code) | 0 | An unsupported "skip" becomes "ask a person" |
| 6 | Read the morning list, open what is marked bid or ask | Bid manager | 15 | Starts at the top; deadline and value come from the notice's own fields |
| 7 | Mark bid or skip with a reason | Bid manager | 3 | Kept, and usable to revise the profile |
| 8 | Decide what to bid on | Sales director, weekly | 5 | Works from the marked list |
| | **Total per day** | | **23** | |

## Who decides what

| Decision | Before | After |
|---|---|---|
| Which tenders are in scope | Saved searches, set up once | The profile file: categories, countries, value band, exclusions |
| Which tender is read first | Publication order | The ranking |
| Whether a condition rules the company out | Bid manager, after reading | The model proposes with the passage shown; the bid manager confirms |
| Whether to bid | Sales director | Sales director. The radar never decides this |
| The deadline | Typed by hand | The notice's own field |

## What reaches a person, and why

| What | Who | Why |
|---|---|---|
| Every listed tender | Bid manager | The radar orders a reading list. It does not file bids or dismiss tenders |
| A tender marked "ask a person" | Bid manager | The fit turns on something neither the profile nor the notice settles, or the model's quoted passage was not in the notice |
| A tender listed without an assessment | Bid manager | It was below the tenth place, the daily spend cap was reached, or the model call failed. The list says which |
| A failed run | Service owner | The feed did not answer, changed its format, went stale or sent far fewer notices than usual |
