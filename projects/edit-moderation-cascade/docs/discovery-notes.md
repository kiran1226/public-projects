# Discovery notes

> This is a simulated engagement. "Calder Commons", a platform where users edit shared
> reference pages, is fictional, and what its people say below is written for the
> exercise. The stream the system is built and measured on is real: edits to English
> Wikipedia articles, read from Wikimedia's public event stream. This project is not
> affiliated with Wikimedia or Wikipedia. It reads; it never edits or reverts.

## Who and what

Calder Commons hosts reference pages that anyone can edit. Twelve reviewers, in
shifts, read new edits and undo the bad ones. Discovery was two days with the review
team and their tooling, and a conversation each with the platform engineer and the
person who owns the review budget.

## The request

The trust and safety lead, in the first conversation:

> "Thousands of edits an hour. My reviewers see them in the order they arrive, and
> most are fine. I want the bad ones at the top of the queue within seconds, I want
> the cost per thousand edits, and I do not want a model call on every edit."

## How an edit is reviewed today

1. Every edit lands in one list, newest first.
2. A reviewer opens an edit, reads what changed, and either moves on or undoes it.
3. A reviewer gets through a few hundred edits in a shift. The list grows faster
   than that, so most edits are never opened by a reviewer.
4. What is caught is caught by luck of timing, or by a regular editor who watches
   the page.

## What we observed

Measured on the stand-in stream, in an experiment before the build and in the first
hours of the build. The evaluation later measured three weeks of sampled history:
6,195 of 91,628 edits by people were reverted within 48 hours: 7 in 100 on the first
nineteen days and 5 in 100 on the last two.

| Observation | Detail |
|---|---|
| Volume | About one article edit by a person every second: 695 in a ten-minute recording, roughly 100,000 a day. The stream as a whole carries about 40 events a second across all wikis |
| How many are bad | About 6 in 100: 359 of 5,758 edits by people were reverted in a sample taken before the build |
| Bots | Bot edits are almost never reverted (10 of 2,242 in a sample taken before the build). They are left out |
| How fast reverts happen | Of 8 reverts checked before the build, 3 happened within a minute and all within 11 hours. Part of the damage is gone before any reviewer could act |
| What the event says | Size change, edit summary, whether the editor is logged in, time, the gap to the page's previous edit. Not what was changed: that needs a second request |
| The platform's own model | Wikimedia publishes a revert-risk model with a public endpoint. It answers in about a third of a second per edit |
| Rate limits | A burst of eleven requests to the wiki API was refused. One request every five seconds was not |
| Names | Edit summaries name other editors, especially in reverts |

Things the team said that shaped the design:

- "I do not need a verdict. I need the queue in a better order than the clock."
- "Show me the part of the change you are worried about. I will decide."
- The platform engineer: "Tell me what it drops when it cannot keep up. Everything
  drops something."
- The budget owner: "Per thousand edits, per tier. And a hard cap per day."
- The policy owner: "It ranks edits. It does not rank people. No names in its logs."

## User stories

| # | As | I need | So that |
|---|---|---|---|
| 1 | a reviewer | my queue ordered by risk, each entry showing the change with the risky part marked and the kind of problem named | I spend my shift on edits that need a person |
| 2 | the trust and safety lead | to see how many of the edits later reverted my reviewers would have reached, next to arrival order and the platform's own model | I know what the ordering is worth |
| 3 | the platform engineer | to see that it keeps up with the stream inside its latency target, and what it gives up first when it cannot | I can size it and trust it under load |
| 4 | the budget owner | the cost per thousand edits for each tier, under a daily cap | the bill cannot surprise me |
| 5 | the policy owner | to know it only orders a queue: it never edits, never reverts, and keeps no record of who made an edit | it cannot become a tool against editors |

## Success criteria

Agreed before the build, to be read off the held-out days:

- The top 10% of the queue holds at least four times as many reverted edits as
  arrival order does.
- The cascade does at least as well in the top 5% as tier 1 alone, or the model tier
  is not worth its cost.
- An edit the model reads is in the queue within 15 seconds at the 95th percentile,
  at the stream's real rate.
- The whole cascade costs under one US dollar per 1,000 edits.

What the held-out days said (a sample of 1,500 edits, 74 of them reverted, run once):

| Criterion | Result |
|---|---|
| Four times arrival order in the top 10% | **Not met by the cascade.** Arrival order holds 7 of 74 there. The cascade holds 21 to 23 with Haiku over three runs and 27 with Sonnet, so three to four times. Haiku on every edit holds 32 and Wikimedia's own model 31 |
| The cascade at least as good as tier 1 alone in the top 5% | **Met, by a margin a repeat run can produce.** 16 to 17 of 74 with Haiku and 20 with Sonnet, against 14 for tier 1 alone |
| In the queue within 15 seconds, 95th percentile | **Met, after a fix.** 7.1 and 7.8 seconds in the second and third live runs and 6.8 in the load test, with a median of about 4. The first live run took 17.9 and exposed a defect in the loop |
| Under one US dollar per 1,000 edits | **Met.** US$0.03 with Haiku, US$0.50 with Sonnet |

## Out of scope

Any action on a wiki, other wikis and languages, images, judging editors instead of
edits, a message broker.

## Open questions

- "Reverted" is the only outcome on record. It includes good-faith mistakes and
  disputes, and it misses damage nobody noticed. Nobody has labelled a sample by hand.
- A share of bad edits is reverted within a minute by existing tools. Whether the
  queue adds anything for those was not measured.
- Tier 1 passes too few of the bad edits on: the slice the model reads held 28 of
  the 74 reverted edits in the held-out sample. Whether features taken from the
  change itself, computed for every edit, close that gap is the next thing to build.
- Whether reviewers work better from a risk-ordered queue, or simply trust it, needs
  reviewers.
