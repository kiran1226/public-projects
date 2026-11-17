# Evaluation report

Written by `tenderradar eval`. Do not edit by hand: change the system, replay, and run the command again.

## What was run

- Feed: TED competition notices and award notices in category divisions 24, 33, 38.
- History the profiles were built from: 81029 awards published 2025-01-01 to the day before 2026-04-01.
- Test window: tenders first published 2026-04-01 to 2026-06-30: 11549 tenders on 60 publication days.
- Awards read for outcomes: published up to 2026-11-17.
- Suppliers found in the awards: 33868. Eligible for the panel (a company, at least 5 wins before the cut, at least 2 wins of tenders in the test window): 1550. On the panel: 200.
- Tenders the panel went on to win: 1003.
- Embedding model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.

Every list holds at most the first 15 tenders. A tender counts as found if it is on the list the system wrote for that supplier on the day the tender was first published.

## Development months (before 2026-06-01)

Ranker settings were chosen on these months.

| Ranker | Tenders later won | In top 1 | In top 3 | In top 5 | In top 15 | Median place | Removed by a rule | Marked skip by the model | Mean list length |
|---|---|---|---|---|---|---|---|---|---|
| Category alert (baseline) | 733 | 206 (28%) | 408 (56%) | 496 (68%) | 605 (83%) | 2 | 6 | n/a | 3.2 |
| Category match, weighted | 733 | 272 (37%) | 499 (68%) | 595 (81%) | 700 (95%) | 2 | 6 | n/a | 6.4 |
| Text match (TF-IDF) | 733 | 259 (35%) | 443 (60%) | 506 (69%) | 664 (91%) | 2 | 6 | n/a | 6.4 |
| Multilingual embeddings | 733 | 289 (39%) | 478 (65%) | 560 (76%) | 684 (93%) | 2 | 6 | n/a | 6.4 |
| Buyer history alone | 733 | 171 (23%) | 377 (51%) | 481 (66%) | 638 (87%) | 3 | 6 | n/a | 6.4 |
| Fused (categories and embeddings) | 733 | 307 (42%) | 531 (72%) | 611 (83%) | 712 (97%) | 2 | 6 | n/a | 6.4 |

Of 733 tenders that panel suppliers went on to win, the fused ranker had 531 in the first 3 places of that day's list (72%, 95% interval 69% to 76%). The category alert had 408 of 733 (56%). The fused ranker never listed 15; the alert never listed 122.

## Held-out month (from 2026-06-01)

Not looked at while settings were chosen. Scored once.

| Ranker | Tenders later won | In top 1 | In top 3 | In top 5 | In top 15 | Median place | Removed by a rule | Marked skip by the model | Mean list length |
|---|---|---|---|---|---|---|---|---|---|
| Category alert (baseline) | 270 | 73 (27%) | 133 (49%) | 172 (64%) | 223 (83%) | 3 | 2 | n/a | 2.9 |
| Category match, weighted | 270 | 108 (40%) | 186 (69%) | 215 (80%) | 258 (96%) | 2 | 2 | n/a | 5.9 |
| Text match (TF-IDF) | 270 | 81 (30%) | 154 (57%) | 184 (68%) | 243 (90%) | 2 | 2 | n/a | 5.9 |
| Multilingual embeddings | 270 | 95 (35%) | 173 (64%) | 207 (77%) | 249 (92%) | 2 | 2 | n/a | 5.9 |
| Buyer history alone | 270 | 53 (20%) | 122 (45%) | 159 (59%) | 227 (84%) | 3 | 2 | n/a | 5.9 |
| Fused (categories and embeddings) | 270 | 109 (40%) | 197 (73%) | 228 (84%) | 261 (97%) | 2 | 2 | n/a | 5.9 |

Of 270 tenders that panel suppliers went on to win, the fused ranker had 197 in the first 3 places of that day's list (73%, 95% interval 67% to 78%). The category alert had 133 of 270 (49%). The fused ranker never listed 7; the alert never listed 45.

## The model, on a sample

The model read the first 10 tenders of 380 lists: 2772 assessments for US$1.49 (US$0.54 per 1,000). The other rankers are scored here on the same supplier-days. The sample was drawn separately in the development months and in the held-out month.

Development months:

| Ranker | Tenders later won | In top 1 | In top 3 | In top 5 | In top 15 | Median place | Removed by a rule | Marked skip by the model | Mean list length |
|---|---|---|---|---|---|---|---|---|---|
| Category alert (baseline) | 258 | 78 (30%) | 149 (58%) | 188 (73%) | 230 (89%) | 2 | 0 | n/a | 6.3 |
| Category match, weighted | 258 | 101 (39%) | 177 (69%) | 211 (82%) | 246 (95%) | 2 | 0 | n/a | 9.7 |
| Text match (TF-IDF) | 258 | 92 (36%) | 165 (64%) | 191 (74%) | 244 (95%) | 2 | 0 | n/a | 9.7 |
| Multilingual embeddings | 258 | 101 (39%) | 164 (64%) | 199 (77%) | 245 (95%) | 2 | 0 | n/a | 9.7 |
| Buyer history alone | 258 | 57 (22%) | 137 (53%) | 174 (67%) | 234 (91%) | 3 | 0 | n/a | 9.7 |
| Fused (categories and embeddings) | 258 | 107 (41%) | 184 (71%) | 217 (84%) | 251 (97%) | 2 | 0 | n/a | 9.7 |
| Fused, then the model reads the top of the list | 258 | 133 (52%) | 204 (79%) | 229 (89%) | 251 (97%) | 1 | 0 | 12 | 9.7 |

Held-out month:

| Ranker | Tenders later won | In top 1 | In top 3 | In top 5 | In top 15 | Median place | Removed by a rule | Marked skip by the model | Mean list length |
|---|---|---|---|---|---|---|---|---|---|
| Category alert (baseline) | 176 | 57 (32%) | 101 (57%) | 130 (74%) | 157 (89%) | 2 | 0 | n/a | 5.1 |
| Category match, weighted | 176 | 85 (48%) | 135 (77%) | 155 (88%) | 172 (98%) | 2 | 0 | n/a | 8.3 |
| Text match (TF-IDF) | 176 | 68 (39%) | 124 (70%) | 142 (81%) | 168 (95%) | 2 | 0 | n/a | 8.3 |
| Multilingual embeddings | 176 | 71 (40%) | 125 (71%) | 148 (84%) | 167 (95%) | 2 | 0 | n/a | 8.3 |
| Buyer history alone | 176 | 46 (26%) | 97 (55%) | 122 (69%) | 157 (89%) | 2 | 0 | n/a | 8.3 |
| Fused (categories and embeddings) | 176 | 81 (46%) | 139 (79%) | 154 (88%) | 172 (98%) | 2 | 0 | n/a | 8.3 |
| Fused, then the model reads the top of the list | 176 | 88 (50%) | 140 (80%) | 155 (88%) | 172 (98%) | 1 | 0 | 18 | 8.3 |

- Decisions: 709 bid, 628 ask a person, 1435 skip.
- Gate: 16 decisions were changed to "ask a person" because the quoted passage was not in the notice. 14 stated conditions were dropped for the same reason; 1756 were kept.

## The worst error

A tender the supplier went on to win that it was never shown.

- Removed by a hard rule before ranking: 8 of 1003 (outside_territory: 5, deadline_too_close: 3).
- Marked "skip" by the model, development months: 12 of 258 won tenders in the sample.
- Marked "skip" by the model, held-out month: 18 of 176 won tenders in the sample.

## Live

- Live since 2026-11-17, starting with the tenders published on 2026-11-16; 1 publication days processed, 402 lists written.
- Outcomes that have arrived for tenders listed live: 0.
- None can be scored yet. In the backtest the median award came about four months after its tender, so the first live scores are expected about four months after the start.

## How far to trust this

- Winners are the only outcome on record. A tender a supplier bid for and lost was relevant to it but counts here as noise in the list, so these figures understate how useful a list is and say nothing about precision.
- A supplier is on the panel only if it won at least two tenders in the test window. The panel is therefore made of active suppliers, and takes them country by country, so small countries weigh more than their share of tenders.
- Only tenders awarded by the day the awards were read can be scored. Procedures that take longer to award are missing.
- Supplier identity is resolved from names and registration numbers typed by buyers. Some wins are attributed to the wrong supplier or to none.
- The backtest replays history through the live pipeline in date order, with profiles frozen at the cut date. It is still a backtest: the live log is the measurement that cannot have been tuned.
