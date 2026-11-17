# Evaluation on outside data

Generated 2026-11-17 by `rfq2quote eval-outside`. LLM matching uses `claude-haiku-5-5` at `medium` effort.

Every other number in this project comes from requests and labels written by the project's author. This report scores one step, matching a line of text to a product or declining, on product offers and labels that someone else produced.

## The data

| | |
|---|---|
| Dataset | Billiger.de Products v1.1, German. Steiner, Elagin, Peeters, Knopp and Bizer, University of Mannheim, [arXiv:2609.37713](https://arxiv.org/abs/2609.37713) |
| Source | [github.com/wbsg-uni-mannheim/billiger-de-products](https://github.com/wbsg-uni-mannheim/billiger-de-products) at commit `1452caee9484` |
| File | `data/solute_de/gold-standards_adjusted/products80cc20rnd050un_gs.json.gz`, the configuration its authors recommend for a single evaluation: 80% corner cases, half of the products unseen in training |
| SHA-256 | `a6f703bf3e454587316eb00e509700723a563230e195be18cb4c823176f7b5f3` |
| Content | Real offers from the price comparison portal billiger.de. The labels (same product or not) come from the dataset's authors |
| Scored here | All 994 queries. 4427 labelled pairs, 339 of them matches. 333 queries have a match among their candidates |

The data is not in this repository. Its page states no licence, only that it is "released with the permission of solute GmbH, the operator of billiger.de", so `rfq2quote fetch-outside` downloads the pinned file and checks its checksum. The recorded model responses keep the chosen offer id and the confidence; the model's free-text reason is not stored.

## How it is scored

- Each left-hand offer in the file is treated as a customer line. The right-hand offers it is paired with are its candidates (1 to 16 per query), loaded into a catalog of their own.
- The matching step runs unchanged: the same search, the same prompt (`match_v1`), the same rule that an answer outside the candidates is discarded. It returns one offer or declines.
- Only titles are used: the name, with the brand in front when the name lacks it. Descriptions and prices are shown neither to the search nor to the model. These choices were fixed before the first model call.
- A pick is right when the dataset labels that pair a match. Precision, recall and F1 are counted over the labelled pairs: a pick is a predicted match, every other pair a predicted non-match.
- The matcher picks at most one offer per query and 5 queries have more than one matching offer, so recall cannot exceed 98.2%.
- In the pipeline only a high-confidence match can be auto-approved; every other pick is shown to a person. The high-confidence rows are therefore the ones that describe what would go out unchecked.

## Results

All 994 queries.

| | `top-hit` | `llm-match` |
|---|---:|---:|
| Right offer picked | 199 of 333 | 230 of 333 |
| Wrong offer picked | 728 | 73 |
| of which with high confidence | 106 | 19 |
| of those, the picked offer's title is word for word the query's | 4 | 4 |
| Declined although a match was among the candidates | 1 | 99 |
| of which the search did not show the match | 1 | 1 |
| Declined correctly, no match among the candidates | 66 of 661 | 592 of 661 |
| Precision, any pick | 21.5% | 75.9% |
| Recall, any pick | 58.7% | 67.8% |
| F1, any pick | 31.4% | 71.7% |
| Precision, high-confidence picks only | 39.1% | 87.2% |
| Recall, high-confidence picks only | 20.1% | 38.1% |
| F1, high-confidence picks only | 26.5% | 53.0% |
| Model calls | 0 | 927 |
| Model calls that failed, counted as declined | 0 | 0 |
| Model cost (USD) | $0.00 | $0.27 |
| Model time, summed over calls (s) | 0 | 2308 |

`top-hit` is the no-model baseline: it always takes the best search hit, and calls it high confidence when every number in the query appears in the candidate.

## The same queries with each recorded model

One recorded run per model. The prompt, the search and the effort setting are the same for all of them; only the model differs.

| | `claude-haiku-5-5` | `claude-sonnet-5-5` | `claude-opus-5-5` |
|---|---:|---:|---:|
| Picks that are right (precision) | 230 of 303 (75.9%) | 242 of 313 (77.3%) | 271 of 354 (76.6%) |
| Matches found (recall) | 230 of 339 (67.8%) | 242 of 339 (71.4%) | 271 of 339 (79.9%) |
| F1, any pick | 71.7% | 74.2% | 78.2% |
| High-confidence picks that are wrong | 19 of 148 (12.8%) | 25 of 184 (13.6%) | 23 of 196 (11.7%) |
| Picked an offer although no candidate matches | 69 of 661 | 67 of 661 | 82 of 661 |
| Declined although a match was among the candidates | 99 | 87 | 61 |
| Model calls | 927 | 927 | 927 |
| Model cost (USD) | $0.27 | $3.13 | $7.72 |

From `claude-haiku-5-5` to `claude-opus-5-5`, recall goes from 67.8% to 79.9%, and the share of high-confidence picks that are wrong from 12.8% to 11.7%. In the pipeline only a high-confidence pick can go out without a person, so that share is the number that bears on safety. Recall says how much of the matching a model does on its own.

Query by query against `claude-haiku-5-5`. A query counts as right when the right offer was picked or the matcher correctly declined.

| Model | Right where `claude-haiku-5-5` is wrong | Wrong where `claude-haiku-5-5` is right | Chance of a split this uneven | Reading |
|---|---:|---:|---:|---|
| `claude-sonnet-5-5` | 44 | 30 | 0.13 | could be chance |
| `claude-opus-5-5` | 60 | 32 | 0.0046 | unlikely to be chance alone |

The chance is from a two-sided exact McNemar test: if the two models were equally good, how often the queries they disagree on would split at least this unevenly. Under 0.05 the table reads the difference as unlikely to be chance alone; that line is a convention. The test treats the queries as independent and says nothing about how much a second run of the same model would differ.

## Published results on the same file

From the dataset's own `reports/final_results/summary.csv` (German, 80% corner cases, Half-Seen).

| System | Precision | Recall | F1 |
|---|---:|---:|---:|
| Word co-occurrence baseline, trained | 31.78% | 42.18% | 36.25% |
| XLM-R, trained (best trained system) | 43.19% | 88.30% | 58.01% |
| GPT-5.2 zero-shot, simple prompt | 53.62% | 89.68% | 67.11% |
| GPT-5.2 zero-shot, rule-guided prompt | 94.54% | 66.37% | 77.99% |

These are context, not a ranking. Those systems classify one pair at a time and see brand, name, description and price. The matcher here sees a query's candidates together, sees titles only, and is built to decline when it is not sure.

## How far to trust this

- **One run per model.** There is no variance estimate: a second run could move each count by a few picks.
- **Consumer products, not industrial materials.** This shows how the matching step behaves on text its author never saw. It says nothing about extraction, pricing or the guardrails.
- **The prompt was not adapted.** It was written for cables and transformers and names their attributes.
- **The search step is barely tested.** Candidate pools are small. Finding the right offer in a full catalog would be harder.
- **Training-data overlap is unknown.** The dataset was published in 2026; whether the model has seen any of it cannot be checked from here.
- **The labels are taken as given.** Their authors document an audit of the test sets; this project did not re-check them.

