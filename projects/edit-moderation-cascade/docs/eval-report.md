# Evaluation report

Written by `editwatch eval` from the committed logs. Do not edit by hand.

## What was scored

- Wiki: `en.wikipedia.org`, article edits by people. History is sampled: 500 edits every 2 hours.
- Training, 25 October to 08 November 2026: 61,623 edits by people, 4,287 reverted.
- Development days, to 13 November: 21,128 edits by people, 1,452 reverted.
- Held-out days, to 15 November: 8,877 edits by people, 456 reverted. Scored once, after every setting was fixed.
- An edit counts as bad if it carried the tag `mw-reverted` at least 48 hours after it was made.

## Held-out days, the model sample

1,500 edits drawn at random, 74 of them reverted within 48 hours. Every configuration orders the same edits.

| Configuration | Edits | Reverted | In the top 2% | In the top 5% | In the top 10% | In the top 20% | **Reverted, in the bottom half** | Read by a model | Cost per 1,000 edits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Arrival order (what reviewers have today) | 1,500 | 74 | 1 of 74 (1%) | 4 of 74 (5%) | 7 of 74 (9%) | 15 of 74 (20%) | **37** | 0 | none |
| Wikimedia's revert-risk model | 1,500 | 74 | 11 of 74 (15%) | 17 of 74 (23%) | 31 of 74 (42%) | 48 of 74 (65%) | **8** | 0 | none |
| Tier 1 alone (event fields, no model) | 1,500 | 74 | 8 of 74 (11%) | 14 of 74 (19%) | 23 of 74 (31%) | 33 of 74 (45%) | **16** | 0 | none |
| Cascade, Haiku reads the top 20% | 1,500 | 74 | 9 of 74 (12%) | 17 of 74 (23%) | 21 of 74 (28%) | 27 of 74 (36%) | **13** | 234 | US$0.03 |
| Cascade, Haiku reads the top 20%, run 2 | 1,500 | 74 | 12 of 74 (16%) | 16 of 74 (22%) | 23 of 74 (31%) | 29 of 74 (39%) | **13** | 234 | US$0.03 |
| Cascade, Haiku reads the top 20%, run 3 | 1,500 | 74 | 10 of 74 (14%) | 17 of 74 (23%) | 23 of 74 (31%) | 31 of 74 (42%) | **13** | 234 | US$0.03 |
| Cascade, Sonnet reads the top 20% | 1,500 | 74 | 13 of 74 (18%) | 20 of 74 (27%) | 27 of 74 (36%) | 33 of 74 (45%) | **16** | 234 | US$0.50 |
| Cascade, Opus reads the top 10% | 1,500 | 74 | 12 of 74 (16%) | 16 of 74 (22%) | 25 of 74 (34%) | 33 of 74 (45%) | **16** | 110 | US$0.49 |
| Haiku reads every edit | 1,500 | 74 | 13 of 74 (18%) | 23 of 74 (31%) | 32 of 74 (43%) | 39 of 74 (53%) | **14** | 1,500 | US$0.16 |

The same cascades with a smaller slice:

| Model | Share of edits it reads | Edits read | In the top 2% | In the top 5% | In the top 10% | Cost per 1,000 edits |
|---|---:|---:|---:|---:|---:|---:|
| Haiku | 5% | 49 | 8 of 74 (11%) | 15 of 74 (20%) | 23 of 74 (31%) | US$0.01 |
| Haiku | 10% | 110 | 8 of 74 (11%) | 14 of 74 (19%) | 21 of 74 (28%) | US$0.01 |
| Haiku | 20% | 234 | 9 of 74 (12%) | 17 of 74 (23%) | 22 of 74 (30%) | US$0.03 |
| Haiku | 40% | 553 | 12 of 74 (16%) | 20 of 74 (27%) | 27 of 74 (36%) | US$0.06 |
| Sonnet | 5% | 49 | 7 of 74 (9%) | 15 of 74 (20%) | 25 of 74 (34%) | US$0.11 |
| Sonnet | 10% | 110 | 10 of 74 (14%) | 16 of 74 (22%) | 23 of 74 (31%) | US$0.24 |
| Sonnet | 20% | 234 | 13 of 74 (18%) | 20 of 74 (27%) | 27 of 74 (36%) | US$0.50 |
| Opus | 5% | 49 | 9 of 74 (12%) | 16 of 74 (22%) | 24 of 74 (32%) | US$0.23 |
| Opus | 10% | 110 | 12 of 74 (16%) | 16 of 74 (22%) | 25 of 74 (34%) | US$0.49 |

Gate, skips and categories:

- Cascade, Haiku reads the top 20%: categories: none 99, false_or_unsourced_claim 51, other 41, content_removal 30, vandalism 11, spam_or_promotion 1, test_or_accident 1.
- Cascade, Haiku reads the top 20%, run 2: categories: none 93, false_or_unsourced_claim 57, other 42, content_removal 31, vandalism 9, spam_or_promotion 1, test_or_accident 1.
- Cascade, Haiku reads the top 20%, run 3: categories: none 99, false_or_unsourced_claim 54, other 38, content_removal 30, vandalism 10, test_or_accident 2, spam_or_promotion 1.
- Cascade, Sonnet reads the top 20%: gate: RANGE_WITHOUT_CATEGORY 1; categories: none 120, other 51, false_or_unsourced_claim 34, content_removal 23, vandalism 5, test_or_accident 1.
- Cascade, Opus reads the top 10%: categories: none 52, false_or_unsourced_claim 23, other 18, content_removal 13, vandalism 4.
- Haiku reads every edit: categories: none 883, false_or_unsourced_claim 260, other 166, content_removal 148, vandalism 34, test_or_accident 6, spam_or_promotion 3.

## Development days, the model sample

1,500 edits drawn at random, 83 of them reverted within 48 hours. Every configuration orders the same edits.

| Configuration | Edits | Reverted | In the top 2% | In the top 5% | In the top 10% | In the top 20% | **Reverted, in the bottom half** | Read by a model | Cost per 1,000 edits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Arrival order (what reviewers have today) | 1,500 | 83 | 2 of 83 (2%) | 4 of 83 (5%) | 8 of 83 (10%) | 17 of 83 (20%) | **42** | 0 | none |
| Wikimedia's revert-risk model | 1,500 | 83 | 11 of 83 (13%) | 22 of 83 (27%) | 39 of 83 (47%) | 59 of 83 (71%) | **7** | 0 | none |
| Tier 1 alone (event fields, no model) | 1,500 | 83 | 5 of 83 (6%) | 10 of 83 (12%) | 26 of 83 (31%) | 35 of 83 (42%) | **16** | 0 | none |
| Cascade, Haiku reads the top 20% | 1,500 | 83 | 17 of 83 (20%) | 24 of 83 (29%) | 27 of 83 (33%) | 41 of 83 (49%) | **15** | 317 | US$0.03 |
| Cascade, Sonnet reads the top 20% | 1,500 | 83 | 22 of 83 (27%) | 29 of 83 (35%) | 34 of 83 (41%) | 47 of 83 (57%) | **13** | 317 | US$0.66 |
| Cascade, Opus reads the top 10% | 1,500 | 83 | 17 of 83 (20%) | 25 of 83 (30%) | 27 of 83 (33%) | 39 of 83 (47%) | **16** | 141 | US$0.62 |
| Haiku reads every edit | 1,500 | 83 | 17 of 83 (20%) | 31 of 83 (37%) | 42 of 83 (51%) | 49 of 83 (59%) | **11** | 1,500 | US$0.16 |

The same cascades with a smaller slice:

| Model | Share of edits it reads | Edits read | In the top 2% | In the top 5% | In the top 10% | Cost per 1,000 edits |
|---|---:|---:|---:|---:|---:|---:|
| Haiku | 5% | 74 | 9 of 83 (11%) | 20 of 83 (24%) | 25 of 83 (30%) | US$0.01 |
| Haiku | 10% | 141 | 14 of 83 (17%) | 19 of 83 (23%) | 22 of 83 (27%) | US$0.02 |
| Haiku | 20% | 317 | 17 of 83 (20%) | 24 of 83 (29%) | 27 of 83 (33%) | US$0.03 |
| Haiku | 40% | 619 | 17 of 83 (20%) | 30 of 83 (36%) | 37 of 83 (45%) | US$0.07 |
| Sonnet | 5% | 74 | 10 of 83 (12%) | 20 of 83 (24%) | 26 of 83 (31%) | US$0.16 |
| Sonnet | 10% | 141 | 15 of 83 (18%) | 21 of 83 (25%) | 23 of 83 (28%) | US$0.30 |
| Sonnet | 20% | 317 | 22 of 83 (27%) | 29 of 83 (35%) | 34 of 83 (41%) | US$0.66 |
| Opus | 5% | 74 | 11 of 83 (13%) | 19 of 83 (23%) | 27 of 83 (33%) | US$0.33 |
| Opus | 10% | 141 | 17 of 83 (20%) | 25 of 83 (30%) | 27 of 83 (33%) | US$0.62 |

Gate, skips and categories:

- Cascade, Haiku reads the top 20%: categories: none 162, false_or_unsourced_claim 52, content_removal 43, other 42, vandalism 12, spam_or_promotion 4, test_or_accident 2.
- Cascade, Sonnet reads the top 20%: categories: none 172, other 61, false_or_unsourced_claim 38, content_removal 28, vandalism 9, spam_or_promotion 5, test_or_accident 4.
- Cascade, Opus reads the top 10%: categories: none 79, false_or_unsourced_claim 20, content_removal 15, other 13, vandalism 7, spam_or_promotion 4, test_or_accident 3.
- Haiku reads every edit: categories: none 922, false_or_unsourced_claim 209, other 168, content_removal 154, vandalism 32, spam_or_promotion 11, test_or_accident 4.

## Held-out days, every edit

| Configuration | Edits | Reverted | In the top 2% | In the top 5% | In the top 10% | In the top 20% | **Reverted, in the bottom half** | Read by a model | Cost per 1,000 edits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Arrival order (what reviewers have today) | 8,877 | 456 | 9 of 456 (2%) | 23 of 456 (5%) | 46 of 456 (10%) | 91 of 456 (20%) | **228** | 0 | none |
| Tier 1 alone (event fields, no model) | 8,877 | 456 | 43 of 456 (9%) | 83 of 456 (18%) | 126 of 456 (28%) | 192 of 456 (42%) | **105** | 0 | none |

## Development days, every edit

| Configuration | Edits | Reverted | In the top 2% | In the top 5% | In the top 10% | In the top 20% | **Reverted, in the bottom half** | Read by a model | Cost per 1,000 edits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Arrival order (what reviewers have today) | 21,128 | 1,452 | 29 of 1,452 (2%) | 73 of 1,452 (5%) | 145 of 1,452 (10%) | 290 of 1,452 (20%) | **726** | 0 | none |
| Tier 1 alone (event fields, no model) | 21,128 | 1,452 | 97 of 1,452 (7%) | 212 of 1,452 (15%) | 374 of 1,452 (26%) | 619 of 1,452 (43%) | **335** | 0 | none |

## Does it keep up?

A recording of 695 edits over 10.0 minutes, replayed on a virtual clock with measured request and model times, 10 times at each speed with a new draw of those times. Counts are per replay. Latency target: 15 seconds at the 95th percentile.

| Replay speed | Edits a second | Read by the model | Skipped: queue full | Seconds to the queue, model tier, median | ...95th percentile | Longest queue |
|---:|---:|---:|---:|---:|---:|---:|
| 1x | 1.16 | 167 | 0 | 3.99 | 6.81 | 7 |
| 2x | 2.32 | 167 | 0 | 4.24 | 10.45 | 15 |
| 5x | 5.81 | 167 | 0 | 7.94 | 16.38 | 36 |
| 10x | 11.61 | 165 | 2 | 9.77 | 17.77 | 50 |
| 20x | 23.23 | 122 | 46 | 11.98 | 19.11 | 50 |
| 50x | 58.07 | 67 | 100 | 12.84 | 25.41 | 50 |

Fastest replay inside the target with nothing skipped: 2x, 2.3 edits a second.

The same recording with the model reading every edit:

| Replay speed | Edits a second | Read by the model | Skipped: queue full | Seconds to the queue, model tier, median | ...95th percentile | Longest queue |
|---:|---:|---:|---:|---:|---:|---:|
| 1x | 1.16 | 695 | 0 | 7.76 | 15.38 | 32 |
| 2x | 2.32 | 689 | 6 | 9.66 | 17.81 | 50 |
| 3x | 3.48 | 610 | 85 | 11.99 | 20.08 | 50 |
| 5x | 5.81 | 404 | 291 | 12.92 | 22.82 | 50 |
| 10x | 11.61 | 230 | 465 | 13.01 | 21.07 | 50 |

Fastest replay inside the target with nothing skipped: none of the speeds tried.

## Live

- Live since 2026-11-17: 1,843 edits in 3 runs, 404 read by the model.
- Seconds from event to queue for edits the model read: median 5.6, 95th percentile 15.9.
- Outcomes read so far: 0. An outcome is read 48 hours after the edit.

## Reading these numbers

- "Reverted" is not "vandalism". Good-faith mistakes are reverted, some reverts are disputes between editors, and damage nobody noticed counts as fine.
- Arrival order is an expectation, not a measurement: a queue that ignores risk holds k% of the reverted edits in its first k%.
- Wikimedia's model was built for every language and for patrolling tools. The comparison shows where this system stands, not that one is better.
- The model sample is small. A difference of a few edits between two configurations is inside what a repeat run of one configuration gives.
