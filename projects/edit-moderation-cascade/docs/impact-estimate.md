# Impact estimate

> This is a model with stated assumptions, not a measured result. The client is
> fictional. A real estimate would replace every assumption below with numbers from
> the client's own stream and its own reviewers.

## The model

```
edits reviewers open a day    = reviewers x edits per shift
share of the queue they reach = edits opened a day / edits a day

bad edits reached a day       = reverted edits a day
                                x share of the reverted edits that sit in that part of the queue
```

The second line is why the ordering matters. Reviewers open the top of the queue and
nothing else. What the ordering is worth is how many of the bad edits it puts there.

## Assumptions

| Input | Value | Source |
|---|---:|---|
| Edits a day | 88,000 | Measured on the stand-in stream before the build |
| Reverted within 48 hours | 5.1% | Measured: 456 of 8,877 edits on the held-out days |
| Reviewers, one shift each a day | 12 | Assumed (discovery notes) |
| Edits a reviewer opens in a shift | 400 | Assumed (workflow map) |
| Seconds to open and read one edit | 35 | Assumed (workflow map) |

Twelve reviewers open 4,800 edits a day, which is 5.5% of the stream. So the number
that matters is how many reverted edits sit in the top 5% of the queue. About 4,500
edits a day are later reverted.

## What the evaluation contributes

Measured on the held-out days: a random sample of 1,500 edits made on 13 to 15
November 2026, 74 of them reverted. The range in the third column is a 95% interval
for a sample of this size.

| Queue order | Reverted edits in the top 5% | Share, with interval | Cost per 1,000 edits |
|---|---:|---:|---:|
| Arrival order | 4 of 74 | 5% (2% to 13%) | none |
| Tier 1 alone | 14 of 74 | 19% (12% to 29%) | none |
| Cascade, Haiku reads the riskiest fifth (three runs) | 16 to 17 of 74 | 22% to 23% (14% to 34%) | US$0.03 |
| Cascade, Sonnet reads the riskiest fifth | 20 of 74 | 27% (18% to 38%) | US$0.50 |
| Haiku reads every edit | 23 of 74 | 31% (22% to 42%) | US$0.16 |
| Wikimedia's revert-risk model, for reference | 17 of 74 | 23% (15% to 34%) | none |

The intervals overlap for every row but the first. This sample shows that any risk
order beats the clock. It does not show which risk order is best.

## Scenarios

| | Bad edits reached a day | Model cost a day | Model cost a month |
|---|---:|---:|---:|
| Today: arrival order | 250 | none | none |
| Tier 1 alone | 850 | none | none |
| Cascade with Haiku | 1,000 | US$2.60 | US$80 |
| Cascade with Sonnet | 1,200 | US$44 | US$1,300 |
| Haiku on every edit | 1,400 | US$14 | US$420 |

Bad edits reached is 4,500 x the share above, rounded to the nearest 50. Arrival
order is 5.5% of 4,500. Cost is 88,000 edits x the cost per 1,000. The stream and the
wiki API are free.

## What the table says

Reviewers who work a risk-ordered queue reach three to six times as many bad edits
as reviewers who work by the clock, with the same twelve people. Most of that comes
from tier 1, which costs nothing to run.

The model tier adds less than the design assumed. With Haiku the cascade reaches
about 150 more bad edits a day than tier 1 alone for US$2.60, and on this sample that
difference is two or three edits of 74, which a repeat run can produce on its own.
The step that did buy something is the one the client ruled out: Haiku on every edit
reaches about 400 more than the cascade for US$11 more a day.

The reason is in the evaluation. The slice the model reads holds 28 of the 74
reverted edits. The other 46 never reach a model, whichever model it is. A cascade
is limited by how many bad edits its cheap tier passes on, and this tier 1 passes on
too few.

## What this is not

- **Not time saved.** Reviewers open the same 4,800 edits. They find more.
- **Not damage prevented.** Many of these edits are reverted by other editors or by
  existing tools, some within a minute. The estimate counts edits a reviewer would
  have reached, not edits that would otherwise have stayed.
- **Not vandalism.** "Reverted" includes good-faith mistakes and disputes.

## What would change the estimate

- **A larger sample.** 74 reverted edits cannot separate the configurations. The
  live log orders about 700 edits a run and reads their outcomes 48 hours later.
- **A stronger tier 1.** The wiki API returns what 25 edits changed in one request,
  enough to read every edit at the stream's rate. Features from the change itself,
  for every edit, are the next step and were not built.
- **The client's own numbers.** How many edits a reviewer opens, and how many bad
  edits are already undone before a reviewer could act.
- **Reviewers.** Whether people find a bad edit faster with the marked lines, or
  stop checking and trust the order, was not measured. No reviewer has used the page.
