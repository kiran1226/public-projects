# Impact estimate

> This is a model with stated assumptions, not a measured result. The client is
> fictional. A real estimate would replace every assumption below with numbers from
> four weeks of running the list next to the alerts.

## The model

Two effects, kept apart because only one of them is measured.

```
tenders seen       = tenders the desk would go on to win
                     x share of them in the first k places of the morning's list

minutes saved      = minutes a day on alerts today - minutes a day on the list
```

## What the evaluation contributes

The share in the first k places, for the alert and for the list. It is measured on
200 real suppliers and the tenders they went on to win, on the held-out month (June
2026, 270 tenders).

| The desk reads the first | Category alert | The list | Difference |
|---:|---:|---:|---:|
| 1 tender of the day | 73 of 270 (27%) | 109 of 270 (40%) | 36 more |
| 3 tenders | 133 (49%) | 197 (73%) | 64 more |
| 5 tenders | 172 (64%) | 228 (84%) | 56 more |
| Never on the day's list | 45 | 7 | 38 fewer |

Read as: of every ten tenders a supplier went on to win, a desk that reads three a
morning sees about five with the alert and about seven with the list.

What this is not. It is not the number of extra bids won. A tender that is seen still
has to be bid for and won, and the panel suppliers won these tenders without the list:
they found them some other way. The figure says how far down a list someone must read
to find what matters.

## Assumptions

| Input | Value | Source |
|---|---:|---|
| Minutes a day on alerts today | 60 | Assumed (see the workflow map) |
| Minutes a day on the morning list | 23 | Assumed; to be measured in the shadow run |
| Working days a month | 21 | |
| Model cost per tender read | US$0.00054 | Measured on 2,772 assessments |
| Tenders read by the model per morning | 10 | The system's setting |

## Scenarios

| | Minutes a day | Hours saved a month | Model cost a month |
|---|---:|---:|---:|
| Alerts, as today | 60 | 0 | 0 |
| The list, as assumed | 23 | 13 | US$0.11 |
| The list, if reading it takes twice as long as assumed | 46 | 5 | US$0.11 |

Hours a month are saved minutes x 21 days / 60. Model cost is 10 tenders x 21 days x
US$0.00054. The feed is free and the scheduled job runs inside GitHub's free minutes
for a public repository.

## What the table says

The time saved is small: about 13 hours a month for the whole desk under these
assumptions. The case for the list is not the time. It is the 45 tenders in 270 that
never appeared on the alert at all, and the order of the ones that did. One bid won
that would otherwise have been missed is worth more than a year of the hours, and
whether the list produces one is exactly what the shadow run has to show.

## What would change the estimate

- **The desk's own bid history.** Only wins are public. With the tenders the desk bid
  for and lost, the share could be measured on what the desk considered relevant.
- **The profile.** The backtest uses suppliers whose profile is their award history.
  A profile the desk writes and revises may do better or worse.
- **The model's "skip".** On the held-out sample it marked 18 of 176 later-won
  tenders "skip". If a desk learned to ignore the bottom of the list, those would be
  lost. That is why the rollout gate on the model is not met.
- **Other markets.** Three category divisions were measured.
