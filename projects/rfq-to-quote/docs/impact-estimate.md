# Impact estimate

> This is a model with stated assumptions, not a measured result. The engagement
> is simulated; a real estimate would replace every assumption below with numbers
> from the shadow period.

## The model

```
minutes saved per day = volume x (manual minutes
                                  - auto share x spot-check minutes
                                  - (1 - auto share) x review minutes)
```

## Assumptions

| Input | Value | Source |
|---|---:|---|
| Requests per working day | 60 | Assumed for the exercise |
| Manual handling per request | 12 min | Assumed (see workflow map) |
| Review of a held quote | 3 min | Assumed; to be measured in the review queue |
| Spot check of an auto-approved quote | 0.5 min | Assumed |
| Working days per month | 21 | |

## What the evaluation contributes

The auto-approved share is the one input the project measures.

- On the 30 development requests, current policy allows 15 (50%) to be
  auto-approved when extraction and matching are perfect. That is the ceiling.
- The rule-based baseline auto-approves 7 of 30, four of them wrongly.
- The default LLM configuration auto-approves 14 of 30, none of them wrongly, and
  5 of the 10 held-out requests.
- These shares come from the published run, the best of six, on a mix of requests
  the author chose. A real mailbox will have its own share, which is why the
  table below gives a range.

## Scenarios

| Auto-approved share | Minutes per request | Saved per request | Hours saved per month |
|---:|---:|---:|---:|
| 0% (every quote reviewed) | 3.0 | 9.0 | 189 |
| 25% | 2.4 | 9.6 | 202 |
| 50% (policy ceiling on the development set) | 1.75 | 10.25 | 215 |

Hours per month are saved minutes x 60 requests x 21 days / 60.

## What the table says

Most of the saving comes from drafting, not from auto-approval: reviewing every
quote already saves about 189 of a possible 252 hours a month under these
assumptions. Auto-approval adds about 26 hours at most. That supports starting in
assisted mode with nothing sent automatically, and treating auto-approval as a
later, optional step that has to earn its risk.

## What would change the estimate

- Review time. If reviewing a held quote takes 6 minutes instead of 3, the saving
  at 0% auto-approval falls from 189 to 126 hours a month.
- Extraction quality. Every wrong draft costs review time and trust; the eval's
  "RFQs with every line exactly right" is the number to watch.
- Error cost. Fewer wrong shipments may be worth more than the hours, and is not
  in this model because there is no baseline error rate to compare against yet.
