# Impact estimate

> This is a model with stated assumptions, not a measured result. The engagement
> is simulated; a real estimate would replace every assumption below with numbers
> from the incident history and the shadow period.

## The model

```
loss avoided per month = incidents per month
                         x share found by the system
                         x loss per hour
                         x (hours until noticed today - hours until acted on with the system)
```

A second, smaller effect is the on-call's time: the minutes spent finding where a
problem is and what changed.

## Assumptions

| Input | Value | Source |
|---|---:|---|
| Incidents per month | 3 | Assumed. The synthetic world has 14 in 12 weeks because it is built to test, not to be typical |
| Loss per incident-hour | EUR 1,500 | Assumed. In the synthetic world a card outage in the largest market loses about EUR 4,000 an hour and a coupon error a few hundred |
| Hours until noticed today | 10 | Assumed: some incidents are caught in an hour, a weekend one runs for two days |
| Minutes from alert to approved action | 15 | Assumed; to be measured in the review queue |
| On-call time to locate a problem today | 60 min | Assumed (see the workflow map) |

## What the evaluation contributes

Three inputs are measured, on synthetic data.

- **Share found.** The forecasting detector finds 14 of 14 development incidents
  on four seeds, and 5 of 7 on the held-out world, whose scenarios were written
  after the detectors were frozen. The static rule that stands for today's
  dashboards finds 7 of 14 and 2 of 7. The development figure is what tuning
  reaches on scenarios it has seen. The held-out figure, about 70%, is the one
  to plan with.
- **Hours to detect.** Two hours for a sharp break, five to seven for a slow
  one. The model below uses three as a typical value.
- **How often the brief is right.** Given one case per incident, the agent
  names the right cause for 18 of 19 incidents that have one in the published
  run, and for 19 of 19 in two repeat runs on the same model. The scripted investigator
  names 16. The brief arrives after 8 to 10 seconds of model time.

The evaluation also bounds the cost of having the system: about one false alert
a week, of which one in twelve weeks would have paged.

## Scenarios

Hours saved per incident = 10 - (hours to detect + 0.25).

| Share found | Hours to detect | Hours saved per incident | Loss avoided per month |
|---:|---:|---:|---:|
| 30% (static rule, held-out) | 3 | 6.75 | EUR 9,100 |
| 50% (static rule, development) | 3 | 6.75 | EUR 15,200 |
| 70% (held-out) | 6 | 3.75 | EUR 11,800 |
| 70% (held-out) | 3 | 6.75 | EUR 21,300 |
| 85% | 6 | 3.75 | EUR 14,300 |
| 100% | 6 | 3.75 | EUR 16,900 |
| 100% | 3 | 6.75 | EUR 30,400 |
| 100% | 2 | 7.75 | EUR 34,900 |

On-call time: at 3 incidents a month and 60 minutes each to find the place and
the change, a brief that is right saves about three hours a month. That is small
next to the loss avoided. The larger effect on the on-call is the false alerts
they no longer mute.

## How to read this

- Plan with the 70% rows. The 100% rows are the development world, where the
  detector was tuned; on scenarios it had not seen it found 5 of 7, and both
  misses were small slices.
- The result is driven by two assumptions nobody has measured here: how long an
  incident runs unnoticed today, and what an hour of it costs. Halve either and
  the result halves.
- The static rule looks respectable in its rows because the model gives it
  the same three hours. In the evaluation it raises two to three false alerts a
  week and seventeen alerts during one large promotion, which is why the team
  had muted it. An alert nobody reads finds nothing.
- The investigator does not appear in the formula. Its effect is on the fifteen
  minutes from alert to action and on calling the right team first, and it can
  only be measured in shadow mode.
- Nothing here counts the cost of a wrong action. The design removes that case
  instead of pricing it: a person approves every mitigation.

## What to measure in shadow mode

1. Hours from an incident's start to the first person knowing, today and with
   the system, on the same incidents.
2. The share of alerts the on-call judges worth having been woken for.
3. Minutes from alert to a decision in the queue.
4. How often the first team called was the right one.
