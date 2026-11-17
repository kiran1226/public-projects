# Impact estimate

> This is a model with stated assumptions, not a measured result. The client is
> fictional. A real estimate would replace every assumption below with numbers from
> the shadow run.

## The model

```
minutes saved a month = issues a month x (minutes per issue today - minutes per issue with the desk)

issues routed right at first pass = issues a month x share whose area the desk gets right
```

## What the evaluation contributes

Measured on the held-out months: 754 issues opened September and October 2026, of
which 638 carry an area label. The model was run three times.

| | Keyword rules | Classifier | Desk (model, three runs) |
|---|---:|---:|---:|
| Area right, of 638 | 206 (32%) | 347 (54%) | 412 to 419 (65% to 66%) |
| Labels proposed that are on the issue | 484 of 1,030 (47%) | 716 of 1,184 (60%) | 621 to 626 of 901 to 905 (69%) |
| Issues it would handle without a person | 229 | 50 | 249 to 259 |
| ...with nothing wrong | 132 (58%) | 42 (84%) | 179 to 189 (72% to 73%) |
| Cost per 1,000 issues | none | none | US$0.89 |

Read as: of every ten issues the maintainers gave an area, the desk proposes the
right one for between six and seven, the classifier for five, keyword rules for
three. One in three issues would need no person, and about seven in ten of those
would have nothing wrong.

What this is not. It is not a measure of time saved: nobody timed a triager with
and without the desk. And "wrong" means "not what the maintainers did", which
includes issues they never labelled.

## Assumptions

| Input | Value | Source |
|---|---:|---|
| Issues a month | 374 | Measured: 2,243 in six months on the stand-in tracker |
| Minutes per issue today | 4.5 | Assumed (workflow map) |
| Minutes per issue with the desk | 2.0 | Assumed; to be measured in the shadow run |
| Model cost per issue | US$0.00089 | Measured on 754 issues, three runs |

## Scenarios

| | Minutes per issue | Hours a month | Hours saved a month | Model cost a month |
|---|---:|---:|---:|---:|
| Today | 4.5 | 28 | | none |
| With the desk, as assumed | 2.0 | 12 | 16 | US$0.33 |
| With the desk, if checking a proposal takes 3.5 minutes | 3.5 | 22 | 6 | US$0.33 |

Hours are issues x minutes / 60. Cost is 374 issues x US$0.00089. The feed is free,
and the scheduled job runs inside GitHub's free minutes for a public repository.

## What the table says

The time is small: between 6 and 16 hours a month for the whole rota. The model
costs a third of a US dollar a month, so cost is not the question either.

The case for the desk is the wait and the misses. An issue has a proposal within the
hour whether or not anyone is on the rota. And the duplicate step points at earlier
reports of the same defect that the team did not link: by the review in
`duplicate-review.md`, most of its calls are of that kind. Whether that is worth a
triager's attention is exactly what the shadow run has to show.

On the stand-in tracker the wait is already short: its maintainers label an issue a
median of 1.4 hours after it opens. A team that fast gains less than the client in
the scenario, whose issues wait for one of five people.

## What would change the estimate

- **Timing a triager.** Both minute figures are assumed.
- **The share handled alone.** It only counts if apply mode is on, and the gate for
  that is not met (project plan, gate 2).
- **A second tracker.** One product's labels and one team's habits were measured.
- **Drift.** Every configuration scored lower on the held-out months than on the
  development months. A desk that is not retrained will keep losing ground.
